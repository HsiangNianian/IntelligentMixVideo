"""Verify the Remotion queue lease: one backend instance per database, takeover after a crash, release on stop.

Recovery on startup interrupts running jobs, so a second backend on the same database must be refused while the
first one renews its lease. The SQLite stand-in for MySQL is used. Run: uv run --locked pytest tests/test_remotion_instance_lease.py.
"""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import update

from server.remotion_templates.models import GenerateTemplateRequest
from server.remotion_templates.runtime import Runtime
from server.remotion_templates.settings import Settings
from server.remotion_templates.store import InstanceInUse, Store, now
from server.remotion_templates.tables import instance

from .conftest import DEVICE


@pytest.fixture
def stores(tmp_path, template_db):
    """Two backend instances (stores) sharing one database, each with its own scratch folder."""
    first, second = Store(tmp_path / "first"), Store(tmp_path / "second")
    first.initialize()
    return first, second


def age_lease(store: Store, seconds: int) -> None:
    """Pretend the holder last renewed its lease this long ago."""
    with store.transaction() as db:
        db.execute(update(instance).values(heartbeat_at=(now() - timedelta(seconds=seconds)).replace(tzinfo=None)))


def test_second_instance_is_refused_while_the_lease_is_fresh(stores):
    """The holder may re-acquire its own lease; another instance is refused with an actionable message and changes nothing."""
    first, second = stores
    first.acquire_lease()
    first.acquire_lease()
    work, job = first.create(GenerateTemplateRequest(description="运行中"), DEVICE)
    first.claim()
    with pytest.raises(InstanceInUse, match="另一个后端实例"):
        second.acquire_lease()
    assert first.job(job.id).status == "running"
    assert first.renew_lease() is True


def test_clean_release_lets_the_next_instance_start_at_once(stores):
    """Releasing on shutdown removes the lease, so a restart does not wait for it to expire."""
    first, second = stores
    first.acquire_lease()
    first.release_lease()
    second.acquire_lease()
    assert second.renew_lease() is True
    first.release_lease()  # releasing a lease it no longer holds leaves the new holder untouched
    assert second.renew_lease() is True


def test_expired_lease_of_a_crashed_instance_is_taken_over(stores):
    """After the TTL the lease is taken over; the old instance then learns it lost it when it renews."""
    first, second = stores
    first.acquire_lease()
    age_lease(first, 31)
    second.acquire_lease()
    assert first.renew_lease() is False
    assert second.renew_lease() is True
    with pytest.raises(InstanceInUse):
        first.acquire_lease()


def test_runtime_startup_leaves_jobs_of_a_live_instance_alone(stores, tmp_path):
    """A refused runtime neither interrupts the other instance's running job nor starts its worker."""
    first, second = stores
    holder = Runtime(first, None, Settings(_env_file=None))
    holder.initialize()
    work, job = first.create(GenerateTemplateRequest(description="线上任务"), DEVICE)
    first.claim()
    with pytest.raises(InstanceInUse):
        Runtime(second, None, Settings(_env_file=None)).initialize()
    assert first.job(job.id).status == "running"


def test_a_lost_lease_fences_the_runtime_and_close_releases_it(stores, monkeypatch):
    """A runtime that finds its lease taken stops claiming jobs; a clean close hands the lease back."""
    first, second = stores
    from server.remotion_templates import runtime as runtime_module

    monkeypatch.setattr(runtime_module, "LEASE_RENEW", 0.01)
    runtime = Runtime(first, None, Settings(_env_file=None))
    runtime.initialize()
    work, job = first.create(GenerateTemplateRequest(description="排队"), DEVICE)

    async def scenario():
        """Let the renewal run, take the lease over, and expect the worker to leave the queued job alone."""
        runtime.start_maintenance()
        await asyncio.sleep(0.05)
        assert not runtime.fenced
        age_lease(first, 60)
        second.acquire_lease()
        await asyncio.sleep(0.1)
        assert runtime.fenced
        await runtime._work()
        assert first.job(job.id).status == "queued"
        await runtime.close()

    asyncio.run(scenario())
    assert second.renew_lease() is True
