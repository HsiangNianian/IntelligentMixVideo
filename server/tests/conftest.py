"""共享 pytest 夹具隔离模型配置并管理应用生命周期；在 server/ 执行 uv run --locked pytest -v。"""

from collections.abc import Iterator
import os

import pytest
from fastapi.testclient import TestClient

from server.app import app
from server.segmentation import segmentation


@pytest.fixture(autouse=True)
def isolate_config(monkeypatch):
    """清除外部 IMV_ 配置并禁用本机 .env；配置专项测试须显式注入，无配置缓存。"""
    for key in list(os.environ):
        if key.upper().startswith("IMV_"):
            monkeypatch.delenv(key)
    monkeypatch.setattr(segmentation, "dotenv_values", lambda _: {})


@pytest.fixture
def client() -> Iterator[TestClient]:
    """提供隔离的 HTTP 客户端；不打开真实端口或连接外部服务。"""
    with TestClient(app) as test_client:
        yield test_client
