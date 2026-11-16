"""Hardware-free tests for overlay GPU admission, cross-process leases and bubblewrap device boundaries."""

import asyncio
from collections import Counter
from contextlib import AsyncExitStack
import os
from pathlib import Path
import select
import subprocess
import sys
import threading

import pytest

from server.remotion_templates import gpu_resources as gpu
from server.remotion_templates.settings import Settings

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux DRM and flock admission")


def device(index=0, total=4000, used=0, sessions=0, **kw):
    """An independent NVIDIA board; IDs, minor numbers and PCI locations need not agree with enumeration order."""
    return gpu.GPU(f"GPU-aaaaaaaa-bbbb-cccc-dddd-{index:012x}", index, index + 2, f"0000:{index+1:02x}:00.0",
                   total, used, sessions, {}, (f"card{index}", f"renderD{128+index}"), **{"cuda": True, **kw})


def claim(pool, chunks=1, **kw):
    """Use the common real business layout unless testing another input explicitly."""
    args = dict(width=1080, height=1920, pages=4, chunks=chunks, skip_graphics=set(), skip_nvenc=set())
    return pool.claim(**(args | kw))


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """Observe mutable GPU state via the real admission path without depending on a system GPU or service config."""
    cards = [device()]
    settings = Settings(_env_file=None, overlay_gpu_lease_dir=tmp_path / "leases", overlay_gpu_pair_mb=768,
                        overlay_gpu_webm_mb=256)
    monkeypatch.setattr(gpu, "_inventory", lambda: (cards, True))
    monkeypatch.setattr(gpu.shutil, "which", lambda name: "/usr/bin/" + name)
    return cards, settings


@pytest.mark.anyio
async def test_host_sessions_excluded_cards_and_partial_chunks(machine, monkeypatch):
    """GPU 4 and excluded UUIDs consume real host slots; one eligible dual-stream chunk wins over GPU WebM."""
    cards, settings = machine
    excluded = device(2, sessions=4)
    monkeypatch.setattr(gpu, "EXCLUDED_GPU_UUIDS", (excluded.uuid.upper(),))
    low, active = device(0), device(1, sessions=1)
    cards[:] = [excluded, device(4, sessions=3), active, low]
    pool = gpu.GPUPool(settings)
    async with claim(pool, 5) as first:
        assert (first.route, first.uuid, first.chunks) == ("h264-pair", low.uuid, 1)
        async with claim(pool, 5) as second:
            assert (second.route, second.uuid, second.chunks) == ("gpu-webm", active.uuid, 2)
    cards[:] = [device(4), excluded]
    async with claim(pool, 5) as none:
        assert (none.route, none.uuid, none.chunks, none.reason) == ("cpu-webm", None, 5, "gpu_device_unavailable")


@pytest.mark.anyio
@pytest.mark.parametrize("occupied,route", [(8, "h264-pair"), (9, "gpu-webm")])
async def test_ten_host_sessions_and_gpu_exclusions_are_code_policy(machine, monkeypatch, occupied, route):
    """Eight external sessions leave room for both streams; nine leave only drawing, regardless of stale .env keys."""
    cards, settings = machine
    cards[0].sessions = occupied
    monkeypatch.setenv("IMV_OVERLAY_GPU_NVENC_SESSIONS", "1")
    monkeypatch.setenv("IMV_OVERLAY_GPU_EXCLUDE_UUIDS", cards[0].uuid)
    pool = gpu.GPUPool(Settings(_env_file=None, overlay_gpu_lease_dir=settings.overlay_gpu_lease_dir))
    async with claim(pool) as granted:
        assert (granted.route, granted.uuid) == (route, cards[0].uuid)


@pytest.mark.anyio
async def test_realized_memory_and_two_sessions_not_counted_twice(machine, monkeypatch):
    """A second renderer can use the exact 85% limit while an already-visible Chrome/FFmpeg is still reserved."""
    cards, settings = machine
    cards.append(device(4, sessions=6))
    card = cards[0] = device(total=2000, used=164)
    first, second = gpu.GPUPool(settings), gpu.GPUPool(settings)
    monkeypatch.setattr(gpu, "_process", lambda pid: {811: (1, 30), 812: (811, 31), 813: (811, 32)}.get(pid))
    async with claim(first) as running:
        running.attach_worker(811)
        card.used, card.sessions = 864, 2
        card.memory_by_pid, card.encoder_pids = {812: 170, 813: 530}, Counter({813: 2})
        async with claim(second) as next_job:
            assert (next_job.route, next_job.chunks) == ("h264-pair", 1)
        card.used += 1
        async with claim(second) as too_large:
            assert too_large.route == "gpu-webm"
        # External sessions have no matching job ancestor and must still count against the host.
        card.used -= 1
        card.sessions += 1
        async with claim(second) as no_session:
            assert (no_session.route, no_session.reason) == ("gpu-webm", "nvenc_sessions_unavailable")


