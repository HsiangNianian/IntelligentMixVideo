"""Hardware-free tests for overlay GPU admission, cross-process leases and bubblewrap device boundaries."""

import sys

import pytest

from server.remotion_templates import gpu_resources as gpu

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux DRM and flock admission")


def device(index=0, total=4000, used=0, sessions=0, **kw):
    """An independent NVIDIA board; IDs, minor numbers and PCI locations need not agree with enumeration order."""
    return gpu.GPU(f"GPU-aaaaaaaa-bbbb-cccc-dddd-{index:012x}", index, index + 2, f"0000:{index+1:02x}:00.0",
                   total, used, sessions, {}, (f"card{index}", f"renderD{128+index}"), **{"cuda": True, **kw})


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
