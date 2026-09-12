"""组装 FastAPI 应用、用户与切片路由，并统一处理应用错误。"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .core.errors import AppError
from .sub_api.router import router
from .sub_api.segmentation.router import router as segmentation_router

# 共享 ASGI 应用由 Uvicorn 和测试客户端加载，路由仅注册一次。
app = FastAPI(title="IntelligentMixVideo API")

app.include_router(router)
app.include_router(segmentation_router)


@app.exception_handler(AppError)
async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    """把受控应用异常转换为统一错误响应。

    作用与效果：保留异常定义的状态码、业务码、详情与重试语义。
    输入：当前请求与捕获到的 `AppError`。
    输出：`{"error": {...}}` 结构的 JSON 响应。
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "retryable": exc.retryable,
            }
        },
    )


@app.get("/")
def root() -> dict[str, str]:
    """返回首页消息，供本地启动后确认应用可访问。"""
    return {"msg": "首页"}
