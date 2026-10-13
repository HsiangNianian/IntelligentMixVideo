"""业务 Remotion 适配回归：参数、字幕区间、快照恢复与 IMS 轨道；云端与模型边界隔离。"""
import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest
from generated.imv.sprite.v1 import sprite_pb2 as pb
from google.protobuf.json_format import MessageToDict

from server.video_composition.remotion_content import ContentError, dynamic_duration, resolve_config, roles
from server.video_composition.remotion_timing import retime_source
from server.remotion_templates.overlay_render import OverlayError
from server.video_composition import remotion, service, store
from server.video_composition.errors import CompositionError


CODE = 'import React from "react"; export default function Sprite(p: any) { return <div>{p.layer.text}</div>; }'
SCHEMA = {"type": "object", "x-imv-dynamic-duration": True, "properties": {
    "layer": {"type": "object", "properties": {
        "text": {"type": "string", "x-imv-role": "text"},
        "ranges": {"type": "array", "x-imv-role": "keywords", "items": {"type": "array", "items": {"type": "integer"}}},
        "frames": {"type": "integer", "x-imv-role": "duration", "minimum": 1},
        "color": {"type": "string"}}, "additionalProperties": False}}, "additionalProperties": False}
DEFAULTS = {"layer": {"text": "旧例字", "ranges": [[0, 3]], "frames": 90, "color": "#ffffff"}}


def asset():
    """一份独立发布数据；默认文案和关键词不能进入最终业务配置。"""
    return pb.PublishedSprite(sprite_id=str(uuid4()), kind=pb.SPRITE_KIND_TEXT, tsx_code=CODE,
        code_sha256=hashlib.sha256(CODE.encode()).hexdigest(), config_schema_json=json.dumps(SCHEMA),
        default_config_json=json.dumps(DEFAULTS), canvas=pb.SpriteCanvas(width=180, height=320, fps=30, preview_frames=90))


def case(composition_case, target="subtitle"):
    """用真实合成请求构建私有资产快照；字幕与标题均来自切片结果。"""
    sprite = asset()
    placement = {"id": "text", "sprite_id": sprite.sprite_id, "start": 1, "duration": 2, "editor": {"business_target": target} if target else {}}
    return {"request": composition_case["request"], "duration_ms": 8000,
            "output": {"width": 1080, "height": 1920, "fps": 30},
            "segmentation": {"segments": composition_case["segments"], "title_keyword": "业务"},
            "remotion": {"revision": 1, "placements": [placement],
                         "assets": {sprite.sprite_id: MessageToDict(sprite, preserving_proto_field_name=True)}}}


@pytest.mark.parametrize("text,keyword,expected", [("😀今天今天", "今天", [[1, 3]]), ("问？你好", "不存在", []), ("空词", "", [])])
def test_resolve_replaces_sample_and_uses_first_codepoint_range(text, keyword, expected):
    """首次关键词使用码点区间；空或未命中清除旧高亮，保留样式且不修改发布值。"""
    sprite = asset()
    original = sprite.SerializeToString()
    result = resolve_config(sprite, text, keyword, 1)
    assert result == {"layer": {"text": text, "ranges": expected, "frames": 1, "color": "#ffffff"}}
    assert sprite.SerializeToString() == original


@pytest.mark.parametrize("role", [True, [], {}, "typo"])
def test_bad_role_is_a_content_error(role):
    """无效角色声明得到业务错误，而非无法解释的类型异常。"""
    with pytest.raises(ContentError):
        roles({"properties": {"text": {"type": "string", "x-imv-role": role}}})


def test_explicit_legacy_mapping_clears_all_declared_sample_fields():
    """补充一个旧字段映射不会漏掉已声明的其他正文实例。"""
    sprite = asset()
    schema = deepcopy(SCHEMA)
    schema["properties"]["old"] = {"type": "string"}
    sprite.config_schema_json = json.dumps(schema)
    sprite.default_config_json = json.dumps({**DEFAULTS, "old": "旧文案"})
    result = resolve_config(sprite, "新的业务", "业务", 15, "old")
    assert result["old"] == result["layer"]["text"] == "新的业务"


@pytest.mark.parametrize("dynamic", [None, False])
def test_fixed_time_uses_host_clock_instead_of_requiring_custom_schema(dynamic):
    """缺少动态声明或明确固定的资产仍可注入正文，实际时长交给合成宿主。"""
    sprite = asset()
    schema = deepcopy(SCHEMA)
    schema.pop("x-imv-dynamic-duration")
    schema["properties"]["layer"]["properties"]["frames"].pop("x-imv-role")
    if dynamic is not None:
        schema["x-imv-dynamic-duration"] = dynamic
    sprite.config_schema_json = json.dumps(schema)
    assert not dynamic_duration(schema)
    assert resolve_config(sprite, "新文案", "", 3)["layer"]["text"] == "新文案"


