"""MySQL transactions keep queued work durable and publish accepted revisions atomically.

Works belong to the client device that created them; routes pass the caller's device and a work of another
device reads as missing. The backend runs as a single instance: one in-process lock serializes write
transactions, so the one-active-job rule, job claims and event IDs commit in order on MySQL as on the SQLite
test fixture. Sealed version files and reference images live in the object store (`object_store.py`); the data
folder only holds job scratch, a verified read cache and the private audit logs (one folder per work).
"""

import asyncio
import json
import logging
import os
import shutil
import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import Connection, delete, func, insert, inspect, select, update
from sqlalchemy.exc import IntegrityError

from ..database import get_engine
from . import history
from .context import Conversation, outcome_record
from .evidence import digest, verify_artifacts
from .models import (
    Asset,
    ClientContext,
    GenerateTemplateRequest,
    GenerationJob,
    JobError,
    JobInput,
    PublicJob,
    TaskInput,
    ImagePayload,
    TemplateCandidate,
    TemplateProject,
    TemplateSpec,
    TemplateVersion,
    ValidationReport,
    validation_fingerprint,
)
from .object_store import PREFIX, Objects, load_objects
from .parameters import patch_parameters
from .tables import (
    assets,
    chat_messages,
    conversations,
    devices,
    instance,
    job_progress,
    job_rounds,
    jobs,
    metadata,
    versions,
    work_assets,
    work_events,
    works,
)

ACTIVE = ("queued", "running")
logger = logging.getLogger(__name__)
# Read-cache files unused for this long are removed by the retention sweep; the object store keeps the originals.
CACHE_IDLE = timedelta(days=30)
# A device's last-seen time is rewritten at most this often.
TOUCH_INTERVAL = timedelta(hours=1)
# Devices whose activity this process wrote or checked recently; bounded, since any caller can send a new device ID.
TOUCH_CACHE = 4096


class NotFound(HTTPException):
    """The requested work, asset, job, or version does not exist (or belongs to another device)."""

    def __init__(self, detail: str) -> None:
        """Use FastAPI's built-in 404 response without registering an exception handler."""
        super().__init__(status_code=404, detail=detail)


class InstanceInUse(OSError):
    """Another backend instance holds the queue lease of this database; wait for it to stop or its lease to expire."""


class Conflict(HTTPException):
    """The requested state transition conflicts with current durable state."""

    def __init__(self, detail: str) -> None:
        """Use FastAPI's built-in 409 response for concrete conflicting requests."""
        super().__init__(status_code=409, detail=detail)


# The queue lease is renewed every LEASE_RENEW and may be taken over once it is LEASE_TTL old (a crashed instance).
LEASE_TTL = timedelta(seconds=30)
LEASE_RENEW = 10.0

# An upload whose message was rejected or never arrived is reclaimed once it is this old; a send uploads
# immediately before it submits, so no pending send can still be about to reference such an image.
ORPHAN_ASSET_TTL = timedelta(hours=24)


def now() -> datetime:
    """Use timezone-aware timestamps consistently in records and event ordering."""
    return datetime.now(UTC)


def naive(stamp: datetime) -> datetime:
    """Database DATETIME columns hold UTC without a zone."""
    return stamp.astimezone(UTC).replace(tzinfo=None)


def version_key(version_id: UUID, name: str = "") -> str:
    """Object key of one sealed version file, or the version's prefix."""
    return f"{PREFIX}/versions/{version_id}/{name}"


def asset_key(asset_id: UUID) -> str:
    """Object key of one normalized reference image."""
    return f"{PREFIX}/assets/{asset_id}.png"


