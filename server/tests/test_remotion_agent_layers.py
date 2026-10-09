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
