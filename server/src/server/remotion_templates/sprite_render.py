"""Render one Sprite for the bus (`SpriteRenderInput`) as one complete video: a fixed style, its content and a time.

The Sprite's style is its accepted code plus the editable style overrides and host transform of the placement.
The content is either one `text` with `keywords` shown for the whole effect (a title) or timed `cues` (subtitles,
each shown once in its own frames, so the Sprite reads the cue's length as `durationInFrames`). A multi-line text
effect (`categories.line_count` above 1) shows up to that many consecutive cues of one sentence together instead:
line i appears when cue i starts (the host passes each line's frame as `lineStartFrames`) and the group clears when
its last cue ends. A bilingual text (双语字幕) is one language per tier, so this request — which carries no
translation — renders it like a single-line text with its English tier empty; both languages are injected by video
composition, which reads the task's segments. A text shown for less than `STATIC_TEXT_SECONDS` plays no entrance or exit: the Sprite runs at its
previewed length, frozen on the middle frame with every line shown, for the text's frames, which avoids flicker and
code that cannot animate in so few frames. The time is
`effect_total_frames`, 0.5 to 120 seconds, whatever length the user previewed when generating the Sprite.
`plan_sprite_render` checks the request and places the instances on the whole effect; `render_sprite` renders it in
one overlay worker run that writes consecutive 15-second chunks from the same composition (so every
animation continues across a chunk boundary) and joins them losslessly into one WebM. The async task, upload and
callback are in `overlay_service.py`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from generated.imv.sprite.v1 import sprite_pb2 as pb
from pydantic import ValidationError

from ..template.schema import RemotionTrackEditor
from .categories import BILINGUAL_PROP, LINE_START_MAX_FRAMES, LINE_STARTS_PROP, bilingual as is_bilingual, line_count
from .overlay_render import Overlay, OverlayError, chunk_ranges, overlay_ranges, render_overlay, to_frames
from .settings import Settings
from .placement import resolve
from .renderer import Renderer
from .tool_validation import MAX_RENDER_SECONDS, MIN_RENDER_SECONDS, STATIC_TEXT_SECONDS


def _editor(message: pb.SpriteRenderInput, sprite: pb.PublishedSprite, text: str, keywords: list[str]) -> RemotionTrackEditor:
    """The placement editor of one content: the style overrides plus the text and keywords, which only content may set."""
    content = {sprite.text_prop, sprite.keywords_prop} - {""}
    parameters = []
    for item in message.resolved_style:
        if item.key in content:
            raise OverlayError("placement_invalid", f"参数 {item.key} 是内容，只能通过 text、keywords 或 cues 传入")
        case = item.value.WhichOneof("value")
        if case is None:
            raise OverlayError("placement_invalid", f"参数 {item.key} 没有给出值")
        value = {"values": list(item.value.string_list_value.values)} if case == "string_list_value" else getattr(item.value, case)
        parameters.append({"key": item.key, case: value})
    if text:
        parameters.append({"key": sprite.text_prop, "string_value": text})
    if keywords:
        parameters.append({"key": sprite.keywords_prop, "string_list_value": {"values": keywords}})
    transform = {
        name: getattr(message.transform, name)
        for name in ("x", "y", "scale", "rotation", "opacity") if message.transform.HasField(name)
    }
    try:
        return RemotionTrackEditor(parameters=parameters, transform=transform or None)
    except ValidationError as exc:
        raise OverlayError("placement_invalid", f"样式或内容无效：{exc.errors()[0]['msg']}") from None


def _contents(message: pb.SpriteRenderInput, sprite: pb.PublishedSprite, total: int) -> list[tuple[int, int, str, list[str], list[int]]]:
    """The shown contents as (start frame, frames, text, keywords, line starts) on the whole effect.

    A text effect takes `text` for the whole effect or ordered, non-overlapping, non-empty `cues`; other kinds take
    no content and render once over the whole effect. A multi-line text effect groups up to its line count of
    consecutive cues, starting a new group when the cue's `sentence` changes (both given): the lines are joined with
    a line break and each line starts at its cue's frame, counted from the group's start. A multi-line `text` is
    shown whole from the first frame and has at most the line count of lines. A bilingual text (双语字幕) takes one
    cue per instance like a single-line text: this request carries no translation, so its English tier stays empty
    (`plan_sprite_render` clears it) instead of grouping cues into two lines.
    """
    if sprite.kind != pb.SPRITE_KIND_TEXT:
        if message.text or message.keywords or message.cues:
            raise OverlayError("content_invalid", "只有字效接收 text、keywords 或 cues")
        return [(0, total, "", [], [])]
    if bool(message.text) == bool(message.cues):
        raise OverlayError("content_invalid", "字效必须提供 text 或 cues 之一")
    schema = json.loads(sprite.config_schema_json or "{}")
    lines = 1 if is_bilingual(schema) else line_count(schema)
    if message.text:
        if lines > 1 and message.text.count("\n") >= lines:
            raise OverlayError("content_invalid", f"这个字效最多显示 {lines} 行")
        contents = [(0, total, message.text, list(message.keywords), [0])]
    else:
        if message.keywords:
            raise OverlayError("content_invalid", "使用 cues 时关键词放在各切片里")
        contents, end = [], 0
        for index, cue in enumerate(message.cues, start=1):
            if not cue.text:
                raise OverlayError("content_invalid", f"第 {index} 个切片没有文字")
            if cue.start_frame < end:
                raise OverlayError("content_invalid", f"第 {index} 个切片与前一个重叠或没有按起点排列")
            if cue.end_frame <= cue.start_frame:
                raise OverlayError("content_invalid", f"第 {index} 个切片的结束帧必须晚于开始帧")
            if cue.end_frame > total:
                raise OverlayError("content_invalid", f"第 {index} 个切片超出了效果时长")
            if lines > 1 and "\n" in cue.text:
                raise OverlayError("content_invalid", f"第 {index} 个切片的文字不能换行")
            contents.append((cue.start_frame, cue.end_frame - cue.start_frame, cue.text, list(cue.keywords), [0]))
            end = cue.end_frame
        if lines > 1:
            limit = json.loads(sprite.config_schema_json or "{}").get("properties", {}).get(sprite.text_prop or "text", {}).get("maxLength")
            contents = _groups(message.cues, contents, lines, limit)
    if any(keywords for *_, keywords, _ in contents) and not sprite.keywords_prop:
        raise OverlayError("content_invalid", "这个 Sprite 不支持关键词")
    return contents


def _groups(cues, contents: list[tuple], lines: int, max_length: int | None) -> list[tuple[int, int, str, list[str], list[int]]]:
    """Group the checked cue contents of a multi-line text: up to `lines` per group, a new group at a sentence change.

    A new group also starts when one more line would make the joined text longer than `max_length` code points (the
    text parameter's limit) or start more than `LINE_START_MAX_FRAMES` after the group, so cues that are valid on
    their own never fail for being grouped. A group lasts from its first cue's start to its last cue's end (the gaps
    between them included), its text is the cues' texts one per line, its keywords the cues' keywords in order without
    repeats, and each line starts at its cue's start frame counted from the group's start.
    """
    groups: list[list[tuple]] = []
    for index, (cue, content) in enumerate(zip(cues, contents)):
        previous = cues[index - 1].sentence if index else 0
        group = groups[-1] if groups else []
        if (not group or len(group) == lines or cue.sentence and previous and cue.sentence != previous
                or max_length is not None and len("\n".join(item[2] for item in group)) + 1 + len(content[2]) > max_length
                or content[0] - group[0][0] > LINE_START_MAX_FRAMES):
            groups.append([])
        groups[-1].append(content)
    return [
        (group[0][0], group[-1][0] + group[-1][1] - group[0][0], "\n".join(item[2] for item in group),
         list(dict.fromkeys(word for item in group for word in item[3])), [item[0] - group[0][0] for item in group])
        for group in groups
    ]


def plan_sprite_render(message: pb.SpriteRenderInput, sprite: pb.PublishedSprite) -> Overlay:
    """Check the canvas, time, content and style, and place one instance per content on the whole effect.

    A text shown for less than `STATIC_TEXT_SECONDS` gets a `hold`: frozen on the middle frame of the previewed length
    the Sprite was accepted at (`hold.length`, which it reads as its length) and shown for the text's frames; the
    instance lasts the longer of the two, so a preview shorter than the text does not cut it. A multi-line text gets
    its lines' start frames, all 0 when held so the frozen frame shows every line, and the frame of its last line
    (`reveal_frame`) for the text fit. Every refusal is an `OverlayError` with a stable code; acceptance turns it into
    a 422.
    """
    canvas = sprite.canvas
    fps = canvas.fps
    if message.HasField("output") and (message.output.width, message.output.height, message.output.fps) != (canvas.width, canvas.height, fps):
        raise OverlayError("canvas_mismatch", f"output 必须与发布画布一致：{canvas.width}x{canvas.height}@{fps}")
    total = message.effect_total_frames
    if total < max(1, to_frames(MIN_RENDER_SECONDS, fps)):
        raise OverlayError("duration_invalid", f"效果时长不能短于 {MIN_RENDER_SECONDS:g} 秒")
    if total > to_frames(MAX_RENDER_SECONDS, fps):
        raise OverlayError("duration_too_long", f"效果时长不能超过 {MAX_RENDER_SECONDS:g} 秒")
    static = to_frames(STATIC_TEXT_SECONDS, fps) if sprite.kind == pb.SPRITE_KIND_TEXT else 0
    preview = max(1, canvas.preview_frames)
    instances = []
    schema = json.loads(sprite.config_schema_json or "{}")
    # 双语字效的下栏是同一句的英文，这个请求只带中文：按单行注入并清空英文，避免显示发布时的示例英文。
    bilingual = sprite.kind == pb.SPRITE_KIND_TEXT and is_bilingual(schema)
    lines = 1 if bilingual else line_count(schema) if sprite.kind == pb.SPRITE_KIND_TEXT else 1
    for start, length, text, keywords, starts in _contents(message, sprite, total):
        try:
            config, host = resolve(sprite, _editor(message, sprite, text, keywords))
        except ValueError as exc:
            raise OverlayError("placement_invalid", f"样式或内容不符合发布契约：{exc}") from None
        if bilingual:
            config[BILINGUAL_PROP] = ""
        instance = {"sprite_id": message.sprite_id, "code": sprite.tsx_code, "config": config, "host": host, "start_frame": start, "duration_frames": length}
        if length < static:
            instance.update(duration_frames=max(preview, length), hold={"frame": preview // 2, "frames": length, "length": preview})
            starts = [0]
        if lines > 1:
            # Content the host injects (not an operator parameter), padded with the last line's start for absent lines.
            config[LINE_STARTS_PROP] = starts + [starts[-1]] * (lines - len(starts))
            instance["reveal_frame"] = starts[-1]
        instances.append(instance)
    return Overlay(canvas.width, canvas.height, fps, total, instances)


async def render_sprite(renderer: Renderer, overlay: Overlay, directory: Path, *, chunk_concurrency: int | None = None,
                        frame_concurrency: int | None = None) -> Path:
    """单 Sprite 始终按 15 秒分块；完整时钟、拼接与成片校验共用整段叠加视频实现。"""
    return await render_overlay(renderer, overlay, directory, chunked=True, chunk_concurrency=chunk_concurrency, frame_concurrency=frame_concurrency)


def join_problem(overlay: Overlay, settings: Settings) -> str | None:
    """Name the joining tool when an effect needs more than one chunk and this host lacks it; shorter ones need none."""
    if len(overlay_ranges(overlay, settings, chunked=True)) == 1:
        return None
    return None if shutil.which("ffmpeg") else "ffmpeg"
