"""Select bounded model observations from complete host evidence; never delete or renumber stored frames."""


def select_frames(spec, frames, *, preferred=(), limit=12):
    """Keep requested/cited frames first, then stable layer views and temporal boundaries; return actual frame numbers."""
    available = sorted(set(frames))
    if not available:
        return []
    selected = set(preferred) & set(available)
    capacity = max(limit, len(selected))
    points = [point for frame in preferred for point in (frame - 1, frame + 1)]
    points.extend((available[0], available[-1]))
    # Phase pairs and visibility boundaries precede interior samples.
    for layer in spec.text_layers:
        for part in layer.motion:
            if part.phase != "hold":
                points.extend((part.start_frame, part.end_frame - 1))
    for layer in spec.text_layers:
        hold = next((part for part in layer.motion if part.phase == "hold"), None)
        start, end = (
            (hold.start_frame, hold.end_frame)
            if hold
            else (layer.start_frame, layer.end_frame)
        )
        points.extend(
            (
                layer.start_frame,
                start,
                (start + end - 1) // 2,
                end - 1,
                layer.end_frame - 1,
            )
        )
    for point in points:
        if len(selected) >= capacity:
            break
        selected.add(min(available, key=lambda frame: (abs(frame - point), frame)))
    # Fill remaining capacity at the largest distances from existing samples.
    while len(selected) < min(capacity, len(available)):
        selected.add(
            max(
                available,
                key=lambda frame: (
                    min(abs(frame - chosen) for chosen in selected),
                    -frame,
                ),
            )
        )
    return sorted(selected)
