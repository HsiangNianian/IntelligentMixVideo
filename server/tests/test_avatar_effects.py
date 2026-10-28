"""圆形开场、IMS 抠像与模板契约回归；pytest tests/test_avatar_effects.py，隔离外部服务。"""
import asyncio
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image
from google.protobuf.json_format import ParseDict
from generated.imv.template.v1 import template_pb2 as pb

from server.template.avatar import AvatarEffects
from server.video_composition import avatar, split_screen, remotion
from server.video_composition.errors import CompositionError
from server.video_composition.timeline import build_timeline
from .conftest import template_track
from .template_wire import post_template, detail_template
from .test_split_screen import enable
from .test_video_composition_remotion import case as remotion_case


def opening(case, seconds=2):
    """模板保存样式，任务保存本条视频的检测结果，不访问视频或云服务。"""
    case["template"]["avatar_effects"] = AvatarEffects(opening_enabled=True, remove_background=True).model_dump(mode="json", by_alias=True)
    case["avatar_opening"] = {"duration": seconds, "x": 0.5, "y": 0.3, "radius": 0.25, "aspect": 720 / 1280, "background": "https://example.test/black.png"}
    return case


def test_saved_effects_roundtrip_and_old_client_update(client):
    """零羽化、关闭开关和另存保留；旧客户端仍携带同屏配置时也不能清掉新增配置。"""
    config = AvatarEffects(opening_enabled=True, opening_duration=3, feather=0, remove_background=True).model_dump(mode="json")
    payload = {"name": "自动数字人", "tracks": [template_track("title"), template_track("subtitle")],
               "split_screen": {"enabled": True}, "avatar_effects": config}
    response = post_template(client, payload)
    assert response.status_code == 201, response.text
    saved = ParseDict(response.json(), pb.SaveTemplateResponse()).template
    assert saved.avatar_effects.HasField("feather") and saved.avatar_effects.feather == 0
    assert ParseDict(client.get(f"/template/{saved.template_id}").json(), pb.GetTemplateResponse()).template == saved
    payload.update(template_id=saved.template_id, name="旧客户端重存")
    del payload["avatar_effects"]
    legacy = ParseDict(post_template(client, payload).json(), pb.SaveTemplateResponse()).template
    assert legacy.avatar_effects == saved.avatar_effects
    payload["avatar_effects"] = {**config, "opening_enabled": False, "remove_background": False}
    disabled = ParseDict(post_template(client, payload).json(), pb.SaveTemplateResponse()).template.avatar_effects
    assert disabled.HasField("opening_enabled") and not disabled.opening_enabled and not disabled.remove_background
    assert disabled.opening_duration == 3 and disabled.feather == 0


@pytest.mark.parametrize("change", [{"opening_duration": 0}, {"opening_duration": 301}, {"circle_scale": 0.5}, {"feather": -1}])
def test_invalid_effects_rejected_without_saving(client, change):
    """无效配置在保存入口返回 422，数据库不产生模板。"""
    response = post_template(client, {"name": "无效开场", "tracks": [template_track("title"), template_track("subtitle")], "avatar_effects": change})
    assert response.status_code == 422
    assert not client.get("/template").json()["templates"]


@pytest.mark.parametrize("same_screen", [False, True])
def test_opening_covers_materials_without_retiming(composition_case, same_screen):
    """两种模式均覆盖前段素材；原轨和转场边界不变，素材保持源进度，音频不重启。"""
    case = enable(composition_case)
    case["template"]["split_screen"]["enabled"] = same_screen
    before = build_timeline(**case)[0]
    opening(case)
    original = deepcopy(case)
    timeline, _ = build_timeline(**case)
    assert timeline["VideoTracks"][0] == before["VideoTracks"][0]
    assert timeline["AudioTracks"] == before["AudioTracks"]
    black, circle = [row["VideoTrackClips"][0] for row in timeline["VideoTracks"][-2:]]
    assert (black["TimelineIn"], black["TimelineOut"], black["Width"], black["Height"]) == (0, 1.9999, 1080, 1920)
    assert (circle["In"], circle["Out"], circle["TimelineIn"], circle["TimelineOut"]) == (0, 2, 0, 1.9999)
    assert [effect["Type"] for effect in circle["Effects"]] == ["Volume", "VFX"]
    assert circle["Effects"][1]["SubType"] == "mask_circle"
    assert "width=0.50000000,height=0.28125000" in circle["Effects"][1]["ExtParams"]
    assert case == original