def test_invalid_dynamic_declaration_is_still_rejected():
    """移除额外准入要求不能放行损坏的显式声明。"""
    with pytest.raises(ContentError, match="布尔值"):
        dynamic_duration({"x-imv-dynamic-duration": "yes"})


def test_plan_uses_absolute_asr_gaps_and_full_audio(composition_case):
    """相同资产的多句字幕独立注入；片头、片中和片尾静音仍属于整段输出。"""
    data = case(composition_case)
    original = deepcopy(data)
    result = remotion.plan(data, 120)
    assert (result.width, result.height, result.fps, result.frames) == (1080, 1920, 30, 240)
    assert [(item["start_frame"], item["duration_frames"]) for item in result.instances] == [(30, 60), (120, 60)]
    assert [item["config"]["layer"]["text"] for item in result.instances] == ["甲乙丙丁", "戊己庚辛"]
    assert [item["config"]["layer"]["ranges"] for item in result.instances] == [[[0, 2]], []]
    assert data == original


def test_plan_handles_legacy_parts_and_very_short_subtitle(composition_case):
    """旧二级短句只首段继承词；末尾不足一帧的字幕只占一帧，不延伸成片。"""
    data = case(composition_case)
    data["segmentation"]["segments"] = [{"segment_id": 1, "text": "甲甲", "keyword": "甲", "start_time": 7.97, "end_time": 8,
        "level": 1, "group_id": [1, 1], "subtitle_parts": [
            {"text": "甲", "start_time": 7.97, "end_time": 7.985}, {"text": "甲", "start_time": 7.985, "end_time": 8}]}]
    result = remotion.plan(data, 120)
    assert all(item["duration_frames"] == 1 and item["start_frame"] == 239 for item in result.instances)
    assert [item["config"]["layer"]["ranges"] for item in result.instances] == [[[0, 1]], []]


@pytest.mark.parametrize("silent", [False, True])
def test_title_uses_template_interval_and_actual_keyword(composition_case, silent):
    """所有模式的标题时间由模板控制，不复用模板示例关键词。"""
    data = case(composition_case, "title")
    if silent:
        data["request"] = {**data["request"], "audioUrl": None, "videoUrl": None, "processRules": {"videoDuration": 8},
                           "materials": [{"fileUrl": "https://media.test/a.jpg", "type": "image"}]}
    result = remotion.plan(data, 120)
    item, = result.instances
    assert (item["start_frame"], item["duration_frames"]) == (30, 60)
    assert item["config"]["layer"]["text"] == data["request"]["title"]
    assert item["config"]["layer"]["ranges"] == [[0, 2]]


@pytest.mark.parametrize("role", ["title", "subtitle"])
def test_upstream_text_slot_follows_its_text_rule_over_legacy_binding(composition_case, role):
    """新文字槽位优先于旧用途，并按本单文字规则重算区间，不沿用预览摆放秒数。"""
    data = case(composition_case, "subtitle" if role == "title" else "title")
    data["template"] = deepcopy(composition_case["template"])
    placement = data["remotion"]["placements"][0]
    placement.update(text_target=role, start=7, duration=0.5)
    if role == "title":
        track = next(item for item in data["template"]["tracks"] if item["target"] == role)
        track.update(start_mode="percent", start=50, duration=2)
    result = remotion.plan(data, 120)
    assert [(item["start_frame"], item["duration_frames"]) for item in result.instances] == (
        [(120, 60)] if role == "title" else [(30, 60), (120, 60)])
    assert result.instances[0]["config"]["layer"]["text"] == (
        data["request"]["title"] if role == "title" else "甲乙丙丁")


@pytest.mark.parametrize("silent", [False, True])
def test_upstream_title_slot_skips_empty_business_title(composition_case, silent):
    """有声或无声的空业务标题都不借用字效预览文案；无字幕时也不触发 IMS 动画校验。"""
    data = case(composition_case)
    data["template"] = composition_case["template"]
    data["remotion"]["placements"][0].update(text_target="title")
    data["request"]["title"] = ""
    if silent:
        data["request"] = {**data["request"], "audioUrl": None, "videoUrl": None,
                           "processRules": {"videoDuration": 8},
                           "materials": [{"fileUrl": "https://media.test/a.jpg", "type": "image"}]}
    assert remotion.plan(data, 120) is None


@pytest.mark.parametrize("kind", [pb.SPRITE_KIND_FILTER_OVERLAY, pb.SPRITE_KIND_VIDEO_OVERLAY,
                                  pb.SPRITE_KIND_TRANSITION_OVERLAY])
