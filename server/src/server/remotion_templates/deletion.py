"""Delete a work's files and relational history after its runtime has stopped all writers.

The durable marker survives partial filesystem failures. Metadata stays until cleanup
finishes, so retry/startup recovery can derive the same server-owned paths again.
"""

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from .store import Store


def remove_path(root: Path, category: str, identifier: str, suffix: str = "") -> None:
    """Remove only UUID-owned paths; unlink leaf symlinks and reject redirected parents."""
    parent = root / category
    if parent.is_symlink() or not parent.resolve().is_relative_to(root.resolve()):
        raise OSError("Data directory points outside its managed location")
    path = parent / f"{UUID(identifier)}{suffix}"
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def finish(store: "Store", work_id: UUID) -> None:
    """Clean files then commit metadata removal; serialize shared-asset checks with creation.

    Call only after worker teardown, or during startup before workers exist. Filesystem
    failure rolls back SQL but preserves the previously committed deletion marker.
    """
    identifier = str(work_id)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute(
            "SELECT 1 FROM work_deletions WHERE work_id=?", (identifier,)
        ).fetchone():
            return
        jobs = db.execute(
            "SELECT id FROM jobs WHERE project_id=?", (identifier,)
        ).fetchall()
        versions = db.execute(
            "SELECT id FROM versions WHERE project_id=?", (identifier,)
        ).fetchall()
        project = db.execute(
            "SELECT data FROM projects WHERE id=?", (identifier,)
        ).fetchone()
        image = json.loads(project[0])["request"].get("image") if project else None
        for row in jobs:
            remove_path(store.root, "jobs", row[0])
        for row in versions:
            remove_path(store.root, "accepted", row[0])
        if image:
            asset_id = str(UUID(image["asset_id"]))
            shared = db.execute(
                "SELECT 1 FROM projects WHERE id<>? AND json_extract(data, '$.request.image.asset_id')=? LIMIT 1",
                (identifier, asset_id),
            ).fetchone()
            if not shared:
                remove_path(store.root, "assets", asset_id, ".png")
                db.execute("DELETE FROM assets WHERE id=?", (asset_id,))
        for table in ("events", "job_progress"):
            db.execute(
                f"DELETE FROM {table} WHERE job_id IN (SELECT id FROM jobs WHERE project_id=?)",
                (identifier,),
            )
        for table in ("chat_messages", "work_events"):
            db.execute(f"DELETE FROM {table} WHERE work_id=?", (identifier,))
        for table in ("conversations", "versions", "jobs"):
            db.execute(f"DELETE FROM {table} WHERE project_id=?", (identifier,))
        db.execute("DELETE FROM projects WHERE id=?", (identifier,))
        db.execute("DELETE FROM work_deletions WHERE work_id=?", (identifier,))
