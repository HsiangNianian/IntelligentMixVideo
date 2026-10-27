"""上下蒙版的协议、PNG 像素与 IMS 层级回归；运行 uv run --locked pytest tests/test_frame_mask.py。"""

from copy import deepcopy
import json
from pathlib import Path

from PIL import Image
import pytest

from server.video_composition import frame_mask, remotion
from server.video_composition.timeline import build_timeline
from .template_wire import post_template, saved_template, detail_template

CASES = json.loads(Path(__file__).with_name("frame_mask_cases.json").read_text())


@pytest.fixture
def mask():
    """两栏使用不同颜色，能够识别错位和上下颠倒。"""
    return {"top": {"enabled": True, "height": 18.4, "color": "#112233"},
            "bottom": {"enabled": True, "height": 16.6, "color": "#445566"}}


def test_mask_roundtrip_and_legacy_update(client, template_payload, mask):
    """保存、重读与旧客户端省略字段的更新保留蒙版；显式关闭不丢样式。"""
    created = post_template(client, {**template_payload, "frame_mask": mask})
    assert created.status_code == 201, created.text
    record = saved_template(created)
    assert record["frame_mask"] == mask
    template_id = record["template_id"]
    assert detail_template(client.get(f"/template/{template_id}"))["frame_mask"] == mask
    updated = post_template(client, {**template_payload, "template_id": template_id, "name": "旧客户端修改名称"})
    assert updated.status_code == 200, updated.text
    assert saved_template(updated)["frame_mask"] == mask
    mask["top"]["enabled"] = mask["bottom"]["enabled"] = False
    disabled = post_template(client, {**template_payload, "template_id": template_id, "frame_mask": mask})
    assert saved_template(disabled)["frame_mask"] == mask


@pytest.mark.parametrize("field,value", [("height", -1), ("height", 51), ("height", "NaN"), ("height", "Infinity"), ("color", "red"), ("color", "#12345600")])
def test_invalid_mask_rejected(client, template_payload, mask, field, value):
    """非法颜色与高度在保存边界拒绝，不留下模板。"""
    mask["top"][field] = value
    assert post_template(client, {**template_payload, "frame_mask": mask}).status_code == 422
    assert client.get("/template").json()["templates"] == []


@pytest.mark.parametrize("case", CASES)
def test_png_matches_shared_pixel_cases(monkeypatch, mask, case):
    """与客户端共享尺寸案例，逐行检查栏高、色值与透明中部，并检查上传路径。"""
    mask["top"]["height"], mask["bottom"]["height"] = case["top"], case["bottom"]
    images = []

    def upload(files, settings, timeout):
        """截获真正生成的 PNG，不访问对象存储。"""
        path, key, mime = files[0]
        assert key == "imv/playground/task-mask.png" and mime == "image/png"
        with Image.open(path) as image:
            images.append(image.copy())
        return ["https://media.test/mask.png"]

    monkeypatch.setattr(frame_mask, "upload_public", upload)
    result = frame_mask.upload_mask(mask, 80, case["height"], "task", None, 1, "imv/playground")
    if not case["expected"]:
        assert result is None and not images
        return
    assert result == "https://media.test/mask.png"
    image = images[0]
    for y in range(case["height"]):
        expected = (0, 0, 0, 0)
        for start, size in case["expected"]:
            if start <= y < start + size:
                expected = (17, 34, 51, 255) if start == 0 else (68, 85, 102, 255)
        assert image.getpixel((0, y)) == image.getpixel((79, y)) == expected


def test_disabled_mask_does_not_upload(monkeypatch, mask):
    """两栏关闭后不调用存储，仍保留原高度和颜色。"""
    mask["top"]["enabled"] = mask["bottom"]["enabled"] = False
    monkeypatch.setattr(frame_mask, "upload_public", lambda *args: pytest.fail("关闭蒙版不应上传"))
    assert frame_mask.upload_mask(mask, 1080, 1920, "task", None, 1, "imv/video_composition") is None


@pytest.mark.parametrize("mode", ["standard", "materials_voice", "materials_silent"])
def test_mask_preserves_video_and_text_for_all_modes(composition_case, mask, mode):
    """所有模式整段覆盖；视频和文字完全不变，Remotion 仍在蒙版上面。"""
    case = composition_case
    if mode != "standard":
        case["request"].update(compositionMode=mode, text="有声字幕" if mode == "materials_voice" else None,
                               audioUrl=case["request"]["audioUrl"] if mode == "materials_voice" else None,
                               materials=[{"fileUrl": "https://media.test/a.mp4", "type": "video"}], processRules={"videoDuration": 8})
        case["material_durations"] = [8]
    baseline, _ = build_timeline(**case)
    case["template"]["frame_mask"] = mask
    original = deepcopy(case)
    timeline, _ = build_timeline(**case, frame_mask_url="https://media.test/mask.png")
    assert case == original
    assert timeline["VideoTracks"][0] == baseline["VideoTracks"][0]
    assert timeline["SubtitleTracks"] == baseline["SubtitleTracks"]
    clip = timeline["VideoTracks"][1]["VideoTrackClips"][0]
    assert clip == {
        "Type": "Image", "MediaURL": "https://media.test/mask.png", "TimelineIn": 0, "TimelineOut": 8,
        "Duration": 8, "X": 0, "Y": 0, "Width": 1080, "Height": 1920, "AdaptMode": "Fill",
    }
    remotion.append_track(timeline, {"video_url": "https://media.test/overlay.webm", "duration": 8}, {"width": 1080, "height": 1920})
    assert [row["VideoTrackClips"][0]["Type"] for row in timeline["VideoTracks"]] == ["Video", "Image", "Video"]


def test_enabled_mask_cannot_silently_disappear(composition_case, mask):
    """启用但缺少上传结果时禁止提交无蒙版成片。"""
    composition_case["template"]["frame_mask"] = mask
    with pytest.raises(ValueError, match="蒙版图片尚未准备"):
        build_timeline(**composition_case)
    mask["top"]["enabled"] = mask["bottom"]["enabled"] = False
    timeline, _ = build_timeline(**composition_case)
    assert len(timeline["VideoTracks"]) == 1
