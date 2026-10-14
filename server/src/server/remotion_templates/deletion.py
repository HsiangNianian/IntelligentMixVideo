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

# Works that use an asset: as an original request image (the single ``image`` of works stored before
# multi-image chat, or ``images``) or as an image attached to a later message.
REFERENCES = """SELECT id FROM projects WHERE json_extract(data, '$.request.image.asset_id')=:asset
    UNION SELECT p.id FROM projects p, json_each(p.data, '$.request.images') i
    WHERE json_extract(i.value, '$.asset_id')=:asset
    UNION SELECT j.project_id FROM jobs j, json_each(j.input_data, '$.images') i
    WHERE json_extract(i.value, '$.asset_id')=:asset"""


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
        request = json.loads(project[0])["request"] if project else {}
        originals = request.get("images", []) + ([request["image"]] if request.get("image") else [])
        images = {str(UUID(image["asset_id"])) for image in originals}
        images.update(
            str(UUID(row[0]))
            for row in db.execute(
                "SELECT json_extract(i.value, '$.asset_id') FROM jobs j, json_each(j.input_data, '$.images') i "
                "WHERE j.project_id=?",
                (identifier,),
            )
        )
        for row in jobs:
            remove_path(store.root, "jobs", row[0])
        for row in versions:
            remove_path(store.root, "accepted", row[0])
        for asset_id in sorted(images):
            shared = db.execute(
                f"SELECT 1 FROM ({REFERENCES}) WHERE id<>:work LIMIT 1",
                {"asset": asset_id, "work": identifier},
            ).fetchone()
            if not shared:
                remove_path(store.root, "assets", asset_id, ".png")
                db.execute("DELETE FROM assets WHERE id=?", (asset_id,))
        # Keep the foreign-key deletion order explicit and bind every work identifier.
        db.execute(
            "DELETE FROM events WHERE job_id IN (SELECT id FROM jobs WHERE project_id=?)",
            (identifier,),
        )
        db.execute(
            "DELETE FROM job_progress WHERE job_id IN (SELECT id FROM jobs WHERE project_id=?)",
            (identifier,),
        )
        db.execute("DELETE FROM chat_messages WHERE work_id=?", (identifier,))
        db.execute(
            "DELETE FROM job_rounds WHERE job_id IN (SELECT id FROM jobs WHERE project_id=?)",
            (identifier,),
        )
        db.execute("DELETE FROM work_events WHERE work_id=?", (identifier,))
        db.execute("DELETE FROM conversations WHERE project_id=?", (identifier,))
        db.execute("DELETE FROM versions WHERE project_id=?", (identifier,))
        db.execute("DELETE FROM jobs WHERE project_id=?", (identifier,))
        db.execute("DELETE FROM projects WHERE id=?", (identifier,))
        db.execute("DELETE FROM work_deletions WHERE work_id=?", (identifier,))
