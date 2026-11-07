"""Verify buffered public events, device ownership, delivery acknowledgement and SSE offline.

The Remotion backend keeps works, jobs and undelivered public events in MySQL (the `template_db` SQLite stand-in
here) and the client stores chat history locally. Run: uv run --locked pytest tests/test_remotion_history.py.
"""

import asyncio
import json
from datetime import timedelta
from functools import partial
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select, update
from server.remotion_templates import deletion, history
from server.remotion_templates import store as store_module
from server.remotion_templates.media import save_image
from server.remotion_templates.models import (
    DialogueOutput,
    GenerateTemplateRequest,
    JobError,
    JobInput,
    MessageImage,
    TaskMessage,
)
from server.remotion_templates.routes import router
from server.remotion_templates.runtime import REFERENCE_MEANINGS, Runtime, reference_images
from server.remotion_templates.store import NotFound, Store, asset_key, now
from server.remotion_templates.settings import Settings
from server.remotion_templates.tables import (
    assets,
    chat_messages,
    devices,
    job_progress,
    job_rounds,
    jobs,
    work_assets,
    work_events,
    works,
)
from .conftest import DEVICE, DEVICE_HEADERS

OTHER = uuid4()
OTHER_HEADERS = {"X-IMV-Device": str(OTHER)}


@pytest.fixture
def history_store(tmp_path, template_db):
    """Use the per-test SQLite stand-in for MySQL and local objects, never the user's data or models."""
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
        notify=lambda *args: None,
        sandbox_problem=lambda: None,
    )
    return app


def counts(store, *tables):
    """Row counts of the given tables."""
    with store.reading() as db:
        return [db.execute(select(func.count()).select_from(table)).scalar_one() for table in tables]


def public(store, job_id):
    """The public task view the client receives, including progress and rounds."""
    with store.reading() as db:
        return history.public_job(db, store.job(job_id))


def cursor(store, work_id):
    """The newest undelivered event ID of a work, or 0."""
    with store.reading() as db:
        return db.execute(select(func.coalesce(func.max(work_events.c.id), 0)).where(work_events.c.work_id == str(work_id))).scalar_one()


def state(store, work_id, job_id):
    """Everything a rolled-back transaction must leave unchanged."""
    return store.work_events(work_id, 0), store.job(job_id), public(store, job_id)


@pytest.mark.parametrize("composition", [
    {"width": 1920, "height": 1080, "fps": 30, "duration_in_frames": 150},
    {"width": 1080, "height": 1920, "fps": 24, "duration_in_frames": 150},
    {"width": 1080, "height": 1080, "fps": 30, "duration_in_frames": 150},
])
def test_creation_rejects_any_other_canvas_or_frame_rate(history_app, history_store, composition):
    """本链路主画布固定为 1080×1920、30 FPS；其他规格在创建时即被拒绝。"""
    with TestClient(history_app) as client:
        response = client.post("/works", headers=DEVICE_HEADERS, json={
            "description": "按所选配置生成标题", "composition": composition,
        })
        assert response.status_code == 422
    assert counts(history_store, works, jobs, chat_messages) == [0, 0, 0]


def test_creation_accepts_the_fixed_canvas_and_queues_the_job(history_app, history_store):
    """固定画布请求正常入库并排队，作品归属请求设备，首条消息与任务状态作为待送达事件。"""
    composition = {"width": 1080, "height": 1920, "fps": 30, "duration_in_frames": 150}
    with TestClient(history_app) as client:
        response = client.post("/works", headers=DEVICE_HEADERS, json={
            "description": "固定画布标题", "composition": composition,
        })
        assert response.status_code == 202
        work_id = response.json()["work"]["id"]
    assert history_store.project(work_id).request.composition.model_dump() == composition
    assert history_store.owner(work_id) == DEVICE
    events = history_store.work_events(work_id, 0)
    assert [event.type for event in events] == ["message.created", "job.updated"]
    assert events[0].data["text"] == "固定画布标题"
    assert events[1].data["status"] == "queued"


@pytest.mark.parametrize("patch", [
    {"width": 1081}, {"height": 0}, {"fps": 0},
    {"duration_in_frames": 0}, {"duration_in_frames": 901},
    {"width": 3840, "height": 3840},
])
def test_invalid_creation_configuration_has_no_side_effects(history_app, history_store, patch):
    """绕过客户端校验时仍返回 422，且不留下作品、任务或聊天消息。"""
    composition = {"width": 1080, "height": 1920, "fps": 30, "duration_in_frames": 150} | patch
    with TestClient(history_app) as client:
        response = client.post("/works", headers=DEVICE_HEADERS, json={"description": "标题", "composition": composition})
        assert response.status_code == 422
    assert counts(history_store, works, jobs, chat_messages) == [0, 0, 0]


def test_public_history_tracks_inputs_and_hides_repairs(history_store):
    """Publish input, question and answer exactly once while keeping steer and provider diagnostics private."""
    store = history_store
    work, job = store.create(GenerateTemplateRequest(description="居中标题"), DEVICE)
    store.claim()
    after = cursor(store, work.id)
    store.update(job.id, attempts=3, stage="repairing")
    assert store.work_events(work.id, after) == []
    store.update(job.id, status="needs_input", questions=["标题写什么？"])
    runtime = Runtime(store, None, None)
    runtime.notify = lambda *args: None
    answer_job = runtime.retry(job.id, "春日快乐")
    store.update(
        answer_job.id,
        status="failed",
        error=JobError(code="model_error", message="private provider secret"),
    )
    retry_job = runtime.retry(answer_job.id)
    events = store.work_events(work.id, 0)
    messages = [event.data for event in events if event.type == "message.created"]
    assert [m["text"] for m in messages if m["role"] == "user"] == ["居中标题", "春日快乐", "重试本次制作。"]
    assert store.latest_job(work.id).id == retry_job.id
    published = json.dumps([e.model_dump(mode="json") for e in events])
    for secret in ["private provider secret", "attempts", "repairing", "usage", "steer"]:
        assert secret not in published
    assert len({m["id"] for m in messages}) == len(messages)


