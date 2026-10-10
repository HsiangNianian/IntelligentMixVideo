"""Durable async tasks that render a cloud template's Remotion placements and publish the video to ZOS.

Accepting a task validates the bindings and stores it in the Remotion SQLite database; a background
asyncio task renders (one at a time), uploads to ZOS, saves the result and notifies the callback.
Unfinished tasks are re-run by `resume()` after a restart, and the terminal notification is retried
with fixed delays. The rendering and planning rules live in `overlay_render.py`.
"""

import asyncio
import json
import logging
import shutil
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException
from generated.imv.sprite.v1 import sprite_pb2 as pb
from google.protobuf.json_format import ParseDict

from ..video_composition.settings import ZosSettings
from ..video_composition.zos import upload_public
from . import sprites
from .overlay_render import (
    OverlayError,
    fixed_end_seconds,
    plan_overlay,
    render_overlay,
    resolve_total_seconds,
    shared_canvas,
)
from .renderer import Renderer
from .settings import Settings
from .store import NotFound, Store

logger = logging.getLogger(__name__)
NOTIFICATION_RETRY_DELAYS = (5, 15, 45)
HTTP_TIMEOUT_SECONDS = 30
TERMINAL = ("succeeded", "failed")


class OverlayRenders:
    """Own the task table rows, the single render slot and the background tasks of one runtime."""

    def __init__(self, store: Store, renderer: Renderer, settings: Settings) -> None:
        """Create no tasks; `resume()` starts recovery on first use."""
        self.store, self.renderer, self.settings = store, renderer, settings
        self.slot = asyncio.Semaphore(1)
        self.tasks: set[asyncio.Task] = set()
        self.resumed = False

    def _load(self, style_id: UUID) -> tuple[list[pb.SpritePlacement], dict[str, pb.PublishedSprite]]:
        """Read the style's saved clips with their publications; a style without clips yields none."""
        _, stored, _ = sprites.get_bindings(self.store, style_id)
        placements = [ParseDict(item, pb.SpritePlacement()) for item in stored]
        publications: dict[str, pb.PublishedSprite] = {}
        for item in placements:
            if item.sprite_id not in publications:
                try:
                    publications[item.sprite_id] = sprites.get(self.store, item.sprite_id)
                except NotFound:
                    raise OverlayError("sprite_missing", f"Sprite {item.sprite_id} 不存在") from None
        return placements, publications

    def _insert(self, task_id: str, data: dict) -> None:
        """Persist a new queued task."""
        with self.store.connection() as db:
            db.execute(
                "INSERT INTO overlay_renders VALUES (?, ?, ?, ?)",
                (task_id, "queued", datetime.now(UTC).isoformat(), json.dumps(data, ensure_ascii=False)),
            )

    def _save(self, task_id: str, status: str, **changes) -> dict:
        """Update the status and merge top-level data fields in one transaction."""
        with self.store.connection() as db:
            row = db.execute("SELECT data FROM overlay_renders WHERE id=?", (task_id,)).fetchone()
            data = {**json.loads(row["data"]), **changes}
            db.execute(
                "UPDATE overlay_renders SET status=?, data=? WHERE id=?",
                (status, json.dumps(data, ensure_ascii=False), task_id),
            )
        return data

    def get(self, task_id: str) -> dict:
        """Return the public view of one task; unknown IDs are a 404."""
        with self.store.connection() as db:
            row = db.execute("SELECT status, data FROM overlay_renders WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "叠加渲染任务不存在")
        data = json.loads(row["data"])
        return {
            "taskId": task_id, "status": row["status"],
            "result": {"videoUrl": data["video_url"]} if data.get("video_url") else None,
            "error": data.get("error"),
        }

    async def accept(self, style_id: UUID, audio_url: str | None, duration: float | None,
                     callback_url: str | None) -> str:
        """Validate cheaply, persist the task and start it; no ffprobe or render happens here."""
        try:
            ZosSettings()
        except ValueError:
            raise HTTPException(503, "ZOS 配置不可用，请检查服务端环境") from None
        try:
            placements, publications = await asyncio.to_thread(self._load, style_id)
            shared_canvas(placements, publications)
            if duration is not None and duration > self.settings.overlay_max_seconds:
                raise OverlayError("duration_too_long", f"成片时长超过上限 {self.settings.overlay_max_seconds:g} 秒")
            if audio_url is None and duration is None and fixed_end_seconds(placements, publications) is None:
                raise OverlayError("duration_unknown", "无法确定成片时长：请提供 audioUrl 或 duration")
        except OverlayError as exc:
            raise HTTPException(422, exc.message) from None
        task_id = str(uuid4())
        await asyncio.to_thread(self._insert, task_id, {"request": {
            "style_id": str(style_id), "audio_url": audio_url, "duration": duration, "callback_url": callback_url,
        }})
        self._spawn(self._run(task_id))
        return task_id

    def _spawn(self, coroutine) -> None:
        """Keep a strong reference to a background task until it ends."""
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def resume(self) -> None:
        """Re-run unfinished tasks and pending notifications once per process."""
        if self.resumed:
            return
        self.resumed = True
        with self.store.connection() as db:
            rows = db.execute("SELECT id, status, data FROM overlay_renders").fetchall()
        for row in rows:
            data = json.loads(row["data"])
            if row["status"] not in TERMINAL:
                self._spawn(self._run(row["id"]))
            elif data.get("notification") == "pending" and data["request"]["callback_url"]:
                self._spawn(self._notify(row["id"]))

    async def _run(self, task_id: str) -> None:
        """Render, upload, record the outcome and notify; any failure becomes a sanitized task error."""
        async with self.slot:
            work = self.store.root / "overlays" / task_id
            try:
                await self._execute(task_id, work)
            except OverlayError as exc:
                self._fail(task_id, exc.code, exc.message)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("叠加渲染任务 %s 失败", task_id)
                self._fail(task_id, "internal_error", "叠加视频渲染或上传失败")
            finally:
                shutil.rmtree(work, ignore_errors=True)
        await self._notify(task_id)

    async def _execute(self, task_id: str, work) -> None:
        """Resolve the duration, render the overlay and publish it to ZOS."""
        with self.store.connection() as db:
            request = json.loads(db.execute(
                "SELECT data FROM overlay_renders WHERE id=?", (task_id,),
            ).fetchone()["data"])["request"]
        placements, publications = await asyncio.to_thread(self._load, UUID(request["style_id"]))
        self._save(task_id, "rendering")
        total = await resolve_total_seconds(
            request["audio_url"], request["duration"], placements, publications, HTTP_TIMEOUT_SECONDS,
        )
        overlay = plan_overlay(placements, publications, total, self.settings.overlay_max_seconds)
        shutil.rmtree(work, ignore_errors=True)
        output = await render_overlay(self.renderer, overlay, work)
        self._save(task_id, "uploading", duration_seconds=total)
        key = f"imv/overlay_render/{task_id}.webm"
        urls = await asyncio.to_thread(
            upload_public, [(output, key, "video/webm")], ZosSettings(), HTTP_TIMEOUT_SECONDS,
        )
        self._save(task_id, "succeeded", video_url=urls[0], zos_object_key=key, error=None,
                   notification="pending" if request["callback_url"] else None)

    def _fail(self, task_id: str, code: str, message: str) -> None:
        """Record a terminal failure; the public error never contains local paths or credentials."""
        with self.store.connection() as db:
            request = json.loads(db.execute(
                "SELECT data FROM overlay_renders WHERE id=?", (task_id,),
            ).fetchone()["data"])["request"]
        self._save(task_id, "failed", error={"code": code, "message": message},
                   notification="pending" if request["callback_url"] else None)

    async def _notify(self, task_id: str) -> None:
        """POST the four-field terminal body, retrying with fixed delays; the result is never rolled back."""
        view = self.get(task_id)
        with self.store.connection() as db:
            data = json.loads(db.execute(
                "SELECT data FROM overlay_renders WHERE id=?", (task_id,),
            ).fetchone()["data"])
        url = data["request"]["callback_url"]
        if not url or data.get("notification") != "pending":
            return
        body = {
            "taskId": task_id, "status": "succeed" if view["status"] == "succeeded" else "failed",
            "videoUrl": view["result"]["videoUrl"] if view["result"] else None,
            "errorMessage": view["error"]["message"] if view["error"] else None,
        }
        outcome = "failed"
        for delay in (0, *NOTIFICATION_RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)
            try:
                async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=False) as client:
                    response = await client.post(url, json=body)
                if 200 <= response.status_code < 300:
                    outcome = "sent"
                    break
            except httpx.HTTPError:
                logger.warning("叠加渲染任务 %s 终态通知未送达", task_id)
        self._save(task_id, view["status"], notification=outcome)
