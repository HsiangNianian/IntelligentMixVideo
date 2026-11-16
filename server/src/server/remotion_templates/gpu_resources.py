"""Linux overlay GPU admission and device isolation. Live NVIDIA usage and outstanding host leases share one budget."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import ctypes
from dataclasses import dataclass, field
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

GPU_UUID = re.compile(r"GPU-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.IGNORECASE)
EXCLUDED_GPU_UUIDS: tuple[str, ...] = ()


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
    """Read exact per-session PIDs, including two sessions owned by the same FFmpeg; failures keep reservations intact."""
    busy = [card for card in cards if card.sessions]
    if not busy:
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
        for card in busy:
            handle = ctypes.c_void_p()
            if nvml.nvmlDeviceGetHandleByUUID(card.uuid.encode("ascii"), ctypes.byref(handle)):
                continue
            size = ctypes.c_uint(card.sessions)
            rows = (session * size.value)()
            # A concurrent change in session count may require another buffer. Keep unmatched leases conservative.
            if get_sessions(handle, ctypes.byref(size), rows) == 0:
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
    xml = _smi("-q", "-x")
    cards = _read_cards(xml, indexes)
    found = {card.uuid for card in cards if card.total > 0}
    consecutive = set(indexes.values()) == set(range(len(indexes)))
    attached = re.search(r"<attached_gpus>\s*(\d+)\s*</attached_gpus>", xml)
    if (attached and int(attached[1]) == len(cards) == len(indexes) and found == indexes.keys() and
            consecutive and all(card.sessions is not None for card in cards)):
        _encoder_owners(cards)
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
