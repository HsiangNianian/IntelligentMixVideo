"""Public chat and replayable work events, buffered in MySQL until the client acknowledges them.

The client keeps chat history in its own local SQLite; the server only appends public messages and events inside
the job transactions that produce them and removes them once delivered (`store.acknowledge`). The model's
sliding window never enters these records; the loop record (`progress.LoopRound`) publishes each tool call's
clipped arguments and error text for auditing, while tool results stay in the private audit.
"""

import json
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field
from sqlalchemy import Connection, insert, select

from .models import Contract, GenerationJob, JobInput, PublicJob
from .progress import LoopRound, ProgressStep, finish_steps, read_rounds, read_steps
from .tables import chat_messages, jobs, work_events


class ChatMessage(Contract):
    """One public message; normalized assets are referenced rather than embedded."""

    id: UUID
    sequence: int
    job_id: UUID
    role: Literal["user", "assistant"]
    text: str
    image_asset_ids: list[UUID] = Field(default_factory=list)
    # Directions the Agent offers after a result; the client shows them as options the user can send as the next instruction.
    suggestions: list[str] = Field(default_factory=list)
    created_at: datetime


class SessionJob(PublicJob):
    """Public task state with timestamps and the user's pending parameter patch."""

    created_at: datetime
    updated_at: datetime
    parameters: dict | None = None
    progress: list[ProgressStep] = Field(default_factory=list)
    rounds: list[LoopRound] = Field(default_factory=list)


class WorkEvent(Contract):
    """A buffered public event belongs to exactly one work and has a stable replay ID."""

    id: int
    work_id: UUID
    type: Literal["message.created", "job.updated", "job.round", "version.ready"]
    data: dict
    created_at: datetime


# Key in `Connection.info` listing the works whose events a transaction appended; `Store.transaction` wakes their streams.
APPENDED = "remotion_appended_works"


def append_event(db: Connection, work_id: UUID, kind: str, data: dict, stamp: datetime) -> None:
    """Append within the caller's state transaction; readers only see committed events."""
    db.info.setdefault(APPENDED, set()).add(str(work_id))
    db.execute(insert(work_events).values(
        work_id=str(work_id), type=kind, data=json.dumps(data, ensure_ascii=False),
        created_at=stamp.astimezone(UTC).replace(tzinfo=None),
    ))


def append_message(db: Connection, job: GenerationJob, role: str, text: str, *, images: list[UUID] | None = None, suggestions: list[str] | None = None) -> None:
    """Publish each task's user/assistant message once, including on retry and replay."""
    exists = db.execute(select(chat_messages.c.id).where(
        chat_messages.c.job_id == str(job.id), chat_messages.c.role == role,
    )).first()
    if exists is not None:
        return
    identifier = uuid4()
    sequence = db.execute(insert(chat_messages).values(
        id=str(identifier), work_id=str(job.project_id), job_id=str(job.id), role=role, data="{}",
    )).inserted_primary_key[0]
    message = ChatMessage(
        id=identifier,
        sequence=sequence,
        job_id=job.id,
        role=role,
        text=text,
        image_asset_ids=images or [],
        suggestions=suggestions or [],
        created_at=job.created_at if role == "user" else job.updated_at,
    )
    db.execute(chat_messages.update().where(chat_messages.c.id == str(identifier)).values(data=message.model_dump_json()))
    append_event(db, job.project_id, "message.created", message.model_dump(mode="json"), message.created_at)


def record_input(
    db: Connection, job: GenerationJob, inputs: JobInput, text: str | None = None, *, images: list[UUID] | None = None,
) -> None:
    """Record the user's message for a new execution with the reference images attached to it.

    The first job passes the work's description and original images; later messages pass their own images.
    """
    if text is None:
        if inputs.clarifications:
            text = json.loads(inputs.clarifications[-1])["answer"]
        elif inputs.parameters is not None:
            text = "调整模板参数。"
        else:
            # An image-only message has no text; the client shows its own caption beside the image.
            text = inputs.instruction or ("" if images else "重试本次制作。")
    append_message(db, job, "user", text, images=images)


def public_job(db: Connection, job: GenerationJob, *, include_rounds: bool = True) -> SessionJob:
    """Expose timestamps, input scalars, the phase timeline and the loop record; the job's own failure stays a fixed message."""
    inputs = JobInput.model_validate_json(
        db.execute(select(jobs.c.input_data).where(jobs.c.id == str(job.id))).scalar_one()
    )
    return SessionJob(
        **PublicJob.from_job(job).model_dump(),
        created_at=job.created_at,
        updated_at=job.updated_at,
        parameters=inputs.parameters,
        progress=read_steps(db, job),
        rounds=read_rounds(db, job) if include_rounds else [],
    )


def record_job(db: Connection, job: GenerationJob) -> None:
    """Suppress internal-only updates and atomically attach terminal messages and success pointers."""
    finish_steps(db, job)
    state = public_job(db, job, include_rounds=False).model_dump(mode="json", exclude={"rounds"})
    previous = db.execute(
        select(work_events.c.data).where(work_events.c.work_id == str(job.project_id), work_events.c.type == "job.updated")
        .order_by(work_events.c.id.desc()).limit(1)
    ).scalar()
    if previous:
        old = json.loads(previous)
        if {k: v for k, v in old.items() if k not in {"updated_at", "rounds"}} == {
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
        text = reply_text(job.reply) if job.reply is not None else "模板已就绪。可以调整参数，或继续描述你想修改的效果。"
        if job.result_version_id is not None:
            append_event(
                db, job.project_id, "version.ready",
                {"version_id": str(job.result_version_id), "job_id": str(job.id)}, job.updated_at,
            )
    elif job.status == "cancelled":
        text = "已停止本次制作，已有成功模板仍可使用。"
    elif job.status in {"failed", "interrupted"}:
        text = PublicJob.from_job(job).message
    if text:
        append_message(db, job, "assistant", text, suggestions=job.reply.suggestions if job.status == "succeeded" and job.reply else None)


def reply_text(reply) -> str:
    """The assistant message of a successful free-form task: what was built, then how anything ambiguous was read."""
    parts = [reply.summary]
    if reply.choices:
        parts.append("我是这样理解的：\n" + "\n".join(f"- {item}" for item in reply.choices))
    parts.append("可以直接调整参数，或继续描述想修改的地方。")
    return "\n\n".join(parts)


def events(db: Connection, work_id: UUID, after: int, limit: int = 100) -> list[WorkEvent]:
    """Replay an ordered bounded page of undelivered events for one work; IDs may contain gaps across works."""
    rows = db.execute(
        select(work_events).where(work_events.c.work_id == str(work_id), work_events.c.id > after)
        .order_by(work_events.c.id).limit(limit)
    )
    return [
        WorkEvent(id=row.id, work_id=work_id, type=row.type, data=json.loads(row.data),
                  created_at=row.created_at.replace(tzinfo=UTC))
        for row in rows
    ]
