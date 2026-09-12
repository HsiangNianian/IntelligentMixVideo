"""把正确文案对齐到 ASR 词级时间轴。

对齐只在可发音字符上进行；时间继承、插值与整理全部由本模块确定性完成，
模型不参与任何时间数字的生成。
"""

from collections import Counter
from dataclasses import dataclass

from ...core.errors import AlignmentInputTooLargeError, AsrTranscriptTooLongError
from .normalizer import is_alignable, normalize_char
from .schemas import AsrResult

DEFAULT_MAX_ALIGNMENT_WORK = 250_000

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
    is_word_start: bool = False


@dataclass
class AsrChar:
    """展开到字符粒度的 ASR 单元，继承所属词的时间区间。"""

    normalized: str
    begin_time_ms: float
    end_time_ms: float
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


def build_asr_chars(
    asr_result: AsrResult,
    *,
    max_chars: int | None = None,
    max_words: int | None = None,
) -> list[AsrChar]:
    """把 ASR 词级结果展开为字符级时间轴。

    作用与效果：过滤可发音字符后，将所属词的时间区间按字符数均分，避免同词多字共用区间；
    展开过程中按上限提前中断，防止超大转写文本在计入对齐规模前就把内存撑满。
    输入：`asr_result` 为带词级时间戳的转写结果，`max_chars` 与 `max_words` 为可选上限。
    输出：按时间顺序排列的字符列表；缺少词级时间戳时返回空列表。
    """
    chars: list[AsrChar] = []
    scanned_words = 0
    for sentence in asr_result.sentences:
        for word in sentence.words:
            text = word.text
            if not text.strip():
                continue
            scanned_words += 1
            if max_words is not None and scanned_words > max_words:
                raise AsrTranscriptTooLongError()
            if max_chars is not None and len(text) > max_chars:
                raise AsrTranscriptTooLongError()
            content = [char for char in text if is_alignable(char)]
            if not content:
                continue
            if max_chars is not None and len(chars) + len(content) > max_chars:
                raise AsrTranscriptTooLongError()
            span = word.end_time_ms - word.begin_time_ms
            for order, char in enumerate(content):
                begin = word.begin_time_ms + span * order / len(content)
                end = word.begin_time_ms + span * (order + 1) / len(content)
                chars.append(
                    AsrChar(
                        normalized=normalize_char(char),
                        begin_time_ms=begin,
                        end_time_ms=end,
                        is_word_start=order == 0,
                    )
                )
    return chars


def script_unmatched_lower_bound(
    script_chars: list[AlignedChar],
    asr_chars: list[AsrChar],
) -> int:
    """给出文案未命中字数的 O(n+m) 下界。

    作用与效果：每个命中最多消化一个字符在两侧的较小计数，因此未命中字数至少是各字符
    正差额之和；调用方可用它在不建表的前提下拒掉差异过大的输入。
    输入：文案字符列表与 ASR 字符列表。
    输出：非负整数下界，恒不大于 `substitution_chars + script_extra_chars`。
    """
    script_counts = Counter(char.normalized for char in script_chars)
    asr_counts = Counter(char.normalized for char in asr_chars)
    return (script_counts - asr_counts).total()


@dataclass
class _AlignmentBudget:
    """累计对齐工作消耗，预算不足时立即中断搜索。"""

    remaining: int

    def spend(self, amount: int = 1) -> None:
        """每个候选状态、字符比较或单侧字符消耗一个工作单位。"""
        if amount > self.remaining:
            raise AlignmentInputTooLargeError()
        self.remaining -= amount


def align(
    script_chars: list[AlignedChar],
    asr_chars: list[AsrChar],
    *,
    max_work: int = DEFAULT_MAX_ALIGNMENT_WORK,
) -> list[AlignmentOp]:
    """裁剪公共前后缀后，用单位代价波前对齐；预算覆盖裁剪和核心搜索。

    替换、文案多字、ASR 多字均计一次编辑。最优代价不变，但重复文本的
    等价路径可能与完整 DP 不同。输出仍为两侧下标单调的 AlignmentOp。
    """
    if max_work <= 0:
        raise ValueError("max_work must be positive")
    budget = _AlignmentBudget(max_work)
    prefix = suffix = 0
    limit = min(len(script_chars), len(asr_chars))
    while prefix < limit:
        budget.spend()
        if script_chars[prefix].normalized != asr_chars[prefix].normalized:
            break
        prefix += 1
    while suffix < limit - prefix:
        budget.spend()
        if script_chars[-1 - suffix].normalized != asr_chars[-1 - suffix].normalized:
            break
        suffix += 1
    rows = len(script_chars) - prefix - suffix
    columns = len(asr_chars) - prefix - suffix
    middle = _align_core(
        script_chars[prefix : prefix + rows],
        asr_chars[prefix : prefix + columns],
        offset=prefix,
        budget=budget,
    )
    return (
        [AlignmentOp(MATCH, i, i) for i in range(prefix)]
        + middle
        + [
            AlignmentOp(
                MATCH, len(script_chars) - suffix + i, len(asr_chars) - suffix + i
            )
            for i in range(suffix)
        ]
    )


