"""Focused tests for Preset creation, Sprite composition, persistence and preview publication.

The shared Preset catalog lives in MySQL only, so every test here uses the temporary SQLite stand-in.
"""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.remotion_templates.models import TemplateCandidate
from server.remotion_templates.provider import Budget
from server.remotion_templates.settings import Settings
from server.remotion_templates.tools.catalog import available
from server.remotion_templates.tools.compose import compose_source
from server.remotion_templates.tools.contracts import (
    CheckResult,
    CodeValidationReport,
    ComponentDefinition,
    Composition,
    PresetDraft,
    PresetModifyInput,
    PresetRecord,
    PresetSearchInput,
    RenderValidationReport,
    SpriteComposeInput,
    SpriteCreateInput,
    SpriteDraft,
    SpriteRecord,
)
from server.remotion_templates.tools.registry import ToolFault, registered_tools
from server.remotion_templates.tools.session import ToolSession


RENDERER_DEPS = Path(__file__).parents[1] / "src" / "server" / "remotion" / "node_modules" / "esbuild"


@pytest.fixture(autouse=True)
def catalog_database(template_db):
    """Preset catalog reads and writes go to the temporary database standing in for MySQL."""
    return template_db


def session(tmp_path: Path) -> ToolSession:
    """Build a creation-only session without a model or browser dependency."""
    settings = Settings(_env_file=None, data_dir=tmp_path)
    harness = SimpleNamespace(settings=settings, renderer=None)
    return ToolSession(harness, None, None, Budget(), tmp_path / "run", [], lambda *_: None, {})


def valid_report() -> CodeValidationReport:
    """Return a deterministic host report for data-only composition tests."""
    return CodeValidationReport(passed=True, diagnostics=[])


def render_report(duration_frames: int, *checks: CheckResult) -> RenderValidationReport:
    """A browser mount report for the fixed canvas with the given base checks; passes when none failed."""
    return RenderValidationReport(
        passed=all(item.status == "passed" for item in checks),
        composition=Composition(width=1080, height=1920, fps=30, duration_frames=duration_frames),
        code_validation=valid_report(),
        checks=list(checks),
        tests=[],
        custom_tests_executed=0,
    )


def patch_validation(current: ToolSession) -> None:
    """Replace isolated browser validation for the no-browser unit path."""
    async def validate(_component: ComponentDefinition) -> CodeValidationReport:
        """Keep the test focused on the creation catalog and source integrity."""
        return valid_report()

    async def mount(request, **_options) -> RenderValidationReport:
        """The browser mount and its transparency measurement need Chromium; report a clean pass."""
        return render_report(request.duration_frames, CheckResult(name="transparency", status="passed"))

    current.validate_code = validate
    current.validate_render = mount


# 滤镜分类的两个必备参数：可以组合成能通过 sprite.create 分类检查的最小 Sprite。
FILTER_SCHEMA = {
    "type": "object",
    "properties": {
        "tintColor": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$"},
        "intensity": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["tintColor", "intensity"],
    "additionalProperties": False,
}
FILTER_BINDINGS = [
    {"name": "intensity", "title": "强度", "targets": [{"instance_id": "title", "parameter": "intensity"}]},
    {"name": "tintColor", "title": "色调", "targets": [{"instance_id": "title", "parameter": "tintColor"}]},
]


def draft() -> PresetDraft:
    """Return a minimal valid Preset that declares the filter category's required parameters."""
    return PresetDraft(
        description="title",
        code="export default function Tint(props: {tintColor: string; intensity: number}){return <div style={{background: props.tintColor, opacity: props.intensity}} />}",
        parameter_schema=FILTER_SCHEMA,
        default_parameters={"tintColor": "#FF8A3D", "intensity": 0.4},
    )


def categorized(record: SpriteRecord) -> SpriteDraft:
    """Turn a saved-Sprite fixture into a draft that exposes the filter category's required top-level parameters."""
    data = record.model_dump(mode="json", exclude={"sprite_id", "created_at"}, exclude_unset=True)
    data["parameter_schema"]["properties"].update(FILTER_SCHEMA["properties"])
    data["default_parameters"].update({"tintColor": "#FF8A3D", "intensity": 0.4})
    return SpriteDraft(**data, kind="filter_overlay", bindings=[
        {**binding, "targets": [{"instance_id": data["instances"][0]["instance_id"], "parameter": binding["name"]}]}
        for binding in FILTER_BINDINGS
    ])


def saved_sprite(sprite_code: str, preset_code: str, instance_ids=("title",)) -> SpriteRecord:
    """构造一条带实例快照的 Sprite 记录，用于观察任务快照只给摘要。"""
    return SpriteRecord(
        sprite_id="saved-sprite",
        created_at="2026-09-30T00:00:00+00:00",
        description="标题",
        code=sprite_code,
        parameter_schema={"type": "object", "properties": {"title": {"type": "object"}}, "additionalProperties": False},
        default_parameters={"title": {"text": "今日灵感"}},
        composition={"width": 1080, "height": 1920, "fps": 30, "duration_frames": 30},
        instances=[{
            "instance_id": instance_id,
            "preset": {
                "description": "title",
                "code": preset_code,
                "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": False},
                "default_parameters": {},
            },
            "parameters": {"text": "今日灵感"},
            "layout": {"x": 0, "y": 0, "width": 1080, "height": 1920, "z_index": 0},
            "timing": {"start_frame": 0, "duration_frames": 30},
        } for instance_id in instance_ids],
    )


