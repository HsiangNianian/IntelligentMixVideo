"""同屏保存、几何、业务时间线及跨布局字幕回归；pytest tests/test_split_screen.py，不访问云服务。"""
from copy import deepcopy
import asyncio
from pathlib import Path
import subprocess

import pytest
from google.protobuf.json_format import ParseDict
from generated.imv.template.v1 import template_pb2 as pb

from server.template.split_screen import SplitScreen, ScreenWindow, screen_rect
from server.template.preview_mask import mask_image
from server.video_composition.timeline import build_timeline
from server.video_composition import split_screen, remotion
from server.remotion_templates.overlay_render import _fit_frames
from .conftest import template_track
from .template_wire import post_template
from .test_video_composition_remotion import case as remotion_case
from .test_video_composition_remotion import REAL_RENDER, RENDER_PATHS


def enable(case):
    """人工媒体尺寸与装饰地址，独立设置两个命中素材，不调用上传。"""
    case["template"]["split_screen"] = SplitScreen(enabled=True).model_dump(mode="json", by_alias=True)
    for match in case["matches"]:
        match.update(matched_candidate_url=f"https://example.test/{match['segment_id']}.mp4", matched_candidate_type="video")
    case["split_assets"] = {"background": "https://example.test/bg.png", "avatar_border": "https://example.test/frame.png",
        "material_borders": {}, "media": {case["request"]["videoUrl"]: [720, 1280], **{match["matched_candidate_url"]: [1920, 1080] for match in case["matches"]}}}
    return case


def test_protobuf_roundtrip_and_legacy_update(client):
    """配置和显式零值完整保存，旧客户端重存保留配置，关闭再开启保留窗口。"""
    layout = SplitScreen(enabled=True, background_color="#224466").model_dump(mode="json")
    layout["avatar"].update(x=0, radius=0, border_width=0)
    payload = {"name": "同屏模板", "tracks": [template_track("title"), template_track("subtitle")], "split_screen": layout}
    response = post_template(client, payload)
    assert response.status_code == 201, response.text
    saved = ParseDict(response.json(), pb.SaveTemplateResponse()).template
    assert saved.split_screen.avatar.HasField("radius") and saved.split_screen.avatar.radius == 0
    assert ParseDict(client.get(f"/template/{saved.template_id}").json(), pb.GetTemplateResponse()).template == saved
    payload.update(template_id=saved.template_id, name="旧客户端重存")
    del payload["split_screen"]
    updated = ParseDict(post_template(client, payload).json(), pb.SaveTemplateResponse()).template
    assert updated.split_screen == saved.split_screen
    payload["split_screen"] = {**layout, "enabled": False}
    off = ParseDict(post_template(client, payload).json(), pb.SaveTemplateResponse()).template.split_screen
    assert not off.enabled and off.avatar == saved.split_screen.avatar


@pytest.mark.parametrize("change", [{"x": 90}, {"width": 0}, {"radius": 10000}, {"border_color": "red"}, {"fit": "Stretch"}])
def test_invalid_window_rejected(client, change):
    """越界和不受支持样式在保存入口拒绝，不写入数据库。"""
    layout = SplitScreen(enabled=True).model_dump(mode="json")
    layout["material"].update(change)
    response = post_template(client, {"name": "无效布局", "tracks": [template_track("title"), template_track("subtitle")], "split_screen": layout})
    assert response.status_code == 422
    assert not ParseDict(client.get("/template").json(), pb.ListTemplatesResponse()).templates


def test_contain_geometry_border_and_mask():
    """横素材完整显示时边框跟随可见画面，透明内外区域不覆盖下面的窗口。"""
    window = ScreenWindow(x=10, y=10, width=80, height=50, radius=24, border_width=6)
    rect = screen_rect(window, [1920, 1080], 1080, 1920)
    assert rect == dict(x=108, y=429, width=864, height=486, radius=24, border=6)
    image = split_screen.border_image(rect, "#FFFFFF")
    assert image.getpixel((0, 0))[3] == 0 and image.getpixel((432, 243))[3] == 0
    assert image.getpixel((432, 2))[3] > 240
    clip = split_screen.window_clip({"Effects": [{"Type": "Background"}, {"Type": "Volume", "Gain": 0}]}, window, [1920, 1080], 1080, 1920)
    assert clip["Effects"][0] == {"Type": "Volume", "Gain": 0}
    assert "radius=24.000000" in clip["Effects"][1]["ExtParams"]


