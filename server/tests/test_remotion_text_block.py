"""字效的文字组合：text / keyword / effect 角色 Preset、宿主生成的配置参数、双栏、关键词与特效组合、编辑快照与 Preset 搜索。

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

from server.remotion_templates.categories import HEX_COLOR, category_problems, parameter_problems
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
# 一栏文字的新写法：宿主排好这一栏（关键词命中已画好、逐字特效时每个字一个单元）作为 children 交来，Preset 只在自己的块上画外观。
CHILD_TIER = """import React from "react";
export default function Tier(props: {children: React.ReactNode; fontFamily: string; fontWeight: number; fontSize: number; color: string}) {
  return <div style={{width: "100%", textAlign: "center", whiteSpace: "normal", wordBreak: "break-all", fontFamily: props.fontFamily, fontWeight: props.fontWeight, fontSize: props.fontSize, lineHeight: 1.2, color: props.color}}>{props.children}</div>;
}
"""
# 关键词命中：在文字流里原位发光，字体继承宿主包裹的样式。
KEYWORD_CODE = """export default function Glow(props: {text: string; glowColor: string}) {
  return <span style={{font: "inherit", textShadow: `0 0 12px ${props.glowColor}`}}>{props.text}</span>;
}
"""

# 关键词命中的新写法：宿主把这个命中的各个字作为 children 交来（逐字特效时每个字是一个单元），Preset 只在外面画发光。
KEYWORD_CHILD_CODE = """import React from "react";
export default function Glow(props: {text: string; children: React.ReactNode; glowColor: string}) {
  return <span style={{font: "inherit", textShadow: `0 0 12px ${props.glowColor}`}}>{props.children}</span>;
}
"""

# 特效：按宿主给的显现进度改变不透明度，包在宿主的 Box 里；没有自己的样式参数，入场与出场共用。
FADE_CODE = """import React from "react";
export default function Fade(props: {children: React.ReactNode; progress: number; Box: React.ComponentType<{style?: React.CSSProperties; children?: React.ReactNode}>}) {
  const {Box} = props;
  return <Box style={{opacity: props.progress}}>{props.children}</Box>;
}
"""
# 特效：从小到大弹出，用于关键词命中；只改 transform，不影响排版。
POP_CODE = FADE_CODE.replace("Fade", "Pop").replace("{opacity: props.progress}", "{transform: `scale(${0.6 + 0.4 * props.progress})`}")
# 持续特效：按自己的颜色给包住的部分加发光。
SHINE_CODE = """import React from "react";
export default function Shine(props: {children: React.ReactNode; progress: number; Box: React.ComponentType<{style?: React.CSSProperties; children?: React.ReactNode}>; glowColor: string}) {
  const {Box} = props;
  return <Box style={{filter: `drop-shadow(0 0 12px ${props.glowColor})`}}>{props.children}</Box>;
}
"""


# 特效 Preset 记下的默认用法：没有特别说明时是整体淡入。
FADE_USAGE = {"title": "淡入", "slot": "enter", "target": "block"}


def effect_preset(code: str, prop: str | None = None, usage: dict | None = None) -> PresetDraft:
    """An effect Preset with no style parameter, or only the colour ``prop``, recording ``usage`` (a fade in by default)."""
    if prop:
        return PresetDraft.model_validate({**preset("effect", code, prop, "#FFD54A").model_dump(mode="json", exclude_unset=True), "usage": usage or FADE_USAGE})
    return PresetDraft(description="effect preset", code=code, role="effect", default_parameters={}, usage=usage or FADE_USAGE,
                       parameter_schema={"type": "object", "properties": {}, "additionalProperties": False})


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


async def stored(current: ToolSession) -> tuple[str, str, str]:
    """Store a text Preset, a keyword Preset and a fade effect Preset; return their IDs."""
    tier = await current.execute("preset.create", preset("text", TIER_CODE, "color"), available())
    glow = await current.execute("preset.create", preset("keyword", KEYWORD_CODE, "glowColor", "#FFD54A"), available())
    fade = await current.execute("preset.create", effect_preset(FADE_CODE), available())
    return tier["preset"]["preset_id"], glow["preset"]["preset_id"], fade["preset"]["preset_id"]


def fades(fade: str, enabled: bool = True) -> list[dict]:
    """The entrance and exit a new work's text block needs: each tier fades in and the block fades out over the stored ``fade``."""
    source = {"kind": "stored", "preset_id": fade}
    return [{"instance_id": "fadeIn", "source": source, "title": "淡入", "slot": "enter", "target": "tiers", "enabled": enabled},
            {"instance_id": "fadeOut", "source": source, "title": "淡出", "slot": "exit", "target": "block", "enabled": enabled}]


def two_tiers(tier: str, glow: str | None, fade: str | None = None, **text) -> SpriteComposeInput:
    """A two-tier text block over the stored ``tier`` Preset, with a keyword Preset when ``glow`` is given and the fade in
    and out (`fades`) over the stored ``fade`` effect Preset when that is given."""
    source = {"kind": "stored", "preset_id": tier}
    block = {
        "layout": {"x": 54, "y": 700, "width": 972, "height": 520}, "text": "判断一个人\n选择花字、滤镜和特效", "font_size": 96,
        "tiers": [
            {"instance_id": "upper", "source": source, "parameters": {"color": "#FFFFFF"}, "font_family": "Noto Sans CJK SC", "font_weight": 700, "size_ratio": 0.5},
            {"instance_id": "lower", "source": source, "parameters": {"color": "#F5B93C"}, "font_family": "Zed Display", "font_weight": 700, "size_ratio": 1},
        ],
        "line_gap": 24, "line_starts": [0, 12], "duration_frames": 30,
        **({"keyword": {"instance_id": "keyword", "source": {"kind": "stored", "preset_id": glow}}} if glow else {}),
        **({"effects": fades(fade)} if fade else {}),
        **text,
    }
    bindings = [
        {"name": "line1Color", "title": "颜色一", "targets": [{"instance_id": "upper", "parameter": "color"}]},
        {"name": "line2Color", "title": "颜色二", "targets": [{"instance_id": "lower", "parameter": "color"}]},
        *([{"name": "keywordEffectColor", "title": "发光", "targets": [{"instance_id": "keyword", "parameter": "glowColor"}]}] if glow else []),
    ]
    return SpriteComposeInput.model_validate({"description": "双栏标题", "kind": "text", "text": block, "parameters": bindings})


@needs_bundler
def test_a_text_block_can_name_host_built_in_effects(tmp_path):
    """收缩创作面：模型只写特效名字（builtin fade/glow），宿主出组件，不必存 effect Preset；组合源码引用 __BUILTINS，
    效果配置参数仍由宿主生成，文字块记下 builtin 以便编辑还原。"""
    current = session(tmp_path)
    tier = asyncio.run(current.execute("preset.create", preset("text", TIER_CODE, "color"), available()))["preset"]["preset_id"]
    block = {
        "layout": {"x": 90, "y": 700, "width": 900, "height": 400}, "text": "今日灵感", "font_size": 96,
        "tiers": [{"instance_id": "tier", "source": {"kind": "stored", "preset_id": tier}, "parameters": {"color": "#FFFFFF"},
                   "font_family": "Noto Sans CJK SC", "font_weight": 700}],
        "effects": [
            {"instance_id": "fadeIn", "builtin": "fade", "title": "淡入", "slot": "enter", "target": "block"},
            {"instance_id": "fadeOut", "builtin": "fade", "title": "淡出", "slot": "exit", "target": "block"},
            {"instance_id": "glow", "builtin": "glow", "title": "发光", "slot": "loop", "target": "block"},
        ],
        "duration_frames": 90,
    }
    bindings = [
        {"name": "color", "title": "颜色", "targets": [{"instance_id": "tier", "parameter": "color"}]},
        {"name": "glowColor", "title": "发光颜色", "targets": [{"instance_id": "glow", "parameter": "color"}]},
        {"name": "glowLight", "title": "发光强度", "targets": [{"instance_id": "glow", "parameter": "intensity"}]},
    ]
    request = SpriteComposeInput.model_validate({"description": "内置淡入淡出", "kind": "text", "text": block, "parameters": bindings})
    composed = asyncio.run(current.execute("sprite.compose", request, available()))
    draft = current.sprite_draft(composed["draft_id"])
    assert 'Preset: __BUILTINS["fade"]' in draft.code and 'Preset: __BUILTINS["glow"]' in draft.code
    effects = {item["instance_id"]: item for item in draft.model_dump(mode="json")["text"]["effects"]}
    assert effects["fadeIn"]["builtin"] == "fade" and effects["glow"]["slot"] == "loop"
    # The effect{k} configuration parameters still come from the host; a loop has no length.
    assert {"effect1", "effect1Target", "effect1Seconds", "effect3", "effect3Target"} <= set(draft.default_parameters)
    assert "effect3Seconds" not in draft.default_parameters
    # A built-in carries tunable style props, bindable like a Preset's.
    assert draft.default_parameters["glowColor"] == "#FFFFFF" and draft.default_parameters["glowLight"] == 10
    assert {"glowColor", "glowLight"} <= set(draft.parameter_schema["properties"])


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
    assert schemas["lineGap"]["minimum"] == -600 and schemas["lineGap"]["maximum"] == 300
    assert (defaults["line1SizeRatio"], defaults["line2FontFamily"], defaults["lineStartFrames"], defaults["keywordEffect"]) == (0.5, "Noto Sans CJK SC", [0, 12], True)


def test_a_transparency_colour_is_refused_so_transparency_stays_an_effect():
    """生成路径：把透明度做成字色（栏颜色默认值带 alpha）时被拒，并说明透明是带可调数字样式参数的特效；不透明颜色通过。
    发布更早的版本不传字体库，不追溯拦截带 alpha 的旧颜色。"""
    one = {"text": "今日灵感", "font_size": 96, "tiers": [{"font_family": "Zed Display", "font_weight": 700}]}
    schemas, defaults = text_configuration(one, LIBRARY)
    properties = {**schemas, "color": COLOR}
    bound = set(properties)
    schema = {"type": "object", "properties": properties}
    assert category_problems("text", schema, bound, {**defaults, "color": "#FFFFFF"}, fonts=LIBRARY) == []
    [problem] = category_problems("text", schema, bound, {**defaults, "color": "#FFFFFF80"}, fonts=LIBRARY)
    assert (problem.name, problem.rule) == ("color", None)
    assert "transparency effect" in problem.reason and "opacity" in problem.reason and "alpha" in problem.reason
    # 单栏与双栏的栏颜色都检查；发布路径（fonts=None）保持旧版本可保存。
    assert category_problems("text", schema, bound, {**defaults, "color": "#FFFFFF80"}) == []
    two = {"text": "上\n下", "font_size": 96, "line_gap": 24, "line_starts": [0, 12],
           "tiers": [{"font_family": "Zed Display", "font_weight": 400, "size_ratio": 0.5}, {"font_family": "Noto Sans CJK SC", "font_weight": 700, "size_ratio": 1}]}
    schemas, defaults = text_configuration(two, LIBRARY)
    properties = {**schemas, "line1Color": COLOR, "line2Color": COLOR}
    problems = category_problems("text", {"type": "object", "properties": properties}, set(properties),
                                 {**defaults, "line1Color": "#FFFFFF80", "line2Color": "#F5B93C"}, fonts=LIBRARY)
    assert [item.name for item in problems] == ["line1Color"]


