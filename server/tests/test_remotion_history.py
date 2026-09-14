"""Verify durable public histories and compatible APIs offline: uv run --locked pytest tests/test_remotion_history.py."""

import json
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr
from server.remotion_templates import history
from server.remotion_templates.models import GenerateTemplateRequest, JobError, JobInput
from server.remotion_templates.routes import router
from server.remotion_templates.runtime import Runtime
from server.remotion_templates.store import Conflict, Store
from server.settings import Settings


@pytest.fixture
def history_store(tmp_path):
    """Use a fresh local database, never the user's saved templates or model configuration."""
    store = Store(tmp_path / "history")
    store.initialize()
    return store


@pytest.fixture
def history_app(history_store):
    """Exercise actual routes with a queue that stays idle and no external model calls."""
    app = FastAPI()
    app.include_router(router)
    app.state.runtime = SimpleNamespace(
        store=history_store,
        settings=Settings(
            _env_file=None,
            actor_api_key=SecretStr("test-only"),
            actor_model="offline",
            vision_model="offline",
        ),
        notify=lambda: None,
    )
    return app


def test_public_history_tracks_inputs_and_hides_repairs(history_store):
    """Persist input, question and answer exactly once while keeping steer and provider diagnostics private."""
    store = history_store
    work, job = store.create(GenerateTemplateRequest(description="居中标题"))
    initial = store.session(work.id)
    assert [m.text for m in initial.messages] == ["居中标题"]
    assert initial.job.created_at == job.created_at
    store.claim()
    cursor = store.session(work.id).cursor
    store.update(job.id, attempts=3, stage="repairing")
    assert store.work_events(work.id, cursor) == []
    store.update(job.id, status="needs_input", questions=["标题写什么？"])
    runtime = Runtime(store, None, None)
    runtime.notify = lambda: None
    answer_job = runtime.retry(job.id, "春日快乐")
    store.update(
        answer_job.id,
        status="failed",
        error=JobError(code="model_error", message="private provider secret"),
    )
    retry_job = runtime.retry(answer_job.id)
    snapshot = store.session(work.id)
    assert [m.text for m in snapshot.messages if m.role == "user"] == [
        "居中标题",
        "春日快乐",
        "重试本次制作。",
    ]
    assert snapshot.job.id == retry_job.id
    public = snapshot.model_dump_json() + json.dumps(
        [e.model_dump(mode="json") for e in store.work_events(work.id, 0)]
    )
    for secret in [
        "private provider secret",
        "attempts",
        "repairing",
        "usage",
        "steer",
    ]:
        assert secret not in public
    assert len({m.id for m in snapshot.messages}) == len(snapshot.messages)


def test_history_and_state_rollback_together(history_store, monkeypatch):
    """An event write failure cannot leave a job, message or work committed without its matching records."""
    original = history.append_event

    def fail_after_write(*args, **kwargs):
        """Simulate a failure after SQLite has accepted an event but before transaction commit."""
        original(*args, **kwargs)
        raise OSError("disk full")

    monkeypatch.setattr(history, "append_event", fail_after_write)
    with pytest.raises(OSError):
        history_store.create(GenerateTemplateRequest(description="事务测试"))
    with history_store.connection() as db:
        for table in ["projects", "jobs", "events", "chat_messages", "work_events"]:
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    monkeypatch.setattr(history, "append_event", original)
    work, job = history_store.create(GenerateTemplateRequest(description="恢复写入"))
    before = history_store.session(work.id)
    monkeypatch.setattr(history, "append_event", fail_after_write)
    with pytest.raises(OSError):
        history_store.update(job.id, status="cancelled")
    assert history_store.session(work.id) == before
    assert history_store.job(job.id).status == "queued"


def test_legacy_backfill_is_factual_and_idempotent(history_store):
    """Reopen an old schema twice without losing works or exposing the bounded model context."""
    store = history_store
    work, job = store.create(GenerateTemplateRequest(description="旧作品"))
    store.update(job.id, status="needs_input", questions=["旧问题"])
    with store.connection() as db:
        db.execute(
            "INSERT INTO conversations VALUES (?, ?)",
            (str(work.id), '"private tool context"'),
        )
        db.execute("DROP TABLE chat_messages")
        db.execute("DROP TABLE work_events")
    store.initialize()
    restored = store.session(work.id)
    assert [m.text for m in restored.messages] == ["旧作品", "旧问题"]
    assert all(m.reconstructed for m in restored.messages)
    assert "private tool context" not in restored.model_dump_json()
    store.initialize()
    assert store.session(work.id) == restored
    assert store.project(work.id).request.description == "旧作品"


