"""Build and cache isolated interactive previews from immutable published Sprite source."""

import asyncio
import hashlib
import json
import os
import shutil
import signal
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from generated.imv.sprite.v1 import sprite_pb2 as pb

from ..remotion_templates.renderer import Renderer
from ..remotion_templates.settings import load_settings


def _control(item: pb.SpriteParameter) -> dict:
    """Expose only scalar authoring rules to the trusted browser host."""
    field = item.default_value.WhichOneof("value")
    result = {
        "key": item.key,
        "access": item.access,
        "defaultValue": getattr(item.default_value, field),
    }
    if item.HasField("minimum"):
        result["minimum"] = item.minimum
    if item.HasField("maximum"):
        result["maximum"] = item.maximum
    return result


async def interactive_script(sprite: pb.PublishedSprite) -> str:
    """Reuse a fingerprinted bundle; execute esbuild only inside the renderer sandbox."""
    settings = load_settings()
    renderer = settings.renderer_dir
    preview = {
        "config": json.loads(sprite.default_config_json),
        "composition": {
            "width": sprite.canvas.width,
            "height": sprite.canvas.height,
            "fps": sprite.canvas.fps,
            "duration_in_frames": sprite.canvas.preview_frames,
        },
        "textProp": sprite.text_prop,
        "keywordsProp": sprite.keywords_prop,
        "parameters": [_control(item) for item in sprite.parameters],
        "animationFrames": sprite.animation_frames,
        "staticFrame": sprite.static_frame,
    }
    fingerprint = hashlib.sha256(
        sprite.tsx_code.encode()
        + json.dumps(preview, sort_keys=True, ensure_ascii=False).encode()
        + (renderer / "sprite-preview-host.tsx").read_bytes()
        + (renderer / "sprite-preview-worker.mjs").read_bytes()
        + (renderer / "bun.lock").read_bytes()
    ).hexdigest()
    cache = settings.data_dir / "published_sprites" / "interactive" / f"{sprite.sprite_id}-{fingerprint}.js"
    if cache.is_file():
        return cache.read_text(encoding="utf-8")
    directory = Path(tempfile.mkdtemp(prefix="imv-sprite-preview-"))
    try:
        (directory / "request.json").write_text(
            json.dumps({"code": sprite.tsx_code, "preview": preview}, ensure_ascii=False),
            encoding="utf-8",
        )
        (directory / ".tmp").mkdir()
        renderer_service = Renderer(settings)
        with (directory / "worker.log").open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                *renderer_service.command(directory, worker="sprite-preview-worker.mjs"),
                stdout=log, stderr=log, start_new_session=True,
                cwd=directory,
                env=renderer_service.worker_environment(directory),
            )
            try:
                await asyncio.wait_for(process.wait(), settings.render_timeout_seconds)
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
        report = json.loads((directory / "renderer.json").read_text())
        if process.returncode or report.get("checks") != [{"name": "interactive_bundle", "status": "pass"}]:
            raise ValueError("Sprite 交互预览构建失败")
        script = (directory / "interactive.js").read_text(encoding="utf-8")
        if not script or len(script) > 10_000_000:
            raise ValueError("Sprite 交互预览产物无效")
        cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache.with_name(f"{cache.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(script, encoding="utf-8")
            os.replace(temporary, cache)
        finally:
            temporary.unlink(missing_ok=True)
        return script
    except (OSError, ValueError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(409, "Sprite 交互预览暂不可用") from exc
    finally:
        shutil.rmtree(directory, ignore_errors=True)