@needs_bundler
def test_a_negative_line_gap_pulls_the_lower_tier_up():
    """栏间距为负时宿主用负 marginTop 把下栏向上移（CSS gap 不支持负值），两栏因此可以叠在一起；配置参数的默认值保留负数。"""
    tier = {
        "preset": {"code": TIER_CODE, "parameter_schema": {"type": "object", "properties": {"color": COLOR}, "required": ["color"], "additionalProperties": False},
                   "default_parameters": {"color": "#FFFFFF"}},
        "parameters": {"color": "#FFFFFF"},
        "layout": {"x": 0, "y": 0, "width": 1080, "height": 600, "z_index": 0},
        "timing": {"start_frame": 0, "duration_frames": 30},
    }
    block = {"layout": {"x": 0, "y": 0, "width": 1080, "height": 600}, "text": "上\n下", "font_size": 96, "line_gap": -60, "line_starts": [0, 15],
             "tiers": [{"font_family": "Noto Sans CJK SC", "font_weight": 700, "size_ratio": 1},
                       {"font_family": "Noto Sans CJK SC", "font_weight": 700, "size_ratio": 1}]}
    code, _schema, defaults, _ = compose_text_source(block, [{**tier, "instance_id": "upper"}, {**tier, "instance_id": "lower"}], None, [], [], ["Noto Sans CJK SC"])
    assert defaults["lineGap"] == -60
    assert "marginTop: __overlap ? 0 : props.lineGap" in code and " gap: props.lineGap" not in code


def test_effects_get_the_hosts_switch_position_and_length():
    """第 k 个特效由宿主给出开关 effect{k}、位置枚举 effect{k}Target，入场与出场另有 0.1～3 秒的时长 effect{k}Seconds（省略为 0.5）；
    入场另有 0～3 秒的推迟开始 effect{k}Delay（省略为 0）；标题取特效的中文名称，双栏可选整体、各栏、上下栏、三种逐字位置与关键词，
    单栏只有整体、逐字位置与关键词；这些 Schema 都能被发布抽样覆盖。"""
    two = {"text": "上\n下", "font_size": 96, "line_gap": 24, "line_starts": [0, 12],
           "tiers": [{"font_family": "Zed Display", "font_weight": 400, "size_ratio": 0.5}, {"font_family": "Noto Sans CJK SC", "font_weight": 700, "size_ratio": 1}],
           "effects": [{"title": "淡入", "slot": "enter", "target": "tiers", "seconds": 0.4}, {"title": "发光", "slot": "loop", "target": "line2", "enabled": False}]}
    schemas, defaults = text_configuration(two, LIBRARY)
    assert schemas["effect1"] == {"type": "boolean", "title": "入场·淡入"} and schemas["effect2"]["title"] == "持续·发光"
    assert schemas["effect1Target"] == {"type": "string", "enum": ["block", "tiers", "line1", "line2", "chars", "keywordChars", "plainChars", "keyword"], "title": "淡入位置"}
    assert schemas["effect1Seconds"] == {"type": "number", "minimum": 0.1, "maximum": 3, "title": "淡入时长（秒）"} and "effect2Seconds" not in schemas
    assert (defaults["effect1"], defaults["effect1Target"], defaults["effect1Seconds"], defaults["effect2"], defaults["effect2Target"]) == (True, "tiers", 0.4, False, "line2")
    assert schemas["effect1Delay"] == {"type": "number", "minimum": 0, "maximum": 3, "title": "淡入延迟（秒）"} and "effect2Delay" not in schemas
    assert defaults["effect1Delay"] == 0
    assert all(parameter_problems(schema, name) is None for name, schema in schemas.items() if name.startswith("effect"))
    one = {"text": "今日灵感", "font_size": 96, "tiers": [{"font_family": "Zed Display", "font_weight": 700}],
           "effects": [{"title": "淡出", "slot": "exit", "target": "block"}]}
    schemas, defaults = text_configuration(one, LIBRARY)
    assert schemas["effect1Target"]["enum"] == ["block", "chars", "keywordChars", "plainChars", "keyword"] and "effect1Delay" not in schemas and defaults["effect1Seconds"] == 0.5


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
        tier, glow, fade = await stored(current)
        composed = await current.execute("sprite.compose", two_tiers(tier, glow, fade), available())
        sprite = current.sprite_drafts[composed["draft_id"]]
        names = list(sprite.parameter_schema["properties"])
        assert names[:3] == ["text", "fontSize", "line1FontFamily"] and names[-5:] == ["upper", "lower", "keyword", "fadeIn", "fadeOut"]
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
        assert [getattr(item, "role", None) for item in checked] == ["text", "keyword", "effect", None]

    asyncio.run(run())


# 双栏标题的特效：各栏淡入、整体淡出（同一个 fade Preset），关键词命中弹出，下栏持续发光（颜色是绑定的样式参数）。
EFFECTS = [
    {"instance_id": "fadeIn", "source": "fade", "title": "淡入", "slot": "enter", "target": "tiers"},
    {"instance_id": "fadeOut", "source": "fade", "title": "淡出", "slot": "exit", "target": "block", "seconds": 0.4},
    {"instance_id": "pop", "source": "pop", "title": "弹出", "slot": "enter", "target": "keyword"},
    {"instance_id": "shine", "source": "shine", "title": "发光", "slot": "loop", "target": "line2"},
]


async def effect_sources(current: ToolSession) -> dict[str, dict]:
    """Store the fade, pop and shine effect Presets; return their stored sources by name."""
    sources = {}
    for name, draft in (("fade", effect_preset(FADE_CODE)), ("pop", effect_preset(POP_CODE)), ("shine", effect_preset(SHINE_CODE, "glowColor"))):
        sources[name] = {"kind": "stored", "preset_id": (await current.execute("preset.create", draft, available()))["preset"]["preset_id"]}
    return sources


def with_effects(request: SpriteComposeInput, sources: dict[str, dict]) -> dict:
    """The two-tier ``request`` as data with `EFFECTS` over ``sources`` and the shine colour bound as a style parameter."""
    data = request.model_dump(mode="json", exclude_none=True)
    data["text"]["effects"] = [{**item, "source": sources[item["source"]]} for item in EFFECTS]
    data["parameters"].append({"name": "shineColor", "title": "发光颜色", "targets": [{"instance_id": "shine", "parameter": "glowColor"}]})
    return data


@needs_bundler
def test_a_text_block_wraps_its_positions_in_effect_presets(tmp_path, monkeypatch):
    """特效组合：effect Preset 按顺序包住各自的位置，宿主给每个特效开关、位置与（入场、出场的）时长并以文字块为绑定目标，发光颜色是
    Agent 绑定的样式参数；记录特效与「各栏 Preset 只画静止外观」的标记，保存时 effect Preset 按宿主的 prop 做类型检查，同一个 fade 只检查一次。"""
    monkeypatch.setattr(FontStore, "active", SimpleNamespace(families=lambda: list(LIBRARY)))

    async def run():
        """Compose the block with its effects and save it."""
        current = session(tmp_path)
        tier, glow, fade = await stored(current)
        request = with_effects(two_tiers(tier, glow), await effect_sources(current))
        composed = await current.execute("sprite.compose", SpriteComposeInput.model_validate(request), available())
        sprite = current.sprite_drafts[composed["draft_id"]]
        host = {item.name for item in sprite.bindings if item.targets[0].instance_id == TEXT_BLOCK_ID}
        assert {"effect1", "effect1Target", "effect1Seconds", "effect2Seconds", "effect3Target", "effect4", "effect4Target"} <= host and "effect4Seconds" not in host
        assert (sprite.default_parameters["effect2Seconds"], sprite.default_parameters["effect3Target"], sprite.default_parameters["shineColor"]) == (0.4, "keyword", "#FFD54A")
        assert [{k: item.model_dump()[k] for k in ("instance_id", "title", "slot")} for item in sprite.text.effects] == [{"instance_id": item["instance_id"], "title": item["title"], "slot": item["slot"]} for item in EFFECTS]
        assert sprite.text.still is True and 'at: ["keyword"], inline: true' in sprite.code and 'at: ["tiers", "line2"]' in sprite.code and "__Hold" in sprite.code
        assert category_problems("text", sprite.parameter_schema, {item.name for item in sprite.bindings}, sprite.default_parameters) == []
        checked = recording(current)
        await current.execute("sprite.create", SpriteCreateInput(draft_id=composed["draft_id"]), available())
        assert [getattr(item, "role", None) for item in checked] == ["text", "keyword", "effect", "effect", "effect", None]

    asyncio.run(run())


@needs_bundler
@pytest.mark.parametrize(("code", "glyphs"), [(TIER_CODE, False), (CHILD_TIER, True)])
def test_only_tiers_that_draw_the_hosts_line_offer_chars(code, glyphs):
    """逐字位置 chars 需要各栏的 text Preset 画宿主排好的 children：这样的栏由宿主排好整行交给它，特效位置可选 chars；
    自己按 segments 画的旧写法照常组合，但位置枚举里没有 chars，用户之后也切不过去。"""
    tier = {"instance_id": "body", "preset": {"code": code, "parameter_schema": {"type": "object", "properties": {"color": COLOR}, "required": ["color"], "additionalProperties": False},
                                              "default_parameters": {"color": "#FFFFFF"}}, "parameters": {"color": "#FFFFFF"}}
    fade = {"instance_id": "fadeIn", "preset": {"code": FADE_CODE, "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": False}, "default_parameters": {}}, "parameters": {}}
    block = {"layout": {"x": 54, "y": 700, "width": 972, "height": 300}, "text": "今日灵感", "font_size": 96, "tiers": [{"font_family": "Noto Sans CJK SC", "font_weight": 700}],
             "effects": [{"title": "淡入", "slot": "enter", "target": "block"}]}
    code, schema, _, _ = compose_text_source(block, [tier], None, [fade], [], LIBRARY, still=True)
    assert ("chars" in schema["properties"]["effect1Target"]["enum"]) is glyphs
    assert ("__line(__segments(" in code) is glyphs


@needs_bundler
@pytest.mark.parametrize(("code", "glyphs"), [(KEYWORD_CODE, False), (KEYWORD_CHILD_CODE, True)])
def test_only_a_keyword_preset_that_draws_the_hosts_glyphs_offers_keyword_chars(code, glyphs):
    """关键词逐字 keywordChars 需要关键词 Preset 画宿主交来的各个字 children：这样命中里的字也是单元；只画 text 的旧写法照常组合，
    但位置枚举里没有 keywordChars（其余逐字位置照常可选），放在那里按字段拒绝并说明修法。"""
    color = {"type": "object", "properties": {"color": COLOR}, "required": ["color"], "additionalProperties": False}
    tier = {"instance_id": "body", "preset": {"code": CHILD_TIER, "parameter_schema": color, "default_parameters": {"color": "#FFFFFF"}}, "parameters": {"color": "#FFFFFF"}}
    glow = {"instance_id": "glow", "preset": {"code": code, "parameter_schema": {"type": "object", "properties": {"glowColor": COLOR}, "required": ["glowColor"], "additionalProperties": False},
                                              "default_parameters": {"glowColor": "#FFD54A"}}, "parameters": {"glowColor": "#FFD54A"}}
    fade = {"instance_id": "typeIn", "preset": {"code": FADE_CODE, "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": False}, "default_parameters": {}}, "parameters": {}}
    block = {"layout": {"x": 54, "y": 700, "width": 972, "height": 300}, "text": "今日灵感", "font_size": 96, "tiers": [{"font_family": "Noto Sans CJK SC", "font_weight": 700}],
             "keyword": {}, "effects": [{"title": "打字", "slot": "enter", "target": "chars"}]}
    _code, schema, _, _ = compose_text_source(block, [tier], glow, [fade], [], LIBRARY, still=True)
    assert schema["properties"]["effect1Target"]["enum"] == ["block", "chars", *(["keywordChars"] if glyphs else []), "plainChars", "keyword"]
    block["effects"][0]["target"] = "keywordChars"
    if glyphs:
        compose_text_source(block, [tier], glow, [fade], [], LIBRARY, still=True)
        return
    with pytest.raises(ToolFault) as refused:
        compose_text_source(block, [tier], glow, [fade], [], LIBRARY, still=True)
    assert refused.value.error.field == "/text/effects/0/target"
    assert "glow draws its text itself" in refused.value.error.message and "{props.children}" in refused.value.error.message


