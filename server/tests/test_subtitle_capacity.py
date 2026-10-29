"""字幕容量：离线覆盖选择、快照、失败与清理；IMV_TEST_RENDERER=1 时用隔离 Chromium 核对真实字效排版。

执行 uv run --locked --project server pytest server/tests/test_subtitle_capacity.py -v；不访问模型、IMS 或生产数据库。
"""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from generated.imv.sprite.v1 import sprite_pb2 as pb
from google.protobuf.json_format import MessageToDict, ParseDict
import pytest

from server.remotion_templates.renderer import Renderer
from server.remotion_templates.fonts import FontStore
from server.remotion_templates.settings import Settings
from server.remotion_templates.tool_validation import ToolValidator, ValidationUnavailable
from server.video_composition import subtitle_capacity
from server.video_composition.errors import CompositionError
from server.video_composition.remotion_content import capacity_samples

from .conftest import SPRITE_ID, published_sprite

pytestmark = pytest.mark.anyio
# 自动隔离夹具会清除 IMV_*，先记录显式指定的本机浏览器/字体位置。
RENDERER_PATHS = {key: os.environ.get(name) for key, name in (
    ("browser_executable", "IMV_BROWSER_EXECUTABLE"), ("font_regular", "IMV_FONT_REGULAR"), ("font_bold", "IMV_FONT_BOLD"),
)}


def data(editor=None):
    """一个有中文 IMS 字幕的任务快照。"""
    return {"request": {"processRules": {}}, "output": {"width": 1080, "height": 1920, "fps": 30},
            "template": {"tracks": [{"target": "subtitle", "editor": editor or {"subtitleSize": 96}}]},
            "remotion": {"placements": [], "assets": {}}}


@pytest.mark.parametrize("editor,width,expected", [
    ({"subtitleSize": 96}, 1080, 9), ({"subtitleSize": 96}, 720, 5),
    ({"subtitleSize": 96, "subtitleX": 40}, 1080, 7),
    ({"subtitleSize": 96, "subtitleKeywordSize": 192}, 1080, 3), ({"subtitleSize": 300}, 720, 1),
])
async def test_ims_full_width_formula(editor, width, expected):
    """IMS 保留原有按输出宽度、位置与较大字号估算的行为，不改变快照。"""
    record = data(editor)
    record["output"]["width"] = width
    before = deepcopy(record)
    assert await subtitle_capacity.calculate("test", record) == expected
    assert record == before


@pytest.mark.parametrize("requested", [4, 100])
async def test_legacy_request_limit_does_not_change_capacity(requested):
    """旧任务请求的手动上限不参与计算。"""
    record = data()
    record["request"]["processRules"]["subtitleMaxChars"] = requested
    assert await subtitle_capacity.calculate("test", record) == 9


async def test_unusable_position_is_not_reported_as_capacity():
    """文字中心贴边放不下一字时明确失败。"""
    with pytest.raises(CompositionError) as failure:
        await subtitle_capacity.calculate("test", data({"subtitleX": 0}))
    assert failure.value.error["code"] == "subtitle_capacity_too_small"


def with_effect(placement: dict, defaults: dict | None = None) -> dict:
    """加入一条有正文/关键词契约的已发布字效；默认源码由浏览器用例提供。"""
    sprite = published_sprite()
    sprite.text_prop, sprite.keywords_prop = "text", "keywords"
    if defaults:
        sprite.default_config_json = json.dumps({**json.loads(sprite.default_config_json), **defaults})
    record = data()
    record["remotion"] = {"placements": [{"id": "fx", "sprite_id": SPRITE_ID, **placement}],
                          "assets": {SPRITE_ID: MessageToDict(sprite, preserving_proto_field_name=True)}}
    return record


@pytest.mark.parametrize("font_size", [64, None])
async def test_replacing_effect_uses_measured_capacity(monkeypatch, font_size):
    """包括没有字号参数的字效：实测容量优先于隐藏 IMS 与静态字号公式。"""
    record = with_effect({"text_target": "subtitle"}, {"fontSize": font_size})
    before = deepcopy(record)
    measured = AsyncMock(return_value=[8])
    monkeypatch.setattr(subtitle_capacity, "remotion_capacities", measured)
    assert await subtitle_capacity.calculate("test", record) == 6
    measured.assert_awaited_once()
    assert record == before