def test_history_and_state_rollback_together(history_store, monkeypatch):
    """An event write failure cannot leave a job, message or work committed without its matching records."""
    original = history.append_event

    def fail_after_write(*args, **kwargs):
        """Simulate a failure after the database has accepted an event but before commit."""
        original(*args, **kwargs)
        raise OSError("disk full")

    monkeypatch.setattr(history, "append_event", fail_after_write)
    with pytest.raises(OSError):
        history_store.create(GenerateTemplateRequest(description="事务测试"), DEVICE)
    assert counts(history_store, works, jobs, chat_messages, work_events) == [0, 0, 0, 0]
    monkeypatch.setattr(history, "append_event", original)
    work, job = history_store.create(GenerateTemplateRequest(description="恢复写入"), DEVICE)
    before = state(history_store, work.id, job.id)
    monkeypatch.setattr(history, "append_event", fail_after_write)
    with pytest.raises(OSError):
        history_store.update(job.id, status="cancelled")
    assert state(history_store, work.id, job.id) == before
    assert history_store.job(job.id).status == "queued"


def test_progress_is_atomic_replayable_and_private(history_store, monkeypatch):
    """公开阶段同事务持久化，重复阶段不发事件，原始诊断不会进入阶段载荷。"""
    store = history_store
    work, job = store.create(GenerateTemplateRequest(description="标题"), DEVICE)
    store.claim()
    after = cursor(store, work.id)
    store.progress(job.id, "understanding")
    assert [step.phase for step in public(store, job.id).progress] == ["understanding"]
    first = cursor(store, work.id)
    store.progress(job.id, "understanding")
    assert cursor(store, work.id) == first
    with pytest.raises(ValidationError):
        store.progress(job.id, "private_model_steer")
    before = state(store, work.id, job.id)
    original = history.append_event

    def fail_write(*args, **kwargs):
        """模拟进度事件写入失败，阶段和任务必须一起回滚。"""
        original(*args, **kwargs)
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(history, "append_event", fail_write)
        with pytest.raises(OSError):
            store.progress(job.id, "generating")
    assert state(store, work.id, job.id) == before
    store.progress(job.id, "generating")
    events = store.work_events(work.id, after)
    assert [event.type for event in events] == ["job.updated", "job.updated"]
    assert events[-1].data["progress"][0]["status"] == "done"
    assert events[-1].data["progress"][0]["ended_at"] == events[-1].data["progress"][1]["started_at"]
    published = json.dumps([event.model_dump(mode="json") for event in events])
    assert all(secret not in published for secret in ("private_model_steer", "tsx_code", "usage", "checks"))
    reopened = Store(store.root)
    reopened.initialize()
    assert reopened.work_events(work.id, 0) == store.work_events(work.id, 0)


@pytest.mark.parametrize("status", ["answered", "needs_input", "failed", "cancelled", "interrupted"])
def test_terminal_progress_closes_without_late_updates(history_store, status):
    """完成、提问、失败、停止和服务中断都封闭最后区间，迟到阶段不能改变历史。"""
    store = history_store
    work, job = store.create(GenerateTemplateRequest(description="任务"), DEVICE)
    store.claim()
    store.progress(job.id, "understanding")
    extra = {"answer": "你好"} if status == "answered" else {"questions": ["文字是什么？"]} if status == "needs_input" else {}
    store.update(job.id, status=status, **extra)
    finished = public(store, job.id)
    step = finished.progress[-1]
    assert step.status == ("done" if status in {"answered", "needs_input"} else "stopped")
    assert step.ended_at == finished.updated_at
    before = state(store, work.id, job.id)
    store.progress(job.id, "rendering")
    assert state(store, work.id, job.id) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "answered"},
        {"status": "answered", "answer": " "},
        {"status": "answered", "answer": "回答", "result_version_id": str(uuid4())},
        {"status": "answered", "answer": "回答", "questions": ["继续追问"]},
        {"status": "running", "answer": "提前发布"},
    ],
)
def test_invalid_answer_state_rolls_back(history_store, changes):
    """缺正文、混入版本或追问、未结束就发布回答均不能落盘。"""
    work, job = history_store.create(GenerateTemplateRequest(description="你好"), DEVICE)
    history_store.claim()
    before = state(history_store, work.id, job.id)
    with pytest.raises(ValidationError):
        history_store.update(job.id, **changes)
    assert state(history_store, work.id, job.id) == before


