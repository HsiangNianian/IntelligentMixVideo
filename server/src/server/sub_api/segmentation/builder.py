"""把语义边界收敛为满足时长约束的片段。

模型只提供"哪里可以切"，本模块负责"工程上能不能切"：时长上下限、词边界吸附、
停顿时优先、数字与英文词不可切断。
"""

import logging
from dataclasses import dataclass

from server.sub_api.segmentation.aligner import AlignedChar

logger = logging.getLogger(__name__)

PAUSE_THRESHOLD_MS = 120
CLAUSE_PUNCTUATION = "，。！？；：、…"
PROTECTED_TRAILING_CHARS = frozenset("%.-")

@dataclass
class SegmentSpan:
    """一个片段的文本区间与时间区间。"""

    start_offset: int
    end_offset: int
    start_char: int
    end_char: int
    start_time_ms: int
    end_time_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_time_ms - self.start_time_ms


@dataclass
class BuildOutcome:
    """约束收敛结果。"""

    spans: list[SegmentSpan]
    merge_count: int
    split_count: int


def _is_protected_pair(previous: str, following: str) -> bool:
    """判断两个字符之间是否禁止切断。

    作用与效果：保护数字、单位与英文词，避免产生 `40` | `%` 这类碎片。
    输入：相邻的两个可发音字符。
    输出：禁止在此切断时为 `True`。
    """
    previous_is_word = previous.isascii() and previous.isalnum()
    following_is_word = following.isascii() and following.isalnum()
    if previous_is_word and following_is_word:
        return True
    return previous_is_word and following in PROTECTED_TRAILING_CHARS


def _has_pause(chars: list[AlignedChar], cut: int) -> bool:
    """判断切点前是否存在可识别的词间停顿。"""
    return chars[cut].begin_time_ms - chars[cut - 1].end_time_ms >= PAUSE_THRESHOLD_MS


def _inside_repair_block(cut: int, blocks: list[tuple[int, int]]) -> bool:
    """判断切点是否落在修复块内部。

    作用与效果：修复块内部的时间是按字均分的估算值，切在块边缘才精确。
    输入：字符序号切点与修复块区间列表。
    输出：落在块内部时为 `True`，恰好等于块边缘时返回 `False`。
    """
    return any(begin < cut < end for begin, end in blocks)


def offsets_to_cuts(
    boundaries: list[int],
    chars: list[AlignedChar],
    text_length: int,
) -> list[int]:
    """把文本下标边界转换为字符序号切点。

    作用与效果：忽略越界与重复边界，并把落在保护区间内的边界吸附到右侧词首。
    输入：候选文本边界、文案字符列表与文案总长度。
    输出：升序去重的字符序号切点列表。
    """
    cuts: set[int] = set()
    for boundary in boundaries:
        if boundary <= 0 or boundary >= text_length:
            continue
        cut = sum(1 for char in chars if char.index < boundary)
        cut = _snap_cut(cut, chars)
        if 0 < cut < len(chars):
            cuts.add(cut)
    return sorted(cuts)


def _snap_cut(cut: int, chars: list[AlignedChar]) -> int:
    """把落在保护区间内的切点吸附到右侧最近的词首。

    作用与效果：避免在数字与单位、英文词内部切断；无法吸附时原样返回。
    输入：字符序号切点与文案字符列表。
    输出：调整后的切点。
    """
    while 0 < cut < len(chars) and _is_protected_pair(chars[cut - 1].char, chars[cut].char):
        cut += 1
    return cut


def _spans_from_cuts(
    cuts: list[int],
    chars: list[AlignedChar],
    text_length: int,
) -> list[SegmentSpan]:
    """按切点把文案切成片段区间。"""
    edges = [0, *cuts, len(chars)]
    spans: list[SegmentSpan] = []
    for start, end in zip(edges, edges[1:], strict=False):
        if end <= start:
            continue
        start_offset = 0 if start == 0 else chars[start].index
        end_offset = text_length if end == len(chars) else chars[end].index
        spans.append(
            SegmentSpan(
                start_offset=start_offset,
                end_offset=end_offset,
                start_char=start,
                end_char=end,
                start_time_ms=round(chars[start].begin_time_ms),
                end_time_ms=round(chars[end - 1].end_time_ms),
            )
        )
    return spans


def _merge_pair(left: SegmentSpan, right: SegmentSpan) -> SegmentSpan:
    """合并两个相邻片段区间。"""
    return SegmentSpan(
        start_offset=left.start_offset,
        end_offset=right.end_offset,
        start_char=left.start_char,
        end_char=right.end_char,
        start_time_ms=left.start_time_ms,
        end_time_ms=right.end_time_ms,
    )


