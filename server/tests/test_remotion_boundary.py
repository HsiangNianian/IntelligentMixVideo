"""发布时边界样本的生成：默认值、两端取值、逐个参数的回退、中间枚举值和各分类的宿主变换。"""

from server.remotion_templates.boundary import FILLER, samples, unsampled
from server.remotion_templates.categories import HEX_COLOR, KEYWORD_COLOR

SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "minLength": 2, "maxLength": 12},
        "size": {"type": "number", "minimum": 10, "maximum": 90},
        "align": {"type": "string", "enum": ["left", "center", "right", "justify"]},
        "color": {"type": "string", "pattern": HEX_COLOR},
        "glow": {"type": "boolean"},
        "inner": {"type": "object"},
    },
    "additionalProperties": False,
}
DEFAULTS = {"text": "标题", "size": 40, "align": "center", "color": "#FF0000", "glow": False, "inner": {"a": 1}}
NAMES = ["text", "size", "align", "color", "glow"]


def by_name(items):
    """Index samples by name for readable assertions."""
    return {item.name: item for item in items}


def test_defaults_and_both_extremes_cover_every_top_level_parameter():
    """第一个样本是默认值；low/high 把每个顶层参数推到一端，未绑定的嵌套参数保持默认值。"""
    found = by_name(samples(SCHEMA, DEFAULTS, NAMES, "text", (1080, 1920)))
    assert list(found)[:3] == ["defaults", "low", "high"]
    assert found["defaults"].values == DEFAULTS and found["defaults"].transform == {}
    assert found["low"].values == {**DEFAULTS, "text": FILLER * 12, "size": 10, "align": "left", "color": "#000000", "glow": False}
    assert found["high"].values == {**DEFAULTS, "text": FILLER * 2, "size": 90, "align": "justify", "color": "#FFFFFF", "glow": True}
    assert found["high"].transform == {"x": 1080, "y": 1920, "scale": 4.0, "rotation": 180.0, "opacity": 1.0}
    # Middle values of the four-value enum get their own samples; "center" equals the defaults and is not repeated.
    assert "enum:1" not in found and found["enum:2"].values == {**DEFAULTS, "align": "right"}


def test_full_frame_kinds_only_sample_opacity():
    """滤镜与转场的宿主变换只有不透明度。"""
    found = by_name(samples(SCHEMA, DEFAULTS, NAMES, "filter_overlay", (1080, 1920)))
    assert found["low"].transform == {"opacity": 0.0} and found["high"].transform == {"opacity": 1.0}


def test_a_combined_extreme_that_breaks_the_schema_falls_back_to_single_parameters():
    """参数之间互相约束、组合样本不合 Schema 时，改为逐步组合仍合法的一端取值，并逐个参数推到一端；每个样本都带同一端的宿主变换。

    修复前逐个参数的样本不带宿主变换，宿主变换只与默认参数一起渲染：例如只在 glow=true 且 scale=4 时出错的组件可以通过发布。
    """
    schema = {**SCHEMA, "if": {"properties": {"glow": {"const": True}}}, "then": {"properties": {"size": {"maximum": 50}}}}
    found = by_name(samples(schema, DEFAULTS, NAMES, "text", (1080, 1920)))
    assert any(item.values.get("glow") is True and item.transform.get("scale") == 4.0 for item in found.values())
    assert "high" not in found and "high:transform" not in found
    high = {"x": 1080, "y": 1920, "scale": 4.0, "rotation": 180.0, "opacity": 1.0}
    assert found["high:size"].values == {**DEFAULTS, "size": 90} and found["high:size"].transform == high
    assert found["high:glow"].values == {**DEFAULTS, "glow": True} and found["high:glow"].transform == high
    # The greedy combination keeps every extreme that still fits: size 90 first, so glow stays false.
    assert found["high:combined"].values == {**DEFAULTS, "text": FILLER * 2, "size": 90, "align": "justify", "color": "#FFFFFF"}
    assert found["high:combined"].transform == high
    assert "low" in found


