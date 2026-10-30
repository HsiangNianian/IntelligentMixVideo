"""效果分类与顶层参数绑定：分类必备参数规则、组合时提升参数、保存前的分类检查。"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.remotion_templates.categories import HEX_COLOR, KEYWORD_COLOR, agent_message, category_problems, line_count, parameter_problems
from server.remotion_templates.fonts import FontStore
from server.remotion_templates.models import TemplateCandidate
from server.remotion_templates.provider import Budget
from server.remotion_templates.settings import Settings
from server.remotion_templates.tools.catalog import available
from server.remotion_templates.tools.compose import compose_source
from server.remotion_templates.tools.contracts import Composition, SpriteCreateInput, SpriteDraft
from server.remotion_templates.tools.registry import ToolFault
from server.remotion_templates.tools.session import ToolSession

RENDERER_DEPS = Path(__file__).parents[1] / "src" / "server" / "remotion" / "node_modules" / "esbuild"
COLOR = {"type": "string", "pattern": HEX_COLOR}
KEYWORD_STYLES = {
    "keywords": {"type": "array", "items": {"type": "string", "maxLength": 100}, "maxItems": 20},
    "keywordBold": {"type": "boolean"},
    "keywordItalic": {"type": "boolean"},
    "keywordUnderline": {"type": "boolean"},
    "keywordStrikeout": {"type": "boolean"},
    "keywordColor": {"type": "string", "pattern": KEYWORD_COLOR},
    "keywordSize": {"type": "integer", "minimum": 0, "maximum": 300},
}
BASE_TEXT = {
    "text": {"type": "string", "minLength": 1, "maxLength": 100},
    "fontSize": {"type": "number", "minimum": 24, "maximum": 240},
    "fontWeight": {"type": "integer", "enum": [400, 700]},
    "color": COLOR,
}
TEXT_PROPERTIES = {**BASE_TEXT, **KEYWORD_STYLES}
# 关键词参数约定的关闭默认值。
OFF = {"keywords": [], "keywordBold": False, "keywordItalic": False, "keywordUnderline": False, "keywordStrikeout": False,
       "keywordColor": "", "keywordSize": 0}


def schema(properties: dict) -> dict:
    """Wrap top-level properties in an object schema like the composed Sprite's."""
    return {"type": "object", "properties": properties, "additionalProperties": False}


def test_each_category_accepts_exactly_its_required_shapes():
    """四个 kind 各自的必备参数齐全且形状正确时没有问题；缺一个或形状不符时逐项列出。"""
    assert category_problems("text", schema(TEXT_PROPERTIES), set(TEXT_PROPERTIES), OFF) == []
    filters = {"intensity": {"type": "number", "minimum": 0, "maximum": 1}, "tintColor": COLOR}
    assert category_problems("filter_overlay", schema(filters), set(filters), OFF) == []
    effects = {"color": COLOR, "speed": {"type": "number", "minimum": 0.25, "maximum": 4}}
    for kind in ("video_overlay", "transition_overlay"):
        assert category_problems(kind, schema(effects), set(effects), OFF) == []
    # A declared but unbound property is not an operator parameter.
    assert [item.name for item in category_problems("text", schema(TEXT_PROPERTIES), set(TEXT_PROPERTIES) - {"fontWeight"}, OFF)] == ["fontWeight"]
    wrong = {**TEXT_PROPERTIES, "color": {"type": "string"}, "fontWeight": {"type": "integer", "enum": [400, 500]}}
    problems = category_problems("text", schema(wrong), set(wrong), OFF)
    assert [(item.name, item.reason) for item in problems] == [("fontWeight", "has the wrong schema"), ("color", "has the wrong schema")]
    assert category_problems("filter_overlay", schema({**filters, "intensity": {"type": "number", "minimum": 0, "maximum": 2}}), set(filters), OFF)[0].name == "intensity"


def test_text_limits_and_keywords_are_checked():
    """文字 maxLength 必须为整数 100，拒绝缩短或放宽上限；关键词仍使用独立限制。"""
    for limit in (6, 40, 99, 101, 200, 500, True, 100.0, "100", None):
        invalid = {**TEXT_PROPERTIES, "text": {"type": "string", "minLength": 1, "maxLength": limit}}
        assert [item.name for item in category_problems("text", schema(invalid), set(invalid), OFF)] == ["text"]
    unbounded = {**TEXT_PROPERTIES, "keywords": {"type": "array", "items": {"type": "string"}}}
    assert [item.name for item in category_problems("text", schema(unbounded), set(unbounded), OFF)] == ["keywords"]


def test_text_requires_every_keyword_parameter():
    """字效必须声明全部关键词参数（不用时保持原样式），缺哪个就报哪个；其他分类不要求。"""
    assert [item.name for item in category_problems("text", schema(BASE_TEXT), set(BASE_TEXT), OFF)] == list(KEYWORD_STYLES)
    partial = {key: value for key, value in TEXT_PROPERTIES.items() if key != "keywordSize"}
    assert [item.name for item in category_problems("text", schema(partial), set(partial), OFF)] == ["keywordSize"]


