"""MySQL registry of the fonts Remotion effects can use: the preinstalled catalog and fonts users uploaded.

A row identifies a font by a unique family name (the name CSS and the render sandbox's fontconfig resolve) and holds
the file's metadata as JSON; the bytes themselves are objects in ZOS (`fonts.py`). Rows are never deleted and their ID
and family never change, so a template or Sprite that names a family keeps rendering; only how a font is shown (its
label, preview strip and style) may be corrected (`update_data`). Like the Sprite table, the table is created on first use
and never altered. The functions are synchronous; callers run them in a worker thread.
"""

from datetime import UTC, datetime
from threading import Lock

from sqlalchemy import JSON, Column, MetaData, String, Table, select
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.exc import IntegrityError

from ..database import get_engine

metadata = MetaData()
fonts = Table(
    "remotion_fonts", metadata,
    Column("font_id", String(40), primary_key=True),
    # Family names are compared exactly; the application also refuses names that differ only by case.
    Column("family", String(255, collation="utf8mb4_bin"), nullable=False, unique=True),
    Column("device_id", String(36), nullable=True),
    Column("created_at", DATETIME(fsp=6), nullable=False, index=True),
    Column("data", JSON, nullable=False),
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


def listing() -> list[dict]:
    """Every font record in registration order: the font's ID, family, uploader and metadata JSON merged."""
    with initialize_schema().connect() as connection:
        rows = connection.execute(select(fonts).order_by(fonts.c.created_at, fonts.c.font_id)).all()
    return [{**row.data, "id": row.font_id, "family": row.family, "device_id": row.device_id} for row in rows]


def update_data(font_id: str, data: dict) -> None:
    """Replace a font's metadata JSON; callers only change how the font is shown, never its file fields."""
    with initialize_schema().begin() as connection:
        connection.execute(fonts.update().where(fonts.c.font_id == font_id).values(data=data))


def insert(font_id: str, family: str, data: dict, device: str | None) -> bool:
    """Register a font; False when its ID or family name is already taken (nothing is written)."""
    try:
        with initialize_schema().begin() as connection:
            connection.execute(fonts.insert().values(
                font_id=font_id, family=family, device_id=device, created_at=datetime.now(UTC).replace(tzinfo=None), data=data,
            ))
    except IntegrityError:
        return False
    return True
