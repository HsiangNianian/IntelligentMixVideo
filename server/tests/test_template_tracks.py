"""多轨模板核心校验与序列化测试；在 server/ 执行 uv run --locked pytest tests/test_template_tracks.py。"""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError
from server.template.schema import EffectTemplateEditor, TemplateSave, effect_catalog
from server.template.timing import resolve_track

from .conftest import SPRITE_ID, remotion_track, template_track


@pytest.mark.parametrize("target", ["title", "subtitle", "vfx"])
def test_singleton_objects_reject_duplicate_tracks(target):
    """单个对象允许保存，第二个同类对象被拒绝。"""
    asset = next(item for item in effect_catalog().values() if item.category == "vfx/normal")
    track = template_track(target, **({"vfx": asset.id} if target == "vfx" else {}))
    payload = {"name": "数量校验", "effect_ids": [asset.id] if target == "vfx" else [], "tracks": [track]}
    TemplateSave.model_validate(payload)
    payload["tracks"].append({**track, "id": "second"})
    with pytest.raises(ValidationError, match="最多只能有一个"):
        TemplateSave.model_validate(payload)


@pytest.mark.parametrize("change", [{"start": 1}, {"duration": 10}, {"start_mode": "percent"}])
def test_subtitle_timing_is_fixed(change):
    """字幕时间固定为从零持续到视频结束。"""
    track = template_track("subtitle")
    payload = {"name": "字幕时间", "tracks": [track]}
    TemplateSave.model_validate(payload)
    track.update(change)
    with pytest.raises(ValidationError, match="固定覆盖整个视频"):
        TemplateSave.model_validate(payload)


def test_transition_boundaries_are_independent_and_unique():
    """两个交界处可分别设置，重复交界处及越界值均被拒绝。"""
    asset = next(item for item in effect_catalog().values() if item.category == "transition/normal")
    tracks = [{**template_track("transition", transition=asset.id), "id": f"boundary-{boundary}", "transition_boundary": boundary} for boundary in (1, 2)]
    payload = {"name": "两个转场", "tracks": tracks, "effect_ids": [asset.id]}
    assert [track.transition_boundary for track in TemplateSave.model_validate(payload).tracks] == [1, 2]
    for boundary in (0, 1, 3, True, 1.5):
        tracks[1]["transition_boundary"] = boundary
        with pytest.raises(ValidationError):
            TemplateSave.model_validate(payload)


@pytest.mark.parametrize("case", json.loads(Path(__file__).with_name("template_timing_cases.json").read_text()), ids=lambda case: case["purpose"])
def test_shared_timing_cases(case: dict) -> None:
    """与客户端共用区间和提示预期，检查帧取整及输入规则保持不变。"""
    payload = track_payload()
    payload["tracks"][0].update(start_mode=case["mode"], start=case["start"], duration=case["length"])
    track = TemplateSave.model_validate(payload).tracks[0]
    original = track.model_dump()
    result = resolve_track(track, case["duration"], case["fps"])
    assert (result.start, result.end) == (case["expected_start"], case["expected_end"])
    assert result.notice == case["expected_notice"]
    assert track.model_dump() == original


def track_payload() -> dict:
    """使用随包真实目录建立两个独立同类效果实例。"""
    asset = next(item for item in effect_catalog().values() if item.category == "filter")
    editor = EffectTemplateEditor(title="", subtitle="", bubble_text="", filter=asset.id)
    return {
        "name": "多轨模板",
        "effect_ids": [asset.id], "tracks": [
            {"id": "effect-a", "target": "filter", "start_mode": "percent", "start": 25, "duration": 3, "editor": editor.model_dump(by_alias=True)},
            {"id": "effect-b", "target": "filter", "start_mode": "seconds", "start": 200, "duration": None, "editor": editor.model_dump(by_alias=True)},
        ],
    }


