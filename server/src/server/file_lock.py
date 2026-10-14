"""进程级文件锁：不等待，冲突即失败；关闭句柄即释放。"""

import os
from typing import IO


def lock_exclusive(file: IO) -> None:
    """取得锁随句柄释放；已被其他进程持有时抛出 OSError。"""
    if os.name == "nt":
        import msvcrt

        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
