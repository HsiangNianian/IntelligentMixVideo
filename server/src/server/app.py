"""组装 FastAPI 应用和用户路由，并增量挂载独立的模板 API。"""

from fastapi import FastAPI
from .sub_api.router import router
from .templates.api import app as templates_app

# 共享 ASGI 应用由 Uvicorn 和测试客户端加载，路由仅注册一次。
app = FastAPI(title="IntelligentMixVideo API")

app.include_router(router)
app.mount("/api/templates", templates_app)


@app.get("/")
def root() -> dict[str, str]:
    """返回首页消息，供本地启动后确认应用可访问。"""
    return {"msg": "首页"}
