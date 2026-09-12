"""组装 FastAPI 应用和用户路由，并提供首页示例响应。"""

from fastapi import FastAPI
from .sub_api.router import router

# 共享 ASGI 应用由 Uvicorn 和测试客户端加载，路由仅注册一次。
app = FastAPI(title="IntelligentMixVideo API")

app.include_router(router)


@app.get("/")
def root() -> dict[str, str]:
    """返回首页消息，供本地启动后确认应用可访问。"""
    return {"msg": "首页"}
