"""Focused offline tests for the Outer → Plan → Executor handoff protocol."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.remotion_templates.agent import AgentRun, Layer
from server.remotion_templates.context import AssistantMessage
from server.remotion_templates.planning import Plan, PlanAction
from server.remotion_templates.tools.registry import registered_tools
from server.remotion_templates.provider import Budget
from server.remotion_templates.settings import Settings


class FakeSession:
    """Small task-owned session exposing only the PR76 APIs used by AgentRun."""

    def __init__(self, _harness, _spec, _base, _budget, directory, images, _stage, intent):
        self.directory = Path(directory)
        self.images = images
        self.intent = intent or {"original_request": {"description": "demo"}}
        self.feedback = []
        self.step_scope = None
        self.latest_sprite_id = "sprite-1"
        self.saved_sprites = {"sprite-1": {"sprite_id": "sprite-1"}}

    def snapshot(self):
        """Return deterministic host facts for all three prompts."""
        return {"user_intent": self.intent, "sprites": sorted(self.saved_sprites)}

    def saved_sprite(self, identifier):
        """Resolve only the task's exact saved Sprite."""
        return self.saved_sprites[identifier]

    async def validate_render(self, _request):
        """Return a passing mount report so publication can proceed."""
        return SimpleNamespace(passed=True)

    def validation_for(self, _record):
        """A saved Sprite has no render receipt until the host finalizer checks it."""
        return None

    async def execute(self, name, args, _catalog):
        """Record one deterministic business tool observation."""
        return {"tool": name, "args": args.model_dump(mode="json")}


class FakeHarness:
    """Script model turns while recording the role sequence and finalization."""

    def __init__(self, responses):
        self.responses = iter(responses)
        self.roles = []
        self.settings = SimpleNamespace(
            max_steps=4,
            max_tooluse=3,
            enforce_no_progress=False,
            max_no_progress_turns=Settings.model_fields["max_no_progress_turns"].default,
        )

    async def _turn(self, _system, _context, _tools, _budget, _images, *, phase):
        """Return the next scripted response for one exact model role."""
        self.roles.append(phase)
        return next(self.responses)

    async def finalize_sprite(self, _session, identifier, _budget, _directory):
        """Return a marker proving only Outer performed publication."""
        return {"published": identifier}


def call(identifier, name, arguments):
    """Build a provider-compatible function call message."""
    return AssistantMessage(
        tool_calls=[
            {
                "id": identifier,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ]
    )


@pytest.mark.parametrize("response", [
    AssistantMessage(content='{"action":"complete","sprite_id":"sprite-1"}'),
    call("complete-1", "tools_plan_execute", {"action": "complete", "sprite_id": "sprite-1"}),
], ids=["json", "tool"])
def test_deferred_tools_are_hidden_and_completion_still_publishes(monkeypatch, tmp_path, response):
    """Only implemented tools reach the model, and host publication needs no model receipts."""
    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FakeSession)
    harness = FakeHarness([response])
    run = AgentRun(harness, None, Budget(), tmp_path, [], lambda *_: None)
    names = {tool.name for tool in run._tools_for_layer()}
    implemented = {item.name for item in registered_tools() if item.implemented}
    assert names == implemented | {"tools.plan_execute"}
    assert asyncio.run(run.plan_execute()) == {"published": "sprite-1"}
    assert harness.roles == ["outer"]


def test_outer_plan_executor_plan_outer_handoff(monkeypatch, tmp_path):
    """A planned task must traverse every layer and publish only after Outer completion."""
    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FakeSession)
    plan = Plan(
        goal="inspect",
        steps=[
            {
                "id": "inspect",
                "goal": "inspect a tool",
                "tool_modules": ["tools"],
                "done_when": "descriptor observed",
            }
        ],
    )
    harness = FakeHarness(
        [
            call("outer-1", "tools_plan_execute", {"action": "delegate", "plan": plan.model_dump()}),
            call("plan-1", "tools_plan_execute", {"action": "update_plan", "plan": plan.model_dump()}),
            call("exec-1", "tools_inspect", {"tool_name": "sprite.create"}),
            AssistantMessage(content=json.dumps({"status": "step_done", "summary": "descriptor observed", "sprite_id": "sprite-1"})),
            call("plan-2", "tools_plan_execute", {"action": "complete"}),
            AssistantMessage(content=json.dumps({"action": "complete", "sprite_id": "sprite-1"})),
        ]
    )
    run = AgentRun(harness, None, Budget(), tmp_path, [], lambda *_: None, intent={"original_request": {"description": "demo"}})
    result = asyncio.run(run.plan_execute())
    assert result == {"published": "sprite-1"}
    assert harness.roles == ["outer", "plan", "executor", "executor", "plan", "outer"]
    assert run.plan_context.messages() and run.executor_context.messages()
    # Successful delegate/update_plan/complete controls must not accumulate towards no_progress.
    assert run.stalled_turns == 0


