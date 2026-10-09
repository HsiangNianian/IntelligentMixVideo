"""进程级文件锁：默认不等待，目录事务可等待；关闭句柄即释放。"""

import errno
import os
import time
from typing import IO


def lock_exclusive(file: IO, *, blocking: bool = False, timeout: float = 10.0) -> None:
    """锁随句柄释放；默认冲突即失败，等待模式在所有平台都有超时边界。"""
    if blocking:
        deadline = time.monotonic() + timeout
        while True:
            try:
                lock_exclusive(file)
                return
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Timed out waiting for the file lock") from exc
                time.sleep(min(0.05, remaining))
    if os.name == "nt":
        import msvcrt

        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