@needs_bundler
def test_glyph_effects_stagger_and_the_block_names_its_preview_keywords(tmp_path, monkeypatch):
    """逐字位置的入场、出场有「逐字错开」配置参数 effect{k}Stagger（0～1，须明确选择），其他位置与持续特效给 stagger 被拒；文字块的
    preview_keywords 须是默认文字里的词，记在组合结果里，编辑快照原样交回，回看帧以它们为示例关键词（审计复现：作品 06eb0788
    的回看帧以开头两个字为关键词，Executor 据此把正确的「关键词先出」改反）。"""
    from server.remotion_templates.tools.session import sample_keywords

    monkeypatch.setattr(FontStore, "active", SimpleNamespace(families=lambda: list(LIBRARY)))

    async def run():
        """Compose a block with a staggered glyph entrance and preview keywords, then wrong variants."""
        current = session(tmp_path)
        tier = await current.execute("preset.create", preset("text", CHILD_TIER, "color"), available())
        fade = await current.execute("preset.create", effect_preset(FADE_CODE), available())
        source = {"kind": "stored", "preset_id": fade["preset"]["preset_id"]}
        request = {"description": "关键词先打出", "kind": "text", "parameters": [{"name": "color", "title": "颜色", "targets": [{"instance_id": "body", "parameter": "color"}]}],
                   "text": {"layout": {"x": 54, "y": 700, "width": 972, "height": 300}, "text": "想玩小众黑科技", "font_size": 96, "duration_frames": 30,
                            "tiers": [{"instance_id": "body", "source": {"kind": "stored", "preset_id": tier["preset"]["preset_id"]}, "font_family": "Noto Sans CJK SC", "font_weight": 700}],
                            "preview_keywords": ["小众黑科技"],
                            "effects": [{"instance_id": "typeIn", "source": source, "title": "打字", "slot": "enter", "target": "keywordChars", "stagger": 1},
                                        {"instance_id": "showIn", "source": source, "title": "显现", "slot": "enter", "target": "plainChars", "delay": 1, "stagger": 0},
                                        {"instance_id": "fadeOut", "source": source, "title": "淡出", "slot": "exit", "target": "block"}]}}
        composed = await current.execute("sprite.compose", SpriteComposeInput.model_validate(request), available())
        sprite = current.sprite_drafts[composed["draft_id"]]
        schema, defaults = sprite.parameter_schema["properties"], sprite.default_parameters
        assert schema["effect2Stagger"] == {"type": "number", "minimum": 0, "maximum": 1, "title": "显现逐字错开"}
        assert (defaults["effect1Stagger"], defaults["effect2Stagger"], "effect3Stagger" in defaults) == (1, 0, False)
        assert sprite.text.preview_keywords == ["小众黑科技"] and sample_keywords(sprite) == ["小众黑科技"]
        base = TemplateCandidate(tsx_code=sprite.code, config_schema=sprite.parameter_schema, default_config=defaults)
        block = session(tmp_path / "edit", spec=SimpleNamespace(sprite=sprite, sprite_kind="text"), base=base).snapshot()["base"]["text"]
        assert block["preview_keywords"] == ["小众黑科技"] and block["effects"][1]["stagger"] == 0
        for change, field in (({"stagger": 0.5}, "/text/effects/2/stagger"), ({"preview_keywords": ["别的"]}, "/text/preview_keywords/0")):
            wrong = json.loads(json.dumps(request))
            if "stagger" in change:
                wrong["text"]["effects"][2].update(change)
            else:
                wrong["text"].update(change)
            with pytest.raises(ToolFault) as refused:
                await current.execute("sprite.compose", SpriteComposeInput.model_validate(wrong), available())
            assert refused.value.error.field == field

    asyncio.run(run())


@needs_bundler
def test_chars_on_a_tier_that_draws_its_segments_is_refused_with_the_fix():
    """旧写法的栏把特效放在 chars 时按字段拒绝，并说明修法：复制那个 Preset，声明 children 并渲染 {props.children}。"""
    tier = {"instance_id": "body", "preset": {"code": TIER_CODE, "parameter_schema": {"type": "object", "properties": {"color": COLOR}, "required": ["color"], "additionalProperties": False},
                                              "default_parameters": {"color": "#FFFFFF"}}, "parameters": {"color": "#FFFFFF"}}
    fade = {"instance_id": "typeIn", "preset": {"code": FADE_CODE, "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": False}, "default_parameters": {}}, "parameters": {}}
    block = {"layout": {"x": 54, "y": 700, "width": 972, "height": 300}, "text": "今日灵感", "font_size": 96, "tiers": [{"font_family": "Noto Sans CJK SC", "font_weight": 700}],
             "effects": [{"title": "打字", "slot": "enter", "target": "chars"}]}
    with pytest.raises(ToolFault) as refused:
        compose_text_source(block, [tier], None, [fade], [], LIBRARY, still=True)
    assert refused.value.error.field == "/text/effects/0/target"
    assert "body draws the segments itself" in refused.value.error.message and "{props.children}" in refused.value.error.message


@needs_bundler
@pytest.mark.parametrize(("change", "field", "message"), [
    ("one-tier", "/text/effects/0/target", "A one-tier text has no tiers position"),
    ("one-tier-cue", "/text/effects/0/slot", "only a two-tier text has one"),
    ("loop-seconds", "/text/effects/3/seconds", "leave seconds out"),
    ("exit-delay", "/text/effects/1/delay", "Only an entrance or a cue effect waits"),
    ("taken-id", "/text/effects/0/instance_id", "not a parameter name of the text block"),
    ("taken-name", "/parameters/3/name", "configuration parameter of the text block"),
    ("text-preset", "/text/effects/0/source", "role 'effect'"),
])
def test_a_wrong_effect_is_refused_with_the_field(tmp_path, monkeypatch, change, field, message):
    """特效不合约定时按字段拒绝：单栏没有各栏位置、单栏没有下栏出现（cue）、持续特效带时长、出场特效带延迟、实例 ID 占用特效配置名（会顶替宿主的开关）、样式参数占用之后
    特效的配置名、特效用了 text 角色的 Preset。"""
    monkeypatch.setattr(FontStore, "active", SimpleNamespace(families=lambda: list(LIBRARY)))

    async def run():
        """Compose the block with one effect wrong."""
        current = session(tmp_path)
        tier, glow, fade = await stored(current)
        request = with_effects(two_tiers(tier, glow), await effect_sources(current))
        effects = request["text"]["effects"]
        if change.startswith("one-tier"):
            request["text"] = {**{key: value for key, value in request["text"].items() if key not in {"line_gap", "line_starts"}},
                               "text": "今日灵感", "tiers": [{key: value for key, value in request["text"]["tiers"][0].items() if key != "size_ratio"}]}
            request["parameters"] = [{"name": "color", "title": "颜色", "targets": [{"instance_id": "upper", "parameter": "color"}]}, *request["parameters"][2:]]
            effects[3]["target"] = "block"
            if change == "one-tier-cue":
                effects[0].update(slot="cue", target="block")
        elif change == "loop-seconds":
            effects[3]["seconds"] = 1
        elif change == "exit-delay":
            effects[1]["delay"] = 1
        elif change == "taken-id":
            effects[0]["instance_id"] = "effect1"
        elif change == "taken-name":
            request["parameters"][3]["name"] = "effect7"
        else:
            effects[0]["source"] = {"kind": "stored", "preset_id": tier}
        with pytest.raises(ToolFault) as refused:
            await current.execute("sprite.compose", SpriteComposeInput.model_validate(request), available())
        assert refused.value.error.code == "INVALID_ARGUMENT" and refused.value.error.field == field
        assert message in refused.value.error.message

    asyncio.run(run())


@needs_bundler
def test_two_tiers_stack_or_overlap(tmp_path, monkeypatch):
    """双栏的排布是宿主的配置参数 tierLayout（「双栏排布」，上下排列或重叠），文字块的 arrangement 给默认值；重叠时两栏叠在同一处，
    不需要栏间距（省略按 0）。单栏没有排布，给了按字段拒绝。"""
    monkeypatch.setattr(FontStore, "active", SimpleNamespace(families=lambda: list(LIBRARY)))

    async def run():
        """Compose an overlapping two-tier block, then a one-tier block with an arrangement."""
        current = session(tmp_path)
        tier, glow, fade = await stored(current)
        request = two_tiers(tier, glow, fade).model_dump(mode="json", exclude_none=True)
        del request["text"]["line_gap"]
        request["text"]["arrangement"] = "overlap"
        composed = await current.execute("sprite.compose", SpriteComposeInput.model_validate(request), available())
        sprite = current.sprite_drafts[composed["draft_id"]]
        assert sprite.parameter_schema["properties"]["tierLayout"] == {"type": "string", "enum": ["stack", "overlap"], "title": "双栏排布"}
        assert (sprite.default_parameters["tierLayout"], sprite.default_parameters["lineGap"]) == ("overlap", 0)
        assert 'props.tierLayout === "overlap"' in sprite.code
        request = two_tiers(tier, glow, fade).model_dump(mode="json", exclude_none=True)
        request["text"] = {**{key: value for key, value in request["text"].items() if key not in {"line_gap", "line_starts"}}, "arrangement": "overlap",
                           "text": "今日灵感", "tiers": [{key: value for key, value in request["text"]["tiers"][0].items() if key != "size_ratio"}]}
        request["text"]["effects"][0]["target"] = "block"
        request["parameters"] = [{"name": "color", "title": "颜色", "targets": [{"instance_id": "upper", "parameter": "color"}]}, *request["parameters"][2:]]
        with pytest.raises(ToolFault) as refused:
            await current.execute("sprite.compose", SpriteComposeInput.model_validate(request), available())
        assert refused.value.error.field == "/text/arrangement" and "Only a two-tier text takes arrangement" in refused.value.error.message

    asyncio.run(run())


