"""SQLite transactions keep queued work durable and publish accepted revisions atomically."""

import json
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator
from uuid import UUID, uuid4

from fastapi import HTTPException

from . import history
from .context import Conversation
from .evidence import verify_artifacts
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
)
from .trajectory import Trajectory


class NotFound(HTTPException):
    """The requested work, asset, job, or version does not exist."""

    def __init__(self, detail: str) -> None:
        """Use FastAPI's built-in 404 response without registering an exception handler."""
        super().__init__(status_code=404, detail=detail)


class Conflict(HTTPException):
    """The requested state transition conflicts with current durable state."""

    def __init__(self, detail: str) -> None:
        """Use FastAPI's built-in 409 response for concrete conflicting requests."""
        super().__init__(status_code=409, detail=detail)


def now() -> datetime:
    """Use timezone-aware timestamps consistently in records and event ordering."""
    return datetime.now(UTC)


class Store:
    """Small synchronous metadata transactions; rendering never holds a database lock."""

    def __init__(self, root: Path) -> None:
        """Locate durable state; initialization is explicit so importing ASGI is read-only."""
        self.root = root
        self.path = root / "templates.sqlite3"

    def initialize(self) -> None:
        """Create the MVP schema and enforce one active modification per work."""
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    status TEXT NOT NULL, created_at TEXT NOT NULL,
                    data TEXT NOT NULL, input_data TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_work
                    ON jobs(project_id) WHERE status IN ('queued', 'running');
                CREATE TABLE IF NOT EXISTS versions (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    number INTEGER NOT NULL, data TEXT NOT NULL,
                    UNIQUE(project_id, number)
                );
                CREATE TABLE IF NOT EXISTS conversations (project_id TEXT PRIMARY KEY REFERENCES projects(id), data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id), data TEXT NOT NULL
                );
            """)
            history.initialize(db)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Commit one transaction or roll it back, always closing its connection."""
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _get(self, table: str, identifier: UUID, model):
        """Read a typed record from a caller-selected internal table."""
        with self.connection() as db:
            row = db.execute(
                f"SELECT data FROM {table} WHERE id=?", (str(identifier),)
            ).fetchone()
        if row is None:
            raise NotFound(f"{table.rstrip('s')} not found")
        return model.model_validate_json(row["data"])

    def conversation(self, project_id: UUID) -> Conversation:
        """Load only this task's bounded window; a new task has no inherited history."""
        self.project(project_id)
        with self.connection() as db:
            row = db.execute(
                "SELECT data FROM conversations WHERE project_id=?", (str(project_id),)
            ).fetchone()
        return Conversation(json.loads(row["data"]) if row else None)

    def save_conversation(self, project_id: UUID, context: Conversation) -> None:
        """Replace the window rather than appending an unbounded conversation log."""
        with self.connection() as db:
            db.execute(
                "INSERT INTO conversations VALUES (?, ?) ON CONFLICT(project_id) DO UPDATE SET data=excluded.data",
                (str(project_id), context.serialize()),
            )

    def latest_job(self, project_id: UUID) -> GenerationJob:
        """Bind clarification replies to the latest execution, including after a retry finishes."""
        self.project(project_id)
        with self.connection() as db:
            row = db.execute(
                "SELECT data FROM jobs WHERE project_id=? ORDER BY rowid DESC LIMIT 1",
                (str(project_id),),
            ).fetchone()
        if row is None:
            raise NotFound("job not found")
        return GenerationJob.model_validate_json(row["data"])

    def project(self, identifier: UUID) -> TemplateProject:
        """Return the work and its current accepted version pointer."""
        return self._get("projects", identifier, TemplateProject)

    def job(self, identifier: UUID) -> GenerationJob:
        """Read private execution state; HTTP routes project only user-visible fields."""
        return self._get("jobs", identifier, GenerationJob)

    def version(self, identifier: UUID) -> TemplateVersion:
        """Read an accepted, immutable version."""
        return self._get("versions", identifier, TemplateVersion)

    def asset(self, identifier: UUID) -> Asset:
        """Only registered normalized images can be used as references."""
        return self._get("assets", identifier, Asset)

    def save_asset(self, asset: Asset) -> None:
        """Register an image after its validated bytes have been written."""
        with self.connection() as db:
            db.execute(
                "INSERT INTO assets VALUES (?, ?)",
                (str(asset.id), asset.model_dump_json()),
            )

    def asset_path(self, identifier: UUID) -> Path:
        """Resolve an asset by server-generated ID, never a user-supplied filename."""
        self.asset(identifier)
        return self.root / "assets" / f"{identifier}.png"

    def job_dir(self, identifier: UUID) -> Path:
        """Keep candidate files separate from both uploaded assets and source code."""
        return self.root / "jobs" / str(identifier)

    def progress(self, identifier: UUID, phase: str) -> None:
        """Atomically publish whitelisted host phase transitions through the existing work stream."""
        from .progress import start_step

        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT data FROM jobs WHERE id=?", (str(identifier),)
            ).fetchone()
            if row is None:
                raise NotFound("job not found")
            job = GenerationJob.model_validate_json(row[0])
            if start_step(db, job, phase, now()):
                self._save_job(db, job)

    def create(
        self, request: GenerateTemplateRequest
    ) -> tuple[TemplateProject, GenerationJob]:
        """Atomically create a work and its first queued generation."""
        stamp = now()
        project = TemplateProject(
            id=uuid4(), request=request, created_at=stamp, updated_at=stamp
        )
        job = self._new_job(project.id, None)
        with self.connection() as db:
            db.execute(
                "INSERT INTO projects VALUES (?, ?)",
                (str(project.id), project.model_dump_json()),
            )
            self._insert_job(db, job, JobInput())
        return project, job

    def _new_job(self, project_id: UUID, base: UUID | None) -> GenerationJob:
        """Assign execution identity before it enters the shared FIFO queue."""
        stamp = now()
        return GenerationJob(
            id=uuid4(),
            project_id=project_id,
            base_version_id=base,
            status="queued",
            stage="queued",
            created_at=stamp,
            updated_at=stamp,
        )

    def _insert_job(
        self,
        db: sqlite3.Connection,
        job: GenerationJob,
        inputs: JobInput,
        message_text: str | None = None,
    ) -> None:
        """Insert a queued job and the first replayable progress event."""
        db.execute(
            "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(job.id),
                str(job.project_id),
                job.status,
                job.created_at.isoformat(),
                job.model_dump_json(),
                inputs.model_dump_json(),
            ),
        )
        history.record_input(db, job, inputs, message_text)
        self._event(db, job)

    def enqueue(
        self,
        project_id: UUID,
        inputs: JobInput,
        base: UUID | None,
        *,
        message_text: str | None = None,
    ) -> GenerationJob:
        """Reject concurrent edits and foreign version IDs before enqueueing any work."""
        self.project(project_id)
        if base is not None and self.version(base).project_id != project_id:
            raise Conflict("base version belongs to another work")
        job = self._new_job(project_id, base)
        try:
            with self.connection() as db:
                self._insert_job(db, job, inputs, message_text)
        except sqlite3.IntegrityError as exc:
            raise Conflict("this work already has a queued or running job") from exc
        return job

    def inputs(self, job_id: UUID) -> JobInput:
        """Recover the exact explicit input of an interrupted or failed execution."""
        with self.connection() as db:
            row = db.execute(
                "SELECT input_data FROM jobs WHERE id=?", (str(job_id),)
            ).fetchone()
        if row is None:
            raise NotFound("job not found")
        return JobInput.model_validate_json(row["input_data"])

    def _event(self, db: sqlite3.Connection, job: GenerationJob) -> None:
        """Persist private snapshots and public state events in the same transaction as the job."""
        db.execute(
            "INSERT INTO events(job_id, data) VALUES (?, ?)",
            (str(job.id), job.model_dump_json()),
        )
        history.record_job(db, job)

    def _save_job(self, db: sqlite3.Connection, job: GenerationJob) -> None:
        """Update indexed status and serialized state together."""
        job.updated_at = now()
        db.execute(
            "UPDATE jobs SET status=?, data=? WHERE id=?",
            (job.status, job.model_dump_json(), str(job.id)),
        )
        self._event(db, job)

    def update(self, job_id: UUID, **changes) -> GenerationJob:
        """Advance an active job; terminal jobs cannot be resurrected by late results."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT data FROM jobs WHERE id=?", (str(job_id),)
            ).fetchone()
            if row is None:
                raise NotFound("job not found")
            job = GenerationJob.model_validate_json(row["data"])
            if job.status not in {"queued", "running"}:
                return job
            job = GenerationJob.model_validate(job.model_dump() | changes)
            self._save_job(db, job)
        return job

    def claim(self) -> GenerationJob | None:
        """Claim the oldest queued execution atomically for the single local worker."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT data FROM jobs WHERE status='queued' ORDER BY created_at, rowid LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            job = GenerationJob.model_validate_json(row["data"])
            job.status, job.stage = "running", "analyzing"
            self._save_job(db, job)
            return job

    def interrupt_unfinished(self) -> None:
        """Startup never pretends to resume a half-completed model call or render."""
        with self.connection() as db:
            rows = db.execute(
                "SELECT data FROM jobs WHERE status IN ('queued', 'running')"
            ).fetchall()
            for row in rows:
                job = GenerationJob.model_validate_json(row["data"])
                job.status, job.stage = "interrupted", "finished"
                job.error = JobError(
                    code="interrupted",
                    message="Server stopped; retry starts a new execution.",
                )
                self._save_job(db, job)

    def publish(
        self,
        job_id: UUID,
        candidate: TemplateCandidate,
        spec: TemplateSpec,
        report: ValidationReport,
        directory: Path,
    ) -> TemplateVersion:
        """Publish only current accepted evidence; cancellation wins over a late result."""
        verify_artifacts(candidate, spec, report, directory)
        assessment = Trajectory().observe(candidate, spec, report)
        if not assessment.completion_allowed:
            raise Conflict(
                "candidate cannot be published: " + "; ".join(assessment.feedback)
            )
        accepted = None
        created = False
        try:
            with self.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT data FROM jobs WHERE id=?", (str(job_id),)
                ).fetchone()
                if row is None:
                    raise NotFound("job not found")
                job = GenerationJob.model_validate_json(row["data"])
                if job.status != "running":
                    raise Conflict("only a running job may publish a version")
                row = db.execute(
                    "SELECT data FROM projects WHERE id=?", (str(job.project_id),)
                ).fetchone()
                project = TemplateProject.model_validate_json(row["data"])
                number = db.execute(
                    "SELECT COALESCE(MAX(number), 0)+1 FROM versions WHERE project_id=?",
                    (str(project.id),),
                ).fetchone()[0]
                version = TemplateVersion(
                    id=uuid4(),
                    project_id=project.id,
                    job_id=job.id,
                    number=number,
                    base_version_id=job.base_version_id,
                    candidate=candidate,
                    spec=spec,
                    validation=report,
                    created_at=now(),
                )
                # Copy sealed bytes into a publication directory; retries never write into accepted output.
                accepted = self.root / "accepted" / str(version.id)
                accepted.mkdir(parents=True)
                created = True
                for name in report.artifacts:
                    shutil.copyfile(directory / name, accepted / name)
                verify_artifacts(candidate, spec, report, accepted)
                db.execute(
                    "INSERT INTO versions VALUES (?, ?, ?, ?)",
                    (
                        str(version.id),
                        str(project.id),
                        number,
                        version.model_dump_json(),
                    ),
                )
                project.current_version_id, project.updated_at = version.id, now()
                db.execute(
                    "UPDATE projects SET data=? WHERE id=?",
                    (project.model_dump_json(), str(project.id)),
                )
                job.status, job.stage, job.result_version_id = (
                    "succeeded",
                    "finished",
                    version.id,
                )
                self._save_job(db, job)
                return version
        except BaseException:
            if created:
                shutil.rmtree(accepted, ignore_errors=True)
            raise

    def versions(self, project_id: UUID) -> list[TemplateVersion]:
        """List accepted revisions in publication order, including historical edit bases."""
        self.project(project_id)
        with self.connection() as db:
            rows = db.execute(
                "SELECT data FROM versions WHERE project_id=? ORDER BY number",
                (str(project_id),),
            ).fetchall()
        return [TemplateVersion.model_validate_json(row["data"]) for row in rows]

    def projects(self, limit: int = 100) -> list[TemplateProject]:
        """List recent works without including large code artifacts."""
        with self.connection() as db:
            rows = db.execute(
                "SELECT data FROM projects ORDER BY rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [TemplateProject.model_validate_json(row["data"]) for row in rows]

    def events(self, job_id: UUID, after: int) -> list[tuple[int, str]]:
        """Return a bounded page of internal progress records for cursor-based HTTP projection."""
        self.job(job_id)
        with self.connection() as db:
            rows = db.execute(
                "SELECT id, data FROM events WHERE job_id=? AND id>? ORDER BY id LIMIT 100",
                (str(job_id), after),
            ).fetchall()
        return [(row["id"], row["data"]) for row in rows]

    def session(
        self, work_id: UUID, before: int | None = None, limit: int = 50
    ) -> history.SessionSnapshot:
        """Bind public messages, accepted pointer and event watermark to one SQLite snapshot."""
        with self.connection() as db:
            db.execute("BEGIN")
            result = history.snapshot(db, work_id, before, limit)
        if result is None:
            raise NotFound("work not found")
        return result

    def work_history(
        self, cursor: str | None = None, limit: int = 30
    ) -> history.WorkPage:
        """Return a bounded public sidebar page ordered by recent activity."""
        with self.connection() as db:
            db.execute("BEGIN")
            return history.work_page(db, cursor, limit)

    def work_events(self, work_id: UUID, after: int) -> list[history.WorkEvent]:
        """Read committed public events without retaining a connection during stream waits."""
        with self.connection() as db:
            return history.events(db, work_id, after)

    def validate_cursor(self, work_id: UUID, after: int) -> None:
        """Reject foreign or stale database cursors before opening SSE; zero requests full replay."""
        self.project(work_id)
        if after == 0:
            return
        with self.connection() as db:
            row = db.execute(
                "SELECT work_id FROM work_events WHERE id=?", (after,)
            ).fetchone()
        if row is None or row[0] != str(work_id):
            raise Conflict(
                "Event cursor is unavailable for this work; reload its session."
            )