def test_snapshot_lists_saved_sprites_without_source(tmp_path):
    """已保存的 Sprite 只以 ID 与摘要进入快照：源码不随每次模型请求重复发送。"""
    current = session(tmp_path)
    current.latest_sprite_id = "saved-sprite"
    current.saved_sprites["saved-sprite"] = saved_sprite("x" * 50_000, "y" * 50_000)
    snapshot = current.snapshot(with_code=True)
    assert snapshot["saved_sprites"] == [{
        "sprite_id": "saved-sprite", "description": "标题",
        "composition": {"width": 1080, "height": 1920, "fps": 30, "duration_frames": 30},
        "instance_ids": ["title"],
    }]
    assert "latest_sprite" not in snapshot and "x" * 100 not in json.dumps(snapshot)


def test_edit_base_presets_become_drafts_and_only_the_coder_sees_their_source(tmp_path):
    """编辑任务：基线实例的预设登记为 base_* 草稿；只有带源码的快照列出源码，相同源码只列一次。"""
    settings = Settings(_env_file=None, data_dir=tmp_path)
    # Runtime hands the session the accepted spec and that version's TemplateCandidate, as for a real edit.
    spec = SimpleNamespace(sprite=saved_sprite("bundle", "preset-source", ("left", "right")), composition=Composition(width=1080, height=1920, fps=30, duration_frames=30), sprite_kind="filter_overlay")
    base = TemplateCandidate(tsx_code="bundle", config_schema={"type": "object"}, default_config={})
    current = ToolSession(SimpleNamespace(settings=settings, renderer=None), spec, base, Budget(), tmp_path / "run", [], lambda *_: None, {})
    assert set(current.preset_drafts) == {"base_left", "base_right"}
    plain = current.snapshot()
    assert [item["source"] for item in plain["base"]["instances"]] == [{"kind": "draft", "draft_id": "base_left"}, {"kind": "draft", "draft_id": "base_right"}]
    assert "preset-source" not in json.dumps(plain) and "bundle" not in json.dumps(plain)
    coded = current.snapshot(with_code=True)["base"]["presets"]
    assert coded["base_left"]["code"] == "preset-source"
    assert coded["base_right"] == {"same_code_as": "base_left"}


def test_an_edit_composes_the_base_instances_as_listed(tmp_path):
    """The edit rule says to call sprite_compose with base.instances as its instances; they used to carry draft_id
    instead of source, so the call was refused before the edit could swap in its copy."""
    layout = {"x": 0, "y": 0, "width": 1080, "height": 1920, "z_index": 0}
    sprite = SpriteRecord.model_validate({
        "sprite_id": "saved-sprite", "created_at": "2026-09-30T00:00:00+00:00", "description": "暖色滤镜", "kind": "filter_overlay",
        "code": "bundle", "parameter_schema": {"type": "object"}, "default_parameters": {},
        "composition": {"width": 1080, "height": 1920, "fps": 30, "duration_frames": 30},
        "instances": [{
            "instance_id": "tint",
            "preset": {"description": "tint", "code": "export default function Tint(props: {opacity: number}) { return null; }",
                       "parameter_schema": {"type": "object", "properties": {"opacity": {"type": "number", "minimum": 0, "maximum": 1}},
                                            "required": ["opacity"], "additionalProperties": False},
                       "default_parameters": {"opacity": 0.4}},
            "parameters": {"opacity": 0.4}, "layout": layout, "timing": {"start_frame": 0, "duration_frames": 30},
        }],
    })
    spec = SimpleNamespace(sprite=sprite, composition=Composition(width=1080, height=1920, fps=30, duration_frames=30), sprite_kind="filter_overlay")
    base = TemplateCandidate(tsx_code="bundle", config_schema={"type": "object"}, default_config={})
    current = ToolSession(SimpleNamespace(settings=Settings(_env_file=None, data_dir=tmp_path), renderer=None), spec, base, Budget(), tmp_path / "run", [], lambda *_: None, {})
    patch_validation(current)
    listed = current.snapshot(with_code=True)["base"]

    async def run():
        """Compose the base exactly as the snapshot lists it, as an edit that changes nothing would."""
        return await current.execute("sprite.compose", SpriteComposeInput.model_validate({
            "description": listed["description"], "kind": listed["kind"], "parameters": listed["parameters"], "instances": listed["instances"],
        }), available())

    composed = asyncio.run(run())
    assert composed["instances"][0]["draft_id"] == "base_tint"
    assert composed["default_parameters"] == {"tint": {"opacity": 0.4}}


