# 转场候选按列表顺序选择；随机结果由任务标识与连接位置决定，重试保持一致。
from hashlib import sha256


def select_transitions(tracks: list[dict], count: int, mode: str | None, seed: str) -> list[tuple[dict, int]]:
    """返回候选及实际连接位置；顺序循环使用列表，随机选择每项最多一次。"""
    candidates = sorted((track for track in tracks if track.get("transition_boundary") is not None),
                        key=lambda track: track["transition_boundary"])
    if not candidates:
        return []
    if mode is None:
        return [(track, track["transition_boundary"]) for track in candidates if track["transition_boundary"] <= count]
    if mode not in ("sequence", "random"):
        raise ValueError("转场设置方式无效")
    selected = []
    available = list(candidates)
    for index in range(min(count, len(candidates)) if mode == "random" else count):
        position = index % len(candidates) if mode == "sequence" else int.from_bytes(
            sha256(f"{seed}:{index}".encode()).digest(), "big") % len(available)
        selected.append((available.pop(position) if mode == "random" else candidates[position], index + 1))
    return selected
