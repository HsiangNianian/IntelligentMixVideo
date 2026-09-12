"""通过真实 SDK 和本地 HTTP 替身验证重试只有一层。

在 server/ 执行 uv run --locked pytest -v；使用离线样本与替身。
"""

from unittest.mock import patch

import pytest

import httpx
from openai import OpenAI

from server.core.config import Settings
from server.app import app
from server.core.errors import LLMOutputInvalidError, LLMProviderError
from server.sub_api.segmentation.builder import split_clauses
from server.sub_api.segmentation.router import get_segment_service


@pytest.mark.parametrize(
    "retries, status, error",
    (
        (0, 500, LLMProviderError),
        (1, 500, None),
        (1, 200, LLMOutputInvalidError),
    ),
)
def test_sdk_retry_budget_and_invalid_output(retries, status, error) -> None:
    """SDK 遵守重试预算，服务错误可恢复而非法 JSON 不重试。"""
    requests = []

    def respond(request):
        """首次返回指定错误，后续返回合法切点，并记录请求次数。"""
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                status, json={"choices": [{"message": {"content": "invalid JSON"}}]}
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"boundaries_after": [1]}'}}]},
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
        with patch(
            "server.llm.client.OpenAI",
            side_effect=lambda **kwargs: OpenAI(**kwargs, http_client=http_client),
        ):
            service = get_segment_service(
                Settings(
                    _env_file=None,
                    llm_base_url="https://example.test/v1",
                    llm_api_key="test-key",
                    llm_model="test-model",
                    llm_max_retries=retries,
                )
            )
        clauses = split_clauses("第一句。第二句。")
        if error:
            with pytest.raises(error):
                service.planner.plan_boundaries(clauses)
        else:
            assert service.planner.plan_boundaries(clauses) == [1]
        assert len(requests) == (2 if error is None else 1)


@pytest.mark.parametrize(
    "transport_error,status,code",
    [
        (httpx.ConnectError, 502, "llm_provider_error"),
        (httpx.ReadTimeout, 504, "llm_timeout"),
    ],
)
def test_transport_failure_returns_api_error(
    client, monkeypatch, transport_error, status, code
):
    """SDK 连接失败与超时转换为约定 HTTP 错误，无属性异常且不返回片段。"""
    from .support import load_asr_payload

    requests = []

    def fail(request):
        """记录请求后抛出本地传输异常，不连接网络。"""
        requests.append(request)
        raise transport_error("test transport failure", request=request)

    with httpx.Client(transport=httpx.MockTransport(fail)) as http_client:
        with patch(
            "server.llm.client.OpenAI",
            side_effect=lambda **kwargs: OpenAI(**kwargs, http_client=http_client),
        ):
            service = get_segment_service(
                Settings(
                    _env_file=None,
                    llm_base_url="https://example.test/v1",
                    llm_api_key="test-key",
                    llm_model="test-model",
                    llm_max_retries=0,
                )
            )
        monkeypatch.setitem(
            app.dependency_overrides, get_segment_service, lambda: service
        )
        payload = load_asr_payload()
        response = client.post(
            "/segmentations",
            json={
                "script": payload["transcripts"][0]["text"],
                "asr_result": payload,
            },
        )
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "segments" not in response.json()
    assert len(requests) == 1