@needs_bundler
def test_a_new_works_text_block_gets_a_built_in_fade_in_and_out(tmp_path, monkeypatch):
    """审计复现（作品 afb7aeb1）：新作品曾保存出完全不动、还复用自带动画旧 Preset 的字效。收缩创作面后，模型不必再写入场/出场：
    新作品文字块没写时宿主补一个内置 fade 入场与出场（effect1/effect2，默认开启，可在编辑器关掉）；已有一个时只补缺的那个；
    编辑更早的字效（没有 still 标记）不补、也不定格各栏。"""
    monkeypatch.setattr(FontStore, "active", SimpleNamespace(families=lambda: list(LIBRARY)))

    async def run():
        """Compose without effects, with only an entrance, with explicit switched-off fades, and in an edit of an older text effect."""
        current = session(tmp_path / "new")
        tier, glow, fade = await stored(current)
        bare = await current.execute("sprite.compose", two_tiers(tier, glow), available())
        block = current.sprite_drafts[bare["draft_id"]]
        assert [(item.instance_id, item.builtin, item.slot) for item in block.text.effects] == [("fadeIn", "fade", "enter"), ("fadeOut", "fade", "exit")]
        assert block.default_parameters["effect1"] is True and block.default_parameters["effect2"] is True
        assert 'Preset: __BUILTINS["fade"]' in block.code
        # Only the entrance is given, so only the missing exit is added.
        half = await current.execute("sprite.compose", two_tiers(tier, glow, effects=fades(fade)[:1]), available())
        assert [item.slot for item in current.sprite_drafts[half["draft_id"]].text.effects] == ["enter", "exit"]
        # Explicit fades keep their switches off rather than being replaced by the defaults.
        still = await current.execute("sprite.compose", two_tiers(tier, glow, effects=fades(fade, enabled=False)), available())
        assert {name: current.sprite_drafts[still["draft_id"]].default_parameters[name] for name in ("effect1", "effect2")} == {"effect1": False, "effect2": False}
        legacy = SimpleNamespace(text=SimpleNamespace(still=None), instances=[])
        base = TemplateCandidate(tsx_code="export default function A(){return null}", config_schema={"type": "object"}, default_config={})
        edit = session(tmp_path / "edit", spec=SimpleNamespace(sprite=legacy, sprite_kind="text"), base=base)
        tier, glow, _ = await stored(edit)
        composed = await edit.execute("sprite.compose", two_tiers(tier, glow), available())
        # The older text effect's tier Presets keep playing their own motion: the host does not hold them.
        assert edit.sprite_drafts[composed["draft_id"]].text.still is None and "__Hold" not in edit.sprite_drafts[composed["draft_id"]].code

    asyncio.run(run())


@needs_bundler
@pytest.mark.parametrize("still", [True, False], ids=["current", "legacy"])
def test_an_edit_snapshot_hands_back_the_effects_as_configured(tmp_path, monkeypatch, still):
    """编辑带特效的基线：快照里的特效给出 base_* 草稿、名称、类型，以及用户在配置里改过的位置、时长与开关；照快照重新组合得到同样的配置。
    「只画静止外观」的标记随基线保留：有标记的基线重新组合仍带标记，没有标记的旧字效（text Preset 自己画动画）重新组合也不带。"""
    monkeypatch.setattr(FontStore, "active", SimpleNamespace(families=lambda: list(LIBRARY)))

    async def run():
        """Compose a base, revise its effects like a user would, then recompose from the edit snapshot."""
        first = session(tmp_path / "first")
        tier, glow, fade = await stored(first)
        request = with_effects(two_tiers(tier, glow), await effect_sources(first))
        composed = await first.execute("sprite.compose", SpriteComposeInput.model_validate(request), available())
        sprite = first.sprite_drafts[composed["draft_id"]]
        if not still:
            sprite = sprite.model_copy(update={"text": sprite.text.model_copy(update={"still": None})})
        config = {**sprite.default_parameters, "effect1Target": "line1", "effect1Seconds": 1.2, "effect1Delay": 0.5, "effect4": False, "shineColor": "#FF0000"}
        base = TemplateCandidate(tsx_code=sprite.code, config_schema=sprite.parameter_schema, default_config=config)
        edit = session(tmp_path / "edit", spec=SimpleNamespace(sprite=sprite, sprite_kind="text"), base=base)
        snapshot = edit.snapshot()["base"]
        effects = snapshot["text"]["effects"]
        assert effects[0] == {"instance_id": "fadeIn", "source": {"kind": "draft", "draft_id": "base_fadeIn"}, "parameters": {}, "title": "淡入",
                              "slot": "enter", "target": "line1", "seconds": 1.2, "delay": 0.5, "enabled": True}
        assert (effects[3]["enabled"], "seconds" in effects[3], effects[3]["source"], effects[3]["parameters"]) == (
            False, False, {"kind": "draft", "draft_id": "base_shine"}, {"glowColor": "#FF0000"})
        assert "shineColor" in [item["name"] for item in snapshot["parameters"]]
        again = await edit.execute("sprite.compose", SpriteComposeInput.model_validate(
            {"description": "双栏标题", "kind": "text", "text": snapshot["text"], "parameters": snapshot["parameters"]}), available())
        recomposed = edit.sprite_drafts[again["draft_id"]]
        assert {name: recomposed.default_parameters[name] for name in ("effect1Target", "effect1Seconds", "effect4", "shineColor")} == {
            "effect1Target": "line1", "effect1Seconds": 1.2, "effect4": False, "shineColor": "#FF0000"}
        assert recomposed.text.still is (True if still else None)

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
        tier, glow, fade = await stored(current)
        if "role" in change:
            legacy = await current.execute("preset.create", preset(None, TIER_CODE.replace("Keyword: React.ComponentType<{text: string}>; ", ""), "color"), available())
            tier = legacy["preset"]["preset_id"]
        request = two_tiers(tier, glow, fade).model_dump(mode="json", exclude_none=True)
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
            tier, *_ = await stored(current)
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
    """编辑以文字块组合的基线：快照给出文字块（各栏的 base_* 草稿、当前配置里的字体与字号比例、排布、栏间距、行起点与关键词），
    只列出 Agent 的样式绑定、不列宿主的配置参数；照快照原样重新组合得到同一份源码。"""
    monkeypatch.setattr(FontStore, "active", SimpleNamespace(families=lambda: list(LIBRARY)))

    async def run():
        """Compose a base, revise one configuration value like a user would, then recompose from the edit snapshot."""
        first = session(tmp_path / "first")
        tier, glow, fade = await stored(first)
        composed = await first.execute("sprite.compose", two_tiers(tier, glow, fade), available())
        sprite = first.sprite_drafts[composed["draft_id"]]
        config = {**sprite.default_parameters, "line1SizeRatio": 0.6, "tierLayout": "overlap"}
        base = TemplateCandidate(tsx_code=sprite.code, config_schema=sprite.parameter_schema, default_config=config)
        edit = session(tmp_path / "edit", spec=SimpleNamespace(sprite=sprite, sprite_kind="text"), base=base)
        snapshot = edit.snapshot()["base"]
        assert "instances" not in snapshot and [item["name"] for item in snapshot["parameters"]] == ["line1Color", "line2Color", "keywordEffectColor"]
        block = snapshot["text"]
        assert [item["source"] for item in block["tiers"]] == [{"kind": "draft", "draft_id": "base_upper"}, {"kind": "draft", "draft_id": "base_lower"}]
        assert block["tiers"][0]["size_ratio"] == 0.6 and block["tiers"][1]["font_family"] == "Zed Display"
        assert (block["arrangement"], block["line_gap"], block["line_starts"], block["keyword"]["source"]["draft_id"]) == ("overlap", 24, [0, 12], "base_keyword")
        again = await edit.execute("sprite.compose", SpriteComposeInput.model_validate(
            {"description": "双栏标题", "kind": "text", "text": block, "parameters": snapshot["parameters"]}), available())
        assert edit.sprite_drafts[again["draft_id"]].default_parameters["line1SizeRatio"] == 0.6
        assert edit.sprite_drafts[again["draft_id"]].default_parameters["tierLayout"] == "overlap"

    asyncio.run(run())


@pytest.mark.parametrize(("role", "usage", "field", "message"), [
    ("effect", None, "/usage", "An effect Preset records how it is used"),
    ("text", FADE_USAGE, "/usage", "Only an effect Preset (role effect) records a usage"),
    ("effect", {"title": "发光", "slot": "loop", "target": "block", "seconds": 1}, "/usage/seconds", "leave usage.seconds out"),
    ("effect", {"title": "显现", "slot": "enter", "target": "plainChars"}, "/usage/stagger", "Choose usage.stagger explicitly"),
    ("effect", {"title": "发光", "slot": "loop", "target": "chars", "stagger": 1}, "/usage/stagger", "Only an entrance, exit or cue"),
    ("effect", {"title": "淡入", "slot": "enter", "target": "block", "stagger": 0}, "/usage/stagger", "Only an entrance, exit or cue"),
], ids=["effect-without", "text-with", "loop-seconds", "glyph-without-timing", "loop-with-timing", "block-with-timing"])
def test_only_effect_presets_record_a_usage(tmp_path, role, usage, field, message):
    """特效 Preset 新建时必须记下默认用法（名称、类型、位置、时长），以后的作品检索到就能原样复用；其他角色不记，持续特效没有时长。"""
    data = (effect_preset(FADE_CODE) if role == "effect" else preset("text", TIER_CODE, "color")).model_dump(mode="json", exclude_unset=True)
    data.pop("usage", None)
    draft = PresetDraft.model_validate({**data, **({"usage": usage} if usage else {})})
    with pytest.raises(ToolFault) as refused:
        asyncio.run(session(tmp_path).execute("preset.create", draft, available()))
    assert refused.value.error.field == field and message in refused.value.error.message


@needs_bundler
def test_an_effect_entry_takes_what_it_leaves_out_from_its_presets_usage(tmp_path, monkeypatch):
    """检索到的特效只要按 instance_id 与 source 引用：省略的名称、类型、位置与时长取自 Preset 记下的默认用法，给出的优先；
    Preset 没有记下用法时省略即按字段拒绝。组合的实例快照记下取自的库内 Preset，版本据此登记用到的配方。"""
    from server.remotion_templates.tools.catalog_store import CatalogStore
    from server.remotion_templates.tools.contracts import PresetRecord

    monkeypatch.setattr(FontStore, "active", SimpleNamespace(families=lambda: list(LIBRARY)))

    async def run():
        """Compose with bare effect entries, then with one whose Preset records no usage."""
        current = session(tmp_path)
        tier, glow, fade = await stored(current)
        typing = await current.execute("preset.create", effect_preset(FADE_CODE, usage={"title": "打字", "slot": "enter", "target": "tiers", "seconds": 1.2}), available())
        request = two_tiers(tier, glow).model_dump(mode="json", exclude_none=True)
        request["text"]["effects"] = [{"instance_id": "typeIn", "source": {"kind": "stored", "preset_id": typing["preset"]["preset_id"]}},
                                      {"instance_id": "fadeOut", "source": {"kind": "stored", "preset_id": fade}, "title": "淡出", "slot": "exit"}]
        composed = await current.execute("sprite.compose", SpriteComposeInput.model_validate(request), available())
        sprite = current.sprite_drafts[composed["draft_id"]]
        assert [(item.title, item.slot) for item in sprite.text.effects] == [("打字", "enter"), ("淡出", "exit")]
        defaults = sprite.default_parameters
        assert (defaults["effect1Target"], defaults["effect1Seconds"], defaults["effect2Target"], defaults["effect2Seconds"]) == ("tiers", 1.2, "block", 0.5)
        assert [item.preset.source_preset_id for item in sprite.instances] == [tier, tier, glow, typing["preset"]["preset_id"], fade]
        # An effect Preset stored before usages existed.
        CatalogStore().append_preset(PresetRecord(preset_id="older-fade", created_at="2026-10-20T00:00:00+00:00",
                                                  **effect_preset(FADE_CODE).model_dump(mode="json", exclude_unset=True, exclude={"usage"})))
        request["text"]["effects"][1]["source"] = {"kind": "stored", "preset_id": "older-fade"}
        del request["text"]["effects"][1]["title"]
        with pytest.raises(ToolFault) as refused:
            await current.execute("sprite.compose", SpriteComposeInput.model_validate(request), available())
        assert refused.value.error.field == "/text/effects/1/title" and "records no usage" in refused.value.error.message

    asyncio.run(run())


