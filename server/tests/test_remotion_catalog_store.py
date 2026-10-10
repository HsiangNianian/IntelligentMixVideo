"""Preset 本地事务、数据库读失败和跨进程互斥回归；全部使用临时目录与隔离数据库边界。"""

import subprocess
import sys
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier, Event
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
        parameter_schema={"type": "object", "properties": {}, "additionalProperties": False}, default_parameters={},
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


def test_waiting_for_a_catalog_lock_has_a_deadline(tmp_path):
    """持锁进程长期不释放时，另一个进程明确超时，不永久占用工作线程。"""
    script = "from server.file_lock import lock_exclusive; import sys; f=open(sys.argv[1], 'a+b'); lock_exclusive(f, blocking=True, timeout=0.1)"
    path = tmp_path / "catalog.lock"
    with path.open("a+b") as lock:
        lock_exclusive(lock)
        result = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True, text=True, timeout=5)
        assert result.returncode != 0
        assert "TimeoutError: Timed out waiting" in result.stderr


@pytest.mark.parametrize("operation", ["search", "modify", "create"])
def test_catalog_waits_leave_the_event_loop_responsive(tmp_path, monkeypatch, operation):
    """真实工具调用等待目录读写时，事件循环仍能处理取消和其他请求。"""
    from server.remotion_templates.tools.contracts import CodeValidationReport
    from server.remotion_templates.tools.registry import get_tool
    from server.remotion_templates.tools.session import ToolSession

    async def scenario():
        """用线程事件暂停存储边界，确认循环在存储释放之前仍能继续调度。"""
        loop = asyncio.get_running_loop()
        entered, release = asyncio.Event(), Event()
        harness = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path), renderer=None)
        session = ToolSession(harness, None, None, None, tmp_path, [], lambda *_: None, {})
        stored = record("source")

        def blocked(*_args):
            """模拟慢数据库或文件锁；上界保证测试失败时也能退出。"""
            loop.call_soon_threadsafe(entered.set)
            release.wait(timeout=2)
            return [stored] if operation == "search" else stored if operation == "modify" else "local"

        async def validate(_component):
            """只隔离渲染器，本例验证实际创建工具的持久化调度。"""
            return CodeValidationReport(passed=True, diagnostics=[])

        if operation == "search":
            monkeypatch.setattr(session.catalog, "read_presets", blocked)
            arguments = {}
        elif operation == "modify":
            monkeypatch.setattr(session.catalog, "find_preset", blocked)
            arguments = {"preset_id": stored.preset_id, "changes": {"description": "updated"}}
        else:
            monkeypatch.setattr(session.catalog, "append_preset", blocked)
            monkeypatch.setattr(session, "validate_code", validate)
            arguments = stored.model_dump(mode="json", exclude={"preset_id", "created_at"}, exclude_unset=True)
        task = asyncio.create_task(get_tool(f"preset.{operation}").invoke(session, arguments))
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            assert not task.done(), "Catalog I/O blocked the event loop until completion"
            if operation == "search":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=1)
            else:
                release.set()
                result = await asyncio.wait_for(task, timeout=1)
                assert result["ok"] is True
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
