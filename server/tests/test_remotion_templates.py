"""Exercise contracts, evidence, persistence and agent behavior offline: uv run --locked pytest tests/test_remotion_templates.py."""

import asyncio
import json
import os
import socket
import subprocess
import sys
from io import BytesIO
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr, ValidationError
from server.settings import Settings
from server.remotion_templates.api import create_template_app
from server.remotion_templates.context import AssistantMessage, Conversation
from server.remotion_templates.evidence import seal_artifacts
from server.remotion_templates.harness import CodeOutput, Harness, controls
from server.remotion_templates.media import save_image
from server.remotion_templates.models import (
    AnalysisResult,
    Check,
    CompositionConfig,
    EditDecision,
    GenerateTemplateRequest,
    JobInput,
    TargetReview,
    TemplateCandidate,
    TemplateSpec,
    TextLayer,
    ValidationReport,
    VisualCheck,
    VisualReview,
    validation_fingerprint,
)
from server.remotion_templates.parameters import patch_parameters, validate_candidate
from server.remotion_templates.provider import (
    Budget,
    ModelFailure,
    Provider,
    model_image,
)
from server.remotion_templates.renderer import Renderer
from server.remotion_templates.runtime import Runtime
from server.remotion_templates.store import Conflict, NotFound, Store
from server.remotion_templates.trajectory import Trajectory

# A maintained reference component demonstrates direct props and deterministic transparent text.
SAMPLE_CODE = """/** Static editable text reference; the preview host loads managed fonts. */
import React from "react";
import {AbsoluteFill} from "remotion";
/** Render the accepted copy at normalized center coordinates. */
export default function Template(p: Record<string, string | number>) {
  return <AbsoluteFill><div style={{position: "absolute", left: Number(p["0_layout_x"])*100+"%", top: Number(p["0_layout_y"])*100+"%", width: Number(p["0_layout_width"])*100+"%", transform: `translate(-50%, -50%) rotate(${p["0_layout_rotation"]}deg)`, textAlign: String(p["0_layout_align"]) as React.CSSProperties["textAlign"], fontFamily: String(p["0_style_font_family"]), fontSize: Number(p["0_style_font_size"]), fontWeight: Number(p["0_style_font_weight"]), lineHeight: Number(p["0_style_line_height"]), letterSpacing: Number(p["0_style_letter_spacing"]), color: String(p["0_style_color"]), whiteSpace: "pre-wrap"}}>{p["0_text"]}</div></AbsoluteFill>;
}
"""


def create_app(settings, *, provider=None, renderer=None):
    """Test the real sub-app mount with isolated resources, without altering shared repository fixtures."""
    application = FastAPI()
    application.state.template_app = create_template_app(
        settings, provider=provider, renderer=renderer
    )
    application.mount("/api/templates", application.state.template_app)
    return application


@pytest.fixture
def spec() -> TemplateSpec:
    """A small but valid composition keeps sandbox integration deterministic and quick."""
    return TemplateSpec(
        name="标题",
        description="居中的白色标题",
        composition=CompositionConfig(width=320, height=240, duration_in_frames=6),
        text_layers=[TextLayer(id="title", text="你好", end_frame=6)],
    )


@pytest.fixture
def candidate(spec) -> TemplateCandidate:
    """Supply real host-generated controls and reusable source, with no model dependency."""
    schema, defaults = controls(spec)
    return TemplateCandidate(
        tsx_code=SAMPLE_CODE, config_schema=schema, default_config=defaults
    )


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Use synthetic credentials and isolated metadata; never load the user's local .env."""
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        actor_model="offline",
        vision_model="offline",
        actor_api_key=SecretStr("test-private-token"),
    )


@pytest.fixture
def store(tmp_path) -> Store:
    """Initialize a temporary database for state-machine and concurrency checks."""
    result = Store(tmp_path / "store")
    result.initialize()
    return result


def evidence(candidate, spec, **statuses) -> ValidationReport:
    """Build explicitly scripted evidence; individual tests vary real acceptance dimensions."""
    names = [
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
    ]
    report = ValidationReport(
        fingerprint="",
        checks=[
            Check(
                name=name,
                status=statuses.get(name, "pass"),
                detail="Scripted test observation.",
            )
            for name in names
        ],
        frames=[0, 2, 5],
        runtime={"test": "offline"},
    )
    report.fingerprint = validation_fingerprint(candidate, spec, report.runtime)
    return report


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"description": " "},
        {"video": {"asset_id": str(uuid4())}},
        {"description": "x", "video": {}},
        {"description": "x", "model": "override"},
    ],
)
def test_invalid_generation_contract(payload):
    """Empty input, video and per-request provider overrides must be rejected explicitly."""
    with pytest.raises(ValidationError):
        GenerateTemplateRequest.model_validate(payload)


@pytest.mark.parametrize(
    "patch",
    [
        {"width": 65},
        {"height": 0},
        {"fps": 0},
        {"fps": float("nan")},
        {"duration_in_frames": True},
        {"duration_in_frames": 1800, "fps": 30},
        {"width": 3840, "height": 3840},
    ],
)
def test_composition_bounds(patch):
    """Reject invalid dimensions, nonfinite values, booleans and excessive duration or pixels."""
    with pytest.raises(ValidationError):
        CompositionConfig(**patch)


def test_analysis_and_edit_exclusive_outcomes(spec):
    """Ambiguity cannot coexist with a supposedly actionable specification or patch."""
    assert AnalysisResult(spec=spec).spec == spec
    assert AnalysisResult(questions=["哪段文字？"]).questions
    for payload in ({}, {"spec": spec, "questions": ["Which?"]}):
        with pytest.raises(ValidationError):
            AnalysisResult(**payload)
    for payload in ({}, {"parameters": {}}, {"parameters": {"x": 1}, "spec": spec}):
        with pytest.raises(ValidationError):
            EditDecision(**payload)


def test_parameters_preserve_source_and_update_goal(candidate, spec):
    """Scalar edits change both defaults and target while preserving exact source bytes."""
    updated, target = patch_parameters(
        candidate, spec, {"0_text": " 新标题 ", "0_layout_x": 0.3}
    )
    assert updated.tsx_code == candidate.tsx_code
    assert target.text_layers[0].text == " 新标题 "
    assert target.text_layers[0].layout.x == 0.3
    assert spec.text_layers[0].text == "你好"
    assert "text_layers" in target.description
    validate_candidate(updated, target)


