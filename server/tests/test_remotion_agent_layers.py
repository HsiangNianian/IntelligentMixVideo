"""Offline tests for the Outer → host plan → Executor protocol with scripted model turns.

Covers the Outer control-only window, the host's standard two-step plan, tools_report_step with
host-recorded outputs, automatic batch continuation, Plan recovery and the no-progress guard.
Run: `uv run --locked pytest tests/test_remotion_agent_layers.py -v`.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.remotion_templates.agent import AgentRun, Layer
from server.remotion_templates.context import AssistantMessage
from server.remotion_templates.planning import Plan, PlanAction, default_plan
from server.remotion_templates.provider import Budget, ExecutionFailure
from server.remotion_templates.settings import Settings
from server.remotion_templates.tools.catalog import PLAN_TOOL, REPORT_TOOL


class FakeSession:
    """Small task-owned session exposing only the APIs AgentRun uses.

    Preset tools answer with a preset_id, preset_modify with a draft_id, and sprite_create saves a
    new Sprite like the real tool, so the host can record each step's outputs from the receipts.
    """

    def __init__(self, _harness, _spec, _base, _budget, directory, images, _stage, intent):
        self.directory = Path(directory)
        self.images = images
        self.intent = intent or {"original_request": {"description": "demo"}}
        self.feedback = []
        self.preset_drafts = {}
        self.latest_sprite_id = "sprite-1"
        self.saved_sprites = {"sprite-1": {"sprite_id": "sprite-1"}}
        if (intent or {}).get("accepted_base"):
            self.preset_drafts["base_title"] = {"description": "base"}

    def snapshot(self, **_options):
        """Return deterministic host facts for all three prompts."""
        return {"user_intent": self.intent, "sprites": sorted(self.saved_sprites)}

    def saved_sprite(self, identifier):
        """Resolve only the task's exact saved Sprite."""
        return self.saved_sprites[identifier]

    async def execute(self, name, args, _catalog):
        """Return one deterministic business tool observation."""
        data = {"tool": name, "args": args.model_dump(mode="json")}
        if name == "sprite.create":
            identifier = f"sprite-{len(self.saved_sprites) + 1}"
            self.saved_sprites[identifier] = {"sprite_id": identifier}
            self.latest_sprite_id = identifier
            data["sprite_id"] = identifier
        elif name == "preset.modify":
            data["draft_id"] = "preset_draft_1"
        elif name in {"preset.create", "preset.search"} and getattr(args, "preset_id", "new") is not None:
            data["preset"] = {"preset_id": getattr(args, "preset_id", None) or "preset-new"}
        return data


class ScriptEnd(Exception):
    """The scripted model turns ran out; a test that stops mid-task expects this."""


class FakeHarness:
    """Script model turns while recording the role sequence and finalization."""

    def __init__(self, responses):
        self.responses = iter(responses)
        self.roles = []
        self.systems = {}
        self.settings = SimpleNamespace(
            max_steps=4,
            max_tooluse=3,
            enforce_no_progress=False,
            max_no_progress_turns=Settings.model_fields["max_no_progress_turns"].default,
        )

    async def _turn(self, system, _context, _tools, _budget, _images, *, phase):
        """Return the next scripted response for one exact model role."""
        self.roles.append(phase)
        self.systems.setdefault(phase, system)
        try:
            return next(self.responses)
        except StopIteration:
            raise ScriptEnd from None

    async def finalize_sprite(self, _session, identifier, _budget, _directory):
        """Return a marker proving the host published exactly this Sprite."""
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


def delegate(identifier="outer-1"):
    """The Outer's delegation of a generation task."""
    return call(identifier, "tools_plan_execute", {"action": "delegate", "reason": "build it"})


def report(identifier, status="step_done", summary="done"):
    """The Executor's tools_report_step call."""
    return call(identifier, "tools_report_step", {"status": status, "summary": summary})


def compose(identifier):
    """A sprite_compose call over one stored Preset."""
    return call(identifier, "sprite_compose", {
        "description": "滤镜",
        "instances": [{
            "instance_id": "filter",
            "source": {"kind": "stored", "preset_id": "preset-1"},
            "layout": {"x": 0, "y": 0, "width": 1080, "height": 1920, "z_index": 0},
            "timing": {"start_frame": 0, "duration_frames": 150},
        }],
    })


