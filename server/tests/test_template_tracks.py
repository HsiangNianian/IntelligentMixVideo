"""多轨模板核心校验与序列化测试；在 server/ 执行 uv run --locked pytest tests/test_template_tracks.py。"""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError
from server.template.schema import EffectTemplateEditor, TemplateSave, effect_catalog
from server.template.timing import resolve_track


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
    asset = next(item for item in effect_catalog().values() if item.category == "vfx/normal")
    editor = EffectTemplateEditor(title="", subtitle="", bubble_text="", vfx=asset.id)
    return {
        "name": "多轨模板",
        "effect_ids": [asset.id], "tracks": [
            {"id": "effect-a", "target": "vfx", "start_mode": "percent", "start": 25, "duration": 3, "editor": editor.model_dump(by_alias=True)},
            {"id": "effect-b", "target": "vfx", "start_mode": "seconds", "start": 200, "duration": None, "editor": editor.model_dump(by_alias=True)},
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
