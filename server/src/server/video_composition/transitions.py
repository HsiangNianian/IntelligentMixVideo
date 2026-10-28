"""按关键词前后真实切镜统一选择 IMS／Remotion 转场；只读任务快照，重试结果一致。"""
from bisect import bisect_left, bisect_right
from hashlib import sha256
from math import ceil, floor

from .schema import Segment
from .text_events import subtitle_events


def keyword_transitions(template: dict, clips: list[dict], segments: list[Segment], matches: list[dict], *,
                        standard: bool, fps: int, seed: str, assets: dict) -> list[tuple[dict, int]]:
    """严格取关键词起点前后最近切镜并去重；无词取全部，过短跳过且不推进顺序候选。"""
    candidates = sorted((track for track in template["tracks"] if track.get("transition_boundary") is not None),
                        key=lambda track: track["transition_boundary"])
    if not template.get("transition_enabled", True) or not candidates:
        return []
    boundaries = list(range(1, len(clips)))
    anchors = [start for _, keyword, start, _ in subtitle_events(segments) if keyword]
    if anchors:
        if standard:
            # 以匹配区间辨认素材，不能靠 URL 判断：素材可能与数字人使用相同地址。
            material_ranges = {(item["start_time"], item["end_time"]) for item in matches
                               if item.get("matched_candidate_url") is not None}
            material = [(clip["TimelineIn"], clip["TimelineOut"]) in material_ranges for clip in clips]
            boundaries = [boundary for boundary in boundaries if material[boundary - 1] != material[boundary]]
        times = [clips[boundary]["TimelineIn"] for boundary in boundaries]
        nearest = set()
        for anchor in anchors:
            # 仅抵消浮点加法误差，不提供关键词邻近时间窗口；同刻切镜两侧都跳过。
            before, after = bisect_left(times, anchor - 1e-8) - 1, bisect_right(times, anchor + 1e-8)
            if before >= 0:
                nearest.add(boundaries[before])
            if after < len(boundaries):
                nearest.add(boundaries[after])
        boundaries = sorted(nearest)

    selected = []
    for boundary in boundaries:
        available = min(floor((clip["TimelineOut"] - clip["TimelineIn"]) * fps + 1e-8) // 2
                        for clip in clips[boundary - 1:boundary + 1])
        if available < ceil(0.1 * fps):
            continue
        position = (int.from_bytes(sha256(f"{seed}:{boundary}".encode()).digest(), "big") % len(candidates)
                    if template.get("transition_mode") == "random" else len(selected) % len(candidates))
        track = candidates[position]
        if track["target"] == "transition":
            seconds = template.get("transition_duration_seconds", 1)
        else:
            seconds = track["duration"]
            if seconds is None:
                canvas = assets[track["sprite_id"]]["canvas"]
                seconds = canvas["preview_frames"] / canvas["fps"]
        frames = min(available, floor(seconds * fps + 0.5))
        if frames < ceil(0.1 * fps):
            continue
        selected.append(({**track, "duration": frames / fps}, boundary))
    return selected
