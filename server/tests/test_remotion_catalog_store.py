"""Preset 本地事务、数据库读失败和跨进程互斥回归；全部使用临时目录与隔离数据库边界。"""

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import SQLAlchemyError

from server.file_lock import lock_exclusive
from server.remotion_templates.tools.catalog_store import CatalogStore
from server.remotion_templates.tools.contracts import PresetRecord


def record(identifier):
    """构造不依赖模型、数据库或渲染器的合法目录记录。"""
    return PresetRecord(
        preset_id=identifier, created_at="2026-10-09T00:00:00+00:00", description=identifier,
        code="export default function C(){return null}",
        parameter_schema={"type": "object", "properties": {}}, default_parameters={},
    )


def test_local_append_waits_for_another_process_lock(tmp_path):
    """另一个进程持锁时必须等待；释放后追加成功且读取原有记录。"""
    store = CatalogStore(tmp_path)
    store._append_local(record("first"))
    script = """
import sys
from pathlib import Path
from server.remotion_templates.tools.catalog_store import CatalogStore
from server.remotion_templates.tools.contracts import PresetRecord
store = CatalogStore(Path(sys.argv[1]))
print('ready', flush=True)
value = PresetRecord.model_validate_json(sys.stdin.readline())
store._append_local(value)
"""
    process = None
    try:
        with (tmp_path / ".catalog.lock").open("a+b") as lock:
            lock_exclusive(lock)
            process = subprocess.Popen([sys.executable, "-c", script, str(tmp_path)],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            # communicate 的超时同时覆盖子进程导入与争锁，不使用无界 readline。
            with pytest.raises(subprocess.TimeoutExpired) as blocked:
                process.communicate(record("second").model_dump_json(exclude_unset=True) + "\n", timeout=1)
            assert b"ready" in (blocked.value.output or b"")
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert "ready" in stdout
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
    assert [item.preset_id for item in store._read_records("presets", PresetRecord)] == ["first", "second"]


def test_concurrent_fallback_creators_preserve_every_record(tmp_path, monkeypatch):
    """多个 Store 实例并发回退写入，成功返回的每个 ID 都必须可读取。"""
    def unavailable(self):
        """强制所有创建走共享本地目录，不连接真实数据库。"""
        raise SQLAlchemyError("offline")

    monkeypatch.setattr(CatalogStore, "_engine", unavailable)
    barrier = Barrier(4)

    def append(index):
        """让四个调用同时进入创建路径，检验锁覆盖整个读改写事务。"""
        barrier.wait(timeout=5)
        return CatalogStore(tmp_path).append_preset(record(f"preset-{index}"))

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(append, range(12))) == ["local"] * 12
    store = CatalogStore(tmp_path)
    assert {item.preset_id for item in store.read_presets()} == {f"preset-{i}" for i in range(12)}
    assert list(tmp_path.glob("*.tmp")) == []


def test_failed_atomic_replace_keeps_catalog_and_releases_lock(tmp_path, monkeypatch):
    """替换失败不能改写原记录；临时文件清理后后续写入仍可成功。"""
    store = CatalogStore(tmp_path)
    store._append_local(record("first"))
    replace = Path.replace
    temporary_names = []

    def fail_replace(source, destination):
        """截获实际临时文件，验证每次写入使用独立路径。"""
        temporary_names.append(source.name)
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    for _ in range(2):
        with pytest.raises(OSError, match="replace failed"):
            store._append_local(record("second"))
    assert len(set(temporary_names)) == 2
    assert list(tmp_path.glob("*.tmp")) == []
    assert [item.preset_id for item in store._read_records("presets", PresetRecord)] == ["first"]
    monkeypatch.setattr(Path, "replace", replace)
    store._append_local(record("second"))
    assert len(store._read_records("presets", PresetRecord)) == 2


@pytest.mark.parametrize("failure", ["connect", "query", "iterate"])
def test_database_read_failure_falls_back_to_local_records(tmp_path, monkeypatch, failure):
    """engine 已取得后，连接、查询或读取结果失败都不能隐藏本地已保存预设。"""
    store = CatalogStore(tmp_path)
    value = record("local")
    store._append_local(value)

    def rows():
        """模拟游标在读取过程中断开，不能使用不完整远端结果。"""
        yield SimpleNamespace(payload=record("partial-remote").model_dump(mode="json", exclude_unset=True))
        raise SQLAlchemyError("cursor disconnected")

    def execute(_statement):
        """模拟查询发送失败或延迟游标异常。"""
        if failure == "query":
            raise SQLAlchemyError("query failed")
        return rows()

    @contextmanager
    def connect():
        """不打开真实连接，仅注入目录读取边界故障。"""
        if failure == "connect":
            raise SQLAlchemyError("connect failed")
        yield SimpleNamespace(execute=execute)

    monkeypatch.setattr(store, "_engine", lambda: SimpleNamespace(connect=connect))
    assert store.read_presets() == [value]
    assert store.find_preset(value.preset_id) == value