def test_keyword_style_parameters_match_the_bus_options():
    """关键词样式参数与总线 IMS 模板同义：颜色允许空串，字号 0～300 整数，布尔为开关；形状不符时逐项列出。"""
    good = TEXT_PROPERTIES
    assert category_problems("text", schema(good), set(good), OFF) == []
    for name, bad in (
        ("keywordColor", COLOR),
        ("keywordSize", {"type": "integer", "minimum": 12, "maximum": 300}),
        ("keywordBold", {"type": "string", "maxLength": 3}),
    ):
        broken = {**good, name: bad}
        assert [item.name for item in category_problems("text", schema(broken), set(broken), OFF)] == [name]
    assert parameter_problems(KEYWORD_STYLES["keywordColor"], "keywordColor") is None
    assert parameter_problems({"type": "string", "pattern": "^a$"}, "label") is not None
    # 审计复现：允许空串的关键词颜色 pattern 曾能用于任何字符串参数，模板里却发布成没有校验的文本框。
    assert "only keywordColor" in parameter_problems(KEYWORD_STYLES["keywordColor"], "strokeColor")
    # 审计复现：每项只能 4 字的关键词列表曾通过检查，总线转发的 6 字标题关键词随后在保存模板时被拒。
    short = {**TEXT_PROPERTIES, "keywords": {"type": "array", "items": {"type": "string", "maxLength": 11}, "maxItems": 20}}
    assert [item.name for item in category_problems("text", schema(short), set(short), OFF)] == ["keywords"]


def test_keyword_parameters_must_default_to_off():
    """审计复现：关键词参数默认开启（命中「标题」、四个样式开启、红色、80 号）的版本曾通过检查；现在每个非关闭默认值都被点名。"""
    on = {"keywords": ["标题"], "keywordBold": True, "keywordItalic": True, "keywordUnderline": True, "keywordStrikeout": True,
          "keywordColor": "#FF0000", "keywordSize": 80}
    problems = category_problems("text", schema(TEXT_PROPERTIES), set(TEXT_PROPERTIES), on)
    assert [item.name for item in problems] == list(KEYWORD_STYLES)
    assert problems[0].reason == "must default to [] (off)" and problems[-1].reason == "must default to 0 (off)"
    # A missing default and a number standing in for a boolean are not off either; 0.0 for the size is.
    assert [item.name for item in category_problems("text", schema(TEXT_PROPERTIES), set(TEXT_PROPERTIES), {**OFF, "keywordBold": 0, "keywordSize": 0.0, "keywordColor": None})] == ["keywordBold", "keywordColor"]
    missing = {key: value for key, value in OFF.items() if key != "keywords"}
    assert [item.name for item in category_problems("text", schema(TEXT_PROPERTIES), set(TEXT_PROPERTIES), missing)] == ["keywords"]


def test_the_agent_is_told_how_to_repair_keyword_defaults():
    """默认值不合规时给 Agent 的说明要能指导修复旧字效：关键词外观留在键词 Preset 的样式参数里，七个选项恢复关闭默认值；
    其他分类问题不带这段修复说明。"""
    on = {**OFF, "keywordSize": 80}
    problems = category_problems("text", schema(TEXT_PROPERTIES), set(TEXT_PROPERTIES), on)
    message = agent_message("text", problems)
    assert "keyword Preset" in message and "keywordEffect..." in message and "keywordSize 0" in message
    missing = {name: TEXT_PROPERTIES[name] for name in BASE_TEXT}
    other = agent_message("text", category_problems("text", schema(missing), set(missing), OFF))
    assert "keyword Preset" not in other and "must default to" not in other


LINE_STARTS = {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 3600}, "minItems": 2, "maxItems": 2}
LINE_GAP = {"type": "number", "minimum": -600, "maximum": 300}
MULTI_LINE = {**TEXT_PROPERTIES, "lineStartFrames": LINE_STARTS, "lineGap": LINE_GAP}
MULTI_LINE_DEFAULTS = {**OFF, "text": "判断一个人\n选择花字、滤镜和特效", "lineStartFrames": [0, 30], "lineGap": 24}


