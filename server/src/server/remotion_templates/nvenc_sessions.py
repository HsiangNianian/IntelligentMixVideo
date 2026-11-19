"""Read NVML encoder owner PIDs in a disposable process so a stalled driver cannot hold up overlay admission."""

import ctypes
import json
import sys


def session_pids(uuids: list[str], capacity: int) -> dict[str, list[int]]:
    """Return one PID per session, including repeats. Omit a board when NVML cannot supply its full list."""
    try:
        nvml = ctypes.CDLL("libnvidia-ml.so.1")
        get_handle = nvml.nvmlDeviceGetHandleByUUID
        get_sessions = nvml.nvmlDeviceGetEncoderSessions
        shutdown = nvml.nvmlShutdown
        if nvml.nvmlInit_v2():
            return {}
    except (OSError, AttributeError):
        return {}

    # nvmlEncoderSessionInfo_t contains eight 32-bit fields. The second field is the host PID.
    session = ctypes.c_uint * 8
    owners = {}
    try:
        for uuid in uuids:
            handle = ctypes.c_void_p()
            if get_handle(uuid.encode("ascii"), ctypes.byref(handle)):
                continue
            size = ctypes.c_uint(capacity)
            rows = (session * capacity)()
            if get_sessions(handle, ctypes.byref(size), rows) == 0 and size.value <= capacity:
                owners[uuid] = [row[1] for row in rows[:size.value]]
    finally:
        shutdown()
    return owners


if __name__ == "__main__":
    print(json.dumps(session_pids(sys.argv[2:], int(sys.argv[1]))))