def test_upstream_visual_slots_render_without_business_target(composition_case, kind):
    """视觉资产由发布分类识别；绑定转场只围绕指定的真实素材交界处播放。"""
    data = case(composition_case, None)
    sprite = asset()
    sprite.kind = kind
    sprite.config_schema_json = '{"type":"object","properties":{}}'
    sprite.default_config_json = '{}'
    data["remotion"]["assets"] = {sprite.sprite_id: MessageToDict(sprite, preserving_proto_field_name=True)}
    placement = data["remotion"]["placements"][0]
    placement["sprite_id"] = sprite.sprite_id
    timeline = {"VideoTracks": [{"VideoTrackClips": [
        {"TimelineIn": 0, "TimelineOut": 1}, {"TimelineIn": 1, "TimelineOut": 3},
        {"TimelineIn": 3, "TimelineOut": 8}]}]}
    if kind == pb.SPRITE_KIND_TRANSITION_OVERLAY:
        placement.update(start=6, transition_boundary=2)
    result = remotion.plan(data, 120, timeline=timeline)
    item, = result.instances
    assert (item["start_frame"], item["duration_frames"]) == (
        (60, 60) if kind == pb.SPRITE_KIND_TRANSITION_OVERLAY else (30, 60))
    if kind == pb.SPRITE_KIND_TRANSITION_OVERLAY:
        with pytest.raises(ContentError, match="最终素材时间线"):
            remotion.plan(data, 120)
        timeline["VideoTracks"][0]["VideoTrackClips"].pop()
        assert remotion.plan(data, 120, timeline=timeline) is None


def test_no_content_and_historical_task_skip_rendering(composition_case):
    """空标题、无字幕和旧任务均不回退示例，且不读取最新绑定。"""
    data = case(composition_case, "title")
    data["request"]["title"] = ""
    assert remotion.plan(data, 120) is None
    data.pop("remotion")
    assert remotion.plan(data, 120) is None
    assert asyncio.run(remotion.render("unused", data, 10)) is None


def test_limit_and_integrity_fail_closed(composition_case):
    """有效字效的整段时长超限不截断；源码指纹变化不允许渲染。"""
    data = case(composition_case)
    data["duration_ms"] = 120001
    with pytest.raises(OverlayError, match="120"):
        remotion.plan(data, 120)
    data["duration_ms"] = 8000
    next(iter(data["remotion"]["assets"].values()))["tsx_code"] += "// changed"
    with pytest.raises(ContentError, match="校验"):
        remotion.plan(data, 120)


def test_unclassified_or_text_decoration_refused(composition_case):
    """已有未分类绑定必须显式适配；文字资产不能通过装饰用途带入示例。"""
    for target in (None, "decoration"):
        with pytest.raises(ContentError):
            remotion.plan(case(composition_case, target), 120)


@pytest.mark.anyio
async def test_render_uses_shared_slot_and_persists_public_video(composition_case, monkeypatch, tmp_path):
    """实际参数经过共享名额；上传成功才返回可保存结果，临时目录随后清理。"""
    settings = SimpleNamespace(overlay_max_seconds=120)
    slot = asyncio.Semaphore(1)
    runtime = SimpleNamespace(settings=settings, store=SimpleNamespace(root=tmp_path),
                              overlays=SimpleNamespace(slot=slot), harness=SimpleNamespace(renderer="renderer"))
    async def get_runtime():
        """提供唯一运行时，与独立预览渲染共用名额。"""
        return runtime
    async def render(renderer, overlay, work):
        """捕获渲染时名额状态与音频全长。"""
        assert slot.locked() and overlay.frames == 240
        work.mkdir(parents=True)
        output = work / "overlay.webm"
        output.write_bytes(b"video")
        return output
    def upload(files, config, timeout):
        """只有实际输出文件进入公共存储，并使用任务固定对象 key。"""
        path, key, mime = files[0]
        assert path.read_bytes() == b"video" and key == "imv/video_composition/task/overlay.webm" and mime == "video/webm"
        return ["https://zos.test/overlay.webm"]
    monkeypatch.setattr(remotion, "sprite_runtime", get_runtime)
    monkeypatch.setattr(remotion, "environment_problem", lambda settings: None)
    monkeypatch.setattr(remotion, "render_overlay", render)
    monkeypatch.setattr(remotion, "upload_public", upload)
    monkeypatch.setattr(remotion, "ZosSettings", lambda: object())
    result = await remotion.render("task", case(composition_case), 10)
    assert result["duration"] == 8 and result["frames"] == 240
    assert not (tmp_path / "composition-overlays" / "task").exists()


