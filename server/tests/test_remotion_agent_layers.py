"""Unified routing, recovery and progress regressions using real workspace tools and scripted model turns.

Run: uv run --locked --project server pytest server/tests/test_remotion_agent_layers.py.
Rendering is stubbed; completion evidence and cache-window tests also live in test_remotion_free_effects.py.
"""
import asyncio
import json

import pytest

from server.remotion_templates.agent import Layer
from server.remotion_templates.context import AssistantMessage
from server.remotion_templates.planning import PlanAction, default_plan
from server.remotion_templates.provider import ExecutionFailure
from server.remotion_templates.tools.registry import ToolFault
from server.remotion_templates.tools.workspace import CODE
from .test_remotion_free_effects import ScriptEnd, call, free_run, needs_bundler, report


def delegate():
    """Start the host's single workspace step."""
    return call("outer", "tools_plan_execute", {"action": "delegate", "reason": "做出字效"})


@needs_bundler
def test_delegation_builds_and_saves_without_waking_plan(monkeypatch, tmp_path):
    """默认新建只走工作区步骤，保存并回看后由宿主发布。"""
    run, harness, budget = free_run(monkeypatch, tmp_path, [delegate(), call("save", "effect_save", {}), report("done", summary="已完成")])
    result = asyncio.run(run.plan_execute())
    assert harness.published == [result["published"]]
    assert harness.roles == ["outer", "executor", "executor"]
    assert [step.id for step in run.state.plan.steps] == ["build"]
    assert budget.reply["summary"] == "已完成"


@needs_bundler
def test_plan_can_design_multiple_steps(monkeypatch, tmp_path):
    """自定计划先检查源码，再保存；已完成步骤通过宿主产出传递。"""
    steps = [
        {"id": "draw", "goal": "画出效果", "tool_modules": ["workspace"], "done_when": "检查通过"},
        {"id": "save", "goal": "保存效果", "tool_modules": ["effect"], "input_refs": ["steps.draw.outputs.effect"], "done_when": "保存并回看"},
    ]
    run, harness, _ = free_run(monkeypatch, tmp_path, [
        call("outer", "tools_plan_execute", {"action": "plan"}),
        call("plan", "tools_plan_execute", {"action": "update_plan", "plan": {"goal": "完成", "steps": steps}}),
        call("check", "validate_check", {}), report("drawn", summary="绘制已检查"),
        call("save", "effect_save", {}), report("saved"),
    ])
    result = asyncio.run(run.plan_execute())
    assert harness.published == [result["published"]]
    assert run.state.completed["draw"]["summary"] == "绘制已检查"
    assert harness.roles.count("plan") == 1


@needs_bundler
def test_missing_save_is_correctable(monkeypatch, tmp_path):
    """空口完成不能跳过保存；纠正后同一任务继续成功。"""
    run, harness, _ = free_run(monkeypatch, tmp_path, [delegate(), report("too-soon"), call("save", "effect_save", {}), report("done")])
    result = asyncio.run(run.plan_execute())
    assert "no effect_save succeeded" in json.dumps(harness.windows, ensure_ascii=False)
    assert harness.published == [result["published"]]


@needs_bundler
def test_blocked_step_can_resume(monkeypatch, tmp_path):
    """受阻步骤交给 Plan，continue 后保留工作区并完成。"""
    run, harness, _ = free_run(monkeypatch, tmp_path, [delegate(), report("blocked", status="blocked"),
        call("resume", "tools_plan_execute", {"action": "continue"}), call("save", "effect_save", {}), report("done")])
    asyncio.run(run.plan_execute())
    assert harness.roles == ["outer", "executor", "plan", "executor", "executor"]
    assert len(harness.published) == 1


