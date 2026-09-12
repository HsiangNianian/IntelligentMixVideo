"""模拟请求，不调用云服务；ASR 模块依赖 python-dotenv。

运行：PYTHONPATH=server/src python -m unittest discover -s server/tests -v
"""

import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from server.asr import transcribe


class TranscribeTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(
            os.environ,
            {
                "ASR_BASE_URL": "https://dashscope.aliyuncs.com/api/v1",
                "DASHSCOPE_API_KEY": "test-key",
            },
        )
        env.start()
        self.addCleanup(env.stop)
        http = patch("server.asr.urlopen")
        self.http = http.start()
        self.addCleanup(http.stop)
        sleep = patch("server.asr.time.sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)
        stderr = patch("server.asr.sys.stderr", new_callable=io.StringIO)
        stderr.start()
        self.addCleanup(stderr.stop)
        self.url = "https://audio.example/tts.wav"
        self.submitted = {"output": {"task_id": "task-123"}}
        self.done = {
            "output": {
                "task_status": "SUCCEEDED",
                "results": [
                    {
                        "subtask_status": "SUCCEEDED",
                        "transcription_url": "https://results.example/result.json",
                    }
                ],
            }
        }

    def respond(self, *documents):
        self.http.side_effect = [
            io.BytesIO(json.dumps(document).encode()) for document in documents
        ]

    def test_returns_original_json_and_does_not_send_key_to_download(self):
        document = {
            "properties": {"channels": [0]},
            "transcripts": [
                {
                    "text": "你好",
                    "sentences": [
                        {
                            "words": [
                                {"text": "你好", "begin_time": 100, "end_time": 500},
                            ]
                        }
                    ],
                }
            ],
        }
        self.respond(
            self.submitted, {"output": {"task_status": "RUNNING"}}, self.done, document
        )
        self.assertEqual(transcribe(self.url), document)
        calls = self.http.call_args_list
        submitted = calls[0].args[0]
        self.assertEqual(submitted.get_method(), "POST")
        self.assertEqual(
            json.loads(submitted.data),
            {
                "model": "fun-asr",
                "input": {"file_urls": [self.url]},
                "parameters": {},
            },
        )
        self.assertEqual(submitted.get_header("X-dashscope-async"), "enable")
        self.assertEqual(submitted.get_header("Content-type"), "application/json")
        for call in calls[:-1]:
            self.assertEqual(
                call.args[0].get_header("Authorization"), "Bearer test-key"
            )
        self.assertEqual(
            calls[1].args[0].full_url,
            "https://dashscope.aliyuncs.com/api/v1/tasks/task-123",
        )
        self.assertIsNone(calls[-1].args[0].get_header("Authorization"))
        self.sleep.assert_called_once_with(2)

    def test_invalid_input_fails_before_network(self):
        for url, key in (
            ("file:///tmp/audio.wav", "key"),
            ("invalid", "key"),
            (self.url, " "),
        ):
            with (
                self.subTest(url=url, key=key),
                patch.dict(os.environ, {"DASHSCOPE_API_KEY": key}),
                self.assertRaises(ValueError),
            ):
                transcribe(url)
        self.http.assert_not_called()

    def test_terminal_failure(self):
        for status in ("FAILED", "CANCELED", "UNKNOWN"):
            self.respond(self.submitted, {"output": {"task_status": status}})
            with (
                self.subTest(status=status),
                self.assertRaisesRegex(RuntimeError, status),
            ):
                transcribe(self.url)

    def test_failed_subtask_under_successful_task(self):
        self.respond(
            self.submitted,
            {
                "output": {
                    "task_status": "SUCCEEDED",
                    "results": [
                        {"subtask_status": "FAILED", "code": "FILE_DOWNLOAD_FAILED"},
                    ],
                }
            },
        )
        with self.assertRaisesRegex(RuntimeError, "FILE_DOWNLOAD_FAILED"):
            transcribe(self.url)
        self.assertEqual(self.http.call_count, 2)

    def test_missing_result_url(self):
        self.done["output"]["results"][0].pop("transcription_url")
        self.respond(self.submitted, self.done)
        with self.assertRaisesRegex(RuntimeError, "缺少转写结果地址"):
            transcribe(self.url)

    def test_timeout_keeps_task_id(self):
        self.respond(self.submitted, {"output": {"task_status": "RUNNING"}})
        with (
            patch("server.asr.time.monotonic", side_effect=[0, 0, 1801]),
            self.assertRaisesRegex(TimeoutError, "task-123"),
        ):
            transcribe(self.url)
        self.assertEqual(self.http.call_count, 2)

    def test_http_error_propagates_without_resubmitting(self):
        self.http.side_effect = HTTPError(
            "https://example.com", 401, "Unauthorized", {}, None
        )
        with self.assertRaises(HTTPError):
            transcribe(self.url)
        self.assertEqual(self.http.call_count, 1)


if __name__ == "__main__":
    unittest.main()
