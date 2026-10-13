"""一次 Remotion 摆放的参数覆盖与宿主变换：按发布参数契约合并、校验，并给出宿主层。"""

import json

import pytest
from generated.imv.sprite.v1 import sprite_pb2 as pb

from server.remotion_templates.categories import KEYWORD_COLOR
from server.remotion_templates.placement import resolve
from server.remotion_templates.sprites import summarize
from server.template.schema import RemotionTrackEditor

from .conftest import published_sprite

BOX = {"x": 90, "y": 760, "width": 900, "height": 400}


def editor(*parameters: dict, **transform) -> RemotionTrackEditor:
    """Build a placement editor from wire-shaped overrides and transform fields."""
    return RemotionTrackEditor.model_validate({"parameters": list(parameters), **({"transform": transform} if transform else {})})


def test_without_overrides_the_published_defaults_render_around_the_content_box():
    """没有 editor 时使用发布默认配置，宿主层只带内容区域。"""
    config, host = resolve(published_sprite(), None)
    assert config == {"text": "标题", "fontSize": 64, "fontWeight": 400, "color": "#FFFFFF", "keywords": [], "glow": 0.5, "particleCount": 20}
    assert host == {"box": BOX, "transform": {}}


def test_overrides_merge_into_the_defaults_and_the_transform_is_kept():
    """覆盖值写入默认配置（整数参数仍是整数），宿主变换原样交给宿主层。"""
    config, host = resolve(published_sprite(), editor(
        {"key": "text", "stringValue": "新品上市"}, {"key": "fontWeight", "numberValue": 700},
        {"key": "keywords", "stringListValue": {"values": ["新品"]}}, {"key": "color", "string_value": "#FF0000AA"},
        x=540, y=300, scale=1.5, rotation=-10, opacity=0.8,
    ))
    assert config["text"] == "新品上市" and config["fontWeight"] == 700 and isinstance(config["fontWeight"], int)
    assert config["keywords"] == ["新品"] and config["color"] == "#FF0000AA" and config["glow"] == 0.5
    assert host == {"box": BOX, "transform": {"x": 540, "y": 300, "scale": 1.5, "rotation": -10, "opacity": 0.8}}


def keyword_styled_sprite():
    """在标准测试资产上再声明关键词颜色（允许空串）与字号（0～300）两个可编辑参数。"""
    sprite = published_sprite()
    schema = json.loads(sprite.config_schema_json)
    schema["properties"]["keywordColor"] = {"type": "string", "pattern": KEYWORD_COLOR}
    schema["properties"]["keywordSize"] = {"type": "integer", "minimum": 0, "maximum": 300}
    sprite.config_schema_json = json.dumps(schema)
    sprite.default_config_json = json.dumps({**json.loads(sprite.default_config_json), "keywordColor": "", "keywordSize": 0})
    editable = pb.OPERATOR_ACCESS_VISIBLE_EDITABLE
    sprite.parameters.add(key="keywordColor", label="关键词颜色", access=editable, default_value=pb.ScalarValue(string_value=""))
    sprite.parameters.add(key="keywordSize", label="关键词字号", access=editable, default_value=pb.ScalarValue(number_value=0),
                          minimum=0, maximum=300, format=pb.SPRITE_PARAMETER_FORMAT_INTEGER)
    return sprite


def test_bus_keyword_options_resolve_like_the_ims_template_fields():
    """总线按 IMS 字段名传来的关键词颜色/字号覆盖被接受（整数字号保持整数），空串与 0 即「沿用」。"""
    config, _ = resolve(keyword_styled_sprite(), editor(
        {"key": "keywordColor", "stringValue": "#FFFF00"}, {"key": "keywordSize", "numberValue": 80},
    ))
    assert config["keywordColor"] == "#FFFF00" and config["keywordSize"] == 80 and isinstance(config["keywordSize"], int)
    config, _ = resolve(keyword_styled_sprite(), editor({"key": "keywordColor", "stringValue": ""}, {"key": "keywordSize", "numberValue": 0}))
    assert config["keywordColor"] == "" and config["keywordSize"] == 0
    with pytest.raises(ValueError, match="不符合发布契约"):
        resolve(keyword_styled_sprite(), editor({"key": "keywordColor", "stringValue": "yellow"}))
    with pytest.raises(ValueError, match="超出允许范围"):
        resolve(keyword_styled_sprite(), editor({"key": "keywordSize", "numberValue": 301}))


