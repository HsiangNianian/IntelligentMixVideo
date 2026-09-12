"""切片配置：按环境变量、当前目录 .env、默认值的优先级读取并校验，不缓存。"""

from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """自动解析 IMV_ 配置；忽略 .env 中无关字段，实例化时校验类型与范围。"""

    model_config = SettingsConfigDict(
        env_prefix="IMV_", env_file=".env", env_file_encoding="utf-8", extra="ignore",
        hide_input_in_errors=True,
    )

    llm_base_url: str = Field(pattern=r"\S")
    llm_api_key: str = Field(pattern=r"\S", repr=False)
    llm_model: str = Field(pattern=r"\S")
    llm_timeout_seconds: float = Field(default=120, gt=0, allow_inf_nan=False)
    llm_max_retries: int = Field(default=1, ge=0, le=3)
    allow_insecure_llm_http: bool = False
    segment_min_duration_ms: int = Field(default=1200, ge=200)
    segment_max_duration_ms: int = Field(default=6000, le=30000)
    segment_max_keywords: int = Field(default=5, ge=0, le=20)
    segment_keyword_max_length: int = Field(default=12, ge=2, le=30)
    segment_max_alignment_work: int = Field(default=250000, gt=0)

    @model_validator(mode="after")
    def validate_duration_range(self) -> Self:
        """最小时长须严格小于最大时长，避免不可用的切片约束。"""
        if self.segment_min_duration_ms >= self.segment_max_duration_ms:
            raise ValueError("最小片段时长必须小于最大时长。")
        return self
