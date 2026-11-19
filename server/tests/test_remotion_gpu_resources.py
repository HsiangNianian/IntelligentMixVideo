"""Hardware-free tests for overlay GPU admission, cross-process leases and bubblewrap device boundaries."""

import asyncio
from collections import Counter
from contextlib import AsyncExitStack
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from server.remotion_templates import gpu_resources as gpu
from server.remotion_templates import nvenc_sessions
from server.remotion_templates.renderer import Renderer
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
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_text('#!/bin/sh\necho "$2" >> "$0.calls"\ncat "$0$2"\n')
    ffmpeg.chmod(0o755)
    (tmp_path / "ffmpeg-encoders").write_text(" V....D h264_nvenc NVIDIA H.264 encoder\n")
    filters = ("format", "split", "scale", "alphaextract", "setparams")
    (tmp_path / "ffmpeg-filters").write_text("".join(f" ... {name} V->V\n" for name in filters))
    monkeypatch.setattr(gpu, "_inventory", lambda: (cards, True))
    monkeypatch.setattr(gpu, "_encoder_owners", lambda cards: None)
    monkeypatch.setattr(gpu.shutil, "which", lambda name: str(tmp_path / name))
    return cards, settings


@pytest.fixture
def nvml_machine(tmp_path, monkeypatch):
    """Keep real probe ordering and admission, with mutable NVML sessions independent of the old SMI snapshot."""
    cards = [device(), device(1), device(4)]
    settings = Settings(_env_file=None, overlay_gpu_lease_dir=tmp_path / "leases")
    driver = SimpleNamespace(pids={card.uuid: [] for card in cards}, queried=[], shutdowns=0)

    def handle(uuid, output):
        """Map physical UUIDs to nonzero opaque test handles."""
        output._obj.value = list(driver.pids).index(uuid.decode()) + 1
        return 0

    def sessions(handle, size, rows):
        """Match NVML's updated count and insufficient-buffer contract without real GPU access."""
        uuid = list(driver.pids)[handle.value - 1]
        driver.queried.append(uuid)
        pids, capacity = driver.pids[uuid], size._obj.value
        size._obj.value = len(pids)
        if len(pids) > capacity:
            return 7
        for row, pid in zip(rows, pids):
            row[1] = pid
        return 0

    def shutdown():
        """Record that each successful initialization is released even when a device query fails."""
        driver.shutdowns += 1
        return 0

    driver.library = SimpleNamespace(nvmlInit_v2=lambda: 0, nvmlShutdown=shutdown,
                                    nvmlDeviceGetHandleByUUID=handle, nvmlDeviceGetEncoderSessions=sessions)
    def driver_output(command, timeout):
        """Run the helper against this process's mock driver and pass its wire result to the real admission code."""
        return json.dumps(nvenc_sessions.session_pids(command[4:], int(command[3])))

    monkeypatch.setattr(nvenc_sessions.ctypes, "CDLL", lambda name: driver.library)
    monkeypatch.setattr(gpu, "_driver_output", driver_output)
    monkeypatch.setattr(gpu, "_inventory", lambda: (cards, True))
    monkeypatch.setattr(gpu, "_nvenc_unavailable_reason", lambda binary: None)
    return cards, settings, driver


@pytest.mark.anyio
@pytest.mark.parametrize("changed_index", [0, 1])
async def test_refreshes_sessions_after_ffmpeg_probe(nvml_machine, monkeypatch, changed_index):
    """A new external session on a busy or idle card during probing must prevent admitting an eleventh session."""
    cards, settings, driver = nvml_machine
    cards[0].sessions = 8
    driver.pids[cards[0].uuid] = [50000] * 8

    def ffmpeg_probe(binary):
        """An external encoder starts after SMI sampling but before the final admission snapshot."""
        driver.pids[cards[changed_index].uuid].append(50001)
        return None

    monkeypatch.setattr(gpu, "_nvenc_unavailable_reason", ffmpeg_probe)
    async with claim(gpu.GPUPool(settings)) as grant:
        assert (grant.route, grant.reason) == ("gpu-webm", "nvenc_sessions_unavailable")
    assert sum(card.sessions for card in cards[:2]) == 9
    assert cards[changed_index].encoder_pids[50001] == 1
    assert cards[0].encoder_pids[50000] == 8
    assert driver.queried == [card.uuid for card in cards[:2]]


