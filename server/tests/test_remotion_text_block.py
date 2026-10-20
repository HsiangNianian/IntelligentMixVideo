"""字效的文字组合：text / keyword 角色 Preset、宿主生成的配置参数、双栏与关键词组合、编辑快照与 Preset 搜索。

执行：在 server/ 下 `uv run --locked pytest tests/test_remotion_text_block.py -v`。组合需要锁定的 Remotion 依赖（esbuild）；
真实浏览器用例另需 `IMV_TEST_RENDERER=1` 与浏览器、字体路径。Preset 目录使用临时 SQLite 代替 MySQL。
"""

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from generated.imv.sprite.v1 import sprite_pb2 as pb

from server.remotion_templates.categories import HEX_COLOR, category_problems
from server.remotion_templates.fonts import FontStore
from server.remotion_templates.models import TemplateCandidate
from server.remotion_templates.provider import Budget
from server.remotion_templates import sprite_render
from server.remotion_templates.renderer import Renderer
from server.remotion_templates.settings import Settings
from server.remotion_templates.tools.catalog import available
from server.remotion_templates.tool_validation import ToolValidator
from server.remotion_templates.tools.compose import TEXT_BLOCK_ID, compose_text_source, text_configuration
from server.remotion_templates.tools.contracts import (
    CheckResult,
    CodeValidationReport,
    ComponentDefinition,
    Composition,
    PresetDraft,
    PresetSearchInput,
    RenderValidationInput,
    RenderValidationReport,
    SpriteComposeInput,
    SpriteCreateInput,
)
from server.remotion_templates.tools.registry import ToolFault
from server.remotion_templates.tools.schema import validate_component_contract
from server.remotion_templates.tools.session import ToolSession

RENDERER_DEPS = Path(__file__).parents[1] / "src" / "server" / "remotion" / "node_modules" / "esbuild"
needs_bundler = pytest.mark.skipif(not RENDERER_DEPS.exists(), reason="install locked Remotion renderer dependencies")
needs_browser = pytest.mark.skipif(
    os.environ.get("IMV_TEST_RENDERER") != "1" or shutil.which("node") is None or not RENDERER_DEPS.exists(),
    reason="set IMV_TEST_RENDERER=1 with Node and the locked Remotion dependencies for the Linux browser integration",
)
BROWSER = {
    name: Path(value)
    for name, value in (("browser_executable", os.environ.get("IMV_BROWSER_EXECUTABLE")), ("font_regular", os.environ.get("IMV_FONT_REGULAR")),
                        ("font_bold", os.environ.get("IMV_FONT_BOLD")))
    if value
}
COLOR = {"type": "string", "pattern": HEX_COLOR}
LIBRARY = ["Noto Sans CJK SC", "Zed Display"]

# 一栏文字：按宿主传入的片段逐段渲染，关键词片段交给宿主的 Keyword；自己只声明文字颜色这一个样式参数。
TIER_CODE = """import React from "react";
export default function Tier(props: {text: string; segments: {text: string; keyword: boolean}[]; Keyword: React.ComponentType<{text: string}>; fontFamily: string; fontWeight: number; fontSize: number; color: string}) {
  const {Keyword} = props;
  return <div style={{width: "100%", textAlign: "center", whiteSpace: "normal", wordBreak: "break-all", fontFamily: props.fontFamily, fontWeight: props.fontWeight, fontSize: props.fontSize, lineHeight: 1.2, color: props.color}}>{props.segments.map((segment, index) => segment.keyword ? <Keyword key={index} text={segment.text} /> : <span key={index}>{segment.text}</span>)}</div>;
}
"""
# 关键词命中：在文字流里原位发光，字体继承宿主包裹的样式。
KEYWORD_CODE = """export default function Glow(props: {text: string; glowColor: string}) {
  return <span style={{font: "inherit", textShadow: `0 0 12px ${props.glowColor}`}}>{props.text}</span>;
}
"""


