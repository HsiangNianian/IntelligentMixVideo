# 多轨模板核心校验与序列化测试；在 server/ 执行 uv run --locked pytest tests/test_template_tracks.py。
from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from server.template.schema import EffectTemplateEditor, TemplateSave, effect_catalog
from server.template.timing import resolve_track


@pytest.mark.parametrize("case", json.loads(Path(__file__).with_name("template_timing_cases.json").read_text()), ids=lambda case: case["purpose"])
def test_shared_timing_cases(case: dict) -> None:
    """与客户端共用逐例说明及预期结果，检查百分比、结束规则和帧取整一致性。"""
    payload = track_payload()
    payload["tracks"][0].update(start_mode=case["mode"], start=case["start"], duration=case["length"])
    track = TemplateSave.model_validate(payload).tracks[0]
    result = resolve_track(track, case["duration"], case["fps"])
    assert (result.start, result.end) == (case["expected_start"], case["expected_end"])


def track_payload() -> dict:
    """使用随包真实目录建立两个独立同类效果实例。"""
    asset = next(item for item in effect_catalog().values() if item.category == "vfx/normal")
    editor = EffectTemplateEditor(title="", subtitle="", bubble_text="", vfx=asset.id)
    return {
        "name": "多轨模板", "editor": EffectTemplateEditor().model_dump(by_alias=True),
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


@pytest.mark.parametrize("duration,start,end", [(20, 5, 8), (60, 15, 18), (120, 30, 33), (2, 0.5, 2)])
def test_percentage_applies_to_different_videos(duration: float, start: float, end: float) -> None:
    """25% 开始与三秒持续时间分别计算，短视频截短区间且保留原规则。"""
    track = TemplateSave.model_validate(track_payload()).tracks[0]
    original = track.model_dump()
    applied = resolve_track(track, duration, 30)
    assert (applied.start, applied.end) == (start, end)
    assert track.model_dump() == original
    assert bool(applied.notice) == (duration == 2)


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


def test_api_persists_tracks_and_rejects_invalid_update(client: TestClient) -> None:
    """真实路由与隔离数据库保存完整轨道，非法更新保留原记录。"""
    payload = track_payload()
    response = client.post("/template", json=payload)
    assert response.status_code == 201
    saved = response.json()
    path = f"/template/{saved['template_id']}"
    assert client.get(path).json()["tracks"] == saved["tracks"]
    assert "media" not in client.get(path).json()
    payload["template_id"] = saved["template_id"]
    payload["tracks"][0]["start"] = 100
    assert client.post("/template", json=payload).status_code == 422
    assert client.get(path).json() == saved
    payload["tracks"].pop(0)
    updated = client.post("/template", json=payload)
    assert updated.status_code == 200
    assert client.get(path).json()["tracks"] == updated.json()["tracks"]
    assert updated.json()["tracks"][0]["id"] == "effect-b"
