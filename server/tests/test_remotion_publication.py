"""PR76 publication, parameter revisions and evidence binding through real SQLite and HTTP boundaries."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.remotion_templates.harness import Harness
from server.remotion_templates.models import GenerateTemplateRequest, JobInput
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
        validate_pr76_render=host_check,
    )


def test_finalization_requires_validation_and_seals_exact_sprite(harness, sprite, tmp_path):
    """Missing or failed tool evidence cannot become a published result; valid source and defaults round-trip."""
    with pytest.raises(ValueError, match="Validate"):
        asyncio.run(harness.finalize_sprite(session_for(sprite, None), "saved", Budget(), tmp_path / "unvalidated"))
    result = asyncio.run(harness.finalize_sprite(session_for(sprite, runtime_report(sprite)), "saved", Budget(), tmp_path / "generated"))
    candidate, spec, report, directory = result
    assert report.passed and report.profile == "pr76"
    assert spec.schema_version == "2" and spec.sprite == sprite
    assert candidate.default_config == sprite.default_parameters
    assert candidate.tsx_code == sprite.code
    assert not list(directory.glob("frame-*.png"))
    assert "interactive.js" in report.artifacts and "Export.tsx" in report.artifacts


def test_parameter_revision_and_http_preview_preserve_source(harness, sprite, tmp_path, monkeypatch):
    """Real Runtime saves nested parameter changes without a model call and exposes only sealed artifacts."""
    store = Store(harness.settings.data_dir)
    store.initialize()
    work, first = store.create(GenerateTemplateRequest(description="标题"))
    store.claim()
    result = asyncio.run(harness.finalize_sprite(session_for(sprite, runtime_report(sprite)), "saved", Budget(), store.job_dir(first.id)))
    initial = store.publish(first.id, *result)
    assert store.version(initial.id).spec.sprite == sprite
    seen = []

    class Validator:
        """Model-free runtime boundary records the real nested patched parameters."""
        def __init__(self, *args):
            """Accept the production validator injection signature."""
        async def validate_render(self, request):
            """Return an explicit offline report after recording input."""
            seen.append(request.component.default_parameters)
            return runtime_report(sprite)

    monkeypatch.setattr("server.remotion_templates.tool_validation.ToolValidator", Validator)
    service = Runtime(store, harness, harness.settings)
    job = store.enqueue(work.id, JobInput(mode="parameters", parameters={"title": {"text": "新标题"}}), initial.id)
    store.claim()
    asyncio.run(service._execute(job.id))
    assert store.job(job.id).status == "succeeded", store.job(job.id).error
    revised = store.version(store.job(job.id).result_version_id)
    assert seen == [{"title": {"text": "新标题"}}]
    assert revised.candidate.tsx_code == initial.candidate.tsx_code
    assert revised.source == "user_parameters" and revised.agent_base_version_id == initial.id
    assert not store.conversation(work.id).messages()
    application = FastAPI()
    application.include_router(router, prefix="/api/templates")
    application.state.runtime = service
    with TestClient(application) as client:
        response = client.get(f"/api/templates/versions/{revised.id}/preview")
        assert response.status_code == 200
        assert "sandbox allow-scripts" in response.headers["content-security-policy"]
        payload = client.get(f"/api/templates/versions/{revised.id}").json()
        assert payload["candidate"]["default_config"] == seen[0]
        assert "source_preset_id" not in payload["spec"]["sprite"]["instances"][0]["preset"]
        (store.root / "accepted" / str(revised.id) / "interactive.js").write_text("tampered")
        assert client.get(f"/api/templates/versions/{revised.id}/preview").status_code == 404


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

    session.validate_pr76_render = validate
    candidate, spec, report, directory = asyncio.run(harness.finalize_sprite(session, "saved", Budget(), tmp_path / "created"))
    assert len(seen) == 1 and seen[0].tests == []
    assert seen[0].component.code == sprite.code
    assert seen[0].component.default_parameters == sprite.default_parameters
    assert report.passed and (directory / "interactive.js").exists()
