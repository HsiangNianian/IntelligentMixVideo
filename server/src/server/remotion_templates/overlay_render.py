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
from .fonts import FontStore
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
            "sprite_id": placement.sprite_id,
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


def _fit_frames(instance: dict, end: int) -> tuple[list[int], int]:
    """The frames at which a placement's text is shown, and the length its Sprite plays at.

    A held text (shown for less than the static threshold) is frozen on one frame of its previewed length; any other
    one plays its own length and is measured on the first, last and three frames in between of the part that is
    rendered: a clip running past the video's `end` frame keeps its length, but its frames after `end` never show.
    A multi-line text is complete only once its last line appears (`reveal_frame`), so only the frames from there on
    are measured.
    """
    hold = instance.get("hold")
    if hold:
        return [hold["frame"]], hold["length"]
    length = instance["duration_frames"]
    shown = max(1, min(length, end - instance["start_frame"]))
    first = min(shown - 1, instance.get("reveal_frame", 0))
    return sorted({first + math.floor((shown - 1 - first) * ratio) for ratio in (0, 0.25, 0.5, 0.75, 1)}), length


async def require_fonts(overlay: Overlay) -> None:
    """Make every library font the code or parameters name available on this host before laying out or rendering.

    A `fontFamily` value is always a library family (`placement.resolve` checks it when the placement is accepted),
    so the only case left is a library font this host has not cached yet: it is fetched from the object store, and a
    font that still cannot be had fails the render instead of being drawn in the fallback font.
    """
    store = FontStore.active
    if store is None:
        return
    named = store.named(json.dumps(overlay.instances, ensure_ascii=False))
    if named and await store.require(named):
        raise OverlayError("font_unavailable", "渲染需要的字体尚未就绪，请稍后重试")


