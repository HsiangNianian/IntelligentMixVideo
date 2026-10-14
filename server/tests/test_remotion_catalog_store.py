"""Preset 目录只存 MySQL：读写往返、数据库故障明确上报，以及目录读写不阻塞事件循环。

使用 conftest 的临时 SQLite 替身代替 MySQL，不连接真实数据库；在 server/ 目录执行
`uv run --locked pytest tests/test_remotion_catalog_store.py -v`。
"""

import asyncio
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

# Importing the catalog registers the preset tools that get_tool resolves.
from server.remotion_templates.tools import catalog  # noqa: F401
from server.remotion_templates.tools.catalog_store import CatalogStore
from server.remotion_templates.tools.contracts import CodeValidationReport, PresetRecord
from server.remotion_templates.tools.registry import get_tool
from server.remotion_templates.tools.session import ToolSession


def record(identifier):
    """构造不依赖模型、数据库或渲染器的合法目录记录。"""
    return PresetRecord(
        preset_id=identifier, created_at="2026-10-09T00:00:00+00:00", description=identifier,
        code="export default function C(){return null}",
        parameter_schema={"type": "object", "properties": {}, "additionalProperties": False}, default_parameters={},
    )


def session_for(tmp_path):
    """不连接模型或渲染器的工具会话。"""
    harness = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path), renderer=None)
    return ToolSession(harness, None, None, None, tmp_path, [], lambda *_: None, {})


def test_presets_round_trip_through_the_shared_database(template_db, tmp_path):
    """追加的记录由任何会话按 ID 顺序读回、按 ID 精确查到；数据目录里不再写 catalog/ 文件。"""
    store = CatalogStore()
    assert store.append_preset(record("b")) == "mysql"
    assert store.append_preset(record("a")) == "mysql"
    assert [item.preset_id for item in CatalogStore().read_presets()] == ["a", "b"]
    assert CatalogStore().find_preset("b") == record("b")
    assert CatalogStore().find_preset("missing") is None
    assert not (tmp_path / "catalog").exists()


def test_database_failures_surface_as_preset_store_failed(tmp_path, monkeypatch):
    """数据库不可用时不回退到本地目录：创建与搜索都以 PRESET_STORE_FAILED 失败，不返回成功回执。"""
    def unavailable(self):
        """模拟连接失败的 MySQL。"""
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(CatalogStore, "_engine", unavailable)
    session = session_for(tmp_path)

    async def validate(_component):
        """只隔离渲染器，本例验证创建工具的持久化失败。"""
        return CodeValidationReport(passed=True, diagnostics=[])

    monkeypatch.setattr(session, "validate_code", validate)
    arguments = record("x").model_dump(mode="json", exclude={"preset_id", "created_at"}, exclude_unset=True)
    created = asyncio.run(get_tool("preset.create").invoke(session, arguments))
    searched = asyncio.run(get_tool("preset.search").invoke(session, {}))
    assert created["ok"] is False and created["error"]["code"] == "PRESET_STORE_FAILED"
    assert searched["ok"] is False and searched["error"]["code"] == "PRESET_STORE_FAILED"
    assert not (tmp_path / "catalog").exists()


@pytest.mark.parametrize("operation", ["search", "modify", "create"])
def test_catalog_waits_leave_the_event_loop_responsive(tmp_path, monkeypatch, operation):
    """真实工具调用等待目录读写时，事件循环仍能处理取消和其他请求。"""
    async def scenario():
        """用线程事件暂停存储边界，确认循环在存储释放之前仍能继续调度。"""
        loop = asyncio.get_running_loop()
        entered, release = asyncio.Event(), Event()
        session = session_for(tmp_path)
        stored = record("source")

        def blocked(*_args):
            """模拟慢数据库；上界保证测试失败时也能退出。"""
            loop.call_soon_threadsafe(entered.set)
            release.wait(timeout=2)
            return [stored] if operation == "search" else stored if operation == "modify" else "mysql"

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