def test_track_roundtrip_and_independent_delete() -> None:
    """同类实例允许重叠，JSON 往返保留时间与 ID，删除一个实例仍保留效果目录。"""
    payload = track_payload()
    saved = TemplateSave.model_validate(payload)
    assert TemplateSave.model_validate_json(saved.model_dump_json(by_alias=True)) == saved
    updated = deepcopy(payload)
    updated["tracks"].pop(0)
    result = TemplateSave.model_validate(updated)
    assert result.tracks[0].id == "effect-b"
    assert result.effect_ids == saved.effect_ids
    assert len(saved.tracks) == 2


@pytest.mark.parametrize("change", [
    {"id": "effect-b"}, {"start": -1}, {"duration": 0}, {"start": 100},
    {"start": float("nan")}, {"duration": float("inf")}, {"target": "unknown"},
    {"start_mode": "frames"}, {"end": 21},
])
def test_invalid_track_boundaries(change: dict) -> None:
    """重复 ID、非法类型、百分比和持续时间均不能保存。"""
    payload = track_payload()
    payload["tracks"][0].update(change)
    with pytest.raises(ValidationError):
        TemplateSave.model_validate(payload)


def test_track_rejects_foreign_content_and_unknown_effect() -> None:
    """实例只允许保存自身对象的内容与可信目录效果。"""
    for change in ({"title": "其他文字"}, {"vfx": "vfx/normal/unknown"}, {"titleIn": "in/fade_in"}):
        payload = track_payload()
        payload["tracks"][0]["editor"].update(change)
        with pytest.raises(ValidationError):
            TemplateSave.model_validate(payload)


def test_keyword_style_belongs_to_subtitle_track() -> None:
    """旧模板默认关闭局部样式，只有字幕轨道可以选择样式与颜色。"""
    payload = track_payload()
    payload["tracks"] = payload["tracks"][:1]
    assert not TemplateSave.model_validate(payload).tracks[0].editor.subtitle_keyword_bold
    assert TemplateSave.model_validate(payload).tracks[0].editor.subtitle_keyword_size == 0
    payload["tracks"][0]["editor"]["subtitleKeywordBold"] = True
    with pytest.raises(ValidationError, match="只有底部字幕"):
        TemplateSave.model_validate(payload)
    payload["tracks"][0]["editor"].update(subtitleKeywordBold=False, subtitleKeywordColor="#12AB34")
    with pytest.raises(ValidationError, match="只有底部字幕"):
        TemplateSave.model_validate(payload)
    payload["tracks"][0]["editor"].update(subtitleKeywordColor="", subtitleKeywordSize=64)
    with pytest.raises(ValidationError, match="只有底部字幕"):
        TemplateSave.model_validate(payload)
    payload["tracks"][0]["target"] = "subtitle"
    payload["tracks"][0].update(start_mode="seconds", start=0, duration=None)
    payload["tracks"][0]["editor"].update(title="", subtitle="示例字幕", filter="", subtitleIn="in/fade_in", subtitleKeywordBold=True, subtitleKeywordColor="#12AB34")
    payload["effect_ids"] = ["in/fade_in"]
    saved = TemplateSave.model_validate(payload)
    assert saved.tracks[0].editor.subtitle_keyword_bold
    assert saved.tracks[0].editor.subtitle_keyword_color == "#12AB34"
    assert saved.tracks[0].editor.subtitle_keyword_size == 64
    payload["tracks"][0]["editor"]["subtitleKeywordBold"] = "true"
    with pytest.raises(ValidationError):
        TemplateSave.model_validate(payload)
    payload["tracks"][0]["editor"].update(subtitleKeywordBold=True, subtitleKeywordColor="red")
    with pytest.raises(ValidationError):
        TemplateSave.model_validate(payload)
    payload["tracks"][0]["editor"].update(subtitleKeywordColor="", subtitleKeywordSize=11)
    with pytest.raises(ValidationError):
        TemplateSave.model_validate(payload)


