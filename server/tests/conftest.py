"""共享 pytest 夹具管理应用生命周期；在 server/ 执行 uv run --locked pytest -v。"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from server.app import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    """提供隔离的 HTTP 客户端；不打开真实端口或连接外部服务。"""
    with TestClient(app) as test_client:
        yield test_client
