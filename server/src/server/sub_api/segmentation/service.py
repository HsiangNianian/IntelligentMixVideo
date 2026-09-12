"""片段构建微服务：正确文案 + ASR 词级时间轴 → Segment[]。

模型负责语义判断（哪里可以切、哪些词重要）；
文字匹配、时间继承、时长约束与关键词校验全部由确定性代码完成。
"""

import logging

from server.core.errors import (
    AsrTimelineMissingError,
    RequestInvalidError,
    ScriptAlignmentError,
    SegmentInvariantError,
)
from server.sub_api.segmentation.aligner import (
    DEFAULT_MAX_ALIGNMENT_WORK,
    align,
    build_asr_chars,
    build_script_chars,
    project_times,
    script_unmatched_lower_bound,
    summarize,
)
from server.sub_api.segmentation.builder import build_spans, offsets_to_cuts, split_clauses
from server.sub_api.segmentation.keywords import validate_keywords
from server.sub_api.segmentation.planner import SegmentPlanner
from server.sub_api.segmentation.schemas import (
    AsrResult,
    Segment,
    SegmentBuildResult,
    SegmentTrace,
    SegmentWarning,
)

logger = logging.getLogger(__name__)

MIN_ALIGNMENT_MATCH_RATIO = 0.5


class SegmentService:
    """按语义边界与时长约束生成可召回的片段。"""

    def __init__(
        self,
        planner: SegmentPlanner,
        *,
        min_duration_ms: int = 1200,
        max_duration_ms: int = 6000,
        max_keywords: int = 5,
        keyword_max_length: int = 12,
        max_alignment_work: int = DEFAULT_MAX_ALIGNMENT_WORK,
        max_asr_chars: int = 20_000,
        max_asr_words: int = 20_000,
    ) -> None:
        """初始化片段构建服务。

        作用与效果：绑定语义规划器与全部工程约束参数，包含对齐规模与 ASR 转写文本上限。
        输入：规划器、片段时长上下限、关键词数量与长度上限、对齐规模上限、ASR 字符与词数上限。
        输出：无。
        """
        self.planner = planner
        self.min_duration_ms = min_duration_ms
        self.max_duration_ms = max_duration_ms
        self.max_keywords = max_keywords
        self.keyword_max_length = keyword_max_length
        self.max_alignment_work = max_alignment_work
        self.max_asr_chars = max_asr_chars
        self.max_asr_words = max_asr_words

    def build(self, *, script: str, asr_result: AsrResult) -> SegmentBuildResult:
        """把文案与 ASR 时间轴收敛为最终片段。

        作用与效果：依次执行对齐、时间投射、模型语义切点、时长约束收敛与关键词校验，
        最后做一次不变量自检。
        输入：原始口播文案与 ASR 转写结果。
        输出：`Segment[]` 及告警、诊断信息。
        """
        if not script.strip():
            raise RequestInvalidError("口播文案不能为空。")
        script_chars = build_script_chars(script)
        if len(script_chars) < 2:
            raise RequestInvalidError("口播文案有效内容过短，无法切分。")
        asr_chars = build_asr_chars(
            asr_result,
            max_chars=self.max_asr_chars,
            max_words=self.max_asr_words,
        )
        if not asr_chars:
            raise AsrTimelineMissingError()
        self._guard_alignment_similarity(script_chars, asr_chars)
        ops = align(script_chars, asr_chars, max_work=self.max_alignment_work)
        repair_block_ranges = project_times(script_chars, asr_chars, ops)
        stats = summarize(ops)
        match_ratio = stats.matched_chars / len(script_chars)
        if match_ratio < MIN_ALIGNMENT_MATCH_RATIO:
            raise ScriptAlignmentError("文案与 ASR 文本差异过大，请确认两者是否为同一段音频。")

        warnings: list[SegmentWarning] = []
        if match_ratio < 0.9:
            warnings.append(
                SegmentWarning(
                    code="low_alignment_match_ratio",
                    message="文案与 ASR 文本存在较多差异，已按文案文本继承 ASR 时间。",
                    detail={"match_ratio": round(match_ratio, 4)},
                )
            )

        clauses = split_clauses(script)
        cuts = self._resolve_cuts(script, script_chars, clauses, repair_block_ranges)
        outcome = build_spans(
            script_chars,
            len(script),
            cuts,
            min_duration_ms=self.min_duration_ms,
            max_duration_ms=self.max_duration_ms,
            repair_block_ranges=repair_block_ranges,
        )
        if not outcome.spans:
            raise ScriptAlignmentError("未能生成任何片段。")

        texts = [script[span.start_offset : span.end_offset] for span in outcome.spans]
        groups = self.planner.plan_keywords(texts)

        segments: list[Segment] = []
        rejected_total = 0
        for index, (span, text, candidates) in enumerate(
            zip(outcome.spans, texts, groups, strict=True), 1
        ):
            keywords, rejected = validate_keywords(
                text,
                candidates,
                max_count=self.max_keywords,
                max_length=self.keyword_max_length,
            )
            rejected_total += rejected
            segments.append(
                Segment(
                    segment_id=f"seg_{index:03d}",
                    text=text,
                    start_time_ms=span.start_time_ms,
                    end_time_ms=span.end_time_ms,
                    keywords=keywords,
                )
            )

        gap_count = _absorb_gaps(segments, max_duration_ms=self.max_duration_ms)
        if gap_count:
            warnings.append(
                SegmentWarning(
                    code="segment_gap_preserved",
                    message="存在较长停顿，已将停顿并入前一片段或保留间隔。",
                    detail={"gap_count": gap_count},
                )
            )
        for segment in segments:
            duration = segment.end_time_ms - segment.start_time_ms
            if not self.min_duration_ms <= duration <= self.max_duration_ms:
                warnings.append(
                    SegmentWarning(
                        code="segment_duration_out_of_range",
                        message="片段时长超出配置范围，已尽力合并或切分。",
                        detail={"segment_id": segment.segment_id, "duration_ms": duration},
                    )
                )

        _assert_invariants(script=script, segments=segments)
        result = SegmentBuildResult(
            segments=segments,
            warnings=warnings,
            trace=SegmentTrace(
                matched_chars=stats.matched_chars,
                substitution_chars=stats.substitution_chars,
                script_extra_chars=stats.script_extra_chars,
                asr_extra_chars=stats.asr_extra_chars,
                edit_cost=stats.edit_cost,
                repair_block_count=len(repair_block_ranges),
                merge_count=outcome.merge_count,
                split_count=outcome.split_count,
                segment_count=len(segments),
                keyword_rejected_count=rejected_total,
            ),
        )
        logger.info(
            "segment_build_completed",
            extra={
                "segment_count": len(segments),
                "edit_cost": stats.edit_cost,
                "repair_block_count": len(repair_block_ranges),
                "match_ratio": round(match_ratio, 4),
                "merge_count": outcome.merge_count,
                "split_count": outcome.split_count,
                "keyword_rejected_count": rejected_total,
                "warning_codes": [warning.code for warning in warnings],
            },
        )
        return result

    def _guard_alignment_similarity(
        self,
        script_chars: list,
        asr_chars: list,
    ) -> None:
        """在对齐前拦下差异过大的输入。

        作用与效果：用 O(n+m) 的未命中字数下界拒掉差异过大的输入；计算预算由对齐核心执行。
        输入：文案字符列表与 ASR 字符列表。
        输出：无；超出上限时抛出 `AppError` 子类。
        """
        budget = int(len(script_chars) * (1 - MIN_ALIGNMENT_MATCH_RATIO))
        if script_unmatched_lower_bound(script_chars, asr_chars) > budget:
            raise ScriptAlignmentError("文案与 ASR 文本差异过大，请确认两者是否为同一段音频。")

    def _resolve_cuts(
        self,
        script: str,
        script_chars: list,
        clauses: list,
        repair_block_ranges: list[tuple[int, int]],
    ) -> list[int]:
        """把模型选中的分句编号换算成字符切点。

        作用与效果：切点落在修复块内部时吸附到最近的块边缘，保证边界时间为精确值。
        输入：文案、文案字符、分句列表与修复块区间。
        输出：升序字符切点列表。
        """
        clause_ids = self.planner.plan_boundaries(clauses)
        offsets = [clauses[item - 1].end_offset for item in clause_ids]
        return _shift_out_of_blocks(
            offsets_to_cuts(offsets, script_chars, len(script)), repair_block_ranges, script_chars
        )


