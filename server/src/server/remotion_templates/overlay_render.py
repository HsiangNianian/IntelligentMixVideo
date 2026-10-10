"""Render every Remotion placement of one cloud template into a single transparent VP9 WebM.

`resolve_total_seconds` decides the video length (TTS audio wins, then an explicit duration, then the
latest fixed placement end); `plan_overlay` turns bindings and publications into per-instance frame
ranges on one shared canvas; `render_overlay` runs the sealed sources in the isolated worker and
verifies the produced file with ffprobe. Persistence, upload and notification live in
`overlay_service.py`.
"""

import asyncio
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from generated.imv.sprite.v1 import sprite_pb2 as pb

from .renderer import Renderer


class OverlayError(Exception):
    """A request or render failure with a stable code that is safe to show to the bus."""

    def __init__(self, code: str, message: str) -> None:
        """Keep the code and the public message separately."""
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class Overlay:
    """One render job: shared canvas plus instances already clipped to `frames`, bottom layer first."""

    width: int
    height: int
    fps: int
    frames: int
    instances: list[dict]


def placement_seconds(sprite: pb.PublishedSprite, placement: pb.SpritePlacement) -> float:
    """Fixed duration of a placement: its saved duration, otherwise the asset's own preview length."""
    if placement.HasField("duration"):
        return placement.duration
    return sprite.canvas.preview_frames / sprite.canvas.fps


async def audio_duration(url: str, timeout: float) -> float:
    """Read the complete container duration of the TTS audio; the subprocess is always reaped."""
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-protocol_whitelist", "http,https,tcp,tls,httpproxy",
            "-rw_timeout", str(int(timeout * 1_000_000)), "-show_entries", "format=duration",
            "-of", "json", url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        output, _ = await asyncio.wait_for(process.communicate(), timeout)
        if process.returncode:
            raise ValueError("ffprobe failed")
        seconds = float(json.loads(output)["format"]["duration"])
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("invalid duration")
        return seconds
    except (OSError, TimeoutError, ValueError, KeyError, TypeError):
        raise OverlayError("audio_duration_invalid", "无法读取配音音频的有效时长") from None
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()


def fixed_end_seconds(
    placements: list[pb.SpritePlacement], sprites: dict[str, pb.PublishedSprite],
) -> float | None:
    """Latest end among the fixed clips, or None when there are none."""
    ends = [item.start + placement_seconds(sprites[item.sprite_id], item) for item in placements]
    return max(ends) if ends else None


async def resolve_total_seconds(
    audio_url: str | None, duration: float | None,
    placements: list[pb.SpritePlacement], sprites: dict[str, pb.PublishedSprite], timeout: float,
) -> float:
    """TTS audio decides the length; otherwise the requested duration, then the last fixed end."""
    if audio_url:
        return await audio_duration(audio_url, timeout)
    if duration is not None:
        return duration
    fallback = fixed_end_seconds(placements, sprites)
    if fallback is None:
        raise OverlayError("duration_unknown", "无法确定成片时长：请提供 audioUrl 或 duration")
    return fallback


def shared_canvas(
    placements: list[pb.SpritePlacement], sprites: dict[str, pb.PublishedSprite],
) -> tuple[int, int, int]:
    """Width, height and fps shared by every placed asset; empty or mixed canvases are rejected."""
    if not placements:
        raise OverlayError("no_placements", "模板没有 Remotion 资产摆放")
    canvases = {
        (item.canvas.width, item.canvas.height, item.canvas.fps)
        for item in (sprites[placement.sprite_id] for placement in placements)
    }
    if len(canvases) != 1:
        raise OverlayError("canvas_mismatch", "模板内 Remotion 资产画布尺寸或帧率不一致")
    return canvases.pop()


