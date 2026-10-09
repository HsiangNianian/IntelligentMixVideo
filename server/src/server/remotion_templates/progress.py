"""Public host-owned phase and loop summaries, persisted separately from private model/tool diagnostics."""

import json
import sqlite3
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

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


# Public summaries use host-authored vocabulary, never exception strings or model names.
PUBLIC_TOOLS = {
    "tools.plan_execute", "tools.inspect", "preset.create", "preset.modify",
    "preset.search", "sprite.compose", "sprite.create", "validate.code",
    "validate.render", "image.info", "image.resize", "image.crop",
}
PUBLIC_ERRORS = {
    "INVALID_ARGUMENT": "工具参数无效。",
    "TOOL_NOT_FOUND": "工具不可用。",
    "CODE_VALIDATION_FAILED": "代码校验未通过。",
    "COMPOSITION_FAILED": "组合生成失败。",
    "PRESET_STORE_FAILED": "预设保存失败。",
    "SPRITE_STORE_FAILED": "字效保存失败。",
    "PRESET_NOT_FOUND": "预设不存在。",
    "VALIDATION_UNAVAILABLE": "校验服务暂不可用。",
    "RESOURCE_LIMIT_EXCEEDED": "已达到资源限制。",
    "TIMEOUT": "工具执行超时。",
    "NOT_IMPLEMENTED": "工具尚不可用。",
    "CANCELLED": "工具执行已取消。",
    "INTERRUPTED": "工具执行已中断。",
    "NOT_EXECUTED": "前序调用中断，本次未执行。",
    "tool_limit_reached": "本批次工具调用已达到上限。",
    "tool_budget_exhausted": "工具调用预算已用尽。",
    "step_limit_reached": "计划步骤已达到上限。",
    "agent_stopped": "任务已停止。",
    "TOOL_FAILED": "工具执行失败。",
}


class RoundCall(Contract):
    """One tool call the host actually ran; the name and the outcome only.

    Arguments and result payloads stay in the private audit, so a public round can
    never carry model input, candidate source or tool output.
    """

    tool: str = Field(min_length=1, max_length=64)
    status: Literal["pass", "fail"]
    error_code: str | None = Field(default=None, max_length=64)
    message: str | None = Field(default=None, max_length=200)

    @model_validator(mode="before")
    @classmethod
    def public_summary(cls, value):
        """Sanitize both new receipts and legacy stored summaries at the public boundary."""
        if not isinstance(value, dict):
            return value
        value = dict(value)
        tool = value.get("tool")
        value["tool"] = tool if isinstance(tool, str) and tool in PUBLIC_TOOLS else "unknown"
        code = value.get("error_code")
        if value.get("status") == "fail":
            code = code if isinstance(code, str) and code in PUBLIC_ERRORS else "TOOL_FAILED"
            value.update(error_code=code, message=PUBLIC_ERRORS[code])
        else:
            value.update(error_code=None, message=None)
        return value


class LoopRound(Contract):
    """One attempted ReAct turn; model failures are distinct from message-only turns."""

    layer: Literal["outer", "plan", "executor"]
    turn: int = Field(ge=1)
    calls: list[RoundCall] = Field(default_factory=list)
    error_code: Literal["MODEL_CONTRACT_FAILED", "MODEL_FAILED", "CANCELLED"] | None = None


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


def read_rounds(db: sqlite3.Connection, job: GenerationJob) -> list[LoopRound]:
    """Read only recorded rounds; jobs without records never get fabricated history."""
    rows = db.execute(
        "SELECT data FROM job_rounds WHERE job_id=? ORDER BY turn, layer", (str(job.id),)
    )
    return [LoopRound.model_validate_json(row[0]) for row in rows]


def save_round(db: sqlite3.Connection, job_id: str, record: LoopRound) -> bool:
    """Insert one immutable round; uniqueness makes retried reporting idempotent."""
    result = db.execute(
        "INSERT INTO job_rounds(job_id,layer,turn,data) VALUES (?,?,?,?) "
        "ON CONFLICT(job_id,layer,turn) DO NOTHING",
        (job_id, record.layer, record.turn, record.model_dump_json()),
    )
    return result.rowcount == 1


def initialize_rounds(db: sqlite3.Connection) -> None:
    """Migrate list storage and sanitize legacy replay data before any job is parsed."""
    db.execute("BEGIN IMMEDIATE")
    columns = {row[1] for row in db.execute("PRAGMA table_info(job_rounds)")}
    legacy = bool(columns) and "turn" not in columns
    if legacy:
        db.execute("ALTER TABLE job_rounds RENAME TO legacy_job_rounds")
    db.execute("""CREATE TABLE IF NOT EXISTS job_rounds (
        job_id TEXT NOT NULL REFERENCES jobs(id), layer TEXT NOT NULL,
        turn INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY(job_id, layer, turn)
    )""")
    if legacy:
        for row in db.execute("SELECT job_id,data FROM legacy_job_rounds").fetchall():
            for record in json.loads(row[1]):
                save_round(db, row[0], LoopRound.model_validate(record))
        db.execute("DROP TABLE legacy_job_rounds")
        # Existing replay IDs remain valid; remove private messages from old snapshots too.
        for row in db.execute("SELECT id,data FROM work_events WHERE type='job.updated'").fetchall():
            data = json.loads(row[1])
            if "rounds" in data:
                data["rounds"] = [LoopRound.model_validate(item).model_dump(mode="json") for item in data["rounds"]]
                db.execute("UPDATE work_events SET data=? WHERE id=?", (json.dumps(data), row[0]))
    db.execute("UPDATE jobs SET data=json_remove(data, '$.loop') WHERE json_type(data, '$.loop') IS NOT NULL")
    db.execute("UPDATE events SET data=json_remove(data, '$.loop') WHERE json_type(data, '$.loop') IS NOT NULL")
    db.execute("UPDATE work_events SET data=json_remove(data, '$.loop') WHERE type='job.updated' AND json_type(data, '$.loop') IS NOT NULL")


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
