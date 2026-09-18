"""发现一级模块的公开设置入口；只返回描述，不读取或保存配置值。"""

from importlib import import_module
from pathlib import Path
from fastapi import APIRouter


def discover_plugins(root: Path, package: str = "server") -> dict[str, dict]:
    """按目录名发现 settings_plugin.py；坏描述、重复 ID 和导入错误直接失败。"""
    discovered = {}
    for path in sorted(root.glob("*/settings_plugin.py")):
        module = path.parent.name
        if not module.isidentifier():
            raise ValueError(f"模块目录名称不合法：{module}")
        entry = import_module(f"{package}.{module}.settings_plugin")
        plugin = getattr(entry, "SETTINGS_PLUGIN", None)
        if not isinstance(plugin, dict):
            raise ValueError(f"{entry.__name__} 必须导出 SETTINGS_PLUGIN 字典")
        plugin_id, name, schema = (plugin.get(key) for key in ("id", "name", "schema"))
        if not isinstance(plugin_id, str) or not plugin_id.strip():
            raise ValueError(f"{entry.__name__} 的插件 ID 不合法")
        if plugin_id in discovered:
            raise ValueError(f"设置插件 ID 重复：{plugin_id}")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{entry.__name__} 缺少显示名称")
        if not isinstance(schema, dict) or schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
            raise ValueError(f"{entry.__name__} 必须提供对象类型的字段 Schema")
        discovered[plugin_id] = {"id": plugin_id, "name": name, "schema": schema}
        if plugin.get("scope") == "client":
            discovered[plugin_id]["scope"] = "client"
        if model := plugin.get("settings"):
            # 完整模型只供 Debug 描述和内置启动使用；不实例化，也不公开模型对象。
            advanced = model.model_json_schema(by_alias=False)
            advanced["properties"].update(schema["properties"])
            paths = set()
            for key, field in advanced["properties"].items():
                # 仅折叠可空单类型；真正的联合类型仍由前端明确拒绝。
                if len(field.get("anyOf", [])) == 2 and {"type": "null"} in field["anyOf"]:
                    field.update(next(option for option in field.pop("anyOf") if option.get("type") != "null"))
                    field["default"] = ""
                if field.get("format") == "path":
                    paths.add(key)
                    field.pop("format")
                    field["default"] = ""  # 随包路径因安装位置而异，留空使用内置默认值。
            discovered[plugin_id].update(settings=model, debug_schema=advanced, paths=paths)
    return discovered


# 每个进程导入时发现一次；文件增删需重启后端，不做 Python 代码热卸载。
plugins = discover_plugins(Path(__file__).resolve().parent)
router = APIRouter(prefix="/api/settings", tags=["客户端设置"])


@router.get("/plugins")
def list_plugins(client_only: bool = False) -> list[dict]:
    """只返回已注册的公开字段描述，不提供服务端配置读写。"""
    result = []
    for plugin in plugins.values():
        if client_only and plugin.get("scope") != "client":
            continue
        public = {key: plugin[key] for key in ("id", "name", "schema", "scope") if key in plugin}
        if not client_only and "debug_schema" in plugin:
            public.update(schema=plugin["debug_schema"], description="Debug 高级配置保存后重启客户端，在内置后端生效；路径留空使用内置默认值。")
        result.append(public)
    return result
