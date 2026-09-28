"""Compute every literal keyword span in Unicode code-point indices for subtitle Sprites."""


def literal_ranges(text: str, keywords: list[str]) -> list[list[int]]:
    """Return merged half-open spans, including repeated and overlapping literal matches."""
    matches = sorted(
        (start, start + len(word))
        for word in set(keywords)
        if word
        for start in range(len(text))
        if text.startswith(word, start)
    )
    merged: list[list[int]] = []
    for start, end in matches:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged
