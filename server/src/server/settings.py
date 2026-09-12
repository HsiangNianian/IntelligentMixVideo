"""Load server-owned model credentials and bounded local worker settings."""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """One local server instance; requests cannot override credentials or executables."""

    model_config = SettingsConfigDict(
        env_prefix="IMV_", env_file=".env", extra="ignore"
    )

    data_dir: Path = Path(".data")
    actor_base_url: str = "https://api.openai.com/v1"
    actor_model: str = ""
    actor_api_key: SecretStr = SecretStr("")
    vision_base_url: str | None = None
    vision_model: str = ""
    vision_api_key: SecretStr | None = None
    disable_thinking: bool = False
    model_timeout_seconds: int = Field(default=120, ge=1, le=600)
    job_timeout_seconds: int = Field(default=600, ge=1, le=3600)
    render_timeout_seconds: int = Field(default=180, ge=1, le=600)
    max_model_calls: int = Field(default=16, ge=1, le=50)
    max_tokens: int = Field(default=100_000, ge=1000, le=1_000_000)
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    max_image_pixels: int = Field(default=20_000_000, gt=0)
    renderer_dir: Path = Path(__file__).parent / "remotion"
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
    root = Path(__file__).resolve().parents[2]
    settings = Settings(_env_file=root / ".env")
    if not settings.data_dir.is_absolute():
        settings.data_dir = Path(__file__).parent / "remotion_templates" / settings.data_dir
    return settings