@pytest.mark.parametrize(
    "patch",
    [
        {"missing": 1},
        {"0_layout_x": 2},
        {"0_text": ""},
        {"0_style_font_family": "download this"},
        {"0_style_font_size": -1},
        {"0_layout_y": float("inf")},
    ],
)
def test_invalid_parameters(candidate, spec, patch):
    """Reject unknown controls and values violating either schema or accepted domain bounds."""
    with pytest.raises(ValueError):
        patch_parameters(candidate, spec, patch)


@pytest.mark.parametrize(
    "pointer",
    [
        "/text_layers/-1/text",
        "/text_layers/00/text",
        "/text_layers/0/id",
        "/text_layers/0/style",
        "/request/secret",
    ],
)
def test_parameter_binding_cannot_escape(candidate, spec, pointer):
    """Bindings reject aliases, negative indices, structure and arbitrary document traversal."""
    bad = candidate.model_copy(deep=True)
    bad.config_schema["properties"]["0_text"]["x-imv-target"] = pointer
    with pytest.raises(ValueError):
        validate_candidate(bad, spec)


def test_no_remote_schema(candidate, spec):
    """Remote JSON Schema references never trigger network resolution."""
    bad = candidate.model_copy(deep=True)
    bad.config_schema["$ref"] = "https://example.com/schema"
    with pytest.raises(ValueError):
        validate_candidate(bad, spec)


def test_trajectory_regression_cycle_stale_and_drift(candidate, spec):
    """Host facts distinguish regression, A-B-A cycles, stale reports and changed goals."""
    monitor = Trajectory(spec)
    first = evidence(candidate, spec, visual_style="fail")
    assert monitor.observe(candidate, spec, first).verdict == "goal_drift"
    changed = candidate.model_copy(update={"tsx_code": candidate.tsx_code + "\n"})
    second = evidence(changed, spec, visual_style="fail", typescript="fail")
    assert monitor.observe(changed, spec, second).verdict == "regression"
    assert monitor.observe(candidate, spec, first).verdict == "non_progress_cycle"
    assert monitor.observe(changed, spec, first).verdict == "stale_evidence"
    drift = spec.model_copy(update={"name": "different goal"})
    assert (
        monitor.observe(candidate, drift, evidence(candidate, drift)).verdict
        == "goal_drift"
    )
    assert monitor.observe(
        candidate, spec, evidence(candidate, spec)
    ).completion_allowed


def test_unknown_missing_and_duplicate_checks_never_complete(candidate, spec):
    """No amount of actor confidence can substitute for complete, unambiguous current evidence."""
    report = evidence(candidate, spec, visual_scope="unknown")
    assert not Trajectory(spec).observe(candidate, spec, report).completion_allowed
    report = evidence(candidate, spec)
    report.checks.pop()
    assert not report.passed
    report = evidence(candidate, spec)
    report.checks.append(report.checks[0])
    assert not Trajectory(spec).observe(candidate, spec, report).completion_allowed


@pytest.mark.parametrize(
    "names",
    [
        ["text"] * 5,
        ["visual_text", "layout", "style", "motion", "scope"],
        ["text", "layout", "style", "motion", "scope"] * 3,
    ],
)
def test_visual_review_requires_one_check_per_dimension(names):
    """Regression: reject invented prefixes and per-frame duplicates instead of silently accepting model output."""
    with pytest.raises(ValidationError):
        VisualReview.model_validate(
            {
                "checks": [
                    {"name": name, "status": "pass", "detail": "observation"}
                    for name in names
                ]
            }
        )


def publish_fixture(store, job_id, candidate, spec, report):
    """Materialize offline host evidence before exercising the real atomic publication gate."""
    directory = store.root / "fixture" / str(uuid4())
    directory.mkdir(parents=True)
    (directory / "Template.tsx").write_text(candidate.tsx_code)
    (directory / "candidate.json").write_text(candidate.model_dump_json())
    (directory / "spec.json").write_text(spec.model_dump_json())
    (directory / "preview.mp4").write_bytes(b"offline media fixture")
    if any(check.name == "interactive_bundle" for check in report.checks):
        (directory / "interactive.js").write_text(
            'const title = "</script><script>bad</script>";'
        )
        (directory / "Export.tsx").write_text(
            candidate.tsx_code + "\n// Export fixture"
        )
    for frame in report.frames:
        Image.new("RGBA", (64, 64), "white").save(directory / f"frame-{frame}.png")
    seal_artifacts(candidate, spec, report, directory)
    return store.publish(job_id, candidate, spec, report, directory)


def test_store_publication_cancel_and_history(store, candidate, spec):
    """Only validated running jobs publish; failures and late cancelled results preserve history."""
    project, job = store.create(GenerateTemplateRequest(description="title"))
    assert store.claim().id == job.id
    with pytest.raises(Conflict):
        store.enqueue(project.id, JobInput(), None)
    with pytest.raises(Conflict):
        publish_fixture(
            store, job.id, candidate, spec, evidence(candidate, spec, render="fail")
        )
    accepted = publish_fixture(
        store, job.id, candidate, spec, evidence(candidate, spec)
    )
    next_job = store.enqueue(
        project.id,
        JobInput(mode="parameters", parameters={"0_text": "new"}),
        accepted.id,
    )
    store.claim()
    store.update(next_job.id, status="cancelled", stage="finished")
    with pytest.raises(Conflict):
        publish_fixture(store, next_job.id, candidate, spec, evidence(candidate, spec))
    assert store.update(next_job.id, status="running").status == "cancelled"
    assert store.project(project.id).current_version_id == accepted.id
    assert [version.number for version in store.versions(project.id)] == [1]
    assert store.events(job.id, 0)
    cursor = store.events(job.id, 0)[-1][0]
    assert store.events(job.id, cursor) == []


def test_store_recovery_and_foreign_base(store, candidate, spec):
    """Restart marks unfinished jobs interrupted; unrelated version IDs cannot become edit bases."""
    first, job = store.create(GenerateTemplateRequest(description="first"))
    store.claim()
    version = publish_fixture(store, job.id, candidate, spec, evidence(candidate, spec))
    second, other = store.create(GenerateTemplateRequest(description="second"))
    store.interrupt_unfinished()
    assert store.job(other.id).status == "interrupted"
    with pytest.raises(Conflict):
        store.enqueue(second.id, JobInput(), version.id)
    with pytest.raises(NotFound):
        store.job(uuid4())


