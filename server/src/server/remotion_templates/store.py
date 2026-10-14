"""MySQL transactions keep queued work durable and publish accepted revisions atomically.

Works belong to the client device that created them; routes pass the caller's device and a work of another
device reads as missing. The backend runs as a single instance: one in-process lock serializes write
transactions, so the one-active-job rule, job claims and event IDs commit in order on MySQL as on the SQLite
test fixture. Sealed version files and reference images live in the object store (`object_store.py`); the data
folder only holds job scratch, a verified read cache and the private audit logs.
"""

import json
import shutil
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import Connection, delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from ..database import get_engine
from . import history
from .context import Conversation
from .evidence import digest, verify_artifacts
from .models import (
    Asset,
    GenerateTemplateRequest,
    GenerationJob,
    JobError,
    JobInput,
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
# A device's last-seen time is rewritten at most this often.
TOUCH_INTERVAL = timedelta(hours=1)


class NotFound(HTTPException):
    """The requested work, asset, job, or version does not exist (or belongs to another device)."""

    def __init__(self, detail: str) -> None:
        """Use FastAPI's built-in 404 response without registering an exception handler."""
        super().__init__(status_code=404, detail=detail)


class Conflict(HTTPException):
    """The requested state transition conflicts with current durable state."""

    def __init__(self, detail: str) -> None:
        """Use FastAPI's built-in 409 response for concrete conflicting requests."""
        super().__init__(status_code=409, detail=detail)


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
        self._write = threading.RLock()
        self._touched: dict[str, datetime] = {}

    def initialize(self) -> None:
        """Create the tables on first use; existing tables are reused unchanged."""
        metadata.create_all(get_engine())

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        """Run one serialized write transaction that commits or rolls back as a whole."""
        with self._write, get_engine().begin() as db:
            yield db

    @contextmanager
    def reading(self) -> Iterator[Connection]:
        """Open a read connection without taking the write lock."""
        with get_engine().connect() as db:
            yield db

    def touch(self, device: UUID) -> None:
        """Record that a device is active; inactive devices are expired after the retention period."""
        key, stamp = str(device), now()
        if (last := self._touched.get(key)) is not None and stamp - last < TOUCH_INTERVAL:
            return
        with self.transaction() as db:
            if db.execute(update(devices).where(devices.c.device_id == key).values(last_seen_at=naive(stamp))).rowcount == 0:
                db.execute(insert(devices).values(device_id=key, last_seen_at=naive(stamp)))
        self._touched[key] = stamp

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

    def conversation(self, project_id: UUID) -> Conversation:
        """Load only this work's bounded window; a new work has no inherited history."""
        self.project(project_id)
        with self.reading() as db:
            data = db.execute(select(conversations.c.data).where(conversations.c.work_id == str(project_id))).scalar()
        return Conversation(json.loads(data) if data else None)

    def save_conversation(self, project_id: UUID, context: Conversation) -> None:
        """Replace the window rather than appending an unbounded conversation log."""
        data = context.serialize()
        with self.transaction() as db:
            if db.execute(update(conversations).where(conversations.c.work_id == str(project_id)).values(data=data)).rowcount == 0:
                db.execute(insert(conversations).values(work_id=str(project_id), data=data))

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
        with self.reading() as db:
            row = db.execute(select(assets.c.device_id, assets.c.data).where(assets.c.id == str(identifier))).first()
        if row is None or device is not None and row.device_id != str(device):
            raise NotFound("asset not found")
        return Asset.model_validate_json(row.data)

    def save_asset(self, asset: Asset, path: Path, device: UUID) -> None:
        """Store the validated bytes, then register the image for this device."""
        self.objects.put(asset_key(asset.id), path, "image/png")
        try:
            with self.transaction() as db:
                db.execute(insert(assets).values(id=str(asset.id), device_id=str(device), data=asset.model_dump_json()))
        except BaseException:
            self.objects.delete_prefix(asset_key(asset.id))
            raise

    def prune_assets(self, older_than: timedelta = ORPHAN_ASSET_TTL) -> list[UUID]:
        """Delete uploads that no work references once they are older than ``older_than``; return their IDs.

        Works being deleted still reference their images until cleanup finishes, so those stay for it. A storage
        failure rolls back the record removal, as work deletion does.
        """
        cutoff = now() - older_than
        removed = []
        with self.transaction() as db:
            rows = db.execute(select(assets.c.id, assets.c.data).where(
                assets.c.id.not_in(select(work_assets.c.asset_id))
            )).all()
            for row in rows:
                if Asset.model_validate_json(row.data).created_at >= cutoff:
                    continue
                self.objects.delete_prefix(asset_key(UUID(row.id)))
                (self.root / "cache" / "assets" / f"{row.id}.png").unlink(missing_ok=True)
                db.execute(delete(assets).where(assets.c.id == row.id))
                removed.append(UUID(row.id))
        return removed

    def require_references(self, db: Connection, images: list[UUID], device: UUID) -> None:
        """Accept images this device uploaded unless a deleting work still uses one, since cleanup may remove it."""
        for image in images:
            owner = db.execute(select(assets.c.device_id).where(assets.c.id == str(image))).scalar()
            if owner != str(device):
                raise NotFound("asset not found")
            if db.execute(
                select(works.c.id).join(work_assets, work_assets.c.work_id == works.c.id)
                .where(work_assets.c.asset_id == str(image), works.c.deleting.is_(True)).limit(1)
            ).first():
                raise Conflict("reference image belongs to a work being deleted")

    @staticmethod
    def _reference(db: Connection, work_id: UUID, images: list[UUID]) -> None:
        """Record that a work uses these images; an image already recorded for it is kept once."""
        for image in images:
            key = (work_assets.c.work_id == str(work_id), work_assets.c.asset_id == str(image))
            if db.execute(select(work_assets.c.asset_id).where(*key)).first() is None:
                db.execute(insert(work_assets).values(work_id=str(work_id), asset_id=str(image)))

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

    def accepted(self, version: TemplateVersion) -> Path:
        """Return a local copy of a version's sealed files, downloaded on demand and checked against its evidence.

        Raises ValueError or OSError (FileNotFoundError when an object is gone) if the files cannot be verified.
        """
        directory = self.root / "cache" / "accepted" / str(version.id)
        try:
            verify_artifacts(version.candidate, version.spec, version.validation, directory)
            return directory
        except (ValueError, OSError):
            pass
        staged = directory.with_name(f".{directory.name}.{uuid4().hex}")
        staged.mkdir(parents=True)
        try:
            for name in version.validation.artifacts:
                self.objects.fetch(version_key(version.id, name), staged / name)
            verify_artifacts(version.candidate, version.spec, version.validation, staged)
            shutil.rmtree(directory, ignore_errors=True)
            staged.rename(directory)
        finally:
            shutil.rmtree(staged, ignore_errors=True)
        return directory

    def job_dir(self, identifier: UUID) -> Path:
        """Scratch for one execution; removed when the execution ends, except its audit log."""
        return self.root / "jobs" / str(identifier)

    def audit_path(self, identifier: UUID) -> Path:
        """Private audit log of one execution, kept for the audit retention period."""
        return self.root / "audit" / f"{identifier}.jsonl"

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
        ran; arguments and result payloads stay in the private audit. Unknown
        layers are rejected by the contract, terminal jobs accept nothing, and a
        repeated layer/turn pair is ignored so a retried handler cannot duplicate
        a round.
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

    def create(self, request: GenerateTemplateRequest, device: UUID) -> tuple[TemplateProject, GenerationJob]:
        """Atomically create a work of this device and its first queued generation."""
        stamp = now()
        project = TemplateProject(id=uuid4(), request=request, created_at=stamp, updated_at=stamp)
        job = self._new_job(project.id, None)
        images = [image.asset_id for image in request.images]
        with self.transaction() as db:
            self.require_references(db, images, device)
            db.execute(insert(works).values(
                id=str(project.id), device_id=str(device), deleting=False, data=project.model_dump_json(),
            ))
            self._reference(db, project.id, images)
            self._insert_job(
                db, job, JobInput(), request.description or "请参考这张图片制作字效。", images=images,
            )
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
        db.execute(insert(jobs).values(
            id=str(job.id), work_id=str(job.project_id), status=job.status,
            active_work_id=str(job.project_id) if job.status in ACTIVE else None,
            data=job.model_dump_json(), input_data=inputs.model_dump_json(),
        ))
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
        try:
            with self.transaction() as db:
                device = UUID(db.execute(select(works.c.device_id).where(works.c.id == str(project_id))).scalar_one())
                self.require_work(db, project_id)
                self.require_references(db, [image.asset_id for image in inputs.images], device)
                self._reference(db, project_id, [image.asset_id for image in inputs.images])
                self._insert_job(db, job, inputs, message_text, images=message_images)
        except IntegrityError as exc:
            raise Conflict("this work already has a queued or running job") from exc
        return job

    def inputs(self, job_id: UUID) -> JobInput:
        """Recover the exact explicit input of an interrupted or failed execution."""
        with self.reading() as db:
            data = db.execute(select(jobs.c.input_data).where(jobs.c.id == str(job_id))).scalar()
        if data is None:
            raise NotFound("job not found")
        return JobInput.model_validate_json(data)

    def _save_job(self, db: Connection, job: GenerationJob) -> None:
        """Update indexed status and serialized state, and publish the public state change."""
        job.updated_at = now()
        db.execute(update(jobs).where(jobs.c.id == str(job.id)).values(
            status=job.status, data=job.model_dump_json(),
            active_work_id=str(job.project_id) if job.status in ACTIVE else None,
        ))
        history.record_job(db, job)

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
        """Startup never pretends to resume a half-completed model call or render."""
        with self.transaction() as db:
            for data in db.execute(select(jobs.c.data).where(jobs.c.status.in_(ACTIVE))).scalars().all():
                job = GenerationJob.model_validate_json(data)
                job.status, job.stage = "interrupted", "finished"
                job.error = JobError(code="interrupted", message="Server stopped; retry starts a new execution.")
                self._save_job(db, job)

    def publish(
        self,
        job_id: UUID,
        candidate: TemplateCandidate,
        spec: TemplateSpec,
        report: ValidationReport,
        directory: Path,
    ) -> TemplateVersion:
        """Publish generated or user-edited results with distinct gates; cancellation wins over either."""
        verify_artifacts(candidate, spec, report, directory)
        version_id = uuid4()
        uploaded = False
        try:
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
                # Upload the sealed bytes before the record commits; a failed commit removes them again.
                uploaded = True
                for name in report.artifacts:
                    self.objects.put(version_key(version.id, name), directory / name, "application/octet-stream")
                db.execute(insert(versions).values(
                    id=str(version.id), work_id=str(project.id), number=number, data=version.model_dump_json(),
                ))
                project.current_version_id, project.updated_at = version.id, now()
                db.execute(update(works).where(works.c.id == str(project.id)).values(data=project.model_dump_json()))
                job.status, job.stage, job.result_version_id = "succeeded", "finished", version.id
                self._save_job(db, job)
        except BaseException:
            if uploaded:
                self.objects.delete_prefix(version_key(version_id))
            raise
        # Seed the read cache from the verified candidate so the first preview needs no download.
        cached = self.root / "cache" / "accepted" / str(version.id)
        cached.mkdir(parents=True, exist_ok=True)
        for name in report.artifacts:
            shutil.copyfile(directory / name, cached / name)
        return version

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

    @staticmethod
    def _prune(db: Connection, work_id: UUID) -> None:
        """Delete delivered execution records of a work once none of its events wait for delivery.

        The latest job row stays, because retrying it or answering its questions needs its input; its progress,
        rounds and messages go once it has finished. Earlier jobs go entirely.
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

    def expire_events(self, before: datetime) -> None:
        """Drop events nobody acknowledged before the cutoff and prune the works they belonged to."""
        with self.transaction() as db:
            stale = db.execute(select(work_events.c.work_id).where(work_events.c.created_at < naive(before)).distinct()).scalars().all()
            db.execute(delete(work_events).where(work_events.c.created_at < naive(before)))
            for work_id in stale:
                self._prune(db, UUID(work_id))

    def expire_devices(self, before: datetime) -> list[UUID]:
        """Mark every work of devices inactive since the cutoff for deletion and forget those devices."""
        with self.transaction() as db:
            stale = db.execute(select(devices.c.device_id).where(devices.c.last_seen_at < naive(before))).scalars().all()
            if not stale:
                return []
            marked = db.execute(select(works.c.id).where(works.c.device_id.in_(stale))).scalars().all()
            db.execute(update(works).where(works.c.device_id.in_(stale)).values(deleting=True))
            db.execute(delete(devices).where(devices.c.device_id.in_(stale)))
        for key in stale:
            self._touched.pop(key, None)
        return [UUID(value) for value in marked]