def test_values_no_sample_can_reach_are_reported():
    """根级约束把参数绑得太紧、单个参数的一端在其他参数取默认值时都不合法时，该参数被列为无法检查，发布仅记日志并照常保存。

    修复前这样的一端被静默跳过，模板却可以填入它，直到叠加渲染才失败。
    """
    defaults = {**DEFAULTS, "glow": True}
    schema = {**SCHEMA, "if": {"properties": {"glow": {"const": True}}}, "then": {"properties": {"size": {"maximum": 50}}}}
    drawn = samples(schema, defaults, NAMES, "text", (1080, 1920))
    assert unsampled(schema, defaults, NAMES, drawn) == ["size"]
    assert unsampled(SCHEMA, DEFAULTS, NAMES, samples(SCHEMA, DEFAULTS, NAMES, "text", (1080, 1920))) == []


def test_identical_samples_are_rendered_once():
    """没有可推的参数时，两端样本与默认值相同的部分去重。"""
    found = samples({"type": "object", "properties": {}}, {}, [], "filter_overlay", (1080, 1920))
    assert [item.name for item in found] == ["defaults", "low", "high"]


def test_keyword_style_parameters_are_sampled_at_their_inherit_and_maximum_values():
    """关键词颜色两端为空串（沿用）与白色，字号为 0（沿用）与 300；另有 keywords:styled 样本让关键词全部命中且样式全开。"""
    schema = {"type": "object", "properties": {
        "text": {"type": "string", "minLength": 1, "maxLength": 12},
        "keywords": {"type": "array", "items": {"type": "string", "maxLength": 4}, "maxItems": 3},
        "keywordBold": {"type": "boolean"},
        "keywordColor": {"type": "string", "pattern": KEYWORD_COLOR},
        "keywordSize": {"type": "integer", "minimum": 0, "maximum": 300},
    }, "additionalProperties": False}
    defaults = {"text": "标题", "keywords": [], "keywordBold": False, "keywordColor": "", "keywordSize": 0}
    found = by_name(samples(schema, defaults, list(defaults), "text", (1080, 1920)))
    assert list(found) == ["defaults", "low", "high", "keywords:styled"]
    assert found["low"].values == {"text": FILLER * 12, "keywords": [FILLER] * 3, "keywordBold": False, "keywordColor": "", "keywordSize": 0}
    assert found["high"].values == {"text": FILLER, "keywords": [], "keywordBold": True, "keywordColor": "#FFFFFF", "keywordSize": 300}
    # Neither end renders a styled hit, so this sample does: every slot hits the longest text with every style on.
    assert found["keywords:styled"].values == {"text": FILLER * 12, "keywords": [FILLER] * 3, "keywordBold": True, "keywordColor": "#FFFFFF", "keywordSize": 300}
    assert found["keywords:styled"].transform == found["low"].transform
    assert unsampled(schema, defaults, list(defaults), list(found.values())) == []


def test_only_sprites_with_keywords_and_styles_get_the_styled_sample():
    """没有关键词列表或没有样式参数时不额外渲染。"""
    for names in (["text", "keywordBold"], ["text", "keywords"]):
        schema = {"type": "object", "properties": {
            "text": {"type": "string", "minLength": 1, "maxLength": 12},
            "keywords": {"type": "array", "items": {"type": "string", "maxLength": 4}, "maxItems": 3},
            "keywordBold": {"type": "boolean"},
        }, "additionalProperties": False}
        defaults = {"text": "标题", "keywords": [], "keywordBold": False}
        assert "keywords:styled" not in by_name(samples(schema, defaults, names, "text", (1080, 1920)))


def test_line_starts_keep_the_assets_own_schedule():
    """多行字效的行起点是宿主注入的内容：每个样本都保持默认的出现节奏，也不报告为漏抽。"""
    schema = {**SCHEMA, "properties": {**SCHEMA["properties"], "lineStartFrames": {
        "type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 3600}, "minItems": 2, "maxItems": 2,
    }}}
    defaults = {**DEFAULTS, "text": "一\n二", "lineStartFrames": [0, 30]}
    names = [*NAMES, "lineStartFrames"]
    drawn = samples(schema, defaults, names, "text", (1080, 1920))
    assert {tuple(item.values["lineStartFrames"]) for item in drawn} == {(0, 30)}
    assert by_name(drawn)["low"].values["text"] == FILLER * 12
    assert unsampled(schema, defaults, names, drawn) == []