def test_a_large_edit_base_is_omitted_largest_first_to_fit_the_snapshot_budget(tmp_path):
    """审计复现：快照体积上限被整体删掉后，Executor 快照里的基线源码不受限制，大基线会让请求超过提供方的 512000 字节上限。"""
    from server.remotion_templates.tools.session import _SNAPSHOT_BYTE_BUDGET

    settings = Settings(_env_file=None, data_dir=tmp_path)
    harness = SimpleNamespace(settings=settings, renderer=None)
    sprite = saved_sprite("bundle", "b" * 190_000, ("big",))
    sprite.instances.append(saved_sprite("bundle", "s" * 30_000, ("small",)).instances[0])
    spec = SimpleNamespace(sprite=sprite, composition=Composition(width=1080, height=1920, fps=30, duration_frames=30), sprite_kind="filter_overlay")
    base = TemplateCandidate(tsx_code="bundle", config_schema={"type": "object"}, default_config={})
    current = ToolSession(harness, spec, base, Budget(), tmp_path / "run", [], lambda *_: None, {})
    snapshot = current.snapshot(with_code=True)
    assert len(json.dumps(snapshot, ensure_ascii=False).encode()) <= _SNAPSHOT_BYTE_BUDGET
    presets = snapshot["base"]["presets"]
    assert "code" not in presets["base_big"] and "190000 bytes" in presets["base_big"]["code_omitted"]
    assert presets["base_big"]["parameter_schema"] and presets["base_small"]["code"] == "s" * 30_000
    # The handle still composes: the draft itself keeps its source.
    assert current.preset_drafts["base_big"].code == "b" * 190_000
    # A small base keeps every source, and a large legacy component is omitted the same way.
    small_spec = SimpleNamespace(sprite=saved_sprite("bundle", "x" * 1000), composition=spec.composition, sprite_kind="filter_overlay")
    small = ToolSession(harness, small_spec, base, Budget(), tmp_path / "small", [], lambda *_: None, {})
    assert small.snapshot(with_code=True)["base"]["presets"]["base_title"]["code"] == "x" * 1000
    # A legacy component holds at most 100000 characters, which only multi-byte text pushes past the budget.
    legacy = TemplateCandidate(tsx_code="灯" * 100_000, config_schema={"type": "object"}, default_config={})
    old = ToolSession(harness, SimpleNamespace(sprite=None, composition=spec.composition, sprite_kind="filter_overlay"), legacy, Budget(), tmp_path / "legacy", [], lambda *_: None, {}).snapshot(with_code=True)
    assert "legacy_code" not in old["base"] and "300000 bytes" in old["base"]["legacy_code_omitted"]


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
        composed = await current.execute("sprite.compose", title_instance(preset["preset"]["preset_id"]), available())
        # compose returns a handle and a summary; the model never sees or re-sends the bundled source.
        assert "code" not in composed and composed["code_bytes"] > 0
        assert composed["kind"] == "filter_overlay" and composed["parameter_names"] == ["intensity", "tintColor"]
        assert composed["instances"][0]["preset_id"] == preset["preset"]["preset_id"]
        saved = await current.execute("sprite.create", SpriteCreateInput(draft_id=composed["draft_id"]), available())
        assert current.saved_sprite(saved["sprite_id"]).code == current.sprite_drafts[composed["draft_id"]].code
        assert len(current.saved_sprites) == 1

    asyncio.run(run())


def test_an_edit_can_modify_a_base_draft_by_its_handle(tmp_path):
    """真实回放：编辑任务里模型直接对 base_* 草稿调用 preset_modify；按句柄复制，原草稿不变，未知 ID 的提示也列出草稿句柄。"""
    settings = Settings(_env_file=None, data_dir=tmp_path)
    spec = SimpleNamespace(sprite=saved_sprite("bundle", "export default function Label(){return <div>Title</div>}"), composition=Composition(width=1080, height=1920, fps=30, duration_frames=30), sprite_kind="filter_overlay")
    base = TemplateCandidate(tsx_code="bundle", config_schema={"type": "object"}, default_config={})
    current = ToolSession(SimpleNamespace(settings=settings, renderer=None), spec, base, Budget(), tmp_path / "run", [], lambda *_: None, {})
    copy = current.modify_preset(PresetModifyInput(preset_id="base_title", changes={"description": "更淡"}))
    assert current.preset_drafts[copy.draft_id].description == "更淡"
    assert current.preset_drafts[copy.draft_id].code == "export default function Label(){return <div>Title</div>}"
    assert current.preset_drafts["base_title"].description == "title"
    assert "source_preset_id" not in copy.model_dump(exclude_unset=True)["preset"]
    with pytest.raises(ToolFault) as missing:
        current.modify_preset(PresetModifyInput(preset_id="base_unknown", changes={"description": "x"}))
    assert "draft_ids in this task" in missing.value.error.message and "base_title" in missing.value.error.message
    # Reading a draft handle as a stored Preset (seen in the replay) says where its source is and how to use it.
    with pytest.raises(ToolFault) as draft_read:
        current.search_presets(PresetSearchInput(preset_id="base_title"))
    assert "is a draft of this task" in draft_read.value.error.message and "base.presets" in draft_read.value.error.message


def test_draft_receipts_carry_a_digest_of_their_content(tmp_path):
    """同样的修改每次得到新的 draft_id，但内容摘要相同；内容一变摘要就变，空转判断据此区分重复与修改。"""
    settings = Settings(_env_file=None, data_dir=tmp_path)
    spec = SimpleNamespace(sprite=saved_sprite("bundle", "export default function Label(){return <div>Title</div>}"), composition=Composition(width=1080, height=1920, fps=30, duration_frames=30), sprite_kind="filter_overlay")
    base = TemplateCandidate(tsx_code="bundle", config_schema={"type": "object"}, default_config={})
    current = ToolSession(SimpleNamespace(settings=settings, renderer=None), spec, base, Budget(), tmp_path / "run", [], lambda *_: None, {})
    first = current.modify_preset(PresetModifyInput(preset_id="base_title", changes={"description": "更淡"}))
    again = current.modify_preset(PresetModifyInput(preset_id="base_title", changes={"description": "更淡"}))
    other = current.modify_preset(PresetModifyInput(preset_id="base_title", changes={"code": "export default function Label(){return <div>Other</div>}"}))
    assert first.draft_id != again.draft_id
    assert first.digest == again.digest
    assert other.digest != first.digest


# The accepted title of work a1cccb1c (2026-10-19): text with a yellow underline drawn under it.
UNDERLINED_TITLE = """import React from "react";
// Title with a yellow underline.
export default function TitleUnderline(props: {text: string}) {
  return (
    <div style={{textAlign: "center"}}>
      <span>{props.text}</span>
      <div style={{height: 14, backgroundColor: "#FFD400"}} />
    </div>
  );
}
"""


def edit_session(tmp_path: Path, preset_code: str) -> ToolSession:
    """An edit task whose accepted version has one instance, title, registered as the base_title draft."""
    settings = Settings(_env_file=None, data_dir=tmp_path)
    spec = SimpleNamespace(sprite=saved_sprite("bundle", preset_code), composition=Composition(width=1080, height=1920, fps=30, duration_frames=30), sprite_kind="text")
    base = TemplateCandidate(tsx_code="bundle", config_schema={"type": "object"}, default_config={})
    return ToolSession(SimpleNamespace(settings=settings, renderer=None), spec, base, Budget(), tmp_path / "run", [], lambda *_: None, {})