@pytest.mark.anyio
async def test_page_size_budget_concurrency_and_cleanup(machine):
    """Hold arbitrary numbers of jobs, reject a new over-budget route, reclaim on cancellation, and scale larger input."""
    cards, settings = machine
    cards[0] = device(total=6400)
    pool = gpu.GPUPool(settings)
    async with AsyncExitStack() as stack:
        for route in ["h264-pair"] * 5 + ["gpu-webm"] * 6:
            assert (await stack.enter_async_context(claim(pool))).route == route
        exhausted = await stack.enter_async_context(claim(pool))
        assert (exhausted.route, exhausted.reason) == ("cpu-webm", "gpu_memory_unavailable")
    async with claim(pool, 9) as released:
        assert released.chunks == 2
    with pytest.raises(asyncio.CancelledError):
        async with claim(pool):
            raise asyncio.CancelledError()
    assert all(not lease["sessions"] for lease in _live(pool))
    cards[0] = device(total=2000)
    async with claim(pool, width=2160, height=3840, pages=8) as large:
        assert large.route == "cpu-webm"


def _live(pool):
    """Read the same locked ledger another process uses during admission."""
    with pool._lock():
        return pool._leases()


@pytest.mark.anyio
async def test_another_process_holds_reservation_until_crash(machine):
    """An independent service process with two pending NVENC sessions blocks admission; killing it releases the lease."""
    cards, settings = machine
    cards[0].sessions = 8
    settings.overlay_gpu_lease_dir.mkdir()
    child = (
        "import fcntl,json,sys,time; "
        "lease=open(sys.argv[1],'w'); fcntl.flock(lease,fcntl.LOCK_EX); "
        "json.dump({'uuid':sys.argv[2],'mib':768,'sessions':2},lease); lease.flush(); "
        "print('reserved',flush=True); time.sleep(10)"
    )
    pool = gpu.GPUPool(settings)
    with subprocess.Popen([sys.executable, "-c", child, str(settings.overlay_gpu_lease_dir / "child.lease"),
                           cards[0].uuid], stdout=subprocess.PIPE) as process:
        try:
            assert select.select([process.stdout], [], [], 5)[0] and process.stdout.readline() == b"reserved\n"
            async with claim(pool) as running:
                assert running.route == "gpu-webm"
        finally:
            process.kill()
            process.wait(timeout=5)
    async with claim(pool) as recovered:
        assert recovered.route == "h264-pair"


@pytest.mark.anyio
async def test_untrusted_default_temp_ledger_cannot_redirect_a_service_write(machine, tmp_path, monkeypatch):
    """A precreated symlink in shared /tmp leaves the unrelated directory untouched and uses the CPU route."""
    _, settings = machine
    settings.overlay_gpu_lease_dir = None
    monkeypatch.setattr(gpu.tempfile, "gettempdir", lambda: str(tmp_path))
    outside = tmp_path / "unrelated"
    outside.mkdir()
    (tmp_path / f"imv-overlay-gpu-{os.getuid()}").symlink_to(outside, target_is_directory=True)
    async with claim(gpu.GPUPool(settings)) as fallback:
        assert (fallback.route, fallback.reason) == ("cpu-webm", "gpu_accounting_unavailable")
    assert not list(outside.iterdir())


@pytest.mark.anyio
async def test_skip_and_cooldown_scope_expiry(machine, monkeypatch):
    """Skip lists stay local; an actual encoder failure cools only NVENC, and the retry does not renew its timestamp."""
    cards, settings = machine
    pool, other = gpu.GPUPool(settings), gpu.GPUPool(settings)
    uuid = cards[0].uuid
    async with claim(pool, skip_nvenc={uuid}) as skipped:
        assert skipped.route == "gpu-webm"
    async with claim(other) as healthy:
        assert healthy.route == "h264-pair"
    pool.failed(uuid, "encoder")
    mark = settings.overlay_gpu_lease_dir / f"{uuid}.encoder"
    before = mark.stat().st_mtime
    async with claim(other, skip_nvenc={uuid}) as only_graphics:
        assert only_graphics.route == "gpu-webm" and mark.stat().st_mtime == before
    pool.failed(uuid, "graphics")
    async with claim(other) as no_graphics:
        assert no_graphics.route == "cpu-webm"
    monkeypatch.setattr(gpu.time, "time", lambda: before + gpu.COOLDOWN_SECONDS + 2)
    async with claim(other) as recovered:
        assert recovered.route == "h264-pair"


