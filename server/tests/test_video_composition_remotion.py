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
from server.remotion_templates.settings import Settings
from server.remotion_templates import render_resources
from server.remotion_templates.render_resources import RenderCapacity
from server.video_composition import execution_log, remotion, service, store
from server.video_composition.errors import CompositionError


@pytest.fixture(autouse=True)
def resource_capacity(monkeypatch):
    """业务离线回归使用确定的资源余量；不依赖开发机的负载。"""
    monkeypatch.setattr(render_resources, "renderer_cpus", lambda reserve: list(range(8)))
    monkeypatch.setattr(render_resources, "memory_headroom", lambda reserve: (48 - 64 * reserve / 100) * 1024**3)


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


@pytest.mark.parametrize("role", ["title", "subtitle"])
def test_bilingual_shared_sprite_has_independent_content_and_timing(composition_case, role):
    """同一 Sprite 的中英文摆放分别注入译文，按各自语言替换 IMS；停用后不产生英文实例。"""
    from server.video_composition.timeline import build_timeline
    from .conftest import remotion_track

    data = case(composition_case, role)
    data["template"] = deepcopy(composition_case["template"])
    source = next(track for track in data["template"]["tracks"] if track["target"] == role)
    english = {**deepcopy(source), "id": f"{role}-en", "language": "en"}
    placement = data["remotion"]["placements"][0]
    placement.update(text_target=role)
    data["template"]["tracks"].append(english)
    for language, track_id in (("zh", "effect-zh"), ("en", "effect-en")):
        data["template"]["tracks"].append(remotion_track(track_id, sprite_id=placement["sprite_id"], text_target=role, language=language))
    data["remotion"]["placements"].append({**deepcopy(placement), "id": "text-en", "language": "en"})
    data["segmentation"].update(title_en="Business, title!", title_keyword_en="Business")
    for item, text in zip(data["segmentation"]["segments"], ["Don't stop!", "Keep going."]):
        item.update(text_en=text, keyword_en="")
    overlay = remotion.plan(data, 120)
    texts = [item["config"]["layer"]["text"] for item in overlay.instances]
    assert texts == ([data["request"]["title"], "Business, title!"] if role == "title" else ["甲乙丙丁", "戊己庚辛", "Don't stop", "Keep going"])
    # 英文标题、字幕的字效按单行设计（排版预检据此缩到一行），中文只要求放得下；字幕轨换行时朝远离另一语言的方向展开，
    # 英文向下、中文向上，标题不要求方向。
    assert [item.get("one_line", False) for item in overlay.instances] == ([False, True] if role == "title" else [False, False, True, True])
    assert [item.get("grow") for item in overlay.instances] == ([None, None] if role == "title" else ["up", "up", "down", "down"])
    half = len(overlay.instances) // 2
    assert [(item["start_frame"], item["duration_frames"]) for item in overlay.instances[:half]] == [(item["start_frame"], item["duration_frames"]) for item in overlay.instances[half:]]
    timeline, _ = build_timeline(**{**composition_case, "template": data["template"], "segments": data["segmentation"]["segments"]}, title_en="Business, title!")
    ims_texts = [clip["Content"] for row in timeline["SubtitleTracks"] for clip in row["SubtitleTrackClips"]]
    assert not set(texts) & set(ims_texts)
    english["enabled"] = False
    disabled = remotion.plan(data, 120)
    assert len(disabled.instances) == half
    # 没有启用英文字幕时中文字幕不再朝上展开，保持原本的居中展开。
    assert all("grow" not in item for item in disabled.instances)


@pytest.mark.parametrize("renderer", ["ims", "remotion"])
@pytest.mark.parametrize("content,keyword,expected,clean_keyword", [
    ('  "Don\'t stop!"\nPay 3.14?  ', "Don't", "Don't stop\nPay 3.14?", "Don't"),
    ("O’Reilly isn’t late.", "O’Reilly", "O’Reilly isn’t late", "O’Reilly"),
    ("Paid 3.14. Why？", "3.14.", "Paid 3.14 Why？", "3.14"),
    ("，。!\"'…", "…", "", ""),
])
def test_english_subtitle_punctuation_and_keywords_are_cleaned_at_rendering(
    composition_case, renderer, content, keyword, expected, clean_keyword,
):
    """两种渲染器的英文字幕共用清洗规则；关键词同步、空文字跳过，原始快照不变。"""
    from server.video_composition.timeline import build_timeline

    role = "subtitle"
    data = case(composition_case, role)
    data["template"] = deepcopy(composition_case["template"])
    source = next(track for track in data["template"]["tracks"] if track["target"] == role)
    english = {**deepcopy(source), "id": f"{role}-en", "language": "en"}
    english["editor"][f"{role}KeywordBold"] = True
    data["template"]["tracks"].append(english)
    data["segmentation"].update(title_en=content, title_keyword_en=keyword)
    data["segmentation"]["segments"] = data["segmentation"]["segments"][:1]
    data["segmentation"]["segments"][0].update(text_en=content, keyword_en=keyword)
    data["remotion"]["placements"][0].update(text_target=role, language="en")
    original = deepcopy(data)
    if renderer == "ims":
        timeline, _ = build_timeline(
            **{**composition_case, "template": data["template"], "segments": data["segmentation"]["segments"],
               "matches": composition_case["matches"][:1]},
            title_en=content, title_keyword_en=keyword,
        )
        texts = [clip["Content"] for row in timeline["SubtitleTracks"] for clip in row["SubtitleTrackClips"]
                 if clip.get("AdaptMode") == "AutoScale"]
        styled = expected.replace(clean_keyword, r"{\b1}" + clean_keyword + r"{\b0}", 1) if clean_keyword else expected
        assert texts == ([styled] if expected else [])
    else:
        overlay = remotion.plan(data, 120)
        if expected:
            layer = overlay.instances[0]["config"]["layer"]
            assert layer["text"] == expected
            start = expected.index(clean_keyword)
            assert layer["ranges"] == [[start, start + len(clean_keyword)]]
        else:
            assert overlay is None
    assert data == original


