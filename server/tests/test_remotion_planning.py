"""Plan 契约：引用形式与产出模块校验、只有 update_plan 接收计划、只收未完成步骤的 update_plan、剩余额度快照、宿主标准计划及模型可见 Schema 的说明。

在 server/ 目录执行 `uv run --locked pytest tests/test_remotion_planning.py -v`。
"""

import json

import pytest
from pydantic import ValidationError

from server.remotion_templates.planning import Plan, PlanAction


def step(identifier, refs, modules=("preset",)):
    """构造一个带指定引用与模块的步骤。"""
    return {
        "id": identifier,
        "goal": f"{identifier} 步骤",
        "input_refs": list(refs),
        "tool_modules": list(modules),
        "done_when": "完成",
    }


@pytest.mark.parametrize(
    "refs",
    [
        ["user_intent"],
        ["accepted_base"],
        ["user_intent", "steps.first.outputs.sprite"],
        ["steps.first.outputs.presets"],
    ],
)
def test_accepted_references_validate(refs):
    """文档写明的引用形式都必须被接受，包括引用前序步骤保存的 Sprite 与准备好的预设。"""
    plan = Plan(
        goal="组合", steps=[step("first", ["user_intent"], ("preset", "sprite")), step("second", refs)]
    )
    assert plan.steps[1].input_refs == refs


@pytest.mark.parametrize(
    "refs",
    [
        ["steps.second.outputs.sprite"],
        ["first"],
        ["steps.first.output"],
        ["steps.first.outputs.sprite.extra"],
    ],
)
def test_rejected_references_report_the_accepted_forms(refs):
    """向前引用、裸步骤名与畸形路径都被拒绝，错误信息给出可接受形式。"""
    with pytest.raises(ValidationError) as failure:
        Plan(
            goal="组合", steps=[step("first", ["user_intent"]), step("second", refs)]
        )
    assert (
        "Inputs must reference user_intent, accepted_base, steps.<earlier_id>.outputs.sprite, "
        "steps.<earlier_id>.outputs.presets or steps.<earlier_id>.outputs.effect"
        in str(failure.value)
    )


def test_model_visible_schema_documents_accepted_references():
    """Plan 的 JSON Schema 必须写明可接受的引用形式，否则模型只能靠报错试错。"""
    schema = PlanAction.model_json_schema()
    described = schema["$defs"]["Step"]["properties"]["input_refs"]["description"]
    assert "user_intent" in described
    assert "accepted_base" in described
    assert "steps.<earlier_step_id>.outputs.sprite" in described
    # 说明必须真的落在模型收到的 Schema 文本里，而不是只存在于 Python 注释中。
    assert "steps.<earlier_step_id>.outputs.sprite" in json.dumps(
        schema, ensure_ascii=False
    )


def test_delegate_refuses_a_plan_it_would_ignore():
    """审计复现：delegate 携带的 plan 曾被校验接受，随后被宿主标准计划静默替换；现在直接拒绝，只有 update_plan 接收计划。"""
    plan = {"goal": "组合", "steps": [step("first", ["user_intent"])]}
    with pytest.raises(ValidationError, match="Only update_plan accepts a plan"):
        PlanAction(action="delegate", reason="做滤镜", plan=plan)
    assert PlanAction(action="delegate", reason="做滤镜").plan is None
    assert PlanAction(action="update_plan", plan=plan).plan.steps[0].id == "first"


def test_reference_requires_a_saved_output_before_dispatch():
    """合法语法不等于已取得产物：前一步没有 Sprite 时不能唤醒依赖它的下一步。"""
    from server.remotion_templates.planning import ExecutionState

    state = ExecutionState(4, 3)
    plan = Plan(goal="组合", steps=[step("first", ["user_intent"], ("sprite",)), step("second", ["steps.first.outputs.sprite"])])
    state.control(PlanAction(action="update_plan", plan=plan))
    state.finish_batch("step_done", sprite_id=None)
    before = state.snapshot()
    with pytest.raises(ValueError, match="finished without sprite"):
        state.control(PlanAction(action="advance"))
    assert state.snapshot() == before
    state.finish_batch("step_done", sprite_id="saved-sprite")
    state.control(PlanAction(action="advance"))
    assert state.snapshot()["step_inputs"] == {"steps.first.outputs.sprite": "saved-sprite"}


def test_a_reference_to_an_output_the_step_cannot_produce_is_refused_when_planned():
    """回放 bb0decec：引用仅有 preset 模块步骤的 Sprite，在 update_plan 时就被拒绝，而不是执行完再拒绝。"""
    from server.remotion_templates.planning import ExecutionState

    state = ExecutionState(4, 3)
    bad = {"goal": "组合", "steps": [step("build", ["user_intent"]), step("compose", ["steps.build.outputs.sprite"], ("sprite",))]}
    with pytest.raises(ValueError, match="has no sprite module"):
        state.control(PlanAction(action="update_plan", plan=bad))
    assert state.plan is None and state.batches == 0