def test_answer_history_is_atomic_replayable_and_terminal(history_store, monkeypatch):
    """回答状态与正文同事务提交；失败回滚、重复更新和重启不会遗漏或重复回答。"""
    store = history_store
    work, job = store.create(GenerateTemplateRequest(description="你能做什么？"), DEVICE)
    store.claim()
    after = cursor(store, work.id)
    before = state(store, work.id, job.id)
    original = history.append_message

    def fail_answer(*args, **kwargs):
        """模拟回答消息写入失败，要求先写入的任务状态和事件一并回滚。"""
        original(*args, **kwargs)
        raise OSError("disk full")

    monkeypatch.setattr(history, "append_message", fail_answer)
    with pytest.raises(OSError):
        store.update(job.id, status="answered", stage="finished", answer="可以制作字效。")
    assert state(store, work.id, job.id) == before
    monkeypatch.setattr(history, "append_message", original)
    store.update(job.id, status="answered", stage="finished", answer="可以制作字效。")
    events = store.work_events(work.id, after)
    assert [e.type for e in events] == ["job.updated", "message.created"]
    assert events[0].data["message"] == events[1].data["text"] == "可以制作字效。"
    saved = state(store, work.id, job.id)
    store.update(job.id, status="answered", answer="重复回答")
    store.initialize()
    assert state(store, work.id, job.id) == saved
    assert saved[1].result_version_id is None and store.project(work.id).current_version_id is None


def test_work_api_contracts(history_app, history_store):
    """缺少或非法设备头为 422；作品列表、服务端会话快照与旧轮询接口已移除，会话列表只在客户端本地。"""
    with TestClient(history_app) as client:
        assert client.post("/works", json={"description": "无设备"}).status_code == 422
        invalid = client.post("/works", headers={"X-IMV-Device": "not-a-uuid"}, json={"description": "非法"})
        assert invalid.status_code == 422
        created = client.post("/works", headers=DEVICE_HEADERS, json={"description": "API 标题"})
        assert created.status_code == 202
        work_id = created.json()["work"]["id"]
        assert client.get("/works", headers=DEVICE_HEADERS).status_code == 405
        assert client.get(f"/works/{work_id}/session", headers=DEVICE_HEADERS).status_code == 404
        assert client.get(f"/jobs/{created.json()['job']['id']}/events", headers=DEVICE_HEADERS).status_code == 404


def test_other_devices_cannot_reach_a_work(history_app, history_store):
    """其他设备读取、修改、订阅、确认或取消该作品均为 404；删除他人作品是 204 空操作。"""
    work, job = history_store.create(GenerateTemplateRequest(description="私有"), DEVICE)
    with TestClient(history_app) as client:
        for method, path, body in (
            ("get", f"/works/{work.id}", None),
            ("get", f"/works/{work.id}/versions", None),
            ("get", f"/works/{work.id}/stream", None),
            ("get", f"/works/{work.id}/events", None),
            ("get", f"/jobs/{job.id}", None),
            ("get", f"/jobs/{job.id}/artifacts", None),
            ("post", f"/works/{work.id}/messages", {"instruction": "改大"}),
            ("post", f"/works/{work.id}/ack", {"cursor": cursor(history_store, work.id)}),
            ("post", f"/jobs/{job.id}/cancel", None),
            ("post", f"/jobs/{job.id}/retry", None),
        ):
            response = getattr(client, method)(path, headers=OTHER_HEADERS, **({"json": body} if body else {}))
            assert response.status_code == 404, path
        assert client.delete(f"/works/{work.id}", headers=OTHER_HEADERS).status_code == 204
        assert client.get(f"/works/{work.id}", headers=DEVICE_HEADERS).status_code == 200
    assert history_store.job(job.id).status == "queued"
    assert len(history_store.work_events(work.id, 0)) == 2


def test_events_route_pages_undelivered_events_for_background_sync(history_app, history_store, monkeypatch):
    """未打开的作品经事件接口按页补取：顺序与 SSE 相同、`after` 之后续页、确认后为空；删除中的作品为 410。"""
    work, job = history_store.create(GenerateTemplateRequest(description="后台"), DEVICE)
    history_store.update(job.id, status="answered", stage="finished", answer="好")
    expected = history_store.work_events(work.id, 0)
    monkeypatch.setattr(history, "events", partial(history.events, limit=2))
    with TestClient(history_app) as client:
        first = client.get(f"/works/{work.id}/events", headers=DEVICE_HEADERS).json()
        rest = client.get(f"/works/{work.id}/events", headers=DEVICE_HEADERS, params={"after": first[-1]["id"]}).json()
        assert [event["id"] for event in first + rest] == [event.id for event in expected]
        assert first[0] == expected[0].model_dump(mode="json")
        assert client.get(f"/works/{work.id}/events", headers=DEVICE_HEADERS, params={"after": -1}).status_code == 422
        client.post(f"/works/{work.id}/ack", headers=DEVICE_HEADERS, json={"cursor": expected[-1].id})
        assert client.get(f"/works/{work.id}/events", headers=DEVICE_HEADERS).json() == []
        history_store.begin_deletion(work.id)
        assert client.get(f"/works/{work.id}/events", headers=DEVICE_HEADERS).status_code == 410


def test_history_images_remain_readable_and_integrity_checked(history_app, history_store):
    """Store a normalized reference in the object store, serve it by ID, report corrupt or missing bytes."""
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
    with TestClient(history_app) as client:
        uploaded = client.post(
            "/assets", headers=DEVICE_HEADERS, files={"file": ("reference.png", buffer.getvalue(), "image/png")}
        )
        assert uploaded.status_code == 201
        asset_id = uploaded.json()["id"]
        assert client.post("/works", headers=OTHER_HEADERS, json={"images": [{"asset_id": asset_id}]}).status_code == 404
        work = client.post("/works", headers=DEVICE_HEADERS, json={"images": [{"asset_id": asset_id}]}).json()["work"]["id"]
        message = history_store.work_events(work, 0)[0]
        assert message.type == "message.created" and message.data["image_asset_ids"] == [asset_id]
        image = client.get(f"/assets/{asset_id}")
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/png"
        stored = history_store.root / "objects" / asset_key(asset_id)
        cached = history_store.root / "cache" / "assets" / f"{asset_id}.png"
        assert stored.is_file() and cached.is_file()
        stored.write_bytes(b"corrupted")
        cached.unlink()
        assert client.get(f"/assets/{asset_id}").status_code == 409
        stored.unlink()
        assert client.get(f"/assets/{asset_id}").status_code == 404
        assert client.get(f"/assets/{uuid4()}").status_code == 404
        assert client.get("/assets/not-a-uuid").status_code == 422


