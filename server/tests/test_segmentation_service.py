"""片段构建服务测试：编排、约束与降级行为。"""

import unittest

from server.sub_api.segmentation.service import SegmentService
from support import StubPlanner, broken_script, load_asr_result


class SegmentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.asr_result = load_asr_result()

    def test_builds_segments_and_keywords_from_planner(self) -> None:
        service = SegmentService(StubPlanner())

        body = service.build(script=self.asr_result.text or "", asr_result=self.asr_result)

        self.assertTrue(body.segments)
        self.assertEqual(
            "".join(segment.text for segment in body.segments), self.asr_result.text
        )
        self.assertEqual(body.trace.keyword_rejected_count, 0)
        self.assertTrue(any(segment.keywords for segment in body.segments))
        for segment in body.segments:
            for keyword in segment.keywords:
                self.assertEqual(segment.text[keyword.start : keyword.end], keyword.text)

    def test_passes_sliced_texts_to_keyword_planner(self) -> None:
        planner = StubPlanner()
        service = SegmentService(planner)

        body = service.build(script=self.asr_result.text or "", asr_result=self.asr_result)

        self.assertEqual(planner.keyword_texts, [segment.text for segment in body.segments])

    def test_model_cut_inside_repair_block_is_snapped_to_edge(self) -> None:
        script = (self.asr_result.text or "").replace("农家土鸡蛋", "农家散养土鸡蛋")
        # 分句 12 的结尾落在「散养」修复块内部（块内时间为估算值）
        planner = StubPlanner(clause_ids=[12])
        service = SegmentService(planner, max_duration_ms=30000)

        body = service.build(script=script, asr_result=self.asr_result)

        self.assertEqual(body.trace.repair_block_count, 1)
        self.assertEqual(len(body.segments), 2)
        self.assertFalse(body.segments[0].text.endswith("农家散"))

    def test_enforces_duration_limits_when_model_cuts_are_coarse(self) -> None:
        service = SegmentService(StubPlanner(clause_ids=[2]))

        body = service.build(script=self.asr_result.text or "", asr_result=self.asr_result)

        self.assertGreaterEqual(body.trace.split_count, 1)
        for segment in body.segments:
            duration = segment.end_time_ms - segment.start_time_ms
            self.assertGreaterEqual(duration, 1200)
            self.assertLessEqual(duration, 6000)

    def test_one_to_one_substitutions_do_not_form_repair_blocks(self) -> None:
        script = (self.asr_result.text or "").replace("那些", "这些").replace("鸡蛋", "鸭蛋")
        service = SegmentService(StubPlanner())

        body = service.build(script=script, asr_result=self.asr_result)

        # 一对一错字直接替换并继承 ASR 时间，不产生修复块，也不漂移
        self.assertEqual(body.trace.substitution_chars, 6)
        self.assertEqual(body.trace.repair_block_count, 0)
        self.assertEqual(body.trace.edit_cost, 6)
        self.assertEqual(body.segments[0].start_time_ms, 160)
        self.assertEqual(body.segments[-1].end_time_ms, 36700)

    def test_survives_typo_missing_and_extra_chars(self) -> None:
        script = broken_script(self.asr_result.text or "")
        service = SegmentService(StubPlanner())

        body = service.build(script=script, asr_result=self.asr_result)

        self.assertEqual("".join(segment.text for segment in body.segments), script)
        self.assertEqual(body.trace.substitution_chars, 2)
        self.assertEqual(body.trace.asr_extra_chars, 3)
        self.assertEqual(body.trace.script_extra_chars, 2)
        self.assertEqual(body.trace.repair_block_count, 2)
        self.assertEqual(body.trace.edit_cost, 7)

    def test_rejects_asr_without_word_timeline(self) -> None:
        from server.core.errors import AsrTimelineMissingError

        payload = load_asr_result().model_dump()
        for sentence in payload["sentences"]:
            sentence.pop("words", None)
        from server.sub_api.segmentation.schemas import AsrResult

        with self.assertRaises(AsrTimelineMissingError):
            SegmentService(StubPlanner()).build(
                script="随便一段文案内容",
                asr_result=AsrResult.model_validate(payload),
            )

    def test_rejects_unrelated_script(self) -> None:
        from server.core.errors import ScriptAlignmentError

        with self.assertRaises(ScriptAlignmentError):
            SegmentService(StubPlanner()).build(
                script="今天讲解 Python 异步编程与协程调度。",
                asr_result=self.asr_result,
            )


if __name__ == "__main__":
    unittest.main()