def test_an_edit_replaces_only_the_lines_it_names(tmp_path):
    """回放 a1cccb1c「去掉黄色下划线」：模型曾整份重写 214 行并顺手改了关键词高亮；edits 只删下划线那一行，其余源码逐字保留，回执给出改动行数。"""
    current = edit_session(tmp_path, UNDERLINED_TITLE)
    copy = current.modify_preset(PresetModifyInput(preset_id="base_title", changes={"edits": [
        {"old_str": '      <div style={{height: 14, backgroundColor: "#FFD400"}} />\n', "new_str": ""},
        {"old_str": "// Title with a yellow underline.", "new_str": "// Title without an underline."},
    ]}))
    expected = UNDERLINED_TITLE.replace('      <div style={{height: 14, backgroundColor: "#FFD400"}} />\n', "").replace(
        "with a yellow underline", "without an underline")
    assert current.preset_drafts[copy.draft_id].code == expected
    assert (copy.code_lines_removed, copy.code_lines_added) == (2, 1)
    assert "notice" not in copy.model_dump(exclude_unset=True)
    # The accepted version's draft is untouched; the copy is a new handle.
    assert current.preset_drafts["base_title"].code == UNDERLINED_TITLE


@pytest.mark.parametrize("changes, field, message", [
    ({"edits": [{"old_str": '<div style={{height:14, backgroundColor: "#FFD400"}} />', "new_str": ""}]},
     "/changes/edits/0/old_str", "does not occur in the code"),
    ({"edits": [{"old_str": "  return (\n<div", "new_str": ""}]},
     "/changes/edits/0/old_str", "Its first line matches line 4"),
    ({"edits": [{"old_str": "    </div>\n  );", "new_str": ""}, {"old_str": "    </div>\n  );", "new_str": ""}]},
     "/changes/edits/1/old_str", "after the earlier edits"),
    ({"edits": [{"old_str": "div", "new_str": "section"}]},
     "/changes/edits/0/old_str", "occurs 3 times (lines 5, 7, 8)"),
    ({"edits": [{"old_str": "Title", "new_str": "Label"}], "code": "export default function Label(){return null}"},
     "/changes/edits", "not both"),
])
def test_edits_must_name_one_exact_place(tmp_path, changes, field, message):
    """old_str 必须原样出现恰好一次：找不到（含缩进不同时指出首行所在行）、出现多次（列出行号）或与整份 code 同时提供都拒绝，且不留下副本。"""
    current = edit_session(tmp_path, UNDERLINED_TITLE)
    with pytest.raises(ToolFault) as refused:
        current.modify_preset(PresetModifyInput(preset_id="base_title", changes=changes))
    assert refused.value.error.code == "INVALID_ARGUMENT" and refused.value.error.field == field
    assert message in refused.value.error.message
    assert list(current.preset_drafts) == ["base_title"]


def test_an_edit_that_copies_a_stored_preset_is_noted(tmp_path):
    """回放 a1cccb1c「去掉所有下划线」：模型从第一版的库内预设复制，把已删掉的下划线又带了回来；回执提示它应复制 base_* 草稿，复制基线草稿或新作品复制库内预设则不提示。"""
    current = edit_session(tmp_path, UNDERLINED_TITLE)
    stored = PresetRecord(preset_id="e64dfa", created_at="2026-10-19T00:00:00+00:00", description="第一版标题",
                          code=UNDERLINED_TITLE, parameter_schema={"type": "object", "properties": {}, "additionalProperties": False}, default_parameters={})
    current.catalog.append_preset(stored)
    noted = current.modify_preset(PresetModifyInput(preset_id="e64dfa", changes={"description": "去掉下划线"}))
    assert "not one the accepted version is built from (base_title)" in noted.notice
    assert "notice" not in current.modify_preset(PresetModifyInput(preset_id="base_title", changes={"description": "去掉下划线"})).model_dump(exclude_unset=True)
    fresh = session(tmp_path)
    assert "notice" not in fresh.modify_preset(PresetModifyInput(preset_id="e64dfa", changes={"description": "副本"})).model_dump(exclude_unset=True)


@pytest.mark.skipif(not RENDERER_DEPS.exists(), reason="install locked Remotion renderer dependencies")
def test_fixed_tier_parameters_carry_the_host_titles(tmp_path):
    """宿主固定名称的参数（这里是双栏的上栏颜色）在参数面板里显示宿主固定的中文标题，不随 Agent 的措辞变化；其他参数保留 Agent 给的标题。"""
    async def run():
        """Compose a Preset whose line1Color the Agent titled 颜色一."""
        current = session(tmp_path)
        patch_validation(current)
        properties = {**FILTER_SCHEMA["properties"], "line1Color": FILTER_SCHEMA["properties"]["tintColor"]}
        tiered = draft().model_copy(update={
            "parameter_schema": {**FILTER_SCHEMA, "properties": properties, "required": list(properties)},
            "default_parameters": {**draft().default_parameters, "line1Color": "#FFFFFF"},
        })
        preset = await current.execute("preset.create", tiered, available())
        request = title_instance(preset["preset"]["preset_id"]).model_dump(mode="json", exclude_none=True)
        request["parameters"].append({"name": "line1Color", "title": "颜色一", "targets": [{"instance_id": "title", "parameter": "line1Color"}]})
        composed = await current.execute("sprite.compose", SpriteComposeInput.model_validate(request), available())
        sprite = current.sprite_drafts[composed["draft_id"]]
        assert sprite.parameter_schema["properties"]["line1Color"]["title"] == "上栏颜色"
        assert sprite.parameter_schema["properties"]["tintColor"]["title"] == "色调"
        assert {item.name: item.title for item in sprite.bindings} == {"intensity": "强度", "tintColor": "色调", "line1Color": "上栏颜色"}

    asyncio.run(run())