def test_ack_deletes_delivered_events_and_prunes_finished_jobs(history_app, history_store):
    """确认全部事件后删除已送达事件与已结束任务的进度、轮次、消息；最新任务保留，仍可重试。"""
    store = history_store
    work, first = store.create(GenerateTemplateRequest(description="确认"), DEVICE)
    store.claim()
    store.progress(first.id, "understanding")
    store.round(first.id, {"layer": "outer", "turn": 1, "calls": []})
    store.update(first.id, status="answered", answer="好")
    latest = store.enqueue(work.id, JobInput(mode="edit", instruction="再改"), None)
    store.update(latest.id, status="failed")
    with TestClient(history_app) as client:
        response = client.post(f"/works/{work.id}/ack", headers=DEVICE_HEADERS, json={"cursor": cursor(store, work.id)})
        assert response.status_code == 204
        assert client.post(f"/works/{work.id}/ack", headers=DEVICE_HEADERS, json={"cursor": -1}).status_code == 422
    assert store.work_events(work.id, 0) == []
    assert counts(store, job_progress, job_rounds, chat_messages) == [0, 0, 0]
    with store.reading() as db:
        assert db.execute(select(jobs.c.id)).scalars().all() == [str(latest.id)]
    with pytest.raises(NotFound):
        store.job(first.id)
    runtime = Runtime(store, None, None)
    runtime.notify = lambda *args: None
    retried = runtime.retry(latest.id)
    assert [event.data["text"] for event in store.work_events(work.id, 0) if event.type == "message.created"] == ["重试本次制作。"]
    assert store.job(retried.id).status == "queued"


def test_partial_ack_keeps_undelivered_records(history_store):
    """只确认部分事件时其余事件保留、不清理任务；运行中的任务即便全部送达也保留进度。"""
    store = history_store
    work, first = store.create(GenerateTemplateRequest(description="部分确认"), DEVICE)
    store.update(first.id, status="cancelled")
    running = store.enqueue(work.id, JobInput(mode="edit", instruction="继续"), None)
    store.claim()
    store.progress(running.id, "understanding")
    events = store.work_events(work.id, 0)
    store.acknowledge(work.id, events[1].id, DEVICE)
    assert store.work_events(work.id, 0) == events[2:]
    assert counts(store, jobs)[0] == 2
    store.acknowledge(work.id, events[-1].id, DEVICE)
    assert store.work_events(work.id, 0) == []
    with store.reading() as db:
        assert db.execute(select(jobs.c.id)).scalars().all() == [str(running.id)]
    assert [step.phase for step in public(store, running.id).progress] == ["understanding"]
    store.progress(running.id, "generating")
    assert [event.type for event in store.work_events(work.id, 0)] == ["job.updated"]


def test_expire_events_drops_stale_deliveries_and_prunes(history_store):
    """超过期限仍未确认的事件被删除，对应作品清理已结束任务；期限之后的事件保留。"""
    store = history_store
    old, old_job = store.create(GenerateTemplateRequest(description="旧"), DEVICE)
    store.update(old_job.id, status="answered", answer="好")
    cutoff = now() + timedelta(microseconds=1)
    fresh, _ = store.create(GenerateTemplateRequest(description="新"), DEVICE)
    with store.reading() as db:
        assert db.execute(select(func.max(work_events.c.created_at)).where(work_events.c.work_id == str(old.id))).scalar() < cutoff.replace(tzinfo=None)
    store.expire_events(cutoff)
    assert store.work_events(old.id, 0) == []
    assert len(store.work_events(fresh.id, 0)) == 2
    with store.reading() as db:
        assert db.execute(select(func.count()).select_from(chat_messages).where(chat_messages.c.work_id == str(old.id))).scalar_one() == 0
    assert store.latest_job(old.id).id == old_job.id


def test_a_work_deleted_while_enqueueing_is_missing(history_store, monkeypatch):
    """作品在入队前的检查之后被删除完毕时，入队报告 404，而不是数据库异常导致的 500。"""
    work, job = history_store.create(GenerateTemplateRequest(description="并发删除"), DEVICE)
    history_store.update(job.id, status="failed", stage="finished")
    checked = history_store.project(work.id)
    history_store.begin_deletion(work.id)
    deletion.finish(history_store, work.id)
    monkeypatch.setattr(history_store, "project", lambda *args: checked)
    with pytest.raises(NotFound):
        history_store.enqueue(work.id, JobInput(mode="edit", instruction="继续"), None)


