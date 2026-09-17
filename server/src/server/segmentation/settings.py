"""切片客户端配置契约与插件描述；服务端 Settings 继续读取 IMV_ 配置并保留 HTTP 策略。"""

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import SettingsConfigDict

from ..config_base import CommonSettings


class ClientSettings(BaseModel):
    """一次请求独立的模型连接参数；必填凭据不回退到服务端，拒绝策略字段。"""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    llm_base_url: str = Field(title="模型 API 地址", pattern=r"\S")
    llm_api_key: str = Field(
        title="API Key", pattern=r"\S", repr=False,
        json_schema_extra={"format": "password", "writeOnly": True},
    )
    llm_model: str = Field(title="模型名称", pattern=r"\S")
    llm_timeout_seconds: float = Field(title="请求超时（秒）", default=120, gt=0, allow_inf_nan=False)
    llm_max_retries: int = Field(title="重试次数", default=1, ge=0, le=3)


class Settings(ClientSettings, CommonSettings):
    """构造参数优先于 IMV_ 环境与文件；HTTP 策略始终由服务端管理。"""

    model_config = SettingsConfigDict(env_prefix="IMV_", extra="ignore")

    allow_insecure_llm_http: bool = True


# 只导出类型与代码默认值，不实例化 Settings 或读取后端配置值。
SETTINGS_PLUGIN = {"id": "segmentation", "name": "文案切片", "schema": ClientSettings.model_json_schema()}