def watch_feedback(run, harness):
    """Record the steering the host showed before each model turn; the returned list grows as the run proceeds."""
    seen = []
    scripted = harness._turn

    async def turn(system, context, tools, budget, images, *, phase):
        """Record the feedback in force, then return the next scripted reply."""
        seen.append(list(run.session.feedback))
        return await scripted(system, context, tools, budget, images, phase=phase)

    harness._turn = turn
    return seen


def new_run(monkeypatch, tmp_path, responses, intent=None, **settings):
    """An AgentRun over the fake session and scripted harness; limits are read once, so pass them here."""
    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FakeSession)
    harness = FakeHarness(responses)
    vars(harness.settings).update(settings)
    run = AgentRun(harness, None, Budget(), tmp_path, [], lambda *_: None, intent=intent or {"original_request": {"description": "demo"}})
    return run, harness


def test_a_delegation_runs_the_host_plan_without_the_plan_model(monkeypatch, tmp_path):
    """回放暖色调滤镜请求：委派后宿主直接建立标准计划，执行层准备预设、组合并保存，宿主自动推进并发布，Plan 模型一次都不调用。"""
    run, harness = new_run(monkeypatch, tmp_path, [
        delegate(),
        call("exec-1", "preset_search", {"preset_id": "preset-1"}),
        report("exec-2", summary="预设已就绪"),
        compose("exec-3"),
        call("exec-4", "sprite_create", {"draft_id": "sprite_draft_1"}),
        report("exec-5", summary="已保存"),
    ])
    assert [tool.name for tool in run._tools_for_layer()] == [PLAN_TOOL.name]
    assert asyncio.run(run.plan_execute()) == {"published": "sprite-2"}
    assert harness.roles == ["outer"] + ["executor"] * 5
    assert [step.id for step in run.state.plan.steps] == ["preset", "sprite"]
    assert run.state.completed["preset"]["presets"] == [{"kind": "stored", "preset_id": "preset-1"}]
    assert run.state.result["sprite_id"] == "sprite-2"
    assert run.stalled_turns == 0


def test_the_executor_window_offers_the_step_tools_and_report_step_only(monkeypatch, tmp_path):
    """执行层只看到当前步骤模块的工具和 tools_report_step；计划控制不在其中，延后实现的工具也不出现。"""
    run, _ = new_run(monkeypatch, tmp_path, [])
    run.state.control(PlanAction(action="update_plan", plan=default_plan(edit=False)))
    run.layer = Layer.EXECUTOR
    names = {tool.name for tool in run._tools_for_layer()}
    assert names == {"preset.create", "preset.search", "preset.modify", "tools.inspect", REPORT_TOOL.name}


def test_every_layer_is_told_the_protocol_it_must_follow(monkeypatch, tmp_path):
    """三层系统提示都写明 inspect 不能搜索工具；Plan 知道组合与保存同在一步，执行层知道用 report_step 与句柄。"""
    run, harness = new_run(monkeypatch, tmp_path, [
        delegate(),
        report("exec-1", "blocked", "缺少信息"),
        call("plan-1", "tools_plan_execute", {"action": "complete"}),
        call("outer-2", "tools_plan_execute", {"action": "complete", "sprite_id": "sprite-1"}),
    ])
    assert asyncio.run(run.plan_execute()) == {"published": "sprite-1"}
    assert set(harness.systems) == {"outer", "plan", "executor"}
    for role, system in harness.systems.items():
        assert "cannot search or list tools" in system, role
    assert "compose AND save" in harness.systems["plan"]
    assert "tools_report_step" in harness.systems["executor"]
    assert "never re-send source" in harness.systems["executor"]
    # The Executor gets one working Preset shape to imitate instead of guessing the contract.
    assert "export default function WarmTint" in harness.systems["executor"]
    # The Executor sees its current step; the counters it sees are remaining calls, not used/limit pairs.
    assert '"current_step"' in harness.systems["executor"] and '"tool_calls_left_in_batch"' in harness.systems["executor"]
    assert "actual_tooluse" not in harness.systems["executor"]


