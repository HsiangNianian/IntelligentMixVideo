"""Plan one Sprite render from the bus's `SpriteRenderInput`: a fixed style, one content and a time.

The Sprite's style is its accepted code plus the editable style overrides and host transform of the placement;
the content is the request's `text` and `keywords`; the time is `effect_total_frames`, between 0.5 and 120
seconds, whatever length the user previewed when generating it. One request renders one segment of at most 30
seconds: the Sprite sees the whole effect length and the segment is cut out of it, so consecutive segments
continue the same animation. The plan runs in the overlay worker (`overlay_render.render_overlay`); the async
task, upload and callback are in `overlay_service.py`.
"""

from __future__ import annotations

from generated.imv.sprite.v1 import sprite_pb2 as pb
from pydantic import ValidationError

from ..template.schema import RemotionTrackEditor
from .overlay_render import Overlay, OverlayError, to_frames
from .placement import resolve
from .tool_validation import MAX_RENDER_SECONDS, MIN_RENDER_SECONDS

# One request renders at most this much of an effect; the bus splits longer effects into adjacent requests.
SEGMENT_SECONDS = 30


def _style(message: pb.SpriteRenderInput, sprite: pb.PublishedSprite) -> RemotionTrackEditor:
    """The placement editor: the style overrides plus the content, which only `text` and `keywords` may set."""
    content = {sprite.text_prop, sprite.keywords_prop} - {""}
    parameters = []
    for item in message.resolved_style:
        if item.key in content:
            raise OverlayError("placement_invalid", f"参数 {item.key} 是内容，只能通过 text 或 keywords 传入")
        case = item.value.WhichOneof("value")
        if case is None:
            raise OverlayError("placement_invalid", f"参数 {item.key} 没有给出值")
        value = {"values": list(item.value.string_list_value.values)} if case == "string_list_value" else getattr(item.value, case)
        parameters.append({"key": item.key, case: value})
    if message.text:
        parameters.append({"key": sprite.text_prop, "string_value": message.text})
    if message.keywords:
        parameters.append({"key": sprite.keywords_prop, "string_list_value": {"values": list(message.keywords)}})
    transform = {
        name: getattr(message.transform, name)
        for name in ("x", "y", "scale", "rotation", "opacity") if message.transform.HasField(name)
    }
    try:
        return RemotionTrackEditor(parameters=parameters, transform=transform or None)
    except ValidationError as exc:
        raise OverlayError("placement_invalid", f"样式或内容无效：{exc.errors()[0]['msg']}") from None


def _check_content(message: pb.SpriteRenderInput, sprite: pb.PublishedSprite) -> None:
    """A text effect needs its text; other kinds take no content, and only Sprites with keywords take keywords."""
    if sprite.kind == pb.SPRITE_KIND_TEXT:
        if not message.text:
            raise OverlayError("content_invalid", "字效必须提供 text")
    elif message.text:
        raise OverlayError("content_invalid", "只有字效接收 text")
    if message.keywords and not sprite.keywords_prop:
        raise OverlayError("content_invalid", "这个 Sprite 不支持关键词")


def plan_sprite_render(message: pb.SpriteRenderInput, sprite: pb.PublishedSprite) -> Overlay:
    """Check the canvas, time, segment, content and style, and place the Sprite for one segment.

    The Sprite's Sequence starts `effect_offset_frames` before the output and lasts the whole effect, so it reads
    the full length as `durationInFrames` and the output holds only this segment. Every refusal is an
    `OverlayError` with a stable code; acceptance turns it into a 422.
    """
    canvas = sprite.canvas
    fps = canvas.fps
    if message.HasField("output") and (message.output.width, message.output.height, message.output.fps) != (canvas.width, canvas.height, fps):
        raise OverlayError("canvas_mismatch", f"output 必须与发布画布一致：{canvas.width}x{canvas.height}@{fps}")
    total, offset, count = message.effect_total_frames, message.effect_offset_frames, message.render_frame_count
    if total < max(1, to_frames(MIN_RENDER_SECONDS, fps)):
        raise OverlayError("duration_invalid", f"效果时长不能短于 {MIN_RENDER_SECONDS:g} 秒")
    if total > to_frames(MAX_RENDER_SECONDS, fps):
        raise OverlayError("duration_too_long", f"效果时长不能超过 {MAX_RENDER_SECONDS:g} 秒")
    if not 0 < count <= to_frames(SEGMENT_SECONDS, fps):
        raise OverlayError("segment_invalid", f"每段渲染 1 帧到 {SEGMENT_SECONDS} 秒，超过 {SEGMENT_SECONDS} 秒的效果请分段请求")
    if offset + count > total:
        raise OverlayError("segment_invalid", "分段超出了效果时长")
    _check_content(message, sprite)
    try:
        config, host = resolve(sprite, _style(message, sprite))
    except ValueError as exc:
        raise OverlayError("placement_invalid", f"样式或内容不符合发布契约：{exc}") from None
    return Overlay(canvas.width, canvas.height, fps, count, [{
        "code": sprite.tsx_code, "config": config, "host": host,
        "start_frame": -offset, "duration_frames": total,
    }])