@pytest.mark.skipif(not RENDERER_DEPS.exists(), reason="install locked Remotion renderer dependencies")
def test_repeated_compose_returns_the_same_digest_under_a_new_handle(tmp_path):
    """重复同一组合：draft_id 不同、digest 相同；换一个参数 digest 就变。"""
    async def run():
        current = session(tmp_path)
        patch_validation(current)
        preset = await current.execute("preset.create", draft(), available())
        first = await current.execute("sprite.compose", title_instance(preset["preset"]["preset_id"]), available())
        again = await current.execute("sprite.compose", title_instance(preset["preset"]["preset_id"]), available())
        changed = title_instance(preset["preset"]["preset_id"]).model_dump(mode="json", exclude_unset=True)
        changed["instances"][0]["timing"]["duration_frames"] = 60
        moved = await current.execute("sprite.compose", SpriteComposeInput.model_validate(changed), available())
        assert first["draft_id"] != again["draft_id"]
        assert first["digest"] == again["digest"]
        assert moved["digest"] != first["digest"]

    asyncio.run(run())


@pytest.mark.skipif(not RENDERER_DEPS.exists(), reason="install locked Remotion renderer dependencies")
def test_a_new_work_is_composed_exactly_as_long_as_the_user_chose(tmp_path):
    """审计复现：用户选的时长只随请求存下，Agent 不知道也没有检查，选 10 秒也可能得到 1 秒；新作品现在必须恰好等于所选帧数。"""
    async def run():
        """Refuse a 30-frame Sprite for a 60-frame request, then accept the retimed one."""
        settings = Settings(_env_file=None, data_dir=tmp_path)
        intent = {"original_request": {"description": "标题", "composition": {"width": 1080, "height": 1920, "fps": 30, "duration_in_frames": 60}}}
        current = ToolSession(SimpleNamespace(settings=settings, renderer=None), None, None, Budget(), tmp_path / "run", [], lambda *_: None, intent)
        patch_validation(current)
        assert current.snapshot()["target_duration_frames"] == 60
        preset = await current.execute("preset.create", draft(), available())
        request = title_instance(preset["preset"]["preset_id"])
        with pytest.raises(ToolFault) as refused:
            await current.execute("sprite.compose", request, available())
        assert refused.value.error.field == "/instances"
        assert "60 frames (2 s)" in refused.value.error.message and "ends at frame 30" in refused.value.error.message
        retimed = request.model_dump(mode="json", exclude_unset=True)
        retimed["instances"][0]["timing"]["duration_frames"] = 60
        composed = await current.execute("sprite.compose", SpriteComposeInput.model_validate(retimed), available())
        assert composed["composition"]["duration_frames"] == 60

    asyncio.run(run())


def test_an_edit_keeps_deriving_its_length_from_the_instances(tmp_path):
    """编辑以已验收版本为基础：原始请求里的时长不再约束，快照也不给出目标帧数。"""
    settings = Settings(_env_file=None, data_dir=tmp_path)
    spec = SimpleNamespace(sprite=saved_sprite("bundle", "preset-source"), composition=Composition(width=1080, height=1920, fps=30, duration_frames=30), sprite_kind="filter_overlay")
    base = TemplateCandidate(tsx_code="bundle", config_schema={"type": "object"}, default_config={})
    intent = {"original_request": {"composition": {"width": 1080, "height": 1920, "fps": 30, "duration_in_frames": 150}}}
    current = ToolSession(SimpleNamespace(settings=settings, renderer=None), spec, base, Budget(), tmp_path / "run", [], lambda *_: None, intent)
    assert current.target_frames is None
    assert "target_duration_frames" not in current.snapshot()


def test_create_checks_each_preset_source_once_and_does_not_bundle_again(tmp_path, monkeypatch):
    """审计复现：create 收到的是宿主自己组合的草稿，却再打包一次来比对，并对同一预设的每个实例都跑一次沙箱类型检查；现在每种源码只查一次。"""
    async def run():
        current = session(tmp_path)
        patch_validation(current)
        checked = []

        async def validate(component):
            """Record which source the isolated validator was asked to check."""
            checked.append(component.code)
            return valid_report()

        def bundle_again(_instances):
            """The draft was bundled by compose already; a second bundle is wasted work."""
            raise AssertionError("sprite.create bundled the host draft again")

        current.validate_code = validate
        monkeypatch.setattr("server.remotion_templates.tools.session.compose_source", bundle_again)
        record = saved_sprite("bundle", "shared", ("a", "b", "c"))
        record.instances.append(saved_sprite("bundle", "other", ("d",)).instances[0])
        current.sprite_drafts["sprite_draft_1"] = categorized(record)
        saved = await current.execute("sprite.create", SpriteCreateInput(draft_id="sprite_draft_1"), available())
        assert checked == ["shared", "other", "bundle"]
        assert current.saved_sprite(saved["sprite_id"]).code == "bundle"

    asyncio.run(run())


