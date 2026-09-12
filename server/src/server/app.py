from fastapi import FastAPI
from .sub_api.router import router

app = FastAPI(title="IntelligentMixVideo API")

app.include_router(router)


@app.get("/")
def root() -> dict[str, str]:
    return {"msg": "首页"}