def test_title_keyword_style_belongs_to_title_track() -> None:
    """标题样式只保存在标题对象，颜色格式须有效。"""
    payload = track_payload()
    payload["tracks"] = payload["tracks"][:1]
    editor = payload["tracks"][0]["editor"]
    editor["titleKeywordBold"] = True
    with pytest.raises(ValidationError, match="只有顶部标题"):
        TemplateSave.model_validate(payload)
    editor.update(titleKeywordBold=False, titleKeywordSize=72)
    with pytest.raises(ValidationError, match="只有顶部标题"):
        TemplateSave.model_validate(payload)
    payload["tracks"][0]["target"] = "title"
    editor.update(title="标题示例", filter="", titleIn="in/fade_in", titleKeywordBold=True, titleKeywordColor="#12AB34")
    payload["effect_ids"] = ["in/fade_in"]
    saved = TemplateSave.model_validate(payload)
    assert saved.tracks[0].editor.title_keyword_bold
    assert saved.tracks[0].editor.title_keyword_color == "#12AB34"
    assert saved.tracks[0].editor.title_keyword_size == 72
    editor["titleKeywordColor"] = "red"
    with pytest.raises(ValidationError):
        TemplateSave.model_validate(payload)


def test_track_animation_uses_available_frames() -> None:
    """应用时缩短动画，输入模板保持原参数；一帧无法容纳两个动画时明确失败。"""
    payload = track_payload()
    editor = EffectTemplateEditor(title="文字", subtitle="", bubble_text="", title_in="in/fade_in")
    payload["tracks"] = [{"id": "title", "target": "title", "start_mode": "seconds", "start": 1, "duration": 0.2, "editor": editor.model_dump(by_alias=True)}]
    payload["effect_ids"] = ["in/fade_in"]
    track = TemplateSave.model_validate(payload).tracks[0]
    applied = resolve_track(track, 10, 30)
    assert applied.editor.title_in_duration == 0.2
    assert track.editor.title_in_duration == 0.5
    track.editor.title_out = "out/fade_out"
    track.duration = 1 / 30
    with pytest.raises(ValueError, match="帧数不足"):
        resolve_track(track, 10, 30)


def test_seconds_and_until_end_do_not_depend_on_preview() -> None:
    """第 200 秒开始的对象可以保存；短视频不显示，长视频持续至结束。"""
    track = TemplateSave.model_validate(track_payload()).tracks[1]
    short = resolve_track(track, 20, 30)
    assert short.start == short.end
    assert "没有可显示" in short.notice
    long = resolve_track(track, 300, 30)
    assert (long.start, long.end) == (200, 300)


def test_template_rejects_media() -> None:
    """模板请求拒绝媒体信息，保证保存数据与预览视频独立。"""
    payload = track_payload()
    payload["media"] = {"url": "https://example.com/video.mp4", "duration": 20, "width": 1920, "height": 1080}
    with pytest.raises(ValidationError):
        TemplateSave.model_validate(payload)


@pytest.mark.parametrize("invalid", ["missing-tracks", "null-tracks", "top-editor"])
def test_template_format_requires_tracks(invalid: str) -> None:
    """Schema 拒绝缺少对象数组、空值以及顶层 editor。"""
    rejected = track_payload()
    if invalid == "missing-tracks":
        rejected.pop("tracks")
    elif invalid == "null-tracks":
        rejected["tracks"] = None
    else:
        rejected["editor"] = EffectTemplateEditor().model_dump(by_alias=True)
    with pytest.raises(ValidationError):
        TemplateSave.model_validate(rejected)


