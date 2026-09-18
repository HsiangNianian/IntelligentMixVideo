"""上海 IMS 官方 SDK 客户端设置入口；两种模式均展示，只生成字段描述。"""

from .settings import ClientSettings


# 字段只维护在模块配置模型中，普通模式也允许客户端使用自己的凭据。
SETTINGS_PLUGIN = {"id": "ims", "name": "上海 IMS 官方 SDK", "scope": "client", "schema": ClientSettings.model_json_schema(by_alias=False)}
