"""AI 资产目录变化推送与索引唤醒：连接时和变更后通知，空闲时不重复索引。

只测进程内唤醒与事件帧，不连接数据库；发布、改名与删除触发唤醒见 test_remotion_sprite_publish.py。
在 server/ 目录执行 `uv run --locked pytest tests/test_remotion_sprite_events.py -v`。
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.responses import StreamingResponse

from server.remotion_templates import runtime, sprite_events, sprite_router

CHANGED = "event: catalog.changed\ndata: {}\n\n"
PING = "event: ping\ndata: {}\n\n"


def test_indexer_runs_once_then_waits_for_catalog_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """首次回填后空闲不查询；发布事件唤醒一次对账，取消时释放监听者。"""
    async def scenario() -> None:
        """用内存通知观察对账次数，不连接 MySQL、Chroma 或模型。"""
        loop = asyncio.get_running_loop()
        calls: asyncio.Queue[int] = asyncio.Queue()
        count = 0

        class FakeSearch:
            """Record each reconciliation without touching external services."""

            def reconcile(self) -> bool:
                """Notify the test loop after one full index pass."""
                nonlocal count
                count += 1
                loop.call_soon_threadsafe(calls.put_nowait, count)
                return True

        monkeypatch.setattr(runtime, "SpriteSearch", FakeSearch)
        task = asyncio.create_task(runtime.Runtime._index_sprites(SimpleNamespace(fenced=False)))
        try:
            async with asyncio.timeout(3):
                assert await calls.get() == 1
                await asyncio.sleep(0.05)
                assert calls.empty()
                sprite_events.changed()
                assert await calls.get() == 2
                await asyncio.sleep(0.05)
                assert calls.empty()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not sprite_events._watchers

    asyncio.run(scenario())


def test_indexer_retries_a_failed_pass_then_waits_for_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """失败时限时重试，成功后回到只等待资产变更的状态。"""
    async def scenario() -> None:
        """用内存回执模拟一次失败和一次成功，不访问外部服务。"""
        loop = asyncio.get_running_loop()
        calls: asyncio.Queue[int] = asyncio.Queue()
        count = 0

        class FakeSearch:
            """Fail once, then report a healthy index."""

            def reconcile(self) -> bool:
                """Record the pass and request one retry on the first call."""
                nonlocal count
                count += 1
                loop.call_soon_threadsafe(calls.put_nowait, count)
                return count > 1

        monkeypatch.setattr(runtime, "SpriteSearch", FakeSearch)
        monkeypatch.setattr(runtime, "INDEX_RETRY_SECONDS", 0.02)
        task = asyncio.create_task(runtime.Runtime._index_sprites(SimpleNamespace(fenced=False)))
        try:
            async with asyncio.timeout(3):
                assert await calls.get() == 1
                assert await calls.get() == 2
                await asyncio.sleep(0.06)
                assert calls.empty()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not sprite_events._watchers

    asyncio.run(scenario())


# 场景：连接即通知一次（重连后补读断线期间的变化），之后每次变化通知一次，其他线程提交的变化同样唤醒。
def test_stream_announces_on_connect_and_after_each_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """首帧设置重连间隔，随后是 catalog.changed；工作线程调用 changed 后再收到一条；关闭后不再登记等待者。"""
    monkeypatch.setattr(sprite_events, "PING_SECONDS", 30.0)

    async def scenario() -> None:
        """按顺序读取帧，每次读取都有时限。"""
        stream = sprite_events.stream()
        async with asyncio.timeout(3):
            assert await anext(stream) == "retry: 3000\n\n"
            assert await anext(stream) == CHANGED
            waiting = asyncio.create_task(anext(stream))
            await asyncio.sleep(0.02)
            assert not waiting.done()
            await asyncio.to_thread(sprite_events.changed)
            assert await waiting == CHANGED
        await stream.aclose()
        assert not sprite_events._watchers

    asyncio.run(scenario())


# 场景：客户端读取目录期间连续发生多次变化时合并为一次通知，读取之后的变化不会丢。
def test_bursts_coalesce_into_one_announcement(monkeypatch: pytest.MonkeyPatch) -> None:
    """通知发出前的多次变化只产生一条 catalog.changed；之后没有新变化时只有 ping。"""
    monkeypatch.setattr(sprite_events, "PING_SECONDS", 0.05)

    async def scenario() -> None:
        """在两次读取之间连续触发三次变化。"""
        stream = sprite_events.stream()
        async with asyncio.timeout(3):
            await anext(stream)
            assert await anext(stream) == CHANGED
            waiting = asyncio.create_task(anext(stream))
            await asyncio.sleep(0)
            for _ in range(3):
                sprite_events.changed()
            assert await waiting == CHANGED
            assert await anext(stream) == PING
        await stream.aclose()

    asyncio.run(scenario())


# 场景：空闲连接定期发送具名 ping（浏览器看不到注释行），客户端据此判断连接仍然有效。
def test_idle_stream_pings(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有变化时按间隔发送 ping，不重复发送 catalog.changed。"""
    monkeypatch.setattr(sprite_events, "PING_SECONDS", 0.02)

    async def scenario() -> None:
        """跳过开头两帧后连续读取两条 ping。"""
        stream = sprite_events.stream()
        async with asyncio.timeout(3):
            await anext(stream)
            await anext(stream)
            assert [await anext(stream), await anext(stream)] == [PING, PING]
        await stream.aclose()

    asyncio.run(scenario())


# 场景：路由返回不经缓存与代理缓冲的 SSE 响应，正文就是上面的事件流。
def test_route_streams_catalog_events() -> None:
    """响应类型为 text/event-stream，带 no-cache 与 X-Accel-Buffering: no，首帧来自事件流。"""

    async def scenario() -> None:
        """直接调用路由函数并读取首帧；无限流不经 TestClient。"""
        response = await sprite_router.catalog_stream()
        assert isinstance(response, StreamingResponse)
        assert response.media_type == "text/event-stream"
        assert response.headers["Cache-Control"] == "no-cache" and response.headers["X-Accel-Buffering"] == "no"
        assert await anext(response.body_iterator) == "retry: 3000\n\n"
        await response.body_iterator.aclose()

    asyncio.run(scenario())
    assert any(getattr(route, "path", None) == "/api/sprites/stream" for route in sprite_router.router.routes)
