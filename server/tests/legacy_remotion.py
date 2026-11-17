"""Seed pre-upgrade image rows/objects for migration and deletion regressions; production has no upload writer."""

import hashlib
from pathlib import Path
from uuid import uuid4

from sqlalchemy import insert, select

from server.database import get_engine
from server.remotion_templates.media import normalize_image
from server.remotion_templates.models import Asset
from server.remotion_templates.store import asset_key, now
from server.remotion_templates.tables import assets, legacy_metadata, work_assets


def link_images(store, work, identifiers):
    """Seed the old association table explicitly; current submissions no longer write it."""
    with store.transaction() as db:
        for identifier in identifiers:
            if not db.execute(select(work_assets).where(work_assets.c.work_id == str(work), work_assets.c.asset_id == str(identifier))).first():
                db.execute(insert(work_assets).values(work_id=str(work), asset_id=str(identifier)))


def create_work(store, request, device):
    """Construct a pre-upgrade work and its historical cloud image associations for deletion fixtures."""
    project, job = store.create(request, device)
    link_images(store, project.id, [image.asset_id for image in request.images])
    return project, job


def save_image(content, store, settings, device):
    """Build exactly the normalized asset layout the previous release wrote, in an isolated test database."""
    from PIL import Image
    from io import BytesIO

    legacy_metadata.create_all(get_engine())
    store.initialize()
    payload = normalize_image(content, settings)
    with Image.open(BytesIO(payload)) as image:
        asset = Asset(id=uuid4(), width=image.width, height=image.height,
                      sha256=hashlib.sha256(payload).hexdigest(), created_at=now())
    path: Path = store.root / f"{asset.id}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    try:
        store.objects.put(asset_key(asset.id), path, "image/png")
    finally:
        path.unlink()
    with store.transaction() as db:
        db.execute(insert(assets).values(id=str(asset.id), device_id=str(device), data=asset.model_dump_json()))
    return asset
