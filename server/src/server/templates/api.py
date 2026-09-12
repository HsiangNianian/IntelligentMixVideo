"""Assemble only template routes; the host mounts this app without lifecycle or exception hooks."""

from fastapi import FastAPI

from ..settings import Settings, load_settings
from .harness import Harness
from .provider import Provider
from .renderer import Renderer
from .routes import router
from .runtime import Runtime
from .store import Store


def create_template_app(
    settings: Settings | None = None,
    *,
    provider: Provider | None = None,
    renderer: Renderer | None = None,
) -> FastAPI:
    """Build a feature-local app; defer model configuration and local files until first use."""
    application = FastAPI(
        title="Remotion 模板服务",
        openapi_tags=[
            {"name": "服务能力", "description": "查看支持的输入、字体及模型配置状态。"},
            {"name": "参考素材", "description": "上传用于还原文字模板的参考图片。"},
            {
                "name": "模板作品",
                "description": "创建、查询作品，提交参数或自然语言修改。",
            },
            {"name": "模板版本", "description": "查看通过验收的历史版本、代码和配置。"},
            {
                "name": "生成任务",
                "description": "查询进度、取消、重试或补充生成所需信息。",
            },
            {"name": "生成产物", "description": "查看并下载已验收的代码和预览。"},
        ],
    )

    def build_runtime() -> Runtime:
        """Defer model configuration and filesystem side effects until a template route is used."""
        config = settings or load_settings()
        return Runtime(
            Store(config.data_dir),
            Harness(
                provider or Provider(config),
                renderer or Renderer(config),
            ),
            config,
        )

    application.state.build_runtime = build_runtime
    application.include_router(router)

    return application


# Importing the feature registers endpoints but does not create its local data folder.
app = create_template_app()