def test_multi_line_text_declares_fixed_lines_and_their_starts():
    """多行字效：行起点恰好 N 项、默认文字恰好 N 个非空行、默认行起点从 0 开始且不递减、带行间距时通过，行数取项数；单行字效为 1 行。"""
    assert category_problems("text", schema(MULTI_LINE), set(MULTI_LINE), MULTI_LINE_DEFAULTS) == []
    assert line_count(schema(MULTI_LINE)) == 2 and line_count(schema(TEXT_PROPERTIES)) == 1
    # Declared but not bound: not a multi-line text, so its defaults are not checked.
    assert category_problems("text", schema(MULTI_LINE), set(TEXT_PROPERTIES), OFF) == []
    # The line gap is required, so users can move the lines closer together or apart.
    problems = category_problems("text", schema(MULTI_LINE), set(MULTI_LINE), MULTI_LINE_DEFAULTS)
    assert problems == []
    # 下界可为负数，模板把下栏向上移、叠住上栏；0 仍接受，更早生成的资产照常发布。
    for minimum in (-600, 0):
        spaced = {**MULTI_LINE, "lineGap": {"type": "number", "minimum": minimum, "maximum": 300}}
        assert category_problems("text", schema(spaced), set(spaced), MULTI_LINE_DEFAULTS) == []
    problems = category_problems("text", schema(MULTI_LINE), set(MULTI_LINE) - {"lineGap"}, MULTI_LINE_DEFAULTS)
    assert [(item.name, item.reason) for item in problems] == [("lineGap", "missing")]
    narrow = {**MULTI_LINE, "lineGap": {"type": "number", "minimum": 10, "maximum": 50}}
    assert [(item.name, item.reason) for item in category_problems("text", schema(narrow), set(narrow), MULTI_LINE_DEFAULTS)] == [("lineGap", "has the wrong schema")]
    for starts in ([5, 30], [0, 30, 60], [30, 0], [0, 1.5], [0, True], None):
        problems = category_problems("text", schema(MULTI_LINE), set(MULTI_LINE), {**MULTI_LINE_DEFAULTS, "lineStartFrames": starts})
        assert [item.name for item in problems] == ["lineStartFrames"], starts
    for text in ("一行文字", "一\n二\n三", "第一行\n "):
        problems = category_problems("text", schema(MULTI_LINE), set(MULTI_LINE), {**MULTI_LINE_DEFAULTS, "text": text})
        assert [item.name for item in problems] == ["text"], text


@pytest.mark.parametrize("definition", [
    {**LINE_STARTS, "minItems": 1},
    {**LINE_STARTS, "minItems": 5, "maxItems": 5},
    {**LINE_STARTS, "minItems": 1, "maxItems": 1},
    {**LINE_STARTS, "items": {"type": "integer", "minimum": 0, "maximum": 100}},
    {**LINE_STARTS, "items": {"type": "string", "maxLength": 12}},
])
def test_wrong_line_starts_are_refused_and_stay_single_line(definition):
    """行起点形状不合规（项数不定、超过 4 行、帧号范围或类型不对）时报告错误形状，资产按单行处理。"""
    properties = {**MULTI_LINE, "lineStartFrames": definition}
    problems = category_problems("text", schema(properties), set(properties), MULTI_LINE_DEFAULTS)
    assert [(item.name, item.reason) for item in problems] == [("lineStartFrames", "has the wrong schema")]
    assert line_count(schema(properties)) == 1
    assert parameter_problems(definition, "lineStartFrames") is not None


def test_a_text_defaulting_to_several_lines_must_be_a_multi_line_text():
    """默认文字带换行却没有绑定 lineStartFrames（例如把它留在实例内部）时被拒：否则资产按单行处理，两行只能在一个输入框里改，召回也不会逐行出现。"""
    problems = category_problems("text", schema(TEXT_PROPERTIES), set(TEXT_PROPERTIES), {**OFF, "text": "今日灵感\n记录每一个小确幸"})
    assert [item.name for item in problems] == ["lineStartFrames"]
    assert "the default text has 2 lines" in problems[0].reason and "minItems and maxItems 2" in problems[0].reason
    assert category_problems("text", schema(TEXT_PROPERTIES), set(TEXT_PROPERTIES), {**OFF, "text": "一行文字"}) == []


def test_line_starts_refusals_name_the_exact_shape():
    """项数不固定的行起点被拒时，说明这是双栏字效的行起点，并给出两栏的完整 Schema。"""
    reason = parameter_problems({**LINE_STARTS, "minItems": 2, "maxItems": 4}, "lineStartFrames")
    assert "two-tier text (双栏)" in reason and '"minItems": 2, "maxItems": 2' in reason


# 双栏字效：两栏各有固定名称的颜色、字号比例、字体与字重，不暴露整段共用的颜色、字重与字体。
TIER_LIBRARY = {"Noto Sans CJK SC", "Zed Display"}
TIER_FONT = {"type": "string", "enum": sorted(TIER_LIBRARY)}
TWO_TIER = {
    **{name: definition for name, definition in MULTI_LINE.items() if name not in ("color", "fontWeight")},
    **{f"line{tier}{suffix}": definition for tier in (1, 2) for suffix, definition in (
        ("Color", COLOR), ("SizeRatio", {"type": "number", "minimum": 0.4, "maximum": 2}), ("FontFamily", TIER_FONT),
        ("FontWeight", {"type": "integer", "enum": [400, 700]}),
    )},
}
TWO_TIER_DEFAULTS = {
    **MULTI_LINE_DEFAULTS, "line1Color": "#FFFFFF", "line1SizeRatio": 0.5, "line1FontFamily": "Noto Sans CJK SC", "line1FontWeight": 700,
    "line2Color": "#F5B93C", "line2SizeRatio": 1, "line2FontFamily": "Zed Display", "line2FontWeight": 700,
}
TIER_NAMES = [f"line{tier}{suffix}" for tier in (1, 2) for suffix in ("Color", "SizeRatio", "FontFamily", "FontWeight")]
# 双语字效（双语字幕）：双栏之上再声明英文文字 `textEn`，默认文字是中英各一行。
BILINGUAL = {**TWO_TIER, "textEn": {"type": "string", "minLength": 1, "maxLength": 100}}
BILINGUAL_DEFAULTS = {**TWO_TIER_DEFAULTS, "text": "今日灵感", "textEn": "Today's idea"}