def test_matting_only_on_visible_avatar_and_keeps_material_decoration(composition_case):
    """开场结束后的同屏才抠像，头像边框和圆角不再发送，素材装饰保留。"""
    case = opening(enable(composition_case), 4.5)
    case["template"]["split_screen"]["material"].update(radius=20, borderWidth=5)
    for match in case["matches"]:
        case["split_assets"]["material_borders"][match["matched_candidate_url"]] = "https://example.test/material-frame.png"
    timeline, _ = build_timeline(**case)
    all_clips = [clip for row in timeline["VideoTracks"] for clip in row["VideoTrackClips"]]
    cutouts = [clip for clip in all_clips if {"Type": "AI_RealMatting"} in clip.get("Effects", [])]
    assert len(cutouts) == 1
    assert (cutouts[0]["TimelineIn"], cutouts[0]["In"], cutouts[0]["Out"]) == (4.5, 4.5, 6)
    assert cutouts[0]["Effects"] == [{"Type": "Volume", "Gain": 0}, {"Type": "AI_RealMatting"}]
    assert not any(clip["MediaURL"] == case["split_assets"]["avatar_border"] for clip in all_clips)
    assert any(clip["MediaURL"].endswith("material-frame.png") for clip in all_clips)
    assert any(effect.get("SubType") == "mask_rec" for clip in all_clips for effect in clip.get("Effects", []))


@pytest.mark.parametrize("fps", [25, 30])
@pytest.mark.parametrize("mode", ["split", "overlay", "circle"])
@pytest.mark.parametrize("opening_seconds", [2, 8])
def test_overlay_boundaries_keep_source_times_and_full_length_tail(composition_case, fps, mode, opening_seconds):
    """各布局在中间边界少显示切换帧，片尾仍完整；开场覆盖整片时也不缩短尾帧或源区间。"""
    case = opening(enable(composition_case), opening_seconds)
    case["fps"] = fps
    case["template"]["split_screen"]["layout"] = mode
    case["template"]["avatar_effects"]["removeBackground"] = False
    case["segments"][-1]["end_time"] = case["matches"][-1]["end_time"] = 8
    for match in case["matches"]:
        case["split_assets"]["material_borders"][match["matched_candidate_url"]] = "https://example.test/material-frame.png"
    snapshot = deepcopy(case)
    timeline, _ = build_timeline(**case)
    main = timeline["VideoTracks"][0]["VideoTrackClips"]
    assert [(clip["TimelineIn"], clip["TimelineOut"]) for clip in main] == [(0, 1), (1, 3), (3, 4), (4, 8)]
    for row in timeline["VideoTracks"][1:]:
        for clip in row["VideoTrackClips"]:
            end = clip["TimelineIn"] + (clip["Out"] - clip["In"] if clip["Type"] == "Video" else clip["Duration"])
            assert end in (2, 3, 8)
            assert clip["TimelineOut"] == (end if end == 8 else end - 0.0001)
            assert (end * fps - 1) / fps <= clip["TimelineOut"]
            if clip["Type"] == "Video" and clip["MediaURL"] == case["request"]["videoUrl"]:
                assert clip["In"] == clip["TimelineIn"]
    assert timeline["AudioTracks"][0]["AudioTrackClips"][0]["Out"] == 8
    assert case == snapshot


def test_no_face_and_pure_materials_skip_opening(composition_case):
    """无脸从 0 秒按原规则显示；纯素材模式不受开场与抠像配置影响。"""
    case = opening(enable(composition_case))
    case["avatar_opening"] = None
    case["template"]["avatar_effects"]["removeBackground"] = False
    timeline = build_timeline(**case)
    case["template"]["avatar_effects"]["openingEnabled"] = False
    assert timeline == build_timeline(**case)
    opening(case)
    case["request"].update(compositionMode="materials_voice", materials=[{"fileUrl": "https://example.test/material.mp4", "type": "video"}])
    case["material_durations"] = [8]
    pure = build_timeline(**case)
    del case["template"]["avatar_effects"]
    assert pure == build_timeline(**case)