def test_review_frames_follow_the_sprites_own_timing():
    """回看优先保留下栏切换前、中、后、出场与末帧，其次每个入场一帧，最后才是泛用的中间帧（切换帧已说明停留状态）；
    至多六帧且标签不宣称视觉通过。"""
    from server.remotion_templates.tools.session import review_frames

    effects = [SimpleNamespace(slot=slot, title=title) for slot, title in (("enter", "淡入"), ("cue", "劈开"), ("exit", "淡出"), ("enter", "上浮"))]
    text = SimpleNamespace(effects=effects, layout=None)
    defaults = {"effect1": True, "effect1Seconds": 0.5, "effect2": True, "effect2Seconds": 1 / 3, "effect3": True, "effect3Seconds": 0.5,
                "effect4": False, "effect4Seconds": 3, "lineStartFrames": [0, 60]}
    sprite = SimpleNamespace(composition=SimpleNamespace(duration_frames=90, fps=30), text=text, default_parameters=defaults)
    # 切换与出场占满六帧时，让位的是中间帧而不是入场：作品的入场曾整帧不见。
    assert review_frames(sprite) == [(7, "half way into the entrance 淡入"), (59, "just before the lower tier appears"),
                                     (65, "half way into the effects played when the lower tier appears"),
                                     (70, "end of the effects played when the lower tier appears"),
                                     (82, "half way out of the exit"), (89, "last frame of the preview")]
    # Entrances one after another each get a frame, so their order shows.
    staggered = SimpleNamespace(composition=SimpleNamespace(duration_frames=150, fps=30), default_parameters={
        "effect1": True, "effect1Seconds": 1, "effect2": True, "effect2Seconds": 0.2, "effect2Delay": 1},
        text=SimpleNamespace(effects=[SimpleNamespace(slot="enter", title="打字"), SimpleNamespace(slot="enter", title="显现")], layout=None))
    assert review_frames(staggered) == [(15, "half way into the entrance 打字"), (33, "half way into the entrance 显现"),
                                        (74, "middle of the preview"), (149, "last frame of the preview")]
    other = SimpleNamespace(composition=SimpleNamespace(duration_frames=90, fps=30), text=None, default_parameters={})
    assert [frame for frame, _ in review_frames(other)] == [22, 44, 66]


def test_review_frames_preserve_the_failed_examples_exit_and_use_tier_clocks():
    """作品 ba6d769e 的两个入场曾占满四帧、遗漏退场；另核对下栏实际起点、剩余长度和 JS 半帧取整，以及跨栏入场在标签上写明是哪一栏。"""
    from server.remotion_templates.tools.session import review_frames

    sprite = SimpleNamespace(composition=SimpleNamespace(duration_frames=150, fps=30), text=SimpleNamespace(effects=[
        SimpleNamespace(slot="enter", title="淡入"), SimpleNamespace(slot="enter", title="横向裁切"), SimpleNamespace(slot="exit", title="淡出")]),
        default_parameters={"effect1Seconds": 0.3, "effect1Target": "block", "effect2Seconds": 1.1, "effect2Target": "tiers",
                            "effect3Seconds": 0.5, "effect3Target": "block", "lineStartFrames": [0, 8]})
    assert review_frames(sprite) == [(4, "half way into the entrance 淡入"), (7, "just before the lower tier appears"),
                                     (16, "half way into the entrance 横向裁切 (upper tier)"), (74, "middle of the preview"),
                                     (142, "half way out of the exit"), (149, "last frame of the preview")]
    sprite.composition.duration_frames = 90
    sprite.text.effects = [SimpleNamespace(slot="enter", title="下栏入场"), SimpleNamespace(slot="cue", title="切换")]
    sprite.default_parameters = {"effect1Seconds": 3, "effect1Target": "line2", "effect2Seconds": 0.15,
                                 "effect2Target": "line2", "lineStartFrames": [0, 60]}
    assert review_frames(sprite) == [(44, "middle of the preview"), (59, "just before the lower tier appears"),
                                     (62, "half way into the effects played when the lower tier appears"),
                                     (65, "end of the effects played when the lower tier appears"),
                                     (67, "half way into the entrance 下栏入场 (lower tier)"), (89, "last frame of the preview")]


@needs_bundler
def test_a_saved_sprite_comes_back_as_frames_for_two_reviews(tmp_path, monkeypatch):
    """保存时同一次挂载截下的帧在回执里依次列出（排在参考图之后的图片序号、帧号与应显示的内容）；默认没有关键词的字效按工作区预览的
    示例关键词截帧（挂载时作为参数覆盖传入）。每个任务两次回看，第三次保存不再截帧，之前的帧也不再附带。审计复现（作品 eb692e7e）：
    回看后只重新组合、没有再保存，上一版的帧一直附着，模型以为改动无效，空转 40 轮直到超时；现在重新组合即撤下旧帧。"""
    monkeypatch.setattr(FontStore, "active", SimpleNamespace(families=lambda: list(LIBRARY)))

    async def run():
        """Save the same composed block three times with a mount that writes the frames it is asked for."""
        current = session(tmp_path)
        current.images = [tmp_path / "reference.png"]
        mounted = current.validate_render

        async def capturing(request, **options):
            """Write a placeholder for every requested frame, then pass the mount."""
            capture = options.get("capture")
            if capture:
                assert capture.parameters == {"keywords": ["判断"]}
                assert options["text_fit_parameters"] == {}
                capture.directory.mkdir(parents=True)
                for frame in capture.frames:
                    (capture.directory / f"frame-{frame}.png").write_bytes(b"png")
            return await mounted(request, **options)

        current.validate_render = capturing
        tier, glow, fade = await stored(current)
        composed = await current.execute("sprite.compose", two_tiers(tier, glow, fade), available())
        receipts = [await current.execute("sprite.create", SpriteCreateInput(draft_id=composed["draft_id"]), available()) for _ in range(3)]
        first = receipts[0]["review"]
        assert [item["image"] for item in first["frames"]] == [2, 3, 4, 5, 6, 7] and first["rounds_left"] == 1
        # The defaults have no keywords, so the frames show the workspace preview's sample one.
        assert first["keywords"] == ["判断"]
        assert [(item["frame"], item["label"]) for item in first["frames"]] == [
            (7, "half way into the entrance 淡入 (upper tier)"), (11, "just before the lower tier appears"),
            (14, "middle of the preview"), (16, "half way into the entrance 淡入 (lower tier)"),
            (22, "half way out of the exit"), (29, "last frame of the preview")]
        assert receipts[1]["review"]["rounds_left"] == 0 and "review" not in receipts[2]
        assert current.review_frames == []
        # Frames show the saved Sprite only: composing anew takes them away until the next save.
        current.review_frames = [(tmp_path / "frame-7.png", 7, "half way into the entrance 淡入")]
        await current.execute("sprite.compose", two_tiers(tier, glow, fade), available())
        assert current.review_frames == []

    asyncio.run(run())


def test_preset_search_ranks_by_matched_words_and_proven_uses(tmp_path):
    """Preset 库是此前作品做成功的配方：查询切成词（中文不用空格），按描述与特效名称命中的词数排序，一个词都不中的不列出；同分时
    存进资产库的版本用过的在前，再按成功版本用过的次数、再按时间。可按角色、特效类型筛选，摘要带特效的默认用法与使用次数。"""
    from server.remotion_templates.tools.catalog_store import CatalogStore

    async def run():
        """Store Presets, record uses for one of them and search."""
        current = session(tmp_path)
        tier = await current.execute("preset.create", preset("text", TIER_CODE, "color").model_copy(update={"description": "金色描边大字，双栏下栏"}), available())
        await current.execute("preset.create", preset("keyword", KEYWORD_CODE, "glowColor").model_copy(update={"description": "关键词发光"}), available())
        await current.execute("preset.create", preset(None, TIER_CODE, "color").model_copy(update={"description": "金色描边旧字效"}), available())
        typing = effect_preset(FADE_CODE, usage={"title": "逐字淡入", "slot": "enter", "target": "chars", "seconds": 1, "stagger": 1})
        await current.execute("preset.create", typing.model_copy(update={"description": "逐字淡入：每个字依次显现，像打字机"}), available())
        await current.execute("preset.create", effect_preset(SHINE_CODE, "glowColor", {"title": "发光", "slot": "loop", "target": "block"}), available())
        found = lambda **query: [item.description for item in current.search_presets(PresetSearchInput(**query)).presets]
        assert found(query="双栏 描边") == ["金色描边大字，双栏下栏", "金色描边旧字效"]
        assert found(query="打字机逐字淡入") == ["逐字淡入：每个字依次显现，像打字机"]
        assert found(query="劈开") == []
        assert found(role="keyword") == ["关键词发光"]
        assert [item.role for item in current.search_presets(PresetSearchInput(query="金色", role="text")).presets] == ["text"]
        loops = current.search_presets(PresetSearchInput(role="effect", slot="loop")).presets
        assert [item.usage.title for item in loops] == ["发光"]
        # Equal matches: the newest first, until accepted and published versions used the older one.
        assert found(query="金色 描边", role="text") == ["金色描边大字，双栏下栏"]
        assert found(query="描边") == ["金色描边旧字效", "金色描边大字，双栏下栏"]
        CatalogStore().record_uses("version-1", [tier["preset"]["preset_id"]])
        CatalogStore().mark_published("version-1")
        assert found(query="描边") == ["金色描边大字，双栏下栏", "金色描边旧字效"]
        summary = current.search_presets(PresetSearchInput(query="描边")).presets[0]
        assert (summary.accepted_uses, summary.published_uses) == (1, 1)

    asyncio.run(run())


def validator(tmp_path: Path) -> ToolValidator:
    """The isolated validator over the real sandboxed browser."""
    return ToolValidator(Renderer(Settings(_env_file=None, data_dir=tmp_path, **BROWSER)), tmp_path / "tools")


@needs_browser
def test_real_type_check_gives_role_presets_the_host_props(tmp_path):
    """真实类型检查：text 角色的 Preset 按宿主传入的文字、片段、Keyword 与字体检查，通过；把 Keyword 写成别的 prop 形状就报 TypeScript 错误。
    画宿主 children 的新写法同样按宿主的 prop 检查，把 children 写成字符串就报错。keyword 角色的 Preset 收到 text 同样通过。"""
    checker = validator(tmp_path)
    assert asyncio.run(checker.validate_code(preset("text", TIER_CODE, "color"))).passed
    assert asyncio.run(checker.validate_code(preset("text", CHILD_TIER, "color"))).passed
    assert asyncio.run(checker.validate_code(preset("keyword", KEYWORD_CODE, "glowColor"))).passed
    wrong = preset("text", TIER_CODE.replace("Keyword: React.ComponentType<{text: string}>", "Keyword: React.ComponentType<{label: string}>")
                   .replace("<Keyword key={index} text={segment.text} />", "<Keyword key={index} label={segment.text} />"), "color")
    report = asyncio.run(checker.validate_code(wrong))
    assert not report.passed and any(item.severity == "error" and "Keyword" in item.message for item in report.diagnostics)
    report = asyncio.run(checker.validate_code(preset("text", CHILD_TIER.replace("children: React.ReactNode", "children: string"), "color")))
    assert not report.passed and any(item.severity == "error" and "children" in item.message for item in report.diagnostics)
    assert asyncio.run(checker.validate_code(effect_preset(FADE_CODE))).passed
    assert asyncio.run(checker.validate_code(effect_preset(SHINE_CODE, "glowColor"))).passed



