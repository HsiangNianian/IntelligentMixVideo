"""按桌面诊断请求编号记录 API 边界；不读取正文、配置头或流式内容，不改变业务结果。"""

import logging
import re
from time import perf_counter

from starlette.types import ASGIApp, Message, Receive, Scope, Send


logger = logging.getLogger("uvicorn.error")
REQUEST_HEADER = b"x-imv-request-id"


class DiagnosticRequests:
    """只跟踪带有效诊断编号的 HTTP 请求，响应头回传编号与处理耗时，便于客户端和服务日志对照。"""

    def __init__(self, app: ASGIApp) -> None:
        """保留内层应用；放在 CORS 以内、统一异常处理以外，覆盖主路由和挂载应用。"""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """每个请求独立持有编号；SSE 响应头与流结束分开记录，异常继续交由原处理链处理。"""
        raw_id = next((value for key, value in scope.get("headers", []) if key == REQUEST_HEADER), b"")
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if not re.fullmatch(rb"[0-9a-f]{16}", raw_id):
            async def advertise(message: Message) -> None:
                """旧客户端无须改变请求；新版客户端先读此能力头，再决定是否附加跨域诊断头。"""
                if message["type"] == "http.response.start":
                    message = {**message, "headers": [*message.get("headers", []), (b"x-imv-diagnostics", b"1")]}
                await send(message)
            await self.app(scope, receive, advertise)
            return
        request_id = raw_id.decode("ascii")
        started = perf_counter()
        status = None
        # 查询参数与请求正文可能包含密钥；日志只包含方法、路径与结果。
        label = f'{scope["method"]} {scope["path"][:300]}'.replace("\n", " ").replace("\r", " ")
        logger.info("[#%s] 后端接收 %s", request_id, label)

        async def tracked(message: Message) -> None:
            """响应开始时添加诊断头；逐块转发原消息，不缓存或消费 SSE/文件内容。"""
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                elapsed = f"{(perf_counter() - started) * 1000:.1f}"
                headers = [(key, value) for key, value in message.get("headers", [])
                           if key.lower() not in (REQUEST_HEADER, b"x-imv-elapsed-ms")]
                message = {**message, "headers": [
                    *headers, (REQUEST_HEADER, raw_id), (b"x-imv-elapsed-ms", elapsed.encode("ascii")),
                    (b"x-imv-diagnostics", b"1"),
                ]}
                route = " ".join(getattr(scope.get("route"), "path", scope["path"]).split())[:300]
                logger.log(logging.ERROR if status >= 500 else logging.WARNING if status >= 400 else logging.INFO,
                           "[#%s] 后端响应 %s HTTP %s (%s ms)", request_id, route, status, elapsed)
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body"):
                logger.info("[#%s] 后端响应发送结束 HTTP %s (%.1f ms)", request_id, status,
                            (perf_counter() - started) * 1000)

        try:
            await self.app(scope, receive, tracked)
        except Exception as error:
            # 不把异常字符串（可能含 SQL、令牌或输入）回传客户端；原服务日志保留原有堆栈。
            logger.error("[#%s] 后端异常 %s，HTTP %s；详细堆栈见同一服务日志",
                         request_id, type(error).__name__, status)
            raise