def preset(role: str | None, code: str, prop: str, default: str = "#FFFFFF") -> PresetDraft:
    """A Preset of ``role`` whose only own parameter is the colour ``prop``."""
    return PresetDraft(
        description=f"{role} preset", code=code, default_parameters={prop: default},
        parameter_schema={"type": "object", "properties": {prop: COLOR}, "required": [prop], "additionalProperties": False},
        **({"role": role} if role else {}),
    )


@pytest.fixture(autouse=True)
def catalog_database(template_db):
    """Preset catalog reads and writes go to the temporary database standing in for MySQL."""
    return template_db


def session(tmp_path: Path, intent: dict | None = None, spec=None, base=None) -> ToolSession:
    """A session whose isolated checks pass, so composition and the category gate decide."""
    current = ToolSession(SimpleNamespace(settings=Settings(_env_file=None, data_dir=tmp_path), renderer=None), spec, base, Budget(),
                          tmp_path / "run", [], lambda *_: None, intent or {})

    async def valid(_component):
        """Code validation passes for every source."""
        return CodeValidationReport(passed=True, diagnostics=[])

    async def mounted(request, **_options):
        """The browser mount passes."""
        return RenderValidationReport(
            passed=True, composition=Composition(width=1080, height=1920, fps=30, duration_frames=request.duration_frames),
            code_validation=CodeValidationReport(passed=True, diagnostics=[]), checks=[CheckResult(name="transparency", status="passed")],
            tests=[], custom_tests_executed=0,
        )

    current.validate_code = valid
    current.validate_render = mounted
    return current


def recording(current: ToolSession) -> list:
    """Record every component the session type-checks, still passing them all."""
    seen = []
    checked = current.validate_code

    async def record(component):
        """Keep the component, then pass it."""
        seen.append(component)
        return await checked(component)

    current.validate_code = record
    return seen


async def stored(current: ToolSession) -> tuple[str, str]:
    """Store a text Preset and a keyword Preset; return their IDs."""
    tier = await current.execute("preset.create", preset("text", TIER_CODE, "color"), available())
    glow = await current.execute("preset.create", preset("keyword", KEYWORD_CODE, "glowColor", "#FFD54A"), available())
    return tier["preset"]["preset_id"], glow["preset"]["preset_id"]


def two_tiers(tier: str, glow: str | None, **text) -> SpriteComposeInput:
    """A two-tier text block over the stored ``tier`` Preset, with a keyword Preset when ``glow`` is given."""
    source = {"kind": "stored", "preset_id": tier}
    block = {
        "layout": {"x": 54, "y": 700, "width": 972, "height": 520}, "text": "判断一个人\n选择花字、滤镜和特效", "font_size": 96,
        "tiers": [
            {"instance_id": "upper", "source": source, "parameters": {"color": "#FFFFFF"}, "font_family": "Noto Sans CJK SC", "font_weight": 700, "size_ratio": 0.5},
            {"instance_id": "lower", "source": source, "parameters": {"color": "#F5B93C"}, "font_family": "Zed Display", "font_weight": 700, "size_ratio": 1},
        ],
        "line_gap": 24, "line_starts": [0, 12], "duration_frames": 30,
        **({"keyword": {"instance_id": "keyword", "source": {"kind": "stored", "preset_id": glow}}} if glow else {}),
        **text,
    }
    bindings = [
        {"name": "line1Color", "title": "颜色一", "targets": [{"instance_id": "upper", "parameter": "color"}]},
        {"name": "line2Color", "title": "颜色二", "targets": [{"instance_id": "lower", "parameter": "color"}]},
        *([{"name": "keywordEffectColor", "title": "发光", "targets": [{"instance_id": "keyword", "parameter": "glowColor"}]}] if glow else []),
    ]
    return SpriteComposeInput.model_validate({"description": "双栏标题", "kind": "text", "text": block, "parameters": bindings})