def test_every_layer_is_told_that_inspect_cannot_search_for_tools(monkeypatch, tmp_path):
    """真实日志里 55% 的 tools_inspect 是在猜工具名：「只读已知名称的契约」这条规则必须进入三层的系统提示。"""
    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FakeSession)
    plan = Plan(goal="inspect", steps=[{"id": "inspect", "goal": "inspect a tool", "tool_modules": ["tools"], "done_when": "observed"}])
    harness = FakeHarness([
        call("outer-1", "tools_plan_execute", {"action": "delegate", "plan": plan.model_dump()}),
        call("plan-1", "tools_plan_execute", {"action": "update_plan", "plan": plan.model_dump()}),
        AssistantMessage(content=json.dumps({"status": "step_done", "summary": "observed", "sprite_id": "sprite-1"})),
        call("plan-2", "tools_plan_execute", {"action": "complete"}),
        AssistantMessage(content=json.dumps({"action": "complete", "sprite_id": "sprite-1"})),
    ])
    systems = {}

    async def turn(system, _context, _tools, _budget, _images, *, phase):
        """Remember the system prompt each role received before returning the scripted reply."""
        systems.setdefault(phase, system)
        harness.roles.append(phase)
        return next(harness.responses)

    harness._turn = turn
    run = AgentRun(harness, None, Budget(), tmp_path, [], lambda *_: None, intent={"original_request": {"description": "demo"}})
    assert asyncio.run(run.plan_execute()) == {"published": "sprite-1"}
    assert set(systems) == {"outer", "plan", "executor"}
    for role, system in systems.items():
        assert "cannot search or list tools" in system, role


def test_executor_cannot_call_plan_control(monkeypatch, tmp_path):
    """An Executor orchestration call is rejected and does not advance the Plan."""
    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FakeSession)
    plan = Plan(
        goal="inspect",
        steps=[
            {
                "id": "inspect",
                "goal": "inspect",
                "tool_modules": ["tools"],
                "done_when": "done",
            }
        ],
    )
    harness = FakeHarness(
        [
            call("outer-1", "tools_plan_execute", {"action": "delegate", "plan": plan.model_dump()}),
            call("plan-1", "tools_plan_execute", {"action": "update_plan", "plan": plan.model_dump()}),
            call("exec-1", "tools_plan_execute", {"action": "advance"}),
            AssistantMessage(content=json.dumps({"status": "needs_input", "summary": "stop"})),
            AssistantMessage(content=json.dumps({"status": "needs_input", "summary": "stop", "questions": ["需要更多输入"]})),
            AssistantMessage(content=json.dumps({"questions": ["需要更多输入"]})),
        ]
    )
    run = AgentRun(harness, None, Budget(), tmp_path, [], lambda *_: None, intent={"original_request": {"description": "demo"}})
    result = asyncio.run(run.plan_execute())
    assert result.questions == ["需要更多输入"]
    assert run.state.index == 0


def test_repeated_invalid_executor_replies_stop_the_run(monkeypatch, tmp_path):
    """Prose that never yields a StepResult is a stall: the guard ends the run after the threshold."""
    from server.remotion_templates.provider import ExecutionFailure

    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FakeSession)
    plan = Plan(goal="g", steps=[{"id": "s", "goal": "g", "tool_modules": ["tools"], "done_when": "d"}])
    harness = FakeHarness(
        [
            call("outer-1", "tools_plan_execute", {"action": "delegate", "plan": plan.model_dump()}),
            call("plan-1", "tools_plan_execute", {"action": "update_plan", "plan": plan.model_dump()}),
            *[AssistantMessage(content="The step is complete, nothing more to do.") for _ in range(10)],
        ]
    )
    harness.settings.enforce_no_progress = True
    run = AgentRun(harness, None, Budget(), tmp_path, [], lambda *_: None, intent={"original_request": {"description": "demo"}})
    with pytest.raises(ExecutionFailure) as stopped:
        asyncio.run(run.plan_execute())
    assert stopped.value.code == "no_progress"
    assert harness.roles.count("executor") == harness.settings.max_no_progress_turns


