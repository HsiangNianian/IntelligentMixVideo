"""对齐与时间投射测试：文案定文本，ASR 定时间。"""

import random
import unittest

from server.core.errors import AsrTranscriptTooLongError
from server.sub_api.segmentation.aligner import (
    ASR_EXTRA,
    GAP_COST,
    MATCH,
    MATCH_COST,
    SCRIPT_EXTRA,
    SUBSTITUTION,
    SUBSTITUTION_COST,
    AlignedChar,
    AlignmentOp,
    AsrChar,
    align,
    alignment_span,
    build_asr_chars,
    build_script_chars,
    project_times,
    repair_blocks,
    script_unmatched_lower_bound,
    summarize,
)
from server.sub_api.segmentation.schemas import AsrResult, AsrSentence, AsrWord
from support import load_asr_result


def synthetic_pair(script_text: str, asr_text: str) -> tuple[list[AlignedChar], list[AsrChar]]:
    """构造只关心字符序列的对照输入，时间字段不参与对齐。"""
    script_chars = [
        AlignedChar(index=index, char=char, normalized=char)
        for index, char in enumerate(script_text)
    ]
    asr_chars = [
        AsrChar(
            char=char,
            normalized=char,
            begin_time_ms=float(index),
            end_time_ms=float(index + 1),
            word_text=char,
            confidence=None,
            is_word_start=False,
        )
        for index, char in enumerate(asr_text)
    ]
    return script_chars, asr_chars