def test_the_host_owns_a_text_blocks_configuration():
    """单栏暴露 fontFamily/fontWeight，双栏暴露每栏的字体、字重与字号比例以及栏间距、行起点；都带文字、字号与七个关闭的关键词参数，
    有关键词 Preset 时另有默认开启的 keywordEffect；名称、Schema 与中文标题由宿主固定，字体枚举是字体库。"""
    one = {"text": "今日灵感", "font_size": 96, "tiers": [{"font_family": "Zed Display", "font_weight": 700}]}
    schemas, defaults = text_configuration(one, LIBRARY)
    assert list(schemas)[:4] == ["text", "fontSize", "fontFamily", "fontWeight"] and "lineGap" not in schemas and "keywordEffect" not in schemas
    assert schemas["fontFamily"] == {"type": "string", "enum": LIBRARY, "title": "字体"}
    assert defaults["fontFamily"] == "Zed Display" and defaults["keywords"] == [] and defaults["keywordSize"] == 0
    two = {"text": "上\n下", "font_size": 96, "line_gap": 24, "line_starts": [0, 12], "keyword": {},
           "tiers": [{"font_family": "Zed Display", "font_weight": 400, "size_ratio": 0.5}, {"font_family": "Noto Sans CJK SC", "font_weight": 700, "size_ratio": 1}]}
    schemas, defaults = text_configuration(two, LIBRARY)
    assert {"line1FontFamily", "line2FontWeight", "line1SizeRatio", "lineGap", "lineStartFrames", "keywordEffect"} <= set(schemas)
    assert not {"fontFamily", "fontWeight", "color"} & set(schemas)
    assert schemas["line1SizeRatio"]["title"] == "上栏字号比例" and schemas["lineGap"]["title"] == "栏间距"
    assert (defaults["line1SizeRatio"], defaults["line2FontFamily"], defaults["lineStartFrames"], defaults["keywordEffect"]) == (0.5, "Noto Sans CJK SC", [0, 12], True)


def test_role_presets_declare_only_their_own_style():
    """text 角色的 Preset 由宿主传入文字、片段、Keyword 与字体，parameter_schema 再声明 fontSize 等就被拒；没有角色的自由组件不受限。"""
    tier = preset("text", TIER_CODE, "color")
    assert validate_component_contract(tier) == []
    sized = tier.model_copy(update={"parameter_schema": {**tier.parameter_schema, "properties": {**tier.parameter_schema["properties"], "fontSize": {"type": "number", "minimum": 24, "maximum": 240}}},
                                    "default_parameters": {**tier.default_parameters, "fontSize": 96}})
    [problem] = validate_component_contract(sized)
    assert "fontSize" in problem.message and "role text" in problem.message
    assert validate_component_contract(sized.model_copy(update={"role": None})) == []