@pytest.mark.parametrize("size", [1, 11])
def test_keyword_sizes_the_ims_template_refuses_are_refused(size):
    """审计复现：Schema 是 0～300，1～11 曾被叠加接口接受；IMS 模板只允许 0 或 12～300，摆放覆盖同样拒绝。"""
    with pytest.raises(ValueError, match="只能是 0（沿用文字字号）或 12～300"):
        resolve(keyword_styled_sprite(), editor({"key": "keywordSize", "numberValue": size}))
    config, _ = resolve(keyword_styled_sprite(), editor({"key": "keywordSize", "numberValue": 12}))
    assert config["keywordSize"] == 12


@pytest.mark.parametrize("override, message", [
    ({"key": "missing", "stringValue": "x"}, "参数 missing 不存在"),
    ({"key": "glow", "numberValue": 0.2}, "参数 glow 不可修改"),
    ({"key": "text", "numberValue": 1}, "值类型与默认值不同"),
    ({"key": "fontSize", "numberValue": 300}, "超出允许范围"),
    ({"key": "fontWeight", "numberValue": 500}, "不是允许的取值"),
    ({"key": "text", "stringValue": ""}, "文字长度超出允许范围"),
    ({"key": "text", "stringValue": "长" * 41}, "文字长度超出允许范围"),
    ({"key": "keywords", "stringListValue": {"values": ["词"] * 21}}, "最多 20 项"),
    ({"key": "keywords", "stringListValue": {"values": ["长" * 101]}}, "文字长度超出允许范围"),
    ({"key": "color", "stringValue": "red"}, "不符合发布契约"),
    ({"key": "particleCount", "numberValue": 1.5}, "参数 particleCount 必须是整数"),
])
def test_overrides_outside_the_published_contract_are_refused(override, message):
    """覆盖必须指向可编辑参数、类型相同、在范围与枚举内，合并后仍满足发布时的 Schema。"""
    with pytest.raises(ValueError, match=message):
        resolve(published_sprite(), editor(override))


@pytest.mark.parametrize("kind, transform, message", [
    (pb.SPRITE_KIND_FILTER_OVERLAY, {"x": 10}, "不能设置 x"),
    (pb.SPRITE_KIND_TRANSITION_OVERLAY, {"scale": 2, "rotation": 5}, "不能设置 rotation、scale"),
    (pb.SPRITE_KIND_TEXT, {"x": 1081}, "x 超出允许范围 0～1080"),
    (pb.SPRITE_KIND_VIDEO_OVERLAY, {"scale": 0.1}, "scale 超出允许范围 0.25～4"),
    (pb.SPRITE_KIND_TEXT, {"opacity": 1.5}, "opacity 超出允许范围 0～1"),
])
def test_transform_fields_depend_on_the_kind_and_stay_in_range(kind, transform, message):
    """滤镜与转场只能调整不透明度；所有字段都受画布与固定范围限制。"""
    with pytest.raises(ValueError, match=message):
        resolve(published_sprite(kind=kind), editor(**transform))
    config, host = resolve(published_sprite(kind=pb.SPRITE_KIND_FILTER_OVERLAY), editor(opacity=0.3))
    assert host["transform"] == {"opacity": 0.3}


def test_publications_without_boundary_samples_keep_parameters_read_only():
    """参数边界检查之前的发布从未在默认值之外渲染过：参数在目录里只读、覆盖被拒绝，宿主变换（在代码之外）仍可设置。

    修复前它们的参数在模板里可编辑，且大多没有范围，-1e300 或 2000 字的覆盖要等总线渲染时才失败。
    """
    sprite = published_sprite()
    sprite.ClearField("content_box")
    with pytest.raises(ValueError, match="参数 fontSize 不可修改：该资产发布于参数边界检查之前"):
        resolve(sprite, editor({"key": "fontSize", "numberValue": 96}))
    config, host = resolve(sprite, editor(x=300, opacity=0.5))
    assert config["fontSize"] == 64 and host["transform"] == {"x": 300, "opacity": 0.5}
    assert host["box"] == {"x": 0, "y": 0, "width": 1080, "height": 1920}
    assert {item.access for item in summarize(sprite).parameters} == {pb.OPERATOR_ACCESS_VISIBLE_READ_ONLY}
    assert summarize(published_sprite()).parameters[0].access == pb.OPERATOR_ACCESS_VISIBLE_EDITABLE
