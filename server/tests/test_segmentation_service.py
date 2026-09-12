"""片段构建服务测试：编排、约束与输入规模闸门。

在 server/ 执行 uv run --locked pytest -v；使用离线样本与替身。
"""

import pytest

from server.core.errors import (
    AlignmentInputTooLargeError,
    AsrTranscriptTooLongError,
    ScriptAlignmentError,
)
from server.sub_api.segmentation.schemas import AsrResult, AsrSentence, AsrWord
from server.sub_api.segmentation.service import SegmentService
from .support import StubPlanner, load_asr_result


def test_builds_segments_and_keywords_from_planner(asr_result) -> None:
    """服务输出完整片段和可定位关键词，无错误拒绝计数。"""
    service = SegmentService(StubPlanner())

    body = service.build(script=asr_result.text or "", asr_result=asr_result)

    assert body.segments
    assert "".join((segment.text for segment in body.segments)) == asr_result.text
    assert body.trace.keyword_rejected_count == 0
    assert any((segment.keywords for segment in body.segments))
    for segment in body.segments:
        for keyword in segment.keywords:
            assert segment.text[keyword.start : keyword.end] == keyword.text


def test_passes_sliced_texts_to_keyword_planner(asr_result) -> None:
    """关键词规划器收到实际切片文本，顺序与输出一致。"""
    planner = StubPlanner()
    service = SegmentService(planner)

    body = service.build(script=asr_result.text or "", asr_result=asr_result)

    assert planner.keyword_texts == [segment.text for segment in body.segments]


def test_model_cut_inside_repair_block_is_snapped_to_edge(asr_result) -> None:
    """模型切点落入修复块时移到边缘，不截断插入文本。"""
    script = (asr_result.text or "").replace("农家土鸡蛋", "农家散养土鸡蛋")
    # 分句 12 的结尾落在「散养」修复块内部（块内时间为估算值）
    planner = StubPlanner(clause_ids=[12])
    service = SegmentService(planner, max_duration_ms=30000)

    body = service.build(script=script, asr_result=asr_result)

    assert body.trace.repair_block_count == 1
    assert len(body.segments) == 2
    assert not body.segments[0].text.endswith("农家散")


def test_enforces_duration_limits_when_model_cuts_are_coarse(asr_result) -> None:
    """模型切点较粗时继续拆分，输出满足样本时长限制。"""
    service = SegmentService(StubPlanner(clause_ids=[2]))

    body = service.build(script=asr_result.text or "", asr_result=asr_result)

    assert body.trace.split_count >= 1
    for segment in body.segments:
        duration = segment.end_time_ms - segment.start_time_ms
        assert duration >= 1200
        assert duration <= 6000


def test_one_to_one_substitutions_do_not_form_repair_blocks(asr_result) -> None:
    """一对一错字不产生修复块，统计代价并保留首尾时间。"""
    script = (asr_result.text or "").replace("那些", "这些").replace("鸡蛋", "鸭蛋")
    service = SegmentService(StubPlanner())

    body = service.build(script=script, asr_result=asr_result)

    # 一对一错字直接替换并继承 ASR 时间，不产生修复块，也不漂移
    assert body.trace.substitution_chars == 6
    assert body.trace.repair_block_count == 0
    assert body.trace.edit_cost == 6
    assert body.segments[0].start_time_ms == 160
    assert body.segments[-1].end_time_ms == 36700


def test_rejects_asr_without_word_timeline() -> None:
    """缺少词级时间戳时拒绝请求，不能推测整段时间轴。"""
    from server.core.errors import AsrTimelineMissingError

    payload = load_asr_result().model_dump()
    for sentence in payload["sentences"]:
        sentence.pop("words", None)
    from server.sub_api.segmentation.schemas import AsrResult

    with pytest.raises(AsrTimelineMissingError):
        SegmentService(StubPlanner()).build(
            script="随便一段文案内容",
            asr_result=AsrResult.model_validate(payload),
        )


def test_rejects_unrelated_script(asr_result) -> None:
    """无关文案被拒绝，不能生成不可靠片段。"""
    from server.core.errors import ScriptAlignmentError

    with pytest.raises(ScriptAlignmentError):
        SegmentService(StubPlanner()).build(
            script="今天讲解 Python 异步编程与协程调度。",
            asr_result=asr_result,
        )


def asr_from(text: str) -> AsrResult:
    """构造每字十毫秒的 ASR 时间轴，供规模约束用例使用。"""
    return AsrResult(
        sentences=[
            AsrSentence(
                words=[
                    AsrWord(
                        text=char,
                        begin_time_ms=index * 10,
                        end_time_ms=index * 10 + 10,
                    )
                    for index, char in enumerate(text)
                ]
            )
        ]
    )


def test_rejects_alignment_above_scale_cap(empty_planner) -> None:
    # 相同字符、相反顺序：未命中下界为 0，但搜索工作量超过上限
    """相反顺序输入即使字符计数一致，也必须受搜索预算限制。"""
    service = SegmentService(empty_planner, max_alignment_work=1_000)
    script = "甲" * 50 + "乙" * 50

    with pytest.raises(AlignmentInputTooLargeError):
        service.build(script=script, asr_result=asr_from("乙" * 50 + "甲" * 50))


def test_work_budget_reaches_aligner_and_accepts_long_similar_text(
    empty_planner,
) -> None:
    """长相似文案按实际工作放行，小预算正确传入核心并终止搜索。"""
    script = "甲乙丙丁" * 750
    changed = list(script)
    changed[10] = changed[-11] = "错"
    payload = asr_from("".join(changed))
    accepted = SegmentService(empty_planner, max_alignment_work=12_000).build(
        script=script, asr_result=payload
    )
    assert "".join((segment.text for segment in accepted.segments)) == script
    assert accepted.trace.substitution_chars == 2
    with pytest.raises(AlignmentInputTooLargeError):
        SegmentService(empty_planner, max_alignment_work=100).build(
            script=script, asr_result=payload
        )


def test_rejects_oversized_asr_transcript(asr_result, empty_planner) -> None:
    """ASR 长度超限在展开阶段拒绝，不继续构建片段。"""
    service = SegmentService(empty_planner, max_asr_chars=3)

    with pytest.raises(AsrTranscriptTooLongError):
        service.build(script=asr_result.text or "", asr_result=asr_result)


def test_rejects_dissimilar_script_before_search(asr_result, empty_planner) -> None:
    # 与 ASR 完全无共同字符：命中下界 300 超过 50% 预算，不应进入波前搜索
    """明显无关文本由未命中下界提前拒绝。"""
    script = "甲乙丙丁戊己庚辛壬癸" * 30
    service = SegmentService(empty_planner)

    with pytest.raises(ScriptAlignmentError):
        service.build(script=script, asr_result=asr_result)