def test_step_done_without_saving_the_sprite_is_refused_with_the_next_tool(monkeypatch, tmp_path):
    """回放 bb0decec：组合后没保存就报完成，宿主拒绝并指明调用 sprite_create {draft_id}；保存后才发布。"""
    run, harness = new_run(monkeypatch, tmp_path, [
        delegate(),
        call("exec-1", "preset_create", {"description": "暖色", "code": "export default function C(){return null}", "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": False}, "default_parameters": {}}),
        report("exec-2"),
        compose("exec-3"),
        report("exec-4", summary="草稿已组合"),
        call("exec-5", "sprite_create", {"draft_id": "sprite_draft_1"}),
        report("exec-6"),
    ])
    feedback = watch_feedback(run, harness)
    assert asyncio.run(run.plan_execute()) == {"published": "sprite-2"}
    refused = feedback[5]
    assert refused and "not done" in refused[0] and "sprite_create" in refused[0] and "draft_id" in refused[0]
    assert "plan" not in harness.roles
    # Accepting the step ends that steering, so it cannot mislead what follows.
    assert run.session.feedback == []


def test_a_preset_step_needs_a_ready_preset(monkeypatch, tmp_path):
    """准备预设的步骤没有读取、创建或复制任何预设时不能报完成，拒绝信息给出可用的工具。"""
    run, harness = new_run(monkeypatch, tmp_path, [
        delegate(),
        report("exec-1"),
        call("exec-2", "preset_modify", {"preset_id": "preset-1", "changes": {"description": "更淡"}}),
        report("exec-3"),
    ])
    feedback = watch_feedback(run, harness)
    with pytest.raises(ScriptEnd):
        asyncio.run(run.plan_execute())
    assert "no Preset is ready" in feedback[2][0] and "preset_search" in feedback[2][0]
    assert run.state.completed["preset"]["presets"] == [{"kind": "draft", "draft_id": "preset_draft_1"}]
    assert run.state.step.id == "sprite"
    assert run.state.snapshot()["step_inputs"] == {"steps.preset.outputs.presets": [{"kind": "draft", "draft_id": "preset_draft_1"}]}


def test_an_edit_can_reuse_the_base_drafts(monkeypatch, tmp_path):
    """编辑任务只调整参数或编排时，基线草稿就是准备好的预设，预设步骤可直接完成并把它们交给组合步骤。"""
    run, harness = new_run(monkeypatch, tmp_path, [delegate(), report("exec-1")], intent={
        "original_request": {"description": "demo"}, "accepted_base": {"name": "old"},
    })
    with pytest.raises(ScriptEnd):
        asyncio.run(run.plan_execute())
    assert "accepted_base" in run.state.plan.steps[0].input_refs
    assert run.state.completed["preset"]["presets"] == [{"kind": "draft", "draft_id": "base_title"}]


def test_a_surplus_closing_brace_does_not_waste_the_call_and_is_audited(monkeypatch, tmp_path):
    """回放 bb0decec：28k token 的 sprite_create 只因结尾多一个括号失败；现在照常执行，并在私有审计中记录被丢弃的文本。"""
    trailing = AssistantMessage(tool_calls=[{
        "id": "exec-2", "type": "function",
        "function": {"name": "sprite_create", "arguments": '{"draft_id": "sprite_draft_1"}}'},
    }])
    run, _ = new_run(monkeypatch, tmp_path, [
        delegate(),
        call("exec-1", "preset_search", {"preset_id": "preset-1"}),
        report("exec-x"),
        trailing,
        report("exec-3"),
    ])
    run.budget.audit_path = tmp_path / "audit.jsonl"
    assert asyncio.run(run.plan_execute()) == {"published": "sprite-2"}
    events = [json.loads(line) for line in run.budget.audit_path.read_text().splitlines()]
    assert [item["dropped"] for item in events if item["event"] == "tool_arguments_repaired"] == ["}"]


def test_the_step_reports_the_sprite_it_saved_last(monkeypatch, tmp_path):
    """同一步骤保存了两次：宿主记录最后保存的 Sprite 作为步骤产出并发布它，模型无需也无法指定 ID。"""
    run, harness = new_run(monkeypatch, tmp_path, [
        delegate(),
        call("exec-1", "preset_search", {"preset_id": "preset-1"}),
        report("exec-2"),
        call("exec-3", "sprite_create", {"draft_id": "a"}),
        call("exec-4", "sprite_create", {"draft_id": "b"}),
        report("exec-5"),
    ], max_tooluse=10)
    assert asyncio.run(run.plan_execute()) == {"published": "sprite-3"}
    assert run.step_sprites == {1: ["sprite-2", "sprite-3"]}


