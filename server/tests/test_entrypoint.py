"""验证两种启动入口；在 server/ 执行 uv run --locked pytest tests/test_entrypoint.py。"""

from importlib import import_module
from importlib.metadata import distribution
import runpy
import sys

import pytest
import uvicorn

from server.app import app


@pytest.mark.parametrize("entry", ["console", "module"])
def test_startup_entry(entry: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """替换阻塞的事件循环，检查真实入口的应用路径可导入且监听设置正确。"""
    calls = []

    def capture_startup(application: str, **options: object) -> None:
        """记录启动请求，避免测试占用用户机器的固定端口。"""
        calls.append((application, options))

    monkeypatch.setattr(uvicorn, "run", capture_startup)
    if entry == "console":
        command = next(
            item for item in distribution("imv-server").entry_points
            if item.group == "console_scripts" and item.name == "server"
        )
        command.load()()
    else:
        # 控制台用例可能已导入该模块；模拟全新 python -m 进程的模块状态。
        monkeypatch.delitem(sys.modules, "server.__main__", raising=False)
        runpy.run_module("server", run_name="__main__")

    assert len(calls) == 1
    application, options = calls[0]
    module, name = application.split(":")
    assert getattr(import_module(module), name) is app
    assert options == {"host": "127.0.0.1", "port": 8000}
