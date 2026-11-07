"""Preset 目录存储：所有设备共享的不可变 Preset 记录只保存在 MySQL，另记每条 Preset 用在了哪些成功版本里。

复用 `server.database` 的连接池，表在首次读写时创建；Preset 只追加记录，不提供覆盖更新或删除。
数据库不可用时直接报错，由工具层返回 `PRESET_STORE_FAILED`，不再写入后端本地目录。使用记录供检索排序：
生成过成功版本、被存进资产库的 Preset 是验证过的配方，排在前面。
"""

from __future__ import annotations

from threading import Lock

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, Column, MetaData, String, Table, case, func, select

from .contracts import PresetRecord, SpriteDraft

# Preset 元数据与完整记录分离：preset_id 用于主键查询，payload 保留不可变快照。
metadata = MetaData()
presets = Table(
    "remotion_presets", metadata,
    Column("preset_id", String(64), primary_key=True),
    Column("description", String(2000, collation="utf8mb4_bin"), nullable=False),
    Column("payload", JSON, nullable=False),
    mysql_charset="utf8mb4",
)
# 一条使用记录：某个成功版本用到了这条 Preset；published 表示该版本被存进了资产库。
preset_uses = Table(
    "remotion_preset_uses", metadata,
    Column("preset_id", String(64), primary_key=True),
    Column("version_id", String(64), primary_key=True),
    Column("published", Boolean, nullable=False),
    mysql_charset="utf8mb4",
)
_schema_lock = Lock()


# 判断两份 Preset 画法是否相同时比较的字段：源码、参数契约、默认参数、角色与特效用法（描述只是说明）。
_DRAWING = {"code", "parameter_schema", "default_parameters", "role", "usage"}
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

    def solidify(self, sprite: SpriteDraft | None) -> list[str]:
        """The stored Presets an accepted Sprite draws with, storing those it changed: an instance drawn exactly as its
        source Preset (`source_preset_id`) is that Preset; a modified copy (a fix the task made with `preset.modify`) is
        kept as a Preset of its own, or found again when an identical one is stored, so a working fix becomes reusable
        and the broken original is not credited for it. A free-form effect (`sprite.effect`) has no source: its single
        instance, the whole Effect.tsx, is kept as a role-less Preset the same way, so `library.search` finds it in later
        works. Other instances without a source (older snapshots) are skipped."""
        drawing = lambda preset: json.dumps(preset.model_dump(mode="json", include=_DRAWING, exclude_unset=True), sort_keys=True)
        records = self.read_presets()
        stored = {drawing(record): record.preset_id for record in records}
        by_id = {record.preset_id: record for record in records}
        used: list[str] = []
        free = sprite is not None and sprite.effect is not None
        for item in sprite.instances if sprite else []:
            source = item.preset.source_preset_id
            if not source and not free:
                continue
            original = by_id.get(source)
            key = drawing(item.preset)
            if original is not None and drawing(original) == key:
                used.append(source)
            elif key in stored:
                used.append(stored[key])
            else:
                record = PresetRecord(preset_id=uuid4().hex, created_at=datetime.now(UTC).isoformat(),
                                      **item.preset.model_dump(mode="json", exclude_unset=True))
                self.append_preset(record)
                stored[key] = record.preset_id
                used.append(record.preset_id)
        return list(dict.fromkeys(used))

    def record_uses(self, version_id: str, preset_ids: Iterable[str]) -> None:
        """Record that the accepted version ``version_id`` uses ``preset_ids``; repeating a record changes nothing."""
        preset_ids = list(dict.fromkeys(preset_ids))
        if not preset_ids:
            return
        with self._engine().begin() as connection:
            known = set(connection.execute(select(preset_uses.c.preset_id).where(preset_uses.c.version_id == version_id)).scalars())
            fresh = [item for item in preset_ids if item not in known]
            if fresh:
                connection.execute(preset_uses.insert(), [{"preset_id": item, "version_id": version_id, "published": False} for item in fresh])

    def mark_published(self, version_id: str) -> None:
        """Mark the Presets the accepted version ``version_id`` was recorded with as saved to the asset library."""
        with self._engine().begin() as connection:
            connection.execute(preset_uses.update().where(preset_uses.c.version_id == version_id).values(published=True))

    def use_counts(self) -> dict[str, tuple[int, int]]:
        """For each used Preset, the number of accepted versions using it and how many of them were published."""
        with self._engine().connect() as connection:
            rows = connection.execute(select(
                preset_uses.c.preset_id, func.count(), func.sum(case((preset_uses.c.published, 1), else_=0)),
            ).group_by(preset_uses.c.preset_id))
            return {preset_id: (int(accepted), int(published or 0)) for preset_id, accepted, published in rows}

    def find_preset(self, preset_id: str) -> PresetRecord | None:
        """按 ID 精确查找。"""
        with self._engine().connect() as connection:
            payload = connection.execute(select(presets.c.payload).where(presets.c.preset_id == preset_id)).scalar()
        return PresetRecord.model_validate(payload) if payload is not None else None