def test_preview_mask_crop_and_endpoint(client, monkeypatch):
    """Cover 的圆角沿实际裁剪边缘生成；公开资源接口只处理几何，无模板数据库副作用。"""
    from server.template import router
    image = mask_image(1920, 1080, 400, 800, 60)
    # 1:2 窗口对横源中心裁剪；角透明、中央及边中不透明，不能只裁源画面的四角。
    assert image.getpixel((690, 0)) < 5
    assert image.getpixel((960, 0)) > 250
    assert image.getpixel((960, 540)) == 255
    calls = []
    def encode(**params):
        """隔离 FFmpeg，记录实际通过边界的参数。"""
        calls.append(params)
        return b"mask-mp4"
    monkeypatch.setattr(router, "mask_video", encode)
    params = dict(width=1920, height=1080, box_width=400, box_height=800, radius=60, seconds=11)
    response = client.get("/template/preview-mask", params=params)
    assert response.status_code == 200 and response.content == b"mask-mp4"
    assert response.headers["content-type"] == "video/mp4" and calls == [params]
    assert client.get("/template/preview-mask", params={**params, "radius": 201}).status_code == 422
    assert len(calls) == 1
    assert not client.get("/template").json()["templates"]


def test_preview_mask_encoder_unavailable(client, monkeypatch):
    """缺少 FFmpeg 明确报 503，不把无法生成的蒙版伪装成成功。"""
    from server.template import router
    def unavailable(**params):
        """模拟部署缺少编码器。"""
        raise FileNotFoundError("ffmpeg")
    monkeypatch.setattr(router, "mask_video", unavailable)
    response = client.get("/template/preview-mask", params=dict(width=100, height=100, box_width=100, box_height=100, radius=10, seconds=11))
    assert response.status_code == 503 and "FFmpeg" in response.json()["detail"]


def test_timeline_windows_sync_gaps_subtitles_and_audio(composition_case):
    """命中同屏、空隙全屏，数字人读取绝对源时间且只保留原配音；同屏字幕使用新位置。"""
    case = enable(composition_case)
    original = deepcopy(case)
    timeline, _ = build_timeline(**case)
    tracks = [track["VideoTrackClips"] for track in timeline["VideoTracks"]]
    assert [clip["Type"] for clip in tracks[0]] == ["Video", "Image", "Video", "Image", "Video"]
    assert [(clip["In"], clip["Out"], clip["TimelineIn"], clip["TimelineOut"]) for clip in tracks[2]] == [(1, 3, 1, 2.9999), (4, 6, 4, 5.9999)]
    assert len(timeline["AudioTracks"]) == 1
    subtitles = timeline["SubtitleTracks"][1]["SubtitleTrackClips"]
    assert [(clip["X"], clip["Y"]) for clip in subtitles] == [(0.5, 0.62)] * 2
    assert all(effect["Type"] != "Background" for clip in tracks[1] + tracks[2] for effect in clip["Effects"])
    assert case == original
    case["segments"][1]["start_time"] = case["matches"][1]["start_time"] = 3
    tracks = build_timeline(**case)[0]["VideoTracks"]
    assert len(tracks[2]["VideoTrackClips"]) == 1
    assert tracks[2]["VideoTrackClips"][0]["Out"] == 6


def test_unmatched_subtitle_and_disabled_layout(composition_case):
    """未命中的字幕保持原位；关闭布局完全回到原时间线。"""
    case = enable(composition_case)
    case["matches"][0]["matched_candidate_url"] = None
    timeline, _ = build_timeline(**case)
    subtitles = timeline["SubtitleTracks"][1]["SubtitleTrackClips"]
    assert [clip["Y"] for clip in subtitles] == [0.82, 0.62]
    case["template"]["split_screen"]["enabled"] = False
    disabled = build_timeline(**case)
    del case["template"]["split_screen"]
    assert disabled == build_timeline(**case)


