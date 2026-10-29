"""上下蒙版的协议、PNG 像素与 IMS 层级回归；运行 uv run --locked pytest tests/test_frame_mask.py。"""

from copy import deepcopy
import json
from pathlib import Path

from PIL import Image
import pytest

from server.template.avatar import AvatarEffects
from server.template.split_screen import SplitScreen
from server.video_composition import frame_mask, remotion
from server.video_composition.timeline import build_timeline
from .template_wire import post_template, saved_template, detail_template
from .test_split_screen import enable

CASES = json.loads(Path(__file__).with_name("frame_mask_cases.json").read_text())


@pytest.fixture
def mask():
    """两栏使用不同颜色，能够识别错位和上下颠倒。"""
    return {"top": {"enabled": True, "height": 18.4, "color": "#112233"},
            "bottom": {"enabled": True, "height": 16.6, "color": "#445566"}}


def test_mask_roundtrip_and_legacy_update(client, template_payload, mask):
    """保存、重读与旧客户端省略字段的更新保留蒙版；显式关闭不丢样式。"""
    settings = {
        "frame_mask": mask,
        "split_screen": SplitScreen(enabled=True).model_dump(mode="json", by_alias=True),
        "avatar_effects": AvatarEffects(opening_enabled=True, feather=0).model_dump(mode="json", by_alias=True),
    }
    created = post_template(client, {**template_payload, **settings})
    assert created.status_code == 201, created.text
    record = saved_template(created)
    assert {field: record[field] for field in settings} == settings
    template_id = record["template_id"]
    detail = detail_template(client.get(f"/template/{template_id}"))
    assert {field: detail[field] for field in settings} == settings
    updated = post_template(client, {**template_payload, "template_id": template_id, "name": "旧客户端修改名称"})
    assert updated.status_code == 200, updated.text
    saved = saved_template(updated)
    assert {field: saved[field] for field in settings} == settings
    mask["top"]["enabled"] = mask["bottom"]["enabled"] = False
    disabled = post_template(client, {**template_payload, "template_id": template_id, "frame_mask": mask})
    saved = saved_template(disabled)
    assert {field: saved[field] for field in settings} == settings


def test_gradient_roundtrip_and_legacy_bar_update(client, template_payload, mask):
    """上下渐变独立保存；旧客户端重存纯色栏不丢新字段，显式切回纯色保留参数。"""
    plain = deepcopy(mask)
    mask["top"]["gradient"] = {"enabled": True, "endColor": "#aabbcc", "direction": "up", "range": 50}
    mask["bottom"]["gradient"] = {"enabled": True, "endColor": "#001122", "direction": "down", "range": 100}
    created = post_template(client, {**template_payload, "frame_mask": mask})
    assert created.status_code == 201, created.text
    template_id = saved_template(created)["template_id"]
    assert detail_template(client.get(f"/template/{template_id}"))["frame_mask"] == mask
    assert created.json()["template"]["frameMask"]["top"]["gradient"]["endColor"] == "#aabbcc"
    payload = {**template_payload, "template_id": template_id}
    updated = post_template(client, {**payload, "frame_mask": plain})
    assert updated.status_code == 200, updated.text
    assert saved_template(updated)["frame_mask"] == mask
    mask["top"]["gradient"]["enabled"] = False
    disabled = post_template(client, {**payload, "frame_mask": mask})
    assert disabled.status_code == 200, disabled.text
    assert saved_template(disabled)["frame_mask"] == mask
    removed = post_template(client, {**payload, "frame_mask": {}})
    assert removed.status_code == 200 and "frame_mask" not in saved_template(removed)


@pytest.mark.parametrize("field,value", [("direction", "left"), ("endColor", "#11223300"), ("range", 0), ("range", 101), ("enabled", None)])
def test_invalid_gradient_keeps_saved_template(client, template_payload, mask, field, value):
    """渐变字段非法或缺少必填开关返回 422，已有模板不变。"""
    original = saved_template(post_template(client, {**template_payload, "frame_mask": mask}))
    gradient = {"enabled": True, "endColor": "#aabbcc", "direction": "up", "range": 50}
    gradient[field] = value
    mask["top"]["gradient"] = gradient
    result = post_template(client, {**template_payload, "template_id": original["template_id"], "frame_mask": mask})
    assert result.status_code == 422
    assert detail_template(client.get(f"/template/{original['template_id']}"))["frame_mask"] == original["frame_mask"]


def test_empty_mask_removes_saved_configuration(client, template_payload, composition_case, mask):
    """明确空消息移除已保存蒙版，省略字段仍保留；移除后详情与合成均无蒙版。"""
    baseline, _ = build_timeline(**composition_case)
    payload = {**template_payload, "template_id": composition_case["template"]["template_id"]}
    added = post_template(client, {**payload, "frame_mask": mask})
    assert added.status_code == 200, added.text
    assert saved_template(post_template(client, payload))["frame_mask"] == mask
    removed = post_template(client, {**payload, "frame_mask": {}})
    assert removed.status_code == 200, removed.text
    assert "frameMask" not in removed.json()["template"]
    record = detail_template(client.get(f"/template/{payload['template_id']}"))
    assert "frame_mask" not in record
    assert "frame_mask" not in saved_template(post_template(client, payload))
    timeline, _ = build_timeline(**{**composition_case, "template": record})
    assert timeline == baseline


@pytest.mark.parametrize("edge", ["top", "bottom"])
def test_partial_mask_cannot_remove_configuration(client, template_payload, mask, edge):
    """只有整个空消息代表移除，缺少单栏的非法配置仍拒绝且不改已有蒙版。"""
    created = saved_template(post_template(client, {**template_payload, "frame_mask": mask}))
    partial = {key: value for key, value in mask.items() if key != edge}
    result = post_template(client, {**template_payload, "template_id": created["template_id"], "frame_mask": partial})
    assert result.status_code == 422
    assert detail_template(client.get(f"/template/{created['template_id']}"))["frame_mask"] == mask


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


@pytest.mark.parametrize("layout", ["split", "overlay", "circle"])
def test_mask_covers_split_screen_and_avatar_opening(composition_case, mask, layout):
    """蒙版位于同屏、边框与数字人开场之上，字幕与后续 Remotion 保持原层级。"""
    case = enable(composition_case)
    case["template"]["split_screen"]["layout"] = layout
    case["template"]["avatar_effects"] = AvatarEffects(opening_enabled=True).model_dump(mode="json", by_alias=True)
    case["avatar_opening"] = {"duration": 2, "x": 0.5, "y": 0.3, "radius": 0.25,
                              "aspect": 720 / 1280, "background": "https://example.test/black.png"}
    baseline, _ = build_timeline(**case)
    case["template"]["frame_mask"] = mask
    timeline, _ = build_timeline(**case, frame_mask_url="https://media.test/mask.png")
    assert timeline["VideoTracks"][:-1] == baseline["VideoTracks"]
    assert timeline["SubtitleTracks"] == baseline["SubtitleTracks"]
    clip = timeline["VideoTracks"][-1]["VideoTrackClips"][0]
    assert clip["MediaURL"] == "https://media.test/mask.png"
    assert (clip["Width"], clip["Height"], clip["TimelineIn"], clip["TimelineOut"]) == (1080, 1920, 0, 8)
    remotion.append_track(timeline, {"video_url": "https://media.test/overlay.webm", "duration": 8}, case)
    assert timeline["VideoTracks"][-2]["VideoTrackClips"][0] == clip
    assert timeline["VideoTracks"][-1]["VideoTrackClips"][0]["MediaURL"] == "https://media.test/overlay.webm"