def test_image_normalization_and_failures(store, settings):
    """Decode actual content, normalize PNG, and reject oversized, animated or corrupt media."""
    buffer = BytesIO()
    Image.new("RGB", (2500, 20), "red").save(buffer, "JPEG")
    asset = save_image(buffer.getvalue(), store, settings)
    assert asset.width == 2048
    with Image.open(store.asset_path(asset.id)) as normalized:
        assert normalized.format == "PNG"
    for data in (b"", b"not a video or image", b"GIF89a"):
        with pytest.raises(ValueError):
            save_image(data, store, settings)
    settings.max_upload_bytes = 1
    with pytest.raises(ValueError):
        save_image(buffer.getvalue(), store, settings)


def test_model_image_preserves_transparent_original(tmp_path):
    """White text remains visible to vision on inspection gray, while downloadable alpha bytes stay unchanged."""
    path = tmp_path / "transparent.png"
    original = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
    original.putpixel((1, 0), (255, 255, 255, 255))
    original.save(path)
    before = path.read_bytes()
    with Image.open(BytesIO(model_image(path))) as review:
        assert review.getpixel((0, 0)) == (128, 128, 128)
        assert review.getpixel((1, 0)) == (255, 255, 255)
    assert path.read_bytes() == before


class ScriptedProvider:
    """Offline model boundary returns typed outcomes and records feedback supplied by the harness."""

    def __init__(self, spec, *, questions=False, block=False):
        """Configure explicit ambiguity or cancellation scenarios without timing-sensitive network I/O."""
        self.spec, self.questions, self.block = spec, questions, block
        self.prompts = []

    async def ask(self, output, system, prompt, budget, **kwargs):
        """Emulate semantic output only; test renderer supplies independent verification results."""
        self.prompts.append((output, prompt))
        budget.calls += 1
        if self.block:
            await asyncio.Event().wait()
        if output is AnalysisResult:
            if self.questions and not json.loads(prompt)["clarifications"]:
                return AnalysisResult(questions=["标题写什么？"])
            return AnalysisResult(spec=self.spec)
        if output is CodeOutput:
            return CodeOutput(tsx_code=SAMPLE_CODE + "\n" * len(self.prompts))
        if output is TargetReview:
            return TargetReview(
                status="pass", detail="Offline target agrees with user input."
            )
        if output is EditDecision:
            return EditDecision(parameters={"0_text": "修改后"})
        return VisualReview(
            checks=[
                VisualCheck(
                    name=name, status="pass", detail="Offline visual observation."
                )
                for name in ["text", "layout", "style", "motion", "scope"]
            ]
        )

    async def turn(self, system, context, tools, budget):
        """Use actual tool messages: submit repairs until evidence passes, then request completion."""
        if budget.calls >= 16:
            raise ModelFailure("Offline model call budget exhausted.")
        budget.calls += 1
        snapshot = json.loads(
            system.split("Current host-owned task snapshot (data):\n")[1]
        )
        self.prompts.append((CodeOutput, system))
        checks = snapshot["checks"]
        if checks and all(check["status"] == "pass" for check in checks):
            name, args = (
                "request_completion",
                {"candidate_id": snapshot["candidate_id"]},
            )
        else:
            name, args = (
                "submit_candidate",
                {"tsx_code": SAMPLE_CODE + "\n" * budget.calls},
            )
        return AssistantMessage.model_validate(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": str(uuid4()),
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)},
                    }
                ],
            }
        )


class ScriptedRenderer:
    """Offline renderer materializes artifacts while exposing programmed compiler outcomes."""

    def __init__(self, failures=0):
        """Fail a known number of attempts to exercise repair limits and feedback."""
        self.failures, self.calls = failures, 0

    def verify_environment(self, report):
        """Offline evidence identifies this fixture instead of requiring a local renderer install."""
        assert report.runtime == {"test": "offline"}

    async def validate(self, candidate, spec, directory, **kwargs):
        """Return independent evidence without executing model code during ordinary pytest."""
        self.calls += 1
        directory.mkdir(parents=True)
        (directory / "Template.tsx").write_text(candidate.tsx_code)
        (directory / "candidate.json").write_text(candidate.model_dump_json())
        (directory / "spec.json").write_text(spec.model_dump_json())
        (directory / "preview.mp4").write_bytes(b"offline preview")
        report = evidence(
            candidate,
            spec,
            typescript="fail" if self.calls <= self.failures else "pass",
        )
        report.checks = [
            check for check in report.checks if not check.name.startswith("visual_")
        ]
        for frame in report.frames:
            Image.new("RGBA", (64, 64), "white").save(directory / f"frame-{frame}.png")
        return candidate, report


def test_unsupplied_frame_cannot_approve_candidate(spec, tmp_path):
    """A model cannot approve evidence by citing a frame that the renderer never supplied."""

    class WrongFrameProvider(ScriptedProvider):
        """Return a plausible review with one impossible evidence reference."""

        async def ask(self, output, system, prompt, budget, **kwargs):
            """Keep valid semantic output while corrupting only a frame citation."""
            result = await super().ask(output, system, prompt, budget, **kwargs)
            if output is VisualReview:
                result.checks[0].frame = 999
            return result

    harness = Harness(WrongFrameProvider(spec), ScriptedRenderer())
    with pytest.raises(ModelFailure):
        asyncio.run(
            harness.generate(spec, Budget(), tmp_path, [], lambda stage, attempt: None)
        )
    reports = [
        ValidationReport.model_validate_json(path.read_text())
        for path in tmp_path.glob("attempt-*/validation.json")
    ]
    assert reports and all(not report.passed for report in reports)
    assert all(
        next(check for check in report.checks if check.name == "visual_text").status
        == "unknown"
        for report in reports
    )


@pytest.mark.parametrize("cancel", [False, True])
def test_renderer_timeout_and_cancellation_reap_process(
    settings, candidate, spec, tmp_path, monkeypatch, cancel
):
    """Use a real harmless subprocess to prove deadlines and cancellation stop execution without browser dependencies."""
    inputs = tmp_path / "renderer-inputs"
    inputs.mkdir()
    for name in (
        "worker.mjs",
        "presentation.mjs",
        "preview-host.tsx",
        "bun.lock",
        "font",
        "browser",
    ):
        (inputs / name).write_text("offline test fixture")
    settings.renderer_dir = inputs
    settings.font_regular = settings.font_bold = inputs / "font"
    settings.browser_executable = inputs / "browser"
    settings.render_timeout_seconds = 1
    renderer = Renderer(settings)
    pid_path = tmp_path / "worker.pid"
    # The test child records its identity, then waits until the renderer terminates it.
    script = "import os,sys,time; from pathlib import Path; Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)"
    monkeypatch.setattr(
        renderer,
        "command",
        lambda directory: [sys.executable, "-c", script, str(pid_path)],
    )

    async def scenario():
        """Wait only for the startup handshake, then exercise one explicit termination path."""
        task = asyncio.create_task(
            renderer.validate(candidate, spec, tmp_path / "attempt")
        )
        async with asyncio.timeout(3):
            while not pid_path.exists():
                await asyncio.sleep(0.01)
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                _, report = await task
                assert not report.passed
                assert any(
                    check.name == "renderer_environment" and check.status == "fail"
                    for check in report.checks
                )
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_path.read_text()), 0)

    asyncio.run(scenario())


