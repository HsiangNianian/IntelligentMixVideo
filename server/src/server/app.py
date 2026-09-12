"""组装 API；启动时确保 MySQL 数据库存在，运行中数据库失败返回 503，退出时释放连接。"""

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from .database import close_database, initialize_database
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


# 仅允许本地前端地址跨域调用；Vite 开发环境通常通过同源代理访问。
app = FastAPI(title="IntelligentMixVideo API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:1420", "http://localhost:4173"],
    allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type"],
)
app.include_router(router)
app.include_router(template_router)


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """隐藏连接凭据和 SQL 细节；失败事务已回滚，客户端可保留草稿并重试。"""
    return JSONResponse(status_code=503, content={
        "detail": "数据库操作失败，请检查 MySQL 服务、数据库及 server/.env 配置后重试",
    })


@app.get("/")
def root() -> dict[str, str]:
    """返回首页消息，供本地启动后确认应用可访问。"""
    return {"msg": "首页"}