def _merge(
    spans: list[SegmentSpan],
    index: int,
    side: str,
) -> tuple[list[SegmentSpan], SegmentSpan]:
    """把指定片段与左或右邻居合并。

    作用与效果：返回合并后的片段列表与合并结果，供调用方评估时长。
    输入：片段列表、目标片段序号与合并方向。
    输出：新片段列表与合并后的片段。
    """
    if side == "left" and index > 0:
        merged = _merge_pair(spans[index - 1], spans[index])
        return [*spans[: index - 1], merged, *spans[index + 1 :]], merged
    if side == "right" and index < len(spans) - 1:
        merged = _merge_pair(spans[index], spans[index + 1])
        return [*spans[:index], merged, *spans[index + 2 :]], merged
    return spans, spans[index]


def build_spans(
    chars: list[AlignedChar],
    text_length: int,
    cuts: list[int],
    *,
    min_duration_ms: int,
    max_duration_ms: int,
    repair_block_ranges: list[tuple[int, int]] | None = None,
) -> BuildOutcome:
    """在时长约束下收敛片段边界。

    作用与效果：先合并过短片段，再切分过长片段；切分优先落在修复块之外，
    其次优先词边界与停顿，并避开保护区间。
    输入：文案字符、文案长度、候选切点、时长上下限与修复块区间。
    输出：最终片段区间及合并、切分次数。
    """
    blocks = repair_block_ranges or []
    spans = _spans_from_cuts(sorted(set(cuts)), chars, text_length)
    merge_count = 0
    split_count = 0
    ideal = (min_duration_ms + max_duration_ms) / 2

    while len(spans) > 1:
        shortest = min(
            (index for index, span in enumerate(spans) if span.duration_ms < min_duration_ms),
            key=lambda index: spans[index].duration_ms,
            default=None,
        )
        if shortest is None:
            break
        options: list[tuple[tuple[int, float], str]] = []
        for side in ("left", "right"):
            if side == "left" and shortest == 0:
                continue
            if side == "right" and shortest == len(spans) - 1:
                continue
            _, merged = _merge(spans, shortest, side)
            score = (0 if merged.duration_ms <= max_duration_ms else 1,
                     abs(merged.duration_ms - ideal))
            options.append((score, side))
        if not options:
            break
        options.sort()
        spans, _ = _merge(spans, shortest, options[0][1])
        merge_count += 1

    while True:
        longest = max(
            (index for index, span in enumerate(spans) if span.duration_ms > max_duration_ms),
            key=lambda index: spans[index].duration_ms,
            default=None,
        )
        if longest is None:
            break
        span = spans[longest]
        parts = max(2, -(-span.duration_ms // max_duration_ms))
        target = span.duration_ms / parts
        best: tuple[tuple[int, int, int, int, float], int] | None = None
        for cut in range(span.start_char + 1, span.end_char):
            if _is_protected_pair(chars[cut - 1].char, chars[cut].char):
                continue
            left_duration = chars[cut - 1].end_time_ms - chars[span.start_char].begin_time_ms
            right_duration = chars[span.end_char - 1].end_time_ms - chars[cut].begin_time_ms
            violation = 1 if min(left_duration, right_duration) < min_duration_ms else 0
            score = (
                violation,
                1 if _inside_repair_block(cut, blocks) else 0,
                0 if chars[cut].is_word_start else 1,
                0 if _has_pause(chars, cut) else 1,
                abs(left_duration - target) + abs(right_duration - target),
            )
            if best is None or score < best[0]:
                best = (score, cut)
        if best is None:
            logger.warning(
                "segment_split_has_no_valid_cut",
                extra={"start_char": span.start_char, "end_char": span.end_char},
            )
            break
        cut = best[1]
        left = SegmentSpan(
            start_offset=span.start_offset,
            end_offset=chars[cut].index,
            start_char=span.start_char,
            end_char=cut,
            start_time_ms=span.start_time_ms,
            end_time_ms=round(chars[cut - 1].end_time_ms),
        )
        right = SegmentSpan(
            start_offset=chars[cut].index,
            end_offset=span.end_offset,
            start_char=cut,
            end_char=span.end_char,
            start_time_ms=round(chars[cut].begin_time_ms),
            end_time_ms=span.end_time_ms,
        )
        spans = [*spans[:longest], left, right, *spans[longest + 1 :]]
        split_count += 1

    return BuildOutcome(spans=spans, merge_count=merge_count, split_count=split_count)


@dataclass
class Clause:
    """按标点切分出的分句及其在文案中的位置。"""

    number: int
    text: str
    end_offset: int


def split_clauses(script: str) -> list[Clause]:
    """按标点把文案切成编号分句。

    作用与效果：分句是模型选择切点的最小粒度，也是确定性兜底的候选边界。
    输入：`script` 为原始口播文案。
    输出：编号从 1 开始的分句列表。
    """
    clauses: list[Clause] = []
    start = 0
    for index, char in enumerate(script):
        if char in CLAUSE_PUNCTUATION:
            clauses.append(
                Clause(
                    number=len(clauses) + 1,
                    text=script[start : index + 1],
                    end_offset=index + 1,
                )
            )
            start = index + 1
    if start < len(script):
        clauses.append(Clause(number=len(clauses) + 1, text=script[start:], end_offset=len(script)))
    return clauses