def wait_job(client, identifier):
    """Bound all asynchronous API tests; no production or order-dependent external polling."""
    import time

    for _ in range(100):
        job = client.get(f"/api/templates/jobs/{identifier}").json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.01)
    pytest.fail("Offline job did not terminate within one second")


def test_api_generation_edit_artifacts_and_events(settings, spec):
    """Exercise actual queue and HTTP contracts through generation, parameter and natural-language edits."""
    provider, renderer = ScriptedProvider(spec), ScriptedRenderer(failures=1)
    with TestClient(
        create_app(settings, provider=provider, renderer=renderer)
    ) as client:
        response = client.post(
            "/api/templates/works",
            json={"description": "标题", "composition": spec.composition.model_dump()},
        )
        assert response.status_code == 202
        project_id, job_id = response.json()["work"]["id"], response.json()["job"]["id"]
        job = wait_job(client, job_id)
        assert job["status"] == "succeeded", job
        assert "attempts" not in job and renderer.calls == 2
        prompts = [
            prompt for output, prompt in provider.prompts if output is CodeOutput
        ]
        assert "typescript: fail" in prompts[1]
        first = client.get(f"/api/templates/versions/{job['result_version_id']}").json()
        assert client.get(f"/api/templates/jobs/{job_id}/events").json()["events"]
        listing = client.get(f"/api/templates/jobs/{job_id}/artifacts").json()
        download = next(
            item["url"] for item in listing if item["name"] == "Template.tsx"
        )
        assert client.get(download).text == first["candidate"]["tsx_code"]
        assert (
            client.get(
                f"/api/templates/jobs/{job_id}/artifacts/2/worker.log"
            ).status_code
            == 404
        )
        edit = client.post(
            f"/api/templates/works/{project_id}/messages",
            json={"parameters": {"0_text": "新标题"}},
        )
        assert edit.status_code == 202
        result = wait_job(client, edit.json()["id"])
        assert result["status"] == "succeeded", result
        second = client.get(
            f"/api/templates/versions/{result['result_version_id']}"
        ).json()
        assert second["candidate"]["tsx_code"] == first["candidate"]["tsx_code"]
        assert second["spec"]["text_layers"][0]["text"] == "新标题"
        third = client.post(
            f"/api/templates/works/{project_id}/messages",
            json={"instruction": "把文字改成修改后"},
        )
        assert wait_job(client, third.json()["id"])["status"] == "succeeded"
        review_prompt = json.loads(
            [prompt for output, prompt in provider.prompts if output is VisualReview][
                -1
            ]
        )
        assert review_prompt["user_intent"]["original_request"] is None
        assert (
            review_prompt["user_intent"]["accepted_target"]["text_layers"][0]["text"]
            == "新标题"
        )

        assert [
            v["number"]
            for v in client.get(f"/api/templates/works/{project_id}/versions").json()
        ] == [1, 2, 3]


def test_api_clarification_failure_and_retry(settings, spec):
    """Needs-input resumes explicitly, exhausted repair preserves candidates, and retry gets a new ID."""
    provider, renderer = (
        ScriptedProvider(spec, questions=True),
        ScriptedRenderer(failures=100),
    )
    with TestClient(
        create_app(settings, provider=provider, renderer=renderer)
    ) as client:
        created = client.post(
            "/api/templates/works",
            json={"description": "title", "composition": spec.composition.model_dump()},
        ).json()
        job_id = created["job"]["id"]
        assert wait_job(client, job_id)["status"] == "needs_input"
        resumed = client.post(
            f"/api/templates/works/{created['work']['id']}/messages",
            json={"instruction": "你好", "reply_to_job_id": job_id},
        )
        result = wait_job(client, resumed.json()["id"])
        assert result["status"] == "failed"
        assert "attempts" not in result and renderer.calls > 3
        assert (
            client.get(f"/api/templates/works/{created['work']['id']}").json()[
                "current_version_id"
            ]
            is None
        )
        retry = client.post(f"/api/templates/jobs/{result['id']}/retry")
        assert retry.status_code == 202 and retry.json()["id"] != result["id"]
        assert client.get(f"/api/templates/jobs/{result['id']}/artifacts").json() == []


def test_api_cancel_conflict_and_shutdown(settings, spec):
    """Cancelling a blocked model closes the active task and is safe to repeat; retry stays explicit."""
    application = create_app(
        settings,
        provider=ScriptedProvider(spec, block=True),
        renderer=ScriptedRenderer(),
    )
    with TestClient(application) as client:
        created = client.post(
            "/api/templates/works", json={"description": "title"}
        ).json()
        job_id = created["job"]["id"]
        assert client.post(f"/api/templates/jobs/{job_id}/retry").status_code == 409
        assert (
            client.post(f"/api/templates/jobs/{job_id}/cancel").json()["status"]
            == "cancelled"
        )
        assert (
            client.post(f"/api/templates/jobs/{job_id}/cancel").json()["status"]
            == "cancelled"
        )
        retried = client.post(f"/api/templates/jobs/{job_id}/retry").json()
        store = application.state.template_app.state.runtime.store
    assert store.job(retried["id"]).status == "interrupted"


def test_runtime_exclusive_directory(settings, spec):
    """A second server fails before recovery and cannot mark another worker's jobs interrupted."""
    first = Runtime(
        Store(settings.data_dir),
        Harness(ScriptedProvider(spec), ScriptedRenderer()),
        settings,
    )
    second = Runtime(Store(settings.data_dir), first.harness, settings)
    first.initialize()
    try:
        with pytest.raises(BlockingIOError):
            second.initialize()
    finally:
        first.lock.close()
    second.initialize()
    second.lock.close()


