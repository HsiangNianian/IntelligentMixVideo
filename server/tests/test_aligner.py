"""对齐与时间投射测试：文案定文本，ASR 定时间。"""

import unittest

from server.sub_api.segmentation.aligner import (
    ASR_EXTRA,
    MATCH,
    SUBSTITUTION,
    align,
    build_asr_chars,
    build_script_chars,
    project_times,
    repair_blocks,
    summarize,
)
from support import load_asr_result


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


if __name__ == "__main__":
    unittest.main()
