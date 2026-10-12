"""PR76 publication, parameter revisions and evidence binding through real SQLite and HTTP boundaries."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import jsonschema
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.remotion_templates.harness import Harness
from server.remotion_templates.models import EditTemplateRequest, GenerateTemplateRequest, JobInput
from server.remotion_templates.provider import Budget
from server.remotion_templates.publication import PresentationBuilder
from server.remotion_templates.routes import router
from server.remotion_templates.runtime import Runtime
from server.remotion_templates.settings import Settings
from server.remotion_templates.store import Store, Conflict
from server.remotion_templates.tools.contracts import RenderValidationReport, SpriteDraft, SpriteRecord


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

    async def run_worker(self, directory, *, worker):
        """Materialize deterministic stand-in artifacts from the exact host request."""
        assert worker == "presentation-worker.mjs"
        request = json.loads((directory / "request.json").read_text())
        (directory / "Template.tsx").write_text(request["code"])
        (directory / "Export.tsx").write_text(request["code"] + "\n// " + json.dumps(request["config"]))
        (directory / "interactive.js").write_text("// offline Player fixture")
        return {"checks": [{"name": "export_source", "status": "pass"}, {"name": "presentation_bundle", "status": "pass"}]}


@pytest.fixture
def harness(tmp_path):
    """No secrets or external model clients are loaded by this fixture."""
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
    work, first = store.create(GenerateTemplateRequest(description="标题"))
    store.claim()
    result = asyncio.run(harness.finalize_sprite(session_for(sprite, runtime_report(sprite)), "saved", Budget(), store.job_dir(first.id)))
    return store, work, store.publish(first.id, *result)


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
    accepted = store.root / "accepted"
    assert (accepted / str(revised.id) / "interactive.js").read_bytes() == (accepted / str(initial.id) / "interactive.js").read_bytes()
    assert '"新标题"' in (accepted / str(revised.id) / "Export.tsx").read_text()
    assert exports[0]["code"] == initial.candidate.tsx_code and exports[0]["composition"]["duration_in_frames"] == 30
    inherited = json.loads((accepted / str(revised.id) / "tool-validation.json").read_text())
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
        # Without a sandbox the instant revision is still accepted over HTTP.
        accepted_job = client.post(f"/api/templates/works/{work.id}/messages", json={"parameters": {"title": {"text": "第三版"}}, "base_version_id": str(revised.id)})
        assert accepted_job.status_code == 202
        (store.root / "accepted" / str(revised.id) / "interactive.js").write_text("tampered")
        assert client.get(f"/api/templates/versions/{revised.id}/preview").status_code == 404


def test_invalid_parameters_and_a_busy_work_are_refused(harness, sprite):
    """不合 Schema 的参数在入队前拒绝；已有运行中的修订时同一作品的第二次保存返回冲突。"""
    store, work, initial = accepted_version(harness, sprite)
    service = Runtime(store, harness, harness.settings)
    with pytest.raises(jsonschema.ValidationError):
        service.edit(work.id, EditTemplateRequest(parameters={"title": {"text": 1}}, base_version_id=initial.id))
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
    exported = (store.root / "accepted" / str(revised.id) / "Export.tsx").read_text()
    assert '{ title: { text: "新标题" } }' in exported or '"新标题"' in exported
    assert "export default function" in exported


def test_cancellation_wins_over_ready_artifacts(harness, sprite):
    """A result built before cancellation cannot be published after the durable cancellation state."""
    store = Store(harness.settings.data_dir)
    store.initialize()
    work, job = store.create(GenerateTemplateRequest(description="title"))
    store.claim()
    result = asyncio.run(harness.finalize_sprite(session_for(sprite, runtime_report(sprite)), "saved", Budget(), store.job_dir(job.id)))
    store.update(job.id, status="cancelled", stage="finished")
    with pytest.raises(Conflict):
        store.publish(job.id, *result)
    assert store.project(work.id).current_version_id is None


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
