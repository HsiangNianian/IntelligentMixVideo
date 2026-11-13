"""Historical text/box components open, edit and save through the single effect workspace.

Run: uv run --locked --project server pytest server/tests/test_remotion_legacy_workspace.py.
Fixtures are synthetic outputs frozen from PR #301 head 6faf98b before removing its composer.
The opt-in browser check compares real rendered pixels before/after conversion, without models or databases.
"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageChops

from server.remotion_templates.agent import AgentRun
from server.remotion_templates.categories import FONT_PARAMETERS
from server.remotion_templates.fonts import open_font_schema
from server.remotion_templates.models import CompositionConfig, TemplateCandidate, TemplateSpec
from server.remotion_templates.provider import Budget
from server.remotion_templates.renderer import Renderer
from server.remotion_templates.settings import Settings
from server.remotion_templates.tool_validation import FrameCapture, ToolValidator
from server.remotion_templates.tools.catalog import available
from server.remotion_templates.tools.contracts import ComponentDefinition, RenderValidationInput, SpriteDraft
from server.remotion_templates.tools.registry import ToolFault
from server.remotion_templates.tools.session import ToolSession
from server.remotion_templates.tools.workspace import CODE, MANIFEST, assemble, contract_probes, preview_keywords, restore_version_files
from .test_remotion_free_effects import BROWSER, FakeHarness, call, needs_browser, needs_bundler, report, stub_checks

FIXTURES = Path(__file__).parent / "fixtures" / "remotion_legacy"
NAMES = ["text-1", "text-2", "text-4", "box"]


def legacy_version(name):
    """Load an immutable historical output as the same spec/candidate pair Runtime hands the Agent."""
    sprite = SpriteDraft.model_validate_json((FIXTURES / f"{name}.json").read_text())
    spec = TemplateSpec(schema_version="2", name="历史测试", description=sprite.description, sprite_kind="composition", sprite=sprite,
        composition=CompositionConfig(width=1080, height=1920, fps=30, duration_in_frames=sprite.composition.duration_frames))
    candidate = TemplateCandidate(tsx_code=sprite.code, config_schema=sprite.parameter_schema, default_config=sprite.default_parameters)
    return spec, candidate


def session(tmp_path, spec, candidate, versions=None):
    """Create a real workspace with only the expensive isolated renderer replaced."""
    harness = SimpleNamespace(settings=Settings(_env_file=None, data_dir=tmp_path), renderer=None)
    return stub_checks(ToolSession(harness, spec, candidate, Budget(), tmp_path, [], lambda *_: None,
        {"accepted_base": {"name": spec.name}, "instruction": "只修改颜色"}, versions=versions))


@needs_bundler
@pytest.mark.parametrize("name", NAMES)
def test_conversion_preserves_source_contract_and_manual_parameters(tmp_path, name):
    """文字块、双栏、四行双语与实例布局均完整复制源码、参数契约和用户手动修改，不改写历史。"""
    spec, candidate = legacy_version(name)
    if name == "box":
        candidate.default_config["tint"]["detail"]["radius"] = 137
    else:
        candidate.default_config["fontSize"] = 110
    before = deepcopy((spec.model_dump(mode="json"), candidate.model_dump(mode="json")))
    current = session(tmp_path, spec, candidate)
    assert current.files[CODE] == candidate.tsx_code
    contract = json.loads(current.files[MANIFEST])["contract"]
    assert contract["default_parameters"] == candidate.default_config
    assert contract["parameter_schema"] == candidate.config_schema
    draft, notes = asyncio.run(current._assemble())
    assert not notes and draft.effect is not None and draft.text is None
    assert draft.default_parameters == candidate.default_config
    assert draft.parameter_schema == candidate.config_schema
    assert len(draft.instances) == 1 and draft.instances[0].layout.x == 0
    assert draft.composition.duration_frames == 90
    from server.remotion_templates.sprites import _content_box
    converted_spec = spec.model_copy(update={"sprite": draft})
    assert _content_box(SimpleNamespace(spec=converted_spec)) == _content_box(SimpleNamespace(spec=spec))
    assert (spec.model_dump(mode="json"), candidate.model_dump(mode="json")) == before
    if name != "box":
        assert preview_keywords(draft) == ["测试"]


@needs_bundler
def test_converted_effect_edits_saves_and_reopens(tmp_path):
    """转换后精确修改、保存、再次编辑及历史读取都使用同一工具；手动嵌套参数持续保留。"""
    spec, candidate = legacy_version("box")
    current = session(tmp_path, spec, candidate, [SimpleNamespace(number=1, spec=spec, candidate=candidate)])
    saved_before = deepcopy(candidate.default_config)
    asyncio.run(current.execute("workspace.edit", {"path": MANIFEST, "edits": [{"old_str": '"radius": 20', "new_str": '"radius": 83'}]}, available()))
    receipt = asyncio.run(current.execute("effect.save", {}, available()))
    saved = current.saved_sprite(receipt["sprite_id"])
    assert saved.default_parameters["tint"]["detail"]["radius"] == 83
    assert candidate.default_config == saved_before
    accepted = spec.model_copy(update={"sprite": saved})
    revised = candidate.model_copy(update={"tsx_code": saved.code, "config_schema": saved.parameter_schema, "default_config": saved.default_parameters})
    again = session(tmp_path / "again", accepted, revised)
    assert json.loads(again.files[MANIFEST])["contract"]["default_parameters"] == saved.default_parameters
    assert again.files[CODE] == current.files[CODE]
    original = asyncio.run(current.execute("workspace.read", {"path": MANIFEST, "version": 1}, available()))
    assert json.loads(original["content"])["contract"]["default_parameters"]["tint"]["detail"]["radius"] == 20
    assert current.snapshot()["versions"] == [{"number": 1, "description": spec.sprite.description}]


@needs_bundler
def test_converted_cue_keeps_its_layout_check_without_changing_playback(tmp_path):
    """下栏出现时移除上栏的旧 cue 保留测量开关；保存、重开及探针都不改实际默认值或源码。"""
    spec, candidate = legacy_version("text-cue")
    before = deepcopy(candidate.model_dump(mode="json"))
    current = session(tmp_path, spec, candidate)
    receipt = asyncio.run(current.execute("effect.save", {}, available()))
    saved = current.saved_sprite(receipt["sprite_id"])
    assert saved.effect["contract"]["text_fit_parameters"] == {"effect1": False}
    assert saved.default_parameters == candidate.default_config
    assert saved.default_parameters["effect1"] is True
    assert saved.default_parameters["lineStartFrames"] == [0, 60]
    options = current.calls[-1]
    assert options["text_fit_parameters"] == {"effect1": False}
    probes = options["effect_contract"]
    assert probes["layout"] == {"effect1": False} and probes["starts"] == [0, 0]
    assert probes["base"]["effect1"] is True and probes["base"]["lineStartFrames"] == [0, 50]
    accepted = spec.model_copy(update={"sprite": saved})
    revised = candidate.model_copy(update={"tsx_code": saved.code, "config_schema": saved.parameter_schema, "default_config": saved.default_parameters})
    again = session(tmp_path / "again", accepted, revised)
    assert again.files[CODE] == candidate.tsx_code
    checked = asyncio.run(again.execute("validate.check", {}, available()))
    assert checked["passed"], checked
    assert again.calls[-1]["text_fit_parameters"] == {"effect1": False}
    assert candidate.model_dump(mode="json") == before


@pytest.mark.parametrize("patch", [{"color": False}, {"effect1": False, "other": False}])
def test_converted_layout_check_cannot_disable_arbitrary_parameters(tmp_path, patch):
    """可编辑清单不能编造更多关闭开关来绕过内容验收，只认已验收历史版本的测量配置。"""
    spec, candidate = legacy_version("text-cue")
    current = session(tmp_path, spec, candidate)
    manifest = json.loads(current.files[MANIFEST])
    manifest["contract"]["text_fit_parameters"] = patch
    current.files[MANIFEST] = json.dumps(manifest)
    checked = asyncio.run(current.execute("validate.check", {}, available()))
    assert not checked["passed"] and "host-owned" in checked["problems"][0]["message"]
    assert not current.calls and not current.saved_sprites


@needs_bundler
def test_invalid_converted_contract_cannot_save(tmp_path):
    """转换不绕过参数校验，非法默认值不能产生候选或破坏已封存基线。"""
    spec, candidate = legacy_version("box")
    current = session(tmp_path, spec, candidate)
    manifest = json.loads(current.files[MANIFEST])
    manifest["contract"]["default_parameters"]["intensity"] = 3
    current.files[MANIFEST] = json.dumps(manifest)
    with pytest.raises(ToolFault, match="defaults do not match"):
        asyncio.run(current.execute("effect.save", {}, available()))
    assert not current.saved_sprites and candidate.default_config["intensity"] == 0.3


@needs_bundler
def test_converted_schema_references_and_open_fonts_survive_reopening(tmp_path):
    """历史 Schema 的本地引用、布尔子 Schema 与保存后开放的字体范围均保留，用户选的新字体可以继续编辑。"""
    spec, candidate = legacy_version("box")
    schema = deepcopy(candidate.config_schema)
    schema["$defs"] = {"detail": schema["properties"]["tint"]}
    schema["properties"]["tint"] = {"$ref": "#/$defs/detail"}
    schema["properties"]["extra"] = True
    schema["properties"]["fontFamily"] = {"type": "string", "enum": ["Old Font"]}
    candidate = candidate.model_copy(update={"config_schema": schema, "default_config": {**candidate.default_config, "extra": {}, "fontFamily": "Old Font"}})
    current = session(tmp_path, spec, candidate)
    draft, _ = asyncio.run(current._assemble())
    assert draft.parameter_schema == schema
    assert "tint" not in {item.name for item in draft.bindings}
    assert asyncio.run(current.execute("validate.check", {}, available()))["passed"]
    assert contract_probes(draft)["wiring"]
    opened = deepcopy(schema)
    opened["properties"]["fontFamily"] = {"type": "string", "maxLength": 100}
    draft = draft.model_copy(update={"parameter_schema": opened})
    revised = candidate.model_copy(update={"config_schema": opened, "default_config": {**candidate.default_config, "fontFamily": "New Font"}})
    current = session(tmp_path / "reopen", spec.model_copy(update={"sprite": draft}), revised)
    restored, _ = asyncio.run(current._assemble())
    assert restored.parameter_schema == opened
    assert restored.default_parameters["fontFamily"] == "New Font"


@needs_bundler
@pytest.mark.parametrize("name", ["text-1", "text-2", "text-4"])
@pytest.mark.parametrize("family", ["New Font", "Missing Font"])
def test_converted_open_fonts_use_the_live_library_without_changing_schema(tmp_path, monkeypatch, name, family):
    """已封存版本开放的字体字符串可选后上传的字体；不存在的字体仍拒绝，原 Schema 不被重新冻结。"""
    spec, candidate = legacy_version(name)
    schema = open_font_schema(candidate.config_schema)
    defaults = {**candidate.default_config, **{key: family for key in FONT_PARAMETERS if key in schema["properties"]}}
    candidate = candidate.model_copy(update={"config_schema": schema, "default_config": defaults})
    current = session(tmp_path, spec, candidate)
    monkeypatch.setattr(current, "_fonts", lambda: ["Noto Sans CJK SC", "New Font"])
    checked = asyncio.run(current.execute("validate.check", {}, available()))
    assert checked["passed"] is (family == "New Font"), checked
    assert json.loads(current.files[MANIFEST])["contract"]["parameter_schema"] == schema
    if family == "New Font":
        receipt = asyncio.run(current.execute("effect.save", {}, available()))
        assert current.saved_sprite(receipt["sprite_id"]).parameter_schema == schema
    else:
        assert any("FontFamily" in item["message"] or "fontFamily" in item["message"] for item in checked["problems"])
        assert not current.calls


@needs_bundler
def test_converted_category_error_points_to_the_preserved_contract(tmp_path):
    """旧作品仍通过同一分类门禁，修复提示定位 contract，不能误导模型另建 text/params。"""
    spec, candidate = legacy_version("box")
    current = session(tmp_path, spec, candidate)
    manifest = json.loads(current.files[MANIFEST])
    manifest["contract"]["parameter_schema"]["properties"]["intensity"]["maximum"] = 3
    current.files[MANIFEST] = json.dumps(manifest)
    with pytest.raises(ToolFault, match="contract.parameter_schema"):
        asyncio.run(current.execute("effect.save", {}, available()))
    assert not current.saved_sprites


def test_pre_sprite_source_is_preserved_and_history_is_read_only(tmp_path):
    """最早的单组件版本也有工作区，不退回旧 Agent，不给它替换成起始工程。"""
    spec = TemplateSpec(name="旧作品", description="单组件", composition=CompositionConfig(duration_in_frames=60),
        text_layers=[{"id": "title", "text": "测试", "end_frame": 60}])
    candidate = TemplateCandidate(tsx_code="export default function Old(){return null}",
        config_schema={"type": "object", "properties": {}, "additionalProperties": False}, default_config={})
    current = session(tmp_path, spec, candidate)
    assert current.files[CODE] == candidate.tsx_code
    assert current._duration() == 60
    assert current.snapshot()["base"]["description"] == "单组件"
    assert spec.sprite is None and json.loads(current.files[MANIFEST])["kind"] == "text"


@needs_bundler
def test_old_environment_switch_cannot_restore_the_retired_route(monkeypatch, tmp_path):
    """旧配置无论取何值，历史编辑都走工作区、保存、回看和统一发布门禁。"""
    spec, candidate = legacy_version("box")
    monkeypatch.setenv("IMV_REMOTION_AGENT", "presets")
    assert "remotion_agent" not in Settings.model_fields
    harness = FakeHarness([call("delegate", "tools_plan_execute", {"action": "delegate"}), call("save", "effect_save", {}), report("done")], tmp_path)
    harness.settings.remotion_agent = "presets"
    run = AgentRun(harness, spec, Budget(), tmp_path, [], lambda *_: None, base=candidate, intent={"accepted_base": {"name": "旧作品"}})
    stub_checks(run.session)
    result = asyncio.run(run.plan_execute())
    assert harness.published == [result["published"]]
    assert run.session.saved_sprite(result["published"]).effect["contract"]
    assert [step.id for step in run.state.plan.steps] == ["edit"]


@needs_browser
@pytest.mark.parametrize("name", [*NAMES, "text-cue"])
def test_real_conversion_preserves_rendered_pixels(tmp_path, name):
    """真实浏览器逐帧比较转换前后；验证没有二次位移、丢字、丢译文或嵌套参数变化。"""
    spec, candidate = legacy_version(name)
    files = restore_version_files(spec, candidate)
    duration = spec.composition.duration_in_frames
    frames = [0, duration // 2, duration - 1]
    draft, _ = assemble(files, fonts=["Noto Sans SC"], requested=set(), duration=duration)
    validator = ToolValidator(Renderer(Settings(_env_file=None, data_dir=tmp_path, **BROWSER)), tmp_path / "validator")
    captures = []
    for label, code in (("old", candidate.tsx_code), ("new", draft.code)):
        capture = FrameCapture(frames, {"x": 0, "y": 0, "width": 1080, "height": 1920}, 0.25, tmp_path / label)
        component = ComponentDefinition(code=code, parameter_schema=candidate.config_schema, default_parameters=candidate.default_config)
        result = asyncio.run(validator.validate_render(RenderValidationInput(component=component, duration_frames=duration), capture=capture))
        assert result.passed, result
        captures.append(capture.directory)
    for frame in frames:
        with Image.open(captures[0] / f"frame-{frame}.png") as old, Image.open(captures[1] / f"frame-{frame}.png") as new:
            assert ImageChops.difference(old.convert("RGB"), new.convert("RGB")).getbbox() is None


@needs_browser
@pytest.mark.parametrize("change", ["unchanged", "inverted", "unwired"])
def test_real_converted_cue_keeps_layout_guard_and_reports_unobserved_motion(tmp_path, change):
    """旧 cue 原样保存；反向隐藏待场文字仍拒绝，采样看不到参数变化只提示并允许用户查看结果。"""
    spec, candidate = legacy_version("text-cue")
    settings = Settings(_env_file=None, data_dir=tmp_path, **BROWSER)
    current = ToolSession(SimpleNamespace(settings=settings, renderer=Renderer(settings)), spec, candidate, Budget(),
        tmp_path / "run", [], lambda *_: None, {"instruction": "只修改颜色"})
    if change == "unchanged":
        receipt = asyncio.run(current.execute("effect.save", {}, available()))
        saved = current.saved_sprite(receipt["sprite_id"])
        assert saved.default_parameters == candidate.default_config
        assert current.files[CODE] == candidate.tsx_code
        assert receipt["review"]["frames"]
    else:
        original = "opacity: 1 - props.progress"
        assert current.files[CODE].count(original) == 1
        current.files[CODE] = current.files[CODE].replace(original, "opacity: props.progress" if change == "inverted" else "opacity: 1")
        checked = asyncio.run(current.execute("validate.check", {}, available()))
        if change == "inverted":
            assert not checked["passed"] and not current.saved_sprites
            messages = {problem["check"]: problem["message"] for problem in checked["problems"]}
            assert "cue effect stays at its start" in messages.get("text_fit", "")
        else:
            assert checked["passed"] and any("effect1" in note for note in checked["notes"]), checked
            saved = asyncio.run(current.execute("effect.save", {}, available()))
            assert saved["notes"] == checked["notes"] and saved["review"]["frames"]
