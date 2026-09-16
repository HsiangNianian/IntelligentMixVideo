"""评审依据与作用域回归；pytest tests/test_remotion_review_grounding.py，完全离线。"""

import pytest
from server.remotion_templates.models import VisualCheck, VisualReview
from server.remotion_templates.review import review_errors


def assessment(**changes):
    """构造一个局部样式否决，其余维度通过；只替换当前场景所需字段。"""
    failure = {
        "name": "style",
        "status": "fail",
        "detail": "要求的底条没有出现。",
        "frame": 10,
        "requirement_source": "/instruction",
        "requirement_quote": "标题加底条",
        "target": "title",
        "observed": "第10帧标题没有底条",
        "mismatch": "缺少明确要求的装饰",
    } | changes
    return VisualReview(
        checks=[
            VisualCheck.model_validate(failure)
            if name == "style"
            else VisualCheck(name=name, status="pass", detail="符合要求")
            for name in ("text", "layout", "style", "motion", "scope")
        ]
    )


def intent():
    """用户指令与成功版本属性分别保留来源，不提供模型估计作为验收要求。"""
    return {
        "instruction": "标题加底条",
        "accepted_base": {
            "text_layers": [
                {"id": "title", "text": "标题", "style": {"color": "#FFFFFF"}}
            ]
        },
    }


def test_scoped_failure_accepts_existing_requirement():
    """明确引用用户要求和已知文字层的否决可直接交给 Actor。"""
    assert (
        review_errors(assessment(), [0, 10], 20, intent=intent(), targets={"title"})
        == []
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"requirement_source": "/candidate_plan/assumptions/0"},
        {"requirement_source": "/instruction", "requirement_quote": "整个画布黄色"},
        {
            "requirement_source": "/accepted_base/text_layers/0/style/color",
            "requirement_quote": "#FFFFFF",
            "target": "canvas",
        },
        {"requirement_source": "/accepted_base/text_layers/9/text"},
        {"target": "invented-layer"},
        {"observed": " "},
        {"mismatch": None},
        {"frame": 11},
    ],
)
def test_ungrounded_failure_requires_judge_correction(changes):
    """模型估计、伪造引用、扩大结构化对象范围及未提供的帧均不能触发代码修复。"""
    errors = review_errors(
        assessment(**changes), [0, 10], 20, intent=intent(), targets={"title"}
    )
    assert errors and all(error.startswith("style:") for error in errors)


def test_reference_requires_existing_image_and_observation():
    """图片依据只能指向已提供的参考，不能引用检查底色或候选图片作为用户要求。"""
    review = assessment(
        requirement_source="/reference_images/0", requirement_quote="参考标题有底条"
    )
    assert (
        review_errors(
            review, [10], 20, intent=intent(), targets={"title"}, reference_count=1
        )
        == []
    )
    assert review_errors(
        review, [10], 20, intent=intent(), targets={"title"}, reference_count=0
    )


def test_unknown_requires_specific_missing_evidence():
    """未知原因必须明确，且不得把同一个已经展示的帧反复请求为新证据。"""
    review = assessment(
        status="unknown", missing_evidence=["需要退出画面"], requested_frames=[15]
    )
    assert review_errors(review, [10], 20, intent=intent(), targets={"title"}) == []
    review.checks[2].requested_frames = [10]
    assert review_errors(review, [10], 20, intent=intent(), targets={"title"})


@pytest.mark.parametrize("status", ["unknown", "conflict"])
@pytest.mark.parametrize("missing", [[], [""], [" \n\t"], ["需要退出帧", " "]])
@pytest.mark.parametrize("requested", [[], [15]])
def test_unexplained_uncertainty_requires_protocol_correction(
    status, missing, requested
):
    """未知或冲突必须列出非空缺失证据；仅提供帧号不能消耗宿主补采样次数。"""
    errors = review_errors(
        assessment(status=status, missing_evidence=missing, requested_frames=requested),
        [10],
        20,
        intent=intent(),
        targets={"title"},
    )
    assert any("missing_evidence" in error for error in errors)


def test_failure_without_basis_remains_parseable_for_dimension_correction():
    """缺依据只使该维度无效，保留同批其他合法失败所需的结构化响应。"""
    review = assessment(requirement_source=None)
    assert review_errors(review, [10], 20, intent=intent(), targets={"title"})


