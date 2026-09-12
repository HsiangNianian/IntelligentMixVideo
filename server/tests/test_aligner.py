"""对齐与时间投射测试：文案定文本，ASR 定时间。

在 server/ 执行 uv run --locked pytest -v；使用离线样本与替身。
"""

import itertools
import random
import pytest

from server.core.errors import AlignmentInputTooLargeError, AsrTranscriptTooLongError
from server.sub_api.segmentation.aligner import (
    ASR_EXTRA,
    MATCH,
    SCRIPT_EXTRA,
    SUBSTITUTION,
    AlignedChar,
    AsrChar,
    align,
    build_asr_chars,
    build_script_chars,
    project_times,
    repair_blocks,
    script_unmatched_lower_bound,
    summarize,
)
from server.sub_api.segmentation.schemas import AsrResult, AsrSentence, AsrWord
from .support import load_asr_result


def synthetic_pair(
    script_text: str, asr_text: str
) -> tuple[list[AlignedChar], list[AsrChar]]:
    """构造只关心字符序列的对照输入，时间字段不参与对齐。"""
    script_chars = [
        AlignedChar(index=index, char=char, normalized=char)
        for index, char in enumerate(script_text)
    ]
    asr_chars = [
        AsrChar(
            normalized=char,
            begin_time_ms=float(index),
            end_time_ms=float(index + 1),
            is_word_start=False,
        )
        for index, char in enumerate(asr_text)
    ]
    return script_chars, asr_chars


def reference_cost(script: str, asr: str) -> int:
    """完整 Levenshtein DP，独立验证最小编辑代价。"""
    table = [[0] * (len(asr) + 1) for _ in range(len(script) + 1)]
    for i in range(len(script) + 1):
        table[i][0] = i
    for j in range(len(asr) + 1):
        table[0][j] = j
    for i, left in enumerate(script, 1):
        for j, right in enumerate(asr, 1):
            table[i][j] = min(
                table[i - 1][j - 1] + (left != right),
                table[i - 1][j] + 1,
                table[i][j - 1] + 1,
            )
    return table[-1][-1]


def run_alignment(script: str):
    """对齐固定 ASR 样本并投射时间，返回文案字符与操作序列。"""
    asr_result = load_asr_result()
    script_chars = build_script_chars(script)
    asr_chars = build_asr_chars(asr_result)
    ops = align(script_chars, asr_chars)
    project_times(script_chars, asr_chars, ops)
    return script_chars, ops


def test_unwraps_fun_asr_word_level_timeline() -> None:
    """原始 ASR 响应正确解包，保留词级时间及音频时长。"""
    result = load_asr_result()

    assert result.audio_duration_ms == 36811
    assert len(result.sentences) == 6
    assert len(result.iter_words()) == 138
    first = result.iter_words()[0]
    assert (first.text, first.begin_time_ms, first.end_time_ms) == ("刚才", 160, 360)


def test_identical_script_inherits_word_timeline() -> None:
    """相同文案全部命中，首尾时间继承 ASR。"""
    script = load_asr_result().text or ""
    script_chars, ops = run_alignment(script)
    stats = summarize(ops)

    assert stats.matched_chars == len(script_chars)
    assert stats.edit_cost == 0
    assert script_chars[0].begin_time_ms == 160
    assert script_chars[-1].end_time_ms == 36700


def test_first_sentence_boundaries_use_asr_time() -> None:
    """首句边界使用 ASR 时间，保留句间停顿。"""
    script = load_asr_result().text or ""
    script_chars, ops = run_alignment(script)

    assert all((op.kind == MATCH for op in ops))
    assert script_chars[16].char == "吗"
    assert (script_chars[16].begin_time_ms, script_chars[16].end_time_ms) == (
        2280,
        2400,
    )
    assert script_chars[17].begin_time_ms == 2520


def test_substitution_borrows_matched_asr_time() -> None:
    """同音错字按替换处理，继承对应 ASR 字符时间。"""
    script = (load_asr_result().text or "").replace("囤一点", "屯一点")
    script_chars, ops = run_alignment(script)
    substitutions = [op for op in ops if op.kind == SUBSTITUTION]

    assert len(substitutions) == 1
    target = script_chars[substitutions[0].script_index]
    assert target.char == "屯"
    assert (target.begin_time_ms, target.end_time_ms) == (14480, 14600)


