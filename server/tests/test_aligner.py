"""对齐与时间投射测试：文案定文本，ASR 定时间。"""

import itertools
import random
import unittest

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
from support import load_asr_result


def synthetic_pair(script_text: str, asr_text: str) -> tuple[list[AlignedChar], list[AsrChar]]:
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


class WavefrontTests(unittest.TestCase):
    """最优代价、完整回溯与按工作量限流。"""

    def check_pair(self, script: str, asr: str) -> None:
        chars, timeline = synthetic_pair(script, asr)
        ops = align(chars, timeline)
        self.assertEqual(summarize(ops).edit_cost, reference_cost(script, asr))
        self.assertEqual(
            [op.script_index for op in ops if op.script_index is not None],
            list(range(len(script))),
        )
        self.assertEqual(
            [op.asr_index for op in ops if op.asr_index is not None],
            list(range(len(asr))),
        )
        for op in ops:
            if op.kind in (MATCH, SUBSTITUTION):
                self.assertEqual(script[op.script_index] == asr[op.asr_index], op.kind == MATCH)
            elif op.kind == SCRIPT_EXTRA:
                self.assertIsNone(op.asr_index)
            else:
                self.assertEqual(op.kind, ASR_EXTRA)
                self.assertIsNone(op.script_index)

    def test_exhaustive_short_pairs_and_random_longer_pairs(self) -> None:
        texts = [
            "".join(chars)
            for length in range(5)
            for chars in itertools.product("甲乙", repeat=length)
        ]
        for script, asr in itertools.product(texts, repeat=2):
            with self.subTest(script=script, asr=asr):
                self.check_pair(script, asr)
        rng = random.Random(20260912)
        for _ in range(500):
            self.check_pair(
                "".join(rng.choices("甲乙丙丁", k=rng.randrange(40))),
                "".join(rng.choices("甲乙丙丁", k=rng.randrange(40))),
            )

    def test_work_budget_boundary_including_affixes_and_empty_sides(self) -> None:
        for script, asr, work in [
            ("甲乙", "甲乙", 2), ("", "甲乙", 2), ("甲乙", "", 2),
            ("甲", "乙", 6), ("乙甲甲", "丙甲甲", 8),
        ]:
            with self.subTest(script=script, asr=asr):
                chars, timeline = synthetic_pair(script, asr)
                align(chars, timeline, max_work=work)
                with self.assertRaises(AlignmentInputTooLargeError):
                    align(chars, timeline, max_work=work - 1)
        self.assertEqual(align([], [], max_work=1), [])
        with self.assertRaises(ValueError):
            align([], [], max_work=0)

    def test_long_text_with_spread_errors_fits_linear_work_budget(self) -> None:
        script = "甲乙丙丁" * 5000
        changed = list(script)
        for index in (10, 10000, 19990):
            changed[index] = "错"
        chars, timeline = synthetic_pair(script, "".join(changed))
        stats = summarize(align(chars, timeline, max_work=4 * len(script)))
        self.assertEqual(stats.edit_cost, 3)
        self.assertEqual(stats.substitution_chars, 3)

    def test_reordered_text_exhausts_budget(self) -> None:
        chars, timeline = synthetic_pair("甲" * 500 + "乙" * 500, "乙" * 500 + "甲" * 500)
        with self.assertRaises(AlignmentInputTooLargeError):
            align(chars, timeline, max_work=1000)

    def test_repeated_text_has_stable_gap_and_time_mapping(self) -> None:
        for script, asr, expected in [
            ("甲甲乙", "甲乙", [MATCH, SCRIPT_EXTRA, MATCH]),
            ("甲乙", "甲甲乙", [MATCH, ASR_EXTRA, MATCH]),
            ("甲乙", "乙甲", [SUBSTITUTION, SUBSTITUTION]),
        ]:
            chars, timeline = synthetic_pair(script, asr)
            ops = align(chars, timeline)
            self.assertEqual([op.kind for op in ops], expected)
            self.assertEqual(ops, align(chars, timeline))
            project_times(chars, timeline, ops)
            self.assertEqual(chars[0].begin_time_ms, 0)
            self.assertEqual(chars[-1].end_time_ms, len(asr))
            for previous, current in zip(chars, chars[1:]):
                self.assertLessEqual(previous.end_time_ms, current.begin_time_ms)
                self.assertLess(current.begin_time_ms, current.end_time_ms)

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
