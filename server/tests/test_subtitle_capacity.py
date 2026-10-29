"""模板一行字数与切片上限：按 IMS 字幕对象或接管字幕的 Remotion 字效的字号与位置离线估算，不启动浏览器。

执行 uv run --locked --project server pytest server/tests/test_subtitle_capacity.py -v；不访问模型、IMS 或生产数据库。
"""
from copy import deepcopy
import json

from google.protobuf.json_format import MessageToDict
import pytest

from server.video_composition import subtitle_capacity
from server.video_composition.errors import CompositionError

from .conftest import SPRITE_ID, published_sprite


def data(editor=None):
    """一个有中文 IMS 字幕的任务快照。"""
    return {"request": {"processRules": {}}, "output": {"width": 1080, "height": 1920, "fps": 30},
            "template": {"tracks": [{"target": "subtitle", "editor": editor or {"subtitleSize": 96}}]},
            "remotion": {"placements": [], "assets": {}}}


@pytest.mark.parametrize("editor,width,expected", [
    ({"subtitleSize": 96}, 1080, 9), ({"subtitleSize": 96}, 720, 5),
    ({"subtitleSize": 96, "subtitleX": 40}, 1080, 7),
    ({"subtitleSize": 96, "subtitleKeywordSize": 192}, 1080, 3),
    ({"subtitleSize": 300}, 720, 1),
])
def test_ims_full_width_formula(editor, width, expected):
    """真实样式与输出宽度决定全角容量，居中位置与较大关键词字号都参与，减二后至少一字。"""
    record = data(editor)
    record["output"]["width"] = width
    before = deepcopy(record)
    assert subtitle_capacity.calculate("test", record) == expected
    assert record == before


@pytest.mark.parametrize("requested", [4, 100])
def test_legacy_request_limit_does_not_change_capacity(requested):
    """旧任务请求中保留的手动上限不参与容量计算，始终使用模板得到的九字上限。"""
    record = data()
    record["request"]["processRules"]["subtitleMaxChars"] = requested
    assert subtitle_capacity.calculate("test", record) == 9


def test_unusable_position_is_not_reported_as_capacity():
    """文字中心贴着画布边缘，一字也放不下时明确失败，不伪造正数容量。"""
    with pytest.raises(CompositionError) as failure:
        subtitle_capacity.calculate("test", data({"subtitleX": 0}))
    assert failure.value.error["code"] == "subtitle_capacity_too_small"


def with_effect(placement: dict, defaults: dict | None = None) -> dict:
    """在 IMS 字幕任务里加一个 Remotion 字效摆放；资产为 64px、内容区域宽 900px 的字效，可改默认参数。"""
    sprite = published_sprite()
    if defaults is not None:
        sprite.default_config_json = json.dumps({**json.loads(sprite.default_config_json), **defaults})
    record = data()
    record["remotion"] = {"placements": [{"id": "fx", "sprite_id": SPRITE_ID, **placement}],
                          "assets": {SPRITE_ID: MessageToDict(sprite, preserving_proto_field_name=True)}}
    return record


def number(key: str, value: float) -> dict:
    """一条数字参数覆盖，键名与合成快照（下划线）一致。"""
    return {"key": key, "number_value": value}


@pytest.mark.parametrize("editor,expected", [
    ({}, 12),
    ({"parameters": [number("fontSize", 100)]}, 7),
    ({"parameters": [number("keywordSize", 128)]}, 5),
    ({"transform": {"scale": 2}}, 6),
    ({"transform": {"x": 270}}, 6),
])
def test_replacing_effect_uses_its_own_font_size(editor, expected):
    """字效接管字幕时不看被替代的 96px IMS 字幕：按字效字号、关键词字号、缩放与位置算，宽度以内容区域和画布为界。"""
    assert subtitle_capacity.calculate("test", with_effect({"text_target": "subtitle", "editor": editor})) == expected


def test_two_tier_effect_uses_the_larger_tier():
    """双栏字效按较大一栏的字号比例估算：64px×1.5 = 96px，900px 宽放 9 字。"""
    record = with_effect({"text_target": "subtitle"}, {"line1SizeRatio": 0.6, "line2SizeRatio": 1.5})
    assert subtitle_capacity.calculate("test", record) == 7


def test_legacy_effect_and_ims_subtitle_both_count():
    """旧 businessTarget 字效与 IMS 字幕同时显示，取两者中较小的容量（IMS 48px 能放 22 字，字效只能放 14 字）。"""
    record = with_effect({"editor": {"business_target": "subtitle"}})
    record["template"]["tracks"][0]["editor"] = {"subtitleSize": 48}
    assert subtitle_capacity.calculate("test", record) == 12


def test_effect_without_font_size_falls_back_to_ims():
    """没有数字字号参数的旧字效无法估算，仍按模板 IMS 字幕对象计算。"""
    assert subtitle_capacity.calculate("test", with_effect({"text_target": "subtitle"}, {"fontSize": None})) == 9


def test_english_subtitle_does_not_limit_chinese_segments():
    """英文字幕对象与英文字效都不参与中文切片上限，只取中文字幕对象。"""
    record = with_effect({"text_target": "subtitle", "language": "en", "editor": {"parameters": [number("fontSize", 240)]}})
    record["template"]["tracks"].append({"target": "subtitle", "language": "en", "editor": {"subtitleSize": 300}})
    assert subtitle_capacity.calculate("test", record) == 9