def test_remotion_tracks_are_references_with_an_empty_editor() -> None:
    """片段轨道只保存 Sprite 引用和秒数起点，编辑参数输出为空对象；同一 Sprite 可摆放多次，IMS 对象的键不增加。"""
    payload = track_payload()
    payload["tracks"] += [remotion_track("clip-a", start=1.5, duration=2), remotion_track("clip-b")]
    saved = TemplateSave.model_validate(payload)
    dumped = saved.model_dump(mode="json", by_alias=True)
    assert dumped["tracks"][2] == {
        "id": "clip-a", "target": "remotion", "sprite_id": SPRITE_ID,
        "start_mode": "seconds", "start": 1.5, "duration": 2, "editor": {},
    }
    assert dumped["tracks"][3]["duration"] is None
    assert [track["sprite_id"] for track in dumped["tracks"][2:]] == [SPRITE_ID, SPRITE_ID]
    assert set(dumped["tracks"][0]) == {"id", "target", "start_mode", "start", "duration", "editor"}
    assert saved.effect_ids == payload["effect_ids"]
    assert TemplateSave.model_validate_json(saved.model_dump_json(by_alias=True)) == saved


def test_template_may_contain_only_remotion_tracks() -> None:
    """只放片段的模板没有 IMS 效果，空的效果列表与对象一致即可保存。"""
    saved = TemplateSave.model_validate({"name": "片段模板", "effect_ids": [], "tracks": [remotion_track()]})
    assert saved.effect_ids == []
    assert str(saved.tracks[0].sprite_id) == SPRITE_ID


@pytest.mark.parametrize(("change", "reason"), [
    ({"sprite_id": None}, "缺少 sprite_id"), ({"sprite_id": "not-a-uuid"}, "valid UUID"),
    ({"start_mode": "percent"}, "只能使用秒数起点"), ({"start": -1}, "greater than or equal to 0"),
    ({"duration": 0}, "greater than 0"), ({"editor": {"title": ""}}, "只能携带参数覆盖与宿主变换"),
    ({"editor": {"vfx": "vfx/normal/unknown"}}, "只能携带参数覆盖与宿主变换"), ({"id": "effect-a"}, "轨道 ID 重复"),
], ids=["no-sprite", "bad-sprite", "percent", "negative-start", "zero-duration",
        "explicit-editor", "editor-effect", "duplicate-id"])
def test_invalid_remotion_tracks_are_validation_errors(change: dict, reason: str) -> None:
    """缺失或伪造的引用、百分比起点、编辑参数和重复 ID 都是校验错误（而不是服务端异常），并说明各自的原因。"""
    payload = track_payload()
    payload["tracks"].append(remotion_track("clip-a", **change))
    with pytest.raises(ValidationError, match=reason):
        TemplateSave.model_validate(payload)


def test_sprite_reference_is_rejected_on_ims_tracks() -> None:
    """只有 Remotion 片段可以引用 Sprite，IMS 对象携带引用时拒绝保存。"""
    payload = track_payload()
    payload["tracks"][0]["sprite_id"] = SPRITE_ID
    with pytest.raises(ValidationError, match="只有 Remotion 片段"):
        TemplateSave.model_validate(payload)


def test_remotion_tracks_share_the_track_limit() -> None:
    """片段与 IMS 对象共用 100 条上限。"""
    payload = {"name": "上限", "effect_ids": [], "tracks": [remotion_track(f"clip-{index}") for index in range(101)]}
    with pytest.raises(ValidationError):
        TemplateSave.model_validate(payload)
    payload["tracks"].pop()
    assert len(TemplateSave.model_validate(payload).tracks) == 100


@pytest.mark.parametrize("duration", [None, 0.5, 2])
def test_ims_timing_defaults(duration: float | None) -> None:
    """Schema 使用一秒转场和半秒文字动画默认值，并保留显式转场时长。"""
    payload = track_payload()
    if duration is not None:
        payload["transition_duration_seconds"] = duration
    saved = TemplateSave.model_validate(payload)
    assert saved.transition_duration_seconds == (1 if duration is None else duration)
    for role in ("title", "subtitle", "bubble"):
        assert getattr(saved.tracks[0].editor, f"{role}_in_duration") == 0.5
        assert getattr(saved.tracks[0].editor, f"{role}_out_duration") == 0.5
