"""设置目录脱敏和文件约定发现测试；执行 uv run --locked pytest tests/test_settings_plugins.py。"""

import importlib
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from server import settings_plugins


@pytest.fixture
def package(tmp_path, monkeypatch):
    """生成独立真实 Python 包，退出时清理导入缓存，不修改生产目录或注册字典。"""
    name = f"settings_test_{uuid.uuid4().hex}"
    root = tmp_path / name
    root.mkdir()
    (root / "__init__.py").write_text('"""隔离的插件测试包。"""\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    yield root, name
    for module in list(sys.modules):
        if module == name or module.startswith(f"{name}."):
            del sys.modules[module]
    importlib.invalidate_caches()


def write_plugin(root, name, source):
    """写入真实入口文件；测试新增/删除文件，而非手工登记插件 ID。"""
    directory = root / name
    directory.mkdir()
    (directory / "__init__.py").write_text('"""测试模块。"""\n')
    entry = directory / "settings_plugin.py"
    entry.write_text(source, encoding="utf-8")
    importlib.invalidate_caches()
    return entry


def test_discovery_add_remove_and_api(package, client, monkeypatch):
    """空目录、新增、稳定排序和移除入口影响 API 目录；没有入口的内部配置不会导入。"""
    root, name = package
    assert settings_plugins.discover_plugins(root, name) == {}
    internal = root / "internal"
    internal.mkdir()
    (internal / "__init__.py").write_text('raise RuntimeError("不得导入内部模块")')
    (internal / "settings.py").write_text('raise RuntimeError("不得导入内部配置")')
    write_plugin(root, "zeta", 'SETTINGS_PLUGIN = {"id": "last", "name": "最后", "schema": {"type": "object", "properties": {}}}')
    entry = write_plugin(root, "alpha", '''from pydantic import BaseModel, Field
class ClientSettings(BaseModel):
    enabled: bool = Field(default=False, title="启用")
SETTINGS_PLUGIN = {"id": "first", "name": "第一个", "schema": ClientSettings.model_json_schema(), "private": object()}
''')
    # 接口使用本次真实发现结果，保留 HTTP 契约验证。
    monkeypatch.setattr(settings_plugins, "plugins", settings_plugins.discover_plugins(root, name))
    response = client.get("/api/settings/plugins")
    assert response.status_code == 200
    assert [plugin["id"] for plugin in response.json()] == ["first", "last"]
    assert response.json()[0]["schema"]["properties"]["enabled"]["default"] is False
    assert set(response.json()[0]) == {"id", "name", "schema"}
    entry.unlink()
    assert list(settings_plugins.discover_plugins(root, name)) == ["last"]


def test_duplicate_plugin_id(package):
    """重复 ID 阻止发现完成，不允许后加载的模块覆盖配置身份。"""
    root, name = package
    for module in ("one", "two"):
        write_plugin(root, module, 'SETTINGS_PLUGIN = {"id": "same", "name": "测试", "schema": {"type": "object", "properties": {}}}')
    with pytest.raises(ValueError, match="ID 重复：same"):
        settings_plugins.discover_plugins(root, name)


@pytest.mark.parametrize("source, error", [
    ("import missing_settings_test_dependency", ModuleNotFoundError),
    ('raise RuntimeError("入口故障")', RuntimeError),
])
def test_import_failure_propagates(package, source, error):
    """入口存在时保留缺依赖和初始化失败错误，不能视为未安装插件。"""
    root, name = package
    write_plugin(root, "broken", source)
    with pytest.raises(error):
        settings_plugins.discover_plugins(root, name)


def test_real_discovery_has_no_runtime_initialization(tmp_path, monkeypatch):
    """首次发现前注入秘密，目录只返回描述，且不实例化配置或连接外部服务。"""
    monkeypatch.setenv("IMV_LLM_API_KEY", "server-secret")
    monkeypatch.setenv("IMV_LLM_MODEL", "private-model")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "private-asr-secret")
    source = Path(settings_plugins.__file__).parents[1]
    code = '''import sys, socket, json
sys.path.insert(0, sys.argv[1])
from pydantic_settings import BaseSettings
def forbidden(*args, **kwargs):
    raise AssertionError("发现描述不能初始化配置或连接外部服务")
BaseSettings.__init__ = forbidden
socket.socket.connect = forbidden
from server.settings_plugins import list_plugins
catalog = list_plugins()
assert catalog
for secret in ("server-secret", "private-model", "private-asr-secret"):
    assert secret not in json.dumps(catalog)
assert "server.asr.asr" not in sys.modules
'''
    result = subprocess.run([sys.executable, "-c", code, str(source)], cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_client_modules_visible_in_both_modes(client, monkeypatch):
    """普通目录只含 Agent 与 IMS；完整目录另含 Debug 模块，凭据从不出现在描述中。"""
    monkeypatch.setenv("IMV_ACTOR_API_KEY", "private-agent")
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "private-ims")
    normal = client.get("/api/settings/plugins?client_only=true")
    debug = client.get("/api/settings/plugins")
    assert normal.status_code == debug.status_code == 200
    assert {item["id"] for item in normal.json()} == {"remotion_agent", "ims"}
    assert {item["id"] for item in debug.json()} == {"remotion_agent", "ims", "asr", "segmentation", "startup"}
    for secret in ("private-agent", "private-ims"):
        assert secret not in debug.text
    fields = next(item for item in normal.json() if item["id"] == "ims")["schema"]["properties"]
    assert fields["ims_access_key_secret"]["format"] == "password"
    assert fields["ims_endpoint"]["default"] == "ice.cn-shanghai.aliyuncs.com"
    assert "match_base_url" not in fields
    catalog = {item["id"]: item for item in debug.json()}
    assert "match_base_url" in catalog["ims"]["schema"]["properties"]
    assert "allow_insecure_llm_http" in catalog["segmentation"]["schema"]["properties"]
    assert catalog["startup"]["schema"]["properties"]["port"]["maximum"] == 65535
    paths = catalog["remotion_agent"]["schema"]["properties"]
    assert paths["runtime_lib_dir"]["type"] == "string" and paths["data_dir"]["default"] == ""
    assert all("settings" not in item and "paths" not in item for item in debug.json())
