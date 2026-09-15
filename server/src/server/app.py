"""组装模板、切片与示例 API；启动检查数据库，统一校验与数据库错误，退出释放连接。"""

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
from .sub_api.router import router
from .template.router import router as template_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动前检查并创建缺失数据库；初始化失败或正常退出时都释放连接池。"""
    try:
        initialize_database()
        yield
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
    allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type"],
)
app.include_router(router)
app.include_router(template_router)
app.include_router(segmentation_router)


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """隐藏连接凭据和 SQL 细节；失败事务已回滚，客户端可保留草稿并重试。"""
    return JSONResponse(status_code=503, content={
        "detail": "数据库操作失败，请检查 MySQL 服务、数据库及 server/.env 配置后重试",
    })


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """保留校验错误结构，将回显输入中的非有限数转换为文字，避免 JSON 编码再次失败。"""
    errors = jsonable_encoder(
        exc.errors(), custom_encoder={float: lambda value: value if isfinite(value) else str(value)},
    )
    return JSONResponse(status_code=422, content={"detail": errors})


@app.get("/")
def root() -> dict[str, str]:
    """返回首页消息，供本地启动后确认应用可访问。"""
    return {"msg": "首页"}
