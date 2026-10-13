"""一次 Remotion 摆放的参数覆盖与宿主变换：按发布参数契约合并、校验，并给出宿主层。"""

import pytest
from generated.imv.sprite.v1 import sprite_pb2 as pb

from server.remotion_templates.placement import resolve
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


def test_historical_dot_path_parameters_still_resolve():
    """更早的发布用点号路径声明嵌套参数，覆盖写入对应的嵌套位置。"""
    sprite = published_sprite()
    sprite.ClearField("parameters")
    sprite.parameters.add(key="title.size", label="字号", access=pb.OPERATOR_ACCESS_VISIBLE_EDITABLE, default_value=pb.ScalarValue(number_value=40))
    sprite.config_schema_json = '{"type":"object","properties":{"title":{"type":"object","properties":{"size":{"type":"integer"}}}}}'
    sprite.default_config_json = '{"title":{"size":40}}'
    config, _ = resolve(sprite, editor({"key": "title.size", "numberValue": 52}))
    assert config == {"title": {"size": 52}}
