"""Persist public chat and replayable work events independently of the model's sliding window."""

import base64
import json
import sqlite3
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from .models import Contract, GenerationJob, JobInput, PublicJob, TemplateProject
from .progress import ProgressStep, finish_steps, read_steps


class ChatMessage(Contract):
    """One durable public message; normalized assets are referenced rather than embedded."""

    id: UUID
    sequence: int
    job_id: UUID
    role: Literal["user", "assistant"]
    text: str
    image_asset_id: UUID | None = None
    created_at: datetime
    reconstructed: bool = False


class SessionJob(PublicJob):
    """Public task state with timestamps and the user's pending parameter patch."""

    created_at: datetime
    updated_at: datetime
    parameters: dict | None = None
    progress: list[ProgressStep] = Field(default_factory=list)


class WorkEvent(Contract):
    """A persisted public event belongs to exactly one work and has a stable replay ID."""

    id: int
    work_id: UUID
    type: Literal["message.created", "job.updated", "version.ready"]
    data: dict
    created_at: datetime


class SessionSnapshot(Contract):
    """One read transaction binds history, task, accepted pointer and stream watermark."""

    work: TemplateProject
    job: SessionJob
    messages: list[ChatMessage]
    next_before: int | None
    cursor: int
    jobs: list[SessionJob] = Field(default_factory=list)


class WorkSummary(Contract):
    """Sidebar metadata excludes code and internal execution diagnostics."""

    id: UUID
    title: str
    deleting: bool = False
    updated_at: datetime
    current_version_id: UUID | None
    job: SessionJob


class WorkPage(Contract):
    """Keyset pagination is ordered by recent activity and work ID."""

    items: list[WorkSummary]
    next_cursor: str | None


def initialize(db: sqlite3.Connection) -> None:
    """Add public tables without rewriting existing works, versions or model context."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
            work_id TEXT NOT NULL REFERENCES projects(id), job_id TEXT NOT NULL REFERENCES jobs(id),
            role TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(job_id, role)
        );
        CREATE INDEX IF NOT EXISTS chat_by_work ON chat_messages(work_id, sequence);
        CREATE TABLE IF NOT EXISTS work_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, work_id TEXT NOT NULL REFERENCES projects(id),
            type TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS events_by_work ON work_events(work_id, id);
        CREATE TABLE IF NOT EXISTS job_progress (
            job_id TEXT PRIMARY KEY REFERENCES jobs(id), data TEXT NOT NULL
        );
    """)
    # Only jobs missing their user message need backfilling. Never expose conversations/tool records.
    rows = db.execute("""SELECT j.data, j.input_data FROM jobs j
        WHERE NOT EXISTS (SELECT 1 FROM chat_messages m WHERE m.job_id=j.id AND m.role='user')
        ORDER BY j.created_at, j.rowid""").fetchall()
    for row in rows:
        job = GenerationJob.model_validate_json(row["data"])
        record_input(
            db, job, JobInput.model_validate_json(row["input_data"]), reconstructed=True
        )
        record_job(db, job, reconstructed=True)


def append_event(
    db: sqlite3.Connection, work_id: UUID, kind: str, data: dict, stamp: datetime
) -> None:
    """Append within the caller's state transaction; readers only see committed events."""
    db.execute(
        "INSERT INTO work_events(work_id,type,data,created_at) VALUES (?,?,?,?)",
        (str(work_id), kind, json.dumps(data, ensure_ascii=False), stamp.isoformat()),
    )


