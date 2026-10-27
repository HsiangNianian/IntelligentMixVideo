"""模板一行字数与切片上限：离线计算/协议测试，IMV_TEST_RENDERER=1 时另跑真实隔离浏览器。

执行 uv run --locked --project server pytest server/tests/test_subtitle_capacity.py -v；不访问模型、IMS 或生产数据库。
"""
import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

from generated.imv.sprite.v1 import sprite_pb2 as pb
from google.protobuf.json_format import MessageToDict
import pytest

from server.remotion_templates.renderer import Renderer
from server.remotion_templates.settings import Settings
from server.remotion_templates.tool_validation import FitSprite, ToolValidator, ValidationUnavailable
from server.remotion_templates.tools.contracts import ComponentDefinition
from server.video_composition import subtitle_capacity
from server.video_composition.errors import CompositionError

REAL_BROWSER = os.environ.get("IMV_TEST_RENDERER") == "1"
BROWSER = {name: Path(value) for name, value in (
    ("browser_executable", os.environ.get("IMV_BROWSER_EXECUTABLE")),
    ("font_regular", os.environ.get("IMV_FONT_REGULAR")), ("font_bold", os.environ.get("IMV_FONT_BOLD")),
) if value}


def data(editor=None):
    """一个有中文 IMS 字幕、可选手动上限的任务快照。"""
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
    assert asyncio.run(subtitle_capacity.calculate("test", record)) == expected
    assert record == before


@pytest.mark.parametrize("requested,expected", [(4, 4), (100, 9)])
def test_manual_limit_can_only_lower_capacity(requested, expected):
    """手动上限只进一步收紧模板计算值，不把九字容量扩大为一百字。"""
    record = data()
    record["request"]["processRules"]["subtitleMaxChars"] = requested
    assert asyncio.run(subtitle_capacity.calculate("test", record)) == expected


def test_unusable_position_is_not_reported_as_capacity():
    """文字中心贴着画布边缘，一字也放不下时明确失败，不伪造正数容量。"""
    with pytest.raises(CompositionError) as failure:
        asyncio.run(subtitle_capacity.calculate("test", data({"subtitleX": 0})))
    assert failure.value.error["code"] == "subtitle_capacity_too_small"


def test_nested_text_and_legacy_subtitles_keep_ims_constraint():
    """旧字效可声明嵌套正文；新槽位替代 IMS，旧用途与 IMS 共存，各自参与容量计算且不改快照。"""
    code = 'export default function Text(p: any) { return <div>{p.layer.text}</div>; }'
    schema = {"type": "object", "properties": {"layer": {"type": "object", "properties": {
        "text": {"type": "string", "x-imv-role": "text", "maxLength": 60}}}}}
    sprite = pb.PublishedSprite(sprite_id="test", kind=pb.SPRITE_KIND_TEXT,
        tsx_code=code, code_sha256=hashlib.sha256(code.encode()).hexdigest(), config_schema_json=json.dumps(schema),
        default_config_json=json.dumps({"layer": {"text": "示例"}}),
        canvas=pb.SpriteCanvas(width=1080, height=1920, fps=30, preview_frames=60))
    record = data()
    placement = {"sprite_id": "test", "editor": {"businessTarget": "subtitle", "transform": {"scale": 2}}}
    record["remotion"] = {"placements": [placement], "assets": {"test": MessageToDict(sprite, preserving_proto_field_name=True)}}
    original = deepcopy(record)
    counts, probes = subtitle_capacity.measurement_inputs(record)
    assert counts == [11]
    assert probes[0].items[0]["text_paths"] == ["layer.text"]
    assert probes[0].items[0]["transform"] == {"scale": 2}
    assert probes[0].items[0]["max_chars"] == 60
    assert record == original
    placement["text_target"] = "subtitle"
    assert subtitle_capacity.measurement_inputs(record)[0] == []
    placement["language"] = "en"
    assert subtitle_capacity.measurement_inputs(record) == ([11], [])


CODE = '''import React from "react";
export default function Subtitle(p: any) {
  return <div style={{position:"absolute", left:0, top:100, width:p.width, fontFamily:p.fontFamily,
    fontSize:p.fontSize, lineHeight:1.2, wordBreak:"break-all", textAlign:"center", color:"white"}}>
    {p.text.split("\\n").map((text: string, i: number) => <div key={i} style={{fontSize:p.fontSize * (i ? p.ratio : 1)}}>{text}</div>)}
  </div>;
}'''


def probe(values, transform=None):
    """同一容器内一或两栏文字，宽度、字号、字体和宿主缩放可独立变化。"""
    defaults = {"text": "测", "width": 480, "fontSize": 48, "fontFamily": "Noto Sans CJK SC", "ratio": 1, **values}
    return FitSprite(ComponentDefinition(code=CODE, parameter_schema={"type": "object"}, default_parameters=defaults),
        {"width": 1080, "height": 1920}, {"x": 0, "y": 100, "width": defaults["width"], "height": 300},
        [{"values": defaults, "text_paths": ["text"], "lines": len(defaults["text"].split("\n")),
          "max_chars": 40, "length": 30, "transform": transform or {}}])


@pytest.mark.parametrize("answer", [None, [], [[True]], [[-1]], [[41]], [[1, 2]]])
def test_invalid_capacity_report_is_unavailable(tmp_path, answer):
    """缺失、越界与错误类型的浏览器回执均不能被当作有效容量。"""
    class FakeRenderer:
        def worker_browser_path(self):
            return "/browser"

        async def run_worker(self, directory, **kwargs):
            return {"capacities": answer}

    with pytest.raises(ValidationUnavailable):
        asyncio.run(ToolValidator(FakeRenderer(), tmp_path).line_capacities([probe({})],
            {"width": 1080, "height": 1920, "fps": 30, "duration_frames": 30}))


@pytest.mark.skipif(not REAL_BROWSER, reason="set IMV_TEST_RENDERER=1 for real isolated browser measurements")
def test_real_line_capacity_respects_font_size_wrapping_tiers_and_transform(tmp_path):
    """真实 Chrome：同宽改字号会减半，双栏取大字一栏，缩放后出画布也降低容量，换字体重新挂载。"""
    renderer = Renderer(Settings(_env_file=None, data_dir=tmp_path / "state", **BROWSER))
    probes = [probe({}), probe({"fontSize": 96}), probe({"text": "测\n量", "ratio": 2}),
              probe({"width": 960}, {"scale": 2, "x": 540, "y": 400}), probe({"fontFamily": "Noto Serif CJK SC"})]
    result = asyncio.run(ToolValidator(renderer, tmp_path / "measure").line_capacities(probes,
        {"width": 1080, "height": 1920, "fps": 30, "duration_frames": 30}, timeout_seconds=180, cpu_seconds=600))
    assert result == [[10], [5], [5], [11], [10]]