@pytest.mark.anyio
async def test_gpu4_is_omitted_from_nvenc_accounting(nvml_machine):
    """The deployment-disabled GPU 4 is neither queried for encoder sessions nor allowed to block healthy NVENC."""
    cards, settings, driver = nvml_machine
    cards[0].sessions, cards[2].sessions, cards[2].total = 8, None, 0
    driver.pids[cards[0].uuid] = [50000] * 8
    async with claim(gpu.GPUPool(settings)) as grant:
        assert (grant.route, grant.uuid, grant.chunks) == ("h264-pair", cards[1].uuid, 1)
    assert driver.queried == [card.uuid for card in cards[:2]]


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["library", "init", "symbol", "handle", "sessions", "buffer"])
async def test_session_refresh_failure_uses_webm_and_recovers(nvml_machine, monkeypatch, failure):
    """An unconfirmed fresh session count cannot reuse old NVENC capacity; the next healthy probe can recover."""
    cards, settings, driver = nvml_machine
    pool = gpu.GPUPool(settings)

    def missing_library(name):
        """Model a host where the NVML library cannot be loaded."""
        raise OSError("NVML unavailable")

    with monkeypatch.context() as failing:
        if failure == "library":
            failing.setattr(nvenc_sessions.ctypes, "CDLL", missing_library)
        elif failure == "init":
            failing.setattr(driver.library, "nvmlInit_v2", lambda: 9)
        elif failure == "symbol":
            failing.delattr(driver.library, "nvmlDeviceGetHandleByUUID")
        elif failure == "handle":
            failing.setattr(driver.library, "nvmlDeviceGetHandleByUUID", lambda *args: 15)
        elif failure == "sessions":
            failing.setattr(driver.library, "nvmlDeviceGetEncoderSessions", lambda *args: 15)
        else:
            failing.setitem(driver.pids, cards[0].uuid, [50000] * (gpu.NVENC_SESSION_LIMIT + 1))
        async with claim(pool) as grant:
            assert (grant.route, grant.reason) == ("gpu-webm", "nvenc_sessions_unknown")
    assert driver.shutdowns == (0 if failure in ("library", "init", "symbol") else 1)
    async with claim(pool) as recovered:
        assert recovered.route == "h264-pair"
    assert not list(settings.overlay_gpu_lease_dir.glob("*.encoder"))


@pytest.mark.anyio
async def test_stalled_session_probe_releases_waiters_and_recovers(tmp_path, monkeypatch):
    """A child that emits plausible data then stalls cannot admit NVENC or prevent the next claim from probing."""
    card = device(sessions=8)
    pool = gpu.GPUPool(Settings(_env_file=None, overlay_gpu_lease_dir=tmp_path / "leases"))
    monkeypatch.setattr(gpu, "_inventory", lambda: ([card], True))
    monkeypatch.setattr(gpu, "_nvenc_unavailable_reason", lambda binary: None)
    monkeypatch.setattr(gpu, "__file__", str(tmp_path / "gpu_resources.py"))
    helper = tmp_path / "nvenc_sessions.py"
    report = f"print({json.dumps({card.uuid: [50000] * 8})!r}, flush=True)\n"
    helper.write_text(report + "import time\ntime.sleep(20)\n")

    async def select_route():
        """Use the pool's shared probe exactly as concurrent overlay jobs do."""
        async with claim(pool) as grant:
            return grant.route, grant.reason

    async with asyncio.timeout(8):
        routes = await asyncio.gather(select_route(), select_route())
    assert routes == [("gpu-webm", "nvenc_sessions_unknown")] * 2
    assert card.sessions is None and not card.encoder_pids
    helper.write_text(report)
    assert await select_route() == ("h264-pair", "nvenc_admitted")
    assert card.sessions == 8 and card.encoder_pids == {50000: 8}