@pytest.mark.parametrize(
    "case",
    ["ok", "unauthorized", "invalid_json", "missing_usage", "truncated", "budget"],
)
def test_provider_contract_and_sanitized_errors(settings, case):
    """Verify wire credentials, JSON parsing, usage accounting and safe error responses offline."""

    def respond(request):
        """Capture the actual request and return a controlled compatible provider response."""
        assert request.headers["authorization"] == "Bearer test-private-token"
        assert json.loads(request.content)["response_format"] == {"type": "json_object"}
        if case == "unauthorized":
            return httpx.Response(401, text="test-private-token")
        payload = {
            "usage": {"total_tokens": 100},
            "choices": [
                {
                    "finish_reason": "length" if case == "truncated" else "stop",
                    "message": {
                        "role": "assistant",
                        "content": "bad JSON"
                        if case == "invalid_json"
                        else '{"questions":["Which title?"]}',
                    },
                }
            ],
        }
        if case == "missing_usage":
            del payload["usage"]
        return httpx.Response(200, json=payload)

    provider = Provider(settings, transport=httpx.MockTransport(respond))
    budget = Budget(calls=settings.max_model_calls if case == "budget" else 0)
    if case == "ok":
        result = asyncio.run(
            provider.ask(AnalysisResult, "Return JSON", "title", budget)
        )
        assert result.questions == ["Which title?"] and budget.tokens == 100
    else:
        with pytest.raises(ModelFailure) as caught:
            asyncio.run(provider.ask(AnalysisResult, "Return JSON", "title", budget))
        assert "test-private-token" not in str(caught.value)


def test_api_upload_and_unconfigured_model(settings):
    """Upload validates content independently of extension; missing models cannot accept jobs."""
    settings.actor_model = ""
    with TestClient(create_app(settings)) as client:
        assert (
            client.post(
                "/api/templates/assets",
                files={"file": ("image.png", b"invalid", "image/png")},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/templates/works", json={"description": "title"}
            ).status_code
            == 503
        )
        assert (
            client.post(
                "/api/templates/works", json={"video": {"asset_id": str(uuid4())}}
            ).status_code
            == 422
        )
        assert (
            "test-private-token" not in client.get("/api/templates/capabilities").text
        )


def test_feature_mount_preserves_other_modules(settings):
    """Existing lifecycle, application state, routes and error handlers survive missing template configuration."""
    from contextlib import asynccontextmanager

    events = []

    @asynccontextmanager
    async def legacy_lifespan(application):
        """Represent another monorepo module's existing startup and cleanup responsibilities."""
        events.append("started")
        yield
        events.append("closed")

    async def legacy_error(request: Request, error: ValueError):
        """An existing route owns its error response independently of template input validation."""
        return JSONResponse({"legacy": str(error)}, status_code=418)

    def legacy_route():
        """A preexisting endpoint exercises the host's original error handler."""
        raise ValueError("existing behavior")

    def broken_configuration():
        """A missing feature setting must never be loaded by unrelated startup or routes."""
        raise HTTPException(503, "template configuration unavailable")

    application = FastAPI(lifespan=legacy_lifespan)
    application.state.runtime = "another module owns this"
    application.add_exception_handler(ValueError, legacy_error)
    application.add_api_route("/legacy", legacy_route)
    feature = create_template_app(settings)
    application.mount("/api/templates", feature)
    assert ValueError not in feature.exception_handlers
    feature.state.build_runtime = broken_configuration
    with TestClient(application) as client:
        assert events == ["started"]
        assert client.get("/legacy").status_code == 418
        assert client.get("/legacy").json() == {"legacy": "existing behavior"}
        assert application.state.runtime == "another module owns this"
        assert client.get("/api/templates/capabilities").status_code == 503
        assert client.get("/legacy").status_code == 418
        assert client.get("/api/templates/docs").status_code == 200
        assert set(client.get("/openapi.json").json()["paths"]) == {"/legacy"}
    assert events == ["started", "closed"]


@pytest.mark.skipif(
    os.environ.get("IMV_TEST_RENDERER") != "1",
    reason="Set IMV_TEST_RENDERER=1 after installing the Linux renderer prerequisites.",
)
def test_real_isolated_renderer(settings, candidate, spec, tmp_path):
    """Render actual TSX to PNG/MP4, preserve formatted code across edits, and reject unsafe imports."""

    async def scenario():
        """Exercise worker subprocesses with no model calls or network access inside the sandbox."""
        renderer = Renderer(settings)
        output, report = await renderer.validate(candidate, spec, tmp_path / "first")
        assert all(check.status == "pass" for check in report.checks), (
            report.model_dump()
        )
        assert {
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
        } <= {check.name for check in report.checks}
        assert (tmp_path / "first" / "preview.mp4").stat().st_size > 1000
        assert {"interactive_bundle", "export_source", "export_defaults"} <= {
            check.name for check in report.checks
        }
        assert (tmp_path / "first" / "interactive.js").stat().st_size > 1000
        assert "IMVExportDefaults" in (tmp_path / "first" / "Export.tsx").read_text()
        with Image.open(tmp_path / "first" / "frame-2.png") as frame:
            assert frame.size == (320, 240)
            assert frame.convert("RGBA").getchannel("A").getbbox() is not None
        updated, target = patch_parameters(output, spec, {"0_text": "再见"})
        revised, validation = await renderer.validate(
            updated, target, tmp_path / "second", preserve_code=True
        )
        assert revised.tsx_code == output.tsx_code
        assert all(check.status == "pass" for check in validation.checks), (
            validation.model_dump()
        )
        hardcoded = candidate.model_copy(
            update={
                "tsx_code": candidate.tsx_code.replace(
                    'String(p["0_style_color"])', '"#FFFFFF"'
                )
            }
        )
        _, hardcoded_report = await renderer.validate(
            hardcoded, spec, tmp_path / "hardcoded"
        )
        assert (
            next(
                check for check in hardcoded_report.checks if check.name == "typescript"
            ).status
            == "pass"
        )
        assert (
            next(
                check for check in hardcoded_report.checks if check.name == "render"
            ).status
            == "pass"
        )
        behavior = next(
            check
            for check in hardcoded_report.checks
            if check.name == "parameter_behavior"
        )
        assert behavior.status == "fail" and "0_style_color" in behavior.detail
        unsafe = candidate.model_copy(
            update={
                "tsx_code": 'import fs from "node:fs"; export default function T(){return null;} '
            }
        )
        _, rejected = await renderer.validate(unsafe, spec, tmp_path / "unsafe")
        assert (
            next(
                check for check in rejected.checks if check.name == "source_policy"
            ).status
            == "fail"
        )
        assert not (tmp_path / "unsafe" / "preview.mp4").exists()

    asyncio.run(scenario())


