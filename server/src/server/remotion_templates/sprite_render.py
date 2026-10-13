"""Render one Sprite for the bus (`SpriteRenderInput`) as one complete video: a fixed style, its content and a time.

The Sprite's style is its accepted code plus the editable style overrides and host transform of the placement.
The content is either one `text` with `keywords` shown for the whole effect (a title) or timed `cues` (subtitles,
each shown once in its own frames, so the Sprite reads the cue's length as `durationInFrames`). A text shown for
less than `STATIC_TEXT_SECONDS` plays no entrance or exit: the Sprite runs at its previewed length, frozen on the
middle frame, for the text's frames, which avoids flicker and code that cannot animate in so few frames. The time is
`effect_total_frames`, 0.5 to 120 seconds, whatever length the user previewed when generating the Sprite.
`plan_sprite_render` checks the request and places the instances on the whole effect; `render_sprite` renders it
in chunks of at most 30 seconds in the overlay worker, each instance keeping its full length across chunks so
the animation continues, and joins the chunks losslessly into one WebM. The async task, upload and callback are
in `overlay_service.py`.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import replace
from pathlib import Path

from generated.imv.sprite.v1 import sprite_pb2 as pb
from pydantic import ValidationError

from ..template.schema import RemotionTrackEditor
from .overlay_render import Overlay, OverlayError, probe_overlay, render_overlay, to_frames
from .placement import resolve
from .renderer import Renderer
from .tool_validation import MAX_RENDER_SECONDS, MIN_RENDER_SECONDS

# The worker renders at most this much in one run; longer effects are rendered chunk by chunk and joined.
CHUNK_SECONDS = 30
# Joining copies the encoded chunks without re-encoding, so it is quick even for the longest effect.
JOIN_SECONDS = 120
# A text effect shown for less than this holds still instead of animating in and out: an entrance plus an exit
# usually take 0.6 to 1.3 seconds, so a shorter subtitle would hardly be fully visible and would flicker.
STATIC_TEXT_SECONDS = 1.5


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


def _contents(message: pb.SpriteRenderInput, sprite: pb.PublishedSprite, total: int) -> list[tuple[int, int, str, list[str]]]:
    """The shown contents as (start frame, frames, text, keywords) on the whole effect.

    A text effect takes `text` for the whole effect or ordered, non-overlapping, non-empty `cues`; other kinds take
    no content and render once over the whole effect.
    """
    if sprite.kind != pb.SPRITE_KIND_TEXT:
        if message.text or message.keywords or message.cues:
            raise OverlayError("content_invalid", "只有字效接收 text、keywords 或 cues")
        return [(0, total, "", [])]
    if bool(message.text) == bool(message.cues):
        raise OverlayError("content_invalid", "字效必须提供 text 或 cues 之一")
    if message.text:
        contents = [(0, total, message.text, list(message.keywords))]
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
            contents.append((cue.start_frame, cue.end_frame - cue.start_frame, cue.text, list(cue.keywords)))
            end = cue.end_frame
    if any(keywords for *_, keywords in contents) and not sprite.keywords_prop:
        raise OverlayError("content_invalid", "这个 Sprite 不支持关键词")
    return contents


def plan_sprite_render(message: pb.SpriteRenderInput, sprite: pb.PublishedSprite) -> Overlay:
    """Check the canvas, time, content and style, and place one instance per content on the whole effect.

    A text shown for less than `STATIC_TEXT_SECONDS` gets a `hold`: its instance lasts the previewed length the
    Sprite was accepted at and is frozen on its middle frame for the text's frames. Every refusal is an
    `OverlayError` with a stable code; acceptance turns it into a 422.
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
    for start, length, text, keywords in _contents(message, sprite, total):
        try:
            config, host = resolve(sprite, _editor(message, sprite, text, keywords))
        except ValueError as exc:
            raise OverlayError("placement_invalid", f"样式或内容不符合发布契约：{exc}") from None
        instance = {"code": sprite.tsx_code, "config": config, "host": host, "start_frame": start, "duration_frames": length}
        if length < static:
            instance.update(duration_frames=preview, hold={"frame": preview // 2, "frames": length})
        instances.append(instance)
    return Overlay(canvas.width, canvas.height, fps, total, instances)


def shown_frames(instance: dict) -> int:
    """How many frames an instance is visible: its held frames, otherwise its whole length."""
    return instance["hold"]["frames"] if "hold" in instance else instance["duration_frames"]


def chunks(overlay: Overlay) -> list[Overlay]:
    """Split the whole effect into consecutive renders of at most `CHUNK_SECONDS`.

    Each chunk keeps the instances that show in it at their full length, shifted to the chunk's first frame (an
    instance that began earlier starts before it), so an animation continues across chunks instead of restarting.
    A held instance shows only its `hold` frames. A chunk without any instance renders transparent frames.
    """
    size = max(1, to_frames(CHUNK_SECONDS, overlay.fps))
    parts = []
    for first in range(0, overlay.frames, size):
        count = min(size, overlay.frames - first)
        shown = [
            {**item, "start_frame": item["start_frame"] - first}
            for item in overlay.instances
            if item["start_frame"] < first + count and item["start_frame"] + shown_frames(item) > first
        ]
        parts.append(replace(overlay, frames=count, instances=shown))
    return parts


async def render_sprite(renderer: Renderer, overlay: Overlay, directory: Path) -> Path:
    """Render every chunk in the isolated worker and join them into the verified `sprite.webm` in `directory`."""
    paths = [await render_overlay(renderer, part, directory / f"chunk-{index}") for index, part in enumerate(chunks(overlay))]
    if len(paths) == 1:
        return paths[0]
    listing = directory / "chunks.txt"
    listing.write_text("".join(f"file '{path}'\n" for path in paths), encoding="utf-8")
    output = directory / "sprite.webm"
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(output),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(JOIN_SECONDS):
            _, stderr = await process.communicate()
    except TimeoutError:
        process.kill()
        await process.wait()
        raise OverlayError("render_failed", "字效视频拼接超时") from None
    if process.returncode != 0 or not output.is_file():
        raise OverlayError("render_failed", "字效视频拼接失败") from RuntimeError(stderr.decode(errors="replace")[-2000:])
    # The joined file must hold every frame of the effect with its alpha channel, like each chunk.
    await probe_overlay(output, overlay)
    return output


def join_problem() -> str | None:
    """Name the joining tool when this host lacks it; a single Sprite render needs it besides the overlay tools."""
    return None if shutil.which("ffmpeg") else "ffmpeg"
