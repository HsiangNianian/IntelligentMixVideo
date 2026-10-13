"""Render the Remotion placements of one request into a single transparent VP9 WebM.

`resolve_total_seconds` decides the video length (TTS audio wins, then an explicit duration, then the
latest fixed placement end); `plan_overlay` turns the placements and their publications into per-instance
frame ranges on one shared canvas; `render_overlay` runs the sealed sources in the isolated worker and
verifies the produced file with ffprobe. Persistence, upload and notification live in
`overlay_service.py`.
"""

import json
import logging
import math
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from generated.imv.sprite.v1 import sprite_pb2 as pb
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from ..template.schema import RemotionTrackEditor
from ..video_composition.media import ffprobe_json
from ..video_composition.schema import PositiveSeconds
from .placement import resolve
from .renderer import CPU_SECONDS, Renderer
from .settings import Settings

logger = logging.getLogger(__name__)
PROBE_SECONDS = 60  # ffprobe of the finished file (it decodes every frame to count them)
MAX_PLACEMENTS = 100  # the same cap as a template's tracks


class OverlayError(Exception):
    """A request or render failure with a stable code and message that are safe to show to the bus.

    A chained `__cause__` may hold private detail (renderer output); the service logs it and never publishes it.
    """

    def __init__(self, code: str, message: str) -> None:
        """Keep the code and the public message separately."""
        super().__init__(message)
        self.code, self.message = code, message


class PlacementEditor(BaseModel):
    """The `editor` of a template's `remotion` track, forwarded as is by the bus; only `remotion` may be set."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    remotion: RemotionTrackEditor | None = None


class OverlayPlacement(BaseModel):
    """One clip of the overlay: a published Sprite, the second it starts, an optional fixed length and its overrides.

    The request, the stored task and the planner share this model, so a recovered task renders exactly
    what was accepted. Placements are listed bottom layer first, like a template's `remotion` tracks.
    Numbers are strict like the composition API's: booleans and numeric strings are refused. `editor` holds the
    track's parameter overrides and host transform; without it the Sprite renders its published defaults.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    sprite_id: str = Field(min_length=1, max_length=64)
    start: float = Field(ge=0, allow_inf_nan=False, strict=True)
    duration: PositiveSeconds | None = Field(default=None, strict=True)
    # Without overrides the stored task keeps the shape it had before placements could carry them.
    editor: PlacementEditor | None = Field(default=None, exclude_if=lambda value: value is None)

    def resolved(self, sprite: pb.PublishedSprite) -> tuple[dict, dict]:
        """The merged configuration and host layer of this clip; an override outside the contract is `placement_invalid`."""
        try:
            return resolve(sprite, self.editor.remotion if self.editor else None)
        except ValueError as exc:
            raise OverlayError("placement_invalid", f"Sprite {self.sprite_id} 的参数覆盖无效：{exc}") from None


@dataclass(frozen=True)
class Overlay:
    """One render job: a shared canvas, the `frames` of output and the placed clips, bottom layer first.

    Clips keep their full length, so one may run past `frames`; `span` is what the composition must cover.
    """

    width: int
    height: int
    fps: int
    frames: int
    instances: list[dict]

    @property
    def span(self) -> int:
        """Composition length: the output or the latest clip end, so no clip is shortened by the composition."""
        return max([self.frames, *(item["start_frame"] + item["duration_frames"] for item in self.instances)])


def placement_seconds(sprite: pb.PublishedSprite, placement: OverlayPlacement) -> float:
    """Fixed duration of a placement: its own duration, otherwise the asset's own preview length."""
    if placement.duration is not None:
        return placement.duration
    return sprite.canvas.preview_frames / sprite.canvas.fps


async def audio_duration(url: str, timeout: float) -> float:
    """Read the complete container duration of the TTS audio; the subprocess is always reaped."""
    try:
        data = await ffprobe_json(url, timeout, "-show_entries", "format=duration")
        seconds = float(data["format"]["duration"])
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("invalid duration")
        return seconds
    except (OSError, TimeoutError, ValueError, KeyError, TypeError) as exc:
        # Only the type is logged: the URL and ffprobe's message may carry a signed query string.
        logger.warning("配音音频时长读取失败：%s", type(exc).__name__)
        raise OverlayError("audio_duration_invalid", "无法读取配音音频的有效时长") from None


def fixed_end_seconds(
    placements: list[OverlayPlacement], sprites: dict[str, pb.PublishedSprite],
) -> float | None:
    """Latest end among the fixed clips, or None when there are none."""
    ends = [item.start + placement_seconds(sprites[item.sprite_id], item) for item in placements]
    return max(ends) if ends else None


async def resolve_total_seconds(
    audio_url: str | None, duration: float | None,
    placements: list[OverlayPlacement], sprites: dict[str, pb.PublishedSprite], timeout: float,
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
    placements: list[OverlayPlacement], sprites: dict[str, pb.PublishedSprite],
) -> tuple[int, int, int]:
    """Width, height and fps shared by every placed asset; empty or mixed canvases are rejected."""
    if not placements:
        raise OverlayError("no_placements", "没有 Remotion 资产摆放")
    canvases = {
        (item.canvas.width, item.canvas.height, item.canvas.fps)
        for item in (sprites[placement.sprite_id] for placement in placements)
    }
    if len(canvases) != 1:
        raise OverlayError("canvas_mismatch", "模板内 Remotion 资产画布尺寸或帧率不一致")
    return canvases.pop()


