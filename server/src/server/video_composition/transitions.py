"""按关键词和成片时长统一选择 IMS／Remotion 转场；只读任务快照，旧任务与重试结果一致。"""
from bisect import bisect_left, bisect_right
from hashlib import sha256
from math import ceil, floor, sqrt
from random import Random

from .schema import Segment
from .text_events import subtitle_events


def keyword_transitions(template: dict, clips: list[dict], segments: list[Segment], matches: list[dict], *,
                        standard: bool, fps: int, seed: str, assets: dict,
                        duration: float | None = None, ordered: bool = False) -> list[tuple[dict, int]]:
    """有总秒数时按动态间隔选点；ordered 按时间筛选，旧任务按种子洗牌，缺省保留旧关键词规则。"""
    candidates = sorted((track for track in template["tracks"] if track.get("transition_boundary") is not None),
                        key=lambda track: track["transition_boundary"])
    if not template.get("transition_enabled", True) or not candidates:
        return []
    boundaries = list(range(1, len(clips)))
    anchors = [start for _, keyword, start, _ in subtitle_events(segments) if keyword]
    pinned = set()
    if anchors:
        if standard:
            # 以匹配区间辨认素材，不能靠 URL 判断：素材可能与数字人使用相同地址。
            material_ranges = {(item["start_time"], item["end_time"]) for item in matches
                               if item.get("matched_candidate_url") is not None}
            material = [(clip["TimelineIn"], clip["TimelineOut"]) in material_ranges for clip in clips]
            boundaries = [boundary for boundary in boundaries if material[boundary - 1] != material[boundary]]
        times = [clips[boundary]["TimelineIn"] for boundary in boundaries]
        nearest = set()
        for index, anchor in enumerate(anchors):
            # 仅抵消浮点加法误差，不提供关键词邻近时间窗口；同刻切镜两侧都跳过。
            before, after = bisect_left(times, anchor - 1e-8) - 1, bisect_right(times, anchor + 1e-8)
            pair = [boundaries[position] for position in (before, after) if 0 <= position < len(boundaries)]
            nearest.update(pair)
            if duration is not None and index in (0, len(anchors) - 1) and pair:
                # 同距优先前侧，舍去浮点加减误差；单关键词及共用切镜自然去重。
                pinned.add(min(pair, key=lambda boundary: (round(abs(clips[boundary]["TimelineIn"] - anchor), 8), boundary)))
        boundaries = sorted(nearest)

    # 先排除无法承载转场的切镜，避免它们占用动态间隔；最近切镜过短仍不向外寻找替代。
    available = {boundary: min(floor((clip["TimelineOut"] - clip["TimelineIn"]) * fps + 1e-8) // 2
                               for clip in clips[boundary - 1:boundary + 1]) for boundary in boundaries}
    boundaries = [boundary for boundary in boundaries if available[boundary] >= ceil(0.1 * fps)]
    if duration is not None:
        gap = min(12, max(4, 8 * sqrt(duration / 160)))
        kept = pinned.intersection(boundaries)
        middle = [boundary for boundary in boundaries if boundary not in kept
                  and (not anchors or anchors[0] < clips[boundary]["TimelineIn"] < anchors[-1])]
        # 首尾不足间隔时仍保留两次，但不再加入中间；无关键词时没有首尾保底。
        if len(kept) < 2 or clips[max(kept)]["TimelineIn"] - clips[min(kept)]["TimelineIn"] >= gap - 1e-8:
            # 仅旧版动态任务恢复时沿用洗牌；新任务保持候选的时间顺序。
            if not ordered:
                Random(seed).shuffle(middle)
            for boundary in middle:
                if all(abs(clips[boundary]["TimelineIn"] - clips[other]["TimelineIn"]) >= gap - 1e-8 for other in kept):
                    kept.add(boundary)
        boundaries = sorted(kept)

    selected = []
    for boundary in boundaries:
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
        frames = min(available[boundary], floor(seconds * fps + 0.5))
        if frames < ceil(0.1 * fps):
            continue
        selected.append(({**track, "duration": frames / fps}, boundary))
    return selected