def generated_problems(properties: dict, defaults: dict = TWO_TIER_DEFAULTS) -> list[tuple[str, str]]:
    """生成路径（传入字体库）对全部绑定的 ``properties`` 的分类检查结果。"""
    return [(item.name, item.reason) for item in category_problems("text", schema(properties), set(properties), defaults, TIER_LIBRARY)]


def test_generated_two_tier_text_uses_the_fixed_tier_parameters():
    """审计复现（作品 29b8f95f 等）：双栏的逐栏设置每次生成叫法都不同（subtitleColor、写死在代码里的 subtitleText，或者干脆没有）；
    生成路径现在要求恰好两栏，每栏有固定名称的颜色、字号比例、字体与字重，且不再暴露整段共用的颜色、字重与字体。"""
    assert generated_problems(TWO_TIER) == []
    assert generated_problems({name: value for name, value in TWO_TIER.items() if name != "line2FontFamily"}) == [("line2FontFamily", "missing")]
    assert generated_problems({**TWO_TIER, "line1SizeRatio": {"type": "number", "minimum": 0.1, "maximum": 3}}) == [("line1SizeRatio", "has the wrong schema")]
    [(name, reason)] = generated_problems({**TWO_TIER, "line1FontFamily": {"type": "string", "enum": ["Ghost"]}}, {**TWO_TIER_DEFAULTS, "line1FontFamily": "Ghost"})
    assert name == "line1FontFamily" and "not in the font library" in reason
    [(name, reason)] = generated_problems({**TWO_TIER, "color": COLOR}, {**TWO_TIER_DEFAULTS, "color": "#FFFFFF"})
    assert name == "color" and "line1Color and line2Color" in reason
    # More than two tiers are not generated.
    three = {**TWO_TIER, "lineStartFrames": {**LINE_STARTS, "minItems": 3, "maxItems": 3}}
    problems = generated_problems(three, {**TWO_TIER_DEFAULTS, "text": "一\n二\n三", "lineStartFrames": [0, 10, 20]})
    assert [name for name, _ in problems] == ["lineStartFrames"] and "exactly 2 lines" in problems[0][1]
    # The free-named per-line style of the earlier generations: every fixed tier parameter is missing and the shared ones must go.
    loose = {**MULTI_LINE, "subtitleColor": COLOR}
    assert [name for name, _ in generated_problems(loose, {**MULTI_LINE_DEFAULTS, "subtitleColor": "#FF00FF"})] == [*TIER_NAMES, "color", "fontWeight"]


def test_a_bilingual_text_is_two_tiers_with_the_english_line():
    """双语字效（双语字幕）：声明英文文字 `textEn` 的字效必须是双栏，默认文字是中文一行加英文一行；用户打开「双语字幕」时
    生成路径要求它，没打开时不要求，普通双栏（两栏都是中文）不声明 textEn。"""
    def check(properties: dict, values: dict, requested: set[str] = set()) -> list[tuple[str, str]]:
        """生成路径对给定参数与默认值的分类检查结果。"""
        return [(item.name, item.reason) for item in category_problems("text", schema(properties), set(properties), values, TIER_LIBRARY, requested)]

    assert check(BILINGUAL, BILINGUAL_DEFAULTS) == []
    # 打开「双语字幕」：普通双栏字效被拒，双语字效通过。
    [(name, reason)] = check(TWO_TIER, TWO_TIER_DEFAULTS, {"bilingual"})
    assert name == "textEn" and "switched on 双语字幕" in reason
    assert check(BILINGUAL, BILINGUAL_DEFAULTS, {"bilingual"}) == []
    # 默认文字仍是两行中文（没有英文行）：说明双语默认值应为中英各一行。
    assert [name for name, _ in check(BILINGUAL, TWO_TIER_DEFAULTS)] == ["text", "textEn"]
    # 只声明英文文字、没有行起点就不是双栏：说明双语字效是下栏显示英文的两栏字效。
    single = {name: value for name, value in BILINGUAL.items() if name != "lineStartFrames"}
    reasons = {item.name: item.reason for item in category_problems("text", schema(single), set(single), BILINGUAL_DEFAULTS, TIER_LIBRARY)}
    assert "a bilingual text" in reasons["lineStartFrames"]