@needs_bundler
def test_a_two_tier_text_with_a_keyword_effect_composes_from_role_presets(tmp_path, monkeypatch):
    """双栏 + 关键词字效：同一个 text Preset 画上下两栏，keyword Preset 画命中词；宿主的配置参数排在前面并以文字块为绑定目标，
    样式参数是 Agent 绑定的颜色（标题由宿主固定）；记录文字块结构，生成路径的分类检查通过并保存。"""
    monkeypatch.setattr(FontStore, "active", SimpleNamespace(families=lambda: list(LIBRARY)))

    async def run():
        """Compose the block and save it."""
        current = session(tmp_path, {"original_request": {"description": "双栏", "two_tier": True, "keyword_effect": True}})
        tier, glow = await stored(current)
        composed = await current.execute("sprite.compose", two_tiers(tier, glow), available())
        sprite = current.sprite_drafts[composed["draft_id"]]
        names = list(sprite.parameter_schema["properties"])
        assert names[:3] == ["text", "fontSize", "line1FontFamily"] and names[-3:] == ["upper", "lower", "keyword"]
        assert sprite.default_parameters["keywordEffect"] is True and sprite.default_parameters["line2FontFamily"] == "Zed Display"
        host = [item for item in sprite.bindings if item.targets[0].instance_id == TEXT_BLOCK_ID]
        assert {item.name for item in host} >= {"text", "lineStartFrames", "keywords", "keywordEffect"}
        assert {item.name: item.title for item in sprite.bindings if item not in host} == {"line1Color": "上栏颜色", "line2Color": "下栏颜色", "keywordEffectColor": "发光"}
        assert sprite.text.tiers == ["upper", "lower"] and sprite.text.keyword == "keyword"
        assert 'data-imv-instance": "text"' in sprite.code and "data-imv-keyword" in sprite.code
        # Saving the version to the asset library later runs the publish-path check over the same parameters.
        assert category_problems("text", sprite.parameter_schema, {item.name for item in sprite.bindings}, sprite.default_parameters) == []
        checked = recording(current)
        saved = await current.execute("sprite.create", SpriteCreateInput(draft_id=composed["draft_id"]), available())
        assert current.saved_sprite(saved["sprite_id"]).text.tiers == ["upper", "lower"]
        # 审计复现（作品 f941b066）：保存时复查源 Preset 曾丢掉角色，text Preset 不带宿主 prop 做类型检查，18 次保存全部失败。
        assert [getattr(item, "role", None) for item in checked] == ["text", "keyword", None]

    asyncio.run(run())


@needs_bundler
@pytest.mark.parametrize(("change", "field", "message"), [
    ({"text": {"text": "只有一行"}}, "/text/text", "has 1 lines for 2 tiers"),
    ({"text": {"line_starts": [5, 12]}}, "/text/line_starts", "the first 0"),
    ({"text": {"line_starts": [0, 30]}}, "/text/line_starts", "start it before the end"),
    ({"text": {"duration_frames": 20}}, "/text/duration_frames", "set its duration_frames to exactly 30"),
    ({"text": {"tiers": "font"}}, "/text/tiers/1/font_family", "not in the font library"),
    ({"binding": "fontSize"}, "/parameters/0/name", "configuration parameter of the text block"),
    ({"role": None}, "/text/tiers/0/source", "role 'text'"),
    ({"instances": True}, "/instances", "text block alone"),
    ({"instance_id": "keywordEffect"}, "/text/keyword/instance_id", "not a parameter name of the text block"),
], ids=["lines", "starts", "late-start", "length", "font", "taken-name", "legacy-preset", "with-boxes", "taken-id"])
def test_a_wrong_text_block_is_refused_with_the_field(tmp_path, monkeypatch, change, field, message):
    """文字块不合约定时按字段拒绝：行数与栏数不符、行起点不从 0 开始、下栏到结束才出现（审计复现：曾被接受，预览按默认行起点播放时下栏从不显示）、
    时长不等于用户所选、字体不在字体库、样式参数占用配置名、
    栏用了没有 text 角色的 Preset、文字块之外还放了固定框实例、实例 ID 占用配置名（审计复现：关键词实例叫 keywordEffect 时，
    它的嵌套参数曾顶替宿主的 keywordEffect 开关，保存时才报一个 Agent 无法绑定的分类参数错误）。"""
    monkeypatch.setattr(FontStore, "active", SimpleNamespace(families=lambda: ["Noto Sans CJK SC"]))

    async def run():
        """Compose the block with one thing wrong."""
        current = session(tmp_path, {"original_request": {"composition": {"duration_in_frames": 30}}})
        tier, glow = await stored(current)
        if "role" in change:
            legacy = await current.execute("preset.create", preset(None, TIER_CODE.replace("Keyword: React.ComponentType<{text: string}>; ", ""), "color"), available())
            tier = legacy["preset"]["preset_id"]
        request = two_tiers(tier, glow).model_dump(mode="json", exclude_none=True)
        if change.get("text", {}).get("tiers") == "font":
            request["text"]["tiers"][1]["font_family"] = "Zed Display"
        elif "text" in change:
            request["text"].update(change["text"])
        if "binding" in change:
            request["parameters"][0]["name"] = change["binding"]
        if "instance_id" in change:
            request["text"]["keyword"]["instance_id"] = request["parameters"][2]["targets"][0]["instance_id"] = change["instance_id"]
        if "instances" in change:
            request["instances"] = [{"instance_id": "box", "source": {"kind": "stored", "preset_id": tier}, "layout": {"x": 0, "y": 0, "width": 10, "height": 10, "z_index": 0},
                                     "timing": {"start_frame": 0, "duration_frames": 30}}]
        if change.get("text", {}).get("tiers") != "font":
            request["text"]["tiers"][1]["font_family"] = "Noto Sans CJK SC"
        with pytest.raises(ToolFault) as refused:
            await current.execute("sprite.compose", SpriteComposeInput.model_validate(request), available())
        assert refused.value.error.code == "INVALID_ARGUMENT" and refused.value.error.field == field
        assert message in refused.value.error.message

    asyncio.run(run())