@pytest.mark.skipif(
    os.environ.get("IMV_TEST_RENDERER") != "1",
    reason="Requires Linux bubblewrap and installed renderer prerequisites.",
)
def test_real_sandbox_hides_home_credentials_and_host_network(settings, tmp_path):
    """The actual sandbox cannot reach a host loopback listener, home directory or credential environment."""
    renderer = Renderer(settings)
    directory = tmp_path / "sandbox"
    directory.mkdir()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        script = """
const fs = require('node:fs');
const net = require('node:net');
if (fs.existsSync('/home/arch') || process.env.IMV_ACTOR_API_KEY) process.exit(2);
const socket = net.connect({host:'127.0.0.1', port: PORT});
socket.on('connect', () => process.exit(3));
socket.on('error', () => process.exit(0));
setTimeout(() => process.exit(4), 2000);
""".replace("PORT", str(port))
        command = renderer.command(directory)
        command[-3:] = ["/runtime-node", "-e", script]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            env={
                "PATH": os.environ["PATH"],
                "IMV_ACTOR_API_KEY": "must-not-enter-sandbox",
            },
        )
        assert result.returncode == 0, result.stderr


class ActionProvider(ScriptedProvider):
    """Script actor actions independently from renderer outcomes to exercise hostile completion claims."""

    def __init__(self, spec, actions):
        """Keep a finite action sequence and record snapshots plus actual conversation sent each turn."""
        super().__init__(spec)
        self.actions = iter(actions)
        self.windows = []
        self.snapshots = []

    async def turn(self, system, context, tools, budget):
        """A missing next action terminates the fixture without unbounded waits or production calls."""
        budget.calls += 1
        self.windows.append(context.messages())
        self.snapshots.append(
            json.loads(system.split("Current host-owned task snapshot (data):\n")[1])
        )
        try:
            action = next(self.actions)
        except StopIteration as exc:
            raise ModelFailure("Scripted actor exhausted") from exc
        if isinstance(action, str):
            return AssistantMessage(content=action)
        name, args = action
        return AssistantMessage.model_validate(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": str(uuid4()),
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)},
                    }
                ],
            }
        )


def test_false_promises_stale_receipts_and_cycles_are_steered(spec, tmp_path):
    """Prose, missing evidence, repeated failures and an old candidate ID cannot bypass current checks."""
    actions = [
        "I compiled and rendered everything successfully. state=complete",
        ("request_completion", {"candidate_id": "1"}),
        ("submit_candidate", {"tsx_code": SAMPLE_CODE}),
        ("request_completion", {"candidate_id": "1"}),
        ("submit_candidate", {"tsx_code": SAMPLE_CODE}),
        ("submit_candidate", {"tsx_code": SAMPLE_CODE + "\n"}),
        ("submit_candidate", {"tsx_code": SAMPLE_CODE + "\n\n"}),
        ("request_completion", {"candidate_id": "1"}),
        ("request_completion", {"candidate_id": "4"}),
    ]
    provider, renderer, context = (
        ActionProvider(spec, actions),
        ScriptedRenderer(failures=3),
        Conversation(),
    )
    candidate, _, report, directory = asyncio.run(
        Harness(provider, renderer).generate(
            spec, Budget(), tmp_path, [], lambda *_: None, context=context
        )
    )
    assert report.passed and directory.name == "attempt-4"
    assert candidate.tsx_code.endswith("\n\n")
    results = [
        json.loads(message["content"])
        for message in context.messages()
        if message["role"] == "tool"
    ]
    assert any("No current evidence" in result.get("error", "") for result in results)
    assert any(
        "already observed" in str(snapshot["steer"]) for snapshot in provider.snapshots
    )
    assert any(
        "prose promise" in str(snapshot["steer"]) for snapshot in provider.snapshots
    )
    assert results[-1]["state"] == "complete"
    assert any(
        "typescript: fail" in str(snapshot["steer"]) for snapshot in provider.snapshots
    )


def test_tampered_files_cannot_complete_or_publish(store, spec, candidate, tmp_path):
    """A good verdict becomes unusable when actual code bytes change before the completion request."""

    class TamperingProvider(ActionProvider):
        """Mutate evidence after rendering, simulating an intervening writer rather than model evidence."""

        async def turn(self, system, context, tools, budget):
            """Tamper just before claiming the previously validated artifact is complete."""
            if self.windows:
                (tmp_path / "run" / "attempt-1" / "Template.tsx").write_text(
                    "changed after verification"
                )
            return await super().turn(system, context, tools, budget)

    provider = TamperingProvider(
        spec,
        [
            ("submit_candidate", {"tsx_code": SAMPLE_CODE}),
            ("request_completion", {"candidate_id": "1"}),
        ],
    )
    context = Conversation()
    with pytest.raises(ModelFailure):
        asyncio.run(
            Harness(provider, ScriptedRenderer()).generate(
                spec, Budget(), tmp_path / "run", [], lambda *_: None, context=context
            )
        )
    assert "changed" in context.messages()[-1]["content"]
    report = ValidationReport.model_validate_json(
        (tmp_path / "run" / "attempt-1" / "validation.json").read_text()
    )
    project, job = store.create(GenerateTemplateRequest(description="title"))
    store.claim()
    with pytest.raises(ValueError, match="changed"):
        store.publish(job.id, candidate, spec, report, tmp_path / "run" / "attempt-1")
    assert store.project(project.id).current_version_id is None


def test_invalid_actor_and_visual_contracts_are_repaired_internally(spec, tmp_path):
    """Malformed model output becomes bounded host steer or unknown evidence, never a successful claim."""
    from server.remotion_templates.provider import ModelContractFailure

    class MalformedProvider(ScriptedProvider):
        """Fail each model role once, then permit a successful repair through real host checks."""

        def __init__(self):
            """Keep separate actor and reviewer failure counters."""
            super().__init__(spec)
            self.actor_invalid = self.review_invalid = True

        async def turn(self, system, context, tools, budget):
            """Reject an invalid call before permitting normal actor tool actions."""
            if self.actor_invalid:
                self.actor_invalid = False
                budget.calls += 1
                raise ModelContractFailure("invalid tools")
            return await super().turn(system, context, tools, budget)

        async def ask(self, output, system, prompt, budget, **kwargs):
            """A malformed reviewer response must leave unknown evidence for the next repair."""
            if output is VisualReview and self.review_invalid:
                self.review_invalid = False
                budget.calls += 1
                raise ModelContractFailure("invalid review")
            return await super().ask(output, system, prompt, budget, **kwargs)

    provider = MalformedProvider()
    _, _, report, directory = asyncio.run(
        Harness(provider, ScriptedRenderer()).generate(
            spec, Budget(), tmp_path, [], lambda *_: None
        )
    )
    assert report.passed and directory.name == "attempt-2"
    first = ValidationReport.model_validate_json(
        (tmp_path / "attempt-1" / "validation.json").read_text()
    )
    assert all(
        check.status == "unknown"
        for check in first.checks
        if check.name.startswith("visual_")
    )