def test_english_position_and_materials_mode(composition_case):
    """英文字幕独立换位；同一模板用于纯素材时完整忽略窗口和同屏字幕位置。"""
    case = enable(composition_case)
    english = deepcopy(next(track for track in case["template"]["tracks"] if track["target"] == "subtitle"))
    english.update(id="subtitle-en", language="en")
    case["template"]["tracks"].append(english)
    for segment in case["segments"]:
        segment["text_en"] = "Example"
    subtitle = [clip for row in build_timeline(**case)[0]["SubtitleTracks"] for clip in row["SubtitleTrackClips"] if clip.get("AdaptMode") == "AutoScale"]
    assert all(clip["Y"] == 0.655 for clip in subtitle)
    case["request"].update(compositionMode="materials_voice", materials=[{"fileUrl": "https://example.test/material.mp4", "type": "video"}])
    case["material_durations"] = [8]
    with_layout = build_timeline(**case)
    del case["template"]["split_screen"]
    assert with_layout == build_timeline(**case)


@pytest.mark.anyio
@pytest.mark.parametrize("cached", [False, True])
async def test_assembly_recovery_reuses_layout_assets(composition_case, composition_runtime, monkeypatch, cached):
    """首次组装持久化几何资产后提交；恢复时只读取已持久化的尺寸和图片。"""
    from server.video_composition import service, store
    case = enable(composition_case)
    store.initialize_schema()
    record = store.create(case["request"], composition_runtime.settings.output())
    record = store.advance(record, "assembling", template=case["template"], duration_ms=8000,
        segmentation={"segments": case["segments"]}, matches=case["matches"], **({"split_assets": case["split_assets"]} if cached else {}))
    calls = []
    async def prepare(*args):
        """首次返回人工布局资产，恢复不应重复探测或上传。"""
        calls.append(args)
        return case["split_assets"]
    monkeypatch.setattr(split_screen, "prepare", prepare)
    async def storage_location(*args):
        """隔离 IMS 存储查询，不访问实际云端。"""
        return "test-bucket.oss-cn-shanghai.aliyuncs.com"
    monkeypatch.setattr(service.ims.IMS, "storage_location", storage_location)
    job = service.Job(composition_runtime, record, composition_runtime.settings)
    await job.assemble()
    saved = store.get(record["task_id"])
    assert saved["stage"] == "submitting"
    assert len(saved["data"]["timeline"]["VideoTracks"]) == 4
    assert saved["data"]["split_assets"] == case["split_assets"]
    assert len(calls) == (0 if cached else 1)


def test_remotion_crossing_keeps_original_clock(composition_case, monkeypatch):
    """跨界字幕保持内容和动画长度，显示区间互斥，排版只检查该位置真正可见的帧。"""
    case = enable(composition_case)
    data = remotion_case(case)
    data.update(template=case["template"], matches=case["matches"])
    monkeypatch.setattr(remotion, "subtitle_events", lambda *args: [("跨界字幕", "", 0, 8)])
    result = remotion.plan(data, 120)
    assert [(i["visible_start_frame"], i["visible_end_frame"]) for i in result.instances] == [(0, 30), (30, 90), (90, 120), (120, 180), (180, 240)]
    assert {(i["start_frame"], i["duration_frames"]) for i in result.instances} == {(0, 240)}
    assert all(i["config"] == result.instances[0]["config"] for i in result.instances)
    assert result.instances[0]["host"] == result.instances[2]["host"]
    assert result.instances[1]["host"] == result.instances[3]["host"] != result.instances[0]["host"]
    frames, length = _fit_frames(result.instances[1], 240)
    assert (min(frames), max(frames), length) == (30, 89, 240)