async def fit_texts(renderer: Renderer, overlay: Overlay, sprites: dict[str, pb.PublishedSprite], directory: Path) -> None:
    """Make every text effect show the text a render really passes, whatever its length.

    A generated effect cannot know how long a title or subtitle the bus will send, so the host measures it: for each
    text Sprite the isolated browser mounts each distinct content with its own values, at the length its Sprite plays
    (which it reads as `durationInFrames`) and where its host transform moves, scales and rotates it on the canvas,
    and, when the text is cut off, leaves the canvas or is unreadably small, lowers the `fontSize` to the largest
    whole size (down to the published minimum) at which it fits. Text that already fits keeps the size it was given.
    Text that does not fit even at the smallest size fails the render with `text_too_long`, so the bus gets a clear
    reason instead of a clipped text. Only effects with the required `text` and `fontSize` parameters take part, all
    of them in one browser run (the render slot waits for it, so its deadline grows with what it measures); a Sprite
    that cannot be mounted is left to the render, which reports its own failure.

    Instances name their Sprite with `sprite_id` (others are not measured) and are measured with the `code` the render
    runs, on their own canvas (`width`, `height`, which the video scales to cover) when they have one. The text is the
    instance's `text` when it carries one (a video composition fills it into several parameter paths), otherwise the
    Sprite's text parameter. An in-place keyword (`anchored`) is not measured: the overlay worker sizes it to the
    keyword hit of its body, which is measured with the body.
    """
    from .tool_validation import FitSprite, ToolValidator, ValidationUnavailable
    from .tools.contracts import ComponentDefinition

    await require_fonts(overlay)
    groups: dict[tuple[str, str], dict[str, dict]] = {}  # (sprite, code) -> distinct values -> item
    members: dict[tuple[tuple[str, str], str], list[int]] = {}  # ((sprite, code), distinct values) -> instance indexes
    for index, instance in enumerate(overlay.instances):
        sprite = sprites.get(instance.get("sprite_id", ""))
        if sprite is None or sprite.kind != pb.SPRITE_KIND_TEXT or "anchored" in instance:
            continue
        config = instance["config"]
        size = json.loads(sprite.config_schema_json).get("properties", {}).get("fontSize", {})
        text = instance["text"] if "text" in instance else config.get(sprite.text_prop or "text")
        if not isinstance(text, str) or not text.strip() or not isinstance(config.get("fontSize"), (int, float)) or "minimum" not in size:
            continue
        frames, length = _fit_frames(instance, overlay.frames)
        # Opacity does not move the text; every other transform field does, so it tells contents apart.
        transform = {name: value for name, value in instance["host"]["transform"].items() if name != "opacity"}
        group = (instance["sprite_id"], instance["code"])
        key = json.dumps([config, frames, length, transform], sort_keys=True, ensure_ascii=False)
        members.setdefault((group, key), []).append(index)
        groups.setdefault(group, {}).setdefault(key, {
            "index": index, "values": config, "text": text, "size_key": "fontSize", "min": math.ceil(size["minimum"]),
            "frames": frames, "length": length, "transform": transform,
        })
    if not groups:
        return
    measured = [(group, list(distinct), list(distinct.values())) for group, distinct in groups.items()]
    items = [item for *_, distinct in measured for item in distinct]
    # One browser run measures every distinct text: a few seconds for each, and for loading each Sprite.
    timeout = max(renderer.settings.render_timeout_seconds, 5 * len(items) + 10 * len(measured))
    fit_sprites = []
    for (sprite_id, code), _, distinct in measured:
        first = overlay.instances[distinct[0]["index"]]
        fit_sprites.append(FitSprite(
            ComponentDefinition(
                code=code, parameter_schema=json.loads(sprites[sprite_id].config_schema_json),
                default_parameters=json.loads(sprites[sprite_id].default_config_json),
            ),
            {"width": first.get("width", overlay.width), "height": first.get("height", overlay.height)},
            first["host"]["box"],
            distinct,
        ))
    try:
        answers = await ToolValidator(renderer, directory).fit_texts(
            fit_sprites,
            {"width": overlay.width, "height": overlay.height, "fps": overlay.fps, "duration_frames": max(item["length"] for item in items)},
            # The CPU cap grows with the deadline, or a long run of binary searches ends on it before the timeout.
            timeout_seconds=timeout, cpu_seconds=overlay_cpu_seconds(timeout),
        )
    except ValidationUnavailable as exc:
        raise OverlayError("render_failed", "叠加视频渲染失败") from exc
    for (group, keys, distinct), fits in zip(measured, answers):
        for key, item, fit in zip(keys, distinct, fits):
            if not fit["fits"]:
                raise OverlayError("text_too_long", "文字过长：缩到最小字号仍无法完整显示，请缩短文字")
            if fit["size"] != item["values"]["fontSize"]:
                logger.info("叠加渲染 Sprite %s 的文字（%d 字）字号 %s 缩小为 %s", group[0], len(item["text"]), item["values"]["fontSize"], fit["size"])
                for index in members[(group, key)]:
                    # keywordSize is absolute, so the worker scales it with the text (see `valuesAtSize` there).
                    overlay.instances[index]["config"] = {**item["values"], "fontSize": fit["size"], **({"keywordSize": fit["keyword_size"]} if "keyword_size" in fit else {})}
            if shift := fit.get("shift"):
                logger.info("叠加渲染 Sprite %s 的文字（%d 字）在最小字号下仍出画布，位置平移 (%s, %s)", group[0], len(item["text"]), shift["x"], shift["y"])
                for index in members[(group, key)]:
                    overlay.instances[index]["host"] = nudged_host(overlay.instances[index]["host"], shift)


def nudged_host(host: dict, shift: dict) -> dict:
    """A copy of `host` moved by `shift` canvas pixels: its transform's content-box centre (x, y) moves by that much.

    Only an axis that moves is written; an unset coordinate starts at the published content box centre, as the host
    layer reads it. The host is replaced, never edited: a video composition gives every cue of a placement the same
    host object, and the saved placement is not touched either.
    """
    box, transform = host["box"], host["transform"]
    centre = {"x": box["x"] + box["width"] / 2, "y": box["y"] + box["height"] / 2}
    return {**host, "transform": {**transform, **{axis: transform.get(axis, centre[axis]) + move for axis, move in shift.items() if move}}}


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
    await require_fonts(overlay)
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
