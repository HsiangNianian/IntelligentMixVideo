"""Offline migration on temporary SQL tables: preserve accepted sources and refuse undelivered private data."""

import pytest
from sqlalchemy import Column, MetaData, String, Table, delete, func, inspect, insert, select

from server.remotion_templates.models import GenerateTemplateRequest
from server.remotion_templates.storage_migration import RETIRED, backfill, retire
from server.remotion_templates.tables import conversations, effect_sources, legacy_metadata, versions
from .conftest import DEVICE
from .test_remotion_publication import accepted_version, harness, sprite  # noqa: F401 -- shared fixtures


def test_backfill_preserves_versions_and_retirement_preserves_public_assets(harness, sprite, template_db):
    """Old accepted versions survive idempotent backfill; only retired empty tables disappear."""
    store, _work, version = accepted_version(harness, sprite)
    legacy_metadata.create_all(template_db)
    extra = MetaData()
    for name in (*RETIRED[:2], "remotion_sprites"):
        Table(name, extra, Column("id", String(64), primary_key=True))
    extra.create_all(template_db)
    with store.transaction() as db:
        original = db.execute(select(versions.c.data)).scalar_one()
        db.execute(delete(effect_sources))
        db.execute(insert(extra.tables["remotion_sprites"]).values(id="published"))
    assert backfill(template_db) == backfill(template_db) == 1
    with store.reading() as db:
        assert db.execute(select(func.count()).select_from(effect_sources)).scalar_one() == 1
        assert db.execute(select(versions.c.data)).scalar_one() == original
    assert set(retire(template_db)) == set(RETIRED)
    assert not set(RETIRED) & set(inspect(template_db).get_table_names())
    assert {effect_sources.name, versions.name, "remotion_sprites"} <= set(inspect(template_db).get_table_names())
    assert store.version(version.id).candidate == version.candidate


def test_retirement_refuses_pending_legacy_data(harness, sprite, template_db):
    """A migration cannot drop private rows before a client has its copy or retention removes them."""
    store, work, _version = accepted_version(harness, sprite)
    legacy_metadata.create_all(template_db)
    with store.transaction() as db:
        db.execute(insert(conversations).values(work_id=str(work.id), data="[]"))
    with pytest.raises(ValueError, match="awaits migration"):
        retire(template_db)
    with store.reading() as db:
        assert db.execute(select(conversations.c.data)).scalar_one() == "[]"
        assert db.execute(select(func.count()).select_from(effect_sources)).scalar_one() == 1


def test_maintenance_refuses_lease_and_active_jobs(harness, sprite, template_db):
    """Both queue ownership and unfinished executions prevent offline maintenance."""
    store, _work, _version = accepted_version(harness, sprite)
    store.acquire_lease()
    with pytest.raises(ValueError, match="Stop"):
        backfill(template_db)
    store.release_lease()
    store.create(GenerateTemplateRequest(description="仍在排队"), DEVICE)
    with pytest.raises(ValueError, match="Active jobs"):
        retire(template_db)