def test_only_devices_with_works_are_recorded(history_app, history_store, monkeypatch):
    """任意设备头不新增设备记录：设备在创建第一个作品时登记；进程内的刷新节流只保留有限个设备。"""
    monkeypatch.setattr(store_module, "TOUCH_CACHE", 3)
    with TestClient(history_app) as client:
        for _ in range(5):
            stranger = {"X-IMV-Device": str(uuid4())}
            assert client.get(f"/works/{uuid4()}", headers=stranger).status_code == 404
        assert counts(history_store, devices) == [0]
        assert len(history_store._touched) == 3
        assert client.post("/works", headers=DEVICE_HEADERS, json={"description": "登记"}).status_code == 202
    with history_store.reading() as db:
        assert db.execute(select(devices.c.device_id)).scalars().all() == [str(DEVICE)]


def test_expire_devices_marks_works_and_leaves_uploads_to_the_orphan_cleanup(history_store):
    """长期不活跃设备的作品被标记删除，活跃设备不受影响；它未被作品引用的上传由 24 小时后的孤儿清理回收。"""
    store = history_store
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "blue").save(buffer, format="PNG")
    settings = Settings(_env_file=None)
    used = save_image(buffer.getvalue(), store, settings, OTHER)
    unused = save_image(buffer.getvalue(), store, settings, OTHER)
    stale, _ = store.create(GenerateTemplateRequest(description="闲置", images=[{"asset_id": used.id}]), OTHER)
    active, _ = store.create(GenerateTemplateRequest(description="活跃"), DEVICE)
    store.touch(OTHER)
    store.touch(DEVICE)
    with store.transaction() as db:
        db.execute(update(devices).where(devices.c.device_id == str(OTHER)).values(
            last_seen_at=(now() - timedelta(days=200)).replace(tzinfo=None)))
    store.expire_devices(now() - timedelta(days=90))
    assert store.pending_deletions() == [stale.id]
    assert store.project(active.id).id == active.id
    with store.reading() as db:
        assert db.execute(select(devices.c.device_id)).scalars().all() == [str(DEVICE)]
    store.expire_devices(now() - timedelta(days=90))
    assert store.pending_deletions() == [stale.id]
    assert store.prune_assets() == []
    aged = unused.model_copy(update={"created_at": unused.created_at - timedelta(hours=25)})
    with store.transaction() as db:
        db.execute(update(assets).where(assets.c.id == str(unused.id)).values(data=aged.model_dump_json()))
    assert store.prune_assets() == [unused.id]
    with pytest.raises(NotFound):
        store.asset(unused.id)
    assert not (store.root / "objects" / asset_key(unused.id)).exists()
    assert store.asset(used.id).id == used.id


def test_sse_replays_all_pages_and_cleans_idle_wait(history_store):
    """Replay more than 100 undelivered events, preserve work isolation and cancel idle waits without cancelling work."""
    import asyncio

    from server.remotion_templates.stream import event_stream

    store = history_store
    work, job = store.create(GenerateTemplateRequest(description="长历史"), DEVICE)
    for _ in range(40):
        store.update(job.id, status="cancelled")
        job = store.enqueue(work.id, JobInput(), None)
    foreign, _ = store.create(GenerateTemplateRequest(description="其他会话"), DEVICE)
    expected = cursor(store, work.id)

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

    work, _ = history_store.create(GenerateTemplateRequest(description="心跳"), DEVICE)
    ticks = iter([0.0, 16.0, 16.0])
    monkeypatch.setattr(streaming, "monotonic", lambda: next(ticks))

    async def scenario():
        """Consume only the welcome and first heartbeat and close immediately."""
        stream = streaming.event_stream(history_store, work.id, cursor(history_store, work.id))
        await anext(stream)
        assert await asyncio.wait_for(anext(stream), 1) == ": heartbeat\n\n"
        await stream.aclose()

    asyncio.run(scenario())


def test_idle_sse_wakes_on_commit_of_its_own_work(history_store, monkeypatch):
    """An idle stream sleeps without reading until a transaction appends an event of its work, and then answers at once.

    The poll fallback is stretched to a minute, so only the commit wake-up can deliver the frame in time; a commit of
    another work leaves the stream waiting, and closing the stream unregisters its watcher.
    """
    import asyncio

    from server.remotion_templates import stream as streaming

    monkeypatch.setattr(streaming, "IDLE_POLL_SECONDS", 60.0)
    store = history_store
    work, job = store.create(GenerateTemplateRequest(description="唤醒"), DEVICE)
    other, other_job = store.create(GenerateTemplateRequest(description="无关"), DEVICE)
    reads = []
    original = store.work_events
    monkeypatch.setattr(store, "work_events", lambda *args: reads.append(args) or original(*args))

    async def scenario():
        """Drain the backlog, let the stream go idle, then commit to another work and to this one from a worker thread."""
        stream = streaming.event_stream(store, work.id, cursor(store, work.id))
        await anext(stream)
        waiting = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.2)
        assert len(reads) == 1 and not waiting.done()
        await asyncio.to_thread(store.update, other_job.id, status="cancelled")
        await asyncio.sleep(0.2)
        assert not waiting.done() and len(reads) == 1
        await asyncio.to_thread(store.update, job.id, status="cancelled")
        frame = await asyncio.wait_for(waiting, 2)
        assert '"status":"cancelled"' in frame.replace(" ", "") and str(work.id) in frame
        await stream.aclose()
        assert store._watchers == {}

    asyncio.run(scenario())