def test_a_progressing_step_continues_past_the_batch_limit_without_the_plan(monkeypatch, tmp_path):
    """回放 2db032a4 的误判：批次用尽不是任务失败；本批有新观察时宿主自动续批，不唤醒 Plan。"""
    run, harness = new_run(monkeypatch, tmp_path, [
        delegate(),
        call("exec-1", "preset_search", {"query": "暖"}),
        call("exec-2", "preset_search", {"query": "filter"}),
        call("exec-3", "preset_search", {"preset_id": "preset-1"}),
        report("exec-4"),
        compose("exec-5"),
        call("exec-6", "sprite_create", {"draft_id": "sprite_draft_1"}),
        report("exec-7"),
    ], max_tooluse=2)
    assert asyncio.run(run.plan_execute()) == {"published": "sprite-2"}
    assert "plan" not in harness.roles
    assert run.state.batches == 4


def test_a_batch_without_new_evidence_wakes_the_plan(monkeypatch, tmp_path):
    """整批都是重复调用时不自动续批，由 Plan 决定继续或停止。"""
    same = {"tool_name": "sprite.create"}
    run, harness = new_run(monkeypatch, tmp_path, [
        delegate(),
        call("exec-1", "tools_inspect", same),
        call("exec-2", "tools_inspect", same),
        call("exec-3", "tools_inspect", same),
        call("exec-4", "tools_inspect", same),
        call("exec-5", "tools_inspect", same),
        call("exec-6", "tools_inspect", same),
        call("plan-1", "tools_plan_execute", {"action": "stop", "reason": "无进展"}),
        call("outer-2", "tools_plan_execute", {"action": "stop", "reason": "无进展"}),
    ])
    with pytest.raises(ExecutionFailure) as stopped:
        asyncio.run(run.plan_execute())
    assert stopped.value.code == "agent_stopped"
    # The first batch saw a new descriptor and was continued; the second only repeated it.
    assert harness.roles == ["outer"] + ["executor"] * 6 + ["plan", "outer"]
    assert run.state.result["status"] == "tool_limit_reached"


def test_a_blocked_step_wakes_the_plan_which_can_continue_it(monkeypatch, tmp_path):
    """步骤受阻时才唤醒 Plan；Plan continue 后执行层在同一步骤上补齐产出并完成。"""
    run, harness = new_run(monkeypatch, tmp_path, [
        delegate(),
        report("exec-1", "blocked", "找不到预设"),
        call("plan-1", "tools_plan_execute", {"action": "continue", "reason": "换个关键词"}),
        call("exec-2", "preset_search", {"preset_id": "preset-1"}),
        report("exec-3"),
        compose("exec-4"),
        call("exec-5", "sprite_create", {"draft_id": "sprite_draft_1"}),
        report("exec-6"),
    ])
    assert asyncio.run(run.plan_execute()) == {"published": "sprite-2"}
    assert harness.roles == ["outer", "executor", "plan"] + ["executor"] * 5


def test_outer_answers_and_asks_through_the_control_tool(monkeypatch, tmp_path):
    """Outer 用 answer/ask 动作回答或澄清，不需要在文本里手写 JSON。"""
    run, _ = new_run(monkeypatch, tmp_path, [call("outer-1", "tools_plan_execute", {"action": "answer", "answer": "可以制作暖色调滤镜。"})])
    run.session.latest_sprite_id = None  # a new task has saved nothing yet
    assert asyncio.run(run.plan_execute()).answer == "可以制作暖色调滤镜。"
    run, _ = new_run(monkeypatch, tmp_path, [call("outer-1", "tools_plan_execute", {"action": "ask", "questions": ["要多暖？"]})])
    assert asyncio.run(run.plan_execute()).questions == ["要多暖？"]


def test_curly_quoted_protocol_json_is_still_understood(monkeypatch, tmp_path):
    """回放 2db032a4：Outer 用中文弯引号写协议 JSON，宿主归一后照常解析。"""
    run, _ = new_run(monkeypatch, tmp_path, [AssistantMessage(content="{“questions”:[“要多暖？”]}")])
    assert asyncio.run(run.plan_execute()).questions == ["要多暖？"]