@pytest.mark.anyio
async def test_host_sessions_excluded_cards_and_partial_chunks(machine, monkeypatch):
    """GPU 4 is omitted; other excluded UUIDs still consume slots, and dual-stream chunks win over GPU WebM."""
    cards, settings = machine
    excluded = device(2, sessions=4)
    monkeypatch.setattr(gpu, "EXCLUDED_GPU_UUIDS", (excluded.uuid.upper(),))
    low, active = device(0), device(1, sessions=1)
    cards[:] = [excluded, device(4, sessions=3), active, low]
    pool = gpu.GPUPool(settings)
    async with claim(pool, 5) as first:
        assert (first.route, first.uuid, first.chunks) == ("h264-pair", low.uuid, 2)
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
    card = cards[0] = device(total=2000, used=164, sessions=6)
    first, second = gpu.GPUPool(settings), gpu.GPUPool(settings)
    monkeypatch.setattr(gpu, "_process", lambda pid: {811: (1, 30), 812: (811, 31), 813: (811, 32)}.get(pid))
    async with claim(first) as running:
        running.attach_worker(811)
        card.used, card.sessions = 864, 8
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
@pytest.mark.parametrize("listing,missing", [("encoders", "h264_nvenc"), ("filters", "alphaextract"), ("filters", "setparams")])
async def test_incomplete_ffmpeg_build_admits_gpu_webm(machine, tmp_path, listing, missing):
    """A present FFmpeg missing the encoder or a required mask filter keeps GPU drawing, with one probe per binary."""
    cards, settings = machine
    path = tmp_path / f"ffmpeg-{listing}"
    path.write_text(path.read_text().replace(missing, missing + "_other"))
    pool = gpu.GPUPool(settings)
    for _ in range(2):
        async with claim(pool) as grant:
            assert (grant.route, grant.uuid, grant.reason) == ("gpu-webm", cards[0].uuid, "nvenc_pipeline_unavailable")
    assert (tmp_path / "ffmpeg.calls").read_text().splitlines().count("-encoders") == 1


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
    (actual,), _ = gpu._inventory()
    assert (actual.index, actual.minor, actual.pci, actual.sessions, actual.used, actual.memory_by_pid) == (4, 9, card.pci, 2, 100, {8: 40})
    monkeypatch.setattr(gpu, "_smi", lambda *args, **kw: "")
    assert gpu._inventory() == ([], False)


@pytest.mark.parametrize("elapsed,complete", [(3.65, True), (7.9, True), (8.1, False)])
@pytest.mark.parametrize("gpu4_metrics", ["valid", "unreadable", "absent"])
def test_slow_multigpu_snapshot_preserves_session_visibility(monkeypatch, elapsed, complete, gpu4_metrics):
    """Slow snapshots tolerate GPU 4's missing metrics/node; a real timeout still falls back to healthy cards."""
    cards = [device(i, sessions=3 if i == 4 else 0) for i in range(8)]
    if gpu4_metrics != "valid":
        cards[4].total, cards[4].sessions = 0, None
    nodes = {card.uuid: f"""<gpu id="{card.pci}"><uuid>{card.uuid}</uuid><minor_number>{card.minor}</minor_number>
        <fb_memory_usage><total>{card.total} MiB</total><free>{card.total} MiB</free></fb_memory_usage>
        <encoder_stats><session_count>{card.sessions}</session_count></encoder_stats></gpu>""" for card in cards}
    snapshot = "<nvidia_smi_log><attached_gpus>8</attached_gpus>" + "".join(
        node for uuid, node in nodes.items() if gpu4_metrics != "absent" or uuid != cards[4].uuid) + "</nvidia_smi_log>"
    probes = []

    def smi(*args, timeout=2):
        """Model a slow full response and fast per-card recovery without sleeping or using the host driver."""
        probes.append((args, timeout))
        if args[0].startswith("--query"):
            return "\n".join(f"{card.index}, {card.uuid}" for card in cards)
        if args == ("-q", "-x"):
            return snapshot if elapsed <= timeout else snapshot[:snapshot.index(nodes[cards[3].uuid])]
        if args[0] == "-i":
            return "<nvidia_smi_log>" + nodes[args[1]] + "</nvidia_smi_log>"
        return ""

    monkeypatch.setattr(gpu, "_smi", smi)
    monkeypatch.setattr(gpu.shutil, "which", lambda name: "/usr/bin/" + name)
    actual, known = gpu._inventory()
    assert known is complete
    assert {card.index: card.sessions for card in actual} == {
        card.index: card.sessions for card in cards if card.index != 4 or (complete and gpu4_metrics != "absent")}
    assert [timeout for args, timeout in probes if args == ("-q", "-x")] == [8]
    assert all(0 < timeout <= 2 for args, timeout in probes if args[0] == "-i")


