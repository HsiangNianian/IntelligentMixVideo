"""Remotion 字效配置：读取服务端模型与执行参数，保持数据目录和渲染资源路径稳定。"""

import shutil
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import SettingsConfigDict

from ..config_base import CommonSettings



# 这些名称覆盖 CI 与常见发行版的安装位置；找不到时仍由 IMV_BROWSER_EXECUTABLE 覆盖。
BROWSER_CANDIDATES = (
    "chrome-headless-shell",
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "chrome",
)


def default_browser_executable() -> Path:
    """按固定候选顺序查找本机浏览器，未找到时返回第一个候选名交由调用方校验。"""
    for name in BROWSER_CANDIDATES:
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    return Path(BROWSER_CANDIDATES[0])


class ClientSettings(BaseModel):
    """客户端可携带的模型参数；运行目录、渲染及任务资源限制仍由服务端维护。"""

    model_config = ConfigDict(hide_input_in_errors=True)

    actor_base_url: str = Field(default="https://api.openai.com/v1", title="模型 API 地址")
    actor_model: str = Field(default="", title="模型")
    actor_api_key: SecretStr = Field(default=SecretStr(""), title="模型 API Key")
    vision_base_url: str = Field(default="", title="视觉 API 地址（留空复用主模型）")
    vision_model: str = Field(default="", title="视觉模型")
    vision_api_key: SecretStr = Field(default=SecretStr(""), title="视觉 API Key（留空复用主模型）")
    disable_thinking: bool = Field(
        default=False,
        title="关闭思考",
        description="仅在模型接口支持 thinking 扩展字段时开启；关闭此选项会省略该字段，采用上游默认行为。",
    )
    model_timeout_seconds: int = Field(default=240, ge=1, le=600, title="模型请求超时（秒）")