def test_task_message_binding_and_private_failures(settings, spec):
    """Question replies bind to one task and cannot be replayed; public data never contains internal diagnostics."""
    provider, renderer = ScriptedProvider(spec, questions=True), ScriptedRenderer()
    application = create_app(settings, provider=provider, renderer=renderer)
    with TestClient(application) as client:
        created = client.post(
            "/api/templates/works",
            json={"description": "first", "composition": spec.composition.model_dump()},
        ).json()
        job_id, work_id = created["job"]["id"], created["work"]["id"]
        assert wait_job(client, job_id)["status"] == "needs_input"
        message_url = f"/api/templates/works/{work_id}/messages"
        assert client.post(message_url, json={"instruction": "你好"}).status_code == 409
        assert (
            client.post(
                message_url,
                json={"instruction": "你好", "reply_to_job_id": str(uuid4())},
            ).status_code
            == 409
        )
        answer = {"instruction": "你好", "reply_to_job_id": job_id}
        resumed = client.post(message_url, json=answer)
        assert resumed.status_code == 202
        result = wait_job(client, resumed.json()["id"])
        assert result["status"] == "succeeded"
        assert client.post(message_url, json=answer).status_code == 409
        accepted_id = result["result_version_id"]
        assert (
            "validation"
            not in client.get(f"/api/templates/versions/{accepted_id}").json()
        )
        events = client.get(f"/api/templates/jobs/{result['id']}/events").json()
        assert all(
            not ({"attempts", "usage", "error", "stage"} & event["job"].keys())
            for event in events["events"]
        )
        for name in (
            "validation.json",
            "trajectory.json",
            "candidate.json",
            "worker.log",
        ):
            assert (
                client.get(
                    f"/api/templates/versions/{accepted_id}/artifacts/{name}"
                ).status_code
                == 404
            )
        assert (
            client.get(
                f"/api/templates/jobs/{result['id']}/artifacts/1/Template.tsx"
            ).status_code
            == 404
        )
        renderer.failures = 100
        edit = client.post(message_url, json={"instruction": "改成修改后"})
        failed = wait_job(client, edit.json()["id"])
        assert failed["status"] == "failed" and "error" not in failed
        assert client.get(f"/api/templates/jobs/{failed['id']}/artifacts").json() == []
        assert (
            client.get(f"/api/templates/works/{work_id}").json()["current_version_id"]
            == accepted_id
        )
        assert (
            client.get(
                f"/api/templates/versions/{accepted_id}/artifacts/Template.tsx"
            ).status_code
            == 200
        )
        # Even accepted files must remain byte-identical to the sealed artifact before download.
        service = application.state.template_app.state.runtime
        (service.store.root / "accepted" / accepted_id / "Template.tsx").write_text(
            "tampered"
        )
        assert (
            client.get(
                f"/api/templates/versions/{accepted_id}/artifacts/Template.tsx"
            ).status_code
            == 404
        )


def test_render_evidence_environment_revision_cannot_be_reused(settings, tmp_path):
    """Changes to managed fonts or executables invalidate the host completion receipt."""
    from server.remotion_templates.evidence import digest

    renderer = Renderer(settings)
    settings.font_regular = settings.font_bold = tmp_path / "font"
    settings.renderer_dir = tmp_path
    settings.browser_executable = tmp_path / "browser"
    for name in ("font", "worker.mjs", "bun.lock", "browser"):
        (tmp_path / name).write_text(name)
    report = ValidationReport(
        fingerprint="",
        runtime={
            "font_400": digest(settings.font_regular),
            "font_700": digest(settings.font_bold),
        },
    )
    settings.font_regular.write_text("new font revision")
    with pytest.raises(ValueError, match="environment changed"):
        renderer.verify_environment(report)


def test_model_derived_target_is_repaired_before_freezing(spec):
    """A contradictory static/enter plan cannot become the fixed goal merely because analysis returned valid JSON."""
    from server.remotion_templates.models import MotionSegment

    class ContradictoryPlanner(ScriptedProvider):
        """First propose static visibility as an enter effect, then repair after independent rejection."""

        def __init__(self):
            """Track separate planning and verification calls and retain host feedback."""
            super().__init__(spec)
            self.plans = 0
            self.steers = []

        async def ask(self, output, system, prompt, budget, **kwargs):
            """The independent review rejects the contradictory first interpretation."""
            budget.calls += 1
            if output is AnalysisResult:
                self.plans += 1
                self.steers.append(system)
                proposed = spec.model_copy(deep=True)
                if self.plans == 1:
                    proposed.text_layers[0].motion = [
                        MotionSegment(
                            phase="enter",
                            start_frame=0,
                            end_frame=6,
                            description="全程静态，无进入动画",
                        )
                    ]
                return AnalysisResult(spec=proposed)
            assert output is TargetReview
            target = json.loads(prompt)["proposed_target"]
            if target["text_layers"][0]["motion"]:
                return TargetReview(
                    status="fail",
                    detail="Static visibility cannot be an enter animation; use an empty motion list.",
                )
            return TargetReview(
                status="pass", detail="Target now agrees with static input."
            )

    planner = ContradictoryPlanner()
    result = asyncio.run(
        Harness(planner, ScriptedRenderer()).analyze(
            json.dumps(
                {
                    "request": {
                        "description": "全程静态",
                        "composition": spec.composition.model_dump(),
                    }
                }
            ),
            Budget(),
            [],
        )
    )
    assert planner.plans == 2 and result.spec.text_layers[0].motion == []
    assert "Static visibility cannot" in planner.steers[1]


def test_unknown_target_review_never_freezes_a_goal(spec):
    """Uncertain interpretation asks the user for information before any candidate generation."""

    class UncertainPlanner(ScriptedProvider):
        """An unknown independent review leads to concrete questions, not a successful target."""

        async def ask(self, output, system, prompt, budget, **kwargs):
            """Produce an initial guess, reject its evidence, and request the missing wording."""
            budget.calls += 1
            if output is TargetReview:
                return TargetReview(
                    status="unknown", detail="Cannot read the requested wording."
                )
            if budget.calls > 1:
                assert "Cannot read" in system
                return AnalysisResult(questions=["请提供确切的标题文字。"])
            return AnalysisResult(spec=spec)

    renderer = ScriptedRenderer()
    result = asyncio.run(
        Harness(UncertainPlanner(spec), renderer).analyze(
            '{"request":{"description":"模糊的标题"}}', Budget(), []
        )
    )
    assert result.spec is None and result.questions
    assert renderer.calls == 0


