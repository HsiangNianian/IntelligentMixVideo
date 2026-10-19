# 模板规则与真实片段共同生成时间结果；顺序拼接扣除重叠，固定时间保留语音位置。
from dataclasses import dataclass
from math import ceil, floor, isfinite
from typing import Literal

from pydantic import BaseModel, Field

from .schema import EffectTemplateEditor, EffectTrack, TemplateData, text_role


@dataclass(frozen=True)
class ResolvedTrack:
    """本次视频中实际使用的区间、参数和可公开调整说明。"""

    start: float
    end: float
    editor: EffectTemplateEditor
    notice: str


def resolve_track(track: EffectTrack, duration: float, fps: int) -> ResolvedTrack:
    """秒数或百分比转帧；结尾以外不显示，越界截短，动画按可用帧数缩短。"""
    if not isfinite(duration) or duration <= 0 or type(fps) is not int or fps <= 0:
        raise ValueError("视频时长或帧率无效")
    if track.target == "transition":
        raise ValueError("转场需要视频片段信息")
    start = duration * track.start / 100 if track.start_mode == "percent" else track.start
    end = duration if track.duration is None else start + track.duration
    first = floor(start * fps + 0.5)
    last = min(floor(duration * fps + 1e-8), floor(end * fps + 0.5))
    editor = track.editor.model_copy(deep=True)
    if first >= last:
        return ResolvedTrack(first / fps, first / fps, editor, "当前视频中没有可显示的时间")
    notice = "持续时间已缩短至视频结束" if end > duration + 1e-8 else ""
    role = text_role(track.target)
    if role in ("title", "subtitle", "bubble"):
        motions = [motion for motion in ("in", "out") if getattr(editor, f"{role}_{motion}")]
        total = sum(getattr(editor, f"{role}_{motion}_duration") for motion in motions)
        if last - first < len(motions):
            raise ValueError("文字显示帧数不足以完成入场与出场动画")
        if total > (last - first) / fps + 1e-8:
            available = last - first
            for index, motion in enumerate(motions):
                field = f"{role}_{motion}_duration"
                frames = available if index == len(motions) - 1 else max(1, min(available - 1, floor(getattr(editor, field) / total * (last - first) + 0.5)))
                setattr(editor, field, frames / fps)
                available -= frames
            notice = "；".join(filter(None, [notice, "入场与出场动画已按显示时间缩短"]))
    return ResolvedTrack(first / fps, last / fps, editor, notice)


class ClipContext(BaseModel):
    """真实片段的可用时长及固定时间模式下的开始位置。"""

    duration: float = Field(gt=0, allow_inf_nan=False)
    start: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class TimelineContext(BaseModel):
    """本次视频的时间安排，所有媒体信息在计算前完成读取。"""

    layout: Literal["sequence", "fixed"]
    clips: list[ClipContext] = Field(min_length=1)
    fps: int = Field(gt=0, le=60, strict=True)
    duration: float | None = Field(default=None, gt=0, allow_inf_nan=False)


@dataclass(frozen=True)
class ResolvedTimeline:
    """计算后的总长、视频区间和对象区间，渲染只转换其表示方式。"""

    duration: float
    clips: list[dict[str, float]]
    tracks: dict[str, ResolvedTrack]
    notices: list[str]


def resolve_template(template: TemplateData, context: TimelineContext) -> ResolvedTimeline:
    """按帧统一应用对象规则，缺少交界处及不可用转场返回明确说明。"""
    fps = context.fps
    lengths = [floor(clip.duration * fps + 1e-8) for clip in context.clips]
    if context.layout == "sequence" and not all(lengths):
        raise ValueError("视频片段不足一帧")
    overlaps = [0] * len(lengths)
    transition_notices = {}
    for track in template.tracks:
        if track.target != "transition":
            continue
        index = track.transition_boundary - 1
        if index + 1 >= len(lengths):
            transition_notices[track.id] = f"交界处 {index + 1} 不存在，未应用转场"
            continue
        requested = floor(track.duration * fps + 0.5)
        frames = min(requested, lengths[index] // 2, lengths[index + 1] // 2)
        if frames < ceil(0.1 * fps):
            transition_notices[track.id] = "相邻片段过短，未应用转场"
        else:
            overlaps[index] = frames
            if frames < requested:
                transition_notices[track.id] = "转场已按相邻片段可用时间缩短"
    clips = []
    end = 0
    for index, frames in enumerate(lengths):
        if context.layout == "fixed":
            if context.clips[index].start is None:
                raise ValueError("固定片段缺少开始时间")
            start = context.clips[index].start
            if start + 1e-8 < end:
                raise ValueError("固定片段时间重叠")
            end = start + context.clips[index].duration
            clips.append({"start": start, "end": end})
        else:
            start = end - (overlaps[index - 1] if index else 0)
            end = start + frames
            clips.append({"start": start / fps, "end": end / fps})
    duration = end / fps if context.layout == "sequence" else context.duration
    if duration is None or clips[-1]["end"] > duration + 1e-8:
        raise ValueError("视频总时长无效")
    tracks = {}
    notices = []
    for track in template.tracks:
        if track.target == "remotion":
            continue
        if track.target == "transition":
            index = track.transition_boundary - 1
            boundary_end = clips[index]["end"] if index < len(clips) else 0
            overlap = overlaps[index] / fps if index < len(overlaps) else 0
            resolved = ResolvedTrack(boundary_end - overlap, boundary_end, track.editor, transition_notices.get(track.id, ""))
        else:
            resolved = resolve_track(track, duration, fps)
        tracks[track.id] = resolved
        if resolved.notice:
            notices.append(f"对象 {track.id}：{resolved.notice}")
    return ResolvedTimeline(duration, clips, tracks, notices)