def test_refused_code_lists_located_errors_with_their_lines_and_fixed_hints(tmp_path):
    """create 校验失败时，拒绝文本逐条给出位置、错误码与所在行源码，最多五条；常见错误附宿主固定的修复说明。"""
    def lsp(line, code="2322", message="Type 'string' is not assignable to type 'number'."):
        """One located TypeScript error in the checked component file."""
        return {"source": "lsp", "severity": "error", "message": message, "file": "Template.tsx", "code": code,
                "range": {"start": {"line": line, "character": 8}, "end": {"line": line, "character": 9}},
                "excerpt": f"const n{line}: number = props.t;"}

    async def run():
        current = session(tmp_path)

        async def failing(_component):
            """Six type errors and one warning, as the isolated validator would report them."""
            warning = {"source": "lsp", "severity": "warning", "message": "unused", "file": "Template.tsx"}
            return CodeValidationReport.model_validate({"passed": False, "diagnostics": [lsp(line) for line in range(2, 8)] + [warning]})

        current.validate_code = failing
        with pytest.raises(ToolFault) as refused:
            await current.execute("preset.create", draft(), available())
        error = refused.value.error
        lines = error.message.splitlines()
        assert lines[0] == "Preset code validation failed."
        assert lines[1] == "- Template.tsx 3:9 2322: Type 'string' is not assignable to type 'number'."
        assert lines[2] == "  > const n2: number = props.t;"
        assert lines[-1] == "- and 1 more in details.validation" and "unused" not in error.message
        assert len(error.details["validation"]["diagnostics"]) == 7
        assert [hint.split(":")[0] for hint in error.details["hints"]] == ["Props must match parameter_schema"]
        assert current.catalog.read_presets() == []

    asyncio.run(run())


def test_invalid_instance_parameters_name_the_instance_field(tmp_path):
    """实例参数不合 Schema 时，错误指向 /instances/N/parameters 并说明原因，而不是只说参数无效。"""
    async def run():
        current = session(tmp_path)
        patch_validation(current)
        stored = await current.execute("preset.create", PresetDraft(
            description="text", code="export default function Label(props: {text: string}){return <div>{props.text}</div>}",
            parameter_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False},
            default_parameters={"text": "默认"},
        ), available())
        request = title_instance(stored["preset"]["preset_id"]).model_dump(mode="json", exclude_unset=True)
        request["instances"][0]["parameters"] = {"text": 42}
        with pytest.raises(ToolFault) as refused:
            await current.execute("sprite.compose", SpriteComposeInput.model_validate(request), available())
        assert refused.value.error.field == "/instances/0/parameters"
        assert refused.value.error.message.startswith("Invalid parameters for title: ")

    asyncio.run(run())


def test_unknown_draft_handle_names_the_task_drafts(tmp_path):
    """sprite.create 只接收本任务 compose 产生的句柄；未知句柄给出字段位置和已有句柄，便于下一次调用改正。"""
    async def run():
        current = session(tmp_path)
        with pytest.raises(ToolFault) as unknown:
            await current.execute("sprite.create", SpriteCreateInput(draft_id="made-up"), available())
        assert unknown.value.error.code == "INVALID_ARGUMENT"
        assert unknown.value.error.field == "/draft_id"
        assert "drafts in this task: none" in unknown.value.error.message

    asyncio.run(run())


@pytest.mark.skipif(not RENDERER_DEPS.exists(), reason="install locked Remotion renderer dependencies")
def test_modified_copy_composes_by_handle_and_a_shared_preset_is_bundled_once(tmp_path):
    """回放 bb0decec：同一预设的多个实例只打包一份模块；modify 副本按 draft_id 参与组合，各实例参数独立。"""
    marker = "SharedPresetMarker"
    shared = PresetDraft(
        description="text",
        code=f"export default function Label(props: {{text: string}}){{return <div>{marker}{{props.text}}</div>}}",
        parameter_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False},
        default_parameters={"text": "默认"},
    )

    async def run():
        current = session(tmp_path)
        patch_validation(current)
        stored = await current.execute("preset.create", shared, available())
        copy = await current.execute("preset.modify", PresetModifyInput(preset_id=stored["preset"]["preset_id"], changes={"description": "副本"}), available())
        layout = {"x": 0, "y": 0, "width": 1080, "height": 960, "z_index": 0}
        composed = await current.execute("sprite.compose", SpriteComposeInput.model_validate({
            "description": "两行文字",
            "kind": "video_overlay",
            "instances": [
                {"instance_id": "top", "source": {"kind": "stored", "preset_id": stored["preset"]["preset_id"]}, "parameters": {"text": "上"}, "layout": layout, "timing": {"start_frame": 0, "duration_frames": 30}},
                {"instance_id": "bottom", "source": {"kind": "draft", "draft_id": copy["draft_id"]}, "parameters": {"text": "下"}, "layout": {**layout, "y": 960}, "timing": {"start_frame": 0, "duration_frames": 30}},
            ],
        }), available())
        assert composed["instances"][1]["draft_id"] == copy["draft_id"]
        assert composed["default_parameters"] == {"top": {"text": "上"}, "bottom": {"text": "下"}}
        assert current.sprite_drafts[composed["draft_id"]].code.count(marker) == 1

    asyncio.run(run())


def title_instance(preset_id: str) -> SpriteComposeInput:
    """One full-canvas instance of a stored Preset, ready for sprite.compose."""
    return SpriteComposeInput.model_validate({
        "description": "title preview",
        "kind": "filter_overlay",
        "instances": [{
            "instance_id": "title",
            "source": {"kind": "stored", "preset_id": preset_id},
            "layout": {"x": 0, "y": 0, "width": 1080, "height": 1920, "z_index": 0},
            "timing": {"start_frame": 0, "duration_frames": 30},
        }],
        "parameters": FILTER_BINDINGS,
    })


