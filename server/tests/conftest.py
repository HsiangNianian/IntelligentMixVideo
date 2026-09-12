"""共享 pytest 夹具管理应用生命周期；在 server/ 执行 uv run --locked pytest -v。"""

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from server.app import app
from server.core.config import Settings, get_settings
from .support import StubPlanner, load_asr_result


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    """移除外部 IMV_ 配置与 .env 来源，并在用例前后清空配置缓存。"""
    for key in list(os.environ):
        if key.upper().startswith("IMV_"):
            monkeypatch.delenv(key)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


@pytest.fixture
def client() -> Iterator[TestClient]:
    """提供隔离的 HTTP 客户端；不打开真实端口或连接外部服务。"""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def asr_result():
    """每个测试独立解析 ASR 样本，避免可变时间轴在用例之间共享。"""
    return load_asr_result()


@pytest.fixture
def empty_planner():
    """提供不指定语义切点的离线规划器，用于工程约束测试。"""
    return StubPlanner(clause_ids=[])
