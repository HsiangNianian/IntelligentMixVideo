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
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
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
    environment_problem,
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
# A locked or full database is retried a few times before the task keeps its previous state.
PERSIST_ATTEMPTS = 3
PERSIST_DELAY_SECONDS = 2
TERMINAL = ("succeeded", "failed")


class OverlayRenders:
    """Own the task table rows, the single render slot and the background tasks of one runtime."""

    def __init__(self, store: Store, renderer: Renderer, settings: Settings) -> None:
        """Create no tasks; `resume()` starts recovery."""
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

    def _read(self, task_id: str) -> tuple[str, dict]:
        """Return one task's status and data; an unknown ID is a 404."""
        with self.store.connection() as db:
            row = db.execute("SELECT status, data FROM overlay_renders WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "叠加渲染任务不存在")
        return row["status"], json.loads(row["data"])

    async def _persist(self, task_id: str, status: str, **changes) -> None:
        """Write a state change on a worker thread, retrying a locked or full database a few times.

        The last failure propagates and the row keeps its previous state, so a restart re-runs the task.
        """
        for attempt in range(1, PERSIST_ATTEMPTS + 1):
            try:
                await asyncio.to_thread(self._save, task_id, status, **changes)
                return
            except sqlite3.Error:
                if attempt == PERSIST_ATTEMPTS:
                    raise
                logger.warning("叠加渲染任务 %s 状态写入失败（第 %d 次），稍后重试", task_id, attempt)
                await asyncio.sleep(PERSIST_DELAY_SECONDS)

    def get(self, task_id: str) -> dict:
        """Return the public view of one task; unknown IDs are a 404."""
        status, data = self._read(task_id)
        return {
            "taskId": task_id, "status": status,
            "result": {"videoUrl": data["video_url"]} if data.get("video_url") else None,
            "error": data.get("error"),
        }

    async def accept(self, style_id: UUID, audio_url: str | None, duration: float | None,
                     callback_url: str | None) -> str:
        """Validate cheaply, persist the task and start it; no ffprobe or render happens here.

        The length limit is applied where the length is already known: an explicit `duration` or, without
        either input, the clips' own end. A TTS audio length is only known once it is probed, so an
        over-long audio fails the task later with `duration_too_long`.
        """
        try:
            ZosSettings()
        except ValueError:
            raise HTTPException(503, "ZOS 配置不可用，请检查服务端环境") from None
        missing = environment_problem(self.settings)
        if missing:
            logger.error("叠加渲染环境缺少 %s，拒绝受理", missing)
            raise HTTPException(503, "叠加渲染环境不可用，请检查服务端环境")
        try:
            placements, publications = await asyncio.to_thread(self._load, style_id)
            shared_canvas(placements, publications)
            if audio_url is None:
                seconds = duration if duration is not None else fixed_end_seconds(placements, publications)
                if seconds > self.settings.overlay_max_seconds:
                    raise OverlayError("duration_too_long", f"成片时长超过上限 {self.settings.overlay_max_seconds:g} 秒")
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
        """Re-run unfinished tasks and pending notifications once per process, oldest first.

        A row that cannot be parsed is logged and skipped so it never blocks the others. If the table itself
        cannot be read nothing is marked done, so the next call tries again.
        """
        if self.resumed:
            return
        try:
            with self.store.connection() as db:
                rows = db.execute("SELECT id, status, data FROM overlay_renders ORDER BY created_at, id").fetchall()
        except sqlite3.Error:
            logger.exception("叠加渲染任务表暂无法读取，恢复推迟到下次访问")
            return
        self.resumed = True
        for row in rows:
            try:
                data = json.loads(row["data"])
                callback_url = data["request"]["callback_url"]
            except (ValueError, KeyError, TypeError):
                logger.exception("叠加渲染任务 %s 的记录已损坏，跳过恢复", row["id"])
                continue
            if row["status"] not in TERMINAL:
                self._spawn(self._run(row["id"]))
            elif callback_url and data.get("notification") == "pending":
                self._spawn(self._notify(row["id"]))

    async def _run(self, task_id: str) -> None:
        """Render, upload, record the outcome and notify; any failure becomes a sanitized task error.

        A database that stays unwritable is not a task failure: the row keeps its previous state and the next
        start re-runs it (the upload key is the task ID, so a repeat overwrites the same object).
        """
        work = self.store.root / "overlays" / task_id
        try:
            async with self.slot:
                try:
                    await self._execute(task_id, work)
                except OverlayError as exc:
                    logger.warning("叠加渲染任务 %s 失败（%s）：%s", task_id, exc.code, exc.__cause__ or exc.message)
                    await self._fail(task_id, exc.code, exc.message)
                except sqlite3.Error:
                    raise
                except Exception:
                    logger.exception("叠加渲染任务 %s 失败", task_id)
                    await self._fail(task_id, "internal_error", "叠加视频渲染或上传失败")
                finally:
                    shutil.rmtree(work, ignore_errors=True)
        except sqlite3.Error:
            logger.error("叠加渲染任务 %s 的状态暂无法保存，保留原状态，重启后重新执行", task_id)
            return
        await self._notify(task_id)

    async def _execute(self, task_id: str, work: Path) -> None:
        """Resolve the duration, render the overlay and publish it to ZOS."""
        _, data = self._read(task_id)
        request = data["request"]
        placements, publications = await asyncio.to_thread(self._load, UUID(request["style_id"]))
        await self._persist(task_id, "rendering")
        total = await resolve_total_seconds(
            request["audio_url"], request["duration"], placements, publications, HTTP_TIMEOUT_SECONDS,
        )
        overlay = plan_overlay(placements, publications, total, self.settings.overlay_max_seconds)
        shutil.rmtree(work, ignore_errors=True)
        output = await render_overlay(self.renderer, overlay, work)
        await self._persist(task_id, "uploading", duration_seconds=total)
        key = f"imv/overlay_render/{task_id}.webm"
        urls = await asyncio.to_thread(
            upload_public, [(output, key, "video/webm")], ZosSettings(), HTTP_TIMEOUT_SECONDS,
        )
        await self._persist(task_id, "succeeded", video_url=urls[0], zos_object_key=key, error=None,
                            notification="pending" if request["callback_url"] else None)

    async def _fail(self, task_id: str, code: str, message: str) -> None:
        """Record a terminal failure; the public error never contains local paths or credentials."""
        _, data = self._read(task_id)
        await self._persist(task_id, "failed", error={"code": code, "message": message},
                            notification="pending" if data["request"]["callback_url"] else None)

    async def _notify(self, task_id: str) -> None:
        """POST the four-field terminal body, retrying with fixed delays; the result is never rolled back."""
        status, data = self._read(task_id)
        url = data["request"]["callback_url"]
        if not url or data.get("notification") != "pending":
            return
        error = data.get("error")
        body = {
            "taskId": task_id, "status": "succeed" if status == "succeeded" else "failed",
            "videoUrl": data.get("video_url"), "errorMessage": error["message"] if error else None,
        }
        outcome = "failed"
        for attempt, delay in enumerate((0, *NOTIFICATION_RETRY_DELAYS), start=1):
            if delay:
                await asyncio.sleep(delay)
            try:
                # The client timeout is per phase; the outer deadline bounds a response that trickles in.
                async with asyncio.timeout(HTTP_TIMEOUT_SECONDS):
                    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=False) as client:
                        response = await client.post(url, json=body)
            except Exception as exc:
                # Not only httpx.HTTPError: an address the schema accepts can still be httpx.InvalidURL.
                logger.warning("叠加渲染任务 %s 终态通知第 %d 次未送达：%s", task_id, attempt, type(exc).__name__)
                continue
            if 200 <= response.status_code < 300:
                outcome = "sent"
                break
            logger.warning("叠加渲染任务 %s 终态通知第 %d 次收到 HTTP %d", task_id, attempt, response.status_code)
        if outcome == "failed":
            logger.error("叠加渲染任务 %s 终态通知重试 %d 次后放弃", task_id, attempt)
        try:
            await self._persist(task_id, status, notification=outcome)
        except sqlite3.Error:
            logger.error("叠加渲染任务 %s 的通知结果暂无法保存，重启后可能重复投递", task_id)
