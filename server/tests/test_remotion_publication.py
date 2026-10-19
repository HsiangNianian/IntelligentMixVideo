"""PR76 publication, parameter revisions and evidence binding through the store (SQLite stand-in for MySQL), the local object store and HTTP."""

import asyncio
import json
import shutil
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.remotion_templates.harness import Harness
from server.remotion_templates.models import EditTemplateRequest, GenerateTemplateRequest, JobInput
from server.remotion_templates.provider import Budget
from server.remotion_templates.publication import PREVIEW_PROTOCOL, PresentationBuilder, current_bundle, revisable
from server.remotion_templates.routes import router
from server.remotion_templates.runtime import Runtime
from server.remotion_templates.settings import Settings
from server.remotion_templates.store import Store, Conflict
from server.remotion_templates.tools.contracts import RenderValidationReport, SpriteDraft, SpriteRecord

from .conftest import DEVICE, DEVICE_HEADERS, RecordingObjects


@pytest.fixture
def sprite():
    """Use a nested parameter composition whose source and instance snapshot are independent of model output."""
    component = {"code": 'import React from "react"; export default function Title(p: {text: string}) { return <div>{p.text}</div>; }', "parameter_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False}, "default_parameters": {"text": "今日灵感"}, "description": "标题"}
    return SpriteDraft(description="标题与说明", code='import React from "react"; export default function Sprite(p: {title: {text:string}}) { return <div>{p.title.text}</div>; }', parameter_schema={"type": "object", "properties": {"title": component["parameter_schema"]}, "required": ["title"], "additionalProperties": False}, default_parameters={"title": {"text": "今日灵感"}}, composition={"width": 1080, "height": 1920, "fps": 30, "duration_frames": 30}, instances=[{"instance_id": "title", "preset": component, "parameters": {"text": "今日灵感"}, "layout": {"x": 0, "y": 0, "width": 1080, "height": 1920, "z_index": 0}, "timing": {"start_frame": 0, "duration_frames": 30}}])


def runtime_report(sprite):
    """Explicit offline worker result; publication tests do not claim actual browser execution."""
    return RenderValidationReport(passed=True, composition=sprite.composition, code_validation={"passed": True, "diagnostics": []}, checks=[{"name": "default_render", "status": "passed"}, {"name": "configured_render", "status": "passed"}], tests=[], custom_tests_executed=0)


class OfflinePresentationRenderer:
    """Replace only isolated compilation, retaining real input preparation, sealing and version storage."""

    def __init__(self, settings):
        """Inject temporary fonts and real managed source paths without a running browser."""
        self.settings = settings
        # A current bundle reads injected defaults and says so on its first line; clearing this stands in for one
        # built before that support.
        self.protocol = PREVIEW_PROTOCOL.decode() + "\n"

    async def run_worker(self, directory, *, worker):
        """Materialize deterministic stand-in artifacts from the exact host request."""
        assert worker == "presentation-worker.mjs"
        request = json.loads((directory / "request.json").read_text())
        (directory / "Template.tsx").write_text(request["code"])
        (directory / "Export.tsx").write_text(request["code"] + "\n// " + json.dumps(request["config"]))
        (directory / "interactive.js").write_text(self.protocol + "// offline Player fixture")
        return {"checks": [{"name": "export_source", "status": "pass"}, {"name": "presentation_bundle", "status": "pass"}]}


@pytest.fixture
def harness(tmp_path, template_db):
    """No secrets or external model clients are loaded by this fixture; the store uses the temporary database."""
    font = tmp_path / "font.ttc"
    font.write_bytes(b"fixture-font")
    settings = Settings(_env_file=None, data_dir=tmp_path / "state", font_regular=font, font_bold=font, )
    return Harness(SimpleNamespace(settings=settings), OfflinePresentationRenderer(settings))


def session_for(sprite, validation):
    """Supply a task-owned saved record and an exact validation lookup to the finalization boundary."""
    record = SpriteRecord(**sprite.model_dump(mode="json", exclude_unset=True), sprite_id="saved", created_at="2026-09-29T00:00:00+00:00")
    async def host_check(requested):
        """Stand in for the isolated mount check when no report was recorded earlier."""
        return validation

    return SimpleNamespace(
        saved_sprite=lambda identifier: record if identifier == "saved" else None,
        validation_for=lambda requested: validation if requested == sprite else None,
        validate_render=host_check,
    )


def test_finalization_requires_validation_and_seals_exact_sprite(harness, sprite, tmp_path):
    """Missing or failed tool evidence cannot become a published result; valid source and defaults round-trip."""
    with pytest.raises(ValueError, match="Validate"):
        asyncio.run(harness.finalize_sprite(session_for(sprite, None), "saved", Budget(), tmp_path / "unvalidated"))
    result = asyncio.run(harness.finalize_sprite(session_for(sprite, runtime_report(sprite)), "saved", Budget(), tmp_path / "generated"))
    candidate, spec, report, directory = result
    assert report.passed and report.profile == "v2"
    assert spec.schema_version == "2" and spec.sprite == sprite
    assert candidate.default_config == sprite.default_parameters
    assert candidate.tsx_code == sprite.code
    assert not list(directory.glob("frame-*.png"))
    assert "interactive.js" in report.artifacts and "Export.tsx" in report.artifacts


RENDERER_DEPS = Path(__file__).parents[1] / "src" / "server" / "remotion" / "node_modules" / "prettier"


def accepted_version(harness, sprite):
    """Publish one accepted composition version through the real store and return the store, work and version."""
    store = Store(harness.settings.data_dir)
    store.initialize()
    work, first = store.create(GenerateTemplateRequest(description="标题"), DEVICE)
    store.claim()
    result = asyncio.run(harness.finalize_sprite(session_for(sprite, runtime_report(sprite)), "saved", Budget(), store.job_dir(first.id)))
    return store, work, store.publish(first.id, *result)


def sealed(store: Store, version_id, name: str) -> Path:
    """The stored object of one sealed version file (local object store, since ZOS is not configured in tests)."""
    return store.root / "objects" / "imv" / "remotion" / "versions" / str(version_id) / name


def save_parameters(service: Runtime, work, initial, parameters: dict):
    """Submit a parameter patch through the runtime and wait for the instant revision to finish."""
    async def scenario():
        """The revision runs as a task outside the FIFO queue; wait for it like the event stream would."""
        job = service.edit(work.id, EditTemplateRequest(parameters=parameters, base_version_id=initial.id))
        await asyncio.gather(*service.saves.values())
        return job

    return asyncio.run(scenario())


def test_parameter_revision_is_saved_at_once_without_the_sandbox(harness, sprite, monkeypatch):
    """组合版本的参数修改只做 Schema 校验并立即封存：不进队列、不启动沙箱或浏览器、不重新打包，预览页带上新默认值。

    修复前每次保存配置都排队渲染并重建播放器包，慢且依赖本机校验环境。
    """
    store, work, initial = accepted_version(harness, sprite)
    exports = []

    def export(command, *, input, **options):
        """Stand in for the host export script: record the request and return a formatted export."""
        exports.append(json.loads(input))
        return SimpleNamespace(stdout=json.dumps({"code": "// export " + json.dumps(json.loads(input)["config"], ensure_ascii=False)}))

    async def no_worker(*args, **kwargs):
        """A parameter revision must not start any isolated worker."""
        raise AssertionError("parameter revision started a worker")

    monkeypatch.setattr("server.remotion_templates.publication.subprocess.run", export)
    monkeypatch.setattr(harness.renderer, "run_worker", no_worker)
    monkeypatch.setattr(harness.renderer, "sandbox_problem", lambda: "bwrap is not installed", raising=False)
    service = Runtime(store, harness, harness.settings)
    job = save_parameters(service, work, initial, {"title": {"text": "新标题"}})
    final = store.job(job.id)
    assert final.status == "succeeded", final.error
    assert service.worker is None
    revised = store.version(final.result_version_id)
    assert revised.candidate.default_config == {"title": {"text": "新标题"}}
    assert revised.candidate.tsx_code == initial.candidate.tsx_code
    assert revised.source == "user_parameters" and revised.agent_base_version_id == initial.id
    assert revised.validation.render_passed
    assert sealed(store, revised.id, "interactive.js").read_bytes() == sealed(store, initial.id, "interactive.js").read_bytes()
    assert '"新标题"' in sealed(store, revised.id, "Export.tsx").read_text()
    assert exports[0]["code"] == initial.candidate.tsx_code and exports[0]["composition"]["duration_in_frames"] == 30
    inherited = json.loads(sealed(store, revised.id, "tool-validation.json").read_text())
    assert inherited["inherited_from"] == str(initial.id) and inherited["component"]["default_parameters"] == {"title": {"text": "新标题"}}
    assert not store.conversation(work.id).messages()
    application = FastAPI()
    application.include_router(router, prefix="/api/templates")
    application.state.runtime = service
    with TestClient(application) as client:
        response = client.get(f"/api/templates/versions/{revised.id}/preview")
        assert response.status_code == 200 and "sandbox allow-scripts" in response.headers["content-security-policy"]
        data = response.text.split('<script type="application/json" id="imv-preview-host">', 1)[1].split("</script>", 1)[0]
        assert json.loads(data) == {"config": {"title": {"text": "新标题"}}}
        # 透明页面复用同一份封存播放器，旧版本也能叠在客户端本地视频上。
        overlay = client.get(f"/api/templates/versions/{revised.id}/preview?overlay=true")
        assert overlay.status_code == 200 and "conic-gradient" not in overlay.text
        assert "background:transparent" in overlay.text and "conic-gradient" in response.text
        assert overlay.headers["content-security-policy"] == response.headers["content-security-policy"]
        # Without a sandbox the instant revision is still accepted over HTTP.
        accepted_job = client.post(f"/api/templates/works/{work.id}/messages", headers=DEVICE_HEADERS, json={"parameters": {"title": {"text": "第三版"}}, "base_version_id": str(revised.id)})
        assert accepted_job.status_code == 202
        # A changed object is refused even when the read cache must be filled again from storage.
        sealed(store, revised.id, "interactive.js").write_text("tampered")
        (store.root / "cache" / "accepted" / str(revised.id) / "interactive.js").write_text("tampered")
        assert client.get(f"/api/templates/versions/{revised.id}/preview").status_code == 404


def test_a_bundle_without_injected_defaults_is_rebuilt_in_the_sandbox(harness, sprite, monkeypatch):
    """基线播放器包早于默认值注入时，复用它会让版本预览继续显示旧参数：这样的修订排队渲染并重建播放器包，路由也要求沙箱。

    修复前即时修订对所有组合版本都复制基线包，新版本已保存而独立打开的预览仍是旧默认值。
    """
    harness.renderer.protocol = ""
    store, work, initial = accepted_version(harness, sprite)
    seen = []

    class Validator:
        """Model-free runtime boundary records the patched defaults it renders."""

        def __init__(self, *args):
            """Accept the production validator injection signature."""

        async def validate_render(self, request):
            """Return an explicit offline report after recording the rendered defaults."""
            seen.append(request.component.default_parameters)
            return runtime_report(sprite)

    monkeypatch.setattr("server.remotion_templates.tool_validation.ToolValidator", Validator)
    service = Runtime(store, harness, harness.settings)
    harness.renderer.protocol = PREVIEW_PROTOCOL.decode() + "\n"
    application = FastAPI()
    application.include_router(router, prefix="/api/templates")
    application.state.runtime = service
    monkeypatch.setattr(harness.renderer, "sandbox_problem", lambda: "bwrap is not installed", raising=False)
    with TestClient(application) as client:
        refused = client.post(f"/api/templates/works/{work.id}/messages", headers=DEVICE_HEADERS, json={"parameters": {"title": {"text": "新标题"}}, "base_version_id": str(initial.id)})
        assert refused.status_code == 503
    job = store.enqueue(work.id, JobInput(mode="parameters", parameters={"title": {"text": "新标题"}}), initial.id)
    store.claim()
    asyncio.run(service._execute(job.id))
    final = store.job(job.id)
    assert final.status == "succeeded", final.error
    revised = store.version(final.result_version_id)
    assert seen == [{"title": {"text": "新标题"}}]
    assert current_bundle(sealed(store, revised.id, "interactive.js").read_bytes()) and service.instant(revised)
    with pytest.raises(ValueError, match="cannot show new defaults"):
        harness.presentation.revise(initial, store.accepted(initial), {"title": {"text": "x"}}, store.root / "direct")


def test_protocol_words_inside_an_older_bundle_do_not_allow_an_instant_revision(tmp_path):
    """只有首行的协议声明算数：生成代码与默认参数也打进包里，旧同步包的默认文字恰为 imv-preview-host 时仍要进沙箱重建。

    PR #111 审查复现：曾在整个包里搜索两个标记，这样的旧包被即时修订，预览继续显示编译进包的旧默认值。
    """
    bundle = tmp_path / "interactive.js"
    bundle.write_text('// imv-preview-sync\nvar config={"text":"imv-preview-host ' + PREVIEW_PROTOCOL.decode() + '"};')
    assert not revisable(tmp_path)
    bundle.write_text(PREVIEW_PROTOCOL.decode() + "\n(()=>{})();")
    assert revisable(tmp_path)


def test_invalid_parameters_and_a_busy_work_are_refused(harness, sprite):
    """不合 Schema 的参数在入队前拒绝；已有运行中的修订时同一作品的第二次保存返回冲突。"""
    store, work, initial = accepted_version(harness, sprite)
    service = Runtime(store, harness, harness.settings)
    with pytest.raises(ValueError, match="invalid parameter value: 1 is not of type 'string'"):
        service.edit(work.id, EditTemplateRequest(parameters={"title": {"text": 1}}, base_version_id=initial.id))
    application = FastAPI()
    application.include_router(router, prefix="/api/templates")
    application.state.runtime = service
    with TestClient(application, raise_server_exceptions=False) as client:
        # Regression: the schema error escaped as an HTTP 500 instead of a 422 the client can show.
        response = client.post(f"/api/templates/works/{work.id}/messages", headers=DEVICE_HEADERS, json={"parameters": {"title": {"text": 1}}, "base_version_id": str(initial.id)})
    assert response.status_code == 422 and "invalid parameter value" in response.json()["detail"]
    store.enqueue(work.id, JobInput(mode="parameters", parameters={"title": {"text": "x"}}), initial.id, running=True)
    with pytest.raises(Conflict):
        service.edit(work.id, EditTemplateRequest(parameters={"title": {"text": "y"}}, base_version_id=initial.id))


def test_a_failed_revision_is_reported_on_its_job(harness, sprite, monkeypatch):
    """导出无法重新生成时任务以 execution_failed 结束，不产生版本。"""
    store, work, initial = accepted_version(harness, sprite)

    def broken(*args, **kwargs):
        """The host export script is unavailable."""
        raise OSError("node is not installed")

    monkeypatch.setattr("server.remotion_templates.publication.subprocess.run", broken)
    service = Runtime(store, harness, harness.settings)
    job = save_parameters(service, work, initial, {"title": {"text": "新标题"}})
    final = store.job(job.id)
    assert (final.status, final.error.code, final.result_version_id) == ("failed", "execution_failed", None)
    assert "Standalone export" in final.error.message


@pytest.mark.skipif(not RENDERER_DEPS.exists(), reason="install locked Remotion renderer dependencies")
def test_the_host_export_script_embeds_the_new_defaults(harness, sprite):
    """真实的 export.mjs 只解析和格式化封存源码，导出文件带上新的默认值。"""
    store, work, initial = accepted_version(harness, sprite)
    service = Runtime(store, harness, harness.settings)
    job = save_parameters(service, work, initial, {"title": {"text": "新标题"}})
    revised = store.version(store.job(job.id).result_version_id)
    exported = sealed(store, revised.id, "Export.tsx").read_text()
    assert '{ title: { text: "新标题" } }' in exported or '"新标题"' in exported
    assert "export default function" in exported


def test_cancellation_wins_over_ready_artifacts(harness, sprite):
    """A result built before cancellation cannot be published after the durable cancellation state."""
    store = Store(harness.settings.data_dir)
    store.initialize()
    work, job = store.create(GenerateTemplateRequest(description="title"), DEVICE)
    store.claim()
    result = asyncio.run(harness.finalize_sprite(session_for(sprite, runtime_report(sprite)), "saved", Budget(), store.job_dir(job.id)))
    store.update(job.id, status="cancelled", stage="finished")
    with pytest.raises(Conflict):
        store.publish(job.id, *result)
    assert store.project(work.id).current_version_id is None


def recording_store(harness) -> tuple[Store, RecordingObjects]:
    """A store whose local object store records where each call ran."""
    objects = RecordingObjects(harness.settings.data_dir / "objects")
    store = Store(harness.settings.data_dir, objects)
    objects.store = store
    store.initialize()
    return store, objects


def test_generation_publishes_off_the_event_loop_and_outside_the_write_lock(harness, sprite):
    """A generated version is uploaded in a worker thread before the record transaction, never under the write lock."""
    store, objects = recording_store(harness)
    work, job = store.create(GenerateTemplateRequest(description="标题"), DEVICE)
    store.claim()
    result = asyncio.run(harness.finalize_sprite(session_for(sprite, runtime_report(sprite)), "saved", Budget(), store.job_dir(job.id)))

    class Generated:
        """Return the finished composition without a model call."""

        renderer = None

        async def generate(self, *args, **options):
            """Hand back the sealed result the runtime then publishes."""
            return result

    service = Runtime(store, Generated(), harness.settings)
    asyncio.run(service._execute(job.id))
    version = store.project(work.id).current_version_id
    assert version is not None and store.job(job.id).status == "succeeded"
    puts = [call for call in objects.calls if call[0] == "put"]
    assert len(puts) == len(store.version(version).validation.artifacts)
    assert all(not on_loop and not locked for _, _, on_loop, locked in puts)


def test_a_failed_publication_removes_its_upload_unless_the_version_committed(harness, sprite, monkeypatch):
    """A rolled-back transaction removes the uploaded objects; a commit whose acknowledgement is lost keeps them."""
    store, objects = recording_store(harness)
    versions_root = harness.settings.data_dir / "objects" / "imv" / "remotion" / "versions"
    work, job = store.create(GenerateTemplateRequest(description="标题"), DEVICE)
    store.claim()
    result = asyncio.run(harness.finalize_sprite(session_for(sprite, runtime_report(sprite)), "saved", Budget(), store.job_dir(job.id)))
    original = Store.transaction

    class Rollback:
        """Raise inside the publication transaction so nothing commits."""

        def __init__(self, store):
            """Wrap the real transaction of this store."""
            self.manager = original(store)

        def __enter__(self):
            """Open the real transaction."""
            return self.manager.__enter__()

        def __exit__(self, kind, error, trace):
            """Roll back as if the commit had failed."""
            self.manager.__exit__(OSError, OSError("commit failed"), None)
            raise OSError("commit failed")

    class LostAcknowledgement(Rollback):
        """Commit for real, then report a failure as a dropped connection would."""

        def __exit__(self, kind, error, trace):
            """Commit, then raise."""
            self.manager.__exit__(kind, error, trace)
            raise OSError("acknowledgement lost")

    monkeypatch.setattr(Store, "transaction", lambda self: Rollback(self) if self is store else original(self))
    with pytest.raises(OSError, match="commit failed"):
        store.publish(job.id, *result)
    monkeypatch.setattr(Store, "transaction", original)
    assert store.project(work.id).current_version_id is None
    assert not versions_root.exists() or not any(versions_root.iterdir())
    monkeypatch.setattr(Store, "transaction", lambda self: LostAcknowledgement(self) if self is store else original(self))
    with pytest.raises(OSError, match="acknowledgement lost"):
        store.publish(job.id, *result)
    monkeypatch.setattr(Store, "transaction", original)
    version = store.version(store.project(work.id).current_version_id)
    assert all(sealed(store, version.id, name).is_file() for name in version.validation.artifacts)


def test_instant_revision_check_reads_only_the_player_bundle(harness, sprite):
    """Deciding whether a parameter patch is instant downloads the bundle alone, not every sealed file."""
    store, objects = recording_store(harness)
    work, job = store.create(GenerateTemplateRequest(description="标题"), DEVICE)
    store.claim()
    result = asyncio.run(harness.finalize_sprite(session_for(sprite, runtime_report(sprite)), "saved", Budget(), store.job_dir(job.id)))
    version = store.publish(job.id, *result)
    shutil.rmtree(store.root / "cache")
    objects.calls.clear()
    assert Runtime(store, harness, harness.settings).instant(version)
    assert [call[1].rsplit("/", 1)[-1] for call in objects.calls] == ["interactive.js"]


def test_concurrent_readers_of_a_cold_cache_all_get_the_sealed_files(harness, sprite, monkeypatch):
    """Preview, diagnostics and downloads opening a version at once each get complete, verified files."""
    store, objects = recording_store(harness)
    work, job = store.create(GenerateTemplateRequest(description="标题"), DEVICE)
    store.claim()
    result = asyncio.run(harness.finalize_sprite(session_for(sprite, runtime_report(sprite)), "saved", Budget(), store.job_dir(job.id)))
    version = store.publish(job.id, *result)
    shutil.rmtree(store.root / "cache")
    fetch = RecordingObjects.fetch

    def slow_fetch(self, key, destination):
        """Download slowly enough for the readers to overlap."""
        time.sleep(0.02)
        fetch(self, key, destination)

    monkeypatch.setattr(RecordingObjects, "fetch", slow_fetch)
    barrier = threading.Barrier(8)
    errors, directories = [], []

    def read():
        """Open the version together with the other readers and read every sealed file."""
        barrier.wait()
        try:
            directory = store.accepted(version)
            directories.append({name: (directory / name).read_bytes() for name in version.validation.artifacts})
        except Exception as exc:
            errors.append(exc)

    readers = [threading.Thread(target=read) for _ in range(8)]
    for reader in readers:
        reader.start()
    for reader in readers:
        reader.join()
    assert errors == [] and len(directories) == 8
    assert all(files == directories[0] for files in directories)


def test_host_builds_preview_without_agent_test_scripts(harness, sprite, tmp_path):
    """The host checks mounting and builds sealed artifacts after a model only saves a Sprite."""
    session = session_for(sprite, None)
    seen = []

    async def validate(request):
        """Record the host's exact component and confirm no custom scripts are requested."""
        seen.append(request)
        return runtime_report(sprite)

    session.validate_render = validate
    candidate, spec, report, directory = asyncio.run(harness.finalize_sprite(session, "saved", Budget(), tmp_path / "created"))
    assert len(seen) == 1 and seen[0].tests == []
    assert seen[0].component.code == sprite.code
    assert seen[0].component.default_parameters == sprite.default_parameters
    assert report.passed and (directory / "interactive.js").exists()