@pytest.mark.parametrize("payload", [{"action": "answer", "answer": "可以"}, {"action": "ask", "questions": ["需要哪一种效果？"]}])
def test_outer_dialogue_before_building(monkeypatch, tmp_path, payload):
    """问答与真正的澄清不生成版本。"""
    run, harness, _ = free_run(monkeypatch, tmp_path, [call("reply", "tools_plan_execute", payload)])
    result = asyncio.run(run.plan_execute())
    field = "answer" if payload["action"] == "answer" else "questions"
    assert getattr(result, field) == payload[field]
    assert not harness.published


def test_executor_question_routes_to_outer(monkeypatch, tmp_path):
    """尚未保存时，执行层无法消除的歧义由 Outer 提问。"""
    run, harness, _ = free_run(monkeypatch, tmp_path, [delegate(), report("ask", status="needs_input", questions=["哪张参考图？"]),
        call("relay", "tools_plan_execute", {"action": "ask", "questions": ["哪张参考图？"]})])
    assert asyncio.run(run.plan_execute()).questions == ["哪张参考图？"]
    assert harness.roles == ["outer", "executor", "outer"]


@pytest.mark.parametrize("old_tool", ["preset_create", "preset_modify", "sprite_compose", "sprite_create"])
def test_retired_tools_never_execute(monkeypatch, tmp_path, old_tool):
    """旧工具不能通过名称直接执行，也不标记生成已开始。"""
    run, harness, _ = free_run(monkeypatch, tmp_path, [delegate(), call("old", old_tool, {})])
    with pytest.raises(ScriptEnd):
        asyncio.run(run.plan_execute())
    assert "Unknown or out-of-scope tool" in run.session.feedback[0]
    assert not run.generation_started and not harness.published
    assert old_tool not in {tool["function"]["name"] for tool in harness.tools[-1]}


@pytest.mark.parametrize("read", [False, True])
def test_no_progress_is_bounded(monkeypatch, tmp_path, read):
    """无效回复与重复读取均受无进展保护；新的调用 ID 不算新观察。"""
    replies = [call(str(i), "workspace_read", {"path": CODE}) if read else AssistantMessage(content="finished") for i in range(6)]
    run, harness, _ = free_run(monkeypatch, tmp_path, [delegate(), *replies])
    harness.settings.enforce_no_progress = True
    harness.settings.max_no_progress_turns = 3
    with pytest.raises(ExecutionFailure):
        asyncio.run(run.plan_execute())
    assert not harness.published


def test_executor_cannot_control_plan(monkeypatch, tmp_path):
    """执行层不能越层控制计划。"""
    run, harness, _ = free_run(monkeypatch, tmp_path, [delegate(), call("wrong", "tools_plan_execute", {"action": "complete"})])
    with pytest.raises(ScriptEnd):
        asyncio.run(run.plan_execute())
    assert run.layer is Layer.EXECUTOR and not harness.published
    assert "Available tools:" in run.session.feedback[0]


@needs_bundler
def test_repaired_arguments_are_audited_and_execute(monkeypatch, tmp_path):
    """保留多余闭括号的真实循环回归：修复后执行保存，并记录丢弃的字符。"""
    trailing = call("save", "effect_save", {})
    trailing.tool_calls[0].function.arguments = "{}}"
    run, harness, budget = free_run(monkeypatch, tmp_path, [delegate(), trailing, report("done")])
    budget.audit_path = tmp_path / "audit.jsonl"
    asyncio.run(run.plan_execute())
    events = [json.loads(line) for line in budget.audit_path.read_text().splitlines()]
    assert [item["dropped"] for item in events if item["event"] == "tool_arguments_repaired"] == ["}"]
    assert len(harness.published) == 1


def test_curly_quoted_protocol_json_remains_understood(monkeypatch, tmp_path):
    """中文弯引号协议回复仍能解析为问答，不发布候选。"""
    run, harness, _ = free_run(monkeypatch, tmp_path, [AssistantMessage(content="{“answer”:“可以制作暖色调滤镜。”}")])
    assert asyncio.run(run.plan_execute()).answer == "可以制作暖色调滤镜。"
    assert not harness.published