@pytest.mark.parametrize("ims_size,expected", [(48, 12), (192, 3)])
async def test_legacy_effect_and_ims_subtitle_both_count(monkeypatch, ims_size, expected):
    """旧 businessTarget 的两套字幕共存，任意一方较小都会限制容量。"""
    record = with_effect({"editor": {"business_target": "subtitle"}})
    record["template"]["tracks"][0]["editor"] = {"subtitleSize": ims_size}
    monkeypatch.setattr(subtitle_capacity, "remotion_capacities", AsyncMock(return_value=[14]))
    assert await subtitle_capacity.calculate("test", record) == expected


async def test_english_subtitle_does_not_start_measurement(monkeypatch):
    """英文与标题字效不参与中文字幕上限，不启动测量。"""
    record = with_effect({"text_target": "subtitle", "language": "en"})
    record["template"]["tracks"].append({"target": "subtitle", "language": "en", "editor": {"subtitleSize": 300}})
    record["remotion"]["placements"].append({"sprite_id": SPRITE_ID, "text_target": "title"})
    measured = AsyncMock()
    monkeypatch.setattr(subtitle_capacity, "remotion_capacities", measured)
    assert await subtitle_capacity.calculate("test", record) == 9
    measured.assert_not_awaited()


async def test_measured_zero_fails_before_segmentation(monkeypatch):
    """实测一字放不下时保留 template 阶段错误，不退回隐藏的 IMS 字号。"""
    monkeypatch.setattr(subtitle_capacity, "remotion_capacities", AsyncMock(return_value=[0]))
    with pytest.raises(CompositionError) as failure:
        await subtitle_capacity.calculate("test", with_effect({"text_target": "subtitle"}))
    assert failure.value.error["code"] == "subtitle_capacity_too_small"
    assert failure.value.error["stage"] == "template"


def placements(record):
    """读取本例的冻结资产，保留多摆放顺序。"""
    return [(item, ParseDict(record["remotion"]["assets"][item["sprite_id"]], published_sprite()))
            for item in record["remotion"]["placements"]]


def test_probes_keep_overrides_and_bound_samples():
    """同一资产只打包一次；覆盖、缩放、预览帧率和长短/关键词样本完整传入，不改输入。"""
    record = with_effect({"text_target": "subtitle", "editor": {
        "parameters": [{"key": "fontSize", "number_value": 100}], "transform": {"x": 270, "scale": 2},
    }})
    record["remotion"]["placements"].append({"sprite_id": SPRITE_ID, "text_target": "subtitle"})
    before = deepcopy(record)
    result = subtitle_capacity.probes(placements(record))
    assert len(result) == 1 and len(result[0].items) == 2
    first, second = result[0].items
    assert first["transform"]["x"] == 270 and first["transform"]["scale"] == 2
    assert first["frames"] == [0, 22, 44, 66, 89]
    assert result[0].canvas["fps"] == 30
    assert [sample["chars"] for sample in first["samples"]] == list(range(1, 41))
    assert first["samples"][-1]["values"][0]["fontSize"] == 100
    assert first["samples"][-1]["values"][1]["keywords"] == ["测" * 40]
    assert second["samples"][-1]["values"][0]["fontSize"] == 64
    assert record == before


def test_probes_accept_a_font_added_after_publication(monkeypatch):
    """字体覆盖按实时字体库校验，浏览器样本不能再被发布时的旧枚举拒绝。"""
    sprite = browser_sprite(TEXT_CODE.replace("SPACING", "0").replace("SCALE", "1").replace("SIZE", "props.fontSize"))
    schema, config = json.loads(sprite.config_schema_json), json.loads(sprite.default_config_json)
    schema["properties"]["fontFamily"] = {"type": "string", "enum": ["Old Font"]}
    config["fontFamily"] = "Old Font"
    sprite.config_schema_json, sprite.default_config_json = json.dumps(schema), json.dumps(config)
    sprite.parameters.add(key="fontFamily", label="字体", access=pb.OPERATOR_ACCESS_VISIBLE_EDITABLE,
                          default_value=pb.ScalarValue(string_value="Old Font"))
    monkeypatch.setattr(FontStore, "active", SimpleNamespace(families=lambda: ["Old Font", "New Font"]))
    query = subtitle_capacity.probes([({"editor": {"parameters": [{"key": "fontFamily", "string_value": "New Font"}]}}, sprite)])[0]
    assert query.items[0]["samples"][0]["values"][0]["fontFamily"] == "New Font"
    assert "enum" not in query.component.parameter_schema["properties"]["fontFamily"]
    assert json.loads(sprite.config_schema_json)["properties"]["fontFamily"]["enum"] == ["Old Font"]


