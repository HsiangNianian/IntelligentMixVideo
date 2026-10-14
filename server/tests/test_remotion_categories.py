"""效果分类与顶层参数绑定：分类必备参数规则、组合时提升参数、保存前的分类检查。"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.remotion_templates.categories import HEX_COLOR, KEYWORD_COLOR, category_problems, parameter_problems
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
    ({"type": "array", "items": {"type": "number"}}, "only a keywords string array is supported"),
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


def session(tmp_path: Path, spec=None, base=None) -> ToolSession:
    """A creation session whose isolated checks always pass, so only the category gate decides."""
    current = ToolSession(SimpleNamespace(settings=Settings(_env_file=None, data_dir=tmp_path), renderer=None), spec, base, Budget(), tmp_path / "run", [], lambda *_: None, {})

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


def test_edit_snapshot_repeats_the_base_kind_and_parameter_bindings(tmp_path):
    """编辑任务的快照带上基线的 kind 与绑定，模型重新组合时可以照原样提供。"""
    base_sprite = sprite("text", list(TEXT_PROPERTIES))
    spec = SimpleNamespace(sprite=base_sprite, composition=Composition(width=1080, height=1920, fps=30, duration_frames=30), sprite_kind="composition")
    base = TemplateCandidate(tsx_code="bundle", config_schema={"type": "object"}, default_config={})
    summary = session(tmp_path, spec, base).snapshot()["base"]
    assert summary["kind"] == "text"
    assert [item["name"] for item in summary["parameters"]] == list(TEXT_PROPERTIES)
