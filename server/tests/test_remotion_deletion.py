"""字效会话删除的真实 SQLite/文件/API/队列回归；执行 uv run --locked pytest tests/test_remotion_deletion.py。"""

import asyncio
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from server.remotion_templates import deletion
from server.remotion_templates.context import Conversation
from server.remotion_templates.media import save_image
from server.remotion_templates.parameters import patch_parameters
from server.remotion_templates.models import GenerateTemplateRequest, JobInput
from server.remotion_templates.routes import router
from server.remotion_templates.runtime import Runtime
from server.remotion_templates.settings import Settings
from server.remotion_templates.store import Conflict, NotFound, Store
from server.remotion_templates.stream import event_stream
from server.remotion_templates.evidence import seal_artifacts
from server.remotion_templates.harness import controls
from server.remotion_templates.models import (
    Check,
    CompositionConfig,
    TemplateCandidate,
    TemplateSpec,
    TextLayer,
    ValidationReport,
    validation_fingerprint,
)


@pytest.fixture
def spec():
    """最小有效方案仅用于验证真实发布事务，不执行模型或浏览器。"""
    return TemplateSpec(
        name="标题",
        description="白字",
        composition=CompositionConfig(
            width=320,
            height=240,
            duration_in_frames=6,
        ),
        text_layers=[TextLayer(id="title", text="你好", end_frame=6)],
    )


@pytest.fixture
def candidate(spec):
    """使用宿主参数契约建立可以保存的候选。"""
    schema, defaults = controls(spec)
    return TemplateCandidate(
        tsx_code="export default function Template() { return null; }",
        config_schema=schema,
        default_config=defaults,
    )


def evidence(candidate, spec):
    """显式离线验收结果用于发布门禁；不宣称执行了真实渲染。"""
    report = ValidationReport(
        checks=[
            Check(name=name, status="pass", detail="offline fixture")
            for name in (
                "configuration",
                "source_policy",
                "typescript",
                "bundle",
                "render",
                "determinism",
                "media_metadata",
                "transparency",
                "parameter_behavior",
                "motion_evidence",
                "visual_text",
                "visual_layout",
                "visual_style",
                "visual_motion",
                "visual_scope",
                "interactive_bundle",
                "export_source",
                "export_default_render",
                "repeat_render",
                "export_defaults",
            )
        ],
        fingerprint="",
        frames=[0],
        runtime={"test": "offline"},
    )
    report.fingerprint = validation_fingerprint(candidate, spec, report.runtime)
    return report


def publish_fixture(store, job_id, candidate, spec, report):
    """在对应任务目录写入和封存产物，再调用真实版本发布事务。"""
    directory = store.job_dir(job_id) / "candidate"
    directory.mkdir(parents=True, exist_ok=True)
    for name, data in {
        "Template.tsx": candidate.tsx_code,
        "candidate.json": candidate.model_dump_json(),
        "spec.json": spec.model_dump_json(),
    }.items():
        (directory / name).write_text(data)
    (directory / "interactive.js").write_text("// offline player")
    (directory / "Export.tsx").write_text(candidate.tsx_code)
    (directory / "preview.mp4").write_bytes(b"offline fixture")
    Image.new("RGBA", (8, 8), "white").save(directory / "frame-0.png")
    Image.new("RGBA", (8, 8), "white").save(directory / "export-default.png")
    seal_artifacts(candidate, spec, report, directory)
    return store.publish(job_id, candidate, spec, report, directory)


@pytest.fixture
def store(tmp_path):
    """所有元数据与生成文件只写入临时目录。"""
    result = Store(tmp_path / "data")
    result.initialize()
    return result


@pytest.fixture
def service(store):
    """真实运行时保持队列空闲；按需由用例显式启动离线任务。"""
    return Runtime(store, None, Settings(_env_file=None))


@pytest.fixture
def client(service):
    """接口不访问模型，使用真实 Runtime 删除流程和 HTTP 状态码。"""
    app = FastAPI()
    app.include_router(router, prefix="/api/templates")
    app.state.runtime = service
    with TestClient(app) as result:
        yield result


def reference(store):
    """创建经真实图片处理链规范化和登记的参考图。"""
    data = BytesIO()
    Image.new("RGB", (8, 8), "red").save(data, "PNG")
    return save_image(data.getvalue(), store, Settings(_env_file=None))