@needs_browser
def test_real_two_tier_block_with_a_keyword_preset_passes_the_generation_measurements(tmp_path, monkeypatch):
    """真实浏览器：上栏 0.5 倍小字、下栏大字、关键词发光的双栏文字块带着各栏淡入、整体淡出、关键词弹出与下栏发光通过透明度与排版检查：
    100 字分到两栏完整显示，两栏由同一个文字块渲染、同时出现与消失，下栏出现时上栏不跳动，关键词命中原位显示且整段文字仍然完整。"""
    monkeypatch.setattr(FontStore, "active", SimpleNamespace(families=lambda: ["Noto Sans CJK SC"]))

    async def run():
        """Compose the block with its effects, then mount it with the generation-path measurements."""
        current = session(tmp_path / "compose")
        tier, glow, fade = await stored(current)
        request = with_effects(two_tiers(tier, glow), await effect_sources(current))
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


# 一栏文字，白色描边只写在非关键词片段的 span 上：关键词片段由宿主的 Keyword 渲染，拿不到描边（模板「民生科普」标题字效的写法）。
SPAN_STROKE_TIER = TIER_CODE.replace("<span key={index}>", '<span key={index} style={{WebkitTextStroke: "8px #FFFFFF", paintOrder: "stroke fill"}}>')
# 同样的描边写在这一栏自己的块上，关键词片段继承它。
BLOCK_STROKE_TIER = TIER_CODE.replace("color: props.color}}", 'color: props.color, WebkitTextStroke: "8px #FFFFFF", paintOrder: "stroke fill"}}')


@needs_browser
@pytest.mark.parametrize(("code", "lost"), [(SPAN_STROKE_TIER, True), (BLOCK_STROKE_TIER, False)], ids=["spans", "block"])
def test_real_keyword_hit_keeps_its_tiers_look(tmp_path, monkeypatch, code, lost):
    """真实浏览器，审计复现（模板「民生科普」）：上栏的白色描边只写在非关键词片段上，模板编辑预览的示例关键词与成片的标题关键词
    都没有描边。生成检查以默认文字开头两个字为关键词、关闭关键词选项挂载时，命中词须与同一栏其余文字外观一致，否则 text_fit 指出差异；
    描边写在这一栏自己的块上就通过。"""
    monkeypatch.setattr(FontStore, "active", SimpleNamespace(families=lambda: ["Noto Sans CJK SC"]))

    async def run():
        """Compose a two-tier block over the tier ``code``, fading in and out, and mount it with the generation measurements."""
        current = session(tmp_path / "compose")
        tier = await current.execute("preset.create", preset("text", code, "color"), available())
        fade = await current.execute("preset.create", effect_preset(FADE_CODE), available())
        request = two_tiers(tier["preset"]["preset_id"], None, fade["preset"]["preset_id"]).model_dump(mode="json", exclude_none=True)
        request["text"]["tiers"][1]["font_family"] = "Noto Sans CJK SC"
        composed = await current.execute("sprite.compose", SpriteComposeInput.model_validate(request), available())
        sprite = current.sprite_drafts[composed["draft_id"]]
        component = ComponentDefinition(code=sprite.code, parameter_schema=sprite.parameter_schema, default_parameters=sprite.default_parameters)
        return await validator(tmp_path).validate_render(RenderValidationInput(component=component, duration_frames=30), text_fit=True)

    check = next(item for item in asyncio.run(run()).checks if item.name == "text_fit")
    if lost:
        assert check.status == "failed"
        assert 'With keywords ["判断"] and the keyword options off, the hit "判断" is drawn without its tier\'s look' in check.message
        assert "-webkit-text-stroke-width is 0px instead of 8px" in check.message
        assert "on its own block, so every <Keyword> segment inside inherits it" in check.message
    else:
        assert check.status == "passed", check.message


def test_the_executor_draws_a_tiers_look_on_its_block():
    """审计复现（模板「民生科普」）：规则只说 text Preset 画出这一栏的描边与阴影，没说画在哪一层，模型把白描边写在非关键词片段的 span 上，
    关键词命中就没有描边。规则现在要求 text Preset 只渲染宿主排好的 children（关键词命中已在其中），外观画在包住它的块上，整行继承。"""
    from server.remotion_templates.agent import EXECUTOR_RULES

    assert "draws the tier's look on that same block" in EXECUTOR_RULES
    assert "renders {props.children} inside one block of its own" in EXECUTOR_RULES
    assert "so the whole line inherits it" in EXECUTOR_RULES


def test_text_and_keywords_are_preview_samples_not_the_thing_to_ask_about():
    """审计复现（作品 58be1abc、e208020e）：Outer 追问用户文案、关键词与语言；Executor 把空的 step_inputs 当成没收到需求、报 needs_input 去问。
    字效的文字与关键词由模板每次渲染时传入，生成只需要预览用的示例文字；规则要说明这一点，并说明需求在 user_intent 里。
    重构后允许为真正的歧义提问，但这些由模板注入的内容仍不应成为问题。"""
    from server.remotion_templates.agent import EXECUTOR_RULES, OUTER_RULES

    assert "are injected by templates on each render" in OUTER_RULES
    assert "do not ask about those unless the user clearly wants a specific wording chosen now" in OUTER_RULES
    assert "its default text and keywords are only the preview sample" in EXECUTOR_RULES
    assert "The request is user_intent, in the host handoff and at the top of the snapshot" in EXECUTOR_RULES


def test_the_executor_recognizes_the_words_a_request_singles_out():
    """指令回归：用户点名某个词要单独变色或单独动效时，Executor 会把效果画到普通文字上，或改用默认文字的开头字当示例关键词，
    回看帧因此对不上要求。规则要求认出任意被点名的原文片段、多个选区分别保留，写进 text.preview_keywords，并说明示例词与位置不能写死在
    Preset 里（真实文字与关键词每次渲染由总线传入）。只校验规则措辞，不代表模型识别准确。"""
    from server.remotion_templates.agent import EXECUTOR_RULES

    assert "use that exact substring of the sample text in text.preview_keywords" in EXECUTOR_RULES
    assert "one to five words of the default text, each at most twelve characters" in EXECUTOR_RULES
    assert "arbitrary user-selected words, not a fixed vocabulary or the opening characters" in EXECUTOR_RULES
    assert "keep multiple explicitly selected words separate" in EXECUTOR_RULES
    assert "The real text and keywords come from the bus on every render" in EXECUTOR_RULES
    assert "never hard-code the sample word, its character indices or its position" in EXECUTOR_RULES
    assert "leave the keyword options at their defaults: the six off and keywords empty" in EXECUTOR_RULES
    assert "chars, block, tiers or line1/line2 targets also affect ordinary text" in EXECUTOR_RULES


def test_the_review_keyword_description_matches_the_sampler():
    """文档回归：回看帧与字效工作区预览优先文字块的 preview_keywords，未指定时才退回开头两字，而排版检查 text_fit 仍以开头两字挂载；
    契约文档、SpriteReview 与 AGENTS.md 须说法一致，不能只改一处又留下相反的旧描述。"""
    from server.remotion_templates.tools.contracts import SpriteReview

    root = Path(__file__).parents[2]
    document = (root / "docs/remotion-agent-tool-contracts.md").read_text(encoding="utf-8")
    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "优先文字块的 preview_keywords" in SpriteReview.__doc__
    assert "优先文字块的 `preview_keywords`" in document
    assert "排版检查 `text_fit` 的关键词挂载不看 `preview_keywords`" in document
    assert "默认文字首个非空行开头至多两个字，与字效工作区预览、排版检查的关键词挂载相同" not in document
    assert "以当前文字的同样位置为示例关键词" not in agents


def test_the_executor_knows_what_hides_a_gradient_fill_and_how_to_order_parts():
    """审计复现（作品 c0993bf2、eb692e7e）：上栏用 background-clip: text 画渐变、同一元素又用 text-shadow 叠出描边，阴影画在渐变上面，
    整行成了深棕色块；要「关键词先出、其余随后」时模型只放了一个逐字入场。规则说明这条渲染事实，以及分开的入场用延迟排先后。"""
    from server.remotion_templates.agent import EXECUTOR_RULES

    assert "a text-shadow on the same element is painted on top of it" in EXECUTOR_RULES
    assert "order them with delay" in EXECUTOR_RULES
    assert "a sprite_compose takes them away" in EXECUTOR_RULES


def test_the_executor_makes_transparency_an_effect_and_allows_negative_line_gaps():
    """透明度有时被做成带 alpha 的字色：规则要求透明（整体或某一栏透出视频）是带可调数字样式参数的持续特效，
    颜色保持不透明；栏间距为负时下栏向上移，可以叠住上栏。"""
    from server.remotion_templates.agent import EXECUTOR_RULES

    assert "Transparency is an effect, never a colour" in EXECUTOR_RULES
    assert "never write it as an alpha colour" in EXECUTOR_RULES
    assert "negative values pull the lower tier up over the upper one" in EXECUTOR_RULES


def test_a_keyword_hit_without_its_tiers_look_gets_no_layout_hint(tmp_path, monkeypatch):
    """关键词丢了所在栏外观是 text_fit 的一种失败，但不是排版问题：拒绝照常列出原因，只是不附「按内容排版、换行」的固定提示，
    免得模型去改排版；其他 text_fit 失败仍附这条提示。"""
    monkeypatch.setattr(FontStore, "active", SimpleNamespace(families=lambda: list(LIBRARY)))
    look = 'With keywords ["判断"] and the keyword options off, the hit "判断" is drawn without its tier\'s look: -webkit-text-stroke-width is 0px instead of 8px.'

    async def refused(message: str) -> ToolFault:
        """Save a composed block whose browser mount fails text_fit with ``message``; return the refusal."""
        current = session(tmp_path / str(len(message)))
        tier, _glow, fade = await stored(current)
        composed = await current.execute("sprite.compose", two_tiers(tier, None, fade), available())

        async def failing(request, **_options):
            """The mount fails only the text_fit check."""
            return RenderValidationReport(
                passed=False, composition=Composition(width=1080, height=1920, fps=30, duration_frames=request.duration_frames),
                code_validation=CodeValidationReport(passed=True, diagnostics=[]), checks=[CheckResult(name="text_fit", status="failed", message=message)],
                tests=[], custom_tests_executed=0,
            )

        current.validate_render = failing
        with pytest.raises(ToolFault) as error:
            await current.execute("sprite.create", SpriteCreateInput(draft_id=composed["draft_id"]), available())
        return error.value

    lost = asyncio.run(refused(look))
    assert f"- text_fit: {look}" in lost.error.message.splitlines()
    assert "hints" not in lost.error.details
    clipped = asyncio.run(refused("The default text is not shown in full: it is cut off."))
    assert [hint.split(":")[0] for hint in clipped.error.details["hints"]] == ["Lay the text out from its content"]


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
    code, schema, defaults, _ = compose_text_source(block, [{**tier, "instance_id": "upper"}, {**tier, "instance_id": "lower"}], None, [], [], ["Noto Sans CJK SC"])
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


