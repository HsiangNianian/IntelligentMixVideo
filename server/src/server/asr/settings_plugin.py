"""ASR 公开设置入口：只生成密钥字段描述，不读取配置值或加载转写业务。"""

from .settings import ASRSettings


# 模块 ID 与本地配置关联；业务运行时配置仍由 ASR 调用入口管理。
SETTINGS_PLUGIN = {"id": "asr", "name": "语音识别 ASR", "schema": ASRSettings.model_json_schema(), "settings": ASRSettings}