def _shift_out_of_blocks(
    cuts: list[int],
    blocks: list[tuple[int, int]],
    script_chars: list,
) -> list[int]:
    """把落在修复块内部的切点移到最近的块边缘。

    作用与效果：模型只按语义选切点，修复块边缘才是精确时间。
    输入：切点列表、修复块区间与文案字符列表。
    输出：调整后的升序切点列表。
    """
    shifted: list[int] = []
    for cut in cuts:
        block = next((item for item in blocks if item[0] < cut < item[1]), None)
        if block is None:
            shifted.append(cut)
            continue
        left, right = block
        candidate = left if cut - left <= right - cut else right
        if 0 < candidate < len(script_chars):
            shifted.append(candidate)
    return sorted(set(shifted))


def _absorb_gaps(segments: list[Segment], *, max_duration_ms: int) -> int:
    """把片段之间的停顿并入前一片段。

    作用与效果：让输出首尾相接，边界停在"下一句开口"的时刻；并入后超过时长上限时保留间隔。
    输入：已构建片段与片段时长上限。
    输出：被保留（未合并）的间隔数量。
    """
    preserved = 0
    for previous, current in zip(segments, segments[1:], strict=False):
        if current.start_time_ms <= previous.end_time_ms:
            continue
        if current.start_time_ms - previous.start_time_ms <= max_duration_ms:
            previous.end_time_ms = current.start_time_ms
        else:
            preserved += 1
    return preserved


def _assert_invariants(*, script: str, segments: list[Segment]) -> None:
    """校验片段结果的硬性不变量。

    作用与效果：确保文本连续、时间单调合法、关键词可精确回溯，失败即抛错而非静默放行。
    输入：原始文案与已构建片段。
    输出：无；违反不变量时抛出 `SegmentInvariantError`。
    """
    if "".join(segment.text for segment in segments) != script:
        raise SegmentInvariantError("片段文本未完整覆盖原始文案。")
    previous_end = None
    for segment in segments:
        if segment.start_time_ms >= segment.end_time_ms:
            raise SegmentInvariantError("片段开始时间必须早于结束时间。")
        if previous_end is not None and segment.start_time_ms < previous_end:
            raise SegmentInvariantError("片段时间区间出现重叠或倒退。")
        previous_end = segment.end_time_ms
        for keyword in segment.keywords:
            if segment.text[keyword.start : keyword.end] != keyword.text:
                raise SegmentInvariantError("关键词区间无法在片段文本中精确回溯。")
