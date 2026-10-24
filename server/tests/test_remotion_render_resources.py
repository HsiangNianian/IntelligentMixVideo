"""共享渲染预算的资源预留、并发排队、降档和取消释放；不依赖宿主当前负载。"""

import asyncio
from pathlib import Path

import pytest

from server.remotion_templates import render_resources as resources
from server.remotion_templates.renderer import Renderer
from server.remotion_templates.settings import Settings

GIB = 1024**3


@pytest.fixture
def capacity(monkeypatch):
    """六个可用于渲染的核，宿主内存足够；真正的限制由被测预算实施。"""
    monkeypatch.setattr(resources, "renderer_cpus", lambda reserve: list(range(6)))
    monkeypatch.setattr(resources, "memory_headroom", lambda reserve: (24 - 32 * reserve / 100) * GIB)
    return resources.RenderCapacity(Settings(_env_file=None, overlay_resource_wait_seconds=1, overlay_chunk_memory_mb=512,
                                             overlay_global_chunk_concurrency=6))


@pytest.mark.anyio
async def test_six_videos_can_use_thirty_six_chunks_when_server_has_capacity(monkeypatch):
    """模拟大内存服务器：允许六条各六块；第七条等待全局预算，而非把服务器限制成六块。"""
    monkeypatch.setattr(resources, "renderer_cpus", lambda reserve: list(range(96)))
    monkeypatch.setattr(resources, "memory_headroom", lambda reserve: 200 * GIB)
    capacity = resources.RenderCapacity(Settings(_env_file=None))
    from contextlib import AsyncExitStack
    async with AsyncExitStack() as stack:
        grants = [await stack.enter_async_context(capacity.claim(12, 1)) for _ in range(6)]
        assert [grant.chunks for grant in grants] == [6] * 6
        assert (capacity.chunks, capacity.pages) == (36, 36)
        assert sum(grant.reserved_memory_mb for grant in grants) == 42 * 1024
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(.02), capacity.claim(6, 1):
                pytest.fail("总量超过 36 块")
        assert capacity.chunks == 36
    assert (capacity.chunks, capacity.pages, capacity.memory_units) == (0, 0, 0)


@pytest.mark.anyio
async def test_videos_share_six_chunks_and_release_after_cancel(capacity):
    """一条视频占满六块时另一条排队，取消前者后后者才能进入；取消后不泄漏预算。"""
    first_entered, second_entered = asyncio.Event(), asyncio.Event()

    async def first():
        async with capacity.claim(12, 1) as grant:
            assert grant.chunks == 6
            first_entered.set()
            await asyncio.Future()

    async def second():
        async with capacity.claim(6, 1) as grant:
            assert grant.chunks == 6
            second_entered.set()

    a = asyncio.create_task(first())
    await first_entered.wait()
    b = asyncio.create_task(second())
    await asyncio.sleep(.02)
    assert not second_entered.is_set()
    a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await a
    await asyncio.wait_for(b, 1)
    assert second_entered.is_set()
    assert (capacity.chunks, capacity.pages, capacity.memory_units) == (0, 0, 0)


@pytest.mark.anyio
async def test_cpu_budget_accounts_for_pages_per_chunk(capacity):
    """每块开两页时最多三块；短视频只申请一块，剩余资源可服务另一条。"""
    async with capacity.claim(1, 2) as short:
        assert short.chunks == 1
        async with capacity.claim(12, 2) as long:
            assert long.chunks == 2
            assert (capacity.chunks, capacity.pages) == (3, 6)
    async with capacity.claim(12, 2) as grant:
        assert grant.chunks == 3
    with pytest.raises(RuntimeError, match="帧并发"):
        async with capacity.claim(1, 7):
            pytest.fail("单块请求超过 CPU 预算")


@pytest.mark.anyio
async def test_memory_reduces_concurrency_and_keeps_host_reserve(capacity, monkeypatch):
    """16 GiB 总量预留 4 GiB，剩余 2 GiB 容纳浏览器预算及三块；不能仍开六块。"""
    monkeypatch.setattr(resources, "memory_headroom", lambda reserve: (6 - 16 * reserve / 100) * GIB)
    async with capacity.claim(6, 1) as grant:
        assert grant.chunks == 3
        assert grant.reserved_memory_mb == 2048


@pytest.mark.anyio
async def test_low_memory_waits_then_recovers_and_waiter_cancel_does_not_reserve(capacity, monkeypatch):
    """不足以保留宿主余量时不启动；取消等待者不扣预算，内存恢复后可以运行。"""
    monkeypatch.setattr(resources, "memory_headroom", lambda reserve: (3 - 16 * reserve / 100) * GIB)
    entered = asyncio.Event()

    async def waiting():
        async with capacity.claim(6, 1):
            entered.set()

    task = asyncio.create_task(waiting())
    await asyncio.sleep(.02)
    assert not entered.is_set() and capacity.chunks == 0
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (capacity.chunks, capacity.pages, capacity.memory_units) == (0, 0, 0)
    task = asyncio.create_task(waiting())
    await asyncio.sleep(.02)
    monkeypatch.setattr(resources, "memory_headroom", lambda reserve: (10 - 16 * reserve / 100) * GIB)
    async with capacity.condition:
        capacity.condition.notify_all()
    await asyncio.wait_for(task, 1)
    assert entered.is_set() and capacity.memory_units == 0


@pytest.mark.anyio
async def test_resource_wait_is_bounded(capacity, monkeypatch):
    """持续资源不足时以明确的私有原因退出，不无限排队或启动 worker。"""
    capacity.settings.overlay_resource_wait_seconds = .02
    monkeypatch.setattr(resources, "memory_headroom", lambda reserve: (3 - 16 * reserve / 100) * GIB)
    with pytest.raises(RuntimeError, match="预算超时"):
        async with capacity.claim(6, 1):
            pytest.fail("资源不足不能进入渲染")
    assert capacity.chunks == 0


def test_parent_cgroup_limits_and_affinity_are_respected(tmp_path, monkeypatch):
    """读取祖先配额，而非只看宿主核数与总内存；CPU 预留反映在实际 worker 命令。"""
    parent, child = tmp_path / "parent", tmp_path / "parent/child"
    child.mkdir(parents=True)
    (parent / "cpu.max").write_text("400000 100000")
    (child / "cpu.max").write_text("max 100000")
    (parent / "memory.high").write_text(str(8 * GIB))
    (parent / "memory.max").write_text(str(10 * GIB))
    (parent / "memory.current").write_text(str(3 * GIB))
    (child / "memory.max").write_text("max")
    monkeypatch.setattr(resources, "cgroup_directories", lambda: [child, parent])
    monkeypatch.setattr(resources.os, "sched_getaffinity", lambda pid: {2, 3, 4, 5, 6, 7, 8, 9})
    original = Path.read_text

    host_available_gib = 24

    def read(path, *args, **kwargs):
        if path == Path('/proc/meminfo'):
            return f"MemTotal: {32 * GIB // 1024} kB\nMemAvailable: {host_available_gib * GIB // 1024} kB\n"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    assert resources.renderer_cpus(25) == [2, 3, 4]
    assert resources.memory_headroom(25) == 3 * GIB
    host_available_gib = 9
    assert resources.memory_headroom(25) == GIB  # 宿主也必须留出 8 GiB，不能只用较小 cgroup 的 2 GiB 余量。
    command = Renderer(Settings(_env_file=None)).command(tmp_path, worker="overlay-worker.mjs")
    assert command[:4] == ["taskset", "--cpu-list", "2,3,4", "bwrap"]
    assert "--unshare-all" in command and "--clearenv" in command