def test_publication_rollback_removes_copied_output(
    store, candidate, spec, monkeypatch
):
    """A database failure after artifact copying leaves neither a public version nor an orphan accepted directory."""
    project, job = store.create(GenerateTemplateRequest(description="title"))
    store.claim()
    original_save = store._save_job

    def fail_on_publication(db, pending):
        """Fail after version insertion, forcing the real SQLite transaction to roll back."""
        if pending.status == "succeeded":
            raise OSError("simulated disk failure")
        original_save(db, pending)

    monkeypatch.setattr(store, "_save_job", fail_on_publication)
    with pytest.raises(OSError, match="simulated"):
        publish_fixture(store, job.id, candidate, spec, evidence(candidate, spec))
    assert store.project(project.id).current_version_id is None
    assert store.versions(project.id) == []
    assert list((store.root / "accepted").iterdir()) == []
    assert store.job(job.id).status == "running"


def test_trajectory_keeps_passing_checkpoint_for_later_regressions(candidate, spec):
    """An eligible candidate is still a checkpoint if the actor submits another revision before completing."""
    trajectory = Trajectory(spec)
    assert trajectory.observe(
        candidate, spec, evidence(candidate, spec)
    ).completion_allowed
    regressed = trajectory.observe(
        candidate, spec, evidence(candidate, spec, typescript="fail")
    )
    assert regressed.verdict == "regression"
    assert any(
        "typescript" in item and "regressed" in item for item in regressed.feedback
    )


def test_task_windows_are_isolated_and_replace_old_storage(tmp_path):
    """A new template task starts empty even when another task retained edits in the same database."""
    store = Store(tmp_path)
    store.initialize()
    first, _ = store.create(GenerateTemplateRequest(description="first"))
    second, _ = store.create(GenerateTemplateRequest(description="second"))
    context = store.conversation(first.id)
    for index in range(12):
        context.append([{"role": "user", "content": f"request-{index}"}])
    store.save_conversation(first.id, context)
    assert store.conversation(second.id).messages() == []
    assert len(store.conversation(first.id).groups) == 8
    context.append([{"role": "user", "content": "latest"}])
    store.save_conversation(first.id, context)
    assert store.conversation(first.id).messages()[-1]["content"] == "latest"
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 1


def test_interactive_preview_and_export_are_sealed(settings, spec, candidate, tmp_path):
    """交互预览、导出和字体均绑定成功版本；脚本闭合标签、篡改和越权路径不可绕过校验。"""
    from server.remotion_templates.evidence import digest

    application = create_app(
        settings, provider=ScriptedProvider(spec), renderer=ScriptedRenderer()
    )
    with TestClient(application) as client:
        assert client.get("/api/templates/capabilities").status_code == 200
        service = application.state.template_app.state.runtime
        work, job = service.store.create(
            GenerateTemplateRequest(description="preview fixture")
        )
        service.store.claim()
        settings.font_regular = settings.font_bold = tmp_path / "font.ttc"
        settings.font_regular.write_bytes(b"isolated font fixture")
        report = evidence(candidate, spec)
        report.checks += [
            Check(name=name, status="pass", detail="Offline fixture")
            for name in ("interactive_bundle", "export_source")
        ]
        report.runtime = {
            "font_400": digest(settings.font_regular),
            "font_700": digest(settings.font_bold),
        }
        report.fingerprint = validation_fingerprint(candidate, spec, report.runtime)
        accepted = publish_fixture(service.store, job.id, candidate, spec, report)
        base = f"/api/templates/versions/{accepted.id}"
        response = client.get(base + "/preview")
        assert response.status_code == 200
        assert "sandbox allow-scripts" in response.headers["content-security-policy"]
        assert "allow-same-origin" not in response.headers["content-security-policy"]
        assert "connect-src 'none'" in response.headers["content-security-policy"]
        assert "</script><script>bad" not in response.text
        assert "<\\/script>" in response.text
        exported = client.get(base + "/artifacts/Export.tsx")
        assert exported.status_code == 200 and "// Export fixture" in exported.text
        font = client.get(base + "/fonts/400")
        assert font.status_code == 200 and font.content == b"isolated font fixture"
        assert font.headers["access-control-allow-origin"] == "*"
        assert client.get(base + "/fonts/900").status_code == 404
        settings.font_regular.write_bytes(b"changed")
        assert client.get(base + "/fonts/400").status_code == 404
        assert client.get(base + "/artifacts/candidate.json").status_code == 404
        (
            service.store.root / "accepted" / str(accepted.id) / "interactive.js"
        ).write_text("changed")
        assert client.get(base + "/preview").status_code == 404
        assert client.get(base + "/artifacts/Export.tsx").status_code == 404
        assert service.store.project(work.id).current_version_id == accepted.id
        assert (
            client.get(f"/api/templates/versions/{uuid4()}/preview").status_code == 404
        )


def test_public_history_only_publishes_accepted_versions(store, candidate, spec):
    """Success events identify sealed versions; later failed edits retain the accepted pointer and defaults."""
    work, job = store.create(GenerateTemplateRequest(description="公开历史"))
    store.claim()
    before = store.session(work.id).cursor
    version = publish_fixture(store, job.id, candidate, spec, evidence(candidate, spec))
    events = store.work_events(work.id, before)
    assert [event.type for event in events] == [
        "job.updated",
        "version.ready",
        "message.created",
    ]
    assert events[1].data["version_id"] == str(version.id)
    assert store.session(work.id).work.current_version_id == version.id
    inputs = JobInput(mode="parameters", parameters={"0_text": "修改文字"})
    edit = store.enqueue(work.id, inputs, version.id)
    pending = store.session(work.id)
    assert pending.job.parameters == {"0_text": "修改文字"}
    assert pending.messages[-1].text == "调整模板参数。"
    cursor = pending.cursor
    store.update(edit.id, status="failed")
    failed = store.session(work.id)
    assert failed.work.current_version_id == version.id
    assert all(
        event.type != "version.ready" for event in store.work_events(work.id, cursor)
    )
    assert (
        store.version(version.id).candidate.default_config == candidate.default_config
    )