def reference_align(
    script_chars: list[AlignedChar], asr_chars: list[AsrChar]
) -> list[AlignmentOp]:
    """未做任何裁剪的完整矩阵版本，用于校验前后缀裁剪不改变最优解。"""
    rows, columns = len(script_chars), len(asr_chars)
    table = [[0] * (columns + 1) for _ in range(rows + 1)]
    for i in range(1, rows + 1):
        table[i][0] = i * GAP_COST
    for j in range(1, columns + 1):
        table[0][j] = j * GAP_COST
    for i in range(1, rows + 1):
        for j in range(1, columns + 1):
            cost = (
                MATCH_COST
                if script_chars[i - 1].normalized == asr_chars[j - 1].normalized
                else SUBSTITUTION_COST
            )
            table[i][j] = min(
                table[i - 1][j - 1] + cost,
                table[i - 1][j] + GAP_COST,
                table[i][j - 1] + GAP_COST,
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
            ops.append(AlignmentOp(MATCH if cost == MATCH_COST else SUBSTITUTION, i - 1, j - 1))
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


def run_alignment(script: str):
    asr_result = load_asr_result()
    script_chars = build_script_chars(script)
    asr_chars = build_asr_chars(asr_result)
    ops = align(script_chars, asr_chars)
    project_times(script_chars, asr_chars, ops)
    return script_chars, ops


class AsrPayloadTests(unittest.TestCase):
    def test_unwraps_fun_asr_word_level_timeline(self) -> None:
        result = load_asr_result()

        self.assertEqual(result.audio_duration_ms, 36811)
        self.assertEqual(len(result.sentences), 6)
        self.assertEqual(len(result.iter_words()), 138)
        first = result.iter_words()[0]
        self.assertEqual((first.text, first.begin_time_ms, first.end_time_ms), ("刚才", 160, 360))


class ScriptAlignerTests(unittest.TestCase):
    def test_identical_script_inherits_word_timeline(self) -> None:
        script = load_asr_result().text or ""
        script_chars, ops = run_alignment(script)
        stats = summarize(ops)

        self.assertEqual(stats.matched_chars, len(script_chars))
        self.assertEqual(stats.edit_cost, 0)
        self.assertEqual(script_chars[0].begin_time_ms, 160)
        self.assertEqual(script_chars[-1].end_time_ms, 36700)

    def test_first_sentence_boundaries_use_asr_time(self) -> None:
        script = load_asr_result().text or ""
        script_chars, ops = run_alignment(script)

        self.assertTrue(all(op.kind == MATCH for op in ops))
        self.assertEqual(script_chars[16].char, "吗")
        self.assertEqual(
            (script_chars[16].begin_time_ms, script_chars[16].end_time_ms), (2280, 2400)
        )
        self.assertEqual(script_chars[17].begin_time_ms, 2520)

    def test_substitution_borrows_matched_asr_time(self) -> None:
        script = (load_asr_result().text or "").replace("囤一点", "屯一点")
        script_chars, ops = run_alignment(script)
        substitutions = [op for op in ops if op.kind == SUBSTITUTION]

        self.assertEqual(len(substitutions), 1)
        target = script_chars[substitutions[0].script_index]
        self.assertEqual(target.char, "屯")
        self.assertEqual((target.begin_time_ms, target.end_time_ms), (14480, 14600))

    def test_asr_extra_words_are_absorbed_into_repair_block(self) -> None:
        script = (load_asr_result().text or "").replace("不吃饲料那些的", "不吃饲料")
        script_chars, ops = run_alignment(script)
        extras = [op for op in ops if op.kind == ASR_EXTRA]

        self.assertEqual(len(extras), 3)
        position = next(index for index, char in enumerate(script_chars) if char.char == "料")
        self.assertEqual(script_chars[position + 1].char, "吃")
        self.assertEqual(script_chars[position].begin_time_ms, 9420)          # 块左边界不动
        self.assertEqual(script_chars[position + 1].end_time_ms, 10320)       # 块右边界不动
        self.assertEqual(
            script_chars[position].end_time_ms, script_chars[position + 1].begin_time_ms
        )
        self.assertEqual(script_chars[position + 2].begin_time_ms, 10320)     # 块之后不动
        self.assertNotIn("那些的", "".join(char.char for char in script_chars))

    def test_repair_blocks_cover_differences_with_neighbour_chars(self) -> None:
        base = load_asr_result().text or ""
        script = base.replace("不吃饲料那些的", "不吃饲料").replace(
            "农家土鸡蛋", "农家散养土鸡蛋"
        )
        script_chars, ops = run_alignment(script)
        blocks = repair_blocks(ops)

        self.assertEqual(len(blocks), 2)
        covered = "".join(
            script_chars[ops[position].script_index].char
            for begin, end in blocks
            for position in range(begin, end)
            if ops[position].script_index is not None
        )
        self.assertEqual(covered, "料吃家散养土")

    def test_error_does_not_shift_timeline_outside_repair_block(self) -> None:
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
        self.assertEqual(covered, "家散养土")
        self.assertEqual(shift, 2)
        for index in range(first):                                 # 块之前：时间一字不动
            self.assertEqual(injected_chars[index].begin_time_ms, base_chars[index].begin_time_ms)
            self.assertEqual(injected_chars[index].end_time_ms, base_chars[index].end_time_ms)
        for index in range(last + 1, len(injected_chars)):         # 块之后：时间一字不动
            self.assertEqual(
                injected_chars[index].begin_time_ms, base_chars[index - shift].begin_time_ms
            )
            self.assertEqual(
                injected_chars[index].end_time_ms, base_chars[index - shift].end_time_ms
            )


class AlignmentScaleTests(unittest.TestCase):
    """公共前后缀裁剪与规模闸门。"""

    def test_alignment_span_excludes_matching_affixes(self) -> None:
        script_chars, asr_chars = synthetic_pair("甲乙丙丁戊", "甲乙丙丁戊")
        self.assertEqual(alignment_span(script_chars, asr_chars), (0, 0))

        script_chars, asr_chars = synthetic_pair("头甲乙丙尾", "头甲错丙尾")
        self.assertEqual(alignment_span(script_chars, asr_chars), (1, 1))

    def test_affix_trimming_preserves_optimal_alignment_stats(self) -> None:
        rng = random.Random(20260912)
        for _ in range(200):
            script_text = "".join(rng.choice("甲乙丙丁") for _ in range(rng.randint(0, 12)))
            asr_text = "".join(rng.choice("甲乙丙丁") for _ in range(rng.randint(0, 12)))
            script_chars, asr_chars = synthetic_pair(script_text, asr_text)

            ops = align(script_chars, asr_chars)

            self.assertEqual(summarize(ops), summarize(reference_align(script_chars, asr_chars)))
            self.assertEqual(
                [op.script_index for op in ops if op.script_index is not None],
                list(range(len(script_chars))),
            )
            self.assertEqual(
                [op.asr_index for op in ops if op.asr_index is not None],
                list(range(len(asr_chars))),
            )

    def test_unmatched_lower_bound_never_exceeds_actual_unmatched_chars(self) -> None:
        rng = random.Random(20260913)
        for _ in range(200):
            script_text = "".join(rng.choice("甲乙丙丁") for _ in range(rng.randint(0, 12)))
            asr_text = "".join(rng.choice("甲乙丙丁") for _ in range(rng.randint(0, 12)))
            script_chars, asr_chars = synthetic_pair(script_text, asr_text)

            stats = summarize(align(script_chars, asr_chars))
            actual_unmatched = stats.substitution_chars + stats.script_extra_chars
            bound = script_unmatched_lower_bound(script_chars, asr_chars)

            self.assertLessEqual(0, bound)
            self.assertLessEqual(bound, actual_unmatched)
            self.assertLessEqual(actual_unmatched, stats.edit_cost)

    def test_build_asr_chars_aborts_before_expanding_oversized_transcript(self) -> None:
        long_word = AsrResult(
            sentences=[
                AsrSentence(words=[AsrWord(text="鸡" * 50, begin_time_ms=0, end_time_ms=1000)])
            ]
        )
        many_words = AsrResult(
            sentences=[
                AsrSentence(
                    words=[AsrWord(text="鸡", begin_time_ms=0, end_time_ms=10) for _ in range(5)]
                )
            ]
        )

        with self.assertRaises(AsrTranscriptTooLongError):
            build_asr_chars(long_word, max_chars=10)
        with self.assertRaises(AsrTranscriptTooLongError):
            build_asr_chars(many_words, max_words=3)

        self.assertEqual(len(build_asr_chars(long_word, max_chars=50)), 50)
        self.assertEqual(len(build_asr_chars(many_words, max_words=5)), 5)


if __name__ == "__main__":
    unittest.main()