def test_capacity_samples_keep_nested_roles_and_legacy_keyword_ranges():
    """多正文路径取最紧长度契约；旧嵌套正文/关键词区间和动态帧数仍按原协议注入。"""
    sprite = browser_sprite("export default function Text() {return null;}", tiers=True)
    schema, config = json.loads(sprite.config_schema_json), json.loads(sprite.default_config_json)
    schema["properties"]["layer"] = {"type": "object", "properties": {
        "text": {"type": "string", "minLength": 5, "maxLength": 7, "x-imv-role": "text"},
        "keywords": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}, "x-imv-role": "keywords"},
        "frames": {"type": "integer", "x-imv-role": "duration"},
    }}
    config["layer"] = {"text": "旧示例文字", "keywords": [], "frames": 1}
    sprite.config_schema_json = json.dumps(schema)
    lines, samples = capacity_samples(sprite, config, 90)
    assert lines == 2 and [sample["chars"] for sample in samples] == [2, 3]
    sample = samples[-1]
    plain, highlighted = sample["values"]
    assert plain["text"] == plain["layer"]["text"] == sample["text"]
    assert plain["lineStartFrames"] == [0, 0] and plain["layer"]["frames"] == 90
    assert plain["layer"]["keywords"] == []
    assert highlighted["layer"]["keywords"] == [[0, 3], [4, 7]]
    assert config["layer"]["text"] == "旧示例文字"


@pytest.fixture
def measuring_runtime(tmp_path, monkeypatch):
    """共享渲染名额与 worker 的替身：断言字体及测量都在准入内，暴露请求和清理状态。"""
    state = SimpleNamespace(active=False, requests=[], error=None, report=None, fonts=False)

    @asynccontextmanager
    async def claim(chunks, pages):
        """测量只占一个浏览器；退出含取消时释放。"""
        assert (chunks, pages) == (1, 1)
        state.active = True
        try:
            yield
        finally:
            state.active = False

    class Worker:
        """只返回离线容量结论，不执行组件源码。"""

        def worker_browser_path(self):
            """返回不可执行的测试路径。"""
            return "/test/chrome"

        async def run_worker(self, directory, **kwargs):
            """捕获真实序列化请求，并在准入内返回或抛出测试指定故障。"""
            assert state.active and state.fonts
            state.requests.append(json.loads((directory / "request.json").read_text()))
            if state.error:
                raise state.error
            return state.report if state.report is not None else {"capacities": [[{"index": 0, "capacity": 8}]]}

    async def fonts(overlay):
        """字体准备接收封存源码和合并参数。"""
        assert state.active and overlay.instances[0]["config"]["fontSize"] == 64
        state.fonts = True

    runtime = SimpleNamespace(store=SimpleNamespace(root=tmp_path), settings=SimpleNamespace(render_timeout_seconds=60),
                              harness=SimpleNamespace(renderer=Worker()),
                              overlays=SimpleNamespace(slot=asyncio.Semaphore(1), capacity=SimpleNamespace(claim=claim)))
    monkeypatch.setattr(subtitle_capacity.remotion, "sprite_runtime", AsyncMock(return_value=runtime))
    monkeypatch.setattr(subtitle_capacity, "require_fonts", fonts)
    return state


async def test_capacity_worker_lifecycle(measuring_runtime, tmp_path):
    """完整主机桥接返回测量结果，保留输出画布用于 cover 裁切，成功后清理工作目录。"""
    record = with_effect({"text_target": "subtitle"})
    record["output"]["width"] = 540
    assert await subtitle_capacity.calculate("test", record) == 6
    request = measuring_runtime.requests[0]
    assert request["mode"] == "capacity" and request["composition"]["width"] == 540
    assert request["sprites"][0]["canvas"]["width"] == 1080
    assert not measuring_runtime.active and not (tmp_path / "subtitle-capacity/test").exists()


@pytest.mark.parametrize("report", [{}, {"capacities": []}, {"capacities": [[{"index": 0, "capacity": True}]]},
                                        {"capacities": [[{"index": 0, "capacity": 41}]]}])
async def test_incomplete_capacity_report_is_not_an_estimate(measuring_runtime, report, tmp_path):
    """缺失、布尔或越界容量都是测量故障，不能静默使用 IMS 或伪造正数。"""
    measuring_runtime.report = report
    with pytest.raises(CompositionError) as failure:
        await subtitle_capacity.calculate("test", with_effect({"text_target": "subtitle"}))
    assert failure.value.error["code"] == "subtitle_capacity_unavailable"
    assert isinstance(failure.value.__cause__, ValidationUnavailable)
    assert not measuring_runtime.active and not (tmp_path / "subtitle-capacity/test").exists()