# 一栏：上半是随帧淡入淡出的实心色块（这一栏自己的动画），下半只画关键词命中。
FADING_TIER = """import React from "react";
import {useCurrentFrame, useVideoConfig} from "remotion";
export default function Fading(props: {text: string; segments: {text: string; keyword: boolean}[]; Keyword: React.ComponentType<{text: string}>; color: string}) {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  const {Keyword} = props;
  return <div style={{height: 160, opacity: Math.min(1, frame / 10, (durationInFrames - 1 - frame) / 10)}}><div style={{height: 80, background: props.color}} />{props.segments.map((segment, index) => segment.keyword ? <Keyword key={index} text={segment.text} /> : null)}</div>;
}
"""
# 关键词命中：80 像素高的色块，按帧淡入。
FADING_KEYWORD = """import {useCurrentFrame} from "remotion";
export default function Box(props: {text: string}) {
  return <span style={{display: "inline-block", width: 180, height: 80, verticalAlign: "top", background: "#0000FF", opacity: Math.min(1, useCurrentFrame() / 10)}} />;
}
"""


@needs_browser
def test_real_render_holds_the_tier_and_keeps_the_keyword_live(tmp_path):
    """真实沙箱渲染：新作品（still）的一栏被宿主定格在中间帧，它自己的淡入淡出不再播放（第 0 帧与最后一帧都完整显示）；
    栏里的关键词 Preset 取回实时帧，仍按自己的动画在 10 帧内淡入。"""
    empty = {"type": "object", "properties": {}, "additionalProperties": False}
    tier = {"instance_id": "body", "preset": {"code": FADING_TIER, "parameter_schema": {"type": "object", "properties": {"color": COLOR}, "required": ["color"], "additionalProperties": False},
                                              "default_parameters": {"color": "#FF0000"}}, "parameters": {"color": "#FF0000"}}
    keyword = {"instance_id": "keyword", "preset": {"code": FADING_KEYWORD, "parameter_schema": empty, "default_parameters": {}}, "parameters": {}}
    block = {"layout": {"x": 0, "y": 0, "width": 180, "height": 160}, "text": "上", "font_size": 96, "tiers": [{"font_family": "Noto Sans CJK SC", "font_weight": 700}], "keyword": {}}
    code, schema, defaults, _ = compose_text_source(block, [tier], keyword, [], [], ["Noto Sans CJK SC"], still=True)
    sprite = pb.PublishedSprite(
        sprite_id="held", name="held", kind=pb.SPRITE_KIND_TEXT, tsx_code=code, text_prop="text", keywords_prop="keywords",
        canvas=pb.SpriteCanvas(width=180, height=160, fps=30, preview_frames=60), content_box=pb.SpriteBox(x=0, y=0, width=180, height=160),
        config_schema_json=json.dumps(schema), default_config_json=json.dumps(defaults),
        parameters=[pb.SpriteParameter(key="text", access=pb.OPERATOR_ACCESS_VISIBLE_EDITABLE, default_value=pb.ScalarValue(string_value="上")),
                    pb.SpriteParameter(key="keywords", access=pb.OPERATOR_ACCESS_VISIBLE_EDITABLE, default_value=pb.ScalarValue(string_list_value=pb.StringList()))],
    )
    message = pb.SpriteRenderInput(sprite_id="held", effect_total_frames=60, text="上", keywords=["上"])
    settings = Settings(_env_file=None, data_dir=tmp_path / "state", **BROWSER)
    video = asyncio.run(sprite_render.render_sprite(Renderer(settings), sprite_render.plan_sprite_render(message, sprite), tmp_path / "job"))
    block_pixel, keyword_pixel = 20 * 180 + 90, 120 * 180 + 90
    for frame, block_alpha, keyword_alpha in ((0, 1.0, 0.0), (4, 1.0, 0.4), (20, 1.0, 1.0), (59, 1.0, 1.0)):
        assert decoded_alpha(video, frame, block_pixel) == pytest.approx(block_alpha, abs=0.05), frame
        assert decoded_alpha(video, frame, keyword_pixel) == pytest.approx(keyword_alpha, abs=0.05), frame


@needs_browser
def test_real_render_times_each_effect_from_its_position(tmp_path):
    """真实沙箱渲染：各栏淡入从每一栏自己出现的帧起按 0.5 秒（15 帧）升满，整体淡出在结尾降到 0；时长不超过各位置播放帧数的一半减一，
    所以 3 秒的淡出在 60 帧里只占 29 帧，定格的中间帧完整显示。下栏在第二个切片（第 30 帧）才出现，它的淡入也从那里开始。"""
    tier = {"preset": {"code": BLOCK_TIER, "parameter_schema": {"type": "object", "properties": {"color": COLOR}, "required": ["color"], "additionalProperties": False},
                       "default_parameters": {"color": "#FF0000"}}, "parameters": {"color": "#FF0000"},
            "layout": {"x": 0, "y": 0, "width": 180, "height": 320, "z_index": 0}, "timing": {"start_frame": 0, "duration_frames": 60}}
    fade = {**tier, "preset": {"code": FADE_CODE, "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": False}, "default_parameters": {}}, "parameters": {}}
    block = {"layout": {"x": 0, "y": 0, "width": 180, "height": 320}, "text": "上\n下", "font_size": 96, "line_gap": 0, "line_starts": [0, 30],
             "tiers": [{"font_family": "Noto Sans CJK SC", "font_weight": 700, "size_ratio": 1}, {"font_family": "Noto Sans CJK SC", "font_weight": 700, "size_ratio": 1}],
             "effects": [{"title": "淡入", "slot": "enter", "target": "tiers"}, {"title": "淡出", "slot": "exit", "target": "block", "seconds": 3}]}
    code, schema, defaults, _ = compose_text_source(block, [{**tier, "instance_id": "upper"}, {**tier, "instance_id": "lower"}], None,
                                                    [{**fade, "instance_id": "fadeIn"}, {**fade, "instance_id": "fadeOut"}], [], ["Noto Sans CJK SC"])
    sprite = pb.PublishedSprite(
        sprite_id="effects", name="effects", kind=pb.SPRITE_KIND_TEXT, tsx_code=code, text_prop="text",
        canvas=pb.SpriteCanvas(width=180, height=320, fps=30, preview_frames=60), content_box=pb.SpriteBox(x=0, y=0, width=180, height=320),
        config_schema_json=json.dumps(schema), default_config_json=json.dumps(defaults),
        parameters=[pb.SpriteParameter(key="text", access=pb.OPERATOR_ACCESS_VISIBLE_EDITABLE, default_value=pb.ScalarValue(string_value="上\n下"))],
    )
    message = pb.SpriteRenderInput(sprite_id="effects", effect_total_frames=60, cues=[
        pb.SpriteCue(text="一", start_frame=0, end_frame=30, sentence=1), pb.SpriteCue(text="二", start_frame=30, end_frame=60, sentence=1)])
    settings = Settings(_env_file=None, data_dir=tmp_path / "state", **BROWSER)
    video = asyncio.run(sprite_render.render_sprite(Renderer(settings), sprite_render.plan_sprite_render(message, sprite), tmp_path / "job"))
    upper, lower = 20 * 180 + 90, 300 * 180 + 90
    # The 3-second exit is held to half of the 60 frames less one (29): it falls to 0 on the last frame (59). The lower tier's
    # entrance counts from its own start (frame 30) and is held to 14 of its 30 frames; an entering tier inside the exiting
    # block shows at both progresses.
    for frame, upper_alpha, lower_alpha in ((0, 0.0, 0.0), (6, 0.4, 0.0), (20, 1.0, 0.0), (36, 23 / 29, 6 / 14 * 23 / 29), (44, 15 / 29, 15 / 29), (59, 0.0, 0.0)):
        assert decoded_alpha(video, frame, upper) == pytest.approx(upper_alpha, abs=0.05), frame
        assert decoded_alpha(video, frame, lower) == pytest.approx(lower_alpha, abs=0.05), frame


# 特效：每个单元（这里是逐字）按自己的进度显现，并铺一块实心底色，方便按像素读出每个字的显现程度。
BOXED_FADE = FADE_CODE.replace("{opacity: props.progress}", '{opacity: props.progress, background: "#0000FF"}')


@needs_browser
def test_real_render_reveals_the_glyphs_in_turn(tmp_path):
    """真实沙箱渲染（逐字淡入）：入场特效放在 chars，两个字依次分享 20 帧的入场：第一个字在前 10 帧显现，第二个字在后 10 帧显现；
    栏被定格在中间帧，逐字单元仍取实时帧；入场结束后两个字都完整显示。"""
    tier = {"instance_id": "body", "preset": {"code": CHILD_TIER, "parameter_schema": {"type": "object", "properties": {"color": COLOR}, "required": ["color"], "additionalProperties": False},
                                              "default_parameters": {"color": "#FFFFFF"}}, "parameters": {"color": "#FFFFFF"}}
    typing = {"instance_id": "typeIn", "preset": {"code": BOXED_FADE, "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": False}, "default_parameters": {}}, "parameters": {}}
    block = {"layout": {"x": 0, "y": 0, "width": 180, "height": 80}, "text": "上下", "font_size": 80, "tiers": [{"font_family": "Noto Sans CJK SC", "font_weight": 700}],
             "effects": [{"title": "打字", "slot": "enter", "target": "chars", "seconds": 2 / 3}]}
    code, schema, defaults, _ = compose_text_source(block, [tier], None, [typing], [], ["Noto Sans CJK SC"], still=True)
    sprite = pb.PublishedSprite(
        sprite_id="typed", name="typed", kind=pb.SPRITE_KIND_TEXT, tsx_code=code, text_prop="text",
        canvas=pb.SpriteCanvas(width=180, height=80, fps=30, preview_frames=60), content_box=pb.SpriteBox(x=0, y=0, width=180, height=80),
        config_schema_json=json.dumps(schema), default_config_json=json.dumps(defaults),
        parameters=[pb.SpriteParameter(key="text", access=pb.OPERATOR_ACCESS_VISIBLE_EDITABLE, default_value=pb.ScalarValue(string_value="上下"))],
    )
    message = pb.SpriteRenderInput(sprite_id="typed", effect_total_frames=60, text="上下")
    settings = Settings(_env_file=None, data_dir=tmp_path / "state", **BROWSER)
    video = asyncio.run(sprite_render.render_sprite(Renderer(settings), sprite_render.plan_sprite_render(message, sprite), tmp_path / "job"))
    # Two 80-pixel glyphs centred in 180 pixels: the first spans columns 10-90, the second 90-170.
    first, second = 40 * 180 + 50, 40 * 180 + 130
    for frame, first_alpha, second_alpha in ((0, 0.0, 0.0), (5, 0.5, 0.0), (15, 1.0, 0.5), (40, 1.0, 1.0)):
        assert decoded_alpha(video, frame, first) == pytest.approx(first_alpha, abs=0.06), frame
        assert decoded_alpha(video, frame, second) == pytest.approx(second_alpha, abs=0.06), frame


# 一栏：只是一块实心色条，高度与颜色是自己的样式参数（代码里提到 children，宿主就把排好的这一栏交给它，这里不画）。
BAR_TIER = """import React from "react";
export default function Bar(props: {children?: React.ReactNode; color: string; height: number}) {
  return <div style={{height: props.height, background: props.color}} />;
}
"""
# 特效：把包住的内容画两份，分别裁成上半与下半，按进度各自向上、向下移开 1em（这一栏的字号）。
SPLIT_CODE = """import React from "react";
export default function Split(props: {children: React.ReactNode; progress: number; Box: React.ComponentType<{style?: React.CSSProperties; children?: React.ReactNode}>}) {
  const {Box} = props;
  return <Box style={{position: "relative"}}>
    <div style={{clipPath: "inset(0 0 50% 0)", transform: `translateY(${-props.progress}em)`}}>{props.children}</div>
    <div style={{position: "absolute", inset: 0, clipPath: "inset(50% 0 0 0)", transform: `translateY(${props.progress}em)`}}>{props.children}</div>
  </Box>;
}
"""