@pytest.mark.parametrize("loop", ["delegate", "continue", "update_plan"])
def test_repeated_valid_handoffs_stop_without_new_evidence(monkeypatch, tmp_path, loop):
    """重复合法交接仍须停止；改写说明、增加批次或计划版本不能伪装成进展。"""
    from server.remotion_templates.provider import ExecutionFailure

    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FakeSession)
    plan = Plan(goal="inspect", steps=[{
        "id": "inspect", "goal": "inspect", "tool_modules": ["tools"], "done_when": "observed",
    }])
    responses = []
    if loop != "delegate":
        responses = [
            call("delegate", "tools_plan_execute", {"action": "delegate"}),
            call("plan", "tools_plan_execute", {"action": "update_plan", "plan": plan.model_dump()}),
        ]
    for index in range(12):
        blocked = AssistantMessage(content=json.dumps({"status": "blocked", "summary": f"still blocked {index}"}))
        arguments = {"action": loop, "reason": f"try again {index}"}
        if loop == "update_plan":
            arguments["plan"] = plan.model_dump() | {"goal": f"revised wording {index}"}
        control = call(f"retry-{index}", "tools_plan_execute", arguments)
        responses.extend([control, blocked] if loop == "delegate" else [blocked, control])
    harness = FakeHarness(responses)
    harness.settings.enforce_no_progress = True
    harness.settings.max_steps = 32
    run = AgentRun(harness, None, Budget(), tmp_path, [], lambda *_: None)
    with pytest.raises(ExecutionFailure) as stopped:
        asyncio.run(run.plan_execute())
    assert stopped.value.code == "no_progress"
    assert run.stalled_turns == 6
    assert len(harness.roles) <= 10
    assert run.state.calls < run.state.total_limit


def test_advancing_steps_with_valid_handoffs_can_finish(monkeypatch, tmp_path):
    """跨步骤的正常交接超过阈值仍可完成，不把相同状态名一概当成空转。"""
    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FakeSession)
    plan = Plan(goal="inspect", steps=[{
        "id": f"inspect_{index}", "goal": "inspect", "tool_modules": ["tools"], "done_when": "observed",
    } for index in range(4)])
    responses = [
        call("delegate", "tools_plan_execute", {"action": "delegate"}),
        call("plan", "tools_plan_execute", {"action": "update_plan", "plan": plan.model_dump()}),
    ]
    for index in range(4):
        responses.extend([
            call(f"inspect-{index}", "tools_inspect", {"tool_name": "sprite.create"}),
            AssistantMessage(content='{"status":"step_done","summary":"observed"}'),
            call(f"advance-{index}", "tools_plan_execute", {"action": "advance" if index < 3 else "complete"}),
        ])
    responses.append(AssistantMessage(content='{"action":"complete","sprite_id":"sprite-1"}'))
    harness = FakeHarness(responses)
    harness.settings.enforce_no_progress = True
    harness.settings.max_tooluse = 10
    run = AgentRun(harness, None, Budget(), tmp_path, [], lambda *_: None)
    assert asyncio.run(run.plan_execute()) == {"published": "sprite-1"}
    assert run.state.index == 3
    assert len(harness.roles) > harness.settings.max_no_progress_turns
    assert run.stalled_turns == 0


@pytest.mark.parametrize("layer,bad_name", [
    (Layer.OUTER, "preset"),
    (Layer.EXECUTOR, "preset"),
    (Layer.EXECUTOR, "sprite_compose"),
    (Layer.PLAN, "preset_create"),
])
def test_dispatch_reports_only_tools_the_current_layer_can_call(monkeypatch, tmp_path, layer, bad_name):
    """真实分发回执包含纠错名称，同时保留 Executor 模块范围和 Plan 权限边界。"""
    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FakeSession)
    run = AgentRun(FakeHarness([]), None, Budget(), tmp_path, [], lambda *_: None)
    run.layer = layer
    if layer is Layer.EXECUTOR:
        plan = Plan(goal="preset", steps=[{
            "id": "preset", "goal": "preset", "tool_modules": ["preset"], "done_when": "saved",
        }])
        run.state.control(PlanAction(action="update_plan", plan=plan))
    context = run._context_for_layer()
    asyncio.run(run._handle_layer_tools(call("wrong", bad_name, {})))
    error = json.loads(context.messages()[-1]["content"])["error"]
    assert error["code"] == "TOOL_NOT_FOUND"
    offered = {item.name for item in run._tools_for_layer()}
    listed = set(error["message"].split("Available tools: ")[1].split(", "))
    assert listed == offered
    if bad_name == "preset":
        assert "Closest:" in error["message"]
    if layer is Layer.EXECUTOR:
        assert "tools.plan_execute" not in listed and "sprite.compose" not in listed
    assert run.state.calls == 1
    assert run.round_calls[0]["status"] == "fail"


