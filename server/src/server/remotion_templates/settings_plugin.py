"""Remotion Agent 客户端设置入口；两种模式均展示，只生成字段描述。"""

from .settings import ClientSettings, Settings


# 字段只维护在模块配置模型中，普通模式也允许客户端使用自己的凭据。
SETTINGS_PLUGIN = {"id": "remotion_agent", "name": "Remotion Agent", "scope": "client", "schema": ClientSettings.model_json_schema(by_alias=False), "settings": Settings}

# 未配置的服务端仍可启动；客户端保存时必须提供实际使用的模型和密钥。
SETTINGS_PLUGIN["schema"]["required"] = ["actor_base_url", "actor_model", "actor_api_key", "vision_model"]
