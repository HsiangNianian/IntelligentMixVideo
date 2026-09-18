"""验证启动入口与公共配置读取规则；在 server/ 执行 uv run --locked pytest tests/test_entrypoint.py。"""

from importlib import import_module
from importlib.metadata import distribution
import runpy
import sys

import pytest
import uvicorn

from server.app import app


@pytest.mark.parametrize("entry", ["console", "module"])
def test_startup_entry(entry: str, monkeypatch: pytest.MonkeyPatch, mocker) -> None:
    """替换阻塞的事件循环，检查真实入口的应用路径可导入且监听设置正确。"""
    # 隔离本机 .env 与端口配置，避免开发环境影响入口断言。
    mocker.patch("pydantic_settings.sources.DotEnvSettingsSource._read_env_files", return_value={})
    monkeypatch.setenv("PORT", "20070")
    calls = []

    def capture_startup(application: str, **options: object) -> None:
        """记录启动请求，避免测试占用用户机器的固定端口。"""
        calls.append((application, options))

    monkeypatch.setattr(uvicorn, "run", capture_startup)
    if entry == "console":
        command = next(
            item for item in distribution("imv-server").entry_points
            if item.group == "console_scripts" and item.name == "server"
        )
        command.load()()
    else:
        # 控制台用例可能已导入该模块；模拟全新 python -m 进程的模块状态。
        monkeypatch.delitem(sys.modules, "server.__main__", raising=False)
        runpy.run_module("server", run_name="__main__")

    assert len(calls) == 1
    application, options = calls[0]
    module, name = application.split(":")
    assert getattr(import_module(module), name) is app
    assert options == {"host": "0.0.0.0", "port": 20070}


@pytest.mark.parametrize("module,class_name,field,key", [
    ("server.__main__", "ServerSettings", "port", "PORT"),
    ("server.database", "DatabaseSettings", "port", "DB_PORT"),
    ("server.asr.settings", "ASRSettings", "dashscope_api_key", "DASHSCOPE_API_KEY"),
    ("server.segmentation.settings", "Settings", "llm_model", "IMV_LLM_MODEL"),
    ("server.remotion_templates.settings", "Settings", "actor_model", "IMV_ACTOR_MODEL"),
    ("server.video_composition.settings", "Settings", "composition_width", "COMPOSITION_WIDTH"),
])
def test_shared_config_path(tmp_path, monkeypatch, module, class_name, field, key):
    """六个配置类从根目录或 server 启动均读取 server/.env，忽略根目录的同名文件。"""
    # 重新定义子类，避免隔离夹具掩盖子类残留 env_file 的回归。
    settings_class = runpy.run_path(import_module(module).__file__, run_name=module)[class_name]
    (tmp_path / "server/.env").write_text(
        "IMV_LLM_BASE_URL=https://model.example/v1\nIMV_LLM_API_KEY=test\n"
        "IMV_LLM_MODEL=模型\nSEGMENT_MATCH_BASE_URL=https://match.example\n"
        "ALIBABA_CLOUD_ACCESS_KEY_ID=test\nALIBABA_CLOUD_ACCESS_KEY_SECRET=test\n"
        f"{key}=21001\n", encoding="utf-8",
    )
    (tmp_path / ".env").write_text(f"{key}=22000\n", encoding="utf-8")
    for cwd in (tmp_path, tmp_path / "server"):
        monkeypatch.chdir(cwd)
        value = getattr(settings_class(), field)
        if hasattr(value, "get_secret_value"):
            value = value.get_secret_value()
        assert str(value) == "21001"


@pytest.mark.parametrize("directory", [None, "custom-data", "absolute"])
def test_remotion_data_directory_keeps_existing_base(tmp_path, directory):
    """配置归入字效模块后，默认/相对/绝对数据目录及默认渲染资源路径保持不变。"""
    from pathlib import Path
    import server
    from server.remotion_templates import settings

    package_dir = Path(server.__file__).parent
    value = str(tmp_path / "absolute-data") if directory == "absolute" else directory
    (tmp_path / "server/.env").write_text(
        f"IMV_DATA_DIR={value}\n" if value is not None else "", encoding="utf-8",
    )
    config = settings.load_settings()
    expected = Path(value) if directory == "absolute" else package_dir / "remotion_templates" / (value or ".data")
    assert config.data_dir == expected
    assert config.renderer_dir == package_dir / "remotion"