@pytest.mark.anyio
async def test_inventory_missing_ffmpeg_or_nvenc(machine, monkeypatch):
    """Unsupported hardware/session visibility never breaks a CPU-capable worker; no ffmpeg retains browser graphics."""
    cards, settings = machine
    pool = gpu.GPUPool(settings)
    cards[0].sessions = None
    async with claim(pool) as unknown:
        assert unknown.route == "gpu-webm"
    cards[0].sessions = 0
    monkeypatch.setattr(gpu.shutil, "which", lambda name: None)
    async with claim(pool) as no_ffmpeg:
        assert (no_ffmpeg.route, no_ffmpeg.reason) == ("gpu-webm", "ffmpeg_unavailable")
    cards.clear()
    async with claim(pool, 3) as missing:
        assert (missing.route, missing.uuid, missing.chunks) == ("cpu-webm", None, 3)


@pytest.mark.anyio
@pytest.mark.parametrize("width,height,skipped", [(1079,1920,set()), (1080,1919,set()), (32,32,{"*"})])
async def test_unsupported_canvas_uses_webm_without_cooling_a_healthy_gpu(machine, width, height, skipped):
    """Odd dimensions or a confirmed per-job NVENC dimension rejection preserve the card for a compatible job."""
    cards, settings = machine
    pool = gpu.GPUPool(settings)
    async with claim(pool, width=width, height=height, skip_nvenc=skipped) as odd:
        assert (odd.route, odd.uuid, odd.reason) == ("gpu-webm", cards[0].uuid, "nvenc_dimensions_unsupported")
    async with claim(pool) as even:
        assert even.route == "h264-pair"


def test_inventory_keeps_driver_index_and_external_sessions(monkeypatch):
    """Physical index is read independently of XML order/minor; process duplicates and driver-reserved FB are accounted once."""
    card = device(4)
    xml = f"""<nvidia_smi_log><gpu id="00000000:05:00.0"><uuid>{card.uuid}</uuid><minor_number>9</minor_number>
    <fb_memory_usage><total>1000 MiB</total><used>50 MiB</used><free>900 MiB</free></fb_memory_usage>
    <encoder_stats><session_count>2</session_count></encoder_stats><processes>
    <process_info><pid>8</pid><used_memory>40 MiB</used_memory></process_info>
    <process_info><pid>8</pid><used_memory>40 MiB</used_memory></process_info></processes></gpu></nvidia_smi_log>"""
    monkeypatch.setattr(gpu, "_smi", lambda *args, **kw: xml if "-x" in args else f"4, {card.uuid}")
    monkeypatch.setattr(gpu.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(gpu, "_encoder_owners", lambda cards: None)
    (actual,), _ = gpu._inventory()
    assert (actual.index, actual.minor, actual.pci, actual.sessions, actual.used, actual.memory_by_pid) == (4, 9, card.pci, 2, 100, {8: 40})
    monkeypatch.setattr(gpu, "_smi", lambda *args, **kw: "")
    assert gpu._inventory() == ([], False)


@pytest.mark.anyio
async def test_broken_card_keeps_healthy_graphics_without_guessing_host_sessions(tmp_path, monkeypatch):
    """A hanging full-card query and partial index CSV recover GPU 0/5 in parallel; GPU 4 and the excluded UUID stay untouched."""
    cards = {i: device(i, sessions=int(i == 0)) for i in (0, 2, 4, 5)}
    barrier = threading.Barrier(2)
    local = []
    monkeypatch.setattr(gpu.shutil, "which", lambda name: "/usr/bin/" + name)
    def smi(*args, timeout=2):
        """Model -L enumerating past GPU 4 when the original index and full XML queries stopped early."""
        if args[0].startswith("--query"):
            return "\n".join(f"{i}, {cards[i].uuid}" for i in (0, 2))
        if args[0] == "-L":
            return "\n".join(f"GPU {i}: GPU (UUID: {card.uuid})" for i, card in cards.items())
        if args[0] == "-i":
            local.append(args[1])
            assert 0 < timeout <= 2
            barrier.wait(timeout=2)
            return args[1]
        return ""
    monkeypatch.setattr(gpu, "_smi", smi)
    monkeypatch.setattr(gpu, "_read_cards", lambda xml, indexes: [c for c in cards.values() if c.uuid == xml])
    monkeypatch.setattr(gpu, "EXCLUDED_GPU_UUIDS", (cards[2].uuid,))
    pool = gpu.GPUPool(Settings(_env_file=None, overlay_gpu_lease_dir=tmp_path))
    async with claim(pool) as recovered:
        assert (recovered.route, recovered.uuid, recovered.reason) == ("gpu-webm", cards[5].uuid, "nvenc_sessions_unknown")
    assert set(local) == {cards[0].uuid, cards[5].uuid}