def _align_core(
    script_chars: list[AlignedChar],
    asr_chars: list[AsrChar],
    *,
    offset: int,
    budget: _AlignmentBudget,
) -> list[AlignmentOp]:
    """按编辑代价逐层扩展，每条对角线 k=i-j 只保留最远文案位置。

    状态记录 (连续匹配终点, 连续匹配起点, 前驱操作)，供确定性回溯。
    到达位置相同时优先替换，其次文案多字、ASR 多字。
    """
    rows, columns = len(script_chars), len(asr_chars)
    if not rows or not columns:
        budget.spend(rows + columns)
        return [AlignmentOp(SCRIPT_EXTRA, offset + i, None) for i in range(rows)] + [
            AlignmentOp(ASR_EXTRA, None, offset + j) for j in range(columns)
        ]

    # ponytail: 保留 O(D²) 回溯状态并受工作预算约束；大差异成为常态时改线性空间回溯。
    history: list[dict[int, tuple[int, int, str]]] = []
    previous: dict[int, tuple[int, int, str]] = {}
    for distance in range(max(rows, columns) + 1):
        current: dict[int, tuple[int, int, str]] = {}
        for diagonal in range(max(-distance, -columns), min(distance, rows) + 1):
            budget.spend()
            start, kind = (0, MATCH) if distance == 0 else (-1, MATCH)
            # 固定优先级；只用严格更远的候选替换已有候选。
            for operation, prior_diagonal, step in (
                (SUBSTITUTION, diagonal, 1),
                (SCRIPT_EXTRA, diagonal - 1, 1),
                (ASR_EXTRA, diagonal + 1, 0),
            ):
                prior = previous.get(prior_diagonal)
                if prior is None:
                    continue
                candidate = prior[0] + step
                j = candidate - diagonal
                if candidate <= rows and 0 <= j <= columns and candidate > start:
                    start, kind = candidate, operation
            if start < 0:
                continue
            i, j = start, start - diagonal
            while i < rows and j < columns:
                budget.spend()
                if script_chars[i].normalized != asr_chars[j].normalized:
                    break
                i, j = i + 1, j + 1
            current[diagonal] = (i, start, kind)
            if i == rows and j == columns:
                history.append(current)
                return _backtrack(history, diagonal, offset)
        history.append(current)
        previous = current
    raise AssertionError("wavefront did not reach the endpoint")


def _backtrack(
    history: list[dict[int, tuple[int, int, str]]], diagonal: int, offset: int
) -> list[AlignmentOp]:
    """沿已保存的前驱回溯，恢复编辑操作和连续匹配。"""
    ops: list[AlignmentOp] = []
    for layer in reversed(history):
        end, start, kind = layer[diagonal]
        for i in range(end - 1, start - 1, -1):
            ops.append(AlignmentOp(MATCH, offset + i, offset + i - diagonal))
        if kind == SUBSTITUTION:
            ops.append(
                AlignmentOp(kind, offset + start - 1, offset + start - diagonal - 1)
            )
        elif kind == SCRIPT_EXTRA:
            ops.append(AlignmentOp(kind, offset + start - 1, None))
            diagonal -= 1
        elif kind == ASR_EXTRA:
            ops.append(AlignmentOp(kind, None, offset + start - diagonal - 1))
            diagonal += 1
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
        target.is_word_start = source.is_word_start

    average = (
        sum(char.end_time_ms - char.begin_time_ms for char in asr_chars)
        / len(asr_chars)
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
            previous = (
                script_chars[script_indices[0] - 1] if script_indices[0] > 0 else None
            )
            window_begin = previous.end_time_ms if previous is not None else 0.0
            window_end = window_begin + average * len(script_indices)
        step = (window_end - window_begin) / len(script_indices)
        for order, index in enumerate(script_indices):
            target = script_chars[index]
            target.begin_time_ms = window_begin + step * order
            target.end_time_ms = window_begin + step * (order + 1)
        char_blocks.append((script_indices[0], script_indices[-1] + 1))
    return char_blocks
