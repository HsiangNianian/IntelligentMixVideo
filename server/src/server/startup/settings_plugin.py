"""Debug 启动配置入口；仅声明端口描述，保存值由客户端内置服务启动时加载。"""

from .settings import ServerSettings


SETTINGS_PLUGIN = {"id": "startup", "name": "服务启动", "schema": ServerSettings.model_json_schema(), "settings": ServerSettings}
