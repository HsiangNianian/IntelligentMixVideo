"""MySQL tables of the Remotion workspace backend; the backend keeps no SQLite file of its own.

Works, jobs, accepted versions, the model's sliding window, public events and overlay render tasks live in the
shared database next to `remotion_sprites`. Every work belongs to one client device. Public chat is only
buffered here until the client acknowledges it into its local SQLite (`store.acknowledge`). Documents are JSON
text in long columns; `seq`/`id` autoincrement columns give the insertion order that SQLite's rowid used to.
The single `remotion_instance` row is a lease that keeps a second backend off the same queue. Tables are created on first use and never altered, like the template and Sprite tables. The SQLite variants only
serve the test fixture that stands in for MySQL.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import DATETIME, LONGTEXT

# MySQL TEXT stops at 64 KiB; model windows and sealed candidates need LONGTEXT.
LONG = Text().with_variant(LONGTEXT(), "mysql")
# SQLite only autoincrements an INTEGER PRIMARY KEY.
SEQUENCE = BigInteger().with_variant(Integer(), "sqlite")
STAMP = DateTime().with_variant(DATETIME(fsp=6), "mysql")
IDENTIFIER = String(36)

metadata = MetaData()

# One row (key "backend") naming the backend instance that owns the queue; see `Store.acquire_lease`.
instance = Table(
    "remotion_instance", metadata,
    Column("name", String(16), primary_key=True),
    Column("instance_id", IDENTIFIER, nullable=False),
    Column("heartbeat_at", STAMP, nullable=False),
    mysql_charset="utf8mb4",
)
devices = Table(
    "remotion_devices", metadata,
    Column("device_id", IDENTIFIER, primary_key=True),
    Column("last_seen_at", STAMP, nullable=False, index=True),
    mysql_charset="utf8mb4",
)
works = Table(
    "remotion_works", metadata,
    Column("seq", SEQUENCE, primary_key=True, autoincrement=True),
    Column("id", IDENTIFIER, nullable=False, unique=True),
    Column("device_id", IDENTIFIER, nullable=False, index=True),
    Column("deleting", Boolean, nullable=False, default=False),
    Column("data", LONG, nullable=False),
    mysql_charset="utf8mb4",
)
# Every reference image a work uses: its original request images and those attached to later messages. Rows
# outlive the delivered jobs, so deletion and orphan cleanup can tell whether another work still needs an image.
work_assets = Table(
    "remotion_work_assets", metadata,
    Column("work_id", IDENTIFIER, primary_key=True),
    Column("asset_id", IDENTIFIER, primary_key=True, index=True),
    mysql_charset="utf8mb4",
)
assets = Table(
    "remotion_assets", metadata,
    Column("id", IDENTIFIER, primary_key=True),
    Column("device_id", IDENTIFIER, nullable=False, index=True),
    Column("data", LONG, nullable=False),
    mysql_charset="utf8mb4",
)
jobs = Table(
    "remotion_jobs", metadata,
    Column("seq", SEQUENCE, primary_key=True, autoincrement=True),
    Column("id", IDENTIFIER, nullable=False, unique=True),
    Column("work_id", IDENTIFIER, nullable=False, index=True),
    Column("status", String(16), nullable=False, index=True),
    # Set to the work ID only while queued or running: the unique key allows one active job per work.
    Column("active_work_id", IDENTIFIER, unique=True),
    Column("data", LONG, nullable=False),
    Column("input_data", LONG, nullable=False),
    mysql_charset="utf8mb4",
)
versions = Table(
    "remotion_versions", metadata,
    Column("id", IDENTIFIER, primary_key=True),
    Column("work_id", IDENTIFIER, nullable=False, index=True),
    Column("number", Integer, nullable=False),
    Column("data", LONG, nullable=False),
    UniqueConstraint("work_id", "number", name="uq_remotion_versions_number"),
    mysql_charset="utf8mb4",
)
conversations = Table(
    "remotion_conversations", metadata,
    Column("work_id", IDENTIFIER, primary_key=True),
    Column("data", LONG, nullable=False),
    mysql_charset="utf8mb4",
)
chat_messages = Table(
    "remotion_chat_messages", metadata,
    Column("sequence", SEQUENCE, primary_key=True, autoincrement=True),
    Column("id", IDENTIFIER, nullable=False, unique=True),
    Column("work_id", IDENTIFIER, nullable=False, index=True),
    Column("job_id", IDENTIFIER, nullable=False),
    Column("role", String(16), nullable=False),
    Column("data", LONG, nullable=False),
    UniqueConstraint("job_id", "role", name="uq_remotion_chat_job_role"),
    mysql_charset="utf8mb4",
)
work_events = Table(
    "remotion_work_events", metadata,
    Column("id", SEQUENCE, primary_key=True, autoincrement=True),
    Column("work_id", IDENTIFIER, nullable=False, index=True),
    Column("type", String(32), nullable=False),
    Column("data", LONG, nullable=False),
    Column("created_at", STAMP, nullable=False, index=True),
    mysql_charset="utf8mb4",
)
job_progress = Table(
    "remotion_job_progress", metadata,
    Column("job_id", IDENTIFIER, primary_key=True),
    Column("data", LONG, nullable=False),
    mysql_charset="utf8mb4",
)
job_rounds = Table(
    "remotion_job_rounds", metadata,
    Column("job_id", IDENTIFIER, primary_key=True),
    Column("layer", String(16), primary_key=True),
    Column("turn", Integer, primary_key=True),
    Column("data", LONG, nullable=False),
    mysql_charset="utf8mb4",
)
overlay_renders = Table(
    "remotion_overlay_renders", metadata,
    Column("id", IDENTIFIER, primary_key=True),
    Column("status", String(16), nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("data", LONG, nullable=False),
    mysql_charset="utf8mb4",
)
