"""Preset 目录存储：所有设备共享的不可变 Preset 记录只保存在 MySQL。

复用 `server.database` 的连接池，表在首次读写时创建；只追加记录，不提供覆盖更新或删除。
数据库不可用时直接报错，由工具层返回 `PRESET_STORE_FAILED`，不再写入后端本地目录。
"""

from __future__ import annotations

from threading import Lock
from typing import Any

from sqlalchemy import JSON, Column, MetaData, String, Table, select

from .contracts import PresetRecord

# Preset 元数据与完整记录分离：preset_id 用于主键查询，payload 保留不可变快照。
metadata = MetaData()
presets = Table(
    "remotion_presets", metadata,
    Column("preset_id", String(64), primary_key=True),
    Column("description", String(2000, collation="utf8mb4_bin"), nullable=False),
    Column("payload", JSON, nullable=False),
    mysql_charset="utf8mb4",
)
_schema_lock = Lock()
_ready_engine = None


class CatalogStore:
    """保存和读取不可变 Preset 记录；不持有连接，每次调用使用共享连接池。"""

    def _engine(self):
        """解析并缓存 MySQL 连接池，首次使用时建表；失败向上抛出。"""
        global _ready_engine
        from ...database import get_engine

        engine = get_engine()
        with _schema_lock:
            if _ready_engine is not engine:
                metadata.create_all(engine)
                _ready_engine = engine
        return engine

    def read_presets(self) -> list[PresetRecord]:
        """按 ID 顺序读取全部记录。"""
        with self._engine().connect() as connection:
            rows = connection.execute(select(presets.c.payload).order_by(presets.c.preset_id))
            return [PresetRecord.model_validate(row.payload) for row in rows]

    def append_preset(self, record: PresetRecord) -> str:
        """追加一条记录并返回存储后端名称；写入失败时抛出，调用方不得当作已保存。"""
        with self._engine().begin() as connection:
            connection.execute(presets.insert().values(
                preset_id=record.preset_id,
                description=record.description,
                payload=record.model_dump(mode="json", exclude_unset=True),
            ))
        return "mysql"

    def find_preset(self, preset_id: str) -> PresetRecord | None:
        """按 ID 精确查找。"""
        with self._engine().connect() as connection:
            payload = connection.execute(select(presets.c.payload).where(presets.c.preset_id == preset_id)).scalar()
        return PresetRecord.model_validate(payload) if payload is not None else None


def catalog_record(record: PresetRecord) -> dict[str, Any]:
    """导出用于任务快照的公开字段，不携带存储后端细节。"""
    return record.model_dump(mode="json", exclude_unset=True)
