"""用户接口示例：按 /users 前缀组织路由，由 FastAPI 校验整数路径参数。"""

from fastapi import APIRouter

# 用户接口的注册对象集中维护前缀与文档分组。
router = APIRouter(
    prefix="/users",       # 本路由所有接口统一前缀
    tags=["用户管理"],      # 接口文档分组标签
)

@router.get("/")
def get_users() -> dict[str, str]:
    """返回列表占位消息，当前尚未接入用户存储。"""
    return {"msg": "用户列表"}

@router.get("/{user_id}")
def get_user(user_id: int) -> dict[str, int]:
    """回显已通过路径参数校验的用户 ID，不执行数据库查询。"""
    return {"user_id": user_id}