@needs_bundler
def test_new_chat_switches_require_their_combination(tmp_path):
    """新会话打开「双栏」时文字块必须两栏，打开「关键词字效」时必须带关键词 Preset；新作品的文字效果也不能再按固定框组合。"""
    async def run():
        """Compose one tier without a keyword Preset in works whose user switched each option on."""
        for name, field in (("two_tier", "/text/tiers"), ("keyword_effect", "/text/keyword")):
            current = session(tmp_path / name, {"original_request": {"description": "标题", name: True}})
            tier, _ = await stored(current)
            one = {"description": "标题", "kind": "text", "text": {
                "layout": {"x": 54, "y": 800, "width": 972, "height": 300}, "text": "今日灵感", "font_size": 96, "duration_frames": 30,
                "tiers": [{"instance_id": "body", "source": {"kind": "stored", "preset_id": tier}, "font_family": "Noto Sans CJK SC", "font_weight": 700}]},
                "parameters": [{"name": "color", "title": "颜色", "targets": [{"instance_id": "body", "parameter": "color"}]}]}
            with pytest.raises(ToolFault) as refused:
                await current.execute("sprite.compose", SpriteComposeInput.model_validate(one), available())
            assert refused.value.error.field == field and "switched on" in refused.value.error.message
        boxes = {"description": "标题", "kind": "text", "instances": [{"instance_id": "title", "source": {"kind": "stored", "preset_id": tier},
                 "layout": {"x": 0, "y": 0, "width": 100, "height": 100, "z_index": 0}, "timing": {"start_frame": 0, "duration_frames": 30}}]}
        with pytest.raises(ToolFault, match="text block") as refused:
            await session(tmp_path / "boxes").execute("sprite.compose", SpriteComposeInput.model_validate(boxes), available())
        assert refused.value.error.field == "/text"

    asyncio.run(run())


