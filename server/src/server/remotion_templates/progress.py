"""Public host-owned phase summaries, persisted separately from private model/tool diagnostics."""

import json
import sqlite3
from datetime import datetime
from typing import Literal

from .models import Contract, GenerationJob

Phase = Literal[
    "understanding",
    "target_review",
    "answering",
    "generating",
    "rendering",
    "reviewing",
    "sampling",
    "adjusting",
    "adjusting_layout",
    "adjusting_style",
    "adjusting_text",
    "preparing",
]


class ProgressStep(Contract):
    """An observed phase interval; done means the phase ended, not that a candidate was accepted."""

    phase: Phase
    started_at: datetime
    ended_at: datetime | None = None
    status: Literal["active", "done", "stopped"] = "active"


def read_steps(db: sqlite3.Connection, job: GenerationJob) -> list[ProgressStep]:
    """Read only recorded summaries; legacy jobs have no fabricated intermediate history."""
    row = db.execute(
        "SELECT data FROM job_progress WHERE job_id=?", (str(job.id),)
    ).fetchone()
    return (
        [ProgressStep.model_validate(item) for item in json.loads(row[0])]
        if row
        else []
    )


def save_steps(
    db: sqlite3.Connection, job: GenerationJob, steps: list[ProgressStep]
) -> None:
    """Write inside the job/event transaction, so snapshot cursors cannot outrun progress."""
    db.execute(
        "INSERT INTO job_progress(job_id,data) VALUES (?,?) ON CONFLICT(job_id) DO UPDATE SET data=excluded.data",
        (str(job.id), json.dumps([step.model_dump(mode="json") for step in steps])),
    )


def start_step(
    db: sqlite3.Connection, job: GenerationJob, phase: Phase, stamp: datetime
) -> bool:
    """Ignore duplicate active phases and terminal jobs; accept only the public phase whitelist."""
    step = ProgressStep(phase=phase, started_at=stamp)
    if job.status != "running":
        return False
    steps = read_steps(db, job)
    if steps:
        if steps[-1].phase == phase and steps[-1].status == "active":
            return False
        steps[-1].ended_at, steps[-1].status = stamp, "done"
    steps.append(step)
    save_steps(db, job, steps)
    return True


def finish_steps(db: sqlite3.Connection, job: GenerationJob) -> None:
    """Close the last observed interval on completion, failure, cancellation or restart interruption."""
    if job.status in {"queued", "running"}:
        return
    steps = read_steps(db, job)
    if steps and steps[-1].status == "active":
        steps[-1].ended_at = job.updated_at
        steps[-1].status = (
            "done"
            if job.status in {"succeeded", "answered", "needs_input"}
            else "stopped"
        )
        save_steps(db, job, steps)
