"""把正确文案对齐到 ASR 词级时间轴。

对齐只在可发音字符上进行；时间继承、插值与整理全部由本模块确定性完成，
模型不参与任何时间数字的生成。
"""

import logging
from array import array
from dataclasses import dataclass

from server.sub_api.segmentation.normalizer import is_alignable, normalize_char
from server.sub_api.segmentation.schemas import AsrResult

logger = logging.getLogger(__name__)

MATCH_COST = 0
SUBSTITUTION_COST = 1
GAP_COST = 1

MATCH = "match"
SUBSTITUTION = "substitution"
SCRIPT_EXTRA = "script_extra"
ASR_EXTRA = "asr_extra"


@dataclass
class AlignedChar:
    """文案中的一个可发音字符及其对齐后的时间。"""

    index: int
    char: str
    normalized: str
    begin_time_ms: float = 0.0
    end_time_ms: float = 0.0
    asr_text: str | None = None
    confidence: float | None = None
    is_word_start: bool = False


@dataclass
class AsrChar:
    """展开到字符粒度的 ASR 单元，继承所属词的时间区间。"""

    char: str
    normalized: str
    begin_time_ms: float
    end_time_ms: float
    word_text: str
    confidence: float | None
    is_word_start: bool


@dataclass
class AlignmentOp:
    """一步对齐操作。"""

    kind: str
    script_index: int | None
    asr_index: int | None


@dataclass
class AlignmentStats:
    """对齐统计，用于诊断与回归。"""

    matched_chars: int = 0
    substitution_chars: int = 0
    script_extra_chars: int = 0
    asr_extra_chars: int = 0
    edit_cost: int = 0


def build_script_chars(script: str) -> list[AlignedChar]:
    """抽取文案中的可发音字符。

    作用与效果：过滤空白与标点，记录每个字符在原文中的下标，供切片回指。
    输入：`script` 为原始口播文案。
    输出：按原文顺序排列的字符列表。
    """
    return [
        AlignedChar(index=index, char=char, normalized=normalize_char(char))
        for index, char in enumerate(script)
        if is_alignable(char)
    ]


def build_asr_chars(asr_result: AsrResult) -> list[AsrChar]:
    """把 ASR 词级结果展开为字符级时间轴。

    作用与效果：过滤可发音字符后，将所属词的时间区间按字符数均分，避免同词多字共用区间。
    输入：`asr_result` 为带词级时间戳的转写结果。
    输出：按时间顺序排列的字符列表；缺少词级时间戳时返回空列表。
    """
    chars: list[AsrChar] = []
    for word in asr_result.iter_words():
        content = [char for char in word.text if is_alignable(char)]
        if not content:
            continue
        span = word.end_time_ms - word.begin_time_ms
        for order, char in enumerate(content):
            begin = word.begin_time_ms + span * order / len(content)
            end = word.begin_time_ms + span * (order + 1) / len(content)
            chars.append(
                AsrChar(
                    char=char,
                    normalized=normalize_char(char),
                    begin_time_ms=begin,
                    end_time_ms=end,
                    word_text=word.text.strip(),
                    confidence=word.confidence,
                    is_word_start=order == 0,
                )
            )
    return chars


def align(script_chars: list[AlignedChar], asr_chars: list[AsrChar]) -> list[AlignmentOp]:
    """用编辑距离对齐文案与 ASR 字符序列。

    作用与效果：执行 Needleman-Wunsch 动态规划，回溯时优先对角，保证替换优先于删增组合；
    返回的操作序列单调递增，可直接用于时间投射。
    输入：文案字符列表与 ASR 字符列表。
    输出：对齐操作列表。
    """
    rows = len(script_chars)
    columns = len(asr_chars)
    table = [array("i", bytes(4 * (columns + 1))) for _ in range(rows + 1)]
    for i in range(1, rows + 1):
        table[i][0] = i * GAP_COST
    for j in range(1, columns + 1):
        table[0][j] = j * GAP_COST
    for i in range(1, rows + 1):
        current = table[i]
        previous = table[i - 1]
        script_char = script_chars[i - 1].normalized
        for j in range(1, columns + 1):
            cost = MATCH_COST if script_char == asr_chars[j - 1].normalized else SUBSTITUTION_COST
            current[j] = min(
                previous[j - 1] + cost,
                previous[j] + GAP_COST,
                current[j - 1] + GAP_COST,
            )

    ops: list[AlignmentOp] = []
    i, j = rows, columns
    while i and j:
        cost = (
            MATCH_COST
            if script_chars[i - 1].normalized == asr_chars[j - 1].normalized
            else SUBSTITUTION_COST
        )
        if table[i][j] == table[i - 1][j - 1] + cost:
            kind = MATCH if cost == MATCH_COST else SUBSTITUTION
            ops.append(AlignmentOp(kind, i - 1, j - 1))
            i, j = i - 1, j - 1
        elif table[i][j] == table[i - 1][j] + GAP_COST:
            ops.append(AlignmentOp(SCRIPT_EXTRA, i - 1, None))
            i -= 1
        else:
            ops.append(AlignmentOp(ASR_EXTRA, None, j - 1))
            j -= 1
    while i:
        ops.append(AlignmentOp(SCRIPT_EXTRA, i - 1, None))
        i -= 1
    while j:
        ops.append(AlignmentOp(ASR_EXTRA, None, j - 1))
        j -= 1
    ops.reverse()
    return ops