@pytest.mark.anyio
@pytest.mark.parametrize("diagnostic_writable", [True, False])
async def test_render_failure_keeps_private_cause_before_cleanup(composition_case, monkeypatch, tmp_path, diagnostic_writable):
    """失败保留 worker 原始错误及完整异常链，清理视频；诊断写入失败仍返回原始业务错误。"""
    slot = asyncio.Semaphore(1)
    runtime = SimpleNamespace(settings=SimpleNamespace(overlay_max_seconds=120), store=SimpleNamespace(root=tmp_path),
                              overlays=SimpleNamespace(slot=slot), harness=SimpleNamespace(renderer="renderer"))
    async def get_runtime():
        """使用隔离目录和独立名额，不访问实际渲染服务。"""
        return runtime
    async def fail(renderer, overlay, work):
        """模拟 Chrome 连接失败，被 overlay 层包装为安全错误。"""
        work.mkdir(parents=True)
        (work / "worker.log").write_text("Timed out after 25000 ms while trying to connect to the browser!")
        (work / "overlay.webm").write_bytes(b"partial-video")
        try:
            raise RuntimeError("private browser connection timeout")
        except RuntimeError as cause:
            raise OverlayError("render_failed", "叠加视频渲染失败") from cause
    def unwritable(work, error):
        """模拟磁盘写入故障，不掩盖浏览器的原始失败。"""
        raise OSError("disk unavailable")
    monkeypatch.setattr(remotion, "sprite_runtime", get_runtime)
    monkeypatch.setattr(remotion, "environment_problem", lambda settings: None)
    monkeypatch.setattr(remotion, "render_overlay", fail)
    if not diagnostic_writable:
        monkeypatch.setattr(remotion, "preserve_failure", unwritable)
    with pytest.raises(CompositionError) as caught:
        await remotion.render("task", case(composition_case), 10)
    assert caught.value.error == {"code": "remotion_render_failed", "message": "叠加视频渲染失败", "stage": "assembling"}
    assert not slot.locked()
    assert not (tmp_path / "composition-overlays" / "task").exists()
    private = tmp_path / "composition-overlay-failures" / "task"
    if diagnostic_writable:
        assert "25000 ms" in (private / "worker.log").read_text()
        detail = (private / "error.log").read_text()
        assert "RuntimeError: private browser connection timeout" in detail and "OverlayError" in detail
        assert {item.name for item in private.iterdir()} == {"worker.log", "error.log"}
        assert private.stat().st_mode & 0o777 == 0o700


# 场景：渲染前的计划/时长适配打包失败时，公开错误只有固定摘要，打包器 stderr 等根因须进入私有诊断。
@pytest.mark.anyio
async def test_plan_failure_keeps_private_cause(composition_case, monkeypatch, tmp_path):
    """打包失败不进入渲染名额，但异常链与子进程 stderr 仍私有保存，工作目录照常清理。"""
    runtime = SimpleNamespace(settings=SimpleNamespace(overlay_max_seconds=120), store=SimpleNamespace(root=tmp_path))
    async def get_runtime():
        """使用隔离目录，不访问实际渲染服务。"""
        return runtime
    def fail(data, max_seconds, *, timeline=None):
        """模拟时长适配打包器缺少依赖：原因只在 stderr 中。"""
        try:
            raise subprocess.CalledProcessError(1, ["node"], stderr="Cannot find module 'esbuild'")
        except subprocess.CalledProcessError as cause:
            raise ContentError("Remotion 合成时长适配打包失败") from cause
    monkeypatch.setattr(remotion, "sprite_runtime", get_runtime)
    monkeypatch.setattr(remotion, "plan", fail)
    with pytest.raises(CompositionError) as caught:
        await remotion.render("task", case(composition_case), 10)
    assert caught.value.error["code"] == "remotion_asset_not_adapted"
    detail = (tmp_path / "composition-overlay-failures" / "task" / "error.log").read_text()
    assert "CalledProcessError" in detail and "Cannot find module 'esbuild'" in detail
    assert not (tmp_path / "composition-overlays" / "task").exists()


