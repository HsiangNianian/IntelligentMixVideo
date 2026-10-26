"""组装模板库、切片、异步合成与示例 API，挂载独立 Remotion 服务。

退出先收束合成任务，再释放数据库连接；保留主应用校验和数据库错误处理。未处理的异常在 CORS 之内转成 500，
跨域客户端看到的是服务端错误，而不是被浏览器拦截后的「无法连接」。
"""

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from importlib.metadata import version
from math import isfinite

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .database import close_database, initialize_database
from .segmentation.router import router as segmentation_router
from .settings_plugins import router as settings_router
from .sub_api.router import router
from .template.router import router as template_router
from .remotion_templates.api import app as remotion_templates_app
from .remotion_templates.overlay_router import router as overlay_router
from .remotion_templates.sprite_router import router as sprite_router
from .video_composition.router import router as composition_router
from .video_composition.service import Runtime


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """初始化数据库后恢复合成任务；启动失败和退出均先收束后台工作再关闭连接池。"""
    runtime = Runtime()
    app.state.video_composition = runtime
    try:
        initialize_database()
        await runtime.start()
        yield
    finally:
        try:
            await runtime.close()
            if (remotion := getattr(remotion_templates_app.state, "runtime", None)) is not None:
                await remotion.close()
        finally:
            close_database()


# 未处理的异常要经过 CORS 才能被跨域客户端读到；具体行为见类文档。
class InternalErrors:
    """在 CORS 之内把未处理的异常转成 500 JSON，再照常抛出，由外层与 Uvicorn 记录堆栈。

    Starlette 的 500 由最外层的 ServerErrorMiddleware 生成，不经过 CORS，跨域的浏览器与 Tauri 客户端拿不到
    响应、只能报「无法连接服务端」，掩盖了服务端其实已经处理（例如资产已删除）的事实。响应已开始时（流式响应
    中途失败、挂载的 Remotion 应用已自行回答 500）不再写入。
    """

    def __init__(self, app: ASGIApp) -> None:
        """包住内层应用。"""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """记录响应是否已开始；失败且尚未回答时先回答 500，再把异常交给外层。"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracked(message: Message) -> None:
            """转发消息并记下响应头是否已发出。"""
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, receive, tracked)
        except Exception:
            if not started:
                await JSONResponse(status_code=500, content={"detail": "服务端内部错误，请稍后重试"})(scope, receive, send)
            raise


# 文档版本直接读取已安装的服务端包元数据，与发布清单保持一致。
app = FastAPI(title="IntelligentMixVideo API", version=version("imv-server"), lifespan=lifespan)
# 先加入的中间件在内层：InternalErrors 位于 CORS 之内，500 也带跨域响应头。
app.add_middleware(InternalErrors)
# 允许本地 Vite 与 Tauri 客户端访问 API；Windows 正式客户端使用动态 localhost 端口。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:1420", "http://localhost:4173",
        "tauri://localhost", "http://tauri.localhost",
    ],
    # 动态来源仅接受系统可分配的十进制端口 1～65535。
    allow_origin_regex=(
        r"^http://localhost:(?:[1-9][0-9]{0,3}|[1-5][0-9]{4}|"
        r"6[0-4][0-9]{3}|65[0-4][0-9]{2}|655[0-2][0-9]|6553[0-5])$"
    ),
    allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type", "Last-Event-ID", "X-Remotion-Config", "X-IMS-Config", "X-IMV-Device"],
)
app.include_router(router)
app.include_router(template_router)
app.include_router(segmentation_router)
app.include_router(settings_router)
app.include_router(sprite_router)
app.include_router(overlay_router)
app.mount("/api/templates", remotion_templates_app)
app.include_router(composition_router)


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """隐藏连接凭据和 SQL 细节；失败事务已回滚，客户端可保留草稿并重试。"""
    return JSONResponse(status_code=503, content={
        "detail": "数据库操作失败，请检查 MySQL 服务、数据库及 server/.env 配置后重试",
    })


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """保留校验错误结构，将回显输入中的非有限数转换为文字，避免 JSON 编码再次失败。"""
    if request.method == "POST" and request.url.path.removeprefix(request.scope.get("root_path", "")) in (composition_router.prefix, f"{composition_router.prefix}/playground"):
        return JSONResponse(status_code=422, content={"code": 422, "message": "请求参数无效", "data": None})
    errors = jsonable_encoder(
        exc.errors(), custom_encoder={float: lambda value: value if isfinite(value) else str(value)},
    )
    # 切片请求可携带客户端密钥；缺少顶层字段时 Pydantic input 也可能包含完整请求。
    if request.url.path == "/segmentations":
        errors = [{"loc": error["loc"], "type": error["type"], "msg": error["msg"]} for error in errors]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.get("/", tags=["首页"])
def root() -> dict[str, str]:
    """返回首页消息，供本地启动后确认应用可访问。"""
    return {"msg": "首页"}
