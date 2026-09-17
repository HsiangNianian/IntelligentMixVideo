"""验证桌面启动配置与进程清理；IMV_TEST_BUNDLE 指向解包运行时后验证真实 MySQL/API/渲染。

常规：uv run --locked --project server pytest server/tests/test_desktop.py。
真实用例只使用临时用户目录，不连接外部 MySQL 或模型服务。
"""

import json
import asyncio
from io import StringIO
import os
from pathlib import Path
import queue
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from server import database, desktop
from server.remotion_templates.renderer import Renderer
from server.settings import Settings

BUNDLE = os.environ.get("IMV_TEST_BUNDLE")
APPDIR = os.environ.get("IMV_TEST_APPDIR")
DESKTOP = os.environ.get("IMV_TEST_DESKTOP_EXECUTABLE")
PYTHON = "python/python.exe" if sys.platform == "win32" else "python/bin/python3"


def test_desktop_config_preserves_credentials_and_uses_private_paths(tmp_path, monkeypatch):
    """用户配置保留，工具和数据库只能指向随包资源与私有数据目录。"""
    runtime, data = tmp_path / "runtime", tmp_path / "data"
    runtime.mkdir()
    data.mkdir()
    (runtime / ".env.example").write_text("IMV_ACTOR_API_KEY=\n")
    (data / ".env").write_text("IMV_ACTOR_API_KEY=user-config\nDB_HOST=remote.test\n")
    # configure 会变更进程环境；本例由 monkeypatch 在结束后恢复全部环境。
    for key in ["PATH", "DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_SOCKET",
                "IMV_ACTOR_API_KEY", "IMV_DATA_DIR", "IMV_RENDERER_DIR", "IMV_BROWSER_EXECUTABLE",
                "IMV_FONT_REGULAR", "IMV_FONT_BOLD", "IMV_RUNTIME_LIB_DIR"]:
        monkeypatch.setenv(key, os.environ.get(key, ""))
        if key == "IMV_ACTOR_API_KEY":
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    desktop.configure(runtime, data, tmp_path / "mysql.sock")
    assert os.environ["IMV_ACTOR_API_KEY"] == "user-config"
    assert os.environ["DB_HOST"] == "localhost"
    assert os.environ["IMV_DATA_DIR"] == str(data / "remotion")
    assert (data / ".env").read_text().startswith("IMV_ACTOR_API_KEY=user-config")
    # 上游统一配置基类后，实际设置对象仍以桌面加载的用户配置和私有路径为准。
    settings = Settings()
    assert settings.actor_api_key.get_secret_value() == "user-config"
    assert settings.data_dir == data / "remotion"
    assert database.DatabaseSettings().socket == str(tmp_path / "mysql.sock")


def test_socket_survives_database_bootstrap(monkeypatch, tmp_path):
    """带 socket 的连接 URL 在去掉库名后仍走同一私有 MySQL，不回退宿主 3306。"""
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setenv("DB_SOCKET", str(tmp_path / "mysql.sock"))
    settings_type = database.DatabaseSettings
    monkeypatch.setattr(database, "DatabaseSettings", lambda: settings_type(_env_file=None))
    engine = database.get_engine()
    try:
        assert engine.url._replace(database=None).query["unix_socket"] == str(tmp_path / "mysql.sock")
    finally:
        database.close_database()




def test_failed_mysql_and_child_cleanup():
    """数据库提前退出立即报错；清理函数回收仍运行的子进程。"""
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    desktop.stop(process)
    assert process.poll() is not None
    with pytest.raises(RuntimeError, match="MySQL"):
        desktop.wait_mysql(process, Path("/nonexistent/mysql.sock"), threading.Event())


def test_readiness_protocol_excludes_http_logs(monkeypatch):
    """真实 Uvicorn 访问日志只能进入诊断流，桌面读取的首行必须是就绪 JSON。"""
    application = FastAPI()

    @application.get("/{path:path}")
    def ready(path: str):
        """仅替换数据库业务边界，保留真实 HTTP、Uvicorn 和就绪握手。"""
        return {"path": path}

    config_type = desktop.uvicorn.Config
    monkeypatch.setattr(desktop.uvicorn, "Config", lambda *args, **kwargs: config_type(application, use_colors=False))
    protocol, diagnostics = StringIO(), StringIO()
    monkeypatch.setattr(sys, "stdout", protocol)
    monkeypatch.setattr(sys, "stderr", diagnostics)

    async def scenario():
        """等回执后模拟父进程关闭，所有监听与任务均有界清理。"""
        stopped = threading.Event()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
            listener.setblocking(False)
            task = asyncio.create_task(desktop.serve(listener, SimpleNamespace(poll=lambda: None), stopped))
            try:
                async with asyncio.timeout(10):
                    while not protocol.getvalue():
                        if task.done():
                            await task
                        await asyncio.sleep(0.01)
            finally:
                stopped.set()
                await asyncio.wait_for(task, timeout=10)

    asyncio.run(scenario())
    assert json.loads(protocol.getvalue())["url"].startswith("http://127.0.0.1:")
    assert "GET /template HTTP/1.1" in diagnostics.getvalue()














def test_exclusive_file_lock_releases_after_close(tmp_path):
    """真实跨进程验证争锁失败；持有者关闭句柄后另一进程可以取得同一把锁。"""
    from server.file_lock import lock_exclusive

    script = "from server.file_lock import lock_exclusive; import sys; f=open(sys.argv[1], 'a'); lock_exclusive(f)"
    path = tmp_path / "state.lock"
    with path.open("a") as lock:
        lock_exclusive(lock)
        conflict = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True, timeout=10)
        assert conflict.returncode != 0
    released = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True, timeout=10)
    assert released.returncode == 0, released.stderr


def test_windows_mysql_uses_password_and_loopback(tmp_path, monkeypatch):
    """Windows 仅绑定回环且不使用默认端口/空密码；初始化密码不出现在 mysqld 参数中。"""
    monkeypatch.setattr(desktop, "WINDOWS", True)
    monkeypatch.setenv("DB_PORT", "23456")
    monkeypatch.setenv("DB_PASSWORD", "private-secret")
    command = desktop.mysql_command(tmp_path, tmp_path / "data", tmp_path / "mysql.sock")
    assert command[0].endswith("mysqld.exe")
    assert "--bind-address=127.0.0.1" in command
    assert "--port=23456" in command
    assert not any("private-secret" in arg for arg in command)






            # 父进程退出后 Python 异步关闭数据库；这里不删除正在使用的数据。