def test_new_chat_switches_are_enforced_when_generating():
    """新会话打开了「双栏」：生成路径拒绝单行字效；打开了「关键词字效」：拒绝没有关键词字效的字效。没打开时都不要求。"""
    single = {**TEXT_PROPERTIES, "fontFamily": TIER_FONT}
    defaults = {**OFF, "fontFamily": "Zed Display"}
    check = lambda requested: [(item.name, item.reason) for item in category_problems("text", schema(single), set(single), defaults, TIER_LIBRARY, requested)]
    assert check(()) == []
    [(name, reason)] = check({"two_tier"})
    assert name == "lineStartFrames" and "switched on 双栏" in reason
    [(name, reason)] = check({"keyword_effect"})
    assert name == "keywordEffect" and "switched on the keyword effect" in reason
    assert category_problems("text", schema(TWO_TIER), set(TWO_TIER), TWO_TIER_DEFAULTS, TIER_LIBRARY, {"two_tier"}) == []


def test_a_keyword_effect_exposes_a_switch_that_defaults_on():
    """画了关键词字效（有 keywordEffect… 外观参数）的字效必须暴露 keywordEffect 开关且默认开启，用户才能不重新生成就关掉它；
    没有关键词字效的字效不要求。"""
    looks = {**TEXT_PROPERTIES, "fontFamily": TIER_FONT, "keywordEffectColor": COLOR}
    defaults = {**OFF, "fontFamily": "Zed Display", "keywordEffectColor": "#FFD54A"}
    check = lambda properties, values: [(item.name, item.reason) for item in category_problems("text", schema(properties), set(properties), values, TIER_LIBRARY)]
    assert check(looks, defaults) == [("keywordEffect", "missing")]
    switched = {**looks, "keywordEffect": {"type": "boolean"}}
    assert check(switched, {**defaults, "keywordEffect": True}) == []
    assert "must default to true" in check(switched, {**defaults, "keywordEffect": False})[0][1]
    assert check({**looks, "keywordEffect": {"type": "string", "maxLength": 4}}, {**defaults, "keywordEffect": "on"}) == [("keywordEffect", "has the wrong schema")]


def test_publishing_a_multi_line_text_needs_no_shared_colour_or_weight():
    """发布路径（不传字体库）：新生成的双栏字效没有整段共用的颜色与字重也能保存到资产，更早生成、没有逐栏参数的多行版本同样可以。"""
    assert category_problems("text", schema(TWO_TIER), set(TWO_TIER), TWO_TIER_DEFAULTS) == []
    assert category_problems("text", schema(MULTI_LINE), set(MULTI_LINE), MULTI_LINE_DEFAULTS) == []


def test_only_line_starts_may_be_an_integer_array():
    """整数数组只允许名为 lineStartFrames 的行起点；其他名称仍只接受关键词形状。"""
    assert parameter_problems(LINE_STARTS, "lineStartFrames") is None
    assert parameter_problems(LINE_STARTS, "value") == "uses schema keywords the publish-time samples cannot cover: items.maximum, items.minimum, minItems"


def test_a_missing_kind_is_reported_without_rules():
    """旧草稿没有 kind：只报告 kind 本身，不再猜测必备参数。"""
    problems = category_problems(None, schema({}), set(), OFF)
    assert [(item.name, item.rule) for item in problems] == [("kind", None)]


@pytest.mark.parametrize("definition, reason", [
    ({"type": "number", "minimum": 0, "maximum": 1}, None),
    ({"type": "number", "minimum": 0}, "a number needs minimum and maximum"),
    ({"type": "integer", "enum": [1, 2]}, None),
    (COLOR, None),
    ({"type": "string", "enum": ["left", "right"]}, None),
    ({"type": "string", "maxLength": 20}, None),
    ({"type": "string", "maxLength": 2000}, None),
    # Longer than a template override may be (2000): the control would accept text the template save refuses, the
    # long-text boundary sample would grow with it, and 2**32 does not even fit the published uint32 field.
    ({"type": "string", "maxLength": 2001}, "a string maxLength must be between 1 and 2000"),
    ({"type": "string", "maxLength": 2**32}, "a string maxLength must be between 1 and 2000"),
    ({"type": "string", "enum": ["a"], "maxLength": 0}, "a string maxLength must be between 1 and 2000"),
    ({"type": "string"}, f"a string needs the colour pattern {HEX_COLOR}, an enum or a maxLength"),
    ({"type": "boolean"}, None),
    ({"type": "array", "items": {"type": "number"}}, "only a keywords string array (and lineStartFrames) is supported: per-tier styles are the fixed scalar "
     "parameters line1Color, line1SizeRatio, line1FontFamily and line1FontWeight (and the same for line2), not arrays"),
    ({"type": "object"}, "type must be number, integer, string, boolean or a keywords array"),
    # Constraints the publish-time extremes would violate, so those ends would silently go unsampled.
    ({"type": "number", "minimum": 0, "maximum": 1, "exclusiveMaximum": 1}, "uses schema keywords the publish-time samples cannot cover: exclusiveMaximum"),
    ({"type": "number", "minimum": 0, "maximum": 10, "multipleOf": 3}, "uses schema keywords the publish-time samples cannot cover: multipleOf"),
    ({"type": "integer", "minimum": 0.5, "maximum": 10.5}, "an integer needs integer minimum and maximum"),
    ({"type": "string", "maxLength": 9, "pattern": "^[A-Z]+$"}, f"a string pattern must be the colour pattern {HEX_COLOR} (only keywordColor may use {KEYWORD_COLOR})"),
    ({"type": "string", "minLength": 9, "maxLength": 3}, "a string minLength must not exceed its maxLength"),
    ({"type": "array", "items": {"type": "string", "maxLength": 9, "minLength": 2}, "maxItems": 3}, "uses schema keywords the publish-time samples cannot cover: items.minLength"),
    ({"type": "boolean", "title": "阴影", "default": False}, None),
    ({"$ref": "#/$defs/x"}, "must not use $ref"),
])
def test_top_level_parameters_need_bounded_values(definition, reason):
    """模板里的控件需要明确的取值范围：数字有上下界，字符串是颜色、枚举或限长。"""
    assert parameter_problems(definition, "value") == reason