def test_sse_rejects_invalid_cursors_before_streaming(history_app, history_store):
    """Malformed cursors and unknown or foreign works return ordinary HTTP errors before SSE headers are sent."""
    work, _ = history_store.create(GenerateTemplateRequest(description="游标"), DEVICE)
    with TestClient(history_app) as client:
        path = f"/works/{work.id}/stream"
        for query, headers in (
            ("?after=-1", {}),
            ("?after=" + "9" * 50, {}),
            ("", {"Last-Event-ID": "invalid"}),
            ("", {"Last-Event-ID": "-1"}),
            ("", {"Last-Event-ID": "9" * 30}),
        ):
            assert client.get(path + query, headers=DEVICE_HEADERS | headers).status_code == 422
        assert client.get(path).status_code == 422
        assert client.get(path, headers=OTHER_HEADERS).status_code == 404
        assert client.get(f"/works/{uuid4()}/stream", headers=DEVICE_HEADERS).status_code == 404


def test_sse_http_reconnect_and_followup_job(history_app, history_store):
    """A real HTTP client receives frames promptly, resumes from Last-Event-ID and sees another job on the same connection."""
    import socket
    import threading
    import time

    import httpx
    import uvicorn

    work, job = history_store.create(GenerateTemplateRequest(description="真实 HTTP"), DEVICE)
    after = cursor(history_store, work.id)
    socket_ = socket.socket()
    socket_.bind(("127.0.0.1", 0))
    port = socket_.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(history_app, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [socket_]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    try:
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        url = f"http://127.0.0.1:{port}/works/{work.id}/stream"
        with httpx.stream("GET", url, params={"after": after}, headers=DEVICE_HEADERS, timeout=3, trust_env=False) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["x-accel-buffering"] == "no"
            lines = response.iter_lines()
            assert next(lines) == "retry: 2000"
            history_store.claim()
            event = json.loads(next(line[6:] for line in lines if line.startswith("data: ")))
            assert event["type"] == "job.updated"
            assert event["data"]["status"] == "running"
            history_store.progress(job.id, "understanding")
            event = json.loads(next(line[6:] for line in lines if line.startswith("data: ")))
            assert event["data"]["progress"][-1]["phase"] == "understanding"
            after = event["id"]
        assert history_store.job(job.id).status == "running"
        history_store.update(job.id, status="cancelled")
        with httpx.stream(
            "GET", url, params={"after": 0},
            headers=DEVICE_HEADERS | {"Last-Event-ID": str(after)}, timeout=3, trust_env=False,
        ) as response:
            lines = response.iter_lines()
            events = [json.loads(line[6:]) for line in _data_lines(lines, 2)]
            assert all(event["id"] > after for event in events)
            assert [event["type"] for event in events] == ["job.updated", "message.created"]
            assert events[0]["data"]["progress"][-1]["status"] == "stopped"
            followup = history_store.enqueue(work.id, JobInput(mode="edit", instruction="继续"), None)
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


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:1420",
        "http://localhost:4173",
        "http://localhost:1",
        "http://localhost:49152",
        "http://localhost:65535",
        "tauri://localhost",
        "http://tauri.localhost",
    ],
)
def test_mounted_stream_allows_replay_and_device_headers(client, origin):
    """实际主应用允许浏览器、Tauri 及 Windows 动态 localhost 端口携带 SSE 续传与设备请求头。"""
    response = client.options(
        f"/api/templates/works/{uuid4()}/stream",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "last-event-id, x-imv-device",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    allowed = response.headers["access-control-allow-headers"].lower()
    assert "last-event-id" in allowed and "x-imv-device" in allowed
    assert (
        client.options(
            f"/api/templates/works/{uuid4()}/stream",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "last-event-id",
            },
        ).status_code
        == 400
    )


MISSING_SANDBOX = "the isolated validator runs only on Linux, this host is darwin"


def test_creation_is_refused_before_queueing_when_the_sandbox_is_missing(history_app, history_store):
    """宿主无法启动隔离校验器（如 macOS）时，创建作品返回 503 并给出原因，不留下作品或任务。"""
    history_app.state.runtime.sandbox_problem = lambda: MISSING_SANDBOX
    with TestClient(history_app) as client:
        response = client.post("/works", headers=DEVICE_HEADERS, json={"description": "标题"})
        assert response.status_code == 503
        assert "darwin" in response.json()["detail"] and "Linux" in response.json()["detail"]
    assert counts(history_store, works, jobs, chat_messages) == [0, 0, 0]


def test_parameter_work_is_refused_but_conversation_is_not(history_app, history_store, monkeypatch):
    """参数补丁与参数任务的重试必然渲染，缺环境时返回 503；提问、澄清回复、文字修改、普通重试不需要校验器，仍交给服务层。

    修复前 messages 无条件预检，macOS 上用户连普通问答和 needs_input 回复都被 503 拒绝。
    """
    runtime = history_app.state.runtime
    with TestClient(history_app) as client:
        work_id = client.post("/works", headers=DEVICE_HEADERS, json={"description": "标题"}).json()["work"]["id"]
        job = history_store.latest_job(work_id)
        reached = []
        runtime.message = lambda *args: reached.append("message") or job
        runtime.retry = lambda *args, **kwargs: reached.append("retry") or job
        runtime.sandbox_problem = lambda: MISSING_SANDBOX

        patch = client.post(f"/works/{work_id}/messages", headers=DEVICE_HEADERS, json={"parameters": {"size": 80}})
        assert patch.status_code == 503 and "darwin" in patch.json()["detail"]
        for body in ({"instruction": "这个模板能做什么？"}, {"instruction": "改大"},
                     {"instruction": "白色", "reply_to_job_id": str(job.id)}):
            assert client.post(f"/works/{work_id}/messages", headers=DEVICE_HEADERS, json=body).status_code == 202
        assert reached == ["message"] * 3

        assert client.post(f"/jobs/{job.id}/retry", headers=DEVICE_HEADERS).status_code == 202
        monkeypatch.setattr(history_store, "inputs", lambda _id: JobInput(mode="parameters", parameters={"size": 80}))
        assert client.post(f"/jobs/{job.id}/retry", headers=DEVICE_HEADERS).status_code == 503
        assert reached == ["message"] * 3 + ["retry"]