@pytest.mark.skipif(not RENDERER_DEPS.exists(), reason="install locked Remotion renderer dependencies")
def test_sprite_that_hides_the_video_is_refused_with_an_actionable_reason(tmp_path):
    """滤镜与特效叠在用户视频上：整帧不透明的 Sprite 在保存前被拒绝，原因写进回执，且不落库。"""
    reason = "The Sprite hides the video: at least 98% of the 1080x1920 canvas is opaque in 4 of the 5 sampled frames."

    async def run():
        """Refuse a Sprite the browser measured as opaque in most sampled frames, then check nothing was stored."""
        current = session(tmp_path)
        patch_validation(current)

        async def opaque(request, **_options):
            """The browser measured an opaque canvas in most sampled frames."""
            return render_report(request.duration_frames, CheckResult(name="transparency", status="failed", message=reason))

        current.validate_render = opaque
        preset = await current.execute("preset.create", draft(), available())
        composed = await current.execute("sprite.compose", title_instance(preset["preset"]["preset_id"]), available())
        with pytest.raises(ToolFault) as refused:
            await current.execute("sprite.create", SpriteCreateInput(draft_id=composed["draft_id"]), available())
        assert refused.value.error.code == "CODE_VALIDATION_FAILED"
        messages = [item["message"] for item in refused.value.error.details["validation"]["diagnostics"]]
        assert messages == [f"transparency: {reason}"]
        assert current.saved_sprites == {}

    asyncio.run(run())


@pytest.mark.skipif(not RENDERER_DEPS.exists(), reason="install locked Remotion renderer dependencies")
def test_creation_mounts_the_sprite_once_and_completion_reuses_that_report(tmp_path):
    """sprite.create 带透明度度量挂载一次；宿主完成时读取同一份缓存，不再启动第二次浏览器。"""
    calls = []

    class Validator:
        """Record how the Sprite was mounted; code validation is not needed here."""

        async def validate_render(self, request, **options):
            """Pass, remembering whether the transparency measurement was requested."""
            calls.append(options.get("transparency", False))
            return render_report(request.duration_frames, CheckResult(name="transparency", status="passed"))

    async def run():
        """Create a Sprite through the real session method, then read back the report the host's final check uses."""
        current = session(tmp_path)
        patch_validation(current)
        del current.validate_render  # use the real session method so the report is cached
        current.validator = Validator()
        preset = await current.execute("preset.create", draft(), available())
        composed = await current.execute("sprite.compose", title_instance(preset["preset"]["preset_id"]), available())
        saved = await current.execute("sprite.create", SpriteCreateInput(draft_id=composed["draft_id"]), available())
        record = current.saved_sprite(saved["sprite_id"])
        assert calls == [True]
        assert current.validation_for(record) is not None and current.validation_for(record).passed

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
        composed = await current.execute("sprite.compose", title_instance(preset["preset"]["preset_id"]), available())
        saved = await current.execute("sprite.create", SpriteCreateInput(draft_id=composed["draft_id"]), available())
        result = await harness.finalize_sprite(current, saved["sprite_id"], Budget(), tmp_path)
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
    """preset.create 经目录存储写入共享数据库，可按 ID 读回；数据目录不再保存 catalog/ 文件。"""
    async def run():
        """用仅创建模式的会话保存预设，再从同一目录重新读取。"""
        current = session(tmp_path)
        patch_validation(current)
        saved = await current.execute("preset.create", draft(), available())
        assert current.catalog_backend == "mysql"
        assert current.catalog.find_preset(saved["preset"]["preset_id"]).description == "title"
        assert not (tmp_path / "catalog").exists()

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


def test_missing_sandbox_is_a_validation_error_not_a_store_failure(tmp_path):
    """worker 因缺少 bwrap 无法启动时，preset.create 返回 VALIDATION_UNAVAILABLE 且不落库。

    修复前 ValidationUnavailable 落入 preset_create 的兜底分支，被报成
    PRESET_STORE_FAILED，用户只看到“预设保存失败”，看不出是校验环境缺失。
    """
    class MissingSandboxRenderer:
        """复现 macOS：启动 worker 时 create_subprocess_exec 找不到 bwrap。"""

        def worker_browser_path(self):
            """单元测试不启动浏览器，只返回一个固定路径。"""
            return "/usr/bin/chromium"

        async def run_worker(self, directory, *, worker, timeout_seconds=None):
            """与真实 Renderer.run_worker 相同的异常。"""
            raise FileNotFoundError(2, "No such file or directory", "bwrap")

    class RecordingCatalog:
        """记录入库调用，断言校验不可用时没有写入。"""

        def __init__(self):
            """保存本次用例实际入库的记录。"""
            self.records: list[PresetRecord] = []

        def append_preset(self, record):
            """返回本地后端标记，避免测试连接数据库。"""
            self.records.append(record)
            return "local"

    settings = Settings(_env_file=None, data_dir=tmp_path)
    catalog = RecordingCatalog()
    current = ToolSession(
        SimpleNamespace(settings=settings, renderer=MissingSandboxRenderer()),
        None, None, Budget(), tmp_path / "run", [], lambda *_: None, {},
    )
    current.catalog = catalog
    with pytest.raises(ToolFault) as failure:
        asyncio.run(current.execute("preset.create", draft(), available()))
    assert failure.value.error.code == "VALIDATION_UNAVAILABLE"
    assert "bwrap" in failure.value.error.message
    assert catalog.records == []


@pytest.mark.parametrize(
    ("platform", "present", "expected"),
    [
        ("darwin", {"bwrap", "prlimit", "node"}, "darwin"),
        ("linux", {"prlimit", "node"}, "bwrap"),
        ("linux", {"bwrap", "node"}, "prlimit"),
        ("linux", {"bwrap", "prlimit"}, "node"),
        ("linux", {"bwrap", "prlimit", "node"}, None),
    ],
)
def test_sandbox_problem_names_the_unsupported_piece(monkeypatch, tmp_path, platform, present, expected):
    """非 Linux 先报平台；Linux 逐项报缺少的工具；全部具备时返回 None，保持原流程。"""
    from server.remotion_templates import renderer as renderer_module

    monkeypatch.setattr(renderer_module.sys, "platform", platform)
    monkeypatch.setattr(renderer_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name in present else None)
    problem = renderer_module.Renderer(Settings(_env_file=None, data_dir=tmp_path)).sandbox_problem()
    assert (problem is None) if expected is None else (expected in problem)


