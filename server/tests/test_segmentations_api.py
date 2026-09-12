"""文案切片接口测试。"""

import unittest

from fastapi.testclient import TestClient
from server.app import app
from server.core.config import Settings, get_settings
from server.sub_api.segmentation.aligner import (
    align,
    build_asr_chars,
    build_script_chars,
    repair_blocks,
)
from server.sub_api.segmentation.router import get_segment_service
from server.sub_api.segmentation.schemas import AsrResult
from server.sub_api.segmentation.service import SegmentService
from support import StubPlanner, broken_script, load_asr_payload

MIN_MS = 1200
MAX_MS = 6000


class SegmentationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[get_segment_service] = lambda: SegmentService(StubPlanner())
        self.addCleanup(app.dependency_overrides.clear)
        self.client = self.enterContext(TestClient(app))

    def post(self, script: str, asr_payload: dict):
        return self.client.post(
            "/segmentations", json={"script": script, "asr_result": asr_payload}
        )

    def assert_segment_contract(self, script: str, body: dict) -> None:
        segments = body["segments"]
        self.assertTrue(segments)
        self.assertEqual("".join(segment["text"] for segment in segments), script)
        previous_end = None
        for index, segment in enumerate(segments):
            if index:
                self.assertEqual(segment["start_time_ms"], previous_end)   # 首尾相接
            self.assertTrue(segment["segment_id"].startswith("seg_"))
            self.assertLess(segment["start_time_ms"], segment["end_time_ms"])
            duration = segment["end_time_ms"] - segment["start_time_ms"]
            self.assertGreaterEqual(duration, MIN_MS)
            self.assertLessEqual(duration, MAX_MS)
            previous_end = segment["end_time_ms"]
            for keyword in segment["keywords"]:
                self.assertEqual(
                    segment["text"][keyword["start"] : keyword["end"]], keyword["text"]
                )

    def test_aligns_script_with_asr_timeline(self) -> None:
        payload = load_asr_payload()
        script = payload["transcripts"][0]["text"]

        response = self.post(script, payload)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assert_segment_contract(script, body)
        self.assertEqual(body["trace"]["edit_cost"], 0)
        self.assertEqual(body["trace"]["repair_block_count"], 0)
        self.assertEqual(body["segments"][0]["start_time_ms"], 160)
        self.assertEqual(body["segments"][-1]["end_time_ms"], 36700)
        self.assertTrue(any(segment["keywords"] for segment in body["segments"]))

    def test_survives_typo_missing_and_extra_chars(self) -> None:
        payload = load_asr_payload()
        script = broken_script(payload["transcripts"][0]["text"])

        response = self.post(script, payload)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assert_segment_contract(script, body)
        trace = body["trace"]
        self.assertEqual(trace["substitution_chars"], 2)
        self.assertEqual(trace["asr_extra_chars"], 3)
        self.assertEqual(trace["script_extra_chars"], 2)
        self.assertEqual(trace["repair_block_count"], 2)
        self.assertEqual(trace["edit_cost"], 7)

    def test_boundaries_stay_outside_repair_blocks(self) -> None:
        payload = load_asr_payload()
        script = broken_script(payload["transcripts"][0]["text"])
        script_chars = build_script_chars(script)
        blocks = repair_blocks(
            align(script_chars, build_asr_chars(AsrResult.model_validate(payload)))
        )
        block_offsets = [
            (script_chars[begin].index, script_chars[end - 1].index + 1)
            for begin, end in blocks
        ]

        response = self.post(script, payload)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["trace"]["repair_block_count"], len(block_offsets))
        self.assertEqual(len(block_offsets), 2)
        cursor = 0
        for segment in body["segments"][:-1]:
            cursor += len(segment["text"])
            for begin, end in block_offsets:
                self.assertFalse(begin < cursor < end)

    def test_rejects_asr_without_word_timeline(self) -> None:
        payload = load_asr_payload()
        for sentence in payload["transcripts"][0]["sentences"]:
            sentence.pop("words", None)

        response = self.post(payload["transcripts"][0]["text"], payload)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "asr_timeline_missing")

    def test_rejects_unrelated_script(self) -> None:
        response = self.post("今天讲解 Python 异步编程与协程调度。", load_asr_payload())

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "script_asr_alignment_failed")

    def test_requires_model_configuration(self) -> None:
        app.dependency_overrides.pop(get_segment_service)
        app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)

        response = self.post("任意文案", load_asr_payload())

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "llm_provider_error")


if __name__ == "__main__":
    unittest.main()