def test_bilingual_native_subtitles_switch_after_opening(composition_case):
    """跨开场中英文字幕由普通位置切到同屏位置；新增边界不再触发入出场。"""
    case = opening(enable(composition_case))
    chinese = next(track for track in case["template"]["tracks"] if track["target"] == "subtitle")
    chinese["editor"]["subtitleIn"] = "in/fade_in"
    english = deepcopy(chinese)
    english.update(id="english", language="en")
    case["template"]["tracks"].append(english)
    for item in case["segments"]:
        item["text_en"] = "Example"
    rows = build_timeline(**case)[0]["SubtitleTracks"]
    for row in [rows[1], rows[2]]:
        first, second = row["SubtitleTrackClips"][:2]
        assert (first["TimelineIn"], first["TimelineOut"], second["TimelineIn"], second["TimelineOut"]) == (1, 1.9999, 2, 2.9999)
        assert first["Y"] == 0.82
        assert second["Y"] == (0.655 if second.get("AdaptMode") == "AutoScale" else 0.62)
        assert first["AaiMotionInEffect"] == "fade_in" and "AaiMotionInEffect" not in second


def test_remotion_crossing_opening_preserves_complete_clock(composition_case, monkeypatch):
    """多行/动画字幕仅改可见区间和宿主位置；内容、起点和完整长度保持一致。"""
    case = opening(enable(composition_case))
    data = remotion_case(case)
    data.update(template=case["template"], matches=case["matches"], avatar_opening=case["avatar_opening"])
    monkeypatch.setattr(remotion, "subtitle_events", lambda *args: [("跨开场字幕", "", 0, 8)])
    result = remotion.plan(data, 120)
    assert [(item["visible_start_frame"], item["visible_end_frame"]) for item in result.instances] == [(0, 60), (60, 90), (90, 120), (120, 180), (180, 240)]
    assert {(item["start_frame"], item["duration_frames"]) for item in result.instances} == {(0, 240)}
    assert all(item["config"] == result.instances[0]["config"] for item in result.instances)
    assert result.instances[0]["host"] != result.instances[1]["host"]


def test_locate_model_no_face_and_corrupt_video():
    """随包真实模型可加载，纯色帧是无脸，损坏帧不能被误当成无脸。"""
    content = BytesIO()
    Image.new("RGB", (320, 240), "black").save(content, format="JPEG")
    assert avatar.locate(content.getvalue(), AvatarEffects()) is None
    with pytest.raises(ValueError):
        avatar.locate(b"broken", AvatarEffects())


@pytest.mark.parametrize("scale", [1.6, 3])
def test_locate_combines_frames_and_keeps_circle_inside_source(monkeypatch, scale):
    """不同帧脸框合成一个固定圆；大留量仍限制在画面内，比例宽高不会被 IMS 误当像素。"""
    import cv2
    import numpy as np
    content = BytesIO()
    Image.new("RGB", (320, 240), "black").save(content, format="JPEG")
    faces = iter([np.array([[40, 70, 60, 100, 0.9]]), np.array([[52, 72, 60, 100, 0.9]])])
    detector = SimpleNamespace(setInputSize=lambda size: None, detect=lambda frame: (None, next(faces)))
    monkeypatch.setattr(cv2, "FaceDetectorYN", SimpleNamespace(create=lambda *args: detector))
    result = avatar.locate(content.getvalue() * 2, AvatarEffects(circle_scale=scale))
    r = min(102 * scale / 2, 120)
    assert result["radius"] == pytest.approx(r / 320)
    assert result["x"] == pytest.approx(r / 320)
    assert result["y"] == pytest.approx(max(109, r) / 240)
    assert result["radius"] * 2 <= 1 and result["radius"] * 2 * result["aspect"] <= 1


@pytest.mark.anyio
@pytest.mark.parametrize("opening_enabled", [True, False])
@pytest.mark.parametrize("result, duration_ms, expected", [(None, 8000, 3.0),
    ({"x": 0.5, "y": 0.3, "radius": 0.2, "aspect": 0.5625}, 1000, 1.0)])