class Store:
    """Small synchronous metadata transactions; rendering never holds a database transaction."""

    def __init__(self, root: Path, objects: Objects | None = None) -> None:
        """Locate scratch storage; tables are created by `initialize`, so constructing touches nothing."""
        self.root = root
        self.objects = objects if objects is not None else load_objects(root)
        self.instance_id = uuid4()
        self._write = threading.RLock()
        self._touched: OrderedDict[str, datetime] = OrderedDict()
        # Live windows exist only while their task runs; terminal state and its checkpoint commit together.
        self.running_contexts: dict[UUID, Conversation] = {}
        self.legacy_tables: set[str] = set()
        # Open event streams by work, woken when a transaction that appended events of that work commits.
        self._watchers: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Event]]] = {}
        self._watching = threading.Lock()

    def initialize(self) -> None:
        """Create the tables on first use; existing tables are reused unchanged."""
        metadata.create_all(get_engine())
        self.legacy_tables = set(inspect(get_engine()).get_table_names()) & {
            "remotion_conversations", "remotion_assets", "remotion_work_assets",
        }

    def legacy_state(self, db: Connection, work_id: UUID) -> str | None:
        """Read an old window only when that table already exists."""
        if conversations.name not in self.legacy_tables:
            return None
        return db.execute(select(conversations.c.data).where(conversations.c.work_id == str(work_id))).scalar()

    def legacy_images(self, db: Connection, work_id: UUID) -> list[UUID]:
        """List every old image association, including older images outside the model window."""
        if work_assets.name not in self.legacy_tables:
            return []
        return [UUID(value) for value in db.execute(select(work_assets.c.asset_id).where(
            work_assets.c.work_id == str(work_id),
        )).scalars()]

    def acquire_lease(self) -> None:
        """Become the one backend instance that owns this database's queue, or raise `InstanceInUse`.

        Recovery on startup interrupts every running job and a second instance would claim queued jobs it cannot
        finish (local scratch, optionally local objects), so an unexpired lease of another instance refuses startup.
        """
        stamp, key = naive(now()), str(self.instance_id)
        with self.transaction() as db:
            row = db.execute(select(instance).where(instance.c.name == "backend").with_for_update()).first()
            if row is None:
                db.execute(insert(instance).values(name="backend", instance_id=key, heartbeat_at=stamp))
            elif row.instance_id == key or row.heartbeat_at < stamp - LEASE_TTL:
                db.execute(update(instance).where(instance.c.name == "backend").values(instance_id=key, heartbeat_at=stamp))
            else:
                raise InstanceInUse(
                    "另一个后端实例正在使用这个数据库的 Remotion 任务队列；请先停止它，或在其停止约 30 秒后重试。"
                )

    def renew_lease(self) -> bool:
        """Refresh the lease; False when another instance has taken it over after this one stopped renewing."""
        with self.transaction() as db:
            return db.execute(update(instance).where(
                instance.c.name == "backend", instance.c.instance_id == str(self.instance_id),
            ).values(heartbeat_at=naive(now()))).rowcount == 1

    def release_lease(self) -> None:
        """Give the lease up on a clean stop so the next instance starts at once."""
        with self.transaction() as db:
            db.execute(delete(instance).where(
                instance.c.name == "backend", instance.c.instance_id == str(self.instance_id),
            ))

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        """Run one serialized write transaction that commits or rolls back as a whole.

        Streams waiting on the works whose events it appended (`history.append_event`) are woken after the commit.
        """
        with self._write:
            with get_engine().begin() as db:
                yield db
                # Reached only when the body did not raise; the commit follows when the block exits.
                appended = db.info.pop(history.APPENDED, ())
        if appended:
            self._wake(appended)

    @contextmanager
    def watch(self, work_id: UUID) -> Iterator[asyncio.Event]:
        """An event set whenever new events of the work commit; call from the event loop that waits on it.

        Create it before reading, clear it, then read and wait: a commit between the read and the wait leaves it set.
        """
        key, entry = str(work_id), (asyncio.get_running_loop(), asyncio.Event())
        with self._watching:
            self._watchers.setdefault(key, set()).add(entry)
        try:
            yield entry[1]
        finally:
            with self._watching:
                self._watchers[key].discard(entry)
                if not self._watchers[key]:
                    del self._watchers[key]

    def _wake(self, work_ids: set[str]) -> None:
        """Set the events of every stream watching one of the works; a stream whose loop is gone is skipped."""
        with self._watching:
            entries = [entry for key in work_ids for entry in self._watchers.get(key, ())]
        for loop, event in entries:
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(event.set)

    @contextmanager
    def reading(self) -> Iterator[Connection]:
        """Open a read connection without taking the write lock."""
        with get_engine().connect() as db:
            yield db

    def touch(self, device: UUID) -> None:
        """Refresh the activity time of a device that owns works; inactive devices are expired after the retention period.

        A device's row is created with its first work, so a request carrying an arbitrary device ID adds no row, and
        the in-memory throttle keeps at most `TOUCH_CACHE` devices.
        """
        key, stamp = str(device), now()
        if (last := self._touched.get(key)) is not None and stamp - last < TOUCH_INTERVAL:
            return
        with self.transaction() as db:
            db.execute(update(devices).where(devices.c.device_id == key).values(last_seen_at=naive(stamp)))
        self._touched[key] = stamp
        self._touched.move_to_end(key)
        while len(self._touched) > TOUCH_CACHE:
            self._touched.popitem(last=False)

    @staticmethod
    def _register(db: Connection, device: UUID) -> None:
        """Create or refresh the row of a device that is creating a work."""
        key, stamp = str(device), naive(now())
        if db.execute(select(devices.c.device_id).where(devices.c.device_id == key)).first() is None:
            db.execute(insert(devices).values(device_id=key, last_seen_at=stamp))
        else:
            db.execute(update(devices).where(devices.c.device_id == key).values(last_seen_at=stamp))

    def require_work(self, db: Connection, identifier: UUID, device: UUID | None = None) -> TemplateProject:
        """Return the work inside the caller's transaction; another device's work is missing, a deleting one is gone."""
        row = db.execute(select(works.c.device_id, works.c.deleting, works.c.data).where(works.c.id == str(identifier))).first()
        if row is None or device is not None and row.device_id != str(device):
            raise NotFound("work not found")
        if row.deleting:
            raise HTTPException(410, "Work is being deleted; retry DELETE to finish cleanup.")
        return TemplateProject.model_validate_json(row.data)

    def owner(self, work_id: UUID) -> UUID:
        """The device that created a work."""
        with self.reading() as db:
            device = db.execute(select(works.c.device_id).where(works.c.id == str(work_id))).scalar()
        if device is None:
            raise NotFound("work not found")
        return UUID(device)

    def begin_deletion(self, identifier: UUID, device: UUID | None = None) -> list[UUID]:
        """Persist deletion intent and cancel all pending jobs before awaiting any writer."""
        with self.transaction() as db:
            row = db.execute(select(works.c.device_id).where(works.c.id == str(identifier))).first()
            if row is None or device is not None and row.device_id != str(device):
                return []
            db.execute(update(works).where(works.c.id == str(identifier)).values(deleting=True))
            rows = db.execute(select(jobs.c.data).where(jobs.c.work_id == str(identifier))).all()
            found = [GenerationJob.model_validate_json(row.data) for row in rows]
            for job in found:
                if job.status in ACTIVE:
                    job.status, job.stage = "cancelled", "finished"
                    self._save_job(db, job)
            return [job.id for job in found]

    def pending_deletions(self) -> list[UUID]:
        """Recover only explicitly requested deletions, never sweep unrelated data."""
        with self.reading() as db:
            return [UUID(value) for value in db.execute(select(works.c.id).where(works.c.deleting.is_(True))).scalars()]

    def client_context(self, project_id: UUID) -> ClientContext:
        """Export an undelivered checkpoint or a legacy window; acknowledged new windows live only on the client."""
        self.project(project_id)
        with self.reading() as db:
            latest = self._latest(db, project_id)
            if latest is None or latest.status in ACTIVE or db.execute(select(jobs.c.active_work_id).where(jobs.c.id == str(latest.id))).scalar():
                raise Conflict("请等待当前任务结束后再读取模型上下文。")
            state = db.execute(select(work_events.c.data).where(
                work_events.c.work_id == str(project_id), work_events.c.type == "job.updated",
            ).order_by(work_events.c.id.desc()).limit(1)).scalar()
            if state:
                checkpoint = json.loads(state).get("context")
                if checkpoint and checkpoint["job_id"] == str(latest.id):
                    return ClientContext.model_validate(checkpoint)
            legacy = self.legacy_state(db, project_id)
            if legacy is not None:
                inputs = JobInput.model_validate_json(db.execute(select(jobs.c.input_data).where(jobs.c.id == str(latest.id))).scalar_one())
                return self.delivery_context(db, latest, legacy, inputs, self.require_work_request(db, project_id))
        raise Conflict("模型上下文已由客户端保存或过期，请使用原设备的本地会话；本地数据丢失时需新建会话。")

    def require_context(self, project_id: UUID, context: ClientContext | None) -> ClientContext:
        """Bind replay to the work and latest task; migration/download never silently creates an empty history."""
        context = context if context is not None else self.client_context(project_id)
        if str(context.work_id) != str(project_id) or context.job_id != self.latest_job(project_id).id:
            raise Conflict("模型上下文不属于当前会话或已过期，请刷新任务后重试。")
        latest = self.latest_job(project_id)
        if context.input is not None and context.base_version_id != latest.base_version_id:
            raise Conflict("恢复点不属于当前任务基线。")
        return context

    @staticmethod
    def _latest(db: Connection, project_id: UUID) -> GenerationJob | None:
        """The newest execution of a work, or None."""
        data = db.execute(
            select(jobs.c.data).where(jobs.c.work_id == str(project_id)).order_by(jobs.c.seq.desc()).limit(1)
        ).scalar()
        return GenerationJob.model_validate_json(data) if data else None

    def latest_job(self, project_id: UUID, device: UUID | None = None) -> GenerationJob:
        """Bind clarification replies to the latest execution, including after a retry finishes."""
        self.project(project_id, device)
        with self.reading() as db:
            job = self._latest(db, project_id)
        if job is None:
            raise NotFound("job not found")
        return job

    def project(self, identifier: UUID, device: UUID | None = None) -> TemplateProject:
        """Return the work and its current accepted version pointer."""
        with self.reading() as db:
            return self.require_work(db, identifier, device)

    def job(self, identifier: UUID, device: UUID | None = None) -> GenerationJob:
        """Read private execution state; HTTP routes project only user-visible fields."""
        with self.reading() as db:
            data = db.execute(select(jobs.c.data).where(jobs.c.id == str(identifier))).scalar()
            if data is None:
                raise NotFound("job not found")
            job = GenerationJob.model_validate_json(data)
            if device is not None:
                self.require_work(db, job.project_id, device)
        return job

    def version(self, identifier: UUID, device: UUID | None = None) -> TemplateVersion:
        """Read an accepted, immutable version of a work that still exists."""
        with self.reading() as db:
            data = db.execute(select(versions.c.data).where(versions.c.id == str(identifier))).scalar()
            if data is None:
                raise NotFound("version not found")
            version = TemplateVersion.model_validate_json(data)
            self.require_work(db, version.project_id, device)
        return version

    def asset(self, identifier: UUID, device: UUID | None = None) -> Asset:
        """Only registered normalized images can be used as references."""
        if assets.name not in self.legacy_tables:
            raise NotFound("参考图片不在本次请求中，请从原设备的本地会话恢复。")
        with self.reading() as db:
            row = db.execute(select(assets.c.device_id, assets.c.data).where(assets.c.id == str(identifier))).first()
        if row is None or device is not None and row.device_id != str(device):
            raise NotFound("asset not found")
        return Asset.model_validate_json(row.data)

    def prune_assets(self, older_than: timedelta = ORPHAN_ASSET_TTL) -> list[UUID]:
        """Delete uploads that no work references once they are older than ``older_than``; return their IDs.

        Works being deleted still reference their images until cleanup finishes, so those stay for it. The records go
        first, so no message can reference an image whose bytes are being removed, and the object store is called
        outside the write lock; an object that fails to delete is only logged, since nothing can reach it any more.
        """
        if not {assets.name, work_assets.name} <= self.legacy_tables:
            return []
        cutoff = now() - older_than
        with self.transaction() as db:
            rows = db.execute(select(assets.c.id, assets.c.data).where(
                assets.c.id.not_in(select(work_assets.c.asset_id))
            )).all()
            removed = [row.id for row in rows if Asset.model_validate_json(row.data).created_at < cutoff]
            if removed:
                db.execute(delete(assets).where(assets.c.id.in_(removed)))
        for identifier in removed:
            try:
                self.objects.delete_prefix(asset_key(UUID(identifier)))
                (self.root / "cache" / "assets" / f"{identifier}.png").unlink(missing_ok=True)
            except Exception:
                logger.exception("Unused reference image %s could not be removed from storage", identifier)
        return [UUID(identifier) for identifier in removed]

    def require_references(self, db: Connection, images: list[UUID], device: UUID) -> None:
        """Accept images this device uploaded unless a deleting work still uses one, since cleanup may remove it."""
        if images and not {assets.name, work_assets.name} <= self.legacy_tables:
            raise NotFound("参考图片不在本次请求中，请从原设备的本地会话恢复。")
        for image in images:
            owner = db.execute(select(assets.c.device_id).where(assets.c.id == str(image))).scalar()
            if owner != str(device):
                raise NotFound("asset not found")
            if db.execute(
                select(works.c.id).join(work_assets, work_assets.c.work_id == works.c.id)
                .where(work_assets.c.asset_id == str(image), works.c.deleting.is_(True)).limit(1)
            ).first():
                raise Conflict("reference image belongs to a work being deleted")

    def asset_path(self, identifier: UUID) -> Path:
        """Return a cached local copy of a registered image whose SHA-256 matches its record."""
        asset = self.asset(identifier)
        return self.cached(asset_key(identifier), Path("assets") / f"{identifier}.png", asset.sha256)

    def cached(self, key: str, relative: Path, sha256: str) -> Path:
        """Return a local copy of one object whose SHA-256 matches, downloading it when the cache lacks it.

        Raises FileNotFoundError when the object is missing and ValueError when its bytes do not match.
        """
        path = self.root / "cache" / relative
        if path.is_file() and digest(path) == sha256:
            os.utime(path)
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        staged = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            self.objects.fetch(key, staged)
            if digest(staged) != sha256:
                raise ValueError("Stored object failed integrity verification.")
            staged.replace(path)
        finally:
            staged.unlink(missing_ok=True)
        return path

    def version_file(self, version: TemplateVersion, name: str) -> Path:
        """Return a verified local copy of one sealed file, downloading only that file when the cache lacks it.

        Raises FileNotFoundError when the version has no such file or its object is gone.
        """
        if name not in version.validation.artifacts or Path(name).name != name:
            raise FileNotFoundError(name)
        return self.cached(
            version_key(version.id, name), Path("accepted") / str(version.id) / name, version.validation.artifacts[name],
        )

    def accepted(self, version: TemplateVersion) -> Path:
        """Return a local copy of a version's sealed files, downloaded on demand and checked against its evidence.

        Each file is cached on its own through a staged download and an atomic replace, so concurrent readers of a
        cold cache never remove or rename a directory another reader is already using.
        Raises ValueError or OSError (FileNotFoundError when an object is gone) if the files cannot be verified.
        """
        directory = self.root / "cache" / "accepted" / str(version.id)
        try:
            verify_artifacts(version.candidate, version.spec, version.validation, directory)
            for name in version.validation.artifacts:
                os.utime(directory / name)
            return directory
        except (ValueError, OSError):
            pass
        for name in version.validation.artifacts:
            self.version_file(version, name)
        verify_artifacts(version.candidate, version.spec, version.validation, directory)
        return directory

    def prune_cache(self, idle: timedelta = CACHE_IDLE) -> None:
        """Remove read-cache files not used within ``idle`` (each hit refreshes a file's time), then empty folders."""
        cache = self.root / "cache"
        if not cache.is_dir():
            return
        cutoff = (now() - idle).timestamp()
        for path in cache.rglob("*"):
            if path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        for folder in sorted((path for path in cache.rglob("*") if path.is_dir()), key=lambda path: len(path.parts), reverse=True):
            with suppress(OSError):
                folder.rmdir()

    def job_dir(self, identifier: UUID) -> Path:
        """Scratch for one execution; removed when the execution ends, except its audit log."""
        return self.root / "jobs" / str(identifier)

    def audit_path(self, work_id: UUID, job_id: UUID) -> Path:
        """Private audit log of one execution, kept for the audit retention period.

        Logs are grouped by work, so deleting a work removes those of executions whose records were already pruned.
        """
        return self.root / "audit" / str(work_id) / f"{job_id}.jsonl"

    def progress(self, identifier: UUID, phase: str) -> None:
        """Atomically publish whitelisted host phase transitions through the existing work stream."""
        from .progress import start_step

        with self.transaction() as db:
            job = self._job_for_update(db, identifier)
            if start_step(db, job, phase, now()):
                self._save_job(db, job)

    def round(self, identifier: UUID, record: dict) -> None:
        """Append one finished ReAct round and publish it through the existing work stream.

        The record carries the layer, the turn and the tool calls the host actually
        ran, each with the model's clipped arguments and, on failure, the tool's
        error text (see ``progress.LoopRound``); result payloads stay in the private
        audit. Unknown layers are rejected by the contract, terminal jobs accept
        nothing, and a repeated layer/turn pair is ignored so a retried handler
        cannot duplicate a round.
        """
        from .progress import LoopRound, save_round

        with self.transaction() as db:
            job = self._job_for_update(db, identifier)
            if job.status != "running":
                return
            current = LoopRound.model_validate(record)
            if not save_round(db, str(job.id), current):
                return
            self._save_job(db, job)
            history.append_event(db, job.project_id, "job.round", {
                "job_id": str(job.id), "round": current.model_dump(mode="json"),
            }, job.updated_at)

    @staticmethod
    def _job_for_update(db: Connection, identifier: UUID) -> GenerationJob:
        """Read one job inside a write transaction."""
        data = db.execute(select(jobs.c.data).where(jobs.c.id == str(identifier)).with_for_update()).scalar()
        if data is None:
            raise NotFound("job not found")
        return GenerationJob.model_validate_json(data)

    def create(self, request: GenerateTemplateRequest, device: UUID, payloads: list[ImagePayload] | None = None) -> tuple[TemplateProject, GenerationJob]:
        """Atomically create a work of this device and its first queued generation."""
        stamp = now()
        project = TemplateProject(id=uuid4(), request=request, created_at=stamp, updated_at=stamp)
        job = self._new_job(project.id, None)
        images = [image.asset_id for image in request.images]
        inputs = JobInput(request=request)
        self.prepare_images(job, project, inputs, payloads or [], device)
        try:
            with self.transaction() as db:
                self._register(db, device)
                db.execute(insert(works).values(
                    id=str(project.id), device_id=str(device), deleting=False, data=project.model_dump_json(),
                ))
                self._insert_job(
                    db, job, inputs, request.description or "请参考这张图片制作字效。", images=images,
                )
        except BaseException:
            shutil.rmtree(self.job_dir(job.id), ignore_errors=True)
            raise
        return project, job

    def _new_job(self, project_id: UUID, base: UUID | None, running: bool = False) -> GenerationJob:
        """Assign execution identity before it enters the shared FIFO queue, or as already running when it bypasses it."""
        stamp = now()
        return GenerationJob(
            id=uuid4(),
            project_id=project_id,
            base_version_id=base,
            status="running" if running else "queued",
            stage="validating" if running else "queued",
            created_at=stamp,
            updated_at=stamp,
        )

    def _insert_job(
        self, db: Connection, job: GenerationJob, inputs: JobInput, message_text: str | None = None,
        *, images: list[UUID] | None = None,
    ) -> None:
        """Insert a job and its user message; a second active job of the same work violates the unique key.

        ``images`` are the references attached to this message, shown with it in the chat.
        """
        if inputs.context is not None:
            latest = self._latest(db, job.project_id)
            if (latest is None or latest.status in ACTIVE or inputs.context.work_id != job.project_id
                    or inputs.context.job_id != latest.id):
                raise Conflict("模型上下文不属于当前会话或已过期，请刷新任务后重试。")
        db.execute(insert(jobs).values(
            id=str(job.id), work_id=str(job.project_id), status=job.status,
            active_work_id=str(job.project_id) if job.status in ACTIVE else None,
            data=job.model_dump_json(), input_data=inputs.model_dump_json(),
        ))
        if inputs.context is not None and conversations.name in self.legacy_tables:
            db.execute(delete(conversations).where(conversations.c.work_id == str(job.project_id)))
        if inputs.context is not None and work_assets.name in self.legacy_tables:
            db.execute(delete(work_assets).where(work_assets.c.work_id == str(job.project_id)))
        history.record_input(db, job, inputs, message_text, images=images)
        history.record_job(db, job)

    def enqueue(
        self,
        project_id: UUID,
        inputs: JobInput,
        base: UUID | None,
        *,
        message_text: str | None = None,
        message_images: list[UUID] | None = None,
        running: bool = False,
        payloads: list[ImagePayload] | None = None,
    ) -> GenerationJob:
        """Reject concurrent edits, foreign version IDs and unusable reference images before enqueueing any work.

        ``message_images`` are the images the user attached to this chat message; ``inputs.images`` may also carry
        images inherited from the task being answered or retried. ``running`` records a job the caller executes
        itself at once (an instant parameter revision); the FIFO worker only claims queued jobs, and the
        one-active-job rule still applies.
        """
        self.project(project_id)
        if base is not None and self.version(base).project_id != project_id:
            raise Conflict("base version belongs to another work")
        job = self._new_job(project_id, base, running)
        project = self.project(project_id)
        inputs.request = inputs.request or (inputs.context.request if inputs.context else None) or project.request
        if inputs.request is None:
            raise Conflict("本地会话缺少原始请求，请从原设备恢复。")
        inputs.finalized = False
        self.prepare_images(job, project, inputs, payloads or [], self.owner(project_id))
        try:
            with self.transaction() as db:
                # The work may have been deleted since the check above: report it as missing, not as a server error.
                self.require_work(db, project_id)
                self._insert_job(db, job, inputs, message_text, images=message_images)
        except IntegrityError as exc:
            shutil.rmtree(self.job_dir(job.id), ignore_errors=True)
            raise Conflict("this work already has a queued or running job") from exc
        except BaseException:
            shutil.rmtree(self.job_dir(job.id), ignore_errors=True)
            raise
        return job

    def inputs(self, job_id: UUID) -> JobInput:
        """Recover the exact explicit input of an interrupted or failed execution."""
        with self.reading() as db:
            data = db.execute(select(jobs.c.input_data).where(jobs.c.id == str(job_id))).scalar()
        if data is None:
            raise NotFound("job not found")
        return JobInput.model_validate_json(data)

    def save_checkpoint(self, job_id: UUID, checkpoint: dict | None) -> None:
        """Keep the latest completed tool boundary in the existing private job row, independent of scratch files.

        A terminating execution may still save its final tool receipt before delivery is sealed. After ACK/TTL
        the client owns recovery; late writers cannot repopulate the finalized row.
        """
        with self.transaction() as db:
            data = db.execute(select(jobs.c.input_data).where(jobs.c.id == str(job_id)).with_for_update()).scalar_one()
            inputs = JobInput.model_validate_json(data)
            if inputs.finalized:
                return
            TaskInput.model_validate(inputs.model_dump(include=set(TaskInput.model_fields)) | {"checkpoint": checkpoint})
            inputs.checkpoint = checkpoint
            if (context := self.running_contexts.get(job_id)) is not None:
                job = self._job_for_update(db, job_id)
                inputs.context = ClientContext(work_id=job.project_id, job_id=job.id, state=context.serialize())
            db.execute(update(jobs).where(jobs.c.id == str(job_id)).values(input_data=inputs.model_dump_json()))

    def _save_job(self, db: Connection, job: GenerationJob) -> None:
        """Persist cancellation/publication immediately, but defer delivery until the execution finalizer."""
        job.updated_at = now()
        deferred = job.id in self.running_contexts
        db.execute(update(jobs).where(jobs.c.id == str(job.id)).values(
            status=job.status, data=job.model_dump_json(),
            active_work_id=str(job.project_id) if job.status in ACTIVE or deferred else None,
        ))
        if job.status in ACTIVE:
            history.record_job(db, job)
        elif not deferred:
            self._finalize_job(db, job)

    def finalize_job(self, job_id: UUID) -> None:
        """Deliver the last checkpoint and Outer outcome exactly once, after all runtime writers have stopped."""
        with self.transaction() as db:
            self._finalize_job(db, self._job_for_update(db, job_id))

    def _finalize_job(self, db: Connection, job: GenerationJob) -> None:
        """Atomically seal the device delivery and release the per-work queue slot."""
        inputs = JobInput.model_validate_json(db.execute(
            select(jobs.c.input_data).where(jobs.c.id == str(job.id)),
        ).scalar_one())
        if inputs.finalized or job.status in ACTIVE:
            return
        context = self.running_contexts.get(job.id)
        if context is None and inputs.context and inputs.context.job_id == job.id and inputs.parameters is None:
            # Restart after a completed tool/terminal DB write: append the outcome the lost finalizer never sent.
            context = Conversation.from_client(inputs.context.state)
            context.append([outcome_record(job)])
        state = context.serialize() if context is not None else inputs.context.state if inputs.context else self.legacy_state(db, job.project_id) or "[]"
        request = inputs.request or (inputs.context.request if inputs.context else None) or self.require_work_request(db, job.project_id)
        if job.status in {"succeeded", "answered"}:
            inputs.checkpoint = None
        checkpoint = self.delivery_context(db, job, state, inputs, request)
        inputs.finalized = True
        inputs.context = None
        db.execute(update(jobs).where(jobs.c.id == str(job.id)).values(input_data=inputs.model_dump_json(), active_work_id=None))
        history.record_job(db, job, context=checkpoint)

    @staticmethod
    def require_work_request(db: Connection, project_id: UUID):
        """Read legacy/in-flight original input even during a durable deletion."""
        data = db.execute(select(works.c.data).where(works.c.id == str(project_id))).scalar_one()
        return TemplateProject.model_validate_json(data).request

    def delivery_context(self, db: Connection, job, state, inputs, request) -> ClientContext:
        """Build the local replay envelope without credentials, image bytes or render acceptance."""
        from .references import earlier_images, reference_images
        replay = TaskInput.model_validate(inputs.model_dump(include=set(TaskInput.model_fields)))
        references = reference_images(TemplateProject.model_construct(request=request), inputs) if request else []
        references += earlier_images(Conversation.from_client(state), references)
        return ClientContext(work_id=job.project_id, job_id=job.id, state=state, request=request, input=replay,
                             base_version_id=job.base_version_id, questions=job.questions, image_ids=[entry["asset_id"] for entry in references],
                             legacy_image_ids=self.legacy_images(db, job.project_id))

    def prepare_images(self, job, project, inputs, payloads, device) -> None:
        """Materialize only required images under the new task ID; failed enqueue leaves no files."""
        from .references import earlier_images, reference_images, stage_images
        from .settings import Settings
        if inputs.parameters is not None:
            return
        references = reference_images(project, inputs)
        if inputs.context:
            references += earlier_images(Conversation.from_client(inputs.context.state), references)
        def legacy(identifier):
            """Migrate old device-owned images; new clients always provide local bytes."""
            with self.reading() as db:
                self.require_references(db, [identifier], device)
            self.asset(identifier, device)
            return self.asset_path(identifier).read_bytes()
        try:
            stage_images(self.job_dir(job.id) / "references", [UUID(entry["asset_id"]) for entry in references],
                         payloads, legacy, Settings(_env_file=None))
        except BaseException:
            shutil.rmtree(self.job_dir(job.id), ignore_errors=True)
            raise

    def update(self, job_id: UUID, **changes) -> GenerationJob:
        """Advance an active job; terminal jobs cannot be resurrected by late results."""
        with self.transaction() as db:
            job = self._job_for_update(db, job_id)
            if job.status not in ACTIVE:
                return job
            job = GenerationJob.model_validate(job.model_dump() | changes)
            self._save_job(db, job)
        return job

    def claim(self) -> GenerationJob | None:
        """Claim the oldest queued execution atomically for the single local worker."""
        with self.transaction() as db:
            data = db.execute(
                select(jobs.c.data).where(jobs.c.status == "queued").order_by(jobs.c.seq).limit(1).with_for_update()
            ).scalar()
            if data is None:
                return None
            job = GenerationJob.model_validate_json(data)
            job.status, job.stage = "running", "analyzing"
            self._save_job(db, job)
            return job

    def interrupt_unfinished(self) -> None:
        """Interrupt unfinished requests; explicit retry resumes the last completed tool boundary."""
        recovered = []
        with self.transaction() as db:
            for data in db.execute(select(jobs.c.data).where(jobs.c.active_work_id.is_not(None))).scalars().all():
                job = GenerationJob.model_validate_json(data)
                recovered.append(job.id)
                if job.status not in ACTIVE:
                    self._finalize_job(db, job)
                    continue
                job.status, job.stage = "interrupted", "finished"
                job.error = JobError(code="interrupted", message="Server stopped; retry continues from saved progress in a new execution.")
                self._save_job(db, job)
        for identifier in recovered:
            shutil.rmtree(self.job_dir(identifier), ignore_errors=True)

    def publish(
        self,
        job_id: UUID,
        candidate: TemplateCandidate,
        spec: TemplateSpec,
        report: ValidationReport,
        directory: Path,
    ) -> TemplateVersion:
        """Publish generated or user-edited results with distinct gates; cancellation wins over either.

        The sealed bytes are uploaded under a fresh version ID before the record transaction, outside the write lock,
        so a slow object store never blocks other writers. If the transaction fails they are removed again, unless the
        version did commit and only the acknowledgement was lost.
        """
        verify_artifacts(candidate, spec, report, directory)
        if self.job(job_id).status != "running":
            raise Conflict("only a running job may publish a version")
        version_id = uuid4()
        try:
            for name in report.artifacts:
                self.objects.put(version_key(version_id, name), directory / name, "application/octet-stream")
            with self.transaction() as db:
                job = self._job_for_update(db, job_id)
                if job.status != "running":
                    raise Conflict("only a running job may publish a version")
                inputs = JobInput.model_validate_json(
                    db.execute(select(jobs.c.input_data).where(jobs.c.id == str(job_id))).scalar_one()
                )
                source, agent_base_id = "agent", None
                if inputs.mode == "parameters":
                    if job.base_version_id is None or inputs.parameters is None:
                        raise Conflict("parameter revision requires a saved base and parameters")
                    base = self.version(job.base_version_id)
                    expected_candidate, expected_spec = patch_parameters(base.candidate, base.spec, inputs.parameters)
                    if (
                        candidate != expected_candidate
                        or spec != expected_spec
                        or report.fingerprint != validation_fingerprint(candidate, spec, report.runtime)
                        or not report.render_passed
                    ):
                        raise Conflict("parameter revision differs from user input or lacks current render evidence")
                    source = "user_parameters"
                    agent_base_id = base.agent_base_version_id if base.source == "user_parameters" else base.id
                else:
                    if (not report.passed
                        or report.fingerprint != validation_fingerprint(candidate, spec, report.runtime)
                        or (spec.schema_version == "2") != (report.profile == "v2")):
                        raise Conflict("Candidate lacks current successful validation evidence")
                project = self.require_work(db, job.project_id)
                number = db.execute(
                    select(func.coalesce(func.max(versions.c.number), 0) + 1).where(versions.c.work_id == str(project.id))
                ).scalar_one()
                version = TemplateVersion(
                    id=version_id,
                    project_id=project.id,
                    job_id=job.id,
                    number=number,
                    base_version_id=job.base_version_id,
                    source=source,
                    agent_base_version_id=agent_base_id,
                    candidate=candidate,
                    spec=spec,
                    validation=report,
                    created_at=now(),
                )
                db.execute(insert(versions).values(
                    id=str(version.id), work_id=str(project.id), number=number, data=version.model_dump_json(),
                ))
                if source == "agent" and spec.schema_version == "2":
                    from .source_library import archive
                    archive(db, version)
                project.current_version_id, project.updated_at = version.id, now()
                db.execute(update(works).where(works.c.id == str(project.id)).values(data=project.model_dump_json()))
                job.status, job.stage, job.result_version_id = "succeeded", "finished", version.id
                self._save_job(db, job)
        except BaseException:
            self._discard_upload(version_id)
            raise
        # Seed the read cache from the verified candidate so the first preview needs no download; each file is replaced
        # atomically, and a failure only costs that download later.
        cached = self.root / "cache" / "accepted" / str(version.id)
        try:
            cached.mkdir(parents=True, exist_ok=True)
            for name in report.artifacts:
                staged = cached / f".{name}.{uuid4().hex}.tmp"
                try:
                    shutil.copyfile(directory / name, staged)
                    staged.replace(cached / name)
                finally:
                    staged.unlink(missing_ok=True)
        except OSError:
            logger.exception("Read cache of version %s was not seeded", version.id)
        return version

    def _discard_upload(self, version_id: UUID) -> None:
        """Remove the objects of a version whose record did not commit; a cleanup failure is logged, never raised."""
        try:
            with self.reading() as db:
                if db.execute(select(versions.c.id).where(versions.c.id == str(version_id))).first() is not None:
                    return
            self.objects.delete_prefix(version_key(version_id))
        except Exception:
            logger.exception("Objects of unpublished version %s could not be removed", version_id)

    def versions(self, project_id: UUID, device: UUID | None = None) -> list[TemplateVersion]:
        """List accepted revisions in publication order, including historical edit bases."""
        self.project(project_id, device)
        with self.reading() as db:
            rows = db.execute(select(versions.c.data).where(versions.c.work_id == str(project_id)).order_by(versions.c.number))
            return [TemplateVersion.model_validate_json(data) for data in rows.scalars()]

    def work_events(self, work_id: UUID, after: int) -> list[history.WorkEvent]:
        """Read committed undelivered events without retaining a connection during stream waits."""
        with self.reading() as db:
            self.require_work(db, work_id)
            return history.events(db, work_id, after)

    def acknowledge(self, work_id: UUID, cursor: int, device: UUID) -> None:
        """Drop events the client has stored locally, then the execution records nothing still needs."""
        with self.transaction() as db:
            self.require_work(db, work_id, device)
            db.execute(delete(work_events).where(work_events.c.work_id == str(work_id), work_events.c.id <= cursor))
            self._prune(db, work_id)

    def _prune(self, db: Connection, work_id: UUID) -> None:
        """Delete delivered execution records of a work once none of its events wait for delivery.

        The latest job retains only routing/status metadata; the acknowledged client owns its input, questions
        and recovery checkpoint. Earlier jobs, progress, rounds and messages go entirely.
        """
        identifier = str(work_id)
        if db.execute(select(work_events.c.id).where(work_events.c.work_id == identifier).limit(1)).first():
            return
        latest = Store._latest(db, work_id)
        finished = [
            row.id for row in db.execute(select(jobs.c.id).where(jobs.c.work_id == identifier, jobs.c.active_work_id.is_(None)))
        ]
        if not finished:
            return
        db.execute(delete(job_progress).where(job_progress.c.job_id.in_(finished)))
        db.execute(delete(job_rounds).where(job_rounds.c.job_id.in_(finished)))
        db.execute(delete(chat_messages).where(chat_messages.c.job_id.in_(finished)))
        db.execute(delete(jobs).where(jobs.c.id.in_(finished), jobs.c.id != str(latest.id)))
        if latest.status not in ACTIVE and str(latest.id) in finished:
            data = db.execute(select(jobs.c.input_data).where(jobs.c.id == str(latest.id))).scalar_one()
            if JobInput.model_validate_json(data).finalized:
                for table in (conversations, work_assets):
                    if table.name in self.legacy_tables:
                        db.execute(delete(table).where(table.c.work_id == identifier))
                db.execute(update(jobs).where(jobs.c.id == str(latest.id)).values(input_data=JobInput(finalized=True).model_dump_json()))
                project = TemplateProject.model_validate_json(db.execute(select(works.c.data).where(works.c.id == identifier)).scalar_one())
                # The client owns replies and outstanding questions too; retain only routing/status metadata.
                latest.questions = []
                latest.reply = None
                if latest.answer is not None:
                    latest.answer = "已回答。"
                if latest.error is not None:
                    latest.error.message = PublicJob.from_job(latest).message or "任务已结束。"
                db.execute(update(jobs).where(jobs.c.id == str(latest.id)).values(data=latest.model_dump_json()))
                project.request = None
                db.execute(update(works).where(works.c.id == identifier).values(data=project.model_dump_json()))

    def expire_events(self, before: datetime) -> None:
        """Drop events nobody acknowledged before the cutoff and prune the works they belonged to."""
        with self.transaction() as db:
            stale = db.execute(select(work_events.c.work_id).where(work_events.c.created_at < naive(before)).distinct()).scalars().all()
            db.execute(delete(work_events).where(work_events.c.created_at < naive(before)))
            for work_id in stale:
                self._prune(db, UUID(work_id))

    def expire_devices(self, before: datetime) -> None:
        """Mark every work of devices inactive since the cutoff for deletion and forget those devices.

        The marked works are then removed like any pending deletion, which the sweep retries until cleanup succeeds.
        """
        with self.transaction() as db:
            stale = db.execute(select(devices.c.device_id).where(devices.c.last_seen_at < naive(before))).scalars().all()
            if not stale:
                return
            db.execute(update(works).where(works.c.device_id.in_(stale)).values(deleting=True))
            db.execute(delete(devices).where(devices.c.device_id.in_(stale)))
        for key in stale:
            self._touched.pop(key, None)
