"""Public host-owned phase and loop summaries, persisted separately from private model/tool diagnostics."""

import json
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator
from sqlalchemy import Connection, insert, select, update

from .models import Contract, GenerationJob
from .tables import job_progress, job_rounds

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
    "tools.plan_execute", "tools.report_step", "tools.inspect", "preset.create", "preset.modify",
    "preset.search", "sprite.compose", "sprite.create", "validate.code",
    "validate.render", "image.info", "image.resize", "image.crop",
}
CALL_TEXT_LIMIT = 2000
ARGUMENTS_LIMIT = 4000
ROUND_TEXT_LIMIT = 4000
PUBLIC_ERRORS = {
    "INVALID_ARGUMENT": "工具参数无效。",
    "TOOL_NOT_FOUND": "工具不可用。",
    "CODE_VALIDATION_FAILED": "代码校验未通过。",
    "COMPOSITION_FAILED": "组合生成失败。",
    "PRESET_STORE_FAILED": "预设保存失败。",
    "SPRITE_STORE_FAILED": "字效保存失败。",
    "PRESET_NOT_FOUND": "预设不存在。",
    "VALIDATION_UNAVAILABLE": "校验环境暂不可用，请检查服务端校验环境。",
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


def clip(value, limit: int) -> str | None:
    """Bound one public diagnostic text; empty values stay absent and a cut is marked."""
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


class RoundCall(Contract):
    """One tool call the host actually ran, with the model's arguments for auditing it.

    ``arguments`` is the raw JSON text the model sent and ``detail`` the tool's real
    error text, both clipped; complete payloads stay in the private audit. The
    outcome vocabulary (tool names, error codes and the fixed ``message``) is still
    host-authored.
    """

    tool: str = Field(min_length=1, max_length=64)
    requested_tool: str | None = Field(default=None, max_length=64)
    status: Literal["pass", "fail"]
    error_code: str | None = Field(default=None, max_length=64)
    message: str | None = Field(default=None, max_length=200)
    detail: str | None = Field(default=None, max_length=CALL_TEXT_LIMIT)
    arguments: str | None = Field(default=None, max_length=ARGUMENTS_LIMIT)

    @model_validator(mode="before")
    @classmethod
    def public_summary(cls, value):
        """Sanitize both new receipts and legacy stored summaries at the public boundary."""
        if not isinstance(value, dict):
            return value
        value = dict(value)
        tool = value.get("tool")
        value["tool"] = tool if isinstance(tool, str) and tool in PUBLIC_TOOLS else "unknown"
        for key, limit in (("detail", CALL_TEXT_LIMIT), ("arguments", ARGUMENTS_LIMIT), ("requested_tool", 64)):
            if key in value:
                value[key] = clip(value[key], limit)
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
    # Bounded text for a turn without tool calls: what the model replied, why its request failed.
    message: str | None = Field(default=None, max_length=ROUND_TEXT_LIMIT)
    error_detail: str | None = Field(default=None, max_length=CALL_TEXT_LIMIT)

    @model_validator(mode="before")
    @classmethod
    def bounded_text(cls, value):
        """Clip free text instead of rejecting a long model reply or host steer."""
        if not isinstance(value, dict):
            return value
        value = dict(value)
        for key, limit in (("message", ROUND_TEXT_LIMIT), ("error_detail", CALL_TEXT_LIMIT)):
            if key in value:
                value[key] = clip(value[key], limit)
        return value


def read_steps(db: Connection, job: GenerationJob) -> list[ProgressStep]:
    """Read only recorded summaries; jobs without records have no fabricated intermediate history."""
    data = db.execute(select(job_progress.c.data).where(job_progress.c.job_id == str(job.id))).scalar()
    return [ProgressStep.model_validate(item) for item in json.loads(data)] if data else []


def save_steps(db: Connection, job: GenerationJob, steps: list[ProgressStep]) -> None:
    """Write inside the job/event transaction, so stream cursors cannot outrun progress."""
    data = json.dumps([step.model_dump(mode="json") for step in steps])
    if db.execute(update(job_progress).where(job_progress.c.job_id == str(job.id)).values(data=data)).rowcount == 0:
        db.execute(insert(job_progress).values(job_id=str(job.id), data=data))


def read_rounds(db: Connection, job: GenerationJob) -> list[LoopRound]:
    """Read only recorded rounds; jobs without records never get fabricated history."""
    rows = db.execute(
        select(job_rounds.c.data).where(job_rounds.c.job_id == str(job.id))
        .order_by(job_rounds.c.turn, job_rounds.c.layer)
    )
    return [LoopRound.model_validate_json(row.data) for row in rows]


def save_round(db: Connection, job_id: str, record: LoopRound) -> bool:
    """Insert one immutable round; a repeated layer/turn is ignored so retried reporting is idempotent."""
    key = (job_rounds.c.job_id == job_id, job_rounds.c.layer == record.layer, job_rounds.c.turn == record.turn)
    if db.execute(select(job_rounds.c.turn).where(*key)).first() is not None:
        return False
    db.execute(insert(job_rounds).values(job_id=job_id, layer=record.layer, turn=record.turn, data=record.model_dump_json()))
    return True


def start_step(
    db: Connection, job: GenerationJob, phase: Phase, stamp: datetime
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


def finish_steps(db: Connection, job: GenerationJob) -> None:
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
