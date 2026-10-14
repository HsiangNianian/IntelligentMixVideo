"""回归验证命令入口在存在未关闭 SSE 时仍能限时退出并释放字效运行锁。

测试在子进程中启动真实入口，避免 Uvicorn 的信号处理污染 pytest 主进程；仅在非 Windows
平台执行，因为当前字效运行锁和 SIGTERM 关闭路径使用 POSIX 文件锁及进程信号。
在 server/ 执行 `uv run --locked pytest -v tests/test_graceful_shutdown.py`。
"""

from __future__ import annotations

import http.client
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="字效运行锁与 SIGTERM 关闭回归测试使用 POSIX flock 和信号。",
)


def _free_port() -> int:
    """返回一个当前可绑定到回环地址的临时端口。"""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _wait_ready(process: subprocess.Popen[str], port: int, timeout: float) -> None:
    """等待子进程 API 可访问；提前退出或超时都报告实际进程状态。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise AssertionError(
                f"服务在就绪前退出（code={process.returncode}）\nstdout={stdout}\nstderr={stderr}"
            )
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
            connection.request("GET", "/api/templates/capabilities")
            response = connection.getresponse()
            response.read()
            if response.status == 200:
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError("服务未在超时前就绪")


def test_open_sse_shutdown_releases_remotion_lock(tmp_path: Path) -> None:
    """持续 SSE 阻止正常收尾时，入口仍须限时退出且让下一次启动重新获取运行锁。"""
    # 子进程只替换数据库与合成调度器生命周期，保留真实 Uvicorn、SSE 和 Remotion 锁路径。
    runner = r'''
import asyncio
from fastapi.responses import StreamingResponse
import server.app as app_module
import server.__main__ as entry
import uvicorn

app_module.initialize_database = lambda: None
app_module.close_database = lambda: None
app_module.Runtime.start = lambda self: asyncio.sleep(0)
app_module.Runtime.close = lambda self: asyncio.sleep(0)

@app_module.app.get("/_probe/stream")
async def probe_stream():
    async def body():
        yield "retry: 1000\n\n"
        await asyncio.Event().wait()
    return StreamingResponse(body(), media_type="text/event-stream")

real_run = uvicorn.run
def run_with_short_timeout(application, **options):
    if options.get("timeout_graceful_shutdown") != 10:
        raise RuntimeError("graceful shutdown timeout was not configured")
    options["timeout_graceful_shutdown"] = 0.2
    real_run(application, **options)

uvicorn.run = run_with_short_timeout
entry.main()
'''
    port = _free_port()
    data_dir = tmp_path / "remotion-data"
    environment = os.environ.copy()
    environment.update({
        "IMV_DATA_DIR": str(data_dir),
        "PORT": str(port),
        "PYTHONUNBUFFERED": "1",
    })
    process = subprocess.Popen(
        [sys.executable, "-c", runner],
        cwd=Path(__file__).parents[1],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_ready(process, port, timeout=20)
        stream = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        stream.request("GET", "/_probe/stream")
        response = stream.getresponse()
        assert response.status == 200
        assert response.readline()

        process.send_signal(signal.SIGTERM)
        process.wait(timeout=5)
        assert process.returncode is not None

        import fcntl

        with (data_dir / "runtime.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock, fcntl.LOCK_UN)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