def append_message(
    db: sqlite3.Connection,
    job: GenerationJob,
    role: str,
    text: str,
    *,
    image: UUID | None = None,
    reconstructed: bool = False,
) -> None:
    """Publish each task's user/assistant message once, including on retry and replay."""
    identifier = uuid4()
    row = db.execute(
        "INSERT OR IGNORE INTO chat_messages(id,work_id,job_id,role,data) VALUES (?,?,?,?,?)",
        (str(identifier), str(job.project_id), str(job.id), role, "{}"),
    )
    if row.rowcount == 0:
        return
    message = ChatMessage(
        id=identifier,
        sequence=row.lastrowid,
        job_id=job.id,
        role=role,
        text=text,
        image_asset_id=image,
        created_at=job.created_at if role == "user" else job.updated_at,
        reconstructed=reconstructed,
    )
    db.execute(
        "UPDATE chat_messages SET data=? WHERE id=?",
        (message.model_dump_json(), str(identifier)),
    )
    append_event(
        db,
        job.project_id,
        "message.created",
        message.model_dump(mode="json"),
        message.created_at,
    )


def record_input(
    db: sqlite3.Connection,
    job: GenerationJob,
    inputs: JobInput,
    text: str | None = None,
    *,
    reconstructed: bool = False,
) -> None:
    """Record persisted input; reconstructed messages never quote the private model context."""
    first = db.execute(
        "SELECT id FROM jobs WHERE project_id=? ORDER BY rowid LIMIT 1",
        (str(job.project_id),),
    ).fetchone()[0] == str(job.id)
    image = None
    if first:
        row = db.execute(
            "SELECT data FROM projects WHERE id=?", (str(job.project_id),)
        ).fetchone()
        project = TemplateProject.model_validate_json(row[0])
        text = project.request.description or "请参考这张图片制作字效。"
        image = project.request.image.asset_id if project.request.image else None
    elif text is None:
        if inputs.clarifications:
            answer = json.loads(inputs.clarifications[-1])
            text = answer["answer"]
        elif inputs.parameters is not None:
            text = "调整模板参数。"
        else:
            text = inputs.instruction or "重试本次制作。"
    append_message(db, job, "user", text, image=image, reconstructed=reconstructed)


def public_job(db: sqlite3.Connection, job: GenerationJob) -> SessionJob:
    """Expose timestamps and input scalars, never provider errors, stages, attempts or tool traces."""
    row = db.execute(
        "SELECT input_data FROM jobs WHERE id=?", (str(job.id),)
    ).fetchone()
    inputs = JobInput.model_validate_json(row[0])
    return SessionJob(
        **PublicJob.from_job(job).model_dump(),
        created_at=job.created_at,
        updated_at=job.updated_at,
        parameters=inputs.parameters,
        progress=read_steps(db, job),
    )


def record_job(
    db: sqlite3.Connection, job: GenerationJob, *, reconstructed: bool = False
) -> None:
    """Suppress internal-only updates and atomically attach terminal messages and success pointers."""
    finish_steps(db, job)
    state = public_job(db, job).model_dump(mode="json")
    previous = db.execute(
        "SELECT data FROM work_events WHERE work_id=? AND type='job.updated' ORDER BY id DESC LIMIT 1",
        (str(job.project_id),),
    ).fetchone()
    if previous:
        old = json.loads(previous[0])
        if {k: v for k, v in old.items() if k != "updated_at"} == {
            k: v for k, v in state.items() if k != "updated_at"
        }:
            return
    append_event(db, job.project_id, "job.updated", state, job.updated_at)
    text = None
    if job.status == "needs_input":
        text = "\n".join(job.questions)
    elif job.status == "answered":
        text = job.answer
    elif job.status == "succeeded":
        text = "模板已就绪。可以调整参数，或继续描述你想修改的效果。"
        if job.result_version_id is not None:
            append_event(
                db,
                job.project_id,
                "version.ready",
                {"version_id": str(job.result_version_id), "job_id": str(job.id)},
                job.updated_at,
            )
    elif job.status == "cancelled":
        text = "已停止本次制作，已有成功模板仍可使用。"
    elif job.status in {"failed", "interrupted"}:
        text = PublicJob.from_job(job).message
    if text:
        append_message(db, job, "assistant", text, reconstructed=reconstructed)


