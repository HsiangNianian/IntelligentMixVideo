"""切片约束测试：模型管语义，代码管时长。"""

import unittest

from server.sub_api.segmentation.aligner import (
    align,
    build_asr_chars,
    build_script_chars,
    project_times,
)
from server.sub_api.segmentation.builder import build_spans, split_clauses
from support import colon_cuts, load_asr_result, sample_cuts, synthetic_chars

MIN_MS = 1200
MAX_MS = 6000


def aligned_sample():
    script = load_asr_result().text or ""
    script_chars = build_script_chars(script)
    asr_chars = build_asr_chars(load_asr_result())
    ops = align(script_chars, asr_chars)
    project_times(script_chars, asr_chars, ops)
    return script, script_chars


class ClauseTests(unittest.TestCase):
    def test_split_clauses_covers_whole_script(self) -> None:
        script, _ = aligned_sample()
        clauses = split_clauses(script)

        self.assertEqual("".join(clause.text for clause in clauses), script)
        self.assertEqual(clauses[0].number, 1)
        self.assertTrue(clauses[0].text.endswith("，"))


class DurationConstraintTests(unittest.TestCase):
    def test_every_span_respects_duration_limits(self) -> None:
        script, script_chars = aligned_sample()
        outcome = build_spans(
            script_chars,
            len(script),
            sample_cuts(script, script_chars),
            min_duration_ms=MIN_MS,
            max_duration_ms=MAX_MS,
        )

        self.assertTrue(outcome.spans)
        for span in outcome.spans:
            self.assertGreaterEqual(span.duration_ms, MIN_MS)
            self.assertLessEqual(span.duration_ms, MAX_MS)
        joined = "".join(script[span.start_offset : span.end_offset] for span in outcome.spans)
        self.assertEqual(joined, script)

    def test_spans_are_contiguous_and_monotonic(self) -> None:
        script, script_chars = aligned_sample()
        spans = build_spans(
            script_chars,
            len(script),
            sample_cuts(script, script_chars),
            min_duration_ms=MIN_MS,
            max_duration_ms=MAX_MS,
        ).spans

        for previous, current in zip(spans, spans[1:], strict=False):
            self.assertEqual(previous.end_offset, current.start_offset)
            self.assertLessEqual(previous.end_time_ms, current.start_time_ms)
            self.assertLess(previous.start_time_ms, previous.end_time_ms)

    def test_short_clause_is_merged_under_duration_floor(self) -> None:
        script, script_chars = aligned_sample()
        outcome = build_spans(
            script_chars,
            len(script),
            sample_cuts(script, script_chars),
            min_duration_ms=MIN_MS,
            max_duration_ms=MAX_MS,
        )

        # 「怎么又买一箱？」只有 0.92s，必须被合并
        self.assertGreaterEqual(outcome.merge_count, 1)
        for span in outcome.spans:
            self.assertGreaterEqual(span.duration_ms, MIN_MS)

    def test_long_sentence_is_split_under_duration_ceiling(self) -> None:
        script, script_chars = aligned_sample()
        outcome = build_spans(
            script_chars,
            len(script),
            colon_cuts(script, script_chars),
            min_duration_ms=MIN_MS,
            max_duration_ms=MAX_MS,
        )

        # 末句 11.12s，必须被切开
        self.assertGreaterEqual(outcome.split_count, 3)
        for span in outcome.spans:
            self.assertLessEqual(span.duration_ms, MAX_MS)

    def test_number_and_latin_runs_are_never_split(self) -> None:
        text = "销量增长40%，QQ弹弹"
        chars = synthetic_chars(text)
        builder_logger = "server.sub_api.segmentation.builder"
        with self.assertLogs(builder_logger, level="WARNING") as captured:
            spans = build_spans(chars, len(text), [], min_duration_ms=1, max_duration_ms=6).spans
        boundaries = {span.start_offset for span in spans}

        # 保护区间内不可切，工具会明确告警而不是静默产出碎片
        self.assertIn("segment_split_has_no_valid_cut", captured.output[0])

        self.assertNotIn(text.index("%"), boundaries)                       # 不能切在 40 | %
        self.assertNotIn(text.index("0%"), boundaries)                      # 不能切在 4 | 0%
        self.assertNotIn(text.index("Q", text.index("QQ") + 1), boundaries)  # 不能切在 Q | Q

    def test_split_prefers_boundary_outside_repair_block(self) -> None:
        text = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥"
        chars = synthetic_chars(text, char_ms=200)
        block = (10, 12)                       # 修复块内部时间为估算值

        without = build_spans(chars, len(text), [], min_duration_ms=100, max_duration_ms=500).spans
        with_block = build_spans(
            chars,
            len(text),
            [],
            min_duration_ms=100,
            max_duration_ms=500,
            repair_block_ranges=[block],
        ).spans
        baseline = {span.start_char for span in without} - {0}
        boundaries = {span.start_char for span in with_block} - {0}

        self.assertIn(11, baseline)            # 不传修复块时，切点会落在块内部
        self.assertNotIn(11, boundaries)       # 传入修复块后，切点被推到块边缘之外
        for span in with_block:
            self.assertLessEqual(span.duration_ms, 500)


if __name__ == "__main__":
    unittest.main()