def test_delete_all_versions_jobs_and_files(client, store, candidate, spec):
    """整条删除包含多个成功版本、参数版本、失败候选及审计；其他会话与数据库保留。"""
    asset = reference(store)
    work, first = store.create(
        GenerateTemplateRequest(description="删除", image={"asset_id": asset.id})
    )
    store.claim()
    v1 = publish_fixture(store, first.id, candidate, spec, evidence(candidate, spec))
    second = store.enqueue(
        work.id, JobInput(mode="parameters", parameters={"0_text": "你好"}), v1.id
    )
    store.claim()
    edited, edited_spec = patch_parameters(candidate, spec, {"0_text": "你好"})
    v2 = publish_fixture(
        store, second.id, edited, edited_spec, evidence(edited, edited_spec)
    )
    failed = store.enqueue(work.id, JobInput(), v2.id)
    store.update(failed.id, status="failed")
    store.save_conversation(work.id, Conversation())
    for job in (first, second, failed):
        directory = store.job_dir(job.id) / "candidate"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "reviews.jsonl").write_text("private")
    other, other_job = store.create(GenerateTemplateRequest(description="保留"))
    store.job_dir(other_job.id).mkdir(parents=True)
    before = store.session(other.id)
    response = client.delete(f"/api/templates/works/{work.id}")
    assert response.status_code == 204 and response.content == b""
    assert client.delete(f"/api/templates/works/{work.id}").status_code == 204
    for path in (
        f"works/{work.id}",
        f"works/{work.id}/session",
        f"works/{work.id}/stream",
        f"versions/{v1.id}",
        f"jobs/{failed.id}",
        f"assets/{asset.id}",
    ):
        assert client.get(f"/api/templates/{path}").status_code == 404
    assert store.session(other.id) == before
    assert store.job_dir(other_job.id).exists() and store.path.exists()
    for job in (first, second, failed):
        assert not store.job_dir(job.id).exists()
    for version in (v1, v2):
        assert not (store.root / "accepted" / str(version.id)).exists()
    assert not (store.root / "assets" / f"{asset.id}.png").exists()
    with store.connection() as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        for table in (
            "versions",
            "conversations",
            "job_progress",
            "assets",
            "work_deletions",
        ):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert db.execute("SELECT DISTINCT work_id FROM chat_messages").fetchall()[0][
            0
        ] == str(other.id)


def test_shared_reference_removed_only_with_last_work(client, store):
    """共享图片和未关联上传不会被其他会话删除；最后一个引用删除时才清理共享图。"""
    asset, orphan = reference(store), reference(store)
    works = [
        store.create(
            GenerateTemplateRequest(description="共享", image={"asset_id": asset.id})
        )[0]
        for _ in range(2)
    ]
    assert client.delete(f"/api/templates/works/{works[0].id}").status_code == 204
    assert store.asset_path(asset.id).exists()
    assert client.delete(f"/api/templates/works/{works[1].id}").status_code == 204
    with pytest.raises(NotFound):
        store.asset(asset.id)
    assert store.asset_path(orphan.id).exists()
    with pytest.raises(NotFound):
        store.create(
            GenerateTemplateRequest(
                description="失效图片", image={"asset_id": asset.id}
            )
        )


def test_cleanup_failure_remains_retryable_and_blocks_writes(
    client, store, monkeypatch
):
    """文件清理失败返回 503，标记持久化且读写被拒绝；显式重试完成其余清理。"""
    work, job = store.create(GenerateTemplateRequest(description="失败恢复"))
    store.job_dir(job.id).mkdir(parents=True)
    original = deletion.remove_path

    def fail_after_removal(*args):
        """模拟文件已经删除，但随后的磁盘操作失败。"""
        original(*args)
        raise PermissionError("private filesystem path")

    monkeypatch.setattr(deletion, "remove_path", fail_after_removal)
    response = client.delete(f"/api/templates/works/{work.id}")
    assert response.status_code == 503
    assert "private" not in response.text
    assert store.pending_deletions() == [work.id]
    assert client.get(f"/api/templates/works/{work.id}/session").status_code == 410
    assert client.get(f"/api/templates/works/{work.id}/stream").status_code == 410
    assert (
        client.get("/api/templates/works?history=true").json()["items"][0]["deleting"]
        is True
    )
    with pytest.raises(HTTPException) as error:
        store.enqueue(work.id, JobInput(), None)
    assert error.value.status_code == 410
    assert store.claim() is None
    monkeypatch.setattr(deletion, "remove_path", original)
    assert client.delete(f"/api/templates/works/{work.id}").status_code == 204
    assert store.pending_deletions() == []


def test_restart_finishes_pending_cleanup(store):
    """进程重启只恢复明确请求的清理，不扫描或误删其他文件。"""
    work, job = store.create(GenerateTemplateRequest(description="重启"))
    store.job_dir(job.id).mkdir(parents=True)
    store.begin_deletion(work.id)
    runtime = Runtime(store, None, Settings(_env_file=None))
    runtime.initialize()
    try:
        assert store.pending_deletions() == []
        assert not store.job_dir(job.id).exists()
        with pytest.raises(NotFound):
            store.project(work.id)
    finally:
        runtime.lock.close()