def test_executor_cannot_call_plan_control(monkeypatch, tmp_path):
    """执行层调用计划控制会被拒绝且不推进计划；需要输入时经 Plan 回到 Outer 提问。"""
    run, _ = new_run(monkeypatch, tmp_path, [
        delegate(),
        call("exec-1", "tools_plan_execute", {"action": "advance"}),
        report("exec-2", "needs_input", "需要更多输入"),
        AssistantMessage(content=json.dumps({"status": "needs_input", "summary": "stop", "questions": ["需要更多输入"]})),
        call("outer-2", "tools_plan_execute", {"action": "ask", "questions": ["需要更多输入"]}),
    ])
    result = asyncio.run(run.plan_execute())
    assert result.questions == ["需要更多输入"]
    assert run.state.index == 0


def test_repeated_invalid_executor_replies_stop_the_run(monkeypatch, tmp_path):
    """Prose that never reports the step is a stall: the guard ends the run after the threshold."""
    run, harness = new_run(monkeypatch, tmp_path, [
        delegate(),
        *[AssistantMessage(content="The step is complete, nothing more to do.") for _ in range(10)],
    ])
    harness.settings.enforce_no_progress = True
    with pytest.raises(ExecutionFailure) as stopped:
        asyncio.run(run.plan_execute())
    assert stopped.value.code == "no_progress"
    assert harness.roles.count("executor") == harness.settings.max_no_progress_turns


@pytest.mark.parametrize("loop", ["continue", "update_plan", "delegate"])
def test_repeated_valid_handoffs_stop_without_new_evidence(monkeypatch, tmp_path, loop):
    """重复合法交接仍须停止；改写说明、增加批次或计划版本不能伪装成进展。"""
    steps = {"goal": "g", "steps": [{"id": "inspect", "goal": "inspect", "tool_modules": ["tools"], "done_when": "observed"}]}
    responses = [delegate()]
    for index in range(12):
        blocked = report(f"blocked-{index}", "blocked", f"still blocked {index}")
        if loop == "delegate":
            responses.extend([
                blocked,
                call(f"stop-{index}", "tools_plan_execute", {"action": "stop", "reason": f"r{index}"}),
                delegate(f"again-{index}"),
                call(f"resume-{index}", "tools_plan_execute", {"action": "continue"}),
            ])
            continue
        arguments = {"action": loop, "reason": f"try again {index}"}
        if loop == "update_plan":
            arguments["plan"] = steps | {"goal": f"revised wording {index}"}
        responses.extend([blocked, call(f"retry-{index}", "tools_plan_execute", arguments)])
    run, harness = new_run(monkeypatch, tmp_path, responses, enforce_no_progress=True, max_steps=32)
    with pytest.raises(ExecutionFailure) as stopped:
        asyncio.run(run.plan_execute())
    assert stopped.value.code == "no_progress"
    assert run.stalled_turns == 6
    assert len(harness.roles) <= 12
    assert run.state.calls < run.state.total_limit


def test_advancing_plan_steps_can_finish_past_the_threshold(monkeypatch, tmp_path):
    """Plan 改写后的多步骤计划由宿主逐步推进；跨步骤的正常交接超过阈值仍可完成。"""
    plan = {"goal": "inspect", "steps": [{
        "id": f"inspect_{index}", "goal": "inspect", "tool_modules": ["tools"], "done_when": "observed",
    } for index in range(4)]}
    responses = [
        delegate(),
        report("blocked", "blocked", "需要改计划"),
        call("plan", "tools_plan_execute", {"action": "update_plan", "plan": plan}),
    ]
    for index in range(4):
        responses.extend([
            call(f"inspect-{index}", "tools_inspect", {"tool_name": ["sprite.create", "preset.create", "preset.search", "sprite.compose"][index]}),
            report(f"done-{index}"),
        ])
    run, harness = new_run(monkeypatch, tmp_path, responses, enforce_no_progress=True, max_steps=8, max_tooluse=10)
    assert asyncio.run(run.plan_execute()) == {"published": "sprite-1"}
    assert run.state.index == 3
    assert len(harness.roles) > harness.settings.max_no_progress_turns
    assert run.stalled_turns == 0


