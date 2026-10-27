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


def test_invalid_layout_rejected(client):
    """未知模式在协议入口返回 422，不落库，也不悄悄用旧布局。"""
    response = post_template(client, {"name": "错误布局", "tracks": [template_track("title"), template_track("subtitle")],
                                      "split_screen": {"layout": "picture-in-picture"}})
    assert response.status_code == 422
    assert not client.get("/template").json()["templates"]