@pytest.mark.anyio
async def test_disabled_english_does_not_read_sprite_or_require_renderer(composition_case, monkeypatch):
    """停用英文的模板配置仍存在，但合成快照、环境检查与恢复计划都不读取其资产。"""
    from server.template.schema import Template
    from .conftest import remotion_track

    data = case(composition_case, "title")
    template = deepcopy(composition_case["template"])
    chinese = next(track for track in template["tracks"] if track["target"] == "title")
    template["tracks"].extend([{**deepcopy(chinese), "id": "title-en", "language": "en", "enabled": False},
                               remotion_track(text_target="title", language="en")])
    monkeypatch.setattr(remotion.sprites, "get", lambda *_: pytest.fail("不应读取停用英文资产"))
    monkeypatch.setattr(remotion, "sprite_runtime", lambda: pytest.fail("不应检查停用英文的渲染环境"))
    snapshot = await remotion.snapshot(Template.model_validate(template))
    assert snapshot["placements"] == [] and snapshot["assets"] == {}
    data["template"] = template
    data["remotion"]["placements"][0].update(text_target="title", language="en")
    data["remotion"]["assets"] = {}  # 恢复时也不能读取停用绑定。
    await remotion.require_environment(data)
    assert remotion.plan(data, 120) is None


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


@pytest.mark.parametrize("mode", ["sequence", "random"])
@pytest.mark.parametrize("duration", [0.6, None])
def test_transition_list_uses_the_same_selection_for_remotion(composition_case, mode, duration):
    """混合列表只在选中 Remotion 的连接处生成实例，并保留自身时长。"""
    from server.template.transitions import select_transitions

    data = case(composition_case, None)
    sprite = asset()
    sprite.kind = pb.SPRITE_KIND_TRANSITION_OVERLAY
    sprite.canvas.preview_frames = 18
    sprite.config_schema_json = '{"type":"object","properties":{}}'
    sprite.default_config_json = '{}'
    data["remotion"]["assets"] = {sprite.sprite_id: MessageToDict(sprite, preserving_proto_field_name=True)}
    placement = data["remotion"]["placements"][0]
    placement.update(sprite_id=sprite.sprite_id, transition_boundary=2, duration=duration)
    candidates = [{"id": "ims", "target": "transition", "transition_boundary": 1},
                  {"id": placement["id"], "target": "remotion", "transition_boundary": 2}]
    data["template"] = {"transition_mode": mode, "transition_duration_seconds": 0.2, "tracks": candidates}
    clips = [{"TimelineIn": index * 1.6, "TimelineOut": (index + 1) * 1.6} for index in range(5)]
    timeline = {"VideoTracks": [{"VideoTrackClips": clips}]}
    original = deepcopy(data)
    overlay = remotion.plan(data, 120, timeline=timeline, transition_seed="mixed-task")
    selected = select_transitions(candidates, 4, mode, "mixed-task")
    boundaries = [boundary for item, boundary in selected if item["target"] == "remotion"]
    assert overlay is not None
    assert [item["start_frame"] for item in overlay.instances] == [round((boundary * 1.6 - 0.3) * 30) for boundary in boundaries]
    assert all(item["duration_frames"] == 18 for item in overlay.instances)
    assert data == original
    data["template"]["transition_enabled"] = False
    assert remotion.plan(data, 120, timeline=timeline, transition_seed="mixed-task") is None
    assert asyncio.run(remotion.require_environment(data)) is None
    data["template"]["transition_enabled"] = True
    data["remotion"]["placements"].append({**placement, "id": "decoration", "transition_boundary": None, "order": 1, "start": 0, "duration": 0.2})
    layered = remotion.plan(data, 120, timeline=timeline, transition_seed="mixed-task")
    assert [item["start_frame"] for item in layered.instances] == [item["start_frame"] for item in overlay.instances] + [0]
    data["template"]["transition_enabled"] = False
    disabled = remotion.plan(data, 120, timeline=timeline, transition_seed="mixed-task")
    assert len(disabled.instances) == 1
    assert disabled.instances[0]["start_frame"] == 0
    data["template"]["transition_enabled"] = True
    assert remotion.plan(data, 120, timeline=timeline, transition_seed="mixed-task").instances == layered.instances


def test_no_content_and_historical_task_skip_rendering(composition_case):
    """空标题、无字幕和旧任务均不回退示例，且不读取最新绑定。"""
    data = case(composition_case, "title")
    data["request"]["title"] = ""
    assert remotion.plan(data, 120) is None
    data.pop("remotion")
    assert remotion.plan(data, 120) is None
    assert asyncio.run(remotion.render("unused", data, 10)) is None


def test_limit_and_integrity_fail_closed(composition_case):
    """有效字效的整段时长超过默认 300 秒不截断；源码指纹变化不允许渲染。"""
    max_seconds = Settings(_env_file=None).overlay_max_seconds
    data = case(composition_case)
    data["duration_ms"] = 300001
    with pytest.raises(OverlayError, match="300"):
        remotion.plan(data, max_seconds)
    data["duration_ms"] = 8000
    next(iter(data["remotion"]["assets"].values()))["tsx_code"] += "// changed"
    with pytest.raises(ContentError, match="校验"):
        remotion.plan(data, max_seconds)


def test_unclassified_or_text_decoration_refused(composition_case):
    """已有未分类绑定必须显式适配；文字资产不能通过装饰用途带入示例。"""
    for target in (None, "decoration"):
        with pytest.raises(ContentError):
            remotion.plan(case(composition_case, target), 120)