@needs_bundler
def test_an_edit_snapshot_hands_back_the_text_block(tmp_path, monkeypatch):
    """编辑以文字块组合的基线：快照给出文字块（各栏的 base_* 草稿、当前配置里的字体与字号比例、栏间距、行起点与关键词），
    只列出 Agent 的样式绑定、不列宿主的配置参数；照快照原样重新组合得到同一份源码。"""
    monkeypatch.setattr(FontStore, "active", SimpleNamespace(families=lambda: list(LIBRARY)))

    async def run():
        """Compose a base, revise one configuration value like a user would, then recompose from the edit snapshot."""
        first = session(tmp_path / "first")
        tier, glow = await stored(first)
        composed = await first.execute("sprite.compose", two_tiers(tier, glow), available())
        sprite = first.sprite_drafts[composed["draft_id"]]
        config = {**sprite.default_parameters, "line1SizeRatio": 0.6}
        base = TemplateCandidate(tsx_code=sprite.code, config_schema=sprite.parameter_schema, default_config=config)
        edit = session(tmp_path / "edit", spec=SimpleNamespace(sprite=sprite, sprite_kind="text"), base=base)
        snapshot = edit.snapshot()["base"]
        assert "instances" not in snapshot and [item["name"] for item in snapshot["parameters"]] == ["line1Color", "line2Color", "keywordEffectColor"]
        block = snapshot["text"]
        assert [item["source"] for item in block["tiers"]] == [{"kind": "draft", "draft_id": "base_upper"}, {"kind": "draft", "draft_id": "base_lower"}]
        assert block["tiers"][0]["size_ratio"] == 0.6 and block["tiers"][1]["font_family"] == "Zed Display"
        assert (block["line_gap"], block["line_starts"], block["keyword"]["source"]["draft_id"]) == (24, [0, 12], "base_keyword")
        again = await edit.execute("sprite.compose", SpriteComposeInput.model_validate(
            {"description": "双栏标题", "kind": "text", "text": block, "parameters": snapshot["parameters"]}), available())
        assert edit.sprite_drafts[again["draft_id"]].default_parameters["line1SizeRatio"] == 0.6

    asyncio.run(run())


def test_preset_search_matches_every_word_and_filters_by_role(tmp_path):
    """搜索按空白分出的每个词都要出现在描述里（以前整串当子串，「双栏 发光」永远搜不到），并可按角色只列文字块用的 Preset。"""
    async def run():
        """Store three Presets and search them."""
        current = session(tmp_path)
        await current.execute("preset.create", preset("text", TIER_CODE, "color").model_copy(update={"description": "金色描边大字，双栏下栏"}), available())
        await current.execute("preset.create", preset("keyword", KEYWORD_CODE, "glowColor").model_copy(update={"description": "关键词发光"}), available())
        await current.execute("preset.create", preset(None, TIER_CODE, "color").model_copy(update={"description": "金色描边旧字效"}), available())
        found = lambda **query: [item.description for item in current.search_presets(PresetSearchInput(**query)).presets]
        assert found(query="双栏 描边") == ["金色描边大字，双栏下栏"]
        assert sorted(found(query="金色 描边")) == ["金色描边大字，双栏下栏", "金色描边旧字效"]
        assert found(role="keyword") == ["关键词发光"]
        assert [item.role for item in current.search_presets(PresetSearchInput(query="金色", role="text")).presets] == ["text"]

    asyncio.run(run())


def validator(tmp_path: Path) -> ToolValidator:
    """The isolated validator over the real sandboxed browser."""
    return ToolValidator(Renderer(Settings(_env_file=None, data_dir=tmp_path, **BROWSER)), tmp_path / "tools")


@needs_browser
def test_real_type_check_gives_role_presets_the_host_props(tmp_path):
    """真实类型检查：text 角色的 Preset 按宿主传入的文字、片段、Keyword 与字体检查，通过；把 Keyword 写成别的 prop 形状就报 TypeScript 错误。
    keyword 角色的 Preset 收到 text 同样通过。"""
    checker = validator(tmp_path)
    assert asyncio.run(checker.validate_code(preset("text", TIER_CODE, "color"))).passed
    assert asyncio.run(checker.validate_code(preset("keyword", KEYWORD_CODE, "glowColor"))).passed
    wrong = preset("text", TIER_CODE.replace("Keyword: React.ComponentType<{text: string}>", "Keyword: React.ComponentType<{label: string}>")
                   .replace("<Keyword key={index} text={segment.text} />", "<Keyword key={index} label={segment.text} />"), "color")
    report = asyncio.run(checker.validate_code(wrong))
    assert not report.passed and any(item.severity == "error" and "Keyword=" in (item.excerpt or "") for item in report.diagnostics)


