"""Preset 目录存储：MySQL 为主、任务本地 JSON 为兜底，读写语义在两条路径上保持一致。

MySQL 复用 `server.database` 的 `DatabaseSettings` 与连接池，表在首次写入或读取时创建；
数据库不可用时回退到模块数据目录下的 `pr76_catalog/`，保证本地与离线开发仍可保存 Preset。
两条路径都只追加不可变记录，不提供覆盖更新或删除。写入返回实际使用的后端，
读取始终合并本地兜底文件，避免一次本地兜底写入在数据库恢复后变得不可见。
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any

from sqlalchemy import JSON, Column, MetaData, String, Table, select
from sqlalchemy.exc import SQLAlchemyError

from .contracts import PresetRecord, SpriteRecord

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
    """保存和读取不可变 Preset 记录；MySQL 优先，失败时明确回退而不是静默丢弃。"""

    def __init__(self, root: Path) -> None:
        """只保存本地兜底目录，不在此处连接数据库或创建目录内容。"""
        self.root = root

    def backend(self) -> str:
        """报告当前有效的存储后端，供宿主快照与诊断使用。"""
        try:
            self._engine()
        except SQLAlchemyError:
            return "local"
        return "mysql"

    def _engine(self):
        """解析并缓存 MySQL 连接池；建表失败时向上抛出，由调用方决定回退。"""
        global _ready_engine
        from ...database import get_engine

        engine = get_engine()
        with _schema_lock:
            if _ready_engine is not engine:
                metadata.create_all(engine)
                _ready_engine = engine
        return engine

    def read_presets(self) -> list[PresetRecord]:
        """读取全部记录。

        数据库可用时以数据库为主，但必须合并本地兜底文件：一次写入在 INSERT
        失败时会落到本地，若只读数据库，这条已确认保存的记录会凭空消失。
        """
        try:
            engine = self._engine()
        except SQLAlchemyError:
            return self._read_records("presets", PresetRecord)
        with engine.connect() as connection:
            rows = connection.execute(select(presets.c.payload).order_by(presets.c.preset_id))
            remote = [PresetRecord.model_validate(row.payload) for row in rows]
        seen = {item.preset_id for item in remote}
        # 本地兜底记录排在末尾；同 ID 已由数据库提供时以数据库为准。
        return remote + [item for item in self._read_records("presets", PresetRecord) if item.preset_id not in seen]

    def append_preset(self, record: PresetRecord) -> str:
        """追加一条记录并返回实际使用的后端；数据库写入失败时回退到本地目录。"""
        try:
            engine = self._engine()
        except SQLAlchemyError:
            self._append_local(record)
            return "local"
        try:
            with engine.begin() as connection:
                connection.execute(presets.insert().values(
                    preset_id=record.preset_id,
                    description=record.description,
                    payload=record.model_dump(mode="json", exclude_unset=True),
                ))
        except SQLAlchemyError:
            self._append_local(record)
            return "local"
        return "mysql"

    def _append_local(self, record: PresetRecord) -> None:
        """把记录写入本地兜底目录，重复 ID 不产生第二份副本。"""
        records = self._read_records("presets", PresetRecord)
        if any(item.preset_id == record.preset_id for item in records):
            return
        records.append(record)
        self._write_records("presets", records)

    def read_sprites(self) -> list[SpriteRecord]:
        """读取任务所属的 Sprite 记录；Sprite 只保存在任务本地目录。"""
        return self._read_records("sprites", SpriteRecord)

    def append_sprite(self, record: SpriteRecord) -> str:
        """追加一条 Sprite 记录并返回实际使用的后端；写入失败时向上抛出。"""
        records = self.read_sprites()
        if any(item.sprite_id == record.sprite_id for item in records):
            return "local"
        records.append(record)
        # 这里不吞异常：写不进去时必须让调用方看到失败，而不是返回成功回执。
        self._write_records("sprites", records)
        return "local"

    def _records_path(self, category: str) -> Path:
        """返回某一类不可变记录的本地文件位置。"""
        return self.root / f"{category}.json"

    def _read_records(self, category: str, model):
        """读取并校验本地记录文件；损坏记录阻止继续操作。"""
        path = self._records_path(category)
        if not path.exists():
            return []
        values = json.loads(path.read_text(encoding="utf-8"))
        return [model.model_validate(item) for item in values]

    def _write_records(self, category: str, records) -> None:
        """原子替换本地记录文件，不暴露半完成状态。"""
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._records_path(category)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps([item.model_dump(mode="json", exclude_unset=True) for item in records], ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(path)

    def find_preset(self, preset_id: str) -> PresetRecord | None:
        """按 ID 精确查找；数据库不可用时同样回退到本地目录。"""
        for record in self.read_presets():
            if record.preset_id == preset_id:
                return record
        return None


def catalog_record(record: PresetRecord) -> dict[str, Any]:
    """导出用于任务快照的公开字段，不携带存储后端细节。"""
    return record.model_dump(mode="json", exclude_unset=True)
