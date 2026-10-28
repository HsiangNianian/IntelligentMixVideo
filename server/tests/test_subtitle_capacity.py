"""模板一行字数与切片上限：按模板中文字幕对象的字号与位置离线估算，不启动浏览器。

执行 uv run --locked --project server pytest server/tests/test_subtitle_capacity.py -v；不访问模型、IMS 或生产数据库。
"""
from copy import deepcopy

import pytest

from server.video_composition import subtitle_capacity
from server.video_composition.errors import CompositionError


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


@pytest.mark.parametrize("placement", [
    {"sprite_id": "missing", "text_target": "subtitle"},
    {"sprite_id": "missing", "editor": {"businessTarget": "subtitle"}},
])
def test_remotion_subtitles_use_the_ims_estimate(placement):
    """Remotion 字效替代或叠加字幕时不读取资产、不实测，仍按模板中文字幕对象估算。"""
    record = data()
    record["remotion"]["placements"] = [placement]
    assert subtitle_capacity.calculate("test", record) == 9


def test_english_subtitle_does_not_limit_chinese_segments():
    """英文字幕对象不参与中文切片上限，只取中文字幕对象。"""
    record = data()
    record["template"]["tracks"].append({"target": "subtitle", "language": "en", "editor": {"subtitleSize": 300}})
    assert subtitle_capacity.calculate("test", record) == 9
