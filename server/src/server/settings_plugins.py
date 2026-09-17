"""设置插件目录：显式注册模块描述，客户端获取后自行渲染并在本地保存值。"""

from fastapi import APIRouter

from .segmentation.settings import SETTINGS_PLUGIN


# 新模块在此注册自己的描述；移除注册不会删除客户端已保存的配置。
plugins = {SETTINGS_PLUGIN["id"]: SETTINGS_PLUGIN}
router = APIRouter(prefix="/api/settings", tags=["客户端设置"])


@router.get("/plugins")
def list_plugins() -> list[dict]:
    """只返回已注册的公开字段描述，不提供服务端配置读写。"""
    return list(plugins.values())
