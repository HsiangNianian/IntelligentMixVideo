"""共享 pytest 夹具管理应用生命周期并隔离 ASR 请求；在 server/ 执行 uv run --locked pytest -v。"""

from collections.abc import Iterator
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from server.app import app

from server import asr


@pytest.fixture
def client() -> Iterator[TestClient]:
    """提供隔离的 HTTP 客户端；不打开真实端口或连接外部服务。"""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def asr_env(monkeypatch, tmp_path):
    """用临时仓库路径和假凭证隔离真实 .env，结束后自动恢复环境。"""
    monkeypatch.setattr(asr, "__file__", str(tmp_path / "server/src/server/asr.py"))
    monkeypatch.setenv("ASR_BASE_URL", "https://dashscope.aliyuncs.com/api/v1")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    return tmp_path / ".env"


@pytest.fixture
def asr_http(monkeypatch):
    """替换网络和轮询等待；未配置响应的请求立即失败，不访问云服务。"""
    http = Mock(side_effect=AssertionError("测试未配置 HTTP 响应"))
    monkeypatch.setattr(asr, "urlopen", http)
    monkeypatch.setattr(asr.time, "sleep", Mock())
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