@pytest.mark.parametrize("layer,bad_name", [
    (Layer.OUTER, "preset_create"),
    (Layer.EXECUTOR, "preset"),
    (Layer.EXECUTOR, "tools_plan_execute"),
    (Layer.PLAN, "preset_create"),
])
def test_dispatch_reports_only_tools_the_current_layer_can_call(monkeypatch, tmp_path, layer, bad_name):
    """真实分发回执包含纠错名称，同时保留 Executor 模块范围和 Outer/Plan 只用控制工具的边界。"""
    run, _ = new_run(monkeypatch, tmp_path, [])
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

    run, _ = new_run(monkeypatch, tmp_path, [])
    run.session.latest_sprite_id = None
    run.state.control(PlanAction(action="update_plan", plan=default_plan(edit=False)))
    run.layer = Layer.EXECUTOR
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
    run.layer = Layer.OUTER
    result = asyncio.run(run._handle_layer_tools(call("answer", "tools_plan_execute", {"action": "answer", "answer": "代码校验失败，请调整需求。"})))
    if earlier_success:
        assert result is None
        assert "Generation has started" in run.session.feedback[0]
    else:
        assert result.answer == "代码校验失败，请调整需求。"


def test_unknown_sprite_in_a_plan_result_is_correctable_without_mutating_plan(monkeypatch, tmp_path):
    """Plan 结果捏造 Sprite ID 时保留当前层和计划，给出纠错后可接受有效回复。"""
    from server.remotion_templates.tools.registry import ToolFault

    run, _ = new_run(monkeypatch, tmp_path, [])
    run.layer = Layer.PLAN
    run.state.control(PlanAction(action="update_plan", plan=default_plan(edit=False)))
    original = run.session.saved_sprite

    def saved_sprite(identifier):
        """采用真实 Session 的未知 ID 错误类型，避免测试夹具的 KeyError 掩盖行为。"""
        if identifier == "unknown":
            raise ToolFault("SPRITE_NOT_FOUND", "Unknown task Sprite: unknown")
        return original(identifier)

    monkeypatch.setattr(run.session, "saved_sprite", saved_sprite)
    before = run.state.snapshot()
    asyncio.run(run._handle_layer_message(AssistantMessage(content=json.dumps({
        "status": "plan_done", "summary": "done", "sprite_id": "unknown",
    }))))
    assert run.layer is Layer.PLAN and run.state.snapshot() == before
    assert run.plan_result is None
    assert run.stalled_turns == 1
    assert "Unknown task Sprite" in run.session.feedback[0]
    asyncio.run(run._handle_layer_message(AssistantMessage(content=json.dumps({
        "status": "plan_done", "summary": "done", "sprite_id": "sprite-1",
    }))))
    assert run.layer is Layer.OUTER
    assert run.stalled_turns == 0


def test_a_repeated_policy_violation_is_the_same_observation_wherever_it_sits(monkeypatch, tmp_path):
    """审计复现：策略诊断的文字里带「(line N, column M)」，同一条违规换了行号就成了新观察，重复犯同一个错不再计入无进展。"""
    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FakeSession)
    run = AgentRun(FakeHarness([]), None, Budget(), tmp_path, [], lambda *_: None)

    def refusal(capability, line):
        """The tool error sprite.create returns for one policy violation at the given line."""
        message = f"Unsupported capability: {capability} (line {line}, column 7)"
        diagnostic = {"source": "contract", "severity": "error", "message": message,
                      "range": {"start": {"line": line - 1, "character": 6}, "end": {"line": line - 1, "character": 11}}}
        return {"code": "CODE_VALIDATION_FAILED", "message": "Sprite code validation failed.",
                "details": {"validation": {"passed": False, "diagnostics": [diagnostic]}}}

    key = run._receipt_key("error:sprite_create", refusal("fetch", 5))
    assert run._receipt_key("error:sprite_create", refusal("fetch", 9)) == key
    assert run._receipt_key("error:sprite_create", refusal("eval", 5)) != key




def test_completion_refusal_says_when_the_task_saved_no_sprite(monkeypatch, tmp_path):
    """Outer 反复提交基线版本的 Sprite ID 40 次：任务一个 Sprite 都没存时，拒绝原因要明说并给出下一步，而不是「必须引用最新 Sprite」。"""
    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FakeSession)
    run = AgentRun(FakeHarness([]), None, Budget(), tmp_path, [], lambda *_: None)
    run.session.latest_sprite_id = None
    with pytest.raises(ValueError, match="saved no Sprite yet") as refusal:
        asyncio.run(run._finalize_outer("c2e32b55bdaa4e9a8e46e7a073fb2b3b"))
    assert "sprite_create" in str(refusal.value)
    run.session.latest_sprite_id = "sprite-2"
    with pytest.raises(ValueError, match="latest task Sprite"):
        asyncio.run(run._finalize_outer("sprite-1"))




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