def to_frames(seconds: float, fps: int) -> int:
    """Whole frames for a time, rounding half a frame up like the template timing (`template.timing`)."""
    return math.floor(seconds * fps + 0.5)


def plan_overlay(
    placements: list[OverlayPlacement], sprites: dict[str, pb.PublishedSprite],
    total_seconds: float, max_seconds: float,
) -> Overlay:
    """Place every asset on one canvas, in list order; starts past the end are dropped, later ends are cut.

    A clip keeps its full length: a Sprite reads its length from its own Sequence, so shrinking the Sequence
    would change animations computed from it. The output length does the cutting (`Overlay.span`). A clip is
    never longer than the longest overlay, which keeps a corrupt saved length from growing the composition.
    """
    width, height, fps = shared_canvas(placements, sprites)
    if not math.isfinite(total_seconds) or total_seconds <= 0:
        raise OverlayError("duration_invalid", "成片时长必须是大于 0 的有限数字")
    if total_seconds > max_seconds:
        raise OverlayError("duration_too_long", f"成片时长超过上限 {max_seconds:g} 秒")
    frames = max(1, to_frames(total_seconds, fps))
    instances = []
    for placement in placements:
        sprite = sprites[placement.sprite_id]
        if placement.start * fps >= frames:  # past the end; a huge saved start never reaches int arithmetic
            continue
        start_frame = to_frames(placement.start, fps)
        if start_frame >= frames:
            continue
        length = max(1, to_frames(min(placement_seconds(sprite, placement), max_seconds), fps))
        config, host = placement.resolved(sprite)
        instances.append({
            "code": sprite.tsx_code,
            "config": config,
            # The host layer moves, scales, rotates and fades the Sprite around its published content box.
            "host": host,
            "start_frame": start_frame,
            "duration_frames": length,
        })
    if not instances:
        raise OverlayError("no_placements", "所有 Remotion 资产的起点都晚于成片结尾")
    return Overlay(width, height, fps, frames, instances)


async def probe_overlay(path: Path, overlay: Overlay) -> None:
    """Check size, rate, frame count and the alpha flag of the rendered file; raise on any mismatch."""
    try:
        data = await ffprobe_json(
            str(path), PROBE_SECONDS, "-select_streams", "v:0", "-count_frames",
            "-show_entries", "stream=codec_name,width,height,avg_frame_rate,nb_read_frames:stream_tags",
            remote=False,
        )
        stream = data["streams"][0]
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


def environment_problem(settings: Settings) -> str | None:
    """Name the first tool or file an overlay render needs that this host lacks, or None when all exist.

    Acceptance refuses with a 503 instead of queueing a task that would only fail later; the isolated
    renderer runs on Linux with bubblewrap, prlimit, Node, a browser and the managed fonts.
    """
    for tool in ("ffprobe", "bwrap", "prlimit", "node"):
        if shutil.which(tool) is None:
            return tool
    for path in (settings.browser_executable, settings.font_regular, settings.font_bold):
        if not path.is_file():
            return str(path)
    return None


def overlay_cpu_seconds(timeout_seconds: float) -> int:
    """Per-process CPU cap of one overlay render: its wall-clock deadline times the host's cores.

    On a 1080x1920 overlay Chrome's software-GL process and the single VP9 encoder each used about 0.9 CPU
    seconds per frame, so a 120 s overlay needs thousands of CPU seconds in one process and the short workers'
    300 s cap would end it far before its timeout. No process can use more than this within the deadline, so
    the cap still bounds a runaway without cutting a healthy render short.
    """
    return max(CPU_SECONDS, math.ceil(timeout_seconds * (os.cpu_count() or 1)))


async def run_overlay_worker(renderer: Renderer, overlay: Overlay, directory: Path, ranges: list[list[int]] | None = None) -> None:
    """Run the isolated worker in `directory`: one `overlay.webm`, or one `chunk-<i>.webm` per inclusive frame range."""
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
            "duration_in_frames": overlay.span, "frames": overlay.frames,
        },
        "instances": overlay.instances,
        "browser": renderer.worker_browser_path(),
        **({"ranges": ranges} if ranges else {}),
    }), encoding="utf-8")
    try:
        await renderer.run_worker(
            directory, worker="overlay-worker.mjs", timeout_seconds=settings.overlay_render_timeout_seconds,
            cpu_seconds=overlay_cpu_seconds(settings.overlay_render_timeout_seconds),
        )
    except (RuntimeError, TimeoutError) as exc:
        # The worker log tail holds sandbox paths and source excerpts: only the chained cause carries it.
        message = "叠加视频渲染超时" if isinstance(exc, TimeoutError) else "叠加视频渲染失败"
        raise OverlayError("render_failed", message) from exc


async def render_overlay(renderer: Renderer, overlay: Overlay, directory: Path) -> Path:
    """Run the isolated worker in `directory` and return the verified `overlay.webm`."""
    await run_overlay_worker(renderer, overlay, directory)
    output = directory / "overlay.webm"
    if not output.is_file() or output.stat().st_size == 0:
        raise OverlayError("render_failed", "渲染器没有产出视频")
    await probe_overlay(output, overlay)
    return output
