"""进程级文件锁：默认不等待，目录事务可等待；关闭句柄即释放。"""

import os
from typing import IO


def lock_exclusive(file: IO, *, blocking: bool = False) -> None:
    """锁随句柄释放；默认冲突即失败，blocking 等待写事务（Windows 最多约十秒）。"""
    if os.name == "nt":
        import msvcrt

        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(file, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