@pytest.mark.anyio
async def test_prepare_reads_distinct_media_and_ignores_no_matches(composition_case, monkeypatch):
    """同一素材只探测一次，旋转和非方形像素计入比例，无命中不上传。"""
    case = enable(composition_case)
    data = {"request": case["request"], "template": case["template"], "matches": case["matches"], "output": {"width": 1080, "height": 1920}}
    calls = []
    async def probe(url, *args):
        """返回旋转的人工元数据。"""
        calls.append(url)
        return {"streams": [{"width": 200, "height": 100, "sample_aspect_ratio": "2:1", "side_data_list": [{"rotation": 90}]}]}
    monkeypatch.setattr(split_screen, "ffprobe_json", probe)
    monkeypatch.setattr(split_screen, "_images", lambda task, data, layout, urls, avatar, sizes, timeout: sizes)
    sizes = await split_screen.prepare("job", data, 30)
    assert len(calls) == 3 and all(value == [100, 400] for value in sizes.values())
    data["matches"] = []
    assert await split_screen.prepare("job", data, 30) is None
    assert len(calls) == 3


def test_split_disables_both_transition_engines(composition_case):
    """同屏关闭 IMS 与 Remotion 转场；纯素材模式仍保留原有转场摆放。"""
    case = enable(composition_case)
    track = template_track("transition", transition="transition/normal/directional")
    track["duration"] = 1
    track["transition_boundary"] = 1
    track["editor"]["transition"] = "transition/normal/directional"
    case["template"]["tracks"].append(track)
    timeline, _ = build_timeline(**case)
    assert not any(effect["Type"] in {"Transition", "DLTransition"} for row in timeline["VideoTracks"] for clip in row["VideoTrackClips"] for effect in clip.get("Effects", []))
    data = {"request": case["request"], "template": case["template"], "remotion": {"placements": [{"id": "transition", "transition_boundary": 1}, {"id": "text"}]}}
    assert [item["id"] for item in remotion.active_placements(data)] == ["text"]
    data["request"]["compositionMode"] = "materials_voice"
    assert len(remotion.active_placements(data)) == 2


@pytest.mark.skipif(not REAL_RENDER, reason="set IMV_TEST_RENDERER=1 for split subtitle clock integration")
def test_real_visible_intervals_preserve_animation_clock(tmp_path):
    """实际 VP9 Alpha 帧证明换位置后保持第 15 帧时钟，旧位置消失，不重播动画。"""
    from server.remotion_templates.overlay_render import Overlay, render_overlay
    from server.remotion_templates.renderer import Renderer
    from server.remotion_templates.settings import Settings

    code = """import React from 'react'; import {useCurrentFrame} from 'remotion';
export default function Mark() { const frame = useCurrentFrame();
 return <div style={{width:20,height:20,background:frame < 15 ? '#ff0000' : '#0000ff'}}/>; }"""
    base = dict(code=code, config={}, width=100, height=100, start_frame=0, duration_frames=30)
    instances = [{**base, "visible_start_frame": start, "visible_end_frame": end,
        "host": {"box": {"x": 0, "y": 0, "width": 100, "height": 100}, "transform": {"x": center, "y": center}}}
        for start, end, center in [(0, 15, 50), (15, 30, 70)]]
    configured = {field: Path(RENDER_PATHS[key]) for field, key in [("browser_executable", "IMV_BROWSER_EXECUTABLE"), ("font_regular", "IMV_FONT_REGULAR"), ("font_bold", "IMV_FONT_BOLD")] if RENDER_PATHS[key]}
    renderer = Renderer(Settings(_env_file=None, data_dir=tmp_path / "state", **configured))
    video = asyncio.run(render_overlay(renderer, Overlay(100, 100, 30, 30, instances), tmp_path / "render", frame_concurrency=1))
    for frame, x, channel in [(14, 10, 0), (15, 30, 2), (29, 30, 2)]:
        pixels = subprocess.run(["ffmpeg", "-v", "error", "-c:v", "libvpx-vp9", "-i", str(video), "-vf", f"select=eq(n\\,{frame})", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "-"], check=True, capture_output=True).stdout
        pixel = pixels[(x * 100 + x) * 4:][:4]
        assert pixel[channel] > 240 and pixel[3] > 240
        old = 30 if frame == 14 else 10
        assert pixels[(old * 100 + old) * 4 + 3] < 5