def instance(instance_id: str, parameters: dict, properties: dict) -> dict:
    """A resolved compose instance over a Preset that declares ``properties``."""
    return {
        "instance_id": instance_id,
        "preset": {
            "description": instance_id,
            "code": "export default function P(props: any){return null}",
            "parameter_schema": {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False},
            "default_parameters": parameters,
        },
        "parameters": parameters,
        "layout": {"x": 0, "y": 0, "width": 1080, "height": 960, "z_index": 0},
        "timing": {"start_frame": 0, "duration_frames": 30},
    }


def binding(name: str, *targets: tuple[str, str]) -> dict:
    """One top-level binding over (instance_id, prop) targets."""
    return {"name": name, "title": name, "targets": [{"instance_id": item, "parameter": prop} for item, prop in targets]}


@pytest.mark.skipif(not RENDERER_DEPS.exists(), reason="install locked Remotion renderer dependencies")
def test_composed_instance_box_does_not_clip_its_content():
    """组合出的实例盒子只是摆放区域：不带 overflow，更大的字号、关键词字号或下划线不会在盒子边缘被裁掉，只受画布限制。

    修复前盒子带 overflow hidden，字号放到最大时关键词顶部与下划线被裁（issue #142）。
    """
    code, _, _ = compose_source([instance("title", {"label": "标题"}, {"label": {"type": "string"}})])
    assert 'data-imv-instance": "title"' in code and "overflow" not in code
    assert "width: 1080, height: 960, zIndex: 0" in code


@pytest.mark.skipif(not RENDERER_DEPS.exists(), reason="install locked Remotion renderer dependencies")
def test_bound_parameters_become_required_top_level_props_passed_to_every_target():
    """绑定的参数提升为顶层必填属性并从实例里移除；生成的宿主把顶层值传给每个目标 prop。"""
    instances = [
        instance("top", {"label": "上", "tint": "#FFFFFF", "glow": 0.2}, {"label": {"type": "string", "maxLength": 9}, "tint": COLOR, "glow": {"type": "number"}}),
        instance("bottom", {"tint": "#FFFFFF"}, {"tint": {**COLOR, "title": "底色"}}),
    ]
    code, composed, defaults = compose_source(instances, [binding("color", ("top", "tint"), ("bottom", "tint")), binding("text", ("top", "label"))])
    assert composed["required"] == ["color", "text"]
    assert composed["properties"]["color"] == {**COLOR, "title": "color"}
    assert composed["properties"]["top"]["properties"] == {"glow": {"type": "number"}}
    assert composed["properties"]["top"]["required"] == ["glow"]
    assert composed["properties"]["bottom"]["properties"] == {}
    assert defaults == {"color": "#FFFFFF", "text": "上", "top": {"glow": 0.2}, "bottom": {}}
    assert '"tint": props["color"]' in code and '"label": props["text"]' in code


