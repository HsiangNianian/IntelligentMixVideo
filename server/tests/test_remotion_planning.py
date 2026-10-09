"""Plan 引用契约：可接受的形式、向前引用与畸形引用的拒绝，以及模型可见 Schema 的说明。

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
    ],
)
def test_accepted_references_validate(refs):
    """文档写明的三种引用形式都必须被接受，包括引用前序步骤保存的 Sprite。"""
    plan = Plan(
        goal="组合", steps=[step("first", ["user_intent"]), step("second", refs)]
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
        "Inputs must reference user_intent, accepted_base or steps.<earlier_id>.outputs.sprite"
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


def test_reference_requires_a_saved_output_before_dispatch():
    """合法语法不等于已取得产物：前一步没有 Sprite 时不能唤醒依赖它的下一步。"""
    from server.remotion_templates.planning import ExecutionState

    state = ExecutionState(4, 3)
    plan = Plan(goal="组合", steps=[step("first", ["user_intent"]), step("second", ["steps.first.outputs.sprite"])])
    state.control(PlanAction(action="update_plan", plan=plan))
    state.finish_batch("step_done", sprite_id=None)
    before = state.snapshot()
    with pytest.raises(ValueError, match="without a saved Sprite"):
        state.control(PlanAction(action="advance"))
    assert state.snapshot() == before
    state.finish_batch("step_done", sprite_id="saved-sprite")
    state.control(PlanAction(action="advance"))
    assert state.snapshot()["step_inputs"] == {"steps.first.outputs.sprite": "saved-sprite"}
