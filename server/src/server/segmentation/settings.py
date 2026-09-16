"""切片模型连接配置：按环境变量、固定的 server/.env、默认值的优先级读取并校验，不缓存。"""

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from ..config_base import CommonSettings


class Settings(CommonSettings):
    """自动解析 IMV_ 配置；忽略 .env 中无关字段，实例化时校验类型与范围。"""

    model_config = SettingsConfigDict(env_prefix="IMV_")

    llm_base_url: str = Field(pattern=r"\S")
    llm_api_key: str = Field(pattern=r"\S", repr=False)
    llm_model: str = Field(pattern=r"\S")
    llm_timeout_seconds: float = Field(default=120, gt=0, allow_inf_nan=False)
    llm_max_retries: int = Field(default=1, ge=0, le=3)
    allow_insecure_llm_http: bool = True
