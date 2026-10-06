"""在共享 SQLAlchemy Engine 上保存合成任务；短事务与版本条件更新阻止重复推进。"""

from datetime import UTC, datetime
import logging
from typing import Literal
from uuid import uuid4

from sqlalchemy import JSON, Column, Integer, MetaData, String, Table, select
from sqlalchemy.dialects.mysql import DATETIME

from ..database import get_engine
from . import execution_log
from .execution_log import BEIJING

# 数据库只保存业务快照供任务恢复；执行日志独立写入本地文件。
metadata = MetaData()
tasks = Table(
    "video_compositions", metadata,
    Column("task_id", String(36), primary_key=True),
    Column("status", String(16), nullable=False, index=True),
    Column("stage", String(16), nullable=False),
    Column("version", Integer, nullable=False),
    Column("data", JSON, nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    Column("updated_at", DATETIME(fsp=6), nullable=False),
    mysql_charset="utf8mb4",
)


def finished_tasks() -> set[str]:
    """只允许清理合成与通知均已结束的任务；终态不可重新推进。"""
    notification = tasks.c.data["notification_status"].as_string()
    with get_engine().connect() as connection:
        return set(connection.execute(select(tasks.c.task_id).where(
            tasks.c.status.in_(("succeeded", "failed")),
            notification.is_(None) | notification.not_in(("pending", "sending")),
        )).scalars())


def add_log(record: dict, event: str, details: dict) -> None:
    """写文件失败只报告异常，不回滚已提交的业务任务或打断后续步骤。"""
    try:
        execution_log.write_log(record, event, details, finished_tasks)
    except Exception:
        logging.getLogger(__name__).exception("任务 %s 执行日志写入或清理失败", record["task_id"])


def initialize_schema() -> None:
    """仅创建业务任务表，不读写旧日志表，也不在启动时清理文件。"""
    tasks.create(get_engine(), checkfirst=True)


def _record(row) -> dict:
    """将数据库无时区北京时间恢复为 API 可序列化的带时区时间。"""
    result = dict(row._mapping)
    for name in ("created_at", "updated_at"):
        result[name] = result[name].replace(tzinfo=BEIJING)
    return result


def create(request: dict, output: dict, callback_base_url: str | None = None, raw_request: dict | None = None, *, task_id: str | None = None) -> dict:
    """事务保存请求的基础地址供后台生成回调 URL；旧任务可缺省，重复 POST 使用新 ID。"""
    now = datetime.now(BEIJING)
    record = dict(
        task_id=task_id or str(uuid4()), status="queued", stage="queued", version=0,
        data={"request": request, "raw_request": raw_request if raw_request is not None else request,
              "output": output, "callback_base_url": callback_base_url, "storage_timezone": "Asia/Shanghai"}, created_at=now, updated_at=now,
    )
    with get_engine().begin() as connection:
        connection.execute(tasks.insert().values(
            **{**record, "created_at": now.replace(tzinfo=None), "updated_at": now.replace(tzinfo=None)},
        ))
    add_log(record, "submitted", {"input": record["data"]["raw_request"], "output": {"data": record["task_id"]},
                                  "output_settings": output})
    return record


def get(task_id: str) -> dict | None:
    """只读本地任务，不查询或推进任何上游任务。"""
    with get_engine().connect() as connection:
        row = connection.execute(select(tasks).where(tasks.c.task_id == task_id)).first()
        return _record(row) if row is not None else None


def pending(exclude: list[str], limit: int) -> list[dict]:
    """读取未完成合成及已落库的待通知终态，共享单进程调度名额。"""
    with get_engine().connect() as connection:
        rows = connection.execute(select(tasks).where(
            (tasks.c.status.in_(("queued", "processing")) | (
                tasks.c.status.in_(("succeeded", "failed")) &
                (tasks.c.data["notification_status"].as_string() == "pending")
                & (tasks.c.data["notification_next_at"].as_string().is_(None)
                   | (tasks.c.data["notification_next_at"].as_string() <= datetime.now(UTC).isoformat()))
            )), tasks.c.task_id.not_in(exclude),
        ).order_by(tasks.c.created_at, tasks.c.task_id).limit(limit))
        return [_record(row) for row in rows]


def advance(record: dict, stage: str, *, status: str = "processing", **data) -> dict | None:
    """检查版本和非终态后原子写入；失去推进权返回 None，调用方必须停止副作用。"""
    if status in ("succeeded", "failed") and record["data"]["request"].get("callbackUrl"):
        data["notification_status"] = "pending"
    now = datetime.now(BEIJING)
    values = dict(stage=stage, status=status, version=record["version"] + 1,
                  data={**record["data"], **data}, updated_at=now.replace(tzinfo=None))
    with get_engine().begin() as connection:
        changed = connection.execute(tasks.update().where(
            tasks.c.task_id == record["task_id"], tasks.c.version == record["version"],
            tasks.c.status.in_(("queued", "processing")),
        ).values(**values))
        if changed.rowcount != 1:
            return None
    add_log({**record, **values}, "task_finished" if status in ("succeeded", "failed") else "stage_updated", data)
    return {**record, **values, "updated_at": now}


def notification_status(record: dict, status: Literal["pending", "sending", "sent", "failed"], details: dict | None = None) -> dict | None:
    """条件认领并保存次数与重试时间；不改合成终态，过期快照不能重复投递。"""
    expected = "pending" if status == "sending" else "sending"
    data = {**record["data"], "notification_status": status}
    if status == "sending":
        if data.get("notification_attempts", 0) >= 4 or data.get("notification_next_at", "") > datetime.now(UTC).isoformat():
            return None
        data["notification_attempts"] = data.get("notification_attempts", 0) + 1
    elif status == "pending":
        data["notification_next_at"] = details["next_at"]
    values = dict(version=record["version"] + 1, data=data)
    with get_engine().begin() as connection:
        changed = connection.execute(tasks.update().where(
            tasks.c.task_id == record["task_id"], tasks.c.version == record["version"],
            tasks.c.status.in_(("succeeded", "failed")),
            tasks.c.data["notification_status"].as_string() == expected,
        ).values(**values))
        if changed.rowcount != 1:
            return None
    add_log({**record, **values}, f"notification_{status}", details or {})
    return {**record, **values}