def summarize(ops: list[AlignmentOp]) -> AlignmentStats:
    """统计对齐结果。

    作用与效果：汇总命中、替换、文案多字与 ASR 多字数量及编辑代价。
    输入：对齐操作列表。
    输出：`AlignmentStats` 统计对象。
    """
    stats = AlignmentStats()
    for op in ops:
        if op.kind == MATCH:
            stats.matched_chars += 1
        elif op.kind == SUBSTITUTION:
            stats.substitution_chars += 1
            stats.edit_cost += SUBSTITUTION_COST
        elif op.kind == SCRIPT_EXTRA:
            stats.script_extra_chars += 1
            stats.edit_cost += GAP_COST
        else:
            stats.asr_extra_chars += 1
            stats.edit_cost += GAP_COST
    return stats


def repair_blocks(ops: list[AlignmentOp]) -> list[tuple[int, int]]:
    """把差异与相邻字合成修复块。

    作用与效果：以对齐操作序号为单位，取每段连续差异并向前后各扩一位，
    再合并重叠区间；块内时间由 `project_times` 按原文字数均分。
    输入：对齐操作列表。
    输出：升序且互不重叠的操作区间列表，区间为半开 `[start, end)`。
    """
    runs: list[tuple[int, int]] = []
    entering: int | None = None
    for position, op in enumerate(ops):
        if op.kind in (SCRIPT_EXTRA, ASR_EXTRA):
            if entering is None:
                entering = position
        elif entering is not None:
            runs.append((entering, position))
            entering = None
    if entering is not None:
        runs.append((entering, len(ops)))

    blocks: list[tuple[int, int]] = []
    for begin, end in runs:
        left = max(0, begin - 1)
        right = min(len(ops), end + 1)
        if blocks and left <= blocks[-1][1]:
            blocks[-1] = (blocks[-1][0], max(right, blocks[-1][1]))
        else:
            blocks.append((left, right))
    return blocks


def project_times(
    script_chars: list[AlignedChar],
    asr_chars: list[AsrChar],
    ops: list[AlignmentOp],
) -> list[tuple[int, int]]:
    """把 ASR 时间投射到文案字符上。

    作用与效果：命中与替换直接继承 ASR 时间；差异与相邻字合并为修复块，
    块内保留"首字开始到末字结束"的总音频范围并按原文字数均分，
    块外字符时间保持不动，因此局部错误不会导致整段时间漂移。
    输入：文案字符、ASR 字符与对齐操作列表。
    输出：修复块的字符序号区间列表，区间为半开 `[start, end)`。
    """
    for op in ops:
        if op.script_index is None or op.asr_index is None:
            continue
        source = asr_chars[op.asr_index]
        target = script_chars[op.script_index]
        target.begin_time_ms = source.begin_time_ms
        target.end_time_ms = source.end_time_ms
        target.asr_text = source.word_text
        target.confidence = source.confidence
        target.is_word_start = source.is_word_start

    average = (
        sum(char.end_time_ms - char.begin_time_ms for char in asr_chars) / len(asr_chars)
        if asr_chars
        else 0.0
    )
    char_blocks: list[tuple[int, int]] = []
    for begin, end in repair_blocks(ops):
        script_indices = [
            ops[position].script_index
            for position in range(begin, end)
            if ops[position].script_index is not None
        ]
        asr_indices = [
            ops[position].asr_index
            for position in range(begin, end)
            if ops[position].asr_index is not None
        ]
        if not script_indices:
            continue
        if asr_indices:
            window_begin = asr_chars[min(asr_indices)].begin_time_ms
            window_end = asr_chars[max(asr_indices)].end_time_ms
        else:
            previous = script_chars[script_indices[0] - 1] if script_indices[0] > 0 else None
            window_begin = previous.end_time_ms if previous is not None else 0.0
            window_end = window_begin + average * len(script_indices)
        step = (window_end - window_begin) / len(script_indices)
        for order, index in enumerate(script_indices):
            target = script_chars[index]
            target.begin_time_ms = window_begin + step * order
            target.end_time_ms = window_begin + step * (order + 1)
        char_blocks.append((script_indices[0], script_indices[-1] + 1))
    return char_blocks
