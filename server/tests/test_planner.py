"""通过真实 SDK 和本地 HTTP 替身验证重试只有一层。"""

import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI

from server.core.config import Settings
from server.core.errors import LLMOutputInvalidError, LLMProviderError
from server.sub_api.segmentation.builder import split_clauses
from server.sub_api.segmentation.router import get_segment_service


class PlannerRetryTests(unittest.TestCase):
    def test_sdk_retry_budget_and_invalid_output(self) -> None:
        for retries, status, error in (
            (0, 500, LLMProviderError),
            (1, 500, None),
            (1, 200, LLMOutputInvalidError),
        ):
            with self.subTest(retries=retries, status=status):
                requests = []

                def respond(request):
                    requests.append(request)
                    if len(requests) == 1:
                        return httpx.Response(status, json={
                            "choices": [{"message": {"content": "invalid JSON"}}]
                        })
                    return httpx.Response(200, json={
                        "choices": [{"message": {"content": '{"boundaries_after": [1]}'}}]
                    })

                with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
                    with patch("server.llm.client.OpenAI", side_effect=lambda **kwargs: OpenAI(
                        **kwargs, http_client=http_client
                    )):
                        service = get_segment_service(Settings(
                            _env_file=None,
                            llm_base_url="https://example.test/v1",
                            llm_api_key="test-key",
                            llm_model="test-model",
                            llm_max_retries=retries,
                        ))
                    clauses = split_clauses("第一句。第二句。")
                    if error:
                        with self.assertRaises(error):
                            service.planner.plan_boundaries(clauses)
                    else:
                        self.assertEqual(service.planner.plan_boundaries(clauses), [1])
                    self.assertEqual(len(requests), 2 if error is None else 1)
