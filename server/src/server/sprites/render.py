"""Bind trusted business values to an accepted Sprite and render a verified alpha WebM in isolation."""

import asyncio
import json
import os
import shutil
import signal
import tempfile
from math import isfinite
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException
from generated.imv.sprite.v1 import sprite_pb2 as pb
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from ..remotion_templates.renderer import Renderer
from ..remotion_templates.keywords import literal_ranges
from ..remotion_templates.settings import load_settings
from .store import get_sprite


def bound_config(sprite: pb.PublishedSprite, request: pb.SpriteRenderInput) -> dict:
    """Accept only declared style controls; replace copy solely with the bus's business text."""
    if request.keywords and not sprite.keywords_prop:
        raise HTTPException(422, "此 Sprite 不支持关键词高亮")
    if len(request.keywords) > 20 or any(
        not word or len(word) > 100 for word in request.keywords
    ):
        raise HTTPException(422, "关键词数量或长度无效")
    if sprite.kind == pb.SPRITE_KIND_TEXT:
        if not request.text:
            raise HTTPException(422, "业务文字不能为空")
    elif request.text:
        raise HTTPException(422, "视觉 Sprite 不接收业务文字")
    config = json.loads(sprite.default_config_json)
    controls = {item.key: item for item in sprite.parameters}
    used = set()
    for override in request.resolved_style:
        control = controls.get(override.key)
        if (
            override.key in used
            or control is None
            or control.access != pb.OPERATOR_ACCESS_VISIBLE_EDITABLE
            or not override.HasField("value")
        ):
            raise HTTPException(422, "Sprite 样式参数无效或重复")
        used.add(override.key)
        field = override.value.WhichOneof("value")
        if field != control.default_value.WhichOneof("value"):
            raise HTTPException(422, "Sprite 样式参数类型不符")
        value = getattr(override.value, field)
        if field == "number_value" and not isfinite(value):
            raise HTTPException(422, "Sprite 样式数值无效")
        config[override.key] = value
    if sprite.kind == pb.SPRITE_KIND_TEXT:
        config[sprite.text_prop] = request.text[:2000]
    try:
        Draft202012Validator(json.loads(sprite.config_schema_json)).validate(config)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, "Sprite 输入不符合发布版本参数约束") from exc
    if sprite.keywords_prop:
        config[sprite.keywords_prop] = literal_ranges(
            config[sprite.text_prop], list(request.keywords)
        )
    return config


async def _probe(path: Path, request: pb.SpriteRenderInput) -> None:
    """Check VP9 dimensions, frame rate, count and actual decoded transparency."""
    probe = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,avg_frame_rate,nb_read_frames:stream_tags=alpha_mode",
        "-of", "json", str(path), stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        output, _ = await asyncio.wait_for(probe.communicate(), 30)
        stream = json.loads(output)["streams"][0]
        numerator, denominator = map(int, stream["avg_frame_rate"].split("/"))
        expected = request.output
        if (
            probe.returncode != 0
            or stream["codec_name"] != "vp9"
            or stream["width"] != expected.width
            or stream["height"] != expected.height
            or stream.get("tags", {}).get("alpha_mode") != "1"
            or numerator / denominator != expected.fps
            or int(stream["nb_read_frames"])
            != request.render_frame_count
        ):
            raise ValueError("VP9 Alpha 视频规格与请求不符")
    finally:
        if probe.returncode is None:
            probe.kill()
            await probe.wait()
    duration = request.render_frame_count
    sample = (
        f"select=eq(n\\,0)+eq(n\\,{duration // 2})+"
        f"eq(n\\,{duration - 1}),scale=256:256:flags=neighbor"
    )
    decoder = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-c:v", "libvpx-vp9", "-i", str(path),
        "-vf", sample, "-fps_mode", "vfr", "-frames:v", "3",
        "-f", "rawvideo", "-pix_fmt", "rgba", "-",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        pixels, _ = await asyncio.wait_for(decoder.communicate(), 30)
        frame_bytes = 256 * 256 * 4
        if decoder.returncode != 0 or not pixels or len(pixels) % frame_bytes:
            raise ValueError("透明视频解码失败")
        if min(pixels[3::4]) == 255:
            raise ValueError("Sprite 采样帧不含透明像素")
    finally:
        if decoder.returncode is None:
            decoder.kill()
            await decoder.wait()


def _render_window(request: pb.SpriteRenderInput) -> tuple[int, int, int]:
    """Validate one effect-relative chunk and return total, offset and output frames."""
    duration = request.render_frame_count
    if duration <= 0 or duration > min(1800, 30 * request.output.fps):
        raise HTTPException(422, "单次 Sprite 渲染须至少 1 帧，且不超过 30 秒或 1800 帧")
    total = request.effect_total_frames
    offset = request.effect_offset_frames
    if total == 0 or total > 216_000 or offset + duration > total:
        raise HTTPException(422, "Sprite 效果区间与本次渲染窗口不匹配")
    return total, offset, duration


async def render(request: pb.SpriteRenderInput) -> Path:
    """Produce a temporary WebM for the bus; the caller owns deletion after transfer."""
    try:
        sprite = get_sprite(UUID(request.sprite_id))
    except ValueError as exc:
        raise HTTPException(422, "Sprite ID 无效") from exc
    canvas = sprite.canvas
    if (
        request.output.width != canvas.width
        or request.output.height != canvas.height
        or request.output.fps != canvas.fps
    ):
        raise HTTPException(422, "输出画布须与发布 Sprite 一致")
    logical_total, offset, duration = _render_window(request)
    config = bound_config(sprite, request)
    settings = load_settings()
    directory = Path(tempfile.mkdtemp(prefix="imv-sprite-"))
    try:
        public = directory / "public"
        public.mkdir()
        (directory / ".tmp").mkdir()
        for weight, font in ((400, settings.font_regular), (700, settings.font_bold)):
            shutil.copyfile(font, public / f"font-{weight}.ttc")
        renderer = Renderer(settings)
        (directory / "request.json").write_text(
            json.dumps({
                "mode": "sprite_render", "code": sprite.tsx_code,
                "config": config, "composition": {
                    "width": canvas.width, "height": canvas.height,
                    "fps": canvas.fps, "duration_in_frames": logical_total,
                }, "browser": renderer.worker_browser_path(),
                "frame_range": [offset, offset + duration - 1],
                **({"static_frame": sprite.static_frame}
                   if sprite.keywords_prop and sprite.animation_frames > logical_total else {}),
            }, ensure_ascii=False), encoding="utf-8",
        )
        with (directory / "worker.log").open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                *renderer.command(directory), stdout=log, stderr=log,
                cwd=directory,
                start_new_session=True,
                env=renderer.worker_environment(directory),
            )
            try:
                await asyncio.wait_for(process.wait(), settings.render_timeout_seconds)
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
        checks = json.loads((directory / "renderer.json").read_text())["checks"]
        if process.returncode or not checks or any(item["status"] != "pass" for item in checks):
            raise ValueError("Sprite 隔离渲染未通过源码、编译或媒体检查")
        path = directory / "sprite.webm"
        await _probe(path, request)
        return path
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