async def test_prepare_saves_explicit_no_face_or_circle(composition_case, monkeypatch, result, duration_ms, expected, opening_enabled):
    """检测只读取开场并按帧对齐；无脸不上传，成功保存定位和黑底。"""
    case = opening(composition_case)
    case["template"]["avatar_effects"]["openingDuration"] = 3.013
    case["template"]["avatar_effects"]["openingEnabled"] = opening_enabled
    commands, uploads = [], []
    async def spawn(*command, **kwargs):
        """以有界 JPEG 输出代替真实网络解码。"""
        commands.append(command)
        async def communicate():
            return b"jpeg", b""
        return SimpleNamespace(returncode=0, communicate=communicate)
    def upload(files, settings, timeout):
        """检查上传实际黑色图片，不访问对象存储。"""
        assert Image.open(files[0][0]).getpixel((0, 0)) == (0, 0, 0)
        uploads.append(files)
        return ["https://example.test/black.png"]
    monkeypatch.setattr(avatar.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(avatar, "locate", lambda *args: result)
    monkeypatch.setattr(avatar, "upload_public", upload)
    monkeypatch.setattr(avatar, "ZosSettings", lambda: None)
    data = {"request": case["request"], "template": case["template"], "duration_ms": duration_ms, "output": {"fps": 30}}
    actual = await avatar.prepare("job", data, 30)
    assert float(commands[0][commands[0].index("-t") + 1]) == expected
    if result is None:
        assert actual is None and not uploads
    elif not opening_enabled:
        assert actual == {**result, "duration": 0} and not uploads
    else:
        assert actual == {**result, "duration": expected, "background": "https://example.test/black.png"}
        assert len(uploads) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("cancel", [False, True])
async def test_prepare_timeout_and_cancel_reap_ffmpeg(composition_case, monkeypatch, cancel):
    """网络卡住或任务取消时终止 FFmpeg；超时明确报错，不能退化为无脸。"""
    case = opening(composition_case)
    started, killed = asyncio.Event(), []
    async def communicate():
        started.set()
        await asyncio.Event().wait()
    async def wait():
        return 0
    async def spawn(*args, **kwargs):
        return SimpleNamespace(returncode=None, communicate=communicate, wait=wait, kill=lambda: killed.append(True))
    monkeypatch.setattr(avatar.asyncio, "create_subprocess_exec", spawn)
    data = {"request": case["request"], "template": case["template"], "duration_ms": 8000, "output": {"fps": 30}}
    task = asyncio.create_task(avatar.prepare("job", data, 30 if cancel else 0.01))
    await started.wait()
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else CompositionError):
        await task
    assert killed == [True]


@pytest.mark.anyio
@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("detected", [False, True])
async def test_assembly_reuses_detected_or_no_face_result(composition_case, composition_runtime, monkeypatch, cached, detected):
    """检测结果与明确无脸均落库；恢复不再解码、检测或上传，提交使用同一结果。"""
    from server.video_composition import service, store
    case = opening(composition_case)
    result = case["avatar_opening"] if detected else None
    store.initialize_schema()
    record = store.create(case["request"], composition_runtime.settings.output())
    record = store.advance(record, "assembling", template=case["template"], duration_ms=8000,
        segmentation={"segments": case["segments"]}, matches=case["matches"], **({"avatar_opening": result} if cached else {}))
    calls = []
    async def prepare(*args):
        calls.append(args)
        return result
    async def storage_location(*args):
        return "test-bucket.oss-cn-shanghai.aliyuncs.com"
    monkeypatch.setattr(avatar, "prepare", prepare)
    monkeypatch.setattr(service.ims.IMS, "storage_location", storage_location)
    await service.Job(composition_runtime, record, composition_runtime.settings).assemble()
    saved = store.get(record["task_id"])
    assert saved["stage"] == "submitting" and saved["data"]["avatar_opening"] == result
    assert len(calls) == (0 if cached else 1)
    assert len(saved["data"]["timeline"]["VideoTracks"]) == (3 if detected else 1)


