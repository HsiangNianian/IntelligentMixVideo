"""Linux overlay GPU admission and device isolation. Live NVIDIA usage and outstanding host leases share one budget."""

import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
import ctypes
from dataclasses import dataclass, field
from functools import cache
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import IO, Literal
import xml.etree.ElementTree as ET

from .settings import Settings

GPU_UUID = re.compile(r"GPU-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.IGNORECASE)
EXCLUDED_GPU_UUIDS: tuple[str, ...] = ()

Route = Literal["h264-pair", "gpu-webm", "cpu-webm"]
COOLDOWN_SECONDS = 60
NVENC_SESSION_LIMIT = 10


class GPUWorkerError(RuntimeError):
    """An attributable graphics or encoder failure that permits another device or render route."""

    def __init__(self, kind: Literal["graphics", "encoder"], reason: str):
        """Keep driver-specific details out of the reason recorded by the composition service."""
        super().__init__(reason)
        self.kind = kind
        self.reason = reason


@dataclass
class GPU:
    """One physical card and a single SMI observation, including workloads outside this service."""

    uuid: str
    index: int
    minor: int
    pci: str
    total: int
    used: int
    sessions: int | None
    memory_by_pid: dict[int, int]
    drm: tuple[str, ...]
    cuda: bool
    encoder_pids: Counter[int] = field(default_factory=Counter)


def _number(value: str | None) -> int | None:
    """An SMI integer or integer with a unit; unsupported/N/A stays unknown."""
    first = (value or "").partition(" ")[0]
    return int(first) if first.isdigit() else None


def _pci_name(value: str) -> str:
    """Normalize SMI's eight-digit PCI domain to the Linux sysfs spelling."""
    match = re.fullmatch(r"([0-9a-f]{4,8}):([\da-f]{2}:[\da-f]{2}\.[0-7])", value.lower())
    return f"{int(match[1], 16):04x}:{match[2]}" if match else ""


def _encoder_owners(cards: list[GPU]) -> None:
    """Refresh counts and per-session PIDs together; failed queries cannot authorize NVENC from stale SMI counts."""
    for card in cards:
        card.sessions = None
        card.encoder_pids.clear()
    if not cards:
        return
    # nvmlEncoderSessionInfo_t contains eight 32-bit fields; the second is the host PID.
    session = ctypes.c_uint * 8
    try:
        nvml = ctypes.CDLL("libnvidia-ml.so.1")
        get_sessions = nvml.nvmlDeviceGetEncoderSessions
        if nvml.nvmlInit_v2():
            return
    except (OSError, AttributeError):
        return
    try:
        for card in cards:
            handle = ctypes.c_void_p()
            if nvml.nvmlDeviceGetHandleByUUID(card.uuid.encode("ascii"), ctypes.byref(handle)):
                continue
            size = ctypes.c_uint(NVENC_SESSION_LIMIT)
            rows = (session * size.value)()
            # More sessions than this buffer already exhaust the host budget; do not retry a growing list.
            if get_sessions(handle, ctypes.byref(size), rows) == 0:
                card.sessions = size.value
                card.encoder_pids.update(row[1] for row in rows[:size.value])
    finally:
        nvml.nvmlShutdown()


def _smi(*args: str, timeout: float = 2) -> str:
    """Bound driver queries, including the wait after SIGKILL if a broken device traps the CLI in kernel I/O."""
    try:
        process = subprocess.Popen(["nvidia-smi", *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return ""
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        output = exc.output or b""
        process.stdout.close()
        try:
            process.wait(timeout=.1)
        except subprocess.TimeoutExpired:
            # Popen registers unreaped children for cleanup on later launches when the driver releases them.
            pass
    return output.decode(errors="replace")


def _read_cards(xml: str, indexes: dict[str, int]) -> list[GPU]:
    """Parse a full or per-board XML observation; exposed DRM nodes must belong to the physical PCI device."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    cards = []
    for node in root.findall("gpu"):
        uuid = node.findtext("uuid", "")
        pci = _pci_name(node.findtext("pci/pci_bus_id") or node.get("id", ""))
        minor = _number(node.findtext("minor_number"))
        total = _number(node.findtext("fb_memory_usage/total"))
        free = _number(node.findtext("fb_memory_usage/free"))
        sessions = _number(node.findtext("encoder_stats/session_count"))
        memory = {}
        for proc in node.findall("processes/process_info"):
            pid, used = _number(proc.findtext("pid")), _number(proc.findtext("used_memory"))
            if pid and used is not None:
                memory[pid] = max(used, memory.get(pid, 0))
        drm = []
        if pci and minor is not None and Path(f"/dev/nvidia{minor}").is_char_device() and Path("/dev/nvidiactl").is_char_device():
            for entry in (Path("/sys/bus/pci/devices") / pci / "drm").glob("*"):
                if re.fullmatch(r"renderD\d+|card\d+", entry.name) and (Path("/dev/dri") / entry.name).is_char_device():
                    drm.append(entry.name)
        cards.append(GPU(uuid, indexes.get(uuid, -1), minor if minor is not None else -1, pci, total or 0,
                         (total - free) if total is not None and free is not None else total or 0,
                         sessions, memory, tuple(sorted(drm)), Path("/dev/nvidia-uvm").is_char_device()))
    return cards


def _is_excluded(index: int, uuid: str) -> bool:
    """Skip physical GPU 4 and any boards pinned by UUID across host reboots."""
    return index == 4 or any(uuid.casefold() == blocked.casefold() for blocked in EXCLUDED_GPU_UUIDS)


def _inventory() -> tuple[list[GPU], bool]:
    """Prefer a complete host snapshot; if a failed board blocks it, retain healthy graphics with an unknown session budget."""
    if sys.platform != "linux" or not shutil.which("nvidia-smi"):
        return [], False
    indexes = {}
    for row in _smi("--query-gpu=index,uuid", "--format=csv,noheader,nounits", timeout=1.5).splitlines():
        index, _, uuid = row.partition(",")
        if index.strip().isdigit() and GPU_UUID.fullmatch(uuid.strip()):
            indexes[uuid.strip()] = int(index)
    def merge_listed_indexes():
        """Some drivers keep returning healthy boards via compact -L after a cross-device query fails."""
        for row in _smi("-L", timeout=1.5).splitlines():
            match = re.match(r"GPU (\d+): .*\(UUID: (GPU-[\da-fA-F-]+)\)", row)
            if match and GPU_UUID.fullmatch(match[2]):
                indexes[match[2]] = int(match[1])
    tried_list = not indexes
    if tried_list:
        merge_listed_indexes()
    if not indexes:
        return [], False
    xml = _smi("-q", "-x", timeout=8)
    cards = _read_cards(xml, indexes)
    found = {card.uuid for card in cards if card.total > 0}
    required = {uuid for uuid, index in indexes.items() if index != 4}
    counted = [card for card in cards if card.index != 4]
    consecutive = set(indexes.values()) == set(range(len(indexes)))
    attached = re.search(r"<attached_gpus>\s*(\d+)\s*</attached_gpus>", xml)
    if (attached and int(attached[1]) == len(indexes) and len(counted) == len(required) and required <= found and
            consecutive and all(card.sessions is not None for card in counted)):
        return cards, True
    if not tried_list:
        merge_listed_indexes()
        for card in cards:
            card.index = indexes.get(card.uuid, -1)
    missing = [uuid for uuid, index in indexes.items() if uuid not in found and not _is_excluded(index, uuid)]
    if missing:
        # All per-card queries share one deadline. Queued boards do not each get another full timeout.
        deadline = time.monotonic() + 2
        def read_one(uuid: str) -> list[GPU]:
            """Only accept the physical UUID requested, even when an older driver returns unrelated XML."""
            left = deadline - time.monotonic()
            if left <= 0:
                return []
            return [card for card in _read_cards(_smi("-i", uuid, "-q", "-x", timeout=left), indexes) if card.uuid == uuid]
        with ThreadPoolExecutor(max_workers=min(8, len(missing))) as pool:
            healthy = {card.uuid: card for group in pool.map(read_one, missing) for card in group}
        cards = [card for card in cards if card.uuid not in healthy] + list(healthy.values())
    return cards, False


@cache
def _nvenc_unavailable_reason(ffmpeg: str | None) -> str | None:
    """Cache the installed FFmpeg build's encoder and filter support for paired overlays."""
    if ffmpeg is None:
        return "ffmpeg_unavailable"
    for option, required in (
        ("-encoders", {"h264_nvenc"}),
        ("-filters", {"format", "split", "scale", "alphaextract", "setparams"}),
    ):
        listing = subprocess.check_output([ffmpeg, "-hide_banner", option], stderr=subprocess.DEVNULL,
                                          timeout=3, text=True, errors="replace")
        # In both FFmpeg tables, the second column is the exact encoder or filter name.
        if not required.issubset(re.findall(r"^[ \t]*\S+[ \t]+(\S+)", listing, re.MULTILINE)):
            return "nvenc_pipeline_unavailable"
    return None


def _probe_host() -> tuple[list[GPU], bool, str | None]:
    """Probe hardware and FFmpeg together before locking leases. A failed command can be retried on the next claim."""
    cards, complete = _inventory()
    # Deployment policy: physical GPU 4 runs no workloads, including external encoders.
    cards = [card for card in cards if card.index != 4]
    try:
        reason = _nvenc_unavailable_reason(shutil.which("ffmpeg")) if cards and complete else None
    except (OSError, subprocess.SubprocessError):
        reason = "nvenc_pipeline_unavailable"
    if cards and complete and reason is None:
        _encoder_owners(cards)
    return cards, complete, reason


def _process(pid: int) -> tuple[int, int] | None:
    """Read a host PID's parent and birth tick, tolerating an exit during the GPU snapshot."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
        return int(fields[1]), int(fields[19])
    except (OSError, IndexError, ValueError):
        return None


def _accounted(card: GPU, leases: list[dict]) -> tuple[int, int]:
    """Add only the unmaterialized portion of each worker's VRAM/session reservations to the driver observation."""
    roots = {tuple(lease["worker"]): i for i, lease in enumerate(leases) if lease["worker"]}
    committed_memory, committed_sessions = Counter(), Counter()
    for pid in (card.memory_by_pid.keys() | card.encoder_pids.keys()) if roots else ():
        current = pid
        while current:
            stat = _process(current)
            if stat is None:
                break
            owner = roots.get((current, stat[1]))
            if owner is not None:
                committed_memory[owner] += card.memory_by_pid.get(pid, 0)
                committed_sessions[owner] += card.encoder_pids.get(pid, 0)
                break
            current = stat[0]
    memory = card.used + sum(max(0, lease["mib"] - committed_memory[i]) for i, lease in enumerate(leases))
    sessions = (card.sessions or 0) + sum(max(0, lease["sessions"] - committed_sessions[i]) for i, lease in enumerate(leases))
    return memory, sessions


@dataclass
class GPUGrant:
    """Chosen route and an optional locked lease kept until the sandbox process tree is reaped."""

    route: Route
    uuid: str | None
    chunks: int
    reason: str
    device: GPU | None = None
    lease: IO[str] | None = None

    def attach_worker(self, pid: int) -> None:
        """Publish the real sandbox ancestor so subsequent claimants can subtract already-observed usage."""
        stat = _process(pid)
        if self.lease is None or stat is None:
            return
        # The base lease remains immutable. An atomic rename publishes PID and birth tick together.
        target = Path(self.lease.name).with_suffix(".pid")
        temp = target.with_suffix(".pid-new")
        temp.write_text(f"{pid} {stat[1]}")
        temp.replace(target)

    def release(self) -> None:
        """Unlock immediately; even a crashed service loses its lease when the kernel closes the descriptor."""
        if self.lease is not None:
            self.lease.close()


class GPUPool:
    """Select and reserve a single GPU atomically across participating processes on the Linux host."""

    def __init__(self, settings: Settings):
        """Use the local lease directory shared by this host's renderer processes."""
        self.settings = settings
        self.directory = settings.overlay_gpu_lease_dir
        self._probe = None
        if self.directory is None and sys.platform == "linux":
            self.directory = Path(tempfile.gettempdir()) / f"imv-overlay-gpu-{os.getuid()}"

    @contextmanager
    def _lock(self):
        """Serialize lease creation and cleanup across service processes; GPU discovery happens before taking the lock."""
        import fcntl

        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.settings.overlay_gpu_lease_dir is None:
            # The default name lives in shared /tmp. Reject a precreated symlink or another account's writable ledger.
            info = self.directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise PermissionError("untrusted GPU lease directory")
        with (self.directory / "pool.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _leases(self) -> list[dict]:
        """Read locked grants and discard automatically unlocked leases from completed, cancelled or crashed jobs."""
        import fcntl

        active = []
        for path in self.directory.glob("*.lease"):
            with path.open() as file:
                try:
                    fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    item = json.load(file)
                    try:
                        item["worker"] = list(map(int, path.with_suffix(".pid").read_text().split()))
                    except FileNotFoundError:
                        item["worker"] = None
                    active.append(item)
                else:
                    path.unlink()
                    path.with_suffix(".pid").unlink(missing_ok=True)
        return active

    def failed(self, uuid: str | None, kind: Literal["graphics", "encoder"]) -> None:
        """Cool down the affected capability for one minute from a confirmed failure, across all local workers."""
        if self.directory is None or not uuid or not GPU_UUID.fullmatch(uuid) or kind not in ("graphics", "encoder"):
            return
        try:
            with self._lock():
                (self.directory / f"{uuid}.{kind}").touch()
        except OSError:
            # The caller also skips this UUID in the current task when the lease directory becomes inaccessible.
            pass

    def _choose(self, cards: list[GPU], width: int, height: int, pages: int, chunks: int,
                skip_graphics: set[str], skip_nvenc: set[str], complete: bool, ffmpeg_issue: str | None) -> GPUGrant:
        """Prefer color/mask on the least session-loaded eligible card, then GPU/WebM and CPU/WebM."""
        import fcntl

        leases = self._leases()
        now = time.time()
        options = []
        host_sessions = 0
        for card in cards:
            used, sessions = _accounted(card, [lease for lease in leases if lease["uuid"] == card.uuid])
            host_sessions += sessions
            if (not GPU_UUID.fullmatch(card.uuid) or card.index < 0 or _is_excluded(card.index, card.uuid) or
                    not card.total or not any(name.startswith("renderD") for name in card.drm)):
                continue
            cooldown = {kind for kind, skipped in (("graphics", skip_graphics), ("encoder", skip_nvenc)) if card.uuid in skipped}
            for kind in ("graphics", "encoder"):
                try:
                    if now - (self.directory / f"{card.uuid}.{kind}").stat().st_mtime < COOLDOWN_SECONDS:
                        cooldown.add(kind)
                except FileNotFoundError:
                    pass
            if "graphics" not in cooldown:
                options.append((sessions, -(card.total * 85 // 100 - used), card.index, card, cooldown))
        # A card absent from this observation can still have an active reservation in this host.
        present = {card.uuid for card in cards}
        host_sessions += sum(lease["sessions"] for lease in leases if lease["uuid"] not in present)
        free_sessions = NVENC_SESSION_LIMIT - host_sessions
        supported_dimensions = width % 2 == 0 and height % 2 == 0 and "*" not in skip_nvenc
        sessions_known = complete and all(card.sessions is not None for card in cards)
        if not sessions_known or ffmpeg_issue:
            free_sessions = 0
        scale = max(1.0, width * height / (1080 * 1920)) * max(1.0, pages / 4)
        for route, base in (("h264-pair", self.settings.overlay_gpu_pair_mb), ("gpu-webm", self.settings.overlay_gpu_webm_mb)):
            unit = math.ceil(base * scale)
            for _, neg_available, _, card, cooldown in sorted(options, key=lambda item: item[:3]):
                count = min(chunks, 2, -neg_available // unit)
                if route == "h264-pair":
                    count = min(count, free_sessions // 2)
                    if not supported_dimensions or "encoder" in cooldown or not card.cuda:
                        continue
                if count < 1:
                    continue
                lease = tempfile.NamedTemporaryFile(mode="w+", dir=self.directory, suffix=".lease", delete=False)
                try:
                    fcntl.flock(lease, fcntl.LOCK_EX)
                    json.dump({"uuid": card.uuid, "sessions": count * 2 if route == "h264-pair" else 0,
                               "mib": unit * count}, lease)
                    lease.flush()
                except BaseException:
                    lease.close()
                    raise
                reason = "nvenc_admitted"
                if route == "gpu-webm":
                    if ffmpeg_issue:
                        reason = ffmpeg_issue
                    elif not supported_dimensions:
                        reason = "nvenc_dimensions_unsupported"
                    elif not sessions_known:
                        reason = "nvenc_sessions_unknown"
                    elif free_sessions < 2:
                        reason = "nvenc_sessions_unavailable"
                    elif not card.cuda or "encoder" in cooldown:
                        reason = "nvenc_device_unavailable"
                    else:
                        reason = "nvenc_memory_unavailable"
                return GPUGrant(route, card.uuid, count, reason, card, lease)
        return GPUGrant("cpu-webm", None, chunks, "gpu_memory_unavailable" if options else "gpu_device_unavailable")

    @asynccontextmanager
    async def claim(self, width: int, height: int, pages: int, chunks: int, *, skip_graphics: set[str], skip_nvenc: set[str]):
        """Reserve from a fresh snapshot. A '*' in skip_nvenc means this job's canvas was rejected by the encoder."""
        if self._probe is None or self._probe.done():
            self._probe = asyncio.create_task(asyncio.to_thread(_probe_host))
        cards, complete, ffmpeg_issue = await asyncio.shield(self._probe)
        grant = GPUGrant("cpu-webm", None, chunks, "nvidia_unavailable")
        if cards and self.directory is not None:
            try:
                with self._lock():
                    grant = self._choose(cards, width, height, pages, chunks, skip_graphics, skip_nvenc, complete, ffmpeg_issue)
            except (OSError, ValueError):
                grant = GPUGrant("cpu-webm", None, chunks, "gpu_accounting_unavailable")
        try:
            yield grant
        finally:
            grant.release()


def sandbox_gpu(grant: GPUGrant) -> tuple[list[str], list[str]]:
    """Expose one GPU's char devices and PCI/DRM links in the isolated worker; never mount all of /dev, /sys or /proc."""
    card = grant.device
    if card is None or grant.uuid != card.uuid:
        raise GPUWorkerError("graphics", "gpu_selection_missing")
    dev = Path(f"/dev/nvidia{card.minor}")
    pci_link = Path("/sys/bus/pci/devices") / card.pci
    if not dev.is_char_device() or not pci_link.is_dir() or not Path("/dev/nvidiactl").is_char_device():
        raise GPUWorkerError("graphics", "gpu_device_unavailable")
    if grant.route == "h264-pair" and not Path("/dev/nvidia-uvm").is_char_device():
        raise GPUWorkerError("encoder", "gpu_cuda_device_unavailable")
    physical = pci_link.resolve()
    mounts = ["--ro-bind", str(physical), str(physical), "--symlink", str(physical), str(pci_link)]
    devices = [dev, Path("/dev/nvidiactl"), Path("/dev/nvidia-modeset"), Path("/dev/nvidia-uvm")]
    for name in card.drm:
        link = Path("/sys/class/drm") / name
        if (link / "device").resolve() != physical or not (Path("/dev/dri") / name).is_char_device():
            raise GPUWorkerError("graphics", "gpu_drm_device_changed")
        mounts.extend(("--symlink", str(physical / "drm" / name), str(link)))
        devices.append(Path("/dev/dri") / name)
    for node in devices:
        if not node.is_char_device():
            continue
        mounts.extend(("--dev-bind", str(node), str(node)))
        rdev = node.stat().st_rdev
        key = f"{os.major(rdev)}:{os.minor(rdev)}"
        syspath = Path("/sys/dev/char") / key
        if syspath.exists():
            real = syspath.resolve()
            if not real.is_relative_to(physical):
                mounts.extend(("--ro-bind", str(real), str(real)))
            mounts.extend(("--symlink", str(real), str(syspath)))
        udev = Path("/run/udev/data") / ("c" + key)
        if udev.is_file():
            mounts.extend(("--ro-bind", str(udev), str(udev)))
    for path in ("/sys/module/nvidia", "/sys/module/nvidia_drm", "/sys/module/nvidia_uvm/initstate"):
        if Path(path).exists():
            mounts.extend(("--ro-bind", path, path))
    environment = ["--setenv", "CUDA_VISIBLE_DEVICES", card.uuid]
    for variable, manifest in (("__EGL_VENDOR_LIBRARY_FILENAMES", "/usr/share/glvnd/egl_vendor.d/10_nvidia.json"),
                               ("VK_DRIVER_FILES", "/usr/share/vulkan/icd.d/nvidia_icd.json")):
        if Path(manifest).is_file():
            environment.extend(("--setenv", variable, manifest))
    return mounts, environment