@pytest.mark.parametrize("csv_indices,listed_indices,sampled,extra_uuid,known", [
    ((0, 1, 2, 3, 4, 5), (), "N/A", None, True),
    ((0, 1, 2), (0, 1, 2, 3, 4, 5), "0", None, True),
    ((0, 1, 2, 3, 5), (0, 1, 2, 3, 5), "0", "N/A", True),
    ((0, 1, 2, 4, 5), (0, 1, 2, 4, 5), "0", None, False),
    ((0, 1, 2, 3, 5), (0, 1, 2, 3, 5), "0", device(6).uuid, False),
])
def test_nvenc_refresh_requires_all_enabled_gpu_identities(monkeypatch, csv_indices, listed_indices, sampled, extra_uuid, known):
    """Refresh after reconciling every enabled index, even with stale N/A stats or one anonymous disabled card."""
    cards = [device(i) for i in range(6)]
    nodes = "".join(
        f"<gpu><uuid>{card.uuid}</uuid><fb_memory_usage><total>4000 MiB</total></fb_memory_usage>"
        f"<encoder_stats><session_count>{sampled}</session_count></encoder_stats></gpu>"
        for card in cards if card.index != 4
    )
    if extra_uuid is not None:
        nodes += f"<gpu><uuid>{extra_uuid}</uuid></gpu>"
    xml = f"<nvidia_smi_log><attached_gpus>6</attached_gpus>{nodes}</nvidia_smi_log>"
    refreshed = []

    def smi(*args, **kwargs):
        """Only the compact listing can fill a gap in the initial CSV enumeration."""
        if args[0].startswith("--query"):
            return "\n".join(f"{i}, {cards[i].uuid}" for i in csv_indices)
        if args == ("-L",):
            return "\n".join(f"GPU {i}: GPU (UUID: {cards[i].uuid})" for i in listed_indices)
        return xml

    def refresh(boards):
        """Record the physical cards on which an authoritative NVML query is attempted."""
        refreshed.extend(card.index for card in boards)
        for card in boards:
            card.sessions = 0

    monkeypatch.setattr(gpu, "_smi", smi)
    monkeypatch.setattr(gpu.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(gpu, "_nvenc_unavailable_reason", lambda binary: None)
    monkeypatch.setattr(gpu, "_encoder_owners", refresh)
    _, complete, _ = gpu._probe_host()
    assert complete is known
    assert refreshed == ([0, 1, 2, 3, 5] if known else [])


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


def test_bwrap_selected_minor_drm_and_only_overlay(tmp_path, monkeypatch):
    """The command exposes the selected physical board, only GPU overlay workers receive devices, and env is cleared first."""
    card = device(1)
    grant = gpu.GPUGrant("h264-pair", card.uuid, 1, "nvenc_admitted", card)
    monkeypatch.setattr(gpu.Path, "is_char_device", lambda p: str(p) in {"/dev/nvidia3", "/dev/nvidiactl", "/dev/nvidia-uvm", "/dev/dri/card1", "/dev/dri/renderD129"})
    original_dir, original_resolve, original_stat = Path.is_dir, Path.resolve, Path.stat
    physical = Path("/sys/devices/pci0000:00") / card.pci
    pci = Path("/sys/bus/pci/devices") / card.pci
    monkeypatch.setattr(gpu.Path, "is_dir", lambda p: True if p == pci else original_dir(p))
    monkeypatch.setattr(gpu.Path, "resolve", lambda p,*args,**kw: physical if p == pci or
                        (str(p).startswith("/sys/class/drm/") and p.name == "device") else original_resolve(p,*args,**kw))
    monkeypatch.setattr(gpu.Path, "stat", lambda p,*a,**kw: SimpleNamespace(st_rdev=os.makedev(226,129))
                        if str(p).startswith("/dev/") else original_stat(p,*a,**kw))
    renderer = Renderer(Settings(_env_file=None, data_dir=tmp_path))
    args = renderer.command(tmp_path, worker="overlay-worker.mjs", gpu=grant)
    exposed = {args[idx+1] for idx, arg in enumerate(args) if arg == "--dev-bind"}
    assert exposed == {"/dev/nvidia3","/dev/nvidiactl","/dev/nvidia-uvm","/dev/dri/card1","/dev/dri/renderD129"}
    assert args.index("--clearenv") < args.index("CUDA_VISIBLE_DEVICES") and args[args.index("CUDA_VISIBLE_DEVICES")+1] == card.uuid
    assert "--unshare-all" in args and "--dev" in args and "--cap-drop" in args
    assert not any(args[i:i+3] in (["--ro-bind","/sys","/sys"],["--dev-bind","/dev","/dev"]) for i in range(len(args)))
    for worker in ("worker.mjs","presentation-worker.mjs","tool-validation-worker.mjs"):
        with pytest.raises(ValueError, match="Only the isolated overlay"):
            renderer.command(tmp_path, worker=worker, gpu=grant)
    assert "--dev-bind" not in renderer.command(tmp_path, worker="overlay-worker.mjs")


@pytest.mark.parametrize("standard_exists", [True, False])
def test_only_nvenc_binds_ffmpeg_from_a_nonstandard_prefix(tmp_path, monkeypatch, standard_exists):
    """The NVENC worker sees the probed host binary, while both WebM routes use the bundled encoder."""
    executable = tmp_path / "opt" / "ffmpeg"
    executable.parent.mkdir()
    executable.touch()
    real_which = gpu.shutil.which
    real_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda p: standard_exists if p == Path("/usr/bin/ffmpeg") else real_is_file(p))
    monkeypatch.setattr(gpu.shutil, "which", lambda name: str(executable) if name == "ffmpeg" else real_which(name))
    monkeypatch.setattr("server.remotion_templates.renderer.sandbox_gpu", lambda grant: ([], []))
    renderer = Renderer(Settings(_env_file=None, data_dir=tmp_path))
    card = device()
    args = renderer.command(tmp_path, worker="overlay-worker.mjs", gpu=gpu.GPUGrant("h264-pair", card.uuid, 1, "test", card))
    i = args.index(str(executable))
    target = "/usr/bin/ffmpeg" if standard_exists else "/runtime-bin/ffmpeg"
    assert args[i-1:i+2] == ["--ro-bind", str(executable), target]
    assert args[args.index("PATH")+1].split(":")[0] == str(Path(target).parent)
    for grant in (None, gpu.GPUGrant("gpu-webm", card.uuid, 1, "test", card)):
        assert str(executable) not in renderer.command(tmp_path, worker="overlay-worker.mjs", gpu=grant)