class Settings(ClientSettings, CommonSettings):
    """服务端策略与模型默认值；客户端覆盖只生成任务独立副本。"""

    model_config = SettingsConfigDict(env_prefix="IMV_", extra="ignore")

    # 保留服务端显式 None 的兼容性；客户端用空字符串表达复用主模型。
    vision_base_url: str | None = None
    vision_api_key: SecretStr | None = None
    # 本地数据目录只放任务草稿、已校验的读取缓存与私有审计；没有配置 ZOS 时也存放对象。
    data_dir: Path = Path(".data")
    # 保留期限（天）：客户端未确认送达的事件、私有审计日志、设备不活跃后的会话数据。
    undelivered_event_days: int = Field(default=7, ge=1, le=365)
    audit_retention_days: int = Field(default=14, ge=1, le=365)
    device_retention_days: int = Field(default=90, ge=1, le=3650)
    job_timeout_seconds: int = Field(default=600, ge=1, le=3600)
    render_timeout_seconds: int = Field(default=180, ge=1, le=600)
    # 整段透明视频默认最多渲染 30 分钟；不含等待名额、成片校验与上传，可由环境变量覆盖。
    overlay_max_seconds: int = Field(default=300, ge=1, le=600)
    overlay_render_timeout_seconds: int = Field(default=1800, ge=1, le=3600)
    # 每块的浏览器渲染页数，与同时执行的渲染任务数分别控制。
    overlay_frame_concurrency: int = Field(default=4, ge=1, le=64)
    overlay_task_concurrency: int = Field(default=6, ge=1, le=16)
    # 两条渲染路径共用固定 15 秒分块；不超过 15 秒只用一块。
    overlay_chunked: bool = True
    overlay_chunk_concurrency: int = Field(default=6, ge=1, le=6)
    # 全部视频共用名额，另预留宿主资源；内存估算用于准入，硬上限由部署 cgroup 负责。
    overlay_global_chunk_concurrency: int = Field(default=36, ge=1, le=64)
    render_cpu_reserve_percent: int = Field(default=25, ge=10, le=90)
    render_memory_reserve_percent: int = Field(default=25, ge=10, le=90)
    # 36 块 × 4 页；每页 1 GiB，加六视频各 1 GiB。与整机/cgroup 预留后的余量取更严者。
    render_max_cpus: int = Field(default=144, ge=1)
    render_max_memory_mb: int = Field(default=153600, ge=1024)
    overlay_chunk_memory_mb: int = Field(default=1024, ge=128, le=16384)
    overlay_resource_wait_seconds: float = Field(default=300, gt=0, le=1800, allow_inf_nan=False)
    overlay_vp9_cpu_used: int | None = Field(default=4, ge=0, le=8)
    overlay_vp9_threads: int | None = Field(default=2, ge=1, le=64)
    # 瞬时传输故障与上游限流/不可用会等待后重试；等待按次数翻倍，上限 30 秒。
    model_retries: int = Field(default=2, ge=0, le=5)
    model_retry_delay_seconds: float = Field(default=2.0, ge=0.1, le=30.0)
    # Keep usage accounting while temporarily disabling cumulative model quota enforcement.
    enforce_model_budget: bool = False
    max_model_calls: int = Field(default=32, ge=1, le=50)
    max_tokens: int = Field(default=200_000, ge=1000, le=1_000_000)
    max_output_tokens: int = Field(default=32_000, ge=1, le=1_000_000)
    max_review_retries: int = Field(default=2, ge=0, le=5)
    max_evidence_retries: int = Field(default=1, ge=0, le=3)
    # Stop a run that keeps producing no new observation after bounded steering.
    enforce_no_progress: bool = True
    max_no_progress_turns: int = Field(default=6, ge=2, le=20)
    # 字效 Agent 的产出形态：presets 组合角色 Preset（文字块）；autonomous 由 Agent 在工作区自由编写 Effect.tsx，宿主只提供内容
    # 运行时、生成配置参数并检查行为。它只决定新作品与自由组件版本的修改，更早的组合版本仍按 Preset 组合修改。
    remotion_agent: Literal["presets", "autonomous"] = "presets"
    # Execution batches and actual tool calls remain bounded even without model quotas.
    max_steps: int = Field(default=8, ge=1, le=32)
    max_tooluse: int = Field(default=10, ge=1, le=32)
    max_outer_calls: int = Field(default=12, ge=1, le=50)
    max_outer_tokens: int = Field(default=80_000, ge=1000, le=1_000_000)
    max_plan_calls: int = Field(default=12, ge=1, le=50)
    max_plan_tokens: int = Field(default=80_000, ge=1000, le=1_000_000)
    max_executor_calls: int = Field(default=24, ge=1, le=50)
    max_executor_tokens: int = Field(default=160_000, ge=1000, le=1_000_000)
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    max_image_pixels: int = Field(default=20_000_000, gt=0)
    renderer_dir: Path = Path(__file__).parent.parent / "remotion"
    runtime_lib_dir: Path | None = None
    # 开发专用：macOS 没有 bubblewrap，开启后直接用 Node 运行 worker，生成的代码不受隔离；其他系统忽略此开关。
    allow_unsandboxed_worker: bool = False
    browser_executable: Path = Field(default_factory=default_browser_executable)
    font_regular: Path = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    font_bold: Path = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")
    # 首次使用时把随包发布的预装字体（`bundled_fonts/`，哈希固定在 `font_catalog.json`）复制进对象存储与注册表；关闭后只用注册表里已有的字体。
    install_bundled_fonts: bool = True
    # 用户上传的字体文件大小上限（字节）。
    font_upload_max_bytes: int = Field(default=50 * 1024 * 1024, gt=0)

    @property
    def models_configured(self) -> bool:
        """Expose readiness without leaking either credential."""
        return bool(
            self.actor_model
            and self.actor_api_key.get_secret_value()
        )


def load_settings() -> Settings:
    """Read server/.env and save relative data paths directly beneath the template module."""
    settings = Settings()
    if not settings.data_dir.is_absolute():
        settings.data_dir = Path(__file__).parent / settings.data_dir
    return settings