@pytest.mark.anyio
async def test_unsupported_matting_region_fails_before_upload(composition_case, composition_runtime, monkeypatch):
    """实际需要抠像而地域不支持时明确失败，不上传布局或默默保留背景。"""
    from server.video_composition import service, store
    case = opening(enable(composition_case))
    store.initialize_schema()
    record = store.create(case["request"], {**composition_runtime.settings.output(), "region_id": "cn-shenzhen"})
    record = store.advance(record, "assembling", template=case["template"], duration_ms=8000, avatar_opening=None,
        segmentation={"segments": case["segments"]}, matches=case["matches"])
    async def unexpected(*args):
        pytest.fail("不支持的地域不应上传或提交")
    monkeypatch.setattr(split_screen, "prepare", unexpected)
    with pytest.raises(CompositionError, match="北京、上海、杭州"):
        await service.Job(composition_runtime, record, composition_runtime.settings).assemble()


@pytest.mark.parametrize("mode", ["split", "overlay", "circle"])
def test_saved_layout_drives_composition_and_legacy_update(client, composition_case, mode):
    """真实协议保存、读回再构建 Timeline；旧客户端不认识 layout 时保留，显式切回 split 生效。"""
    case = opening(enable(composition_case))
    layout = {**case["template"]["split_screen"], "layout": mode}
    payload = {"name": "布局往返", "tracks": [template_track("title"), template_track("subtitle")],
               "split_screen": layout, "avatar_effects": case["template"]["avatar_effects"]}
    response = post_template(client, payload)
    assert response.status_code == 201, response.text
    saved = ParseDict(response.json(), pb.SaveTemplateResponse()).template
    case["template"] = detail_template(client.get(f"/template/{saved.template_id}"))
    timeline, _ = build_timeline(**case)
    main = timeline["VideoTracks"][0]["VideoTrackClips"]
    assert main[1]["Type"] == ("Image" if mode == "split" else "Video")
    if mode != "split":
        assert main[1]["AdaptMode"] == "Contain"
        assert main[1]["Effects"][0] == {"Type": "Background", "SubType": "Blur", "Radius": 0.1}
        assert (main[1]["Width"], main[1]["Height"], main[1]["In"]) == (1080, 1920, 0)
    effects = [e for row in timeline["VideoTracks"] for clip in row["VideoTrackClips"] for e in clip.get("Effects", [])]
    assert any(e["Type"] == "AI_RealMatting" for e in effects) == (mode != "circle")
    payload.update(template_id=saved.template_id)
    del payload["split_screen"]["layout"]
    legacy = ParseDict(post_template(client, payload).json(), pb.SaveTemplateResponse()).template
    assert legacy.split_screen.layout == mode
    payload["split_screen"]["layout"] = "split"
    assert ParseDict(post_template(client, payload).json(), pb.SaveTemplateResponse()).template.split_screen.layout == "split"


def test_invalid_layout_rejected(client):
    """未知模式在协议入口返回 422，不落库，也不悄悄用旧布局。"""
    response = post_template(client, {"name": "错误布局", "tracks": [template_track("title"), template_track("subtitle")],
                                      "split_screen": {"layout": "picture-in-picture"}})
    assert response.status_code == 422
    assert not client.get("/template").json()["templates"]


@pytest.mark.parametrize("aspect", [720 / 1280, 1920 / 1080])
def test_circle_without_opening_preserves_time_and_round_shape(composition_case, aspect):
    """头像独立于开场；按真实源宽高比截头部，目标保持正圆，无素材区间使用完整原视频。"""
    case = opening(enable(composition_case), 0)
    case["template"]["split_screen"]["layout"] = "circle"
    case["template"]["avatar_effects"]["openingEnabled"] = False
    case["avatar_opening"].update(aspect=aspect, y=0.5)
    timeline, _ = build_timeline(**case)
    main, avatars = [row["VideoTrackClips"] for row in timeline["VideoTracks"]]
    assert [(c["TimelineIn"], c["TimelineOut"], c["In"], c["Out"]) for c in avatars] == [(1, 2.9999, 1, 3), (4, 5.9999, 4, 6)]
    assert all(c["Width"] == c["Height"] for c in avatars)
    effects = avatars[0]["Effects"]
    assert [e["Type"] for e in effects] == ["Volume", "Crop", "VFX"]
    assert effects[1]["Width"] * 1920 == pytest.approx(effects[1]["Height"] * 1920 / aspect)
    assert effects[2]["SubType"] == "mask_circle"
    assert "width=1.0,height=1.0" in effects[2]["ExtParams"]
    assert all(c["MediaURL"] == case["request"]["videoUrl"] and c["AdaptMode"] == "Contain" and c["In"] == c["TimelineIn"] for c in main[::2])
    assert not any(e["Type"] == "AI_RealMatting" for c in main + avatars for e in c["Effects"])