@pytest.mark.parametrize("earlier_success", [False, True])
def test_failed_generation_tool_preserves_previous_generation_state(monkeypatch, tmp_path, earlier_success):
    """首次生成失败仍可解释问题；已经成功开始生成时，后续失败不能解除完成约束。"""
    from server.remotion_templates.tools.registry import ToolFault

    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FakeSession)
    run = AgentRun(FakeHarness([]), None, Budget(), tmp_path, [], lambda *_: None)
    run.session.latest_sprite_id = None
    arguments = {"description": "title", "code": "export default function C(){return null}",
                 "parameter_schema": {"type": "object", "properties": {}}, "default_parameters": {}}
    if earlier_success:
        asyncio.run(run._handle_layer_tools(call("success", "preset_create", arguments)))

    async def fail(*_args):
        """模拟真实代码校验失败，尚未保存任何新的预设。"""
        raise ToolFault("CODE_VALIDATION_FAILED", "Invalid code")

    monkeypatch.setattr(run.session, "execute", fail)
    asyncio.run(run._handle_layer_tools(call("failure", "preset_create", arguments)))
    assert run.generation_started is earlier_success
    result = asyncio.run(run._handle_layer_message(AssistantMessage(content='{"answer":"代码校验失败，请调整需求。"}')))
    if earlier_success:
        assert result is None
        assert "Generation has started" in run.session.feedback[0]
    else:
        assert result.answer == "代码校验失败，请调整需求。"


@pytest.mark.parametrize("layer,status", [(Layer.EXECUTOR, "step_done"), (Layer.PLAN, "plan_done")])
def test_unknown_sprite_reply_is_correctable_without_mutating_plan(monkeypatch, tmp_path, layer, status):
    """模型捏造 Sprite ID 时保留当前层和计划，给出纠错后可接受有效回复。"""
    from server.remotion_templates.tools.registry import ToolFault

    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FakeSession)
    run = AgentRun(FakeHarness([]), None, Budget(), tmp_path, [], lambda *_: None)
    run.layer = layer
    plan = Plan(goal="g", steps=[{"id": "s", "goal": "g", "tool_modules": ["preset"], "done_when": "d"}])
    run.state.control(PlanAction(action="update_plan", plan=plan))
    original = run.session.saved_sprite

    def saved_sprite(identifier):
        """采用真实 Session 的未知 ID 错误类型，避免测试夹具的 KeyError 掩盖行为。"""
        if identifier == "unknown":
            raise ToolFault("SPRITE_NOT_FOUND", "Unknown task Sprite: unknown")
        return original(identifier)

    monkeypatch.setattr(run.session, "saved_sprite", saved_sprite)
    before = run.state.snapshot()
    asyncio.run(run._handle_layer_message(AssistantMessage(content=json.dumps({
        "status": status, "summary": "done", "sprite_id": "unknown",
    }))))
    assert run.layer is layer and run.state.snapshot() == before
    assert run.plan_result is None
    assert run.stalled_turns == 1
    assert "Unknown task Sprite" in run.session.feedback[0]
    asyncio.run(run._handle_layer_message(AssistantMessage(content=json.dumps({
        "status": status, "summary": "done", "sprite_id": "sprite-1",
    }))))
    assert run.layer is (Layer.PLAN if layer is Layer.EXECUTOR else Layer.OUTER)
    assert run.stalled_turns == 0


def test_no_progress_threshold_counts_rounds_instead_of_tool_calls(monkeypatch, tmp_path):
    """同轮四个重复调用只算一次空转；首轮的新观察不能被尾部重复调用抹掉。"""
    from server.remotion_templates.provider import ExecutionFailure

    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FakeSession)
    responses = [AssistantMessage(tool_calls=[
        call(f"inspect-{turn}-{index}", "tools_inspect", {"tool_name": "sprite.create"}).tool_calls[0]
        for index in range(4)
    ]) for turn in range(10)]
    harness = FakeHarness(responses)
    harness.settings.enforce_no_progress = True
    harness.settings.max_steps = 20
    harness.settings.max_tooluse = 4
    run = AgentRun(harness, None, Budget(), tmp_path, [], lambda *_: None)
    with pytest.raises(ExecutionFailure) as stopped:
        asyncio.run(run.plan_execute())
    assert stopped.value.code == "no_progress"
    assert run.stalled_turns == 6
    assert len(harness.roles) == 7  # 一轮新观察后，确实经历六轮空转。
    assert run.state.calls == 28
