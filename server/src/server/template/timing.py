# 模板对象的时间应用计算；输出帧区间和动画副本，保留输入规则，供合成与测试共用。
from dataclasses import dataclass
from math import floor, isfinite

from .schema import EffectTemplateEditor, EffectTrack


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
    start = duration * track.start / 100 if track.start_mode == "percent" else track.start
    end = duration if track.duration is None else start + track.duration
    first = floor(start * fps + 0.5)
    last = min(floor(duration * fps + 1e-8), first + floor(track.duration * fps + 0.5) if track.target == "transition" else floor(end * fps + 0.5))
    editor = track.editor.model_copy(deep=True)
    if track.target == "transition" and (first <= 0 or last >= floor(duration * fps + 1e-8) or end > duration):
        raise ValueError("转场须位于两个非空视频片段之间")
    if first >= last:
        return ResolvedTrack(first / fps, first / fps, editor, "当前视频中没有可显示的时间")
    notice = "持续时间已缩短至视频结束" if end > duration + 1e-8 else ""
    if track.target in ("title", "subtitle", "bubble"):
        motions = [motion for motion in ("in", "out") if getattr(editor, f"{track.target}_{motion}")]
        total = sum(getattr(editor, f"{track.target}_{motion}_duration") for motion in motions)
        if last - first < len(motions):
            raise ValueError("文字显示帧数不足以完成入场与出场动画")
        if total > (last - first) / fps + 1e-8:
            available = last - first
            for index, motion in enumerate(motions):
                field = f"{track.target}_{motion}_duration"
                frames = available if index == len(motions) - 1 else max(1, min(available - 1, floor(getattr(editor, field) / total * (last - first) + 0.5)))
                setattr(editor, field, frames / fps)
                available -= frames
            notice = "；".join(filter(None, [notice, "入场与出场动画已按显示时间缩短"]))
    return ResolvedTrack(first / fps, last / fps, editor, notice)