@pytest.mark.parametrize("disconnect", [False, True])
@pytest.mark.parametrize("stop_first", [False, True])
def test_running_delete_waits_for_teardown_and_keeps_queue_alive(
    service, store, disconnect, stop_first
):
    """取消中仍有写入时先等待，重复删除共用操作且其他会话队列继续运行。"""

    async def scenario():
        """用可控屏障模拟模型/渲染 finally，所有等待设定上限。"""
        work, job = store.create(GenerateTemplateRequest(description="运行中"))
        other, other_job = store.create(GenerateTemplateRequest(description="后续"))
        started, stopping, release, completed = (asyncio.Event() for _ in range(4))

        async def execute(identifier):
            """目标任务取消后仍写最后一份审计；另一个任务正常完成。"""
            if identifier == job.id:
                store.job_dir(identifier).mkdir(parents=True)
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopping.set()
                    await release.wait()
                    (store.job_dir(identifier) / "audit.jsonl").write_text("last write")
                    store.save_conversation(work.id, Conversation())
            else:
                store.update(identifier, status="answered", answer="完成")
                completed.set()

        service._execute = execute
        service.notify()
        await started.wait()
        stop = asyncio.create_task(service.cancel(job.id)) if stop_first else None
        if stop:
            await stopping.wait()
        first = asyncio.create_task(service.delete_work(work.id))
        second = asyncio.create_task(service.delete_work(work.id))
        await stopping.wait()
        assert not first.done() and not second.done()
        assert store.job(job.id).status == "cancelled"
        if disconnect:
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert not second.done()
        release.set()
        if disconnect:
            await second
        else:
            await asyncio.gather(first, second)
        await completed.wait()
        await service.worker
        assert not store.job_dir(job.id).exists()
        assert store.job(other_job.id).status == "answered"
        if stop:
            await stop
        assert service.active is None
        assert service.deletions == {}

    asyncio.run(asyncio.wait_for(scenario(), 5))


def test_late_publish_is_rejected(store, candidate, spec):
    """开始删除后已有候选也不能发布，且入队前通过的请求会在写事务内再次校验。"""
    work, job = store.create(GenerateTemplateRequest(description="发布竞争"))
    store.claim()
    store.begin_deletion(work.id)
    with pytest.raises(Conflict):
        publish_fixture(store, job.id, candidate, spec, evidence(candidate, spec))
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM versions").fetchone()[0] == 0


@pytest.mark.parametrize("begin_only", [True, False])
def test_open_stream_terminates_on_deletion(service, store, begin_only):
    """已有 SSE 连接收到终止控制事件后关闭；不为删除保留聊天内容。"""

    async def scenario():
        """先建立流再触发删除，验证已有连接的下一次读。"""
        work, _ = store.create(GenerateTemplateRequest(description="连接"))
        stream = event_stream(store, work.id, store.session(work.id).cursor)
        assert "connected" in await anext(stream)
        if begin_only:
            store.begin_deletion(work.id)
        else:
            await service.delete_work(work.id)
        assert "event: work.deleted" in await anext(stream)
        with pytest.raises(StopAsyncIteration):
            await anext(stream)

    asyncio.run(asyncio.wait_for(scenario(), 5))


@pytest.mark.parametrize("parent_link", [True, False])
def test_cleanup_never_follows_symlinks(client, store, tmp_path, parent_link):
    """父目录重定向拒绝删除，叶子链接只删链接；两者均不触碰外部内容。"""
    work, job = store.create(GenerateTemplateRequest(description="边界"))
    external = tmp_path / "external"
    external.mkdir()
    (external / "keep.txt").write_text("keep")
    parent = store.root / "jobs"
    if parent_link:
        parent.symlink_to(external, target_is_directory=True)
    else:
        parent.mkdir()
        store.job_dir(job.id).symlink_to(external, target_is_directory=True)
    response = client.delete(f"/api/templates/works/{work.id}")
    assert response.status_code == (503 if parent_link else 204)
    assert (external / "keep.txt").read_text() == "keep"


def test_unknown_and_invalid_delete(client):
    """不存在的 ID 幂等成功，非法 ID 返回 422 且不会触碰目录。"""
    assert client.delete(f"/api/templates/works/{uuid4()}").status_code == 204
    assert client.delete("/api/templates/works/not-an-id").status_code == 422


def test_enqueue_rechecks_deletion_inside_transaction(store, monkeypatch):
    """入队预读后删除标记抢先提交，后续写事务仍拒绝创建任务。"""
    work, job = store.create(GenerateTemplateRequest(description="并发入队"))
    store.update(job.id, status="answered", answer="完成")
    original = store.project

    def read_then_delete(identifier):
        """模拟读取成功到实际插入之间的线程竞争。"""
        project = original(identifier)
        store.begin_deletion(identifier)
        return project

    monkeypatch.setattr(store, "project", read_then_delete)
    with pytest.raises(HTTPException) as error:
        store.enqueue(work.id, JobInput(), None)
    assert error.value.status_code == 410
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_pending_image_cannot_be_reused_after_partial_cleanup(store):
    """删除未完成时禁止新会话复用可能已经清理的参考图片元数据。"""
    asset = reference(store)
    work, _ = store.create(
        GenerateTemplateRequest(description="图片", image={"asset_id": asset.id})
    )
    store.begin_deletion(work.id)
    store.asset_path(asset.id).unlink()
    with pytest.raises(Conflict):
        store.create(
            GenerateTemplateRequest(description="复用", image={"asset_id": asset.id})
        )
    assert len(store.work_history().items) == 1