def test_snapshot_watermark_uses_one_read_transaction(history_store, monkeypatch):
    """A writer racing the snapshot is seen entirely through subsequent replay, never partly in history."""
    work, job = history_store.create(GenerateTemplateRequest(description="并发快照"))
    original = history.snapshot

    def concurrent_snapshot(db, *args):
        """Pin the read snapshot, then commit a concurrent terminal state on another connection."""
        db.execute("SELECT COUNT(*) FROM work_events").fetchone()
        history_store.update(job.id, status="cancelled")
        return original(db, *args)

    monkeypatch.setattr(history, "snapshot", concurrent_snapshot)
    snap = history_store.session(work.id)
    assert snap.job.status == "queued"
    assert len(snap.messages) == 1
    events = history_store.work_events(work.id, snap.cursor)
    assert [e.type for e in events] == ["job.updated", "message.created"]
    assert events[0].data["status"] == "cancelled"


def test_history_pagination_and_work_isolation(history_store):
    """Page recent works and older messages without duplicates, private records or cross-work leakage."""
    store = history_store
    works = [
        store.create(GenerateTemplateRequest(description=f"作品 {i}")) for i in range(3)
    ]
    work, job = works[0]
    store.update(job.id, status="cancelled")
    for i in range(5):
        job = store.enqueue(
            work.id, JobInput(mode="edit", instruction=f"修改 {i}"), None
        )
        store.update(job.id, status="cancelled")
    first = store.work_history(limit=2)
    second = store.work_history(first.next_cursor, limit=2)
    assert first.items[0].id == work.id
    assert len({w.id for w in first.items + second.items}) == 3
    assert second.next_cursor is None
    messages = []
    before = None
    while True:
        page = store.session(work.id, before, limit=3)
        messages = page.messages + messages
        if page.next_before is None:
            break
        before = page.next_before
    assert len(messages) == 12
    assert len({m.id for m in messages}) == 12
    assert [m.sequence for m in messages] == sorted(m.sequence for m in messages)
    assert messages[0].text == "作品 0"
    foreign = store.session(works[1][0].id).cursor
    with pytest.raises(Conflict):
        store.validate_cursor(work.id, foreign)
    assert all(e.work_id == work.id for e in store.work_events(work.id, 0))


def test_history_api_contracts_and_legacy_list(history_app):
    """History opt-in preserves the old list shape; malformed cursors and nonexistent works fail explicitly."""
    with TestClient(history_app) as client:
        assert client.get("/works?history=true").json() == {
            "items": [],
            "next_cursor": None,
        }
        created = client.post("/works", json={"description": "API 标题"})
        assert created.status_code == 202
        work_id = created.json()["work"]["id"]
        assert isinstance(client.get("/works").json(), list)
        page = client.get("/works?history=true&limit=1")
        assert page.status_code == 200
        assert page.json()["items"][0]["title"] == "API 标题"
        snap = client.get(f"/works/{work_id}/session")
        assert snap.status_code == 200
        assert snap.json()["messages"][0]["text"] == "API 标题"
        assert snap.json()["cursor"] > 0
        for query in ["limit=0", "limit=101", "cursor=%%%", "cursor=e30="]:
            assert client.get(f"/works?history=true&{query}").status_code == 422
        assert client.get(f"/works/{work_id}/session?before=0").status_code == 422
        assert client.get(f"/works/{uuid4()}/session").status_code == 404
        old = client.get(f"/jobs/{created.json()['job']['id']}/events").json()
        assert old["events"][0]["job"]["status"] == "queued"


def test_history_images_remain_readable_and_integrity_checked(
    history_app, history_store
):
    """Restore a normalized reference by ID, report missing/corrupt bytes, and never accept arbitrary paths."""
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
    with TestClient(history_app) as client:
        uploaded = client.post(
            "/assets", files={"file": ("reference.png", buffer.getvalue(), "image/png")}
        )
        assert uploaded.status_code == 201
        asset_id = uploaded.json()["id"]
        work = client.post("/works", json={"image": {"asset_id": asset_id}}).json()[
            "work"
        ]["id"]
        assert (
            client.get(f"/works/{work}/session").json()["messages"][0]["image_asset_id"]
            == asset_id
        )
        image = client.get(f"/assets/{asset_id}")
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/png"
        path = history_store.root / "assets" / f"{asset_id}.png"
        path.write_bytes(b"corrupted")
        assert client.get(f"/assets/{asset_id}").status_code == 409
        path.unlink()
        assert client.get(f"/assets/{asset_id}").status_code == 404
        assert client.get(f"/assets/{uuid4()}").status_code == 404
        assert client.get("/assets/not-a-uuid").status_code == 422


