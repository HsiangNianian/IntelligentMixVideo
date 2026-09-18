"""切片公开设置入口：从配置类生成描述，不实例化配置或读取服务端凭据。"""

from .settings import ClientSettings, Settings


# ID 是本地存储的稳定键；新增字段只需修改 ClientSettings。
SETTINGS_PLUGIN = {"id": "segmentation", "name": "文案切片", "schema": ClientSettings.model_json_schema(), "settings": Settings}