def plan_overlay(
    placements: list[pb.SpritePlacement], sprites: dict[str, pb.PublishedSprite],
    total_seconds: float, max_seconds: float,
) -> Overlay:
    """Place every asset on one canvas; starts past the end are dropped, later ends are cut by the total.

    A clip keeps its full length: Remotion reports a Sequence's length as the asset's own duration, so
    shrinking it would change animations computed from it. The composition length does the cutting.
    """
    width, height, fps = shared_canvas(placements, sprites)
    if not math.isfinite(total_seconds) or total_seconds <= 0:
        raise OverlayError("duration_invalid", "成片时长必须是大于 0 的有限数字")
    if total_seconds > max_seconds:
        raise OverlayError("duration_too_long", f"成片时长超过上限 {max_seconds:g} 秒")
    frames = max(1, round(total_seconds * fps))
    instances = []
    for placement in sorted(placements, key=lambda item: item.order):
        sprite = sprites[placement.sprite_id]
        start_frame = round(placement.start * fps)
        if start_frame >= frames:
            continue
        length = max(1, round(placement_seconds(sprite, placement) * fps))
        instances.append({
            "code": sprite.tsx_code,
            "config": json.loads(sprite.default_config_json),
            "start_frame": start_frame,
            "duration_frames": length,
            # Sprites derive animation ranges from useVideoConfig().durationInFrames of their own composition.
            "own_frames": sprite.canvas.preview_frames,
        })
    if not instances:
        raise OverlayError("no_placements", "所有 Remotion 资产的起点都晚于成片结尾")
    return Overlay(width, height, fps, frames, instances)


async def probe_overlay(path: Path, overlay: Overlay) -> None:
    """Check size, rate, frame count and the alpha flag of the rendered file; raise on any mismatch."""
    process = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
        "-show_entries", "stream=codec_name,width,height,avg_frame_rate,nb_read_frames:stream_tags",
        "-of", "json", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(60):
            output, _ = await process.communicate()
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()
    try:
        stream = json.loads(output)["streams"][0]
        numerator, denominator = map(float, stream["avg_frame_rate"].split("/"))
        tags = {key.upper(): str(value) for key, value in stream.get("tags", {}).items()}
        correct = (
            stream["codec_name"] == "vp9"
            and (stream["width"], stream["height"]) == (overlay.width, overlay.height)
            and abs(numerator / denominator - overlay.fps) < 0.001
            and int(stream["nb_read_frames"]) == overlay.frames
            and tags.get("ALPHA_MODE") == "1"
        )
    except (ValueError, KeyError, IndexError, ZeroDivisionError, TypeError):
        correct = False
    if not correct:
        raise OverlayError("render_invalid", "渲染结果与请求的规格或透明通道不一致")


async def render_overlay(renderer: Renderer, overlay: Overlay, directory: Path) -> Path:
    """Run the isolated worker in `directory` and return the verified `overlay.webm`."""
    settings = renderer.settings
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ".tmp").mkdir(exist_ok=True)
    public = directory / "public"
    public.mkdir(exist_ok=True)
    for weight, font in ((400, settings.font_regular), (700, settings.font_bold)):
        (public / f"font-{weight}.ttc").write_bytes(font.read_bytes())
    (directory / "request.json").write_text(json.dumps({
        "composition": {
            "width": overlay.width, "height": overlay.height, "fps": overlay.fps,
            "duration_in_frames": overlay.frames,
        },
        "instances": overlay.instances,
        "browser": renderer.worker_browser_path(),
    }), encoding="utf-8")
    try:
        await renderer.run_worker(
            directory, worker="overlay-worker.mjs", timeout_seconds=settings.overlay_render_timeout_seconds,
        )
    except (RuntimeError, TimeoutError) as exc:
        detail = re.sub(r"\s+", " ", str(exc))[:300]
        raise OverlayError("render_failed", f"叠加视频渲染失败：{detail}") from None
    output = directory / "overlay.webm"
    if not output.is_file() or output.stat().st_size == 0:
        raise OverlayError("render_failed", "渲染器没有产出视频")
    await probe_overlay(output, overlay)
    return output
