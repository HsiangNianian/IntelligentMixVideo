"""Remotion 字效配置：读取服务端模型与执行参数，保持数据目录和渲染资源路径稳定。"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import SettingsConfigDict

from ..config_base import CommonSettings


class ClientSettings(BaseModel):
    """客户端可携带的模型参数；运行目录、渲染及任务资源限制仍由服务端维护。"""

    model_config = ConfigDict(hide_input_in_errors=True)

    actor_base_url: str = Field(default="https://api.openai.com/v1", title="Actor API 地址")
    actor_model: str = Field(default="", title="Actor 模型")
    actor_api_key: SecretStr = Field(default=SecretStr(""), title="Actor API Key")
    vision_base_url: str = Field(default="", title="视觉 API 地址（留空复用 Actor）")
    vision_model: str = Field(default="", title="视觉模型")
    vision_api_key: SecretStr = Field(default=SecretStr(""), title="视觉 API Key（留空复用 Actor）")
    disable_thinking: bool = Field(default=False, title="关闭思考")
    model_timeout_seconds: int = Field(default=240, ge=1, le=600, title="模型请求超时（秒）")


class Settings(ClientSettings, CommonSettings):
    """服务端策略与模型默认值；客户端覆盖只生成任务独立副本。"""

    model_config = SettingsConfigDict(env_prefix="IMV_", extra="ignore")

    # 保留服务端显式 None 的兼容性；客户端用空字符串表达复用 Actor。
    vision_base_url: str | None = None
    vision_api_key: SecretStr | None = None
    data_dir: Path = Path(".data")
    job_timeout_seconds: int = Field(default=600, ge=1, le=3600)
    render_timeout_seconds: int = Field(default=180, ge=1, le=600)
    # Keep usage accounting while temporarily disabling cumulative model quota enforcement.
    enforce_model_budget: bool = False
    max_model_calls: int = Field(default=32, ge=1, le=50)
    max_tokens: int = Field(default=200_000, ge=1000, le=1_000_000)
    max_output_tokens: int = Field(default=32_000, ge=1, le=1_000_000)
    max_review_retries: int = Field(default=2, ge=0, le=5)
    max_evidence_retries: int = Field(default=1, ge=0, le=3)
    max_no_progress_turns: int = Field(default=4, ge=2, le=20)
    max_judge_calls: int = Field(default=12, ge=1, le=50)
    max_judge_tokens: int = Field(default=60_000, ge=1000, le=1_000_000)
    max_actor_calls: int = Field(default=24, ge=1, le=50)
    max_actor_tokens: int = Field(default=160_000, ge=1000, le=1_000_000)
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    max_image_pixels: int = Field(default=20_000_000, gt=0)
    renderer_dir: Path = Path(__file__).parent.parent / "remotion"
    runtime_lib_dir: Path | None = None
    browser_executable: Path = Path("/opt/google/chrome/chrome")
    font_regular: Path = Path("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc")
    font_bold: Path = Path("/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc")

    @property
    def models_configured(self) -> bool:
        """Expose readiness without leaking either credential."""
        return bool(
            self.actor_model
            and self.actor_api_key.get_secret_value()
            and self.vision_model
            and (self.vision_api_key or self.actor_api_key).get_secret_value()
        )


def load_settings() -> Settings:
    """Read server/.env and save relative data paths directly beneath the template module."""
    settings = Settings()
    if not settings.data_dir.is_absolute():
        settings.data_dir = Path(__file__).parent / settings.data_dir
    return settings