def snapshot(
    db: sqlite3.Connection, work_id: UUID, before: int | None, limit: int
) -> SessionSnapshot | None:
    """Read a consistent bounded history page and watermark; caller owns the read transaction."""
    row = db.execute("SELECT data FROM projects WHERE id=?", (str(work_id),)).fetchone()
    if row is None:
        return None
    work = TemplateProject.model_validate_json(row[0])
    latest = db.execute(
        "SELECT data FROM jobs WHERE project_id=? ORDER BY rowid DESC LIMIT 1",
        (str(work_id),),
    ).fetchone()
    rows = db.execute(
        "SELECT data FROM chat_messages WHERE work_id=? AND (? IS NULL OR sequence<?) ORDER BY sequence DESC LIMIT ?",
        (str(work_id), before, before, limit + 1),
    ).fetchall()
    messages = [ChatMessage.model_validate_json(row[0]) for row in rows[:limit]][::-1]
    cursor = db.execute(
        "SELECT COALESCE(MAX(id),0) FROM work_events WHERE work_id=?", (str(work_id),)
    ).fetchone()[0]
    return SessionSnapshot(
        work=work,
        job=public_job(db, GenerationJob.model_validate_json(latest[0])),
        messages=messages,
        next_before=messages[0].sequence if len(rows) > limit else None,
        cursor=cursor,
        jobs=[
            public_job(
                db,
                GenerationJob.model_validate_json(
                    db.execute(
                        "SELECT data FROM jobs WHERE id=? AND project_id=?",
                        (str(identifier), str(work_id)),
                    ).fetchone()[0]
                ),
            )
            for identifier in dict.fromkeys(message.job_id for message in messages)
        ],
    )


def events(
    db: sqlite3.Connection, work_id: UUID, after: int, limit: int = 100
) -> list[WorkEvent]:
    """Replay an ordered bounded page for one work; event IDs may contain gaps across works."""
    rows = db.execute(
        "SELECT * FROM work_events WHERE work_id=? AND id>? ORDER BY id LIMIT ?",
        (str(work_id), after, limit),
    ).fetchall()
    return [
        WorkEvent(
            id=row["id"],
            work_id=work_id,
            type=row["type"],
            data=json.loads(row["data"]),
            created_at=row["created_at"],
        )
        for row in rows
    ]


def work_page(db: sqlite3.Connection, cursor: str | None, limit: int) -> WorkPage:
    """Use an opaque activity timestamp/ID cursor without loading code or the full chat log."""
    stamp, identifier = None, None
    if cursor:
        try:
            stamp, identifier = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            datetime.fromisoformat(stamp)
            UUID(identifier)
        except (ValueError, TypeError, UnicodeError) as exc:
            raise ValueError("invalid history cursor") from exc
    rows = db.execute(
        """SELECT p.id,p.data,MAX(e.created_at) AS activity FROM projects p
        JOIN work_events e ON e.work_id=p.id GROUP BY p.id
        HAVING (? IS NULL OR (activity,p.id)<(?,?)) ORDER BY activity DESC,p.id DESC LIMIT ?""",
        (stamp, stamp, identifier, limit + 1),
    ).fetchall()
    items = []
    for row in rows[:limit]:
        work = TemplateProject.model_validate_json(row["data"])
        job = db.execute(
            "SELECT data FROM jobs WHERE project_id=? ORDER BY rowid DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        items.append(
            WorkSummary(
                id=work.id,
                deleting=db.execute(
                    "SELECT 1 FROM work_deletions WHERE work_id=?", (str(work.id),)
                ).fetchone() is not None,
                title=(work.request.description or "图片字效")[:40],
                updated_at=row["activity"],
                current_version_id=work.current_version_id,
                job=public_job(db, GenerationJob.model_validate_json(job[0])),
            )
        )
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = base64.urlsafe_b64encode(
            json.dumps([last["activity"], last["id"]]).encode()
        ).decode()
    return WorkPage(items=items, next_cursor=next_cursor)