def reference_image(store):
    """Register a normalized reference through the real upload pipeline."""
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
    return save_image(buffer.getvalue(), store, Settings(_env_file=None), DEVICE)


def messages(store, work_id):
    """Public chat messages still buffered for delivery, oldest first."""
    return [
        history.ChatMessage.model_validate(event.data)
        for event in store.work_events(work_id, 0) if event.type == "message.created"
    ]


def test_follow_up_messages_attach_reference_images(history_store, monkeypatch):
    """开始对话后的消息与澄清回答都可附参考图；模型按顺序收到每张图及其来源，回答图片绑定到所答问题。

    修复前 messages 不接受 image，开始对话后无法继续上传参考图（#127）；之后审计发现新增图片没有来源，
    模型提示仍称原图之后是候选帧，图片-only 的回答也不知道在回答哪个问题。
    """
    runtime = Runtime(history_store, None, Settings(_env_file=None))
    monkeypatch.setattr(runtime, "notify", lambda *args: None)
    original, answer, later = (reference_image(history_store) for _ in range(3))
    work, job = history_store.create(
        GenerateTemplateRequest(description="标题", images=[{"asset_id": original.id}]), DEVICE
    )
    history_store.update(job.id, status="needs_input", stage="finished", questions=["什么颜色？"])

    reply = runtime.message(
        work.id, TaskMessage(images=[{"asset_id": answer.id}], reply_to_job_id=job.id)
    )
    assert history_store.inputs(reply.id).images == [
        MessageImage(asset_id=answer.id, source="clarification_answer", clarification=0)
    ]
    message = messages(history_store, work.id)[-1]
    assert (message.text, message.image_asset_ids) == ("", [answer.id])

    seen = []

    class Harness:
        """Record what the model would receive and answer without generating."""

        renderer = None

        async def generate(self, spec, budget, directory, images, on_stage, **options):
            """Capture image files, the intent and the user turn written to the model window."""
            window = json.loads(options["context"].messages()[-1]["content"])
            seen.append((images, options["intent"], window))
            return DialogueOutput(answer="好的")

    runtime.harness = Harness()
    asyncio.run(runtime._execute(reply.id))
    images, intent, window = seen.pop()
    assert images == [history_store.asset_path(original.id), history_store.asset_path(answer.id)]
    assert intent["reference_images"] == [
        {"asset_id": str(original.id), "source": "work_original", "meaning": REFERENCE_MEANINGS["work_original"]},
        {"asset_id": str(answer.id), "source": "clarification_answer", "clarification": 0,
         "meaning": REFERENCE_MEANINGS["clarification_answer"]},
    ]
    assert json.loads(intent["clarifications"][0]) == {
        "questions": ["什么颜色？"], "answer": "", "answer_images": [str(answer.id)],
    }
    assert window["reference_images"] == intent["reference_images"]

    with pytest.raises(ValidationError):
        TaskMessage(parameters={"size": 80}, images=[{"asset_id": later.id}])
    with pytest.raises(NotFound):
        runtime.message(work.id, TaskMessage(instruction="参考", images=[{"asset_id": uuid4()}]))
    assert history_store.latest_job(work.id).id == reply.id
    follow = runtime.message(work.id, TaskMessage(instruction="改成这样", images=[{"asset_id": later.id}]))
    assert history_store.inputs(follow.id).images == [MessageImage(asset_id=later.id)]
    message = messages(history_store, work.id)[-1]
    assert (message.text, message.image_asset_ids) == ("改成这样", [later.id])
    asyncio.run(runtime._execute(follow.id))
    images, intent, _ = seen.pop()
    assert images == [history_store.asset_path(original.id), history_store.asset_path(later.id)]
    assert [(entry["asset_id"], entry["source"]) for entry in intent["reference_images"]] == [
        (str(original.id), "work_original"), (str(later.id), "message"),
    ]


def test_a_free_form_work_carries_earlier_turns_into_the_next_task(history_store, monkeypatch):
    """自由组件路径的多轮：之前消息附的参考图继续发送并标明来源，每个任务结束时 Outer 窗口写入结果记录（失败原因也在内），任务拿得到作品的版本列表；默认路径都没有。"""
    runtime = Runtime(history_store, None, Settings(_env_file=None, remotion_agent="autonomous"))
    monkeypatch.setattr(runtime, "notify", lambda *args: None)
    original, later = (reference_image(history_store) for _ in range(2))
    work, job = history_store.create(GenerateTemplateRequest(description="标题", images=[{"asset_id": original.id}]), DEVICE)
    seen = []

    class Harness:
        """Record what a task receives; the first task fails, the others answer."""

        renderer = None

        async def generate(self, spec, budget, directory, images, on_stage, **options):
            """Capture the images, intent, window and versions of one task."""
            seen.append((images, options["intent"], options["context"].messages(), options.get("versions")))
            if len(seen) == 1:
                raise ValueError("画不出来")
            return DialogueOutput(answer="好的")

    runtime.harness = Harness()
    asyncio.run(runtime._execute(job.id))
    second = runtime.message(work.id, TaskMessage(instruction="参考这张", images=[{"asset_id": later.id}]))
    asyncio.run(runtime._execute(second.id))
    third = runtime.message(work.id, TaskMessage(instruction="再改一下"))
    asyncio.run(runtime._execute(third.id))
    images, intent, window, versions = seen[-1]
    assert images == [history_store.asset_path(original.id), history_store.asset_path(later.id)]
    assert [(entry["asset_id"], entry["source"]) for entry in intent["reference_images"]] == [(str(original.id), "work_original"), (str(later.id), "earlier_message")]
    records = [json.loads(message["content"].split(": ", 1)[1])["task_outcome"] for message in window if message["content"].startswith("Host record (data): ")]
    assert [record["status"] for record in records] == ["failed", "answered"] and records[0]["error"] == "画不出来"
    assert versions == []