def test_validation_unavailable_public_message_is_generic():
    """同一错误码也覆盖 worker 报告无效、超时等原因，公开文案不指向某个具体缺失，也不暴露异常原文。"""
    from server.remotion_templates.progress import RoundCall

    call = RoundCall(tool="preset.create", status="fail", error_code="VALIDATION_UNAVAILABLE", message="[Errno 2] 'bwrap'")
    assert call.error_code == "VALIDATION_UNAVAILABLE"
    assert "校验环境" in call.message and "Linux" not in call.message and "bubblewrap" not in call.message
    assert "Errno" not in call.message


def test_macos_sandbox_problem_mentions_the_development_switch(monkeypatch, tmp_path):
    """macOS 未开启免沙箱时，原因里直接给出可用的开发开关；其他系统不提这个开关。"""
    from server.remotion_templates import renderer as renderer_module

    renderer = renderer_module.Renderer(Settings(_env_file=None, data_dir=tmp_path))
    for platform, mentioned in (("darwin", True), ("win32", False)):
        monkeypatch.setattr(renderer_module.sys, "platform", platform)
        assert ("IMV_ALLOW_UNSANDBOXED_WORKER" in renderer.sandbox_problem()) is mentioned


@pytest.mark.parametrize(
    ("platform", "allowed", "isolated"),
    [("darwin", True, False), ("darwin", False, True), ("linux", True, True), ("win32", True, True)],
)
def test_unsandboxed_worker_is_macos_development_opt_in_only(monkeypatch, tmp_path, platform, allowed, isolated):
    """只有 macOS 且显式开启时生成流程的 worker 才以普通 Node 运行；其他系统即使开启也保持沙箱。"""
    from server.remotion_templates import renderer as renderer_module

    monkeypatch.setattr(renderer_module.sys, "platform", platform)
    monkeypatch.setattr(renderer_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    settings = Settings(_env_file=None, data_dir=tmp_path, allow_unsandboxed_worker=allowed)
    renderer = renderer_module.Renderer(settings)
    for worker in renderer_module.UNSANDBOXED_WORKERS:
        command = renderer.command(tmp_path, worker=worker)
        environment = renderer.worker_environment(tmp_path, worker)
        if isolated:
            assert command[0] == "bwrap" and "IMV_WORK_ROOT" not in environment
        else:
            assert renderer.sandbox_problem() is None
            assert "bwrap" not in command and command[-1].endswith(worker)
            assert environment["IMV_WORK_ROOT"] == str(tmp_path.resolve())
            assert environment["IMV_RENDERER_ROOT"] == str(settings.renderer_dir.resolve())
            assert set(environment) == {"PATH", "HOME", "IMV_WORK_ROOT", "IMV_RENDERER_ROOT"}


def test_overlay_worker_keeps_the_sandbox_even_when_opted_out(monkeypatch, tmp_path):
    """叠加渲染（含重启后恢复任务）不在免沙箱名单内：即使开启开关，仍走 bwrap，环境变量也不放开路径。"""
    from server.remotion_templates import renderer as renderer_module

    monkeypatch.setattr(renderer_module.sys, "platform", "darwin")
    monkeypatch.setattr(renderer_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    renderer = renderer_module.Renderer(Settings(_env_file=None, data_dir=tmp_path, allow_unsandboxed_worker=True))
    assert "overlay-worker.mjs" not in renderer_module.UNSANDBOXED_WORKERS
    assert renderer.command(tmp_path, worker="overlay-worker.mjs")[0] == "bwrap"
    assert "IMV_WORK_ROOT" not in renderer.worker_environment(tmp_path, "overlay-worker.mjs")


def test_unsandboxed_macos_still_requires_node(monkeypatch, tmp_path):
    """免沙箱模式仍要求 Node；缺失时给出明确原因而不是启动失败。"""
    from server.remotion_templates import renderer as renderer_module

    monkeypatch.setattr(renderer_module.sys, "platform", "darwin")
    monkeypatch.setattr(renderer_module.shutil, "which", lambda name: None)
    renderer = renderer_module.Renderer(Settings(_env_file=None, data_dir=tmp_path, allow_unsandboxed_worker=True))
    assert renderer.sandbox_problem() == "node is not installed"


@pytest.mark.parametrize("name", ["preset.create", "sprite.create"])
def test_creation_tools_declare_validation_unavailable(name):
    """会启动校验器的两个创建工具在契约里声明 VALIDATION_UNAVAILABLE，tools.inspect 与实际行为一致。"""
    from server.remotion_templates.tools.registry import get_tool

    assert "VALIDATION_UNAVAILABLE" in get_tool(name).describe()["error_codes"]


def test_session_stays_importable_without_the_validation_module(monkeypatch, tmp_path):
    """部署可裁剪校验模块：session 仍可导入，构造时降级为无校验器，校验返回 VALIDATION_UNAVAILABLE。

    修复前 session 在模块顶层导入 tool_validation，模块缺失时导入阶段就失败，降级路径不可达。
    """
    import importlib
    import sys

    monkeypatch.setitem(sys.modules, "server.remotion_templates.tool_validation", None)
    monkeypatch.delitem(sys.modules, "server.remotion_templates.tools.session")
    module = importlib.import_module("server.remotion_templates.tools.session")
    settings = Settings(_env_file=None, data_dir=tmp_path)
    current = module.ToolSession(
        SimpleNamespace(settings=settings, renderer=None), None, None, Budget(), tmp_path / "run", [], lambda *_: None, {},
    )
    assert current.validator is None
    component = ComponentDefinition(**draft().model_dump(include={"code", "parameter_schema", "default_parameters"}))
    with pytest.raises(module.ToolFault) as failure:
        asyncio.run(current.validate_code(component))
    assert failure.value.error.code == "VALIDATION_UNAVAILABLE"
