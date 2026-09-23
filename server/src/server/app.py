"""组装模板库、切片、异步合成与示例 API，挂载独立 Remotion 服务。

退出先收束合成任务，再释放数据库连接；保留主应用校验和数据库错误处理。
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

from .database import close_database, initialize_database
from .segmentation.router import router as segmentation_router
from .settings_plugins import router as settings_router
from .sub_api.router import router
from .template.router import router as template_router
from .remotion_templates.api import app as remotion_templates_app
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
        finally:
            close_database()


# 允许本地 Vite 与 Tauri 客户端访问 API；Windows 正式客户端使用动态 localhost 端口。
# 文档版本直接读取已安装的服务端包元数据，与发布清单保持一致。
app = FastAPI(title="IntelligentMixVideo API", version=version("imv-server"), lifespan=lifespan)
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
    allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type", "Last-Event-ID", "X-Remotion-Config", "X-IMS-Config"],
)
app.include_router(router)
app.include_router(template_router)
app.include_router(segmentation_router)
app.include_router(settings_router)
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
    if request.method == "POST" and request.url.path.removeprefix(request.scope.get("root_path", "")) == composition_router.prefix:
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