@pytest.mark.parametrize("prefix", ["", "/user_intent"])
def test_latest_parameter_overrides_old_accepted_requirement(prefix):
    """明确参数更新后不能用旧成功值否决新结果，但可以引用本次参数约束。"""
    current = intent() | {"parameters": {"0_style_color": "#000000"}}
    old = assessment(
        requirement_source=prefix + "/accepted_base/text_layers/0/style/color",
        requirement_quote="#FFFFFF",
    )
    assert "superseded" in str(
        review_errors(old, [10], 20, intent=current, targets={"title"})
    )
    latest = assessment(
        requirement_source=prefix + "/parameters/0_style_color", requirement_quote="#000000"
    )
    assert review_errors(latest, [10], 20, intent=current, targets={"title"}) == []


@pytest.mark.parametrize(
    "source,quote,target",
    [
        ("/parameters/0_style_color", "#000000", "canvas"),
        ("/original_request/composition/width", "1080", "title"),
    ],
)
@pytest.mark.parametrize("prefix", ["", "/user_intent"])
def test_structured_requirement_target_cannot_change_scope(source, quote, target, prefix):
    """结构化的画布要求和层参数分别绑定各自对象，不能任意交换作用范围。"""
    current = intent() | {
        "parameters": {"0_style_color": "#000000"},
        "original_request": {"composition": {"width": 1080}},
    }
    assert review_errors(
        assessment(requirement_source=prefix + source, requirement_quote=quote, target=target),
        [10],
        20,
        intent=current,
        targets={"title"},
    )


@pytest.mark.parametrize("prefix", ["", "/user_intent"])
@pytest.mark.parametrize("path,quote,target", [
    ("/instruction", "标题加底条", "title"),
    ("/original_request/description", "淡入淡出", "title"),
    ("/original_request/composition/width", "1080", "canvas"),
    ("/clarifications/0", "保留正文", "title"),
    ("/parameters/0_style_color", "#000000", "title"),
    ("/accepted_base/text_layers/0/text", "标题", "title"),
])
def test_full_and_legacy_requirement_paths_preserve_negative_verdict(prefix, path, quote, target):
    """完整路径与旧相对路径均解析同一事实；保留 fail 与原始路径，不修改审计内容。"""
    current = intent() | {
        "original_request": {"description": "淡入淡出", "composition": {"width": 1080}},
        "clarifications": ["保留正文"],
        "parameters": {"0_style_color": "#000000"},
    }
    review = assessment(requirement_source=prefix + path, requirement_quote=quote, target=target)
    original = review.model_dump()
    assert review_errors(review, [10], 20, intent=current, targets={"title"}) == []
    assert review.model_dump() == original
    assert review.checks[2].status == "fail"


@pytest.mark.parametrize("path,quote,target,reason", [
    ("/user_intent/instruction", "伪造要求", "title", "requirement_quote"),
    ("/user_intent/accepted_base/text_layers/9/text", "标题", "title", "does not exist"),
    ("/user_intent/instruction", "标题加底条", "missing", "target"),
    ("/user_intent/accepted_base/text_layers/0/style/color", "#FFFFFF", "canvas", "another layer"),
    ("/user_intent/original_request/missing", "标题", "title", "original description"),
    ("/user_intent", "标题", "title", "/user_intent/instruction"),
    ("/user_intent/user_intent/instruction", "标题加底条", "title", "/user_intent/instruction"),
    ("/user_intent/candidate_plan/description", "标题", "title", "/user_intent/instruction"),
    ("/candidate_plan/description", "标题", "title", "/user_intent/instruction"),
    ("/user_intent/reference_images/0", "标题", "title", "/reference_images/N"),
])
def test_full_paths_cannot_bypass_grounding_and_errors_identify_source(path, quote, target, reason):
    """只兼容一层明确前缀；伪造事实、扩大作用域和候选要求仍被拒绝并提供具体纠错原因。"""
    errors = review_errors(
        assessment(requirement_source=path, requirement_quote=quote, target=target),
        [10], 20, intent=intent(), targets={"title"}, reference_count=1,
    )
    assert len(errors) == 1 and errors[0].startswith("style:")
    assert path in errors[0] and reason in errors[0]
