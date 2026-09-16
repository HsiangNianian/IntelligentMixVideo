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


@pytest.mark.parametrize("cwd", [".", "server"])
@pytest.mark.parametrize("source", ["file", "environment", "constructor"])
@pytest.mark.parametrize("module,class_name,field,key", [
    ("server.__main__", "ServerSettings", "port", "PORT"),
    ("server.database", "DatabaseSettings", "port", "DB_PORT"),
    ("server.asr.asr", "ASRSettings", "dashscope_api_key", "DASHSCOPE_API_KEY"),
    ("server.segmentation.settings", "Settings", "llm_model", "IMV_LLM_MODEL"),
    ("server.settings", "Settings", "actor_model", "IMV_ACTOR_MODEL"),
    ("server.video_composition.settings", "Settings", "composition_width", "COMPOSITION_WIDTH"),
])
def test_shared_config_path_and_priority(
    tmp_path, monkeypatch, cwd, source, module, class_name, field, key,
):
    """六个配置类从根目录或 server 启动均读取固定文件，构造参数 > 环境 > 文件。"""
    # 重新定义子类，让测试同时发现子类残留的 env_file 覆盖，避免夹具掩盖回归。
    settings_class = runpy.run_path(import_module(module).__file__, run_name=module)[class_name]
    (tmp_path / "server/.env").write_text(
        "IMV_LLM_BASE_URL=https://model.example/v1\nIMV_LLM_API_KEY=test\n"
        "IMV_LLM_MODEL=模型\nSEGMENT_MATCH_BASE_URL=https://match.example\n"
        "ALIBABA_CLOUD_ACCESS_KEY_ID=test\nALIBABA_CLOUD_ACCESS_KEY_SECRET=test\n"
        f"{key}=21001\nUNRELATED=ignored\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(f"{key}=22000\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path / cwd)
    if source != "file":
        monkeypatch.setenv(key, "21002")
    settings = settings_class(**({field: "21003"} if source == "constructor" else {}))
    value = getattr(settings, field)
    if hasattr(value, "get_secret_value"):
        value = value.get_secret_value()
    assert str(value) == {"file": "21001", "environment": "21002", "constructor": "21003"}[source]


def test_shared_config_missing_file_and_explicit_override(tmp_path, monkeypatch):
    """固定文件缺失不回退 cwd；保留显式文件覆盖、禁用文件及字段默认值。"""
    from server.__main__ import ServerSettings

    env = tmp_path / ".env"
    env.write_text("PORT=21001\n", encoding="utf-8")
    assert ServerSettings().port == 20070
    assert ServerSettings(_env_file=env).port == 21001
    (tmp_path / "server/.env").write_text("PORT=21002\n", encoding="utf-8")
    assert ServerSettings(_env_file=None).port == 20070
    monkeypatch.setenv("PORT", "21003")
    assert ServerSettings(_env_file=None).port == 21003


@pytest.mark.parametrize("port", ["", "0", "65536", "not-a-port"])
def test_invalid_port_prevents_startup(port, monkeypatch, mocker):
    """公共基类保留启动端口校验，非法配置不能启动 Uvicorn。"""
    from pydantic import ValidationError
    from server.__main__ import main

    monkeypatch.setenv("PORT", port)
    run = mocker.patch.object(uvicorn, "run")
    with pytest.raises(ValidationError):
        main()
    run.assert_not_called()


@pytest.mark.parametrize("data_dir", ["custom-data", "/tmp/imv-config-test-data"])
def test_remotion_data_directory_keeps_existing_base(data_dir, tmp_path):
    """统一文件读取后，相对数据目录仍归模板模块，绝对目录原样保留。"""
    from pathlib import Path
    from server import settings

    (tmp_path / "server/.env").write_text(f"IMV_DATA_DIR={data_dir}\n", encoding="utf-8")
    expected = Path(data_dir)
    if not expected.is_absolute():
        expected = Path(settings.__file__).parent / "remotion_templates" / expected
    assert settings.load_settings().data_dir == expected


def test_config_base_uses_source_relative_path():
    """直接执行基类文件验证源码路径约定，不依赖夹具替换后的 model_config。"""
    from pathlib import Path
    from server import config_base

    loaded = runpy.run_path(config_base.__file__)
    assert loaded["CommonSettings"].model_config["env_file"] == (
        Path(config_base.__file__).resolve().parents[2] / ".env"
    )