def test_new_chat_switches_reach_every_job_of_the_work(history_store, monkeypatch):
    """新建会话时打开「关键词字效」与「双栏」：作品请求保存 keyword_effect 与 two_tier，第一条任务与之后的修改任务都在
    original_request 里读到它们；未选择时为 false，只接受布尔值。"""
    runtime = Runtime(history_store, None, Settings(_env_file=None))
    monkeypatch.setattr(runtime, "notify", lambda *args: None)
    seen = []

    class Harness:
        """Record the intent and answer without generating."""

        renderer = None

        async def generate(self, spec, budget, directory, images, on_stage, **options):
            """Capture the intent the model would receive."""
            seen.append(options["intent"])
            return DialogueOutput(answer="好的")

    runtime.harness = Harness()
    work, job = history_store.create(GenerateTemplateRequest(description="标题", keyword_effect=True, two_tier=True), DEVICE)
    asyncio.run(runtime._execute(job.id))
    history_store.update(job.id, status="answered", stage="finished")
    follow = runtime.message(work.id, TaskMessage(instruction="关键词再大一点"))
    asyncio.run(runtime._execute(follow.id))
    assert [(intent["original_request"]["keyword_effect"], intent["original_request"]["two_tier"]) for intent in seen] == [(True, True)] * 2
    default = GenerateTemplateRequest(description="标题")
    assert default.keyword_effect is False and default.two_tier is False
    for name in ("keyword_effect", "two_tier"):
        with pytest.raises(ValidationError):
            GenerateTemplateRequest(description="标题", **{name: "yes"})


def test_other_devices_cannot_attach_my_reference_images(history_store, monkeypatch):
    """参考图只属于上传它的设备：其他设备的作品引用它时视为不存在，不登记引用。"""
    runtime = Runtime(history_store, None, Settings(_env_file=None))
    monkeypatch.setattr(runtime, "notify", lambda *args: None)
    mine = reference_image(history_store)
    with pytest.raises(NotFound):
        history_store.create(GenerateTemplateRequest(description="借图", images=[{"asset_id": mine.id}]), OTHER)
    work, job = history_store.create(GenerateTemplateRequest(description="他人"), OTHER)
    history_store.update(job.id, status="failed", stage="finished")
    with pytest.raises(NotFound):
        runtime.message(work.id, TaskMessage(images=[{"asset_id": mine.id}]))
    assert counts(history_store, work_assets) == [0]


def test_messages_carry_several_reference_images(history_app, history_store, monkeypatch):
    """创建与后续消息每条最多 4 张参考图，按发送顺序进入公开消息与模型输入；超量或重复列出同一张图被拒绝。"""
    runtime = Runtime(history_store, None, Settings(_env_file=None))
    monkeypatch.setattr(runtime, "notify", lambda *args: None)
    first, second, third, fourth, fifth = (reference_image(history_store) for _ in range(5))
    with TestClient(history_app, headers=DEVICE_HEADERS) as client:
        created = client.post("/works", json={
            "description": "标题", "images": [{"asset_id": str(first.id)}, {"asset_id": str(second.id)}],
        })
        assert created.status_code == 202
        work_id = created.json()["work"]["id"]
        assert messages(history_store, work_id)[0].image_asset_ids == [first.id, second.id]
        for images in ([first, second, third, fourth, fifth], [first, first]):
            body = {"description": "标题", "images": [{"asset_id": str(image.id)} for image in images]}
            assert client.post("/works", json=body).status_code == 422
    job = history_store.latest_job(work_id)
    history_store.update(job.id, status="failed", stage="finished")
    follow = runtime.message(
        work_id, TaskMessage(images=[{"asset_id": fourth.id}, {"asset_id": third.id}])
    )
    assert history_store.inputs(follow.id).images == [
        MessageImage(asset_id=fourth.id), MessageImage(asset_id=third.id),
    ]
    assert messages(history_store, work_id)[-1].image_asset_ids == [fourth.id, third.id]
    project = history_store.project(work_id)
    assert [(entry["asset_id"], entry["source"]) for entry in reference_images(project, history_store.inputs(follow.id))] == [
        (str(first.id), "work_original"), (str(second.id), "work_original"),
        (str(fourth.id), "message"), (str(third.id), "message"),
    ]


def test_legacy_single_image_request_creates_a_one_image_work(history_store):
    """旧客户端仍发送单张 image：创建的作品与首条公开消息按一张图记录。"""
    original = reference_image(history_store)
    work, _ = history_store.create(GenerateTemplateRequest(image={"asset_id": original.id}), DEVICE)
    assert [image.asset_id for image in history_store.project(work.id).request.images] == [original.id]
    assert messages(history_store, work.id)[0].image_asset_ids == [original.id]