def test_sse_replays_all_pages_and_cleans_idle_wait(history_store):
    """Replay more than 100 committed events, preserve work isolation and cancel idle waits without cancelling work."""
    import asyncio

    from server.remotion_templates.stream import event_stream

    store = history_store
    work, job = store.create(GenerateTemplateRequest(description="长历史"))
    for _ in range(40):
        store.update(job.id, status="cancelled")
        job = store.enqueue(work.id, JobInput(), None)
    foreign, _ = store.create(GenerateTemplateRequest(description="其他会话"))
    expected = store.session(work.id).cursor

    async def scenario():
        """Bound all generator reads; a stream never owns the queued job lifecycle."""
        stream = event_stream(store, work.id, 0)
        assert "connected" in await anext(stream)
        identifiers = []
        async with asyncio.timeout(3):
            while not identifiers or identifiers[-1] != expected:
                frame = await anext(stream)
                data = json.loads(frame.split("data: ", 1)[1])
                assert data["work_id"] == str(work.id)
                assert str(foreign.id) not in frame
                identifiers.append(data["id"])
        assert len(identifiers) > 100
        assert identifiers == sorted(set(identifiers))
        wait = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.02)
        wait.cancel()
        with pytest.raises(asyncio.CancelledError):
            await wait
        await stream.aclose()
        assert store.job(job.id).status == "queued"

    asyncio.run(scenario())


def test_sse_heartbeat_without_public_activity(history_store, monkeypatch):
    """Idle connections send comments rather than fake message events or unbounded busy loops."""
    import asyncio

    from server.remotion_templates import stream as streaming

    work, _ = history_store.create(GenerateTemplateRequest(description="心跳"))
    ticks = iter([0.0, 16.0, 16.0])
    monkeypatch.setattr(streaming, "monotonic", lambda: next(ticks))

    async def scenario():
        """Consume only the welcome and first heartbeat and close immediately."""
        stream = streaming.event_stream(
            history_store, work.id, history_store.session(work.id).cursor
        )
        await anext(stream)
        assert await asyncio.wait_for(anext(stream), 1) == ": heartbeat\n\n"
        await stream.aclose()

    asyncio.run(scenario())


def test_sse_rejects_invalid_cursors_before_streaming(history_app, history_store):
    """Invalid, foreign and unavailable cursors return ordinary HTTP errors before SSE headers are sent."""
    work, _ = history_store.create(GenerateTemplateRequest(description="游标"))
    other, _ = history_store.create(GenerateTemplateRequest(description="其他"))
    with TestClient(history_app) as client:
        path = f"/works/{work.id}/stream"
        assert client.get(path + "?after=-1").status_code == 422
        assert client.get(path + "?after=999999999").status_code == 409
        assert client.get(path + "?after=" + "9" * 50).status_code == 422
        assert client.get(path, headers={"Last-Event-ID": "invalid"}).status_code == 422
        assert client.get(path, headers={"Last-Event-ID": "-1"}).status_code == 422
        assert (
            client.get(
                path,
                headers={"Last-Event-ID": str(history_store.session(other.id).cursor)},
            ).status_code
            == 409
        )
        assert client.get(f"/works/{uuid4()}/stream").status_code == 404


def test_sse_http_reconnect_and_followup_job(history_app, history_store):
    """A real HTTP client receives frames promptly, resumes from Last-Event-ID and sees another job on the same connection."""
    import socket
    import threading
    import time

    import httpx
    import uvicorn

    work, job = history_store.create(GenerateTemplateRequest(description="真实 HTTP"))
    cursor = history_store.session(work.id).cursor
    socket_ = socket.socket()
    socket_.bind(("127.0.0.1", 0))
    port = socket_.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(history_app, log_level="error", lifespan="off")
    )
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [socket_]}, daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 5
    try:
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        url = f"http://127.0.0.1:{port}/works/{work.id}/stream"
        with httpx.stream("GET", url, params={"after": cursor}, timeout=3) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["x-accel-buffering"] == "no"
            lines = response.iter_lines()
            assert next(lines) == "retry: 2000"
            history_store.claim()
            data = next(line[6:] for line in lines if line.startswith("data: "))
            event = json.loads(data)
            assert event["type"] == "job.updated"
            assert event["data"]["status"] == "running"
            cursor = event["id"]
        assert history_store.job(job.id).status == "running"
        history_store.update(job.id, status="cancelled")
        with httpx.stream(
            "GET",
            url,
            params={"after": 0},
            headers={"Last-Event-ID": str(cursor)},
            timeout=3,
        ) as response:
            lines = response.iter_lines()
            events = [json.loads(line[6:]) for line in _data_lines(lines, 2)]
            assert all(event["id"] > cursor for event in events)
            assert [event["type"] for event in events] == [
                "job.updated",
                "message.created",
            ]
            followup = history_store.enqueue(
                work.id, JobInput(mode="edit", instruction="继续"), None
            )
            events = [json.loads(line[6:]) for line in _data_lines(lines, 2)]
            assert events[0]["data"]["text"] == "继续"
            assert events[1]["data"]["id"] == str(followup.id)
        assert history_store.job(followup.id).status == "queued"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        socket_.close()
        assert not thread.is_alive()


def _data_lines(lines, count):
    """Take a fixed number of SSE data lines; the HTTP read timeout bounds absence of progress."""
    found = 0
    for line in lines:
        if line.startswith("data: "):
            yield line
            found += 1
            if found == count:
                return
    raise AssertionError("SSE connection closed before the expected events")