@pytest.mark.parametrize("error", [TimeoutError("private worker detail"), asyncio.CancelledError()])
async def test_capacity_failure_and_cancellation_release_resources(measuring_runtime, tmp_path, error):
    """超时返回固定摘要；取消继续传播，两者都释放名额并清理样本。"""
    measuring_runtime.error = error
    with pytest.raises(asyncio.CancelledError if isinstance(error, asyncio.CancelledError) else CompositionError) as failure:
        await subtitle_capacity.calculate("test", with_effect({"text_target": "subtitle"}))
    assert "private worker detail" not in str(failure.value)
    assert not measuring_runtime.active and not (tmp_path / "subtitle-capacity/test").exists()


# 一个真实文字块：宽度控制换行，样式可复现字号之外的尺寸因素；不编码视频。
TEXT_CODE = '''/** 用固定内容区域验证字距、内部缩放及关键词/分栏的实际排版。 */
import React from "react";
export default function Text(props) {
  return <div style={{position:"absolute",left:90,top:760,width:900,height:400,display:"flex",alignItems:"center",justifyContent:"center"}}>
    <div style={{width:900,textAlign:"center",wordBreak:"break-all",lineHeight:1.2,fontFamily:"Noto Sans CJK SC",color:"white",letterSpacing:SPACING,transform:"scale(SCALE)"}}>
      {props.text.split("\\n").map((text,index)=><div key={index} style={{fontSize:SIZE}}>{text}</div>)}
    </div>
  </div>;
}'''


def browser_sprite(code, *, size=True, tiers=False, keyword=False):
    """提供真实源码及严格参数契约，可用无字号参数的旧字效或双栏布局。"""
    sprite = published_sprite()
    sprite.text_prop, sprite.keywords_prop = "text", "keywords"
    sprite.tsx_code = code
    sprite.code_sha256 = hashlib.sha256(code.encode()).hexdigest()
    schema = json.loads(sprite.config_schema_json)
    config = json.loads(sprite.default_config_json)
    if not size:
        schema["properties"].pop("fontSize")
        config.pop("fontSize")
    if tiers:
        schema["properties"]["lineStartFrames"] = {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 3600}, "minItems": 2, "maxItems": 2}
        config["lineStartFrames"] = [0, 0]
    if keyword:
        schema["properties"]["keywordSize"] = {"type": "number", "minimum": 0, "maximum": 300}
        config["keywordSize"] = 128
    sprite.config_schema_json, sprite.default_config_json = json.dumps(schema), json.dumps(config)
    return sprite


@pytest.mark.skipif(os.environ.get("IMV_TEST_RENDERER") != "1", reason="set IMV_TEST_RENDERER=1 for isolated Chromium")
async def test_real_capacity_measures_layout_instead_of_font_size(tmp_path):
    """真实 Chromium 覆盖字距、内部/宿主缩放、位置、关键词、双栏、写死字号及输出 cover 裁切。"""
    settings = Settings(_env_file=None, data_dir=tmp_path / "state",
                        **{key: Path(value) for key, value in RENDERER_PATHS.items() if value})
    cases = [
        (0, 1, "props.fontSize", {}, {}, 14),
        (16, 1, "props.fontSize", {}, {}, 11),
        (0, 1.5, "props.fontSize", {}, {}, 11),
        (0, 1, "128", {"size": False}, {}, 7),
        (0, 1, "props.keywords.includes(text) ? props.keywordSize : props.fontSize", {"keyword": True}, {}, 7),
        (0, 1, "props.fontSize * (index === 1 ? 1.5 : 1)", {"tiers": True}, {}, 9),
        (0, 1, "props.fontSize", {}, {"scale": 2}, 8),
        (0, 1, "props.fontSize", {}, {"x": 270}, 8),
    ]
    queries = []
    for spacing, scale, size, options, transform, _ in cases:
        code = TEXT_CODE.replace("SPACING", str(spacing)).replace("SCALE", str(scale)).replace("SIZE", size)
        sprite = browser_sprite(code, **options)
        queries.extend(subtitle_capacity.probes([({"editor": {"transform": transform}}, sprite)]))
    before = deepcopy(queries)
    validator = ToolValidator(Renderer(settings), tmp_path / "fit")
    result = await validator.text_capacities(queries, {"width": 1080, "height": 1920, "fps": 30, "duration_frames": 90},
                                           timeout_seconds=180, cpu_seconds=300)
    assert [group[0]["capacity"] for group in result] == [case[-1] for case in cases]
    assert queries == before
    cropped = await validator.text_capacities(queries[:1], {"width": 540, "height": 1920, "fps": 30, "duration_frames": 90},
                                            timeout_seconds=90, cpu_seconds=300)
    assert cropped[0][0]["capacity"] == 8
