"""Delete a work's stored files and database records after its runtime has stopped all writers.

The durable `deleting` flag survives partial failures. Records stay until cleanup finishes, so a retried DELETE,
the expiry sweep or startup recovery can derive the same object keys and local paths again.
"""

import shutil
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import delete, select

from .tables import (
    assets,
    chat_messages,
    conversations,
    job_progress,
    job_rounds,
    jobs,
    versions,
    work_assets,
    work_events,
    works,
)

if TYPE_CHECKING:
    from .store import Store


def remove_path(root: Path, category: str, identifier: str, suffix: str = "") -> None:
    """Remove only UUID-owned local paths; unlink leaf symlinks and reject redirected parents."""
    parent = root / category
    if not parent.exists():
        return
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
    """Remove objects and local copies, then the records; reference images no other work uses go too.

    Call only after worker teardown, or during startup before workers exist. A storage failure rolls back the
    record removal but keeps the previously committed deletion flag.
    """
    from .store import asset_key, version_key

    identifier = str(work_id)
    with store.transaction() as db:
        deleting = db.execute(select(works.c.deleting).where(works.c.id == identifier)).scalar()
        if not deleting:
            return
        job_ids = db.execute(select(jobs.c.id).where(jobs.c.work_id == identifier)).scalars().all()
        version_ids = db.execute(select(versions.c.id).where(versions.c.work_id == identifier)).scalars().all()
        for job_id in job_ids:
            remove_path(store.root, "jobs", job_id)
            remove_path(store.root, "audit", job_id, ".jsonl")
        for version_id in version_ids:
            store.objects.delete_prefix(version_key(UUID(version_id)))
            remove_path(store.root / "cache", "accepted", version_id)
        images = db.execute(select(work_assets.c.asset_id).where(work_assets.c.work_id == identifier)).scalars().all()
        for asset_id in sorted(images):
            if not db.execute(select(work_assets.c.work_id).where(
                work_assets.c.asset_id == asset_id, work_assets.c.work_id != identifier,
            ).limit(1)).first():
                store.objects.delete_prefix(asset_key(UUID(asset_id)))
                remove_path(store.root / "cache", "assets", asset_id, ".png")
                db.execute(delete(assets).where(assets.c.id == asset_id))
        db.execute(delete(work_assets).where(work_assets.c.work_id == identifier))
        db.execute(delete(job_progress).where(job_progress.c.job_id.in_(job_ids)))
        db.execute(delete(job_rounds).where(job_rounds.c.job_id.in_(job_ids)))
        db.execute(delete(chat_messages).where(chat_messages.c.work_id == identifier))
        db.execute(delete(work_events).where(work_events.c.work_id == identifier))
        db.execute(delete(conversations).where(conversations.c.work_id == identifier))
        db.execute(delete(versions).where(versions.c.work_id == identifier))
        db.execute(delete(jobs).where(jobs.c.work_id == identifier))
        db.execute(delete(works).where(works.c.id == identifier))