@pytest.mark.anyio
@pytest.mark.parametrize("duration, frames, cpus, pages", [(8, 240, 1, 1), (121, 3630, 3, 3), (300, 9000, 8, 4)])
@pytest.mark.parametrize("playground", [False, True])
async def test_render_uses_shared_slot_and_persists_public_video(composition_case, monkeypatch, tmp_path, caplog, duration, frames, cpus, pages, playground):
    """默认支持短片、超过旧 120 秒限制及恰好 5 分钟的成片；完整渲染上传后返回并清理。"""
    settings = Settings(_env_file=None)
    monkeypatch.setattr(render_resources, "renderer_cpus", lambda reserve: list(range(cpus)))
    slot = asyncio.Semaphore(1)
    runtime = SimpleNamespace(settings=settings, store=SimpleNamespace(root=tmp_path),
                              overlays=SimpleNamespace(slot=slot, capacity=RenderCapacity(Settings(_env_file=None))), harness=SimpleNamespace(renderer="renderer"))
    # 名额已取得后模拟两秒资源等待，确保诊断不会把它再次算进 slot_wait_ms。
    from contextlib import asynccontextmanager
    clock = [0.0]
    claim = runtime.overlays.capacity.claim
    @asynccontextmanager
    async def delayed_claim(requested, configured_pages):
        async with claim(requested, configured_pages) as grant:
            clock[0] += 2
            yield render_resources.RenderGrant(grant.chunks, grant.pages, 2000, grant.reserved_memory_mb)
    monkeypatch.setattr(runtime.overlays.capacity, "claim", delayed_claim)
    monkeypatch.setattr(remotion, "perf_counter", lambda: clock[0])
    async def get_runtime():
        """提供唯一运行时，与独立预览渲染共用名额。"""
        return runtime
    async def render(renderer, overlay, work, *, chunk_concurrency, frame_concurrency):
        """捕获渲染时名额状态与音频全长。"""
        assert slot.locked() and overlay.frames == frames
        assert frame_concurrency == pages
        assert chunk_concurrency == min(cpus // pages, (frames + 449) // 450)
        assert runtime.overlays.capacity.chunks == chunk_concurrency
        work.mkdir(parents=True, exist_ok=True)
        output = work / "overlay.webm"
        output.write_bytes(b"video")
        return output
    def upload(files, config, timeout):
        """只有实际输出文件进入公共存储，并使用任务固定对象 key。"""
        path, key, mime = files[0]
        prefix = "imv/playground" if playground else "imv/video_composition"
        assert path.read_bytes() == b"video" and key == f"{prefix}/task/overlay.webm" and mime == "video/webm"
        return ["https://zos.test/overlay.webm"]
    monkeypatch.setattr(remotion, "sprite_runtime", get_runtime)
    monkeypatch.setattr(remotion, "environment_problem", lambda settings, *args, **kwargs: None)
    monkeypatch.setattr(remotion, "render_overlay", render)
    monkeypatch.setattr(remotion, "upload_public", upload)
    monkeypatch.setattr(remotion, "ZosSettings", lambda: object())
    data = case(composition_case)
    if playground:
        data["playground"] = {"mode": "standard", "source_task_id": "sample"}
    data["duration_ms"] = duration * 1000
    with caplog.at_level("INFO", logger=remotion.logger.name):
        result = await remotion.render("task", data, 10)
    assert result["duration"] == duration and result["frames"] == frames
    assert set(result) == {"video_url", "duration", "width", "height", "fps", "frames"}
    [entry] = [record for record in caplog.records if record.msg.startswith("Remotion performance task_id=")]
    performance = json.loads(entry.args[-1])
    assert all(performance[key] >= 0 for key in ("plan_ms", "slot_wait_ms", "text_fit_ms", "upload_ms", "total_ms"))
    assert (performance["slot_wait_ms"], performance["resource_wait_ms"]) == (0, 2000)
    assert "旧例字" not in entry.getMessage() and "https://" not in entry.getMessage()
    assert not (tmp_path / "composition-overlays" / "task").exists()


@pytest.mark.anyio
async def test_resource_timeout_has_a_distinct_composition_error(composition_case, monkeypatch, tmp_path):
    """容量超时不会被归为渲染器故障，公开可重试摘要且不泄漏名额或预算。"""
    settings = Settings(_env_file=None, overlay_resource_wait_seconds=.02)
    overlays = SimpleNamespace(slot=asyncio.Semaphore(1), capacity=RenderCapacity(settings))
    async def runtime():
        return SimpleNamespace(settings=settings, store=SimpleNamespace(root=tmp_path), overlays=overlays)
    monkeypatch.setattr(remotion, "sprite_runtime", runtime)
    monkeypatch.setattr(remotion, "environment_problem", lambda settings, *args, **kwargs: None)
    monkeypatch.setattr(render_resources, "memory_headroom", lambda reserve: 0)
    with pytest.raises(CompositionError) as error:
        await remotion.render("task", case(composition_case), 10)
    assert error.value.error == {"code": "remotion_resource_unavailable", "message": "渲染资源暂时不足，请稍后重试", "stage": "assembling"}
    assert not overlays.slot.locked() and overlays.capacity.chunks == 0


@pytest.mark.anyio
@pytest.mark.parametrize("diagnostic_writable", [True, False])
async def test_render_failure_keeps_private_cause_before_cleanup(composition_case, composition_runtime, composition_logs, monkeypatch, tmp_path, diagnostic_writable):
    """失败引用已有私有根因；私有诊断写入失败时仍保存通用错误文件，不掩盖原错误。"""
    slot = asyncio.Semaphore(1)
    runtime = SimpleNamespace(settings=Settings(_env_file=None, overlay_max_seconds=120), store=SimpleNamespace(root=tmp_path),
                              overlays=SimpleNamespace(slot=slot, capacity=RenderCapacity(Settings(_env_file=None))), harness=SimpleNamespace(renderer="renderer"))
    async def get_runtime():
        """使用隔离目录和独立名额，不访问实际渲染服务。"""
        return runtime
    async def fail(renderer, overlay, work, *, chunk_concurrency, frame_concurrency):
        """模拟 Chrome 连接失败，被 overlay 层包装为安全错误。"""
        work.mkdir(parents=True, exist_ok=True)
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
    monkeypatch.setattr(remotion, "environment_problem", lambda settings, *args, **kwargs: None)
    monkeypatch.setattr(remotion, "render_overlay", fail)
    if not diagnostic_writable:
        monkeypatch.setattr(remotion, "preserve_failure", unwritable)
    legacy = tmp_path / "composition-overlay-failures" / "task" / "error.log"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("历史诊断")
    store.initialize_schema()
    record = store.advance(store.create(composition_case["request"], {}, task_id="task"), "assembling")
    with pytest.raises(CompositionError) as caught:
        await composition_runtime.step(record, "remotion_render", lambda: remotion.render("task", case(composition_case), 10), {})
    assert caught.value.error == {"code": "remotion_render_failed", "message": "叠加视频渲染失败", "stage": "assembling"}
    store.advance(record, "failed", status="failed", error=caught.value.error,
                  diagnostics=execution_log.exception_details(caught.value))
    entries = composition_logs("task", raw=True)[0]["timeline"]["error_log"]
    assert [entry["action"] for entry in entries] == ["remotion_render.step_failed", "assembling.task_finished"]
    assert "private browser connection timeout" not in json.dumps(entries)
    assert not slot.locked()
    assert not (tmp_path / "composition-overlays" / "task").exists()
    private = execution_log.LOG_ROOT / "video_composition" / "task" / "errors"
    for entry in entries:
        relative = Path(entry["error"]["detail_log_path"])
        assert not relative.is_absolute()
        linked = execution_log.LOG_ROOT / "video_composition" / "task" / relative
        assert linked.is_file()
        if diagnostic_writable:
            assert relative.as_posix() == "errors/remotion-error.log"
            assert linked.resolve() == private / "remotion-error.log"
        else:
            assert relative.parent == Path("errors")
            assert "private browser connection timeout" not in linked.read_text()
    if diagnostic_writable:
        assert "25000 ms" in (private / "remotion-worker.log").read_text()
        detail = (private / "remotion-error.log").read_text()
        assert "RuntimeError: private browser connection timeout" in detail and "OverlayError" in detail
        assert {item.name for item in private.iterdir()} == {"remotion-worker.log", "remotion-error.log"}
        assert private.stat().st_mode & 0o777 == 0o700
        # JSON 单独未超限，加入 Remotion 文件才超限；已结束任务应整体清理。
        monkeypatch.setattr(execution_log, "MAX_BYTES", sum(path.stat().st_size for path in private.parent.glob("*.json")))
        execution_log.cleanup(store.finished_tasks)
        assert not private.parent.exists()
    assert legacy.read_text() == "历史诊断"


# 场景：渲染前的计划/时长适配打包失败时，公开错误只有固定摘要，打包器 stderr 等根因须进入私有诊断。
@pytest.mark.anyio
async def test_plan_failure_keeps_private_cause(composition_case, monkeypatch, tmp_path):
    """打包失败不进入渲染名额，但异常链与子进程 stderr 仍私有保存，工作目录照常清理。"""
    runtime = SimpleNamespace(settings=Settings(_env_file=None, overlay_max_seconds=120), store=SimpleNamespace(root=tmp_path))
    async def get_runtime():
        """使用隔离目录，不访问实际渲染服务。"""
        return runtime
    def fail(data, max_seconds, *, timeline=None, transition_seed=""):
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
    detail = (execution_log.LOG_ROOT / "video_composition" / "task" / "errors" / "remotion-error.log").read_text()
    assert "CalledProcessError" in detail and "Cannot find module 'esbuild'" in detail
    assert not (tmp_path / "composition-overlays" / "task").exists()


@pytest.mark.anyio
async def test_render_failure_is_terminal_not_sample_fallback(composition_case, monkeypatch, tmp_path):
    """资产已适配但渲染环境不可用时整单失败，不提交无字效成片。"""
    async def runtime():
        """仅提供计划所需配置与私有诊断目录，不提供可降级渲染器。"""
        return SimpleNamespace(settings=Settings(_env_file=None, overlay_max_seconds=120), store=SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(remotion, "sprite_runtime", runtime)
    monkeypatch.setattr(remotion, "environment_problem", lambda settings, *args, **kwargs: "unavailable")
    with pytest.raises(CompositionError) as error:
        await remotion.render("task", case(composition_case), 10)
    assert error.value.error["code"] == "remotion_render_environment_unavailable"


@pytest.mark.anyio
@pytest.mark.parametrize("seconds", [15, 16])
async def test_composition_preflight_uses_actual_chunk_count(composition_case, monkeypatch, tmp_path, seconds):
    """合成的真实预检允许无 FFmpeg 的单块输出，多块时在申请容量与渲染前失败。"""
    from server.remotion_templates import overlay_render
    path = tmp_path / "environment-file"
    path.write_bytes(b"file")
    settings = Settings(_env_file=None, browser_executable=path, font_regular=path, font_bold=path)
    slot = asyncio.Semaphore(1)
    capacity = RenderCapacity(settings)
    async def runtime():
        return SimpleNamespace(settings=settings, store=SimpleNamespace(root=tmp_path),
                               overlays=SimpleNamespace(slot=slot, capacity=capacity), harness=SimpleNamespace(renderer=None))
    called = []
    async def render(renderer, overlay, work, **kwargs):
        called.append(True)
        output = work / "overlay.webm"
        output.write_bytes(b"video")
        return output
    async def fit(*args):
        pass
    monkeypatch.setattr(remotion, "sprite_runtime", runtime)
    monkeypatch.setattr(overlay_render.shutil, "which", lambda tool: None if tool == "ffmpeg" else "/usr/bin/" + tool)
    monkeypatch.setattr(remotion, "render_overlay", render)
    monkeypatch.setattr(remotion, "fit_texts", fit)
    monkeypatch.setattr(remotion, "upload_public", lambda *args: ["https://zos.example/video.webm"])
    monkeypatch.setattr(remotion, "ZosSettings", lambda: object())
    data = case(composition_case)
    data["duration_ms"] = seconds * 1000
    if seconds == 15:
        assert (await remotion.render("task", data, 10))["duration"] == seconds
        assert called == [True]
    else:
        with pytest.raises(CompositionError) as error:
            await remotion.render("task", data, 10)
        assert error.value.error["code"] == "remotion_render_environment_unavailable"
        assert not called and not slot.locked() and capacity.chunks == 0


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
@pytest.mark.parametrize("saved_mode", [False, True])
async def test_environment_check_skips_bindings_without_content(composition_case, monkeypatch, saved_mode):
    """标题绑定遇空标题、字幕绑定遇无声模式都不会渲染，因此不读取 Remotion 运行时；滤镜总会渲染。"""
    async def runtime():
        """记录被调用，说明本单确实需要渲染环境。"""
        called.append(True)
        return SimpleNamespace(settings=SimpleNamespace())
    called = []
    monkeypatch.setattr(remotion, "sprite_runtime", runtime)
    monkeypatch.setattr(remotion, "environment_problem", lambda settings, *args, **kwargs: "bwrap")
    title = case(composition_case, "title")
    title["request"] = {**title["request"], "title": " "}
    subtitle = case(composition_case, "subtitle")
    subtitle["request"] = {**subtitle["request"], "audioUrl": None, "videoUrl": None, "processRules": {"videoDuration": 8},
                           "materials": [{"fileUrl": "https://media.test/a.jpg", "type": "image"}]}
    if saved_mode:
        subtitle["request"].update(compositionMode="materials_silent", text=None,
                                   videoUrl=composition_case["request"]["videoUrl"])
    for data in (title, subtitle, {**title, "remotion": {"placements": []}}):
        await remotion.require_environment(data)
    assert remotion.plan(subtitle, 120) is None
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
    from server.template.schema import Template
    data = case(composition_case)
    sprite_id, published = next(iter(data["remotion"]["assets"].items()))
    sprite_store.insert(sprite_id=sprite_id, source_version_id=str(uuid4()), kind=1,
                        text_prop="", keywords_prop="", published_at=datetime.now(UTC), data=published)
    template = Template.model_validate({**composition_case["template"], "tracks": [{
        "id": "text", "target": "remotion", "sprite_id": sprite_id, "start_mode": "seconds",
        "start": 1, "duration": 2, "text_target": "subtitle", "editor": {},
    }]})
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


def fitted_case(composition_case, code="", title="甲乙丙丁", font_size=64):
    """一个带 text、fontSize（默认 font_size）参数的上游字效作标题：源码为 code，按固定时钟播放（不声明动态时长）。"""
    from .conftest import published_sprite
    sprite = published_sprite()
    sprite.text_prop = "text"
    sprite.default_config_json = json.dumps({**json.loads(sprite.default_config_json), "fontSize": font_size}, ensure_ascii=False)
    sprite.tsx_code, sprite.code_sha256 = code, hashlib.sha256(code.encode()).hexdigest()
    data = case(composition_case, target="title")
    data["request"]["title"] = title
    data["remotion"]["assets"] = {sprite.sprite_id: MessageToDict(sprite, preserving_proto_field_name=True)}
    data["remotion"]["placements"][0]["sprite_id"] = sprite.sprite_id
    return data, sprite


# 场景：合成里的标题与字幕交给 Remotion 渲染时，实例带上资产与实际显示的文字，供渲染前的排版预检测量。
def test_plan_names_the_sprite_and_text_for_the_fit(composition_case, monkeypatch):
    """标题实例的 sprite_id 是快照里的资产，text 是请求标题。"""
    monkeypatch.setattr(remotion, "retime_source", lambda sprite: "timed source")
    data, sprite = fitted_case(composition_case, title="一个业务标题")
    [instance] = remotion.plan(data, 120).instances
    assert (instance["sprite_id"], instance["text"], instance["code"]) == (sprite.sprite_id, "一个业务标题", "timed source")


# 场景：预检测量合成真正运行的源码（固定时钟包装），在资产自己的画布上排版，成片尺寸用于计算 cover 后的可见部分。
def test_fit_request_carries_the_rendered_code_and_the_sprite_canvas(composition_case, monkeypatch, tmp_path):
    """横版成片里的竖版字效：请求的成片是 1920x1080，Sprite 画布是 1080x1920，源码是时钟包装后的源码，文字是请求标题。"""
    from server.remotion_templates.overlay_render import fit_texts

    class Recorder:
        """记录排版预检请求，并回答所有文字都放得下。"""

        settings = SimpleNamespace(render_timeout_seconds=180)
        requests: list[dict] = []

        def worker_browser_path(self):
            """返回固定浏览器路径。"""
            return "/browser"

        async def run_worker(self, directory, *, worker=None, timeout_seconds=None, cpu_seconds=None):
            """保存请求，按原字号作答。"""
            request = json.loads((directory / "request.json").read_text())
            self.requests.append(request)
            return {"fits": [[{"index": item["index"], "fits": True, "size": item["values"]["fontSize"]} for item in sprite["fit"]]
                             for sprite in request["sprites"]]}

    monkeypatch.setattr(remotion, "retime_source", lambda sprite: "timed source")
    data, sprite = fitted_case(composition_case, title="一个业务标题")
    data["output"] = {"width": 1920, "height": 1080, "fps": 30}
    renderer = Recorder()
    asyncio.run(fit_texts(renderer, remotion.plan(data, 120), {sprite.sprite_id: sprite}, tmp_path / "fit"))
    [request] = renderer.requests
    [measured] = request["sprites"]
    assert (request["composition"]["width"], request["composition"]["height"]) == (1920, 1080)
    assert (measured["code"], measured["canvas"]) == ("timed source", {"width": 1080, "height": 1920})
    assert [item["text"] for item in measured["fit"]] == ["一个业务标题"]


# 场景：合成渲染先在共享名额内做排版预检、再渲染；放不下的标题以 remotion_text_too_long 失败，不出被裁切的成片。
@pytest.mark.anyio
async def test_render_fits_texts_in_the_slot_and_reports_text_too_long(composition_case, monkeypatch, tmp_path):
    """预检拿到快照里的发布资产且已占名额；它拒绝时任务失败于 assembling，渲染器不被调用。"""
    slot = asyncio.Semaphore(1)
    runtime = SimpleNamespace(settings=Settings(_env_file=None, overlay_max_seconds=120), store=SimpleNamespace(root=tmp_path),
                              overlays=SimpleNamespace(slot=slot, capacity=RenderCapacity(Settings(_env_file=None))), harness=SimpleNamespace(renderer="renderer"))
    data, sprite = fitted_case(composition_case)
    calls = []

    async def get_runtime():
        """提供唯一运行时。"""
        return runtime

    async def refuse(renderer, overlay, publications, directory):
        """记录预检时的名额与资产，然后报告放不下。"""
        calls.append((slot.locked(), list(publications), overlay.instances[0]["text"]))
        raise OverlayError("text_too_long", "文字过长：缩到最小字号仍无法完整显示，请缩短文字")

    async def render(*args):
        """预检失败后不应渲染。"""
        raise AssertionError("rendered after a failed fit")

    monkeypatch.setattr(remotion, "retime_source", lambda sprite: "timed source")
    monkeypatch.setattr(remotion, "sprite_runtime", get_runtime)
    monkeypatch.setattr(remotion, "environment_problem", lambda settings, *args, **kwargs: None)
    monkeypatch.setattr(remotion, "fit_texts", refuse)
    monkeypatch.setattr(remotion, "render_overlay", render)
    with pytest.raises(CompositionError) as caught:
        await remotion.render("task", data, 10)
    assert calls == [(True, [sprite.sprite_id], "甲乙丙丁")]
    assert caught.value.error == {"code": "remotion_text_too_long", "message": "文字过长：缩到最小字号仍无法完整显示，请缩短文字", "stage": "assembling"}


# 972px 宽、自动换行的居中标题，字号就是 fontSize 参数。
WRAPPED_TITLE = """import React from "react";
import { AbsoluteFill } from "remotion";
export default function Title(props: { text: string; fontSize: number }) {
  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div style={{ width: 972, textAlign: "center", fontSize: props.fontSize, lineHeight: 1.2, whiteSpace: "normal", wordBreak: "break-all", color: "#fff" }}>{props.text}</div>
    </AbsoluteFill>
  );
}
"""


# 场景：真实浏览器里，竖版字效（1080x1920）放进横版成片（1920x1080）时按 cover 缩放，只有中间约 607px 高的部分出现在视频里；
# 40 字标题在 128px 时有 6 行（约 922px 高），放得下整块画布却超出可见部分。预检测量合成真正运行的固定时钟源码，
# 把字号缩到可见部分放得下的最大值（5 行 × 1.2 × 字号 ≤ 607，约 100）。
@pytest.mark.skipif(not REAL_RENDER, reason="set IMV_TEST_RENDERER=1 for the real text fit")
def test_real_fit_measures_the_part_of_the_canvas_the_video_shows(composition_case, tmp_path):
    """标题字号落在 90～103。"""
    from server.remotion_templates.overlay_render import fit_texts
    from server.remotion_templates.renderer import Renderer
    from server.remotion_templates.settings import Settings

    data, sprite = fitted_case(composition_case, WRAPPED_TITLE, "测" * 40, font_size=128)
    data["output"] = {"width": 1920, "height": 1080, "fps": 30}
    overlay = remotion.plan(data, 120)
    configured = {field: Path(RENDER_PATHS[key]) for field, key in [("browser_executable", "IMV_BROWSER_EXECUTABLE"),
        ("font_regular", "IMV_FONT_REGULAR"), ("font_bold", "IMV_FONT_BOLD")] if RENDER_PATHS[key]}
    settings = Settings(_env_file=None, data_dir=tmp_path / "state", **configured)
    asyncio.run(fit_texts(Renderer(settings), overlay, {sprite.sprite_id: sprite}, tmp_path / "fit"))
    assert 90 <= overlay.instances[0]["config"]["fontSize"] <= 103


def multi_line_case(composition_case, *, dynamic=True, target="subtitle"):
    """两行字效（声明 lineStartFrames）配一组字幕：第一句三个切片、第二句一个切片，切片之间有空隙。"""
    from .conftest import published_sprite
    sprite = published_sprite()
    sprite.text_prop, sprite.keywords_prop = "text", "keywords"
    schema = json.loads(sprite.config_schema_json)
    schema["properties"]["lineStartFrames"] = {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 3600},
                                               "minItems": 2, "maxItems": 2}
    if dynamic:
        schema["x-imv-dynamic-duration"] = True
    sprite.config_schema_json = json.dumps(schema)
    sprite.default_config_json = json.dumps({**json.loads(sprite.default_config_json), "text": "一\n二", "lineStartFrames": [0, 30]})
    data = case(composition_case, target)
    data["remotion"]["assets"] = {sprite.sprite_id: MessageToDict(sprite, preserving_proto_field_name=True)}
    data["remotion"]["placements"][0]["sprite_id"] = sprite.sprite_id
    segment = lambda index, text, keyword, start, end, group: {"segment_id": index, "text": text, "keyword": keyword, "start_time": start,
                                                              "end_time": end, "level": 1, "group_id": group}
    data["segmentation"]["segments"] = [
        segment(1, "甲乙，", "甲乙", 0, 1, [1, 3]), segment(2, "丙丁，", "丁", 1, 2, [2, 3]),
        segment(3, "戊己。", "", 2.5, 3.5, [3, 3]), segment(4, "庚辛。", "庚", 4, 5, [1, 1]),
    ]
    return data


def test_multi_line_subtitles_fill_lines_from_consecutive_cues_of_one_sentence(composition_case):
    """多行字效：同一句里每两个切片一组，第 i 行在第 i 个切片开始时出现，整组在最后一个切片结束时清空；换句时提前开始新组。"""
    result = remotion.plan(multi_line_case(composition_case), 120)
    assert [(item["start_frame"], item["duration_frames"]) for item in result.instances] == [(0, 60), (75, 30), (120, 30)]
    assert [item["config"]["text"] for item in result.instances] == ["甲乙\n丙丁", "戊己", "庚辛"]
    assert [item["config"]["lineStartFrames"] for item in result.instances] == [[0, 30], [0, 0], [0, 0]]
    assert [item["config"]["keywords"] for item in result.instances] == [["甲乙", "丁"], [], ["庚"]]
    # The fit measures each group once its last line is shown.
    assert [(item["text"], item["reveal_frame"]) for item in result.instances] == [("甲乙\n丙丁", 30), ("戊己", 0), ("庚辛", 0)]


@pytest.mark.parametrize("duration_ms, expected", [
    (8000, [(0, 60, "甲乙\n丙丁", [0, 30]), (75, 30, "戊己", [0, 0]), (120, 30, "庚辛", [0, 0])]),
    # The second line starts after the video ends: it is dropped and the group is one line.
    (900, [(0, 27, "甲乙", [0, 0])]),
    # The second line is cut by the end of the video, which also ends the group.
    (1500, [(0, 45, "甲乙\n丙丁", [0, 30])]),
])
def test_multi_line_text_slot_keeps_only_the_lines_inside_its_interval(composition_case, duration_ms, expected):
    """新字幕槽位按文字对象区间（字幕从 0 到成片结尾）裁剪：区间外开始的行丢弃，被截的组随区间结束。"""
    data = multi_line_case(composition_case)
    data["template"] = deepcopy(composition_case["template"])
    data["duration_ms"] = duration_ms
    data["remotion"]["placements"][0].update(text_target="subtitle", editor={})
    result = remotion.plan(data, 120)
    assert [(item["start_frame"], item["duration_frames"], item["config"]["text"], item["config"]["lineStartFrames"])
            for item in result.instances] == expected


def test_fixed_clock_multi_line_starts_follow_the_preview_clock(composition_case, monkeypatch):
    """固定时钟的资产读到预览时钟：行起点按宿主同样的线性映射换算到预览帧，排版预检仍按实际帧测量。"""
    monkeypatch.setattr(remotion, "retime_source", lambda sprite: "timed source")
    result = remotion.plan(multi_line_case(composition_case, dynamic=False), 120)
    first = result.instances[0]
    # 30 of 60 real frames is round(30 * 89 / 59) of the 90 preview frames.
    assert first["config"]["lineStartFrames"] == [0, 45] and first["reveal_frame"] == 30 and first["code"] == "timed source"


@pytest.mark.parametrize("title, expected", [
    ("业务标题\n保留换行", "业务标题\n保留换行"),
    # Without a line break the whole title is the first line and the second line stays empty.
    ("业务标题", "业务标题"),
    # Blank lines are skipped; lines beyond the effect's two join the last one with a space.
    ("上栏\n\n下栏\n多出", "上栏\n下栏 多出"),
])
def test_multi_line_title_splits_the_title_at_its_line_breaks(composition_case, title, expected):
    """多行字效绑定标题：按标题里的换行分行，所有行同时出现；没有换行时整段放第一行。"""
    data = multi_line_case(composition_case, target="title")
    data["request"] = {**data["request"], "title": title}
    item, = remotion.plan(data, 120).instances
    assert (item["config"]["text"], item["text"]) == (expected, expected)
    assert item["config"]["lineStartFrames"] == [0, 0] and item["reveal_frame"] == 0


def bilingual_multi_line_case(composition_case):
    """中英双语各自绑定同一个双栏字效：中文按句分组两行，英文固定单行，英文内容取自译文。"""
    from .conftest import remotion_track

    data = multi_line_case(composition_case)
    data["template"] = deepcopy(composition_case["template"])
    subtitle = next(track for track in data["template"]["tracks"] if track["target"] == "subtitle")
    data["template"]["tracks"].append({**deepcopy(subtitle), "id": "subtitle-en", "language": "en"})
    placement = data["remotion"]["placements"][0]
    for language in ("zh", "en"):
        data["template"]["tracks"].append(remotion_track(f"effect-{language}", sprite_id=placement["sprite_id"],
                                                         text_target="subtitle", language=language))
    data["remotion"]["placements"] = [{**deepcopy(placement), "id": f"text-{language}", "text_target": "subtitle",
                                       "language": language, "editor": {}} for language in ("zh", "en")]
    data["segmentation"].update(title_en="Business title", title_keyword_en="")
    for item, text in zip(data["segmentation"]["segments"], ["Don't stop!", "Keep going.", "One more.", "Last line."]):
        item.update(text_en=text, keyword_en="")
    return data


def test_english_text_uses_only_the_upper_tier_of_a_two_tier_effect(composition_case):
    """英文标题和字幕固定单行：绑定的双栏字效只用上栏，中英双语因此不会出现中文两栏加英文两栏的四行。"""
    result = remotion.plan(bilingual_multi_line_case(composition_case), 120)
    # 中文仍按句两行一组，英文每个切片各占一行、不注入多行。
    assert [item["config"]["text"] for item in result.instances] == [
        "甲乙\n丙丁", "戊己", "庚辛", "Don't stop", "Keep going", "One more", "Last line"]
    english = result.instances[3:]
    assert [item["config"]["lineStartFrames"] for item in english] == [[0, 0]] * 4
    # 排版预检按单行测量，最后一行出现的帧不再有意义。
    assert all(item["one_line"] and "reveal_frame" not in item for item in english)


def test_english_title_with_a_line_break_stays_on_one_row(composition_case):
    """英文标题在双栏字效里也只占上栏：译文里的换行按空格合并，不把后面的行放进下栏。"""
    data = multi_line_case(composition_case, target="title")
    data["template"] = deepcopy(composition_case["template"])
    title = next(track for track in data["template"]["tracks"] if track["target"] == "title")
    data["template"]["tracks"].append({**deepcopy(title), "id": "title-en", "language": "en"})
    data["remotion"]["placements"][0].update(text_target="title", language="en", editor={})
    data["segmentation"].update(title_en="Lead line\nMain headline", title_keyword_en="")
    item, = remotion.plan(data, 120).instances
    assert (item["config"]["text"], item["text"]) == ("Lead line Main headline", "Lead line Main headline")
    assert item["config"]["lineStartFrames"] == [0, 0]
    assert item["one_line"] is True and "reveal_frame" not in item


def bilingual_sprite_case(composition_case, *, target="subtitle"):
    """双语字效（声明英文文字 `textEn`）绑定中文对象：同一句的中文与译文由同一个字效同时显示。"""
    data = multi_line_case(composition_case, target=target)
    sprite = next(iter(data["remotion"]["assets"].values()))
    schema = json.loads(sprite["config_schema_json"])
    schema["properties"]["textEn"] = {"type": "string", "minLength": 1, "maxLength": 100}
    sprite["config_schema_json"] = json.dumps(schema)
    defaults = {**json.loads(sprite["default_config_json"]), "text": "中文示例", "textEn": "English sample", "lineStartFrames": [0, 0]}
    sprite["default_config_json"] = json.dumps(defaults)
    data["segmentation"].update(title_en="Business title", title_keyword_en="")
    for item, text in zip(data["segmentation"]["segments"], ["Don't stop!", "Keep going.", "One more.", "Last line."]):
        item.update(text_en=text, keyword_en="")
    return data


def test_bilingual_sprite_shows_chinese_and_english_together_per_cue(composition_case):
    """双语字效：每条切片一个实例，上栏中文、下栏同一句的译文同时出现，不按行分组也不要求单行字号。"""
    result = remotion.plan(bilingual_sprite_case(composition_case), 120)
    assert [item["config"]["text"] for item in result.instances] == ["甲乙", "丙丁", "戊己", "庚辛"]
    assert [item["config"]["textEn"] for item in result.instances] == ["Don't stop", "Keep going", "One more", "Last line"]
    assert [item["config"]["lineStartFrames"] for item in result.instances] == [[0, 0]] * 4
    # 两种语言在同一个字效里上下叠放：关键词仍取自中文切片，不缩成单行、不朝任何方向展开。
    assert [item["config"]["keywords"] for item in result.instances] == [["甲乙"], ["丁"], [], ["庚"]]
    assert all("one_line" not in item and "grow" not in item and "reveal_frame" not in item for item in result.instances)


def test_bilingual_sprite_is_not_shown_twice_with_an_english_effect(composition_case):
    """双语字效已显示英文：绑定英文对象的另一个字效不再渲染，否则英文会出现两次。"""
    from .conftest import remotion_track

    data = bilingual_sprite_case(composition_case)
    data["template"] = deepcopy(composition_case["template"])
    subtitle = next(track for track in data["template"]["tracks"] if track["target"] == "subtitle")
    data["template"]["tracks"].append({**deepcopy(subtitle), "id": "subtitle-en", "language": "en"})
    placement = data["remotion"]["placements"][0]
    for language in ("zh", "en"):
        data["template"]["tracks"].append(remotion_track(f"effect-{language}", sprite_id=placement["sprite_id"],
                                                         text_target="subtitle", language=language))
    data["remotion"]["placements"] = [{**deepcopy(placement), "id": f"text-{language}", "text_target": "subtitle",
                                       "language": language, "editor": {}} for language in ("zh", "en")]
    result = remotion.plan(data, 120)
    assert [item["config"]["text"] for item in result.instances] == ["甲乙", "丙丁", "戊己", "庚辛"]


def test_bilingual_sprite_title_puts_the_translation_under_the_title(composition_case):
    """双语字效绑定标题：上栏中文标题、下栏它的英文译文，换行都按空格合并到各自一行。"""
    data = bilingual_sprite_case(composition_case, target="title")
    data["request"] = {**data["request"], "title": "业务标题\n保留换行"}
    data["segmentation"].update(title_en="Business title", title_keyword_en="")
    item, = remotion.plan(data, 120).instances
    assert (item["config"]["text"], item["config"]["textEn"]) == ("业务标题 保留换行", "Business title")
    assert item["config"]["lineStartFrames"] == [0, 0]
    assert "one_line" not in item and "reveal_frame" not in item


def test_bilingual_sprite_fails_without_the_english_translation(composition_case):
    """缺少译文时按内容错误终止，不能悄悄只显示中文。"""
    data = bilingual_sprite_case(composition_case)
    data["segmentation"]["segments"][1]["text_en"] = ""
    with pytest.raises(ContentError):
        remotion.plan(data, 120)


def test_single_line_subtitles_are_still_one_instance_per_cue(composition_case):
    """单行字效不分组：每个切片一个实例，不注入行起点（回归）。"""
    data = multi_line_case(composition_case)
    sprite = next(iter(data["remotion"]["assets"].values()))
    schema = json.loads(sprite["config_schema_json"])
    schema["properties"].pop("lineStartFrames")
    sprite["config_schema_json"] = json.dumps(schema)
    defaults = json.loads(sprite["default_config_json"])
    defaults.pop("lineStartFrames")
    sprite["default_config_json"] = json.dumps(defaults)
    result = remotion.plan(data, 120)
    assert [item["config"]["text"] for item in result.instances] == ["甲乙", "丙丁", "戊己", "庚辛"]
    assert all("lineStartFrames" not in item["config"] and "reveal_frame" not in item for item in result.instances)


def test_multi_line_groups_split_before_the_text_or_line_start_limits(composition_case):
    """同一句的两个切片单独都合规：拼起来超过资产文字上限（40 字），或第二行比组首晚 120 秒以上（行起点上限 3600 帧）时分成两组，不让合成失败。"""
    data = multi_line_case(composition_case)
    long = lambda index, char, start, group: {"segment_id": index, "text": char * 25, "keyword": "", "start_time": start,
                                               "end_time": start + 1, "level": 1, "group_id": group}
    data["segmentation"]["segments"] = [long(1, "甲", 0, [1, 2]), long(2, "乙", 1, [2, 2])]
    assert [item["config"]["text"] for item in remotion.plan(data, 120).instances] == ["甲" * 25, "乙" * 25]
    data = multi_line_case(composition_case)
    data["duration_ms"] = 200_000
    segment = lambda index, text, start, group: {"segment_id": index, "text": text, "keyword": "", "start_time": start,
                                                 "end_time": start + 1, "level": 1, "group_id": group}
    data["segmentation"]["segments"] = [segment(1, "开头", 0, [1, 2]), segment(2, "很久以后", 125, [2, 2])]
    result = remotion.plan(data, 400)
    assert [(item["start_frame"], item["config"]["text"]) for item in result.instances] == [(0, "开头"), (3750, "很久以后")]