@pytest.mark.parametrize("bindings, field, message", [
    ([binding("top", ("top", "tint"))], "/parameters/0/name", "repeats a parameter or an instance_id"),
    ([binding("color", ("top", "tint")), binding("color", ("bottom", "tint"))], "/parameters/1/name", "repeats a parameter"),
    ([binding("color", ("middle", "tint"))], "/parameters/0/targets/0/instance_id", "Unknown instance_id middle"),
    ([binding("color", ("top", "shade"))], "/parameters/0/targets/0/parameter", "has no parameter shade"),
    ([binding("color", ("top", "tint")), binding("tone", ("top", "tint"))], "/parameters/1/targets/0", "already bound"),
    ([binding("color", ("top", "tint"), ("bottom", "tint"))], "/parameters/0/targets/1", "share one schema and one value"),
    ([binding("size", ("top", "size"))], "/parameters/0/targets/0", "uses $ref"),
    ([binding("any", ("top", "any"))], "/parameters/0/targets/0", "has a boolean schema"),
])
def test_invalid_bindings_name_the_offending_field(bindings, field, message):
    """绑定冲突、未知目标、重复绑定、$ref、布尔子 Schema 与取值不一致都在打包前按字段拒绝，而不是变成未结构化的异常。"""
    instances = [
        # JSON Schema allows a boolean sub-schema (`true` accepts anything); the component contract accepts it too.
        instance("top", {"tint": "#FFFFFF", "size": 1, "any": 1}, {"tint": COLOR, "size": {"$ref": "#/$defs/size"}, "any": True}),
        instance("bottom", {"tint": "#000000"}, {"tint": COLOR}),
    ]
    with pytest.raises(ToolFault) as refused:
        compose_source(instances, bindings)
    assert refused.value.error.code == "INVALID_ARGUMENT"
    assert refused.value.error.field == field and message in refused.value.error.message


def session(tmp_path: Path, spec=None, base=None, intent=None) -> ToolSession:
    """A creation session whose isolated checks always pass, so only the category gate decides; ``intent`` is the user intent."""
    current = ToolSession(SimpleNamespace(settings=Settings(_env_file=None, data_dir=tmp_path), renderer=None), spec, base, Budget(), tmp_path / "run", [], lambda *_: None, intent or {})

    async def passed(*_args, **_options):
        """Code and render checks are not under test here."""
        raise AssertionError("the category gate runs before the browser mount")

    async def valid(_component):
        """Code validation passes for every source."""
        from server.remotion_templates.tools.contracts import CodeValidationReport
        return CodeValidationReport(passed=True, diagnostics=[])

    current.validate_code = valid
    current.validate_render = passed
    return current


def sprite(kind: str | None, bound: list[str]) -> SpriteDraft:
    """A text Sprite draft whose instance declares every text parameter, of which ``bound`` are top-level."""
    data = {
        "description": "标题",
        "code": "export default function Sprite(){return null}",
        "parameter_schema": schema({name: TEXT_PROPERTIES[name] for name in bound}),
        "default_parameters": {name: OFF[name] for name in bound if name in OFF},
        "composition": {"width": 1080, "height": 1920, "fps": 30, "duration_frames": 30},
        "instances": [instance("title", {}, TEXT_PROPERTIES)],
        "bindings": [binding(name, ("title", name)) for name in bound],
    }
    return SpriteDraft(**data, **({"kind": kind} if kind else {}))


def test_create_refuses_a_sprite_missing_its_category_parameters(tmp_path):
    """sprite.create 在挂载浏览器之前检查分类：缺少的参数和应有 Schema 写进回执，不保存。"""
    async def run():
        """Create three drafts: one without fontWeight, one without a kind, one whose keywordBold defaults to on."""
        current = session(tmp_path)
        current.sprite_drafts["d"] = sprite("text", [name for name in TEXT_PROPERTIES if name != "fontWeight"])
        with pytest.raises(ToolFault) as refused:
            await current.execute("sprite.create", SpriteCreateInput(draft_id="d"), available())
        assert refused.value.error.code == "CATEGORY_PARAMETERS_MISSING"
        assert refused.value.error.details["missing"] == [{"name": "fontWeight", "reason": "missing", "schema": {"type": "integer", "enum": [400, 700]}}]
        assert "sprite_compose" in refused.value.error.message
        current.sprite_drafts["old"] = sprite(None, [])
        with pytest.raises(ToolFault) as untyped:
            await current.execute("sprite.create", SpriteCreateInput(draft_id="old"), available())
        assert untyped.value.error.details["missing"] == [{"name": "kind", "reason": "kind must be one of text, filter_overlay, video_overlay, transition_overlay"}]
        current.sprite_drafts["on"] = sprite("text", list(TEXT_PROPERTIES)).model_copy(update={"default_parameters": {**OFF, "keywordBold": True}})
        with pytest.raises(ToolFault) as enabled:
            await current.execute("sprite.create", SpriteCreateInput(draft_id="on"), available())
        assert enabled.value.error.code == "CATEGORY_PARAMETERS_MISSING"
        assert enabled.value.error.details["missing"] == [{"name": "keywordBold", "reason": "must default to false (off)", "schema": {"type": "boolean"}}]
        # 复现模型把 maxLength 降到 6 绕过长文字排版；保存前拒绝并回传正确契约。
        for limit in (6, 101):
            current.sprite_drafts["limit"] = sprite("text", list(TEXT_PROPERTIES)).model_copy(deep=True)
            current.sprite_drafts["limit"].parameter_schema["properties"]["text"]["maxLength"] = limit
            with pytest.raises(ToolFault) as invalid:
                await current.execute("sprite.create", SpriteCreateInput(draft_id="limit"), available())
            assert invalid.value.error.code == "CATEGORY_PARAMETERS_MISSING"
            assert invalid.value.error.details["missing"] == [{"name": "text", "reason": "has the wrong schema", "schema": TEXT_PROPERTIES["text"]}]
        assert current.saved_sprites == {}

    asyncio.run(run())