@pytest.mark.anyio
async def test_render_failure_is_terminal_not_sample_fallback(composition_case, monkeypatch, tmp_path):
    """资产已适配但渲染环境不可用时整单失败，不提交无字效成片。"""
    async def runtime():
        """仅提供计划所需配置与私有诊断目录，不提供可降级渲染器。"""
        return SimpleNamespace(settings=SimpleNamespace(overlay_max_seconds=120), store=SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(remotion, "sprite_runtime", runtime)
    monkeypatch.setattr(remotion, "environment_problem", lambda settings: "unavailable")
    with pytest.raises(CompositionError) as error:
        await remotion.render("task", case(composition_case), 10)
    assert error.value.error["code"] == "remotion_render_environment_unavailable"


def visual_case(composition_case):
    """一份只含透明滤镜摆放的快照：无论请求文字如何都会实际渲染。"""
    data = case(composition_case, None)
    sprite = asset()
    sprite.kind = pb.SPRITE_KIND_FILTER_OVERLAY
    sprite.config_schema_json, sprite.default_config_json = '{"type":"object","properties":{}}', '{}'
    data["remotion"]["assets"] = {sprite.sprite_id: MessageToDict(sprite, preserving_proto_field_name=True)}
    data["remotion"]["placements"][0]["sprite_id"] = sprite.sprite_id
    return data


# 场景：只有无内容可显示的文字绑定（例如请求没有标题）时不要求渲染环境，合成照常继续。
@pytest.mark.anyio
async def test_environment_check_skips_bindings_without_content(composition_case, monkeypatch):
    """标题绑定遇空标题、字幕绑定遇无声模式都不会渲染，因此不读取 Remotion 运行时；滤镜总会渲染。"""
    async def runtime():
        """记录被调用，说明本单确实需要渲染环境。"""
        called.append(True)
        return SimpleNamespace(settings=SimpleNamespace())
    called = []
    monkeypatch.setattr(remotion, "sprite_runtime", runtime)
    monkeypatch.setattr(remotion, "environment_problem", lambda settings: "bwrap")
    title = case(composition_case, "title")
    title["request"] = {**title["request"], "title": " "}
    subtitle = case(composition_case, "subtitle")
    subtitle["request"] = {**subtitle["request"], "audioUrl": None, "videoUrl": None, "processRules": {"videoDuration": 8},
                           "materials": [{"fileUrl": "https://media.test/a.jpg", "type": "image"}]}
    for data in (title, subtitle, {**title, "remotion": {"placements": []}}):
        await remotion.require_environment(data)
    assert called == []
    with pytest.raises(CompositionError) as error:
        await remotion.require_environment(visual_case(composition_case))
    assert called == [True] and error.value.error["code"] == "remotion_render_environment_unavailable"


@pytest.mark.anyio
async def test_assemble_reuses_saved_overlay_and_keeps_ims_text(composition_case, composition_runtime, monkeypatch):
    """恢复组装复用已上传地址；叠加从 0 秒开始，IMS 字幕轨保留在其原位置。"""
    data = case(composition_case)
    data.update(template=composition_case["template"], matches=composition_case["matches"])
    result = {"video_url": "https://zos.test/overlay.webm", "duration": 8}
    store.initialize_schema()
    record = store.create(data["request"], {**data["output"], "region_id": "cn-shanghai"}, "https://app.test")
    record = store.advance(record, "assembling", **{key: value for key, value in data.items() if key not in ("request", "output")}, remotion_result=result)
    async def should_not_render(*args):
        """已持久化结果不重复编码或上传。"""
        pytest.fail("重复渲染")
    async def storage(self):
        """无需真实 IMS 账号即可组装最终提交。"""
        return "bucket.oss-cn-shanghai.aliyuncs.com"
    monkeypatch.setattr(remotion, "render", should_not_render)
    monkeypatch.setattr(service.ims.IMS, "storage_location", storage)
    job = service.Job(composition_runtime, record, composition_runtime.settings)
    await job.assemble()
    timeline = job.record["data"]["timeline"]
    assert len(timeline["SubtitleTracks"]) == 3
    overlay = timeline["VideoTracks"][-1]["VideoTrackClips"][0]
    assert (overlay["TimelineIn"], overlay["TimelineOut"], overlay["MediaURL"]) == (0, 8, result["video_url"])
    assert job.record["stage"] == "submitting"


# 捕获显式测试开关；测试夹具稍后清理进程配置，不影响本地集成选择。
REAL_RENDER = os.environ.get("IMV_TEST_RENDERER") == "1"
RENDER_PATHS = {key: os.environ.get(key) for key in ("IMV_BROWSER_EXECUTABLE", "IMV_FONT_REGULAR", "IMV_FONT_BOLD")}


@pytest.mark.skipif(not REAL_RENDER, reason="set IMV_TEST_RENDERER=1 for real dynamic overlay")
def test_real_subtitles_replace_text_and_use_actual_interval(composition_case, tmp_path):
    """已适配资产经隔离 Chromium 输出 VP9 Alpha：空隙透明，实际文字驱动颜色，1 帧可见且尾帧淡出。"""
    from server.remotion_templates.renderer import Renderer
    from server.remotion_templates.settings import Settings
    from server.remotion_templates.overlay_render import render_overlay

    preset = {"type": "object", "x-imv-dynamic-duration": True, "properties": {
        "text": {"type": "string", "x-imv-role": "text"}, "frames": {"type": "integer", "x-imv-role": "duration"},
        "ranges": {"type": "array", "x-imv-role": "keywords"}}, "additionalProperties": False}
    code = """import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
/** 单帧保留正文；其他长度按实际区间安排出场。 */
export default function Label(p: {text:string; frames:number; ranges:number[][]}) {
 const f = useCurrentFrame(), d = useVideoConfig().durationInFrames;
 const valid = p.frames === d;
 const color = !valid ? '#000000' : p.text === '甲' ? '#00ff00' : p.text === '乙' ? '#ff0000' : '#0000ff';
 return <AbsoluteFill style={{background:color, opacity:d <= 3 ? 1 : Math.min(1, (d-f)/2)}}>{p.text}</AbsoluteFill>;
}"""
    data = case(composition_case)
    data.update(duration_ms=1600, output={"width":180,"height":320,"fps":30})
    sprite = asset()
    sprite.tsx_code = code
    sprite.code_sha256 = hashlib.sha256(code.encode()).hexdigest()
    sprite.config_schema_json = json.dumps(preset)
    sprite.default_config_json = json.dumps({"text": "旧例字", "frames": 90, "ranges": [[0, 3]]})
    data["remotion"]["assets"] = {sprite.sprite_id: MessageToDict(sprite, preserving_proto_field_name=True)}
    data["remotion"]["placements"][0]["sprite_id"] = sprite.sprite_id
    data["segmentation"]["segments"] = [
        {"segment_id":i+1,"text":text,"keyword":text if i == 0 else "", "level":1,"group_id":[1,1],"start_time":start,"end_time":end}
        for i,(text,start,end) in enumerate([( "甲",0.1,0.3),("乙",0.6,19/30),("甲",0.9,1.5)])]
    configured = {field:Path(RENDER_PATHS[key]) for field,key in [("browser_executable","IMV_BROWSER_EXECUTABLE"),("font_regular","IMV_FONT_REGULAR"),("font_bold","IMV_FONT_BOLD")] if RENDER_PATHS[key]}
    settings = Settings(_env_file=None, data_dir=tmp_path / "state", **configured)
    video = asyncio.run(render_overlay(Renderer(settings), remotion.plan(data, 120), tmp_path / "render"))
    for frame, color, alpha in [(0,None,0),(3,"green",1),(8,"green",0.5),(12,None,0),(18,"red",1),(44,"green",0.5),(47,None,0)]:
        pixels = subprocess.run(["ffmpeg","-v","error","-c:v","libvpx-vp9","-i",str(video),"-vf",f"select=eq(n\\,{frame})","-frames:v","1","-f","rawvideo","-pix_fmt","rgba","-"], capture_output=True,check=True).stdout
        pixel = pixels[(100*180+100)*4:][:4]
        assert pixel[3]/255 == pytest.approx(alpha,abs=0.05)
        if color:
            assert pixel[1 if color == "green" else 0] > 240


@pytest.mark.anyio
async def test_snapshot_survives_binding_edit(composition_case, template_db):
    """共享资产读取后，修改模板轨道不会替换已受理任务的资产集合。"""
    from datetime import UTC, datetime
    from server.remotion_templates import sprite_store
    from server.template.schema import EffectTrack
    data = case(composition_case)
    sprite_id, published = next(iter(data["remotion"]["assets"].items()))
    sprite_store.insert(sprite_id=sprite_id, source_version_id=str(uuid4()), kind=1,
                        text_prop="", keywords_prop="", published_at=datetime.now(UTC), data=published)
    template = SimpleNamespace(updated_at=datetime.now(UTC), tracks=[EffectTrack.model_validate({
        "id": "text", "target": "remotion", "sprite_id": sprite_id, "start_mode": "seconds",
        "start": 1, "duration": 2, "text_target": "subtitle", "editor": {},
    })])
    data["remotion"] = await remotion.snapshot(template)
    data["template"] = composition_case["template"]
    assert data["remotion"]["placements"][0]["text_target"] == "subtitle"
    assert data["remotion"]["placements"][0]["transition_boundary"] is None
    template.tracks.clear()
    assert remotion.plan(data, 120).instances
    assert (await remotion.snapshot(template))["placements"] == []


def test_render_exception_chain_does_not_leak_source():
    """隔离编译器的原始源码片段不能通过异常链进入七模块执行日志。"""
    from server.video_composition.execution_log import exception_details
    try:
        try:
            raise RuntimeError("export default PRIVATE_SOURCE with business text")
        except RuntimeError as cause:
            raise CompositionError("remotion_render_failed", "Remotion 字效渲染或上传失败", "assembling") from cause
    except CompositionError as error:
        details = json.dumps(exception_details(error), ensure_ascii=False)
    assert "PRIVATE_SOURCE" not in details and "业务原文" not in details
    assert "Remotion 字效渲染或上传失败" in details


# 场景：Remotion 错误只公开摘要，但执行日志仍须能定位根因的类型与代码位置，否则只能看到一句固定文案。
def test_render_exception_chain_keeps_root_cause_location():
    """根因保留类型、调用位置与错误码，不保留可能含源码或业务文字的文本。"""
    from server.video_composition.execution_log import exception_details
    def bundle():
        """模拟打包子进程失败，其文本与输出均属私有。"""
        raise subprocess.CalledProcessError(1, ["node", "PRIVATE_SOURCE"], stderr="PRIVATE_SOURCE")
    try:
        try:
            bundle()
        except subprocess.CalledProcessError as cause:
            raise CompositionError("remotion_asset_not_adapted", "Remotion 合成时长适配打包失败", "assembling") from cause
    except CompositionError as error:
        details = exception_details(error)
    root = details["exceptions"][-1]
    assert root["type"] == "CalledProcessError" and "message" not in root
    assert root["frames"][-1]["function"] == "bundle"
    assert "PRIVATE_SOURCE" not in json.dumps(details, ensure_ascii=False)


def test_new_category_preserves_style_transform_and_injects_business_copy(composition_case):
    """上游顶层词列表契约：运营样式和 HostLayer 保留，正文及例词被业务输入覆盖。"""
    from .conftest import published_sprite
    sprite = published_sprite()
    sprite.text_prop, sprite.keywords_prop = "text", "keywords"
    data = case(composition_case)
    data["remotion"]["assets"] = {sprite.sprite_id: MessageToDict(sprite, preserving_proto_field_name=True)}
    item = data["remotion"]["placements"][0]
    item["sprite_id"] = sprite.sprite_id
    item["editor"].update(parameters=[{"key": "color", "string_value": "#FF0000"}, {"key": "text", "string_value": "预览例字"}], transform={"x": 300, "opacity": 0.5})
    first = remotion.plan(data, 120).instances[0]
    assert first["config"]["text"] == "甲乙丙丁"
    assert first["config"]["keywords"] == ["甲乙"]
    assert first["config"]["color"] == "#FF0000"
    assert first["host"]["transform"] == {"x": 300, "opacity": 0.5}


def test_upstream_fixed_asset_is_bundled_once_without_changing_snapshot(composition_case, monkeypatch):
    """真实上游契约不含私有标记；标题与多句字幕共用宿主，区间、正文及已存源码不改变。"""
    from .conftest import published_sprite
    sprite = published_sprite()
    sprite.text_prop = "text"
    data = case(composition_case)
    data["remotion"]["assets"] = {sprite.sprite_id: MessageToDict(sprite, preserving_proto_field_name=True)}
    item = data["remotion"]["placements"][0]
    item["sprite_id"] = sprite.sprite_id
    data["remotion"]["placements"].append({**item, "editor": {"business_target": "title"}})
    before = deepcopy(data)
    bundled = []
    def bundle(value):
        """记录每个资产的打包次数，返回可信宿主测试占位源码。"""
        bundled.append(value.sprite_id)
        return "timed source"
    monkeypatch.setattr(remotion, "retime_source", bundle)
    result = remotion.plan(data, 120)
    assert bundled == [sprite.sprite_id]
    assert [(i["start_frame"], i["duration_frames"]) for i in result.instances] == [(30, 60), (120, 60), (30, 60)]
    assert all(i["code"] == "timed source" for i in result.instances)
    assert [i["config"]["text"] for i in result.instances[:2]] == ["甲乙丙丁", "戊己庚辛"]
    assert data == before


def test_fixed_asset_rejects_invalid_metadata_and_unsafe_imports():
    """固定时钟必须有合法时长；打包只解析源码，不能借资产导入读取本地文件。"""
    sprite = asset()
    sprite.canvas.preview_frames = 0
    with pytest.raises(ContentError, match="发布时长"):
        retime_source(sprite)
    sprite.canvas.preview_frames = 90
    sprite.tsx_code = 'import secret from "/etc/passwd"; export default secret;'
    with pytest.raises(ContentError, match="打包失败") as rejected:
        retime_source(sprite)
    assert "Unsupported Sprite import" in rejected.value.__cause__.stderr


# 场景：业务区间与发布时长不成整数比时，映射到资产的帧号仍须是整数，按帧下标取值的资产不会取到 undefined。
def test_fixed_clock_maps_to_whole_source_frames(tmp_path):
    """用替身 React/Remotion 执行真实打包的时间宿主：每帧都是整数，首尾对齐发布首尾帧且单调不减。"""
    sprite = asset()
    sprite.canvas.preview_frames = 150
    (tmp_path / "timed.mjs").write_text(retime_source(sprite))
    stubs = {
        "react/package.json": '{"name":"react","type":"module","exports":{".":"./index.js","./jsx-runtime":"./jsx-runtime.js"}}',
        "react/index.js": "/** React 替身。 */ export default {};",
        "react/jsx-runtime.js": "/** 元素即普通对象，便于读取上下文。 */ export const jsx = (type, props) => ({type, props}); export const jsxs = jsx;",
        "remotion/package.json": '{"name":"remotion","type":"module","exports":"./index.js"}',
        "remotion/index.js": """/** 由测试设置当前帧与业务区间长度的 Remotion 替身。 */
export const useCurrentFrame = () => globalThis.frame;
export const useVideoConfig = () => ({id: "overlay", fps: 30, durationInFrames: globalThis.length});
export const Internals = {RemotionContextProvider: "provider",
  useRemotionContexts: () => ({compositionManagerCtx: {}, timelineContext: {}, sequenceContext: {}})};""",
    }
    for name, content in stubs.items():
        (tmp_path / "node_modules" / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "node_modules" / name).write_text(content)
    (tmp_path / "probe.mjs").write_text("""/** 逐帧读取宿主交给资产的发布帧号。 */
import TimedSprite from "./timed.mjs";
globalThis.length = 60;
const frames = [];
for (globalThis.frame = 0; globalThis.frame < 60; globalThis.frame++) frames.push(TimedSprite({}).props.contexts.timelineContext.frame.overlay);
console.log(JSON.stringify(frames));
""")
    frames = json.loads(subprocess.run(["node", "probe.mjs"], cwd=tmp_path, capture_output=True, text=True, check=True).stdout)
    assert all(isinstance(frame, int) for frame in frames)
    assert frames[0] == 0 and frames[-1] == 149 and frames == sorted(frames)


@pytest.mark.skipif(not REAL_RENDER, reason="set IMV_TEST_RENDERER=1 for upstream clock regression")
def test_real_upstream_sequences_keep_animation_at_short_long_and_single_frame_intervals(composition_case, tmp_path):
    """真实上游组合器生成固定 Sequence：短/长区间、非零起点、不同帧率、一帧和 Alpha 空隙。"""
    from server.remotion_templates.tools.compose import compose_source
    from server.remotion_templates.renderer import Renderer
    from server.remotion_templates.settings import Settings
    from server.remotion_templates.overlay_render import render_overlay

    code = """import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
/** 固定原动画第 0～2 帧淡入、第 5～7 帧淡出；参数替换错误会变成蓝色。 */
export default function Text(p) {
 const f = useCurrentFrame(), c = useVideoConfig();
 const valid = c.durationInFrames === 8 && c.fps === 24;
 const color = !valid ? '#0000ff' : p.text === '甲' && p.keywords[0] === '甲' ? '#00ff00' : '#ff0000';
 return <AbsoluteFill style={{background: color, opacity: Math.max(0, Math.min(1, f/2, (7-f)/2))}}/>;
}"""
    schema = {"type": "object", "properties": {"text": {"type": "string"},
              "keywords": {"type": "array", "items": {"type": "string"}}}, "additionalProperties": False}
    # 内层从第 2 帧开始，验证时钟既不会沿用外层绝对时间，也不受外层短 Sequence 截断。
    source, schema, defaults = compose_source([{"instance_id": "label", "preset": {"code": code, "parameter_schema": schema},
        "parameters": {"text": "旧例字", "keywords": ["旧词"]},
        "layout": {"x": 0, "y": 0, "width": 180, "height": 320, "z_index": 0},
        "timing": {"start_frame": 2, "duration_frames": 8}}],
        [{"name": key, "title": key, "targets": [{"instance_id": "label", "parameter": key}]} for key in ["text", "keywords"]])
    sprite = asset()
    sprite.tsx_code, sprite.code_sha256 = source, hashlib.sha256(source.encode()).hexdigest()
    sprite.canvas.fps, sprite.canvas.preview_frames = 24, 10
    sprite.text_prop, sprite.keywords_prop = "text", "keywords"
    sprite.config_schema_json, sprite.default_config_json = json.dumps(schema), json.dumps(defaults)
    data = case(composition_case)
    data.update(duration_ms=3500, output={"width": 180, "height": 320, "fps": 30})
    data["remotion"]["assets"] = {sprite.sprite_id: MessageToDict(sprite, preserving_proto_field_name=True)}
    data["remotion"]["placements"][0]["sprite_id"] = sprite.sprite_id
    data["segmentation"]["segments"] = [
        {"segment_id": i+1, "text": text, "keyword": text, "level": 1, "group_id": [1, 1], "start_time": start/30, "end_time": end/30}
        for i, (text, start, end) in enumerate([("甲", 3, 9), ("乙", 30, 90), ("甲", 96, 97), ("乙", 100, 102)])]
    paths = {field: Path(RENDER_PATHS[key]) for field, key in [("browser_executable", "IMV_BROWSER_EXECUTABLE"),
        ("font_regular", "IMV_FONT_REGULAR"), ("font_bold", "IMV_FONT_BOLD")] if RENDER_PATHS[key]}
    settings = Settings(_env_file=None, data_dir=tmp_path / "state", overlay_render_timeout_seconds=180, **paths)
    video = asyncio.run(render_overlay(Renderer(settings), remotion.plan(data, 120), tmp_path / "render"))
    pixels = subprocess.run(["ffmpeg", "-v", "error", "-c:v", "libvpx-vp9", "-i", str(video),
        "-f", "rawvideo", "-pix_fmt", "rgba", "-"], capture_output=True, check=True).stdout
    # 短动画仍遍历首尾，长动画超过源 10 帧之后仍可见，一/二帧都保留可读画面。
    for frame, color, alpha in [(0,None,0),(3,None,0),(5,"green",0.8),(6,"green",1),(7,"green",0.9),
        (8,None,0),(15,None,0),(50,"red",0.525),(65,"red",1),(88,"red",0.076),(89,None,0),
        (90,None,0),(96,"green",1),(97,None,0),(100,"red",1),(101,"red",1),(102,None,0)]:
        offset = (frame*180*320 + 100*180+100)*4
        pixel = pixels[offset:offset+4]
        assert pixel[3]/255 == pytest.approx(alpha, abs=0.06), (frame, list(pixel))
        if color:
            assert pixel[1 if color == "green" else 0] > 225, (frame, list(pixel))