@pytest.mark.anyio
@pytest.mark.parametrize("log,kind", [
    ("bwrap: Can't bind mount /dev/nvidia-uvm: No such device", "encoder"),
    ("bwrap: Can't find source path /dev/dri/renderD129", "graphics"),
    ("[overlay] browser connected\nbwrap: Can't find source path /dev/dri/renderD129", None),
])
async def test_only_host_bwrap_setup_failures_get_hardware_kind(tmp_path, monkeypatch, log, kind):
    """Known driver/device mount errors permit targeted retry; arbitrary later user-renderer output stays ordinary."""
    renderer = Renderer(Settings(_env_file=None, data_dir=tmp_path))
    card = device()
    grant = gpu.GPUGrant("h264-pair", card.uuid, 1, "test", card)
    monkeypatch.setattr(renderer, "command", lambda *args, **kw: ["isolated-worker"])
    class Exited:
        """An already-reaped worker, represented by the same wait contract as asyncio.Process."""
        pid = 999999
        returncode = 1

        async def wait(self):
            """Return the simulated worker exit code without starting a child process."""
            return self.returncode

    async def failed(*args, stdout, **kw):
        """Place host setup or user-renderer output into the private attempt log."""
        stdout.write(log.encode())
        return Exited()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", failed)
    with pytest.raises(RuntimeError) as caught:
        await renderer.run_worker(tmp_path, worker="overlay-worker.mjs", gpu=grant)
    if kind:
        assert isinstance(caught.value, gpu.GPUWorkerError) and caught.value.kind == kind
    else:
        assert not isinstance(caught.value, gpu.GPUWorkerError)
