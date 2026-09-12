from functools import lru_cache
from urllib.parse import urlparse

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """由环境变量驱动的应用配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="IMV_",
        extra="ignore",
        case_sensitive=False,
    )

    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    allow_insecure_llm_http: bool = False
    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = Field(default=1, ge=0, le=3)

    segment_min_duration_ms: int = Field(default=1200, ge=200, le=10000)
    segment_max_duration_ms: int = Field(default=6000, ge=1000, le=30000)
    segment_max_keywords: int = Field(default=5, ge=0, le=20)
    segment_keyword_max_length: int = Field(default=12, ge=2, le=30)

    segment_max_alignment_cells: int = Field(default=4_000_000, gt=0)
    segment_max_asr_chars: int = Field(default=20_000, gt=0)
    segment_max_asr_words: int = Field(default=20_000, gt=0)

    @model_validator(mode="after")
    def validate_segment_durations(self) -> "Settings":
        """校验片段时长区间。

        作用与效果：拒绝上下限倒置的配置，避免运行期出现无法满足的约束。
        输入：当前已完成字段解析的配置实例。
        输出：校验后的同一配置实例；区间非法时抛出 `ValueError`。
        """
        if self.segment_min_duration_ms >= self.segment_max_duration_ms:
            raise ValueError("IMV_SEGMENT_MIN_DURATION_MS 必须小于 IMV_SEGMENT_MAX_DURATION_MS")
        return self

    @model_validator(mode="after")
    def validate_llm_transport(self) -> "Settings":
        """校验模型服务地址及明文传输授权。

        作用与效果：拒绝无效地址，并要求远程 HTTP 服务显式开启不安全传输开关。
        输入：当前已完成字段解析的配置实例。
        输出：校验后的同一配置实例；传输配置不安全或无效时抛出 `ValueError`。
        """
        if not self.llm_base_url:
            return self

        parsed = urlparse(self.llm_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("IMV_LLM_BASE_URL 必须是有效的 HTTP(S) 地址")

        local_hosts = {"127.0.0.1", "localhost", "::1"}
        is_remote_http = parsed.scheme == "http" and parsed.hostname not in local_hosts
        if is_remote_http and not self.allow_insecure_llm_http:
            raise ValueError("远程 HTTP 模型地址需要显式设置 IMV_ALLOW_INSECURE_LLM_HTTP=true")
        return self


@lru_cache
def get_settings() -> Settings:
    """加载并缓存应用配置。

    作用与效果：首次调用从环境和 `.env` 构建配置，后续调用复用同一实例。
    输入：无。
    输出：经过完整校验的 `Settings` 实例。
    """
    return Settings()