def test_update_plan_keeps_completed_steps_and_takes_only_pending_ones():
    """已完成步骤由宿主保留：只发送未完成步骤即可，重发的已完成步骤按 ID 忽略，改写其文字也不会被拒。"""
    from server.remotion_templates.planning import ExecutionState

    state = ExecutionState(4, 3)
    state.control(PlanAction(action="update_plan", plan={"goal": "组合", "steps": [step("first", ["user_intent"]), step("second", ["user_intent"])]}))
    state.finish_batch("step_done", presets=[{"kind": "stored", "preset_id": "p1"}])
    rewritten = {**step("first", ["user_intent"]), "goal": "改写过的已完成步骤"}
    state.control(PlanAction(action="update_plan", plan={
        "goal": "组合", "steps": [rewritten, step("save", ["steps.first.outputs.presets"], ("preset", "sprite"))],
    }))
    assert [item.id for item in state.plan.steps] == ["first", "save"]
    assert state.plan.steps[0].goal == "first 步骤"
    assert state.step.id == "save"
    assert state.snapshot()["step_inputs"] == {"steps.first.outputs.presets": [{"kind": "stored", "preset_id": "p1"}]}


def test_update_plan_names_the_completed_steps_it_kept():
    """审计复现：Plan 想用同一 ID 重做已完成步骤时，新步骤被静默丢弃；现在返回被保留的步骤 ID，只剩已完成步骤时报错并说明改用新 ID。"""
    from server.remotion_templates.planning import ExecutionState

    state = ExecutionState(4, 3)
    state.control(PlanAction(action="update_plan", plan={"goal": "组合", "steps": [step("first", ["user_intent"]), step("second", ["user_intent"])]}))
    state.finish_batch("step_done", presets=[{"kind": "stored", "preset_id": "p1"}])
    redo = {**step("first", ["user_intent"]), "goal": "重写预设"}
    assert state.control(PlanAction(action="update_plan", plan={"goal": "组合", "steps": [redo, step("second", ["user_intent"])]})) == ["first"]
    assert state.control(PlanAction(action="continue")) == []
    with pytest.raises(ValueError, match="first already finished") as refused:
        state.control(PlanAction(action="update_plan", plan={"goal": "组合", "steps": [redo]}))
    assert "new step id" in str(refused.value)


def test_snapshot_shows_remaining_tool_calls_instead_of_raw_counters():
    """回放 2db032a4：快照只给剩余额度，不再并列已用总数与单批上限，避免模型误读为预算耗尽。"""
    from server.remotion_templates.planning import ExecutionState, default_plan

    state = ExecutionState(8, 10)
    state.control(PlanAction(action="update_plan", plan=default_plan(edit=False)))
    for _ in range(3):
        state.consume()
    snapshot = state.snapshot()
    assert snapshot["tool_calls_left_in_batch"] == 7
    assert snapshot["tool_calls_left_in_task"] == 77
    assert not {"limits", "batches", "actual_tooluse", "total_tooluse"} & set(snapshot)


@pytest.mark.parametrize("edit", [False, True])
def test_default_plan_composes_and_saves_in_one_step(edit):
    """宿主标准计划：先准备预设，再在同一个带 sprite 模块的步骤中组合并保存。"""
    from server.remotion_templates.planning import default_plan

    plan = default_plan(edit=edit)
    assert [item.id for item in plan.steps] == ["preset", "sprite"]
    assert plan.steps[1].tool_modules == ["preset", "sprite"]
    assert plan.steps[1].input_refs == ["user_intent", "steps.preset.outputs.presets"]
    assert ("accepted_base" in plan.steps[0].input_refs) is edit


def test_a_legacy_base_is_not_offered_base_drafts_it_does_not_have():
    """审计复现：没有 Sprite 的旧版基线也走编辑计划，提示却让执行层复用并不存在的 base_* 草稿；现在改为提示按 legacy_code 写预设。"""
    from server.remotion_templates.planning import default_plan

    legacy = default_plan(edit=True, base_drafts=False)
    assert "accepted_base" in legacy.steps[0].input_refs
    assert "base_*" not in legacy.steps[0].goal and "base_*" not in legacy.steps[1].goal
    assert "base.legacy_code" in legacy.steps[0].goal
    sprite_base = default_plan(edit=True)
    assert "base_* drafts" in sprite_base.steps[0].goal and "base_* drafts" in sprite_base.steps[1].goal
    assert "base_*" not in default_plan(edit=False).steps[1].goal


def test_an_edit_plan_asks_for_exact_edits_of_the_base_drafts():
    """审计复现 a1cccb1c：编辑计划曾让执行层「用 preset_create 存一份改好的副本」，模型于是整份重写；现在要求从 base_* 草稿出发用 edits 只替换相关行，新作品的计划不变。"""
    from server.remotion_templates.planning import default_plan

    edit = default_plan(edit=True).steps[0].goal
    assert "preset_modify" in edit and "edits" in edit and "only the lines involved" in edit
    assert "write complete code" not in edit
    assert "write complete code with preset_create" in default_plan(edit=False).steps[0].goal


def test_an_edit_changes_a_value_written_into_the_code_by_editing_that_line():
    """编辑计划与 Executor 规则曾说所有数值改动都走参数、不碰代码；可基线常把下划线颜色等写死在代码里（a1cccb1c 的 #FFD400），
    没有参数可改时模型只能违背规则或被拒。现在只有基线已暴露的参数走 sprite_compose，写死的数值改那一行。"""
    from server.remotion_templates.agent import EXECUTOR_RULES
    from server.remotion_templates.planning import default_plan

    plan = default_plan(edit=True).steps[0].goal
    assert "A value the base exposes as a parameter" in plan and "a value written into a draft's code is changed by editing that line" in plan
    assert "A change of a value the base exposes" in EXECUTOR_RULES and "A value written into the code" in EXECUTOR_RULES
    assert "same_code_as" in EXECUTOR_RULES
