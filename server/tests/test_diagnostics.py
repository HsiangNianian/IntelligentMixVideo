"""请求诊断回归：编号、CORS、异常、并发与流；隔离外部服务，根目录执行 uv run --locked --project server pytest server/tests/test_diagnostics.py。"""

import asyncio
import logging
import re
from urllib.parse import quote

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from server.app import InternalErrors
from server.diagnostics import DiagnosticRequests


@pytest.fixture
def diagnostic_client():
    """用生产中间件顺序覆盖正常、受控错误、未处理异常和挂载应用。"""
    app = FastAPI()

    @app.get("/ok")
    def ok():
        """正常响应不改变业务 JSON。"""
        return {"ok": True}

    @app.get("/conflict")
    def conflict():
        """受控业务错误保留原说明和状态码。"""
        raise HTTPException(409, "名称冲突")

    @app.get("/broken")
    def broken():
        """敏感异常字符串留在原异常链，不写进新的摘要或响应头。"""
        raise RuntimeError("secret=private-server-key")

    child = FastAPI()
    child.add_api_route("/broken", broken, methods=["GET"])
    app.mount("/mounted", child)
    app.add_middleware(InternalErrors)
    app.add_middleware(DiagnosticRequests)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:1420"],
                       allow_headers=["X-IMV-Request-ID"],
                       expose_headers=["X-IMV-Request-ID", "X-IMV-Elapsed-Ms", "X-IMV-Diagnostics"])
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.mark.parametrize("path,status", [("/ok", 200), ("/conflict", 409), ("/broken", 500), ("/mounted/broken", 500)])
def test_request_outcome_has_correlation(diagnostic_client, caplog, path, status):
    """所有响应保留编号、处理耗时与 CORS；未处理异常同样能从客户端编号定位。"""
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    request_id = "0123456789abcdef"
    response = diagnostic_client.get(path + "?token=private-query", headers={
        "Origin": "http://localhost:1420", "X-IMV-Request-ID": request_id,
        "X-Remotion-Config": "private-config",
    })
    assert response.status_code == status
    assert response.headers["x-imv-request-id"] == request_id
    assert float(response.headers["x-imv-elapsed-ms"]) >= 0
    assert response.headers["access-control-allow-origin"] == "http://localhost:1420"
    assert "X-IMV-Request-ID" in response.headers["access-control-expose-headers"]
    assert f"[#{request_id}] 后端接收 GET {path}" in caplog.text
    assert f"HTTP {status}" in caplog.text
    assert "private-" not in caplog.text
    if path.endswith("broken"):
        assert "后端异常 RuntimeError" in caplog.text
    elif path == "/ok":
        assert response.json() == {"ok": True}


@pytest.mark.parametrize("request_id", ["", "bad\nlog", "a" * 200])
def test_untraced_requests_only_advertise_support(diagnostic_client, caplog, request_id):
    """不支持/非法编号不回显、不产生详细日志；无编号的旧客户端照常使用响应。"""
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    response = diagnostic_client.get("/ok", headers={"X-IMV-Request-ID": request_id})
    assert response.status_code == 200
    assert response.headers["x-imv-diagnostics"] == "1"
    assert "x-imv-request-id" not in response.headers
    assert not caplog.records


def test_production_cors_accepts_diagnostic_header(client):
    """检查实际主应用的 CORS 配置，不能只在测试应用中允许关联头。"""
    response = client.options("/template", headers={
        "Origin": "http://localhost:1420", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type,X-IMV-Request-ID",
    })
    assert response.status_code == 200
    response = client.get("/users/abc", headers={"Origin": "http://localhost:1420", "X-IMV-Request-ID": "fedcba9876543210"})
    assert response.status_code == 422
    assert response.headers["x-imv-request-id"] == "fedcba9876543210"


@pytest.mark.parametrize("path", [
    "/missing\nforged\rrecord",
    "/missing\x1b[2J\x1b[H",
    "/missing" + "".join(chr(value) for value in [*range(32), *range(127, 160)]),
])
def test_unmatched_path_cannot_inject_log_lines(diagnostic_client, caplog, path):
    """接收与 404 回退路径都转义 C0/C1 控制字符，不能控制终端，也不能超出长度上限。"""
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    response = diagnostic_client.get(quote(path) + "x" * 500, headers={
        "X-IMV-Request-ID": "0123456789abcdef",
    })
    assert response.status_code == 404
    records = [record for record in caplog.records if "后端接收" in record.getMessage() or "后端响应 /" in record.getMessage()]
    assert len(records) == 2
    for record in records:
        message = record.getMessage()
        assert not re.search(r"[\x00-\x1f\x7f-\x9f]", message)
        assert "x" * 301 not in message


def test_concurrent_streams_keep_ids_and_do_not_buffer(caplog):
    """并发流式请求保持各自编号；首块立即发送，流结束前不能记录发送结束。"""
    caplog.set_level(logging.INFO, logger="uvicorn.error")

    async def run():
        """用有界事件交错两个 ASGI 请求，不依赖真实端口或计时睡眠。"""
        gate = asyncio.Event()
        first_chunks = [asyncio.Event(), asyncio.Event()]
        outputs = [[], []]

        async def stream_app(scope, receive, send):
            """发出首块后等待，证明诊断中间件不会读完整流才返回。"""
            await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/event-stream")]})
            await send({"type": "http.response.body", "body": b": ping\n\n", "more_body": True})
            await gate.wait()
            await send({"type": "http.response.body", "body": b"done", "more_body": False})

        async def receive():
            """本场景不读取请求体。"""
            return {"type": "http.request", "body": b""}

        async def send(index, message):
            """记录各流原始消息并通知首块已发送。"""
            outputs[index].append(message)
            if message["type"] == "http.response.body":
                first_chunks[index].set()

        app = DiagnosticRequests(stream_app)
        tasks = [asyncio.create_task(app({
            "type": "http", "path": "/stream", "method": "GET",
            "headers": [(b"x-imv-request-id", str(index + 1).encode() * 16)],
        }, receive, lambda message, index=index: send(index, message))) for index in range(2)]
        try:
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in first_chunks)), timeout=1)
            assert "发送结束" not in caplog.text
            for index in range(2):
                assert dict(outputs[index][0]["headers"])[b"x-imv-request-id"] == str(index + 1).encode() * 16
                assert outputs[index][1]["body"] == b": ping\n\n"
        finally:
            gate.set()
            await asyncio.gather(*tasks)
        assert caplog.text.count("发送结束") == 2

    asyncio.run(run())
