"""单实例的共享渲染预算：预留 CPU、内存，按块准入并在取消后释放。

CPU 亲和性限制子进程可用的核；内存是保守的准入估算，不替代部署层的 cgroup 硬上限。
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import math
import os
from pathlib import Path
from time import perf_counter

from .settings import Settings

MIB = 1024 ** 2


class RenderCapacityTimeout(RuntimeError):
    """资源暂不可用，调用方可重试；与渲染器代码或环境故障分别处理。"""


def cgroup_directories() -> list[Path]:
    """返回当前 cgroup v2 及祖先；父级的限制同样约束服务，未使用 v2 时为空。"""
    root = Path('/sys/fs/cgroup')
    try:
        relative = next(line[3:] for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::'))
    except (OSError, StopIteration):
        return []
    current = root / relative.lstrip('/')
    return [current, *[parent for parent in current.parents if parent == root or root in parent.parents]]


def renderer_cpus(reserve_percent: int) -> list[int]:
    """按有效亲和性和 cgroup CPU 配额选择核，至少留一核运行；单核机依赖外层 CPUQuota 留余量。"""
    cpus = sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else list(range(os.cpu_count() or 1))
    available = float(len(cpus))
    for directory in cgroup_directories():
        try:
            quota, period = (directory / 'cpu.max').read_text().split()
            if quota != 'max':
                available = min(available, int(quota) / int(period))
        except (OSError, ValueError, ZeroDivisionError):
            continue
    count = max(1, math.floor(available * (100 - reserve_percent) / 100))
    return cpus[:count]


def memory_headroom(reserve_percent: int) -> int:
    """分别在宿主和各级 cgroup 留出余量，取剩余可用量的最小值；读取失败不假定资源无限。"""
    values = {key: int(value.split()[0]) * 1024 for key, value in
              (line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())}
    available = values['MemAvailable'] - math.ceil(values['MemTotal'] * reserve_percent / 100)
    for directory in cgroup_directories():
        for name in ('memory.high', 'memory.max'):
            try:
                limit = (directory / name).read_text().strip()
                if limit != 'max':
                    bound = int(limit)
                    current = int((directory / 'memory.current').read_text())
                    available = min(available, bound - current - math.ceil(bound * reserve_percent / 100))
            except (OSError, ValueError):
                continue
    return max(0, available)


@dataclass(frozen=True)
class RenderGrant:
    """本次视频获准的并行块数与诊断；不改变帧区间、画面参数或编码质量。"""

    chunks: int
    pages: int
    wait_ms: float
    reserved_memory_mb: int


class RenderCapacity:
    """同一运行时共用资源预算；允许多视频各开六块，总量与余量不足时才降并发或等待。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.condition = asyncio.Condition()
        self.chunks = 0
        self.pages = 0
        self.memory_units = 0

    @asynccontextmanager
    async def claim(self, requested: int, pages: int):
        """一次预留整个视频的预算，覆盖排版、准备、渲染、校验；上传不占名额。"""
        started = perf_counter()
        granted = units = 0
        settings = self.settings
        try:
            async with asyncio.timeout(settings.overlay_resource_wait_seconds):
                async with self.condition:
                    while True:
                        cpus = renderer_cpus(settings.render_cpu_reserve_percent)[:settings.render_max_cpus]
                        actual_pages = min(pages, len(cpus))
                        available = await asyncio.to_thread(memory_headroom, settings.render_memory_reserve_percent)
                        available = min(available, settings.render_max_memory_mb * MIB)
                        # 一份额外预算留给打包、浏览器和排版；多页按页数预留，已有预留不重复分给新任务。
                        free_units = available // (settings.overlay_chunk_memory_mb * MIB) - self.memory_units - 1
                        granted = min(requested, settings.overlay_chunk_concurrency,
                                      settings.overlay_global_chunk_concurrency - self.chunks,
                                      (len(cpus) - self.pages) // actual_pages, free_units // actual_pages)
                        if granted > 0:
                            units = 1 + granted * actual_pages
                            self.chunks += granted
                            self.pages += granted * actual_pages
                            self.memory_units += units
                            break
                        try:
                            await asyncio.wait_for(self.condition.wait(), timeout=1)
                        except TimeoutError:
                            pass
        except TimeoutError as exc:
            raise RenderCapacityTimeout('渲染资源暂时不足，请稍后重试') from exc
        try:
            yield RenderGrant(granted, actual_pages, (perf_counter() - started) * 1000, units * settings.overlay_chunk_memory_mb)
        finally:
            async with self.condition:
                self.chunks -= granted
                self.pages -= granted * actual_pages
                self.memory_units -= units
                self.condition.notify_all()
