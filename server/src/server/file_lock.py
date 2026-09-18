"""进程级非阻塞文件锁：Unix 使用 flock，Windows 锁定首字节；关闭文件即释放。"""

import os
from typing import IO


def lock_exclusive(file: IO) -> None:
    """保持锁在传入文件句柄的生命周期内；冲突抛出 OSError，不等待另一实例。"""
    if os.name == "nt":
        import msvcrt

        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
