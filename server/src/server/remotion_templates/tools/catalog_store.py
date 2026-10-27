"""Preset 目录存储：所有设备共享的不可变 Preset 记录只保存在 MySQL，另记每条 Preset 用在了哪些成功版本里。

复用 `server.database` 的连接池，表在首次读写时创建；Preset 只追加记录，不提供覆盖更新或删除。
数据库不可用时直接报错，由工具层返回 `PRESET_STORE_FAILED`，不再写入后端本地目录。使用记录供检索排序：
生成过成功版本、被存进资产库的 Preset 是验证过的配方，排在前面。
"""

from __future__ import annotations

from threading import Lock

from collections.abc import Iterable

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


def preset_sources(sprite: SpriteDraft | None) -> list[str]:
    """The stored Presets a composed Sprite was built from (each instance's direct source), without repeats."""
    return list(dict.fromkeys(item.preset.source_preset_id for item in (sprite.instances if sprite else []) if item.preset.source_preset_id))
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

    def record_uses(self, version_id: str, preset_ids: Iterable[str], *, published: bool = False) -> None:
        """Record that the accepted version ``version_id`` uses ``preset_ids``; ``published`` also marks them saved to the
        asset library. Repeating a record changes nothing, except that a later publication marks it published."""
        preset_ids = list(dict.fromkeys(preset_ids))
        if not preset_ids:
            return
        with self._engine().begin() as connection:
            known = set(connection.execute(select(preset_uses.c.preset_id).where(preset_uses.c.version_id == version_id)).scalars())
            fresh = [item for item in preset_ids if item not in known]
            if fresh:
                connection.execute(preset_uses.insert(), [{"preset_id": item, "version_id": version_id, "published": published} for item in fresh])
            if published and known:
                connection.execute(preset_uses.update().where(preset_uses.c.version_id == version_id, preset_uses.c.preset_id.in_(known)).values(published=True))

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
