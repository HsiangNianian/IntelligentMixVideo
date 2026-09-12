from fastapi import APIRouter

router = APIRouter(
    prefix="/users",       # 本路由所有接口统一前缀
    tags=["用户管理"],      # 接口文档分组标签
)

@router.get("/")
def get_users() -> dict[str, str]:
    return {"msg": "用户列表"}

@router.get("/{user_id}")
def get_user(user_id: int) -> dict[str, int]:
    return {"user_id": user_id}
