"""MySQL storage of published Remotion Sprites: one immutable row per publication.

A row holds the `imv.sprite.v1.PublishedSprite` JSON and the columns that identify a publication: its source
version and field choice, which are unique together. A row is only deleted as a whole, when nothing uses it. The preview bundle stays a file in the Remotion data
folder (`sprites.py`). Like the template and Preset tables, the table is created on first use and never altered.
Templates reference Sprites by ID, and keeping both in one database lets a template be checked against the
published Sprites when it is saved. The functions are synchronous; callers run them in a worker thread.
"""

from collections.abc import Iterable
from datetime import UTC, datetime
from threading import Lock

from sqlalchemy import JSON, Column, MetaData, SmallInteger, String, Table, UniqueConstraint, delete, select, update
from sqlalchemy.dialects.mysql import DATETIME

from ..database import get_engine

# Field names are JSON property names, which are case-sensitive, so they are compared as binary strings.
# 255 characters keep the unique key (up to 4 bytes per character) far inside InnoDB's 3072-byte limit.
NAME_LENGTH = 255
metadata = MetaData()
sprites = Table(
    "remotion_sprites", metadata,
    Column("sprite_id", String(36), primary_key=True),
    Column("source_version_id", String(36), nullable=False),
    Column("kind", SmallInteger, nullable=False),
    Column("text_prop", String(NAME_LENGTH, collation="utf8mb4_bin"), nullable=False),
    Column("keywords_prop", String(NAME_LENGTH, collation="utf8mb4_bin"), nullable=False),
    Column("published_at", DATETIME(fsp=6), nullable=False, index=True),
    Column("data", JSON, nullable=False),
    UniqueConstraint("source_version_id", "kind", "text_prop", "keywords_prop", name="uq_remotion_sprites_selection"),
    mysql_charset="utf8mb4",
)
_schema_lock = Lock()
_ready_engine = None


def initialize_schema():
    """Create the table on first use and return the shared engine; a failure is retried by the next call."""
    global _ready_engine
    engine = get_engine()
    with _schema_lock:
        if _ready_engine is not engine:
            metadata.create_all(engine)
            _ready_engine = engine
    return engine


def get(sprite_id: str) -> dict | None:
    """Return the stored publication JSON of one Sprite, or None when no row has that ID."""
    with initialize_schema().connect() as connection:
        return connection.execute(select(sprites.c.data).where(sprites.c.sprite_id == sprite_id)).scalar()


def listing() -> list[tuple[str, dict]]:
    """Return every publication as (ID, JSON), newest first.

    The order is applied here, not in SQL: sorting rows that carry whole JSON documents can exceed MySQL's sort
    buffer (error 1038), and the catalog reads every row anyway.
    """
    with initialize_schema().connect() as connection:
        rows = connection.execute(select(sprites.c.sprite_id, sprites.c.published_at, sprites.c.data)).all()
    rows.sort(key=lambda row: row.sprite_id)
    rows.sort(key=lambda row: row.published_at, reverse=True)
    return [(row.sprite_id, row.data) for row in rows]


def find(source_version_id: str, kind: int, text_prop: str, keywords_prop: str) -> dict | None:
    """Return the publication JSON that this exact source and field choice already produced, or None."""
    with initialize_schema().connect() as connection:
        return connection.execute(select(sprites.c.data).where(
            sprites.c.source_version_id == source_version_id, sprites.c.kind == kind,
            sprites.c.text_prop == text_prop, sprites.c.keywords_prop == keywords_prop,
        )).scalar()


def find_any(source_version_id: str, kind: int) -> dict | None:
    """Return the oldest publication of this source and kind whatever its text/keyword fields, or None.

    Only the identifying columns are sorted (sorting whole JSON rows exhausts MySQL's sort buffer).
    """
    with initialize_schema().connect() as connection:
        sprite_id = connection.execute(select(sprites.c.sprite_id).where(
            sprites.c.source_version_id == source_version_id, sprites.c.kind == kind,
        ).order_by(sprites.c.published_at, sprites.c.sprite_id).limit(1)).scalar()
    return get(sprite_id) if sprite_id is not None else None


def insert(
    *, sprite_id: str, source_version_id: str, kind: int, text_prop: str, keywords_prop: str,
    published_at: datetime, data: dict,
) -> None:
    """Add one publication; a second one for the same source and field choice is a duplicate-key error (1062)."""
    with initialize_schema().begin() as connection:
        connection.execute(sprites.insert().values(
            sprite_id=sprite_id, source_version_id=source_version_id, kind=kind,
            text_prop=text_prop, keywords_prop=keywords_prop,
            published_at=published_at.astimezone(UTC).replace(tzinfo=None), data=data,
        ))


def replace_data(sprite_id: str, data: dict) -> None:
    """Replace the stored JSON of one publication (its refreshed preview hash); the identifying columns never change."""
    with initialize_schema().begin() as connection:
        connection.execute(update(sprites).where(sprites.c.sprite_id == sprite_id).values(data=data))


def remove(sprite_id: str) -> bool:
    """Delete one publication; False when no row has that ID."""
    with initialize_schema().begin() as connection:
        return connection.execute(delete(sprites).where(sprites.c.sprite_id == sprite_id)).rowcount > 0


def records(sprite_ids: Iterable[str]) -> dict[str, object]:
    """Return the stored publication JSON of each given Sprite ID that has one; unknown IDs are left out.

    A template save reads them to check the clips' shared canvas and every placement's overrides.
    """
    wanted = set(sprite_ids)
    if not wanted:
        return {}
    with initialize_schema().connect() as connection:
        rows = connection.execute(
            select(sprites.c.sprite_id, sprites.c.data).where(sprites.c.sprite_id.in_(wanted))
        ).all()
    return {row.sprite_id: row.data for row in rows}
