"""共享 pytest 夹具管理应用生命周期并隔离 ASR 请求；在 server/ 执行 uv run --locked pytest -v。"""

from collections.abc import Iterator
from functools import partial

import httpx
import pytest
from fastapi.testclient import TestClient
from server.app import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    """提供隔离的 HTTP 客户端；不打开真实端口或连接外部服务。"""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def asr(mocker):
    """首次导入时屏蔽真实 .env 读取；返回 ASR 实现供各用例替换配置。"""
    env_reader = mocker.patch(
        "pydantic_settings.sources.DotEnvSettingsSource._read_env_files",
        return_value={},
    )
    from server.asr import asr

    mocker.stop(env_reader)
    return asr


@pytest.fixture
def asr_env(asr, monkeypatch):
    """为每个用例注入假配置，禁止从文件读取真实凭证。"""
    settings = asr.ASRSettings(_env_file=None, dashscope_api_key="test-key")
    monkeypatch.setattr(asr, "settings", settings)
    return settings


@pytest.fixture
def anyio_backend():
    """使用已有 AnyIO 插件在 asyncio 上运行异步用例，不额外引入测试依赖。"""
    return "asyncio"


@pytest.fixture
def asr_http(asr, mocker):
    """用内存传输替换异步客户端网络，保留资源生命周期并跳过轮询等待。"""
    http = mocker.Mock(side_effect=AssertionError("测试未配置 HTTP 响应"))
    mocker.patch.object(asr.asyncio, "sleep")
    mocker.patch.object(
        asr.httpx,
        "AsyncClient",
        side_effect=partial(httpx.AsyncClient, transport=httpx.MockTransport(http)),
    )
    return http


@pytest.fixture
def asr_responses():
    """提供单音频提交、成功任务及原始字词结果，各用例独立修改。"""
    return [
        {"output": {"task_id": "task-123"}},
        {
            "output": {
                "task_status": "SUCCEEDED",
                "results": [
                    {
                        "subtask_status": "SUCCEEDED",
                        "transcription_url": "https://results.example/result.json",
                    }
                ],
            }
        },
        {
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
            ]
        },
    ]