def test_asr_extra_words_are_absorbed_into_repair_block() -> None:
    """ASR 多字并入局部修复块，块外时间保持不变。"""
    script = (load_asr_result().text or "").replace("不吃饲料那些的", "不吃饲料")
    script_chars, ops = run_alignment(script)
    extras = [op for op in ops if op.kind == ASR_EXTRA]

    assert len(extras) == 3
    position = next(
        index for index, char in enumerate(script_chars) if char.char == "料"
    )
    assert script_chars[position + 1].char == "吃"
    assert script_chars[position].begin_time_ms == 9420  # 块左边界不动
    assert script_chars[position + 1].end_time_ms == 10320  # 块右边界不动
    assert (
        script_chars[position].end_time_ms == script_chars[position + 1].begin_time_ms
    )
    assert script_chars[position + 2].begin_time_ms == 10320  # 块之后不动
    assert "那些的" not in "".join((char.char for char in script_chars))


def test_repair_blocks_cover_differences_with_neighbour_chars() -> None:
    """增删及相邻字符构成两个预期修复块。"""
    base = load_asr_result().text or ""
    script = base.replace("不吃饲料那些的", "不吃饲料").replace(
        "农家土鸡蛋", "农家散养土鸡蛋"
    )
    script_chars, ops = run_alignment(script)
    blocks = repair_blocks(ops)

    assert len(blocks) == 2
    covered = "".join(
        script_chars[ops[position].script_index].char
        for begin, end in blocks
        for position in range(begin, end)
        if ops[position].script_index is not None
    )
    assert covered == "料吃家散养土"


def test_error_does_not_shift_timeline_outside_repair_block() -> None:
    """文案插字只调整修复块，之前与之后字符不漂移。"""
    base = load_asr_result().text or ""
    injected = base.replace("农家土鸡蛋", "农家散养土鸡蛋")
    base_chars, _ = run_alignment(base)
    injected_chars, ops = run_alignment(injected)
    blocks = repair_blocks(ops)
    inside = {
        ops[position].script_index
        for begin, end in blocks
        for position in range(begin, end)
        if ops[position].script_index is not None
    }
    first, last = min(inside), max(inside)
    shift = len(injected_chars) - len(base_chars)

    covered = "".join(injected_chars[index].char for index in sorted(inside))
    assert covered == "家散养土"
    assert shift == 2
    for index in range(first):  # 块之前：时间一字不动
        assert injected_chars[index].begin_time_ms == base_chars[index].begin_time_ms
        assert injected_chars[index].end_time_ms == base_chars[index].end_time_ms
    for index in range(last + 1, len(injected_chars)):  # 块之后：时间一字不动
        assert (
            injected_chars[index].begin_time_ms
            == base_chars[index - shift].begin_time_ms
        )
        assert (
            injected_chars[index].end_time_ms == base_chars[index - shift].end_time_ms
        )


def check_pair(script: str, asr: str) -> None:
    """用独立 DP 核对代价，并验证操作完整覆盖两侧字符及其匹配关系。"""
    chars, timeline = synthetic_pair(script, asr)
    ops = align(chars, timeline)
    assert summarize(ops).edit_cost == reference_cost(script, asr)
    assert [op.script_index for op in ops if op.script_index is not None] == list(
        range(len(script))
    )
    assert [op.asr_index for op in ops if op.asr_index is not None] == list(
        range(len(asr))
    )
    for op in ops:
        if op.kind in (MATCH, SUBSTITUTION):
            assert (script[op.script_index] == asr[op.asr_index]) == (op.kind == MATCH)
        elif op.kind == SCRIPT_EXTRA:
            assert op.asr_index is None
        else:
            assert op.kind == ASR_EXTRA
            assert op.script_index is None


def test_exhaustive_short_pairs_and_random_longer_pairs() -> None:
    """穷举短文本并随机生成长文本，验证最优代价与完整回溯。"""
    texts = [
        "".join(chars)
        for length in range(5)
        for chars in itertools.product("甲乙", repeat=length)
    ]
    for script, asr in itertools.product(texts, repeat=2):
        check_pair(script, asr)
    rng = random.Random(20260912)
    for _ in range(500):
        check_pair(
            "".join(rng.choices("甲乙丙丁", k=rng.randrange(40))),
            "".join(rng.choices("甲乙丙丁", k=rng.randrange(40))),
        )