@pytest.mark.parametrize("earlier_success", [False, True])
def test_failed_generation_preserves_previous_generation_state(monkeypatch, tmp_path, earlier_success):
    """首次写入失败仍可解释问题，已有成功写入时失败不能解除交付要求。"""
    run, _, _ = free_run(monkeypatch, tmp_path, [])
    run.state.control(PlanAction(action="update_plan", plan=default_plan(edit=False)))
    run.layer = Layer.EXECUTOR
    args = {"path": CODE, "content": "export default function Effect(){return null}"}
    if earlier_success:
        asyncio.run(run._handle_layer_tools(call("ok", "workspace_write", args)))

    async def fail(*_args):
        """Simulate a failure before any workspace mutation."""
        raise ToolFault("INVALID_ARGUMENT", "Invalid file")

    monkeypatch.setattr(run.session, "execute", fail)
    asyncio.run(run._handle_layer_tools(call("failed", "workspace_write", args)))
    assert run.generation_started is earlier_success
    run.layer = Layer.OUTER
    result = asyncio.run(run._handle_layer_tools(call("answer", "tools_plan_execute", {"action": "answer", "answer": "写入失败"})))
    if earlier_success:
        assert result is None and "an ordinary answer cannot replace it" in run.session.feedback[0]
    else:
        assert result.answer == "写入失败"


@pytest.mark.parametrize("action", ["continue", "update_plan"])
def test_repeated_valid_handoffs_do_not_fake_progress(monkeypatch, tmp_path, action):
    """合法交接、说明变化及计划版本递增不能掩盖连续无进展。"""
    responses = [delegate()]
    for index in range(12):
        arguments = {"action": action, "reason": str(index)}
        if action == "update_plan":
            arguments["plan"] = {"goal": f"revised wording {index}", "steps": [{"id": "inspect", "goal": "inspect", "tool_modules": ["tools"], "done_when": "observed"}]}
        responses += [report(f"blocked-{index}", "blocked", str(index)), call(f"retry-{index}", "tools_plan_execute", arguments)]
    run, harness, _ = free_run(monkeypatch, tmp_path, responses)
    harness.settings.enforce_no_progress = True
    run.state.max_tooluse = 40
    with pytest.raises(ExecutionFailure) as stopped:
        asyncio.run(run.plan_execute())
    assert stopped.value.code == "no_progress" and run.stalled_turns == 6
    assert run.state.calls < run.state.total_limit


def test_policy_locations_do_not_create_new_evidence(monkeypatch, tmp_path):
    """同一条能力违规换了行列仍是相同观察，真正不同的违规才算新证据。"""
    run, _, _ = free_run(monkeypatch, tmp_path, [])
    def refusal(capability, line):
        """A located policy error from the shared validation gate."""
        return {"code": "CODE_VALIDATION_FAILED", "message": f"Unsupported capability: {capability} (line {line}, column 7)"}
    key = run._receipt_key("error:effect_save", refusal("fetch", 5))
    assert run._receipt_key("error:effect_save", refusal("fetch", 9)) == key
    assert run._receipt_key("error:effect_save", refusal("eval", 5)) != key


def test_no_progress_counts_rounds_instead_of_calls(monkeypatch, tmp_path):
    """同轮四个相同调用只算一轮空转，首轮新观察不被该轮后续重复调用抹掉。"""
    responses = [delegate(), *[AssistantMessage(tool_calls=[
        call(f"inspect-{turn}-{index}", "tools_inspect", {"tool_name": "effect.save"}).tool_calls[0]
        for index in range(4)
    ]) for turn in range(10)]]
    run, harness, _ = free_run(monkeypatch, tmp_path, responses)
    harness.settings.enforce_no_progress = True
    run.state.max_tooluse = 40
    with pytest.raises(ExecutionFailure) as stopped:
        asyncio.run(run.plan_execute())
    assert stopped.value.code == "no_progress" and run.stalled_turns == 6
    assert harness.roles.count("executor") == 7