def test_create_stores_the_font_family_as_an_open_string(tmp_path, monkeypatch):
    """Agent 从字体库枚举里选字体：枚举里出现库外的字体名被拒绝；通过后保存的 Sprite 把 fontFamily 存成普通字符串（去掉冻结的枚举），
    宿主之后的校验与模板里的覆盖都看到这个形式，所以之后上传的字体也能选。"""
    library = ["Noto Sans CJK SC", "Zed Display"]
    monkeypatch.setattr(FontStore, "active", SimpleNamespace(families=lambda: list(library)))

    def draft(enum: list[str]) -> SpriteDraft:
        """A text Sprite whose fontFamily enum is `enum`, defaulting to the library's second font."""
        properties = {**TEXT_PROPERTIES, "fontFamily": {"type": "string", "enum": enum}}
        return SpriteDraft(
            description="标题", code="export default function Sprite(){return null}", kind="text",
            parameter_schema=schema(properties), default_parameters={**OFF, "fontFamily": "Zed Display"},
            composition={"width": 1080, "height": 1920, "fps": 30, "duration_frames": 30},
            instances=[instance("title", {}, properties)], bindings=[binding(name, ("title", name)) for name in properties],
        )

    async def run():
        """Create two drafts: one naming a font outside the library, one valid whose mounted schema is captured."""
        current = session(tmp_path)
        current.sprite_drafts["ghost"] = draft(["Zed Display", "Ghost Font"])
        with pytest.raises(ToolFault) as refused:
            await current.execute("sprite.create", SpriteCreateInput(draft_id="ghost"), available())
        assert refused.value.error.code == "CATEGORY_PARAMETERS_MISSING" and "Ghost Font" in refused.value.error.details["missing"][0]["reason"]
        mounted = {}

        async def capture(request, **_options):
            """Record the schema the browser check receives, then stop before anything is saved."""
            mounted["schema"] = request.component.parameter_schema
            raise RuntimeError("stop after the mount")

        current.validate_render = capture
        current.sprite_drafts["ok"] = draft(library)
        with pytest.raises(ToolFault, match="stop after the mount"):
            await current.execute("sprite.create", SpriteCreateInput(draft_id="ok"), available())
        assert mounted["schema"]["properties"]["fontFamily"] == {"type": "string", "maxLength": 100}

    asyncio.run(run())


def test_a_new_work_with_the_two_tier_switch_refuses_a_single_line_text(tmp_path, monkeypatch):
    """新会话打开「双栏」后，sprite.create 在挂载浏览器之前就拒绝单行字效并说明用户要的是双栏；编辑已有作品（有基线）时不强制。"""
    monkeypatch.setattr(FontStore, "active", SimpleNamespace(families=lambda: list(TIER_LIBRARY)))
    properties = {**TEXT_PROPERTIES, "fontFamily": TIER_FONT}
    single = SpriteDraft(
        description="标题", code="export default function Sprite(){return null}", kind="text",
        parameter_schema=schema(properties), default_parameters={**OFF, "fontFamily": "Zed Display"},
        composition={"width": 1080, "height": 1920, "fps": 30, "duration_frames": 30},
        instances=[instance("title", {}, properties)], bindings=[binding(name, ("title", name)) for name in properties],
    )
    intent = {"original_request": {"description": "双栏标题", "two_tier": True}}

    async def run():
        """Create the single-line draft in a new work whose user switched on 双栏."""
        current = session(tmp_path, intent=intent)
        current.sprite_drafts["single"] = single
        with pytest.raises(ToolFault) as refused:
            await current.execute("sprite.create", SpriteCreateInput(draft_id="single"), available())
        assert refused.value.error.code == "CATEGORY_PARAMETERS_MISSING"
        assert [item["name"] for item in refused.value.error.details["missing"]] == ["lineStartFrames"]
        assert "switched on 双栏" in refused.value.error.details["missing"][0]["reason"]

    asyncio.run(run())
    assert session(tmp_path, spec=SimpleNamespace(sprite_kind="text"), intent=intent).requested == set()


def test_edit_snapshot_repeats_the_base_kind_and_parameter_bindings(tmp_path):
    """编辑任务的快照带上基线的 kind 与绑定，模型重新组合时可以照原样提供。"""
    base_sprite = sprite("text", list(TEXT_PROPERTIES))
    spec = SimpleNamespace(sprite=base_sprite, composition=Composition(width=1080, height=1920, fps=30, duration_frames=30), sprite_kind="composition")
    base = TemplateCandidate(tsx_code="bundle", config_schema={"type": "object"}, default_config={})
    summary = session(tmp_path, spec, base).snapshot()["base"]
    assert summary["kind"] == "text"
    assert [item["name"] for item in summary["parameters"]] == list(TEXT_PROPERTIES)