@pytest.mark.parametrize(
    "script, asr, work",
    [
        ("甲乙", "甲乙", 2),
        ("", "甲乙", 2),
        ("甲乙", "", 2),
        ("甲", "乙", 6),
        ("乙甲甲", "丙甲甲", 8),
    ],
)
def test_work_budget_boundary_including_affixes_and_empty_sides(
    script, asr, work
) -> None:
    """裁剪、替换和单侧字符恰好预算时通过，少一个单位时拒绝。"""
    chars, timeline = synthetic_pair(script, asr)
    align(chars, timeline, max_work=work)
    with pytest.raises(AlignmentInputTooLargeError):
        align(chars, timeline, max_work=work - 1)


def test_empty_input_and_invalid_work_budget() -> None:
    """空序列返回空操作，非正工作预算被拒绝。"""
    assert align([], [], max_work=1) == []
    with pytest.raises(ValueError):
        align([], [], max_work=0)


def test_long_text_with_spread_errors_fits_linear_work_budget() -> None:
    """两万字含三处分散替换，在线性规模工作预算内完成。"""
    script = "甲乙丙丁" * 5000
    changed = list(script)
    for index in (10, 10000, 19990):
        changed[index] = "错"
    chars, timeline = synthetic_pair(script, "".join(changed))
    stats = summarize(align(chars, timeline, max_work=4 * len(script)))
    assert stats.edit_cost == 3
    assert stats.substitution_chars == 3


def test_reordered_text_exhausts_budget() -> None:
    """字符相同但顺序相反的文本耗尽预算时及时终止。"""
    chars, timeline = synthetic_pair("甲" * 500 + "乙" * 500, "乙" * 500 + "甲" * 500)
    with pytest.raises(AlignmentInputTooLargeError):
        align(chars, timeline, max_work=1000)


@pytest.mark.parametrize(
    "script, asr, expected",
    [
        ("甲甲乙", "甲乙", [MATCH, SCRIPT_EXTRA, MATCH]),
        ("甲乙", "甲甲乙", [MATCH, ASR_EXTRA, MATCH]),
        ("甲乙", "乙甲", [SUBSTITUTION, SUBSTITUTION]),
    ],
)
def test_repeated_text_has_stable_gap_and_time_mapping(script, asr, expected) -> None:
    """重复文本按固定规则选路径，投射时间单调并保留首尾范围。"""
    chars, timeline = synthetic_pair(script, asr)
    ops = align(chars, timeline)
    assert [op.kind for op in ops] == expected
    assert ops == align(chars, timeline)
    project_times(chars, timeline, ops)
    assert chars[0].begin_time_ms == 0
    assert chars[-1].end_time_ms == len(asr)
    for previous, current in zip(chars, chars[1:]):
        assert previous.end_time_ms <= current.begin_time_ms
        assert current.begin_time_ms < current.end_time_ms


def test_unmatched_lower_bound_never_exceeds_actual_unmatched_chars() -> None:
    """随机文本的未命中下界不超过实际未命中数或编辑代价。"""
    rng = random.Random(20260913)
    for _ in range(200):
        script_text = "".join(rng.choice("甲乙丙丁") for _ in range(rng.randint(0, 12)))
        asr_text = "".join(rng.choice("甲乙丙丁") for _ in range(rng.randint(0, 12)))
        script_chars, asr_chars = synthetic_pair(script_text, asr_text)

        stats = summarize(align(script_chars, asr_chars))
        actual_unmatched = stats.substitution_chars + stats.script_extra_chars
        bound = script_unmatched_lower_bound(script_chars, asr_chars)

        assert 0 <= bound
        assert bound <= actual_unmatched
        assert actual_unmatched <= stats.edit_cost


def test_build_asr_chars_aborts_before_expanding_oversized_transcript() -> None:
    """ASR 字符与词数超限被拒绝，恰好达到上限时允许展开。"""
    long_word = AsrResult(
        sentences=[
            AsrSentence(
                words=[AsrWord(text="鸡" * 50, begin_time_ms=0, end_time_ms=1000)]
            )
        ]
    )
    many_words = AsrResult(
        sentences=[
            AsrSentence(
                words=[
                    AsrWord(text="鸡", begin_time_ms=0, end_time_ms=10)
                    for _ in range(5)
                ]
            )
        ]
    )

    with pytest.raises(AsrTranscriptTooLongError):
        build_asr_chars(long_word, max_chars=10)
    with pytest.raises(AsrTranscriptTooLongError):
        build_asr_chars(many_words, max_words=3)

    assert len(build_asr_chars(long_word, max_chars=50)) == 50
    assert len(build_asr_chars(many_words, max_words=5)) == 5