@needs_browser
def test_real_render_splits_the_upper_tier_when_the_lower_one_appears(tmp_path):
    """真实沙箱渲染（劈开揭示）：两栏重叠排布，同一句的第二个切片开始时下栏出现；上栏的 cue 特效此前不动，之后在 10 帧内把上栏的上半、
    下半各移开 1em（上栏字号 60 像素）。上栏 80 像素高、居中在 120～200 行，劈开后上半在 60～100 行、下半在 220～260 行。"""
    schema = {"type": "object", "properties": {"color": COLOR, "height": {"type": "number", "minimum": 0, "maximum": 400}}, "required": ["color", "height"], "additionalProperties": False}
    upper = {"instance_id": "upper", "preset": {"code": BAR_TIER, "parameter_schema": schema, "default_parameters": {"color": "#FF0000", "height": 80}}, "parameters": {"color": "#FF0000", "height": 80}}
    lower = {**upper, "instance_id": "lower", "parameters": {"color": "#0000FF", "height": 40}}
    split = {"instance_id": "split", "preset": {"code": SPLIT_CODE, "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": False}, "default_parameters": {}}, "parameters": {}}
    block = {"layout": {"x": 0, "y": 0, "width": 180, "height": 320}, "text": "上\n下", "font_size": 96, "arrangement": "overlap", "line_starts": [0, 30],
             "tiers": [{"font_family": "Noto Sans CJK SC", "font_weight": 700, "size_ratio": 0.625}, {"font_family": "Noto Sans CJK SC", "font_weight": 700, "size_ratio": 1}],
             "effects": [{"title": "劈开", "slot": "cue", "target": "line1", "seconds": 1 / 3}]}
    code, schema, defaults, _ = compose_text_source(block, [upper, lower], None, [split], [], ["Noto Sans CJK SC"], still=True)
    sprite = pb.PublishedSprite(
        sprite_id="split", name="split", kind=pb.SPRITE_KIND_TEXT, tsx_code=code, text_prop="text",
        canvas=pb.SpriteCanvas(width=180, height=320, fps=30, preview_frames=60), content_box=pb.SpriteBox(x=0, y=0, width=180, height=320),
        config_schema_json=json.dumps(schema), default_config_json=json.dumps(defaults),
        parameters=[pb.SpriteParameter(key="text", access=pb.OPERATOR_ACCESS_VISIBLE_EDITABLE, default_value=pb.ScalarValue(string_value="上\n下"))],
    )
    message = pb.SpriteRenderInput(sprite_id="split", effect_total_frames=60, cues=[
        pb.SpriteCue(text="一", start_frame=0, end_frame=30, sentence=1), pb.SpriteCue(text="二", start_frame=30, end_frame=60, sentence=1)])
    settings = Settings(_env_file=None, data_dir=tmp_path / "state", **BROWSER)
    video = asyncio.run(sprite_render.render_sprite(Renderer(settings), sprite_render.plan_sprite_render(message, sprite), tmp_path / "job"))
    above, top_half, lower_half, below = (row * 180 + 90 for row in (80, 130, 190, 240))
    for frame, alphas in ((20, (0.0, 1.0, 1.0, 0.0)), (50, (1.0, 0.0, 0.0, 1.0))):
        for pixel, alpha in zip((above, top_half, lower_half, below), alphas):
            assert decoded_alpha(video, frame, pixel) == pytest.approx(alpha, abs=0.05), (frame, pixel // 180)


@needs_browser
def test_real_render_types_the_keyword_in_before_the_rest_appears(tmp_path):
    """真实沙箱渲染（关键词逐字淡入，范围与延迟回归；打字机的离散显字另见 test_remotion_typewriter）：关键词「上下」的两个字在
    keywordChars 依次分享 20 帧的入场；其余的「左右」在 plainChars 推迟 20 帧后，以 stagger 0 在 6 帧里一起显现，而不是又逐个打出。"""
    color = {"type": "object", "properties": {"color": COLOR}, "required": ["color"], "additionalProperties": False}
    tier = {"instance_id": "body", "preset": {"code": CHILD_TIER, "parameter_schema": color, "default_parameters": {"color": "#FFFFFF"}}, "parameters": {"color": "#FFFFFF"}}
    glow = {"instance_id": "glow", "preset": {"code": KEYWORD_CHILD_CODE, "parameter_schema": {"type": "object", "properties": {"glowColor": COLOR}, "required": ["glowColor"], "additionalProperties": False},
                                              "default_parameters": {"glowColor": "#FFD54A"}}, "parameters": {"glowColor": "#FFD54A"}}
    boxed = {"preset": {"code": BOXED_FADE, "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": False}, "default_parameters": {}}, "parameters": {}}
    block = {"layout": {"x": 0, "y": 0, "width": 180, "height": 80}, "text": "上下左右", "font_size": 40, "tiers": [{"font_family": "Noto Sans CJK SC", "font_weight": 700}], "keyword": {},
             "effects": [{"title": "打字", "slot": "enter", "target": "keywordChars", "seconds": 2 / 3},
                         {"title": "显现", "slot": "enter", "target": "plainChars", "seconds": 0.2, "delay": 2 / 3, "stagger": 0}]}
    code, schema, defaults, _ = compose_text_source(block, [tier], glow, [{**boxed, "instance_id": "typeIn"}, {**boxed, "instance_id": "showIn"}], [], ["Noto Sans CJK SC"], still=True)
    assert (defaults["effect2Stagger"], "effect1Stagger" in defaults) == (0, True)
    sprite = pb.PublishedSprite(
        sprite_id="keyword-first", name="keyword-first", kind=pb.SPRITE_KIND_TEXT, tsx_code=code, text_prop="text", keywords_prop="keywords",
        canvas=pb.SpriteCanvas(width=180, height=80, fps=30, preview_frames=60), content_box=pb.SpriteBox(x=0, y=0, width=180, height=80),
        config_schema_json=json.dumps(schema), default_config_json=json.dumps(defaults),
        parameters=[pb.SpriteParameter(key="text", access=pb.OPERATOR_ACCESS_VISIBLE_EDITABLE, default_value=pb.ScalarValue(string_value="上下左右")),
                    pb.SpriteParameter(key="keywords", access=pb.OPERATOR_ACCESS_VISIBLE_EDITABLE, default_value=pb.ScalarValue(string_list_value=pb.StringList()))],
    )
    message = pb.SpriteRenderInput(sprite_id="keyword-first", effect_total_frames=60, text="上下左右", keywords=["上下"])
    settings = Settings(_env_file=None, data_dir=tmp_path / "state", **BROWSER)
    video = asyncio.run(sprite_render.render_sprite(Renderer(settings), sprite_render.plan_sprite_render(message, sprite), tmp_path / "job"))
    # Four 40-pixel glyphs centred in 180 pixels: the keyword's two at columns 10-50 and 50-90, the rest at 90-130 and 130-170.
    pixels = [40 * 180 + column for column in (30, 70, 110, 150)]
    for frame, alphas in ((5, (0.5, 0.0, 0.0, 0.0)), (15, (1.0, 0.5, 0.0, 0.0)), (19, (1.0, 0.9, 0.0, 0.0)), (23, (1.0, 1.0, 0.5, 0.5)), (40, (1.0, 1.0, 1.0, 1.0))):
        for pixel, alpha in zip(pixels, alphas):
            assert decoded_alpha(video, frame, pixel) == pytest.approx(alpha, abs=0.06), (frame, pixel % 180)


# 一栏文字，带一个联合类型的样式参数：对齐方式的默认值 "center" 必须满足 "left" | "center" | "right"。
ALIGNED_TIER = TIER_CODE.replace("color: string})", 'color: string; align: "left" | "center" | "right"})').replace('textAlign: "center"', "textAlign: props.align")


@needs_browser
@pytest.mark.parametrize("variant", ["segments", "children", "split"])
def test_real_creation_saves_a_text_block_from_role_presets(tmp_path, monkeypatch, variant):
    """真实端到端（不替换任何检查）：创建 text 与 keyword Preset、组合双栏文字块并保存。审计复现（作品 f941b066）：保存时复查源 Preset
    曾不带角色（text Preset 缺宿主 prop）；默认值以对象展开传入时 "center" 被放宽成 string，联合类型的 align 也通不过类型检查。
    画 children 的新写法同样通过类型检查、排版与关键词外观检查，入场特效放在 chars（逐字）也一样，关键词 Preset 画 children、
    关键词逐字先入场、其余逐字推迟入场也一样；两栏重叠、上栏在下栏出现时
    被劈开（cue 特效把内容画两份）也不触发排版、逐行同步或跳动检查的误报。"""
    code = ALIGNED_TIER if variant == "segments" else CHILD_TIER.replace("color: string})", 'color: string; align: "left" | "center" | "right"})').replace('textAlign: "center"', "textAlign: props.align")
    monkeypatch.setattr(FontStore, "active", SimpleNamespace(families=lambda: ["Noto Sans CJK SC"]))
    settings = Settings(_env_file=None, data_dir=tmp_path, **BROWSER)
    current = ToolSession(SimpleNamespace(settings=settings, renderer=Renderer(settings)), None, None, Budget(), tmp_path / "run", [], lambda *_: None,
                          {"original_request": {"description": "双栏", "two_tier": True, "keyword_effect": True}})

    async def run():
        """Create both Presets, compose the block and save it with every real check."""
        tier = preset("text", code, "color")
        tier = tier.model_copy(update={"parameter_schema": {**tier.parameter_schema, "properties": {**tier.parameter_schema["properties"], "align": {"type": "string", "enum": ["left", "center", "right"]}},
                                                            "required": ["color", "align"]}, "default_parameters": {"color": "#FFFFFF", "align": "center"}})
        stored_tier = await current.execute("preset.create", tier, available())
        glow = await current.execute("preset.create", preset("keyword", KEYWORD_CODE if variant == "segments" else KEYWORD_CHILD_CODE, "glowColor", "#FFD54A"), available())
        fade = await current.execute("preset.create", effect_preset(FADE_CODE), available())
        request = two_tiers(stored_tier["preset"]["preset_id"], glow["preset"]["preset_id"], fade["preset"]["preset_id"]).model_dump(mode="json", exclude_none=True)
        request["text"]["tiers"][1]["font_family"] = "Noto Sans CJK SC"
        if variant != "segments":
            request["text"]["effects"][0]["target"] = "chars"
            request["text"]["effects"][0]["stagger"] = 1
        if variant == "children":
            # The keyword's glyphs type in first, the other glyphs follow.
            request["text"]["effects"][0]["target"] = "keywordChars"
            request["text"]["effects"].append({**request["text"]["effects"][0], "instance_id": "restIn", "target": "plainChars", "title": "显现", "delay": 0.3, "seconds": 0.2, "stagger": 0})
        if variant == "split":
            split = await current.execute("preset.create", effect_preset(SPLIT_CODE), available())
            del request["text"]["line_gap"]
            request["text"]["arrangement"] = "overlap"
            request["text"]["effects"].append({"instance_id": "split", "source": {"kind": "stored", "preset_id": split["preset"]["preset_id"]},
                                               "title": "劈开", "slot": "cue", "target": "line1", "seconds": 0.3})
        composed = await current.execute("sprite.compose", SpriteComposeInput.model_validate(request), available())
        return await current.execute("sprite.create", SpriteCreateInput(draft_id=composed["draft_id"]), available())

    saved = asyncio.run(run())
    assert saved["validation"]["passed"] is True
