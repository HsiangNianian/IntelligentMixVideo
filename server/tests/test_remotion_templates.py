"""Focused tests for Preset creation, Sprite composition, persistence and preview publication."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.remotion_templates.provider import Budget
from server.remotion_templates.settings import Settings
from server.remotion_templates.tools.catalog import available
from server.remotion_templates.tools.compose import compose_source
from server.remotion_templates.tools.contracts import (
    CodeValidationReport,
    ComponentDefinition,
    PresetDraft,
    PresetRecord,
    SpriteComposeInput,
    SpriteCreateInput,
    SpriteRecord,
)
from server.remotion_templates.tools.registry import ToolFault, registered_tools
from server.remotion_templates.tools.session import ToolSession


RENDERER_DEPS = Path(__file__).parents[1] / "src" / "server" / "remotion" / "node_modules" / "esbuild"


def session(tmp_path: Path) -> ToolSession:
    """Build a creation-only session without a model or browser dependency."""
    settings = Settings(_env_file=None, data_dir=tmp_path)
    harness = SimpleNamespace(settings=settings, renderer=None)
    return ToolSession(harness, None, None, Budget(), tmp_path / "run", [], lambda *_: None, {})


def valid_report() -> CodeValidationReport:
    """Return a deterministic host report for data-only composition tests."""
    return CodeValidationReport(passed=True, diagnostics=[])


def patch_validation(current: ToolSession) -> None:
    """Replace isolated browser validation for the no-browser unit path."""
    async def validate(_component: ComponentDefinition) -> CodeValidationReport:
        """Keep the test focused on the creation catalog and source integrity."""
        return valid_report()

    current.validate_code = validate


def draft() -> PresetDraft:
    """Return a minimal valid title Preset."""
    return PresetDraft(
        description="title",
        code="export default function Label(){return <div>Title</div>}",
        parameter_schema={"type": "object", "properties": {}, "additionalProperties": False},
        default_parameters={},
    )


def saved_sprite(sprite_code: str, preset_code: str) -> SpriteRecord:
    """构造一条带实例快照的 Sprite 记录，用于观察任务快照的体积收缩。"""
    return SpriteRecord(
        sprite_id="saved-sprite",
        created_at="2026-09-30T00:00:00+00:00",
        description="标题",
        code=sprite_code,
        parameter_schema={"type": "object", "properties": {"title": {"type": "object"}}, "additionalProperties": False},
        default_parameters={"title": {"text": "今日灵感"}},
        composition={"width": 1080, "height": 1920, "fps": 30, "duration_frames": 30},
        instances=[{
            "instance_id": "title",
            "preset": {
                "description": "title",
                "code": preset_code,
                "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": False},
                "default_parameters": {},
            },
            "parameters": {"text": "今日灵感"},
            "layout": {"x": 0, "y": 0, "width": 1080, "height": 1920, "z_index": 0},
            "timing": {"start_frame": 0, "duration_frames": 30},
        }],
    )


def test_snapshot_drops_rebuildable_source_only_when_over_budget(tmp_path):
    """快照超过请求预算时才丢弃可由记录重建的源码，结构、Schema 与默认值始终保留。

    实例内的预设源码已包含在合成后的 Sprite 源码里，先丢它；仍超限才丢 Sprite 源码。
    """
    current = session(tmp_path)
    source = "export default function Sprite(){return null}"
    current.latest_sprite_id = "saved-sprite"

    current.saved_sprites["saved-sprite"] = saved_sprite(source, source)
    plain = current.snapshot()
    assert plain["saved_sprites"][0]["code"] == source
    assert plain["saved_sprites"][0]["instances"][0]["preset"]["code"] == source

    current.saved_sprites["saved-sprite"] = saved_sprite(source, "x" * 150_000)
    trimmed = current.snapshot()
    assert "code" not in trimmed["saved_sprites"][0]["instances"][0]["preset"]
    assert trimmed["saved_sprites"][0]["code"] == source

    current.saved_sprites["saved-sprite"] = saved_sprite("y" * 250_000, "x" * 150_000)
    minimal = current.snapshot()
    assert "code" not in minimal["saved_sprites"][0]
    assert "code" not in minimal["latest_sprite"]
    assert minimal["saved_sprites"][0]["parameter_schema"]["type"] == "object"
    assert minimal["saved_sprites"][0]["default_parameters"] == {"title": {"text": "今日灵感"}}


def test_full_catalog_is_registered_but_only_implemented_tools_are_public():
    """The documented catalog registers eleven tools; deferred ones stay out of the model window."""
    names = {item.name for item in registered_tools()}
    assert names == {
        "image.info", "image.resize", "image.crop",
        "preset.search", "preset.create", "preset.modify",
        "validate.code", "validate.render", "sprite.compose", "sprite.create",
        "tools.inspect",
    }
    implemented = {item.name for item in registered_tools() if item.implemented}
    assert implemented == {
        "preset.create", "preset.search", "preset.modify",
        "sprite.compose", "sprite.create", "tools.inspect",
    }
    assert {item.name for item in available()} == implemented | {"tools.plan_execute"}


@pytest.mark.skipif(not RENDERER_DEPS.exists(), reason="install locked Remotion renderer dependencies")
def test_creation_flow_persists_one_sprite_without_semantic_index(tmp_path):
    """Preset creation, composition and Sprite storage work without ChromaDB or model calls."""
    async def run():
        current = session(tmp_path)
        patch_validation(current)
        preset = await current.execute("preset.create", draft(), available())
        composed = await current.execute(
            "sprite.compose",
            SpriteComposeInput.model_validate({
                "description": "title preview",
                "instances": [{
                    "instance_id": "title",
                    "source": {"kind": "stored", "preset_id": preset["preset"]["preset_id"]},
                    "layout": {"x": 0, "y": 0, "width": 1080, "height": 1920, "z_index": 0},
                    "timing": {"start_frame": 0, "duration_frames": 30},
                }],
            }),
            available(),
        )
        saved = await current.execute("sprite.create", SpriteCreateInput.model_validate(composed), available())
        assert saved["sprite"]["code"] == composed["sprite"]["code"]
        assert len(current.saved_sprites) == 1
        assert len(current.catalog.read_sprites()) == 1

    asyncio.run(run())


@pytest.mark.skipif(os.environ.get("IMV_TEST_RENDERER") != "1", reason="set IMV_TEST_RENDERER=1 for Linux preview integration")
def test_real_creation_flow_builds_preview(tmp_path):
    """Linux integration verifies the saved Sprite produces Export.tsx and interactive.js."""
    from server.remotion_templates.harness import Harness
    from server.remotion_templates.renderer import Renderer

    # The autouse isolation fixture strips IMV_* variables, so read the renderer
    # locations this run was configured with before building the settings object.
    configured = {
        name: Path(value)
        for name, value in (
            ("browser_executable", os.environ.get("IMV_BROWSER_EXECUTABLE")),
            ("font_regular", os.environ.get("IMV_FONT_REGULAR")),
            ("font_bold", os.environ.get("IMV_FONT_BOLD")),
        )
        if value
    }
    settings = Settings(_env_file=None, data_dir=tmp_path, **configured)
    renderer = Renderer(settings)
    harness = Harness(SimpleNamespace(settings=settings), renderer)

    async def run():
        current = ToolSession(harness, None, None, Budget(), tmp_path / "run", [], lambda *_: None, {})
        preset = await current.execute("preset.create", draft(), available())
        composed = await current.execute("sprite.compose", SpriteComposeInput.model_validate({
            "description": "title preview",
            "instances": [{
                "instance_id": "title",
                "source": {"kind": "stored", "preset_id": preset["preset"]["preset_id"]},
                "layout": {"x": 0, "y": 0, "width": 1080, "height": 1920, "z_index": 0},
                "timing": {"start_frame": 0, "duration_frames": 30},
            }],
        }), available())
        saved = await current.execute("sprite.create", SpriteCreateInput.model_validate(composed), available())
        result = await harness.finalize_sprite(current, saved["sprite"]["sprite_id"], Budget(), tmp_path)
        assert (result[3] / "Export.tsx").stat().st_size > 0
        assert (result[3] / "interactive.js").stat().st_size > 0

    asyncio.run(run())


def test_deferred_tools_refuse_instead_of_fabricating():
    """延后实现的工具仍可查询契约，但调用只返回明确错误，不伪造成功结果。"""
    asyncio.run(_deferred_scenario())


def test_tools_inspect_accepts_both_name_forms():
    """tools.inspect 同时接受契约里的点号 ID 和模型窗口里显示的下划线名。

    模型看到的函数名是下划线形式（preset_create），只能查点号 ID 时它会反复
    猜名字并因此失败。
    """
    asyncio.run(_inspect_scenario())


async def _inspect_scenario():
    """两种写法返回同一份描述，未知名字报出全部已注册 ID。"""
    from server.remotion_templates.tools.registry import get_tool

    tool = get_tool("tools.inspect")
    # 每个模型可见名称都必须可检查，包括非业务注册器管理的计划控制工具。
    for visible in available():
        for name in (visible.name, visible.wire()["function"]["name"]):
            result = await tool.invoke(None, tool.input.model_validate({"tool_name": name}))
            assert result["ok"] is True
            assert result["data"]["tool_name"] == visible.name
            assert result["data"]["input_schema"] == visible.input.model_json_schema()
    unknown = await tool.invoke(None, tool.input.model_validate({"tool_name": "preset"}))
    assert unknown["ok"] is False
    assert unknown["error"]["code"] == "TOOL_NOT_FOUND"
    for expected in ("preset.create", "sprite.create", "tools.inspect", "tools.plan_execute"):
        assert expected in unknown["error"]["message"]


async def _deferred_scenario():
    """逐个调用延后工具，确认统一返回 NOT_IMPLEMENTED 与自身名称。"""
    from server.remotion_templates.tools.registry import get_tool

    deferred = {
        "image.info": {"image": {"asset_id": "3f2504e0-4f89-41d3-9a0c-0305e82c3301"}},
        "image.crop": {"image": {"asset_id": "3f2504e0-4f89-41d3-9a0c-0305e82c3301"}, "x": 0, "y": 0, "width": 1, "height": 1},
        "validate.code": {"component": {"code": "export default function C(){return null}", "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": False}, "default_parameters": {}}},
    }
    for name, values in deferred.items():
        tool = get_tool(name)
        result = await tool.invoke(None, tool.input.model_validate(values))
        assert result["ok"] is False
        assert result["error"]["code"] == "NOT_IMPLEMENTED"
        assert name in result["error"]["message"]


def test_deferred_tools_stay_out_of_the_model_window():
    """延后工具不得出现在任何一层的工具列表里。"""
    skipped = [item.name for item in available() if item.name in {"image.info", "image.crop", "validate.code"}]
    assert skipped == []


def test_mysql_preset_create_persists_through_the_catalog_store(tmp_path):
    """preset.create 经目录存储写入；数据库不可用时仍能在本地目录读回。"""
    async def run():
        """用仅创建模式的会话保存预设，再从同一目录重新读取。"""
        current = session(tmp_path)
        patch_validation(current)
        saved = await current.execute("preset.create", draft(), available())
        assert current.catalog_backend in {"mysql", "local"}
        assert current.catalog.find_preset(saved["preset"]["preset_id"]).description == "title"

    asyncio.run(run())


@pytest.mark.skipif(not RENDERER_DEPS.exists(), reason="install locked Remotion renderer dependencies")
def test_compose_source_is_stable_across_parameter_key_order():
    """参数键顺序变化必须生成完全相同的源码：模型回显 JSON 时键序合法地会变。

    sprite.create 逐字节比对重新生成的源码；若生成结果依赖调用方的字典顺序，
    一次未经修改的回显会被判成手工改过的候选而无法保存。
    """
    parameters = {"text": "今日灵感", "size": 64, "color": "#FFFFFF"}

    def instance(values):
        """构造一个最小可组合实例，字段与 compose 契约一致。"""
        return {
            "instance_id": "title",
            "preset": {
                "code": 'import React from "react"; export default function Label(p: {text: string; size: number; color: string}) { return <div>{p.text}</div>; }',
                "parameter_schema": {"type": "object", "properties": {"text": {"type": "string"}, "size": {"type": "number"}, "color": {"type": "string"}}, "additionalProperties": False},
                "default_parameters": values,
                "description": "title",
            },
            "parameters": values,
            "layout": {"x": 0, "y": 0, "width": 1080, "height": 1920, "z_index": 0},
            "timing": {"start_frame": 0, "duration_frames": 30},
        }

    first = compose_source([instance(parameters)])
    reordered = {key: parameters[key] for key in reversed(list(parameters))}
    assert compose_source([instance(reordered)]) == first


def test_code_failure_returns_diagnostics_and_the_fix_saves(tmp_path):
    """语法错误时 Executor 收到真实诊断，同一会话修复后才保存 Preset。

    修复前 worker 的提前返回被当作基础设施故障，ValidationUnavailable 直接穿透
    工具调度，任务失败且模型拿不到任何可以照着修的诊断。
    """
    class ScriptedRenderer:
        """按提交源码复现 worker 的两种报告形态，不启动浏览器。"""

        def worker_browser_path(self):
            """单元测试不启动浏览器，只返回一个固定路径。"""
            return "/usr/bin/chromium"

        async def run_worker(self, directory, *, worker, timeout_seconds=None):
            """只有闭合的默认导出通过源码策略检查，否则按真实形态停在失败阶段。"""
            code = json.loads((directory / "request.json").read_text())["code"]
            if code.rstrip().endswith("}"):
                return {
                    "passed": True,
                    "checks": [
                        {"name": "source_policy", "status": "pass"},
                        {"name": "export_source", "status": "pass"},
                        {"name": "typescript", "status": "pass"},
                    ],
                    "diagnostics": [],
                }
            return {
                "passed": False,
                "checks": [{"name": "source_policy", "status": "failed", "message": "JSX element has no corresponding closing tag."}],
                "diagnostics": [{"source": "lsp", "severity": "error", "message": "JSX element has no corresponding closing tag."}],
            }

    class RecordingCatalog:
        """只记录入库调用；目录存储的读写由它自己的用例覆盖。"""

        def __init__(self):
            """保存本次用例实际入库的记录，便于断言失败时没有落库。"""
            self.records: list[PresetRecord] = []

        def append_preset(self, record):
            """返回本地后端标记，避免测试连接数据库。"""
            self.records.append(record)
            return "local"

    settings = Settings(_env_file=None, data_dir=tmp_path)
    catalog = RecordingCatalog()
    current = ToolSession(
        SimpleNamespace(settings=settings, renderer=ScriptedRenderer()),
        None, None, Budget(), tmp_path / "run", [], lambda *_: None, {},
    )
    current.catalog = catalog
    broken = draft().model_copy(update={"code": "export default function Label(){return <div>Title</div>"})
    with pytest.raises(ToolFault) as failure:
        asyncio.run(current.execute("preset.create", broken, available()))
    assert failure.value.error.code == "CODE_VALIDATION_FAILED"
    detail = json.dumps(failure.value.error.model_dump(mode="json"), ensure_ascii=False)
    assert "JSX element has no corresponding closing tag." in detail
    assert catalog.records == []
    saved = asyncio.run(current.execute("preset.create", draft(), available()))
    assert saved["validation"]["passed"] is True
    assert len(catalog.records) == 1


def test_local_fallback_preset_stays_readable_after_database_recovers(tmp_path):
    """INSERT 失败落到本地兜底后，数据库恢复时读取仍须看到这条已保存记录。

    若只读数据库，一次已返回成功的 Preset 会在恢复后消失，而 Agent 拿到的
    preset_id 再也解析不到。
    """
    from sqlalchemy.exc import OperationalError

    from server.remotion_templates.tools.catalog_store import CatalogStore

    store = CatalogStore(tmp_path / "catalog")
    record = PresetRecord(
        preset_id="fallback-1",
        created_at="2026-09-30T00:00:00+00:00",
        description="兜底预设",
        code="export default function C(){return null}",
        parameter_schema={"type": "object", "properties": {}, "additionalProperties": False},
        default_parameters={},
    )

    class BrokenInsert:
        """探活成功但写入失败，模拟提交阶段的数据库故障。"""

        def begin(self):
            raise OperationalError("insert failed", {}, Exception())

    store._engine = lambda: BrokenInsert()
    assert store.append_preset(record) == "local"

    class RecoveredEngine:
        """数据库恢复且目标表为空，本地兜底记录不得因此消失。"""

        def connect(self):
            class Connection:
                """返回空结果集，代表服务端尚未包含这条记录。"""

                def execute(self, *_args, **_kwargs):
                    return []

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

            return Connection()

    store._engine = lambda: RecoveredEngine()
    assert [item.preset_id for item in store.read_presets()] == ["fallback-1"]
    assert store.find_preset("fallback-1").description == "兜底预设"