@needs_browser
def test_real_two_tier_block_with_a_keyword_preset_passes_the_generation_measurements(tmp_path, monkeypatch):
    """真实浏览器：上栏 0.5 倍小字、下栏大字、关键词发光的双栏文字块通过透明度与排版检查：100 字分到两栏完整显示，两栏由同一个文字块渲染、
    同时出现与消失，下栏出现时上栏不跳动，关键词命中原位显示且整段文字仍然完整。"""
    monkeypatch.setattr(FontStore, "active", SimpleNamespace(families=lambda: ["Noto Sans CJK SC"]))

    async def run():
        """Compose the block, then mount it with the generation-path measurements."""
        current = session(tmp_path / "compose")
        tier, glow = await stored(current)
        request = two_tiers(tier, glow).model_dump(mode="json", exclude_none=True)
        request["text"]["tiers"][1]["font_family"] = "Noto Sans CJK SC"
        composed = await current.execute("sprite.compose", SpriteComposeInput.model_validate(request), available())
        sprite = current.sprite_drafts[composed["draft_id"]]
        component = ComponentDefinition(code=sprite.code, parameter_schema=sprite.parameter_schema, default_parameters=sprite.default_parameters)
        checker = validator(tmp_path)
        # sprite.create type-checks the generated host source too.
        assert (await checker.validate_code(component)).passed
        return await checker.validate_render(RenderValidationInput(component=component, duration_frames=30), transparency=True, text_fit=True)

    report = asyncio.run(run())
    checks = {item.name: item for item in report.checks}
    assert checks["transparency"].status == "passed", checks["transparency"].message
    assert checks["text_fit"].status == "passed", checks["text_fit"].message
    assert "over 2 lines is shown in full at the smallest fontSize 48" in checks["text_fit"].message


# 一栏实心色块：160 像素高，便于在成片里按像素判断这一栏是否显示。
BLOCK_TIER = """export default function Block(props: {text: string; color: string}) {
  return <div style={{height: 160, background: props.color}}>{props.text}</div>;
}
"""


def decoded_alpha(video: Path, index: int, pixel: int) -> float:
    """用 libvpx 解码第 index 帧，返回按行展开的第 pixel 个像素的 alpha（原生 vp9 解码器会丢弃透明通道）。"""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-c:v", "libvpx-vp9", "-i", str(video), "-vf", f"select=eq(n\\,{index})", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        capture_output=True, check=True,
    ).stdout
    return raw[pixel * 4 + 3] / 255


@needs_browser
def test_real_render_reveals_the_lower_tier_at_its_cue_and_clears_both(tmp_path):
    """真实沙箱渲染（总线的单 Sprite 渲染）：同一句的两个切片放进双栏文字块，上栏从第一个切片起显示，下栏在第二个切片开始时出现，
    之前不显示但占住位置；整组在最后一个切片结束时一起清空。"""
    tier = {"preset": {"code": BLOCK_TIER, "parameter_schema": {"type": "object", "properties": {"color": COLOR}, "required": ["color"], "additionalProperties": False},
                       "default_parameters": {"color": "#FF0000"}}, "parameters": {"color": "#FF0000"},
            "layout": {"x": 0, "y": 0, "width": 180, "height": 320, "z_index": 0}, "timing": {"start_frame": 0, "duration_frames": 30}}
    block = {"layout": {"x": 0, "y": 0, "width": 180, "height": 320}, "text": "上\n下", "font_size": 96, "line_gap": 0, "line_starts": [0, 15],
             "tiers": [{"font_family": "Noto Sans CJK SC", "font_weight": 700, "size_ratio": 1}, {"font_family": "Noto Sans CJK SC", "font_weight": 700, "size_ratio": 1}]}
    code, schema, defaults, _ = compose_text_source(block, [{**tier, "instance_id": "upper"}, {**tier, "instance_id": "lower"}], None, [], ["Noto Sans CJK SC"])
    sprite = pb.PublishedSprite(
        sprite_id="tiers", name="tiers", kind=pb.SPRITE_KIND_TEXT, tsx_code=code, text_prop="text",
        canvas=pb.SpriteCanvas(width=180, height=320, fps=30, preview_frames=30), content_box=pb.SpriteBox(x=0, y=0, width=180, height=320),
        config_schema_json=json.dumps(schema), default_config_json=json.dumps(defaults),
        parameters=[pb.SpriteParameter(key="text", access=pb.OPERATOR_ACCESS_VISIBLE_EDITABLE, default_value=pb.ScalarValue(string_value="上\n下"))],
    )
    message = pb.SpriteRenderInput(sprite_id="tiers", effect_total_frames=100, cues=[
        pb.SpriteCue(text="一", start_frame=0, end_frame=45, sentence=1), pb.SpriteCue(text="二", start_frame=45, end_frame=90, sentence=1)])
    settings = Settings(_env_file=None, data_dir=tmp_path / "state", **BROWSER)
    video = asyncio.run(sprite_render.render_sprite(Renderer(settings), sprite_render.plan_sprite_render(message, sprite), tmp_path / "job"))
    # Row 20 is inside the upper tier only while the waiting lower tier keeps its space (centred alone it would span rows 80-240).
    upper, lower = 20 * 180 + 90, 300 * 180 + 90
    for frame, upper_alpha, lower_alpha in ((10, 1.0, 0.0), (60, 1.0, 1.0), (95, 0.0, 0.0)):
        assert decoded_alpha(video, frame, upper) == pytest.approx(upper_alpha, abs=0.05), frame
        assert decoded_alpha(video, frame, lower) == pytest.approx(lower_alpha, abs=0.05), frame


