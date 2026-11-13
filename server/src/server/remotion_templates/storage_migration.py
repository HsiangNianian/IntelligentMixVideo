"""Offline storage maintenance: backfill accepted shared sources and retire unused legacy tables.

Run with the backend stopped. No objects are uploaded/deleted; legacy image and conversation rows must already
have been delivered to clients or expired through the normal retention sweep before tables can be dropped.
"""

import argparse
import json

from sqlalchemy import MetaData, Table, func, inspect, select

from ..database import get_engine
from .models import TemplateVersion
from .source_library import archive
from .store import LEASE_TTL, naive, now
from .tables import effect_sources, instance, jobs, versions

RETIRED = ("remotion_presets", "remotion_preset_uses", "remotion_conversations", "remotion_assets", "remotion_work_assets")


def require_stopped(engine) -> None:
    """Refuse maintenance while a backend owns the queue or any execution is still active."""
    names = set(inspect(engine).get_table_names())
    with engine.connect() as db:
        if instance.name in names and db.execute(select(instance.c.name).where(instance.c.heartbeat_at >= naive(now()) - LEASE_TTL)).first():
            raise ValueError("Stop the Remotion backend before storage maintenance.")
        if jobs.name in names and db.execute(select(jobs.c.id).where(jobs.c.active_work_id.is_not(None))).first():
            raise ValueError("Active jobs must finish or be recovered before storage maintenance.")


def backfill(engine) -> int:
    """Idempotently archive accepted Agent versions in bounded pages, preserving all original records."""
    require_stopped(engine)
    effect_sources.create(engine, checkfirst=True)
    if versions.name not in inspect(engine).get_table_names():
        return 0
    cursor, count = "", 0
    while True:
        with engine.begin() as db:
            rows = db.execute(select(versions.c.id, versions.c.data).where(versions.c.id > cursor).order_by(versions.c.id).limit(50)).all()
            for row in rows:
                version = TemplateVersion.model_validate_json(row.data)
                if version.source == "agent":
                    archive(db, version)
                    count += 1
                cursor = row.id
        if len(rows) < 50:
            return count


def retire(engine) -> list[str]:
    """Drop only the fixed retired tables after all private legacy rows are gone; preserve published assets."""
    require_stopped(engine)
    names = set(inspect(engine).get_table_names())
    tables = {name: Table(name, MetaData(), autoload_with=engine) for name in RETIRED if name in names}
    with engine.connect() as db:
        pending = {name: db.execute(select(func.count()).select_from(table)).scalar_one()
                   for name, table in tables.items() if name not in {"remotion_presets", "remotion_preset_uses"}}
    if any(pending.values()):
        raise ValueError(f"Legacy client data still awaits migration/expiry: {pending}")
    backfill(engine)
    for name, table in tables.items():
        table.drop(engine, checkfirst=True)
    return list(tables)


def main() -> None:
    """Explicit maintenance actions; use --backfill before rollout and --retire after old clients have migrated."""
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--backfill", action="store_true")
    action.add_argument("--retire", action="store_true")
    args = parser.parse_args()
    engine = get_engine()
    try:
        print(json.dumps({"scanned_agent_versions": backfill(engine)} if args.backfill else {"dropped_tables": retire(engine)}))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