def test_circle_no_face_falls_back_to_direct_switch_and_normal_subtitles(composition_case, monkeypatch):
    """明确无脸时有素材只显示素材、无素材显示原数字人；两类字幕均回普通位置。"""
    case = opening(enable(composition_case))
    case["template"]["split_screen"]["layout"] = "circle"
    case["avatar_opening"] = None
    timeline, _ = build_timeline(**case)
    assert len(timeline["VideoTracks"]) == 1
    main = timeline["VideoTracks"][0]["VideoTrackClips"]
    assert [(c["MediaURL"], c["In"], c["Out"]) for c in main[::2]] == [(case["request"]["videoUrl"], 0, 1), (case["request"]["videoUrl"], 3, 4), (case["request"]["videoUrl"], 6, 8)]
    assert [c["MediaURL"] for c in main[1::2]] == [m["matched_candidate_url"] for m in case["matches"]]
    assert all(c["Y"] == 0.82 for c in timeline["SubtitleTracks"][1]["SubtitleTrackClips"])
    data = remotion_case(case)
    data.update(template=case["template"], matches=case["matches"], avatar_opening=None)
    monkeypatch.setattr(remotion, "subtitle_events", lambda *args: [("完整字幕", "", 0, 8)])
    result = remotion.plan(data, 120)
    assert len(result.instances) == 1
    data["template"]["split_screen"]["enabled"] = False
    assert result.instances == remotion.plan(data, 120).instances


def test_circle_filling_source_uses_valid_crop_ratios(composition_case):
    """最大圆形留量可占满源宽；Crop 比例值不能传保留值 1。"""
    case = opening(enable(composition_case), 0)
    case["template"]["avatar_effects"]["openingEnabled"] = False
    case["template"]["split_screen"]["layout"] = "circle"
    case["avatar_opening"].update(x=0.5, y=0.5, radius=0.5, aspect=1)
    crop = build_timeline(**case)[0]["VideoTracks"][1]["VideoTrackClips"][0]["Effects"][1]
    assert crop == {"Type": "Crop", "X": 0, "Y": 0, "Width": 0.9999, "Height": 0.9999}


@pytest.mark.anyio
@pytest.mark.parametrize("detected", [False, True])
async def test_circle_assembly_detects_without_opening_and_skips_matting_region(composition_case, composition_runtime, monkeypatch, detected):
    """只开头像也检测一次并持久化；圆内背景保留，不受旧抠像开关及抠像地域限制影响。"""
    from server.video_composition import service, store
    case = opening(enable(composition_case), 0)
    case["template"]["split_screen"]["layout"] = "circle"
    case["template"]["avatar_effects"]["openingEnabled"] = False
    result = case["avatar_opening"] if detected else None
    store.initialize_schema()
    record = store.create(case["request"], {**composition_runtime.settings.output(), "region_id": "cn-shenzhen"})
    record = store.advance(record, "assembling", template=case["template"], duration_ms=8000,
                           segmentation={"segments": case["segments"]}, matches=case["matches"])
    calls = []
    async def prepare(*args):
        """模拟一次检测，不读网络。"""
        calls.append(True)
        return result
    async def assets(*args):
        return case["split_assets"] if detected else None
    async def storage_location(*args):
        return "test-bucket.oss-cn-shanghai.aliyuncs.com"
    monkeypatch.setattr(avatar, "prepare", prepare)
    monkeypatch.setattr(split_screen, "prepare", assets)
    monkeypatch.setattr(service.ims.IMS, "storage_location", storage_location)
    await service.Job(composition_runtime, record, composition_runtime.settings).assemble()
    saved = store.get(record["task_id"])
    assert calls == [True] and saved["data"]["avatar_opening"] == result
    assert len(saved["data"]["timeline"]["VideoTracks"]) == (2 if detected else 1)