# 一栏文字，带一个联合类型的样式参数：对齐方式的默认值 "center" 必须满足 "left" | "center" | "right"。
ALIGNED_TIER = TIER_CODE.replace("color: string})", 'color: string; align: "left" | "center" | "right"})').replace('textAlign: "center"', "textAlign: props.align")


@needs_browser
def test_real_creation_saves_a_text_block_from_role_presets(tmp_path, monkeypatch):
    """真实端到端（不替换任何检查）：创建 text 与 keyword Preset、组合双栏文字块并保存。审计复现（作品 f941b066）：保存时复查源 Preset
    曾不带角色（text Preset 缺宿主 prop）；默认值以对象展开传入时 "center" 被放宽成 string，联合类型的 align 也通不过类型检查。"""
    monkeypatch.setattr(FontStore, "active", SimpleNamespace(families=lambda: ["Noto Sans CJK SC"]))
    settings = Settings(_env_file=None, data_dir=tmp_path, **BROWSER)
    current = ToolSession(SimpleNamespace(settings=settings, renderer=Renderer(settings)), None, None, Budget(), tmp_path / "run", [], lambda *_: None,
                          {"original_request": {"description": "双栏", "two_tier": True, "keyword_effect": True}})

    async def run():
        """Create both Presets, compose the block and save it with every real check."""
        tier = preset("text", ALIGNED_TIER, "color")
        tier = tier.model_copy(update={"parameter_schema": {**tier.parameter_schema, "properties": {**tier.parameter_schema["properties"], "align": {"type": "string", "enum": ["left", "center", "right"]}},
                                                            "required": ["color", "align"]}, "default_parameters": {"color": "#FFFFFF", "align": "center"}})
        stored_tier = await current.execute("preset.create", tier, available())
        glow = await current.execute("preset.create", preset("keyword", KEYWORD_CODE, "glowColor", "#FFD54A"), available())
        request = two_tiers(stored_tier["preset"]["preset_id"], glow["preset"]["preset_id"]).model_dump(mode="json", exclude_none=True)
        request["text"]["tiers"][1]["font_family"] = "Noto Sans CJK SC"
        composed = await current.execute("sprite.compose", SpriteComposeInput.model_validate(request), available())
        return await current.execute("sprite.create", SpriteCreateInput(draft_id=composed["draft_id"]), available())

    saved = asyncio.run(run())
    assert saved["validation"]["passed"] is True
