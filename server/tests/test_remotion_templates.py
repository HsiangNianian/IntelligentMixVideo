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
from server.remotion_templates.api import create_template_app
from server.remotion_templates.context import AssistantMessage, Conversation
from server.remotion_templates.evidence import seal_artifacts
from server.remotion_templates.harness import CodeOutput, Harness, controls
from server.remotion_templates.media import save_image
from server.remotion_templates.models import (
    AnswerReview,
    Check,
    CompositionConfig,
    DialogueOutput,
    GenerateTemplateRequest,
    JobInput,
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
from server.settings import Settings

# A maintained reference component demonstrates direct props and deterministic transparent text.
SAMPLE_CODE = """/** Static editable text reference; the preview host loads managed fonts. */
import React from "react";
import {AbsoluteFill} from "remotion";
/** Render the accepted copy at normalized center coordinates. */
export default function Template(p: Record<string, string | number>) {
  return <AbsoluteFill><div style={{position: "absolute", left: Number(p["0_layout_x"])*100+"%", top: Number(p["0_layout_y"])*100+"%", width: Number(p["0_layout_width"])*100+"%", transform: `translate(-50%, -50%) rotate(${p["0_layout_rotation"]}deg)`, textAlign: String(p["0_layout_align"]) as React.CSSProperties["textAlign"], fontFamily: String(p["0_style_font_family"]), fontSize: Number(p["0_style_font_size"]), fontWeight: Number(p["0_style_font_weight"]), lineHeight: Number(p["0_style_line_height"]), letterSpacing: Number(p["0_style_letter_spacing"]), color: String(p["0_style_color"]), whiteSpace: "pre-wrap"}}>{p["0_text"]}</div></AbsoluteFill>;
}
"""


def actor_tool(name, args):
    """生成一个带唯一 ID 的离线 Actor 工具响应，走真实工具参数校验与反馈路径。"""
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
    """宿主区分退步、循环和过期证据；修正估算值并重新验收后可完成。"""
    monitor = Trajectory()
    first = evidence(candidate, spec, visual_style="fail")
    assert monitor.observe(candidate, spec, first).verdict == "repair"
    changed = candidate.model_copy(update={"tsx_code": candidate.tsx_code + "\n"})
    second = evidence(changed, spec, visual_style="fail", typescript="fail")
    assert monitor.observe(changed, spec, second).verdict == "regression"
    assert monitor.observe(candidate, spec, first).verdict == "non_progress_cycle"
    assert monitor.observe(changed, spec, first).verdict == "stale_evidence"
    drift = spec.model_copy(update={"name": "different goal"})
    assert (
        monitor.observe(candidate, drift, evidence(candidate, drift)).verdict
        == "complete"
    )
    assert monitor.observe(
        candidate, spec, evidence(candidate, spec)
    ).completion_allowed


def test_unknown_missing_and_duplicate_checks_never_complete(candidate, spec):
    """No amount of actor confidence can substitute for complete, unambiguous current evidence."""
    report = evidence(candidate, spec, visual_scope="unknown")
    assert not Trajectory().observe(candidate, spec, report).completion_allowed
    report = evidence(candidate, spec)
    report.checks.pop()
    assert not report.passed
    report = evidence(candidate, spec)
    report.checks.append(report.checks[0])
    assert not Trajectory().observe(candidate, spec, report).completion_allowed


@pytest.mark.parametrize("delta, allowed", [(1, True), (255, False)])
def test_consistency_measurements_reach_trajectory_steer(
    candidate, spec, tmp_path, delta, allowed
):
    """稀疏噪声不触发修复；真实重复帧差异阻止完成并原样传递测量值及修复建议。"""
    from server.remotion_templates.image_comparison import consistency_checks

    image = Image.new(
        "RGBA", (spec.composition.width, spec.composition.height), "white"
    )
    for name in ("frame-0.png", "frame-2.png", "export-default.png"):
        image.save(tmp_path / name)
    image.putpixel((20, 20), (255 - delta, 255, 255, 255))
    image.save(tmp_path / "repeat.png")
    measured = consistency_checks(tmp_path, spec, [0, 2, 5])
    report = evidence(candidate, spec)
    report.checks = [
        check for check in report.checks if check.name != "determinism"
    ] + measured
    assessment = Trajectory().observe(candidate, spec, report)
    assert assessment.completion_allowed is allowed
    if allowed:
        assert not assessment.feedback
    else:
        steer = " ".join(assessment.feedback)
        assert all(
            value in steer
            for value in (
                "determinism: fail",
                "Frame 2",
                "repeat.png",
                "255/255",
                "bbox=(20, 20, 21, 21)",
                "Remotion frame",
            )
        )


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
        if output is CodeOutput:
            return CodeOutput(
                tsx_code=SAMPLE_CODE + "\n" * len(self.prompts), spec=self.spec
            )
        if output is AnswerReview:
            return AnswerReview(
                status="pass", detail="Offline answer agrees with user input."
            )
        intent = json.loads(prompt).get("user_intent") or {}
        source = (
            "/original_request/description"
            if (intent.get("original_request") or {}).get("description")
            else "/instruction"
        )
        quote = (intent.get("original_request") or {}).get("description") or intent.get(
            "instruction"
        )
        return VisualReview(
            checks=[
                VisualCheck(
                    name=name,
                    status="pass",
                    detail="Offline visual observation.",
                    requirement_source=source,
                    requirement_quote=quote,
                    target=self.spec.text_layers[0].id,
                    observed="Observed fixture mismatch",
                    mismatch="Explicit requested property differs",
                )
                for name in ["text", "layout", "style", "motion", "scope"]
            ]
        )

    async def turn(self, system, context, tools, budget, **kwargs):
        """Use actual tool messages: submit repairs until the host accepts current evidence."""
        if budget.calls >= 16:
            raise ModelFailure("Offline model call budget exhausted.")
        budget.calls += 1
        snapshot = json.loads(
            system.split("Current host-owned task snapshot (data):\n")[1]
        )
        self.prompts.append((CodeOutput, system))
        if self.block:
            await asyncio.Event().wait()
        intent = snapshot.get("user_intent") or {}
        if (
            self.questions
            and not intent.get("clarifications")
            and not intent.get("accepted_base")
        ):
            name, args = "respond", {"questions": ["标题写什么？"]}
        else:
            proposed = self.spec.model_copy(deep=True)
            if intent.get("instruction") and intent.get("accepted_base"):
                proposed.text_layers[0].text = "修改后"
            name, args = (
                "submit_candidate",
                {
                    "tsx_code": SAMPLE_CODE + "\n" * budget.calls,
                    "spec": proposed.model_dump(),
                },
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


def test_review_image_positions_map_to_actual_frames(candidate, spec, tmp_path):
    """参考图在前且采样帧不连续时，视觉模型按显式映射引用真实帧号。"""

    class MappedFrameProvider(ScriptedProvider):
        """用生产请求中的图片映射选择证据，避免把图片序号误作帧号。"""

        async def ask(self, output, system, prompt, budget, **kwargs):
            """引用最后一张帧图并核对参考图偏移和实际传图数量。"""
            result = await super().ask(output, system, prompt, budget, **kwargs)
            if output is VisualReview:
                request = json.loads(prompt)
                assert request["frame_images"] == [
                    {"image_position": 3, "frame": 0},
                    {"image_position": 4, "frame": 2},
                    {"image_position": 5, "frame": 5},
                ]
                assert len(kwargs["images"]) == 5
                for check in result.checks:
                    check.frame = request["frame_images"][-1]["frame"]
            return result

    references = [tmp_path / "reference-1.png", tmp_path / "reference-2.png"]
    for path in references:
        Image.new("RGB", (64, 64), "white").save(path)
    harness = Harness(MappedFrameProvider(spec), ScriptedRenderer())
    _, report, _ = asyncio.run(
        harness.inspect(candidate, spec, tmp_path / "attempt", references, Budget())
    )
    assert report.passed
    assert all(
        check.frame == 5 for check in report.checks if check.source == "visual_model"
    )


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
        "node",
        "ffprobe",
    ):
        (inputs / name).write_text("offline test fixture")
    settings.renderer_dir = inputs
    settings.font_regular = settings.font_bold = inputs / "font"
    settings.browser_executable = inputs / "browser"
    # Fingerprinting uses fixture files; only the harmless Python child executes.
    monkeypatch.setattr(
        "server.remotion_templates.renderer.shutil.which",
        lambda name: str(inputs / name),
    )
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
        try:
            async with asyncio.timeout(3):
                while not pid_path.exists():
                    if task.done():
                        _, report = await task
                        pytest.fail(f"Child did not start: {report.model_dump_json()}")
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
        finally:
            # A failed handshake must not leave a task or child running past the test.
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
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
        assert review_prompt["user_intent"]["original_request"]["description"]
        assert (
            review_prompt["user_intent"]["accepted_base"]["text_layers"][0]["text"]
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


@pytest.mark.parametrize("mode", ["generate", "repair", "answer"])
def test_runtime_publishes_actual_phase_chain(settings, spec, mode):
    """真实 Runtime/Harness 把生成、布局修复及问答分支写成公开阶段，模型原文留在私有审计。"""

    class ProgressProvider(ScriptedProvider):
        """控制独立视觉失败或纯回答，不直接生成公开事件。"""

        reviews = 0

        async def turn(self, system, context, tools, budget, **kwargs):
            """纯提问直接由 Actor 回复，不生成候选。"""
            if mode == "answer":
                budget.calls += 1
                return actor_tool("respond", {"answer": "可以帮你制作字效。"})
            return await super().turn(system, context, tools, budget, **kwargs)

        async def ask(self, output, system, prompt, budget, **kwargs):
            """先布局失败再通过；问答绕过所有候选与渲染。"""
            result = await super().ask(output, system, prompt, budget, **kwargs)
            if output is VisualReview:
                self.reviews += 1
                if mode == "repair" and self.reviews == 1:
                    result.checks[1].status = "fail"
                    result.checks[1].detail = "private layout diagnosis"
            return result

    renderer = ScriptedRenderer()
    with TestClient(
        create_app(settings, provider=ProgressProvider(spec), renderer=renderer)
    ) as client:
        created = client.post(
            "/api/templates/works",
            json={"description": "标题", "composition": spec.composition.model_dump()},
        ).json()
        result = wait_job(client, created["job"]["id"])
        assert result["status"] == ("answered" if mode == "answer" else "succeeded")
        snap = client.get(
            f"/api/templates/works/{created['work']['id']}/session"
        ).json()
        steps = snap["job"]["progress"]
        expected = (
            ["understanding", "answering"]
            if mode == "answer"
            else [
                "understanding",
                "rendering",
                "reviewing",
            ]
        )
        if mode == "repair":
            expected += ["adjusting_layout", "rendering", "reviewing"]
        if mode != "answer":
            expected += ["preparing"]
        assert [step["phase"] for step in steps] == expected
        assert all(step["status"] == "done" and step["ended_at"] for step in steps)
        assert renderer.calls == {"generate": 1, "repair": 2, "answer": 0}[mode]
        assert "private layout diagnosis" not in json.dumps(snap)
        assert snap["jobs"][0]["progress"] == steps
        service = client.app.state.template_app.state.runtime
        private_job = service.store.job(result["id"])
        assert "planner_calls" not in private_job.usage
        assert private_job.usage["actor_calls"] >= 1
        window = service.store.conversation(created["work"]["id"]).messages()
        assert sum(message["role"] == "user" for message in window) == 1
        assert "image_url" not in json.dumps(window)


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
@pytest.mark.parametrize("enforced", [False, True])
def test_provider_contract_and_sanitized_errors(settings, case, enforced):
    """预算开关只影响额度和用量缺失；鉴权、JSON、截断检查及错误脱敏始终生效。"""
    settings.enforce_model_budget = enforced

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
    if case == "ok" or (not enforced and case in {"missing_usage", "budget"}):
        result = asyncio.run(
            provider.ask(DialogueOutput, "Return JSON", "title", budget)
        )
        assert result.questions == ["Which title?"]
        assert budget.tokens == (0 if case == "missing_usage" else 100)
    else:
        with pytest.raises(ModelFailure) as caught:
            asyncio.run(provider.ask(DialogueOutput, "Return JSON", "title", budget))
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
        output, report = await renderer.validate(
            candidate, spec, tmp_path / "first", extra_frames=[1, 3]
        )
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
        seal_artifacts(output, spec, report, tmp_path / "first")
        assert {1, 3} <= set(report.frames)
        assert {"frame-1.png", "frame-3.png"} <= report.artifacts.keys()
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

    async def turn(self, system, context, tools, budget, **kwargs):
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


@pytest.mark.parametrize("outcome", ["success", "cancelled", "environment"])
def test_host_finalizes_without_another_actor_call(
    store, settings, spec, candidate, monkeypatch, outcome
):
    """复现验收通过后预算已用完：无需模型确认即可发布，取消和环境变化仍保留旧版本。"""
    project, first = store.create(GenerateTemplateRequest(description="标题"))
    store.claim()
    accepted = publish_fixture(
        store, first.id, candidate, spec, evidence(candidate, spec)
    )
    job = store.enqueue(
        project.id, JobInput(mode="edit", instruction="拉开文字间距"), accepted.id
    )
    store.claim()
    revised = spec.model_copy(deep=True)
    revised.text_layers[0].layout.y = 0.65

    class LastCallProvider(ActionProvider):
        """候选提交用满 Actor token 额度，额外确认调用必然失败。"""

        async def turn(self, system, context, tools, budget, **kwargs):
            """只允许候选调用，退休的完成工具不再提供给模型。"""
            assert {tool["function"]["name"] for tool in tools} == {
                "read_current_template",
                "submit_candidate",
                "respond",
            }
            result = await super().turn(system, context, tools, budget, **kwargs)
            budget.tokens += settings.max_actor_tokens
            return result

    provider = LastCallProvider(
        spec,
        [("submit_candidate", {"spec": revised.model_dump(), "tsx_code": SAMPLE_CODE})],
    )
    provider.settings = settings
    renderer = ScriptedRenderer()
    progress = store.progress

    def prepare(job_id, phase):
        """真实进度进入收尾后注入取消或环境变更，不修改验收结论。"""
        progress(job_id, phase)
        if phase == "preparing" and outcome == "cancelled":
            store.update(job_id, status="cancelled", stage="finished")
        if phase == "preparing" and outcome == "environment":
            monkeypatch.setattr(renderer, "verify_environment", reject_environment)

    def reject_environment(report):
        """模拟已验收的运行环境指纹发生变化。"""
        raise ValueError("render environment changed")

    monkeypatch.setattr(store, "progress", prepare)
    asyncio.run(Runtime(store, Harness(provider, renderer), settings)._execute(job.id))
    actual = store.job(job.id)
    assert (
        actual.status
        == {"success": "succeeded", "cancelled": "cancelled", "environment": "failed"}[
            outcome
        ]
    )
    assert actual.usage["actor_calls"] == 1
    assert actual.usage["actor_tokens"] == settings.max_actor_tokens
    assert len(provider.prompts) == 1
    if outcome != "cancelled":
        assert actual.usage["judge_calls"] == 1
    assert len(provider.windows) == 1
    messages = store.conversation(project.id).messages()
    assert messages[-1]["role"] == "tool"
    assert json.loads(messages[-1]["content"])["eligible"] is True
    if outcome == "success":
        assert store.project(project.id).current_version_id == actual.result_version_id
        assert store.version(actual.result_version_id).spec == revised
        assert len(store.versions(project.id)) == 2
        assert store.session(project.id).job.progress[-1].phase == "preparing"
        assert store.session(project.id).job.progress[-1].status == "done"
    else:
        assert actual.result_version_id is None
        assert store.project(project.id).current_version_id == accepted.id
        assert len(store.versions(project.id)) == 1


def test_batch_uses_latest_candidate_evidence(spec, tmp_path):
    """同一工具批次先通过、后失败时不得发布前一个候选，必须修复最新候选。"""

    class BatchProvider(ActionProvider):
        """第一轮返回两次提交，第二轮才能修复被替换的候选。"""

        async def turn(self, system, context, tools, budget, **kwargs):
            """保留真实双工具回执，在下一轮检查失败反馈。"""
            response = await super().turn(system, context, tools, budget, **kwargs)
            if len(self.windows) == 1:
                response.tool_calls.extend(
                    actor_tool(
                        "submit_candidate", {"tsx_code": SAMPLE_CODE + "\n"}
                    ).tool_calls
                )
            return response

    class SecondFails(ScriptedRenderer):
        """仅第二次渲染失败，检验当前候选而不是历史通过状态。"""

        async def validate(self, *args, **kwargs):
            """第一次和修复后的第三次通过。"""
            self.failures = 2 if self.calls == 1 else 0
            return await super().validate(*args, **kwargs)

    provider = BatchProvider(
        spec,
        [
            ("submit_candidate", {"tsx_code": SAMPLE_CODE}),
            ("submit_candidate", {"tsx_code": SAMPLE_CODE + "\n\n"}),
        ],
    )
    context = Conversation()
    _, _, report, directory = asyncio.run(
        Harness(provider, SecondFails()).generate(
            spec, Budget(), tmp_path, [], lambda *_: None, context=context
        )
    )
    assert report.passed and directory.name == "attempt-3"
    assert len(provider.windows) == 2
    assert "typescript: fail" in " ".join(provider.snapshots[1]["steer"])
    receipts = [
        json.loads(m["content"]) for m in context.messages() if m["role"] == "tool"
    ]
    assert [r["eligible"] for r in receipts] == [True, False, True]


def test_actor_repairs_estimated_layout_without_planner(spec, tmp_path):
    """Actor 根据实际失败修订估算布局，宿主验收新方案后直接完成。"""
    revised = spec.model_copy(deep=True)
    revised.text_layers[0].layout.y = 0.65
    revised.text_layers[0].style.font_size = 42
    provider = ActionProvider(
        spec,
        [
            ("submit_candidate", {"spec": spec.model_dump(), "tsx_code": SAMPLE_CODE}),
            (
                "submit_candidate",
                {"spec": revised.model_dump(), "tsx_code": SAMPLE_CODE},
            ),
        ],
    )
    renderer = ScriptedRenderer(failures=1)
    _, actual, report, _ = asyncio.run(
        Harness(provider, renderer).generate(
            None,
            Budget(),
            tmp_path,
            [],
            lambda *_: None,
            intent={
                "original_request": {
                    "description": "清晰可读的标题",
                    "composition": spec.composition.model_dump(),
                }
            },
        )
    )
    assert actual == revised and report.passed and renderer.calls == 2
    assert all(output is VisualReview for output, _ in provider.prompts)
    assert len(provider.snapshots) == 2
    assert "typescript: fail" in " ".join(provider.snapshots[-1]["steer"])


def test_estimated_static_plan_cannot_hide_user_requested_animation(
    candidate, spec, tmp_path
):
    """Actor 的空 motion 只能描述实现，不能把用户明确要求的动画改判为无需动画。"""

    class MissingAnimation(ScriptedProvider):
        """基于用户要求报告缺少动画，其他维度通过。"""

        async def ask(self, output, system, prompt, budget, **kwargs):
            """结果 Judge 返回有效负面结论，宿主不能用模型 spec 覆盖它。"""
            result = await super().ask(output, system, prompt, budget, **kwargs)
            result.checks[3].status = "fail"
            result.checks[
                3
            ].detail = "User requested a fade but actual frames are static."
            return result

    _, report, _ = asyncio.run(
        Harness(MissingAnimation(spec), ScriptedRenderer()).inspect(
            candidate,
            spec,
            tmp_path / "candidate",
            [],
            Budget(),
            intent={"original_request": {"description": "标题淡入淡出"}},
        )
    )
    assert not report.passed
    assert next(c for c in report.checks if c.name == "visual_motion").status == "fail"


def test_actor_cannot_change_explicit_composition(spec, tmp_path):
    """估算布局允许修正，但结构化的用户画布尺寸仍由宿主严格保护。"""
    invalid = spec.model_copy(deep=True)
    invalid.composition.width = 640
    provider = ActionProvider(
        spec,
        [
            (
                "submit_candidate",
                {"spec": invalid.model_dump(), "tsx_code": SAMPLE_CODE},
            ),
            ("submit_candidate", {"spec": spec.model_dump(), "tsx_code": SAMPLE_CODE}),
        ],
    )
    renderer = ScriptedRenderer()
    _, actual, _, _ = asyncio.run(
        Harness(provider, renderer).generate(
            None,
            Budget(),
            tmp_path,
            [],
            lambda *_: None,
            intent={"original_request": {"composition": spec.composition.model_dump()}},
        )
    )
    assert actual.composition == spec.composition and renderer.calls == 1
    assert "user-specified composition" in " ".join(provider.snapshots[1]["steer"])


@pytest.mark.skipif(
    os.environ.get("IMV_TEST_RENDERER") != "1",
    reason="Requires isolated Chromium renderer.",
)
def test_real_actor_revises_layout_and_renders_requested_fade(settings, spec, tmp_path):
    """真实渲染验证 Actor 同次提交新方案和帧动画，宿主基于修订参数验收，无 Planner 或目标预审。"""
    from server.remotion_templates.models import MotionSegment

    revised = spec.model_copy(deep=True)
    revised.text_layers[0].style.font_size = 42
    revised.text_layers[0].layout.y = 0.6
    revised.text_layers[0].motion = [
        MotionSegment(phase="enter", start_frame=0, end_frame=3, description="淡入"),
        MotionSegment(phase="exit", start_frame=3, end_frame=6, description="淡出"),
    ]
    code = (
        SAMPLE_CODE.replace(
            "{AbsoluteFill}", "{AbsoluteFill, interpolate, useCurrentFrame}"
        )
        .replace(
            "  return <AbsoluteFill>",
            "  const opacity = interpolate(useCurrentFrame(), [0, 2, 3, 5], [0, 1, 1, 0]);\n  return <AbsoluteFill>",
        )
        .replace('position: "absolute",', 'opacity, position: "absolute",')
    )
    provider = ActionProvider(
        spec,
        [
            ("submit_candidate", {"spec": revised.model_dump(), "tsx_code": code}),
        ],
    )
    budget = Budget()
    candidate, actual, report, directory = asyncio.run(
        Harness(provider, Renderer(settings)).generate(
            spec,
            budget,
            tmp_path / "render",
            [],
            lambda *_: None,
            intent={
                "original_request": {
                    "description": "标题淡入淡出",
                    "composition": spec.composition.model_dump(),
                }
            },
        )
    )
    assert report.passed and actual == revised
    assert candidate.default_config["0_style_font_size"] == 42
    assert (directory / "preview.mp4").is_file()
    assert budget.summary()["actor_calls"] == 1 and budget.summary()["judge_calls"] == 1


def test_false_promises_retired_tools_and_cycles_are_steered(spec, tmp_path):
    """普通完成声明、旧完成工具和重复失败不能绕过当前候选验收。"""
    actions = [
        "I compiled and rendered everything successfully. state=complete",
        ("request_completion", {"candidate_id": "1"}),
        ("submit_candidate", {"tsx_code": SAMPLE_CODE}),
        ("request_completion", {"candidate_id": "1"}),
        ("submit_candidate", {"tsx_code": SAMPLE_CODE}),
        ("submit_candidate", {"tsx_code": SAMPLE_CODE + "\n"}),
        ("submit_candidate", {"tsx_code": SAMPLE_CODE + "\n\n"}),
        ("request_completion", {"candidate_id": "1"}),
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
    assert any("Unknown tool" in result.get("error", "") for result in results)
    assert any(
        "already observed" in str(snapshot["steer"]) for snapshot in provider.snapshots
    )
    assert any(
        "prose promise" in str(snapshot["steer"]) for snapshot in provider.snapshots
    )
    assert results[-1]["eligible"] is True
    assert any(
        "typescript: fail" in str(snapshot["steer"]) for snapshot in provider.snapshots
    )


def test_tampered_files_cannot_complete_or_publish(store, spec, candidate, tmp_path):
    """验收后进入自动收尾时文件被篡改，Harness 和 Store 都必须拒绝发布。"""

    def tamper(phase):
        """验收结束进入宿主收尾时模拟文件被外部改写。"""
        if phase == "preparing":
            (tmp_path / "run" / "attempt-1" / "Template.tsx").write_text(
                "changed after verification"
            )

    provider = ActionProvider(spec, [("submit_candidate", {"tsx_code": SAMPLE_CODE})])
    context = Conversation()
    with pytest.raises(ValueError, match="changed"):
        asyncio.run(
            Harness(provider, ScriptedRenderer()).generate(
                spec,
                Budget(on_progress=tamper),
                tmp_path / "run",
                [],
                lambda *_: None,
                context=context,
            )
        )
    assert context.messages()[-1]["role"] == "tool"
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

        async def turn(self, system, context, tools, budget, **kwargs):
            """Reject an invalid call before permitting normal actor tool actions."""
            if self.actor_invalid:
                self.actor_invalid = False
                budget.calls += 1
                raise ModelContractFailure("invalid tools")
            return await super().turn(system, context, tools, budget)

        async def ask(self, output, system, prompt, budget, **kwargs):
            """A malformed reviewer response is corrected against the same rendered candidate."""
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
    assert report.passed and directory.name == "attempt-1"
    first = ValidationReport.model_validate_json(
        (tmp_path / "attempt-1" / "validation.json").read_text()
    )
    assert all(
        check.status == "pass"
        for check in first.checks
        if check.name.startswith("visual_")
    )


def test_contradictory_review_is_corrected_without_rewriting_candidate(spec, tmp_path):
    """复现 58fa 任务：静态误判与 scope 状态矛盾只纠正评审，不能要求重写已验证代码。"""

    class ContradictoryProvider(ScriptedProvider):
        """第一次评审返回实际故障形态，第二次根据同一份证据纠正 scope。"""

        reviews = 0

        async def ask(self, output, system, prompt, budget, **kwargs):
            """静态检查由宿主负责；矛盾纠错不丢失原始用户目标和帧引用。"""
            result = await super().ask(output, system, prompt, budget, **kwargs)
            if output is VisualReview:
                self.reviews += 1
                if self.reviews == 1:
                    result.checks[3].status = "fail"
                    result.checks[
                        3
                    ].detail = "Frames are identical, correct for a static hold. Empty motion does not prove authored intent. Status is unknown."
                    result.checks[4].status = "fail"
                    result.checks[
                        4
                    ].detail = "Only text decorations are present, so scope is within typography-only bounds. Status is pass."
                else:
                    assert "correction" in json.loads(prompt)
            return result

    provider, renderer = ContradictoryProvider(spec), ScriptedRenderer()
    _, _, report, directory = asyncio.run(
        Harness(provider, renderer).generate(
            spec,
            Budget(),
            tmp_path,
            [],
            lambda *_: None,
        )
    )
    assert report.passed and provider.reviews == 2
    assert renderer.calls == 1 and directory.name == "attempt-1"


def test_idle_actor_stops_with_audit_before_global_budget(spec, tmp_path):
    """复现 58fa 最后八轮等待：无新证据时有限 steer 后停止，保留逐轮原因与成本。"""
    from server.remotion_templates.provider import ExecutionFailure

    provider = ActionProvider(spec, ["Waiting."] * 20)
    budget = Budget()
    harness = Harness(provider, ScriptedRenderer())
    with pytest.raises(ExecutionFailure) as exc:
        asyncio.run(harness.generate(spec, budget, tmp_path, [], lambda *_: None))
    assert exc.value.code == "no_progress"
    assert budget.calls == harness.settings.max_no_progress_turns
    assert budget.summary()["actor_calls"] == budget.calls
    events = [
        json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()
    ]
    assert (
        len([event for event in events if event["event"] == "actor_response"])
        == budget.calls
    )
    assert events[-1]["event"] == "run_stalled"
    assert any("No user input is pending" in str(event) for event in events)


@pytest.mark.parametrize("moving_location", [False, True])
def test_changed_source_without_new_evidence_is_not_progress(
    spec, tmp_path, moving_location
):
    """修改注释、空白或编译错误行列号不会重置无进展计数。"""
    from server.remotion_templates.provider import ExecutionFailure

    class CompilerRenderer(ScriptedRenderer):
        """模拟源码移动后同一个编译错误出现在不同位置。"""

        async def validate(self, candidate, spec, directory, **kwargs):
            """保留真实检查结果，只改变 TypeScript 诊断的位置。"""
            output, report = await super().validate(
                candidate, spec, directory, **kwargs
            )
            if moving_location:
                next(
                    c for c in report.checks if c.name == "typescript"
                ).detail = f"Export.tsx({self.calls},11): error TS2740: missing property 'title'."
            return output, report

    actions = [
        ("submit_candidate", {"tsx_code": SAMPLE_CODE + "\n" * i}) for i in range(20)
    ]
    provider, renderer = ActionProvider(spec, actions), CompilerRenderer(failures=100)
    harness = Harness(provider, renderer)
    with pytest.raises(ExecutionFailure, match="no new action evidence"):
        asyncio.run(harness.generate(spec, Budget(), tmp_path, [], lambda *_: None))
    assert renderer.calls == 1 + harness.settings.max_no_progress_turns


def test_compiler_steer_preserves_diagnostics_and_explains_contract(spec, tmp_path):
    """编译失败回执和下一轮快照说明契约权威，保留原始诊断，修复通过后才交付。"""
    provider, renderer = ScriptedProvider(spec), ScriptedRenderer(failures=1)
    _, _, report, _ = asyncio.run(
        Harness(provider, renderer).generate(
            spec, Budget(), tmp_path, [], lambda *_: None
        )
    )
    assert report.passed and renderer.calls == 2
    snapshots = [
        json.loads(prompt.split("Current host-owned task snapshot (data):\n")[1])
        for output, prompt in provider.prompts
        if output is CodeOutput
    ]
    failed = snapshots[1]
    original = next(c["detail"] for c in failed["checks"] if c["name"] == "typescript")
    assert original in " ".join(failed["steer"])
    assert "config_schema/default_props" in failed["steer"][0]
    assert "declarations and property reads" in failed["steer"][0]
    assert (failed["config_schema"], failed["default_props"]) == controls(spec)
    events = [
        json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()
    ]
    receipts = [e["result"] for e in events if e["event"] == "tool_result"]
    assert receipts[0]["steer"] == failed["steer"]
    assert receipts[-1]["steer"] == []


def test_real_negative_review_is_not_retried_into_pass(candidate, spec, tmp_path):
    """合法文字错误原样交给 Actor；不能因为不满意负面结果反复询问 Judge。"""

    class NegativeProvider(ScriptedProvider):
        """返回有证据的合法负面结论。"""

        async def ask(self, output, system, prompt, budget, **kwargs):
            """只修改文字维度，其他维度保留正常结果。"""
            result = await super().ask(output, system, prompt, budget, **kwargs)
            if output is VisualReview:
                result.checks[0].status = "fail"
                result.checks[0].detail = "Frame 0 displays the wrong title wording."
                result.checks[0].frame = 0
            return result

    provider = NegativeProvider(spec)
    _, report, _ = asyncio.run(
        Harness(provider, ScriptedRenderer()).inspect(
            candidate,
            spec,
            tmp_path / "attempt",
            [],
            Budget(),
            intent={"instruction": "标题"},
        )
    )
    assert not report.passed
    assert len(provider.prompts) == 1
    assert next(c for c in report.checks if c.name == "visual_text").status == "fail"


@pytest.mark.parametrize("invalid_first", [False, True])
def test_full_requirement_path_reaches_actor_repair(spec, tmp_path, invalid_first):
    """复现 3fcdbbda：布局 fail 经路径校验后交给 Actor，新候选重新渲染通过才结束。"""

    class LayoutProvider(ScriptedProvider):
        """在模型边界返回完整用户引用；可先给错误路径以检查同证据协议纠错。"""

        reviews = 0
        actor_snapshots = []
        first_payload = None

        async def turn(self, system, context, tools, budget, **kwargs):
            """记录 Actor 实际收到的 steer，第二轮必须携带有依据的布局失败。"""
            snapshot = json.loads(
                system.split("Current host-owned task snapshot (data):\n")[1]
            )
            self.actor_snapshots.append(snapshot)
            return await super().turn(system, context, tools, budget, **kwargs)

        async def ask(self, output, system, prompt, budget, **kwargs):
            """旧候选始终返回布局失败，只有 Actor 提交新候选之后才允许通过。"""
            result = await super().ask(output, system, prompt, budget, **kwargs)
            if output is VisualReview:
                self.reviews += 1
                payload = json.loads(prompt)
                if len(self.actor_snapshots) == 1:
                    result.checks[1] = VisualCheck(
                        name="layout",
                        status="fail",
                        frame=0,
                        detail="文字层重叠，最新修改尚未解决。",
                        requirement_source=(
                            "/user_intent/user_intent/instruction"
                            if invalid_first and self.reviews == 1
                            else "/user_intent/instruction"
                        ),
                        requirement_quote="图层之间都挤在一起了，你仔细检查",
                        target="canvas",
                        observed="标题覆盖日期文字",
                        mismatch="没有消除用户指出的文字重叠",
                    )
                    if self.reviews > 1:
                        correction = payload["correction"]
                        assert "/user_intent/user_intent/instruction" in str(
                            correction["errors"]
                        )
                        assert "/user_intent/instruction" in str(correction["errors"])
                        assert (
                            payload["candidate_plan"]
                            == self.first_payload["candidate_plan"]
                        )
                        assert (
                            payload["frame_images"]
                            == self.first_payload["frame_images"]
                        )
                    self.first_payload = payload
                else:
                    assert "correction" not in payload
            return result

    provider, renderer = LayoutProvider(spec), ScriptedRenderer()
    _, _, report, directory = asyncio.run(
        Harness(provider, renderer).generate(
            spec,
            Budget(),
            tmp_path,
            [],
            lambda *_: None,
            intent={"instruction": "图层之间都挤在一起了，你仔细检查"},
        )
    )
    assert report.passed and directory.name == "attempt-2"
    assert renderer.calls == 2 and len(provider.actor_snapshots) == 2
    assert provider.reviews == (3 if invalid_first else 2)
    assert "标题覆盖日期文字" in str(provider.actor_snapshots[1]["steer"])
    initial = ValidationReport.model_validate_json(
        (tmp_path / "attempt-1/validation.json").read_text()
    )
    assert not initial.passed
    assert next(c for c in initial.checks if c.name == "visual_layout").status == "fail"


@pytest.mark.parametrize("prefix", ["", "/user_intent"])
def test_valid_failure_survives_correction_of_other_dimension(
    candidate, spec, tmp_path, prefix
):
    """scope 协议纠错时保留此前合法文字失败，不允许整批重审把它洗成通过。"""

    class MixedProvider(ScriptedProvider):
        """第一批有一个真实错误和一个矛盾；第二批试图把两者都改为通过。"""

        async def ask(self, output, system, prompt, budget, **kwargs):
            """使用请求次数决定两次评审结果。"""
            result = await super().ask(output, system, prompt, budget, **kwargs)
            result.checks[0].requirement_source = (
                prefix + result.checks[0].requirement_source
            )
            if len(self.prompts) == 1:
                result.checks[0].status = "fail"
                result.checks[0].detail = "Frame 0 shows incorrect wording."
                result.checks[4].status = "fail"
                result.checks[4].detail = "Typography-only scope. Status is pass."
            return result

    provider = MixedProvider(spec)
    _, report, _ = asyncio.run(
        Harness(provider, ScriptedRenderer()).inspect(
            candidate,
            spec,
            tmp_path / "attempt",
            [],
            Budget(),
            intent={"instruction": "标题"},
        )
    )
    assert len(provider.prompts) == 2
    assert next(c for c in report.checks if c.name == "visual_text").status == "fail"
    assert next(c for c in report.checks if c.name == "visual_scope").status == "pass"


def test_missing_visual_evidence_is_captured_without_actor_rewrite(
    candidate, spec, tmp_path
):
    """Judge 缺少指定帧时由宿主补采样，复核相同代码并返回真实证据目录。"""

    class SupplementalRenderer(ScriptedRenderer):
        """生成请求中的补充帧，模拟隔离渲染边界。"""

        async def validate(self, candidate, spec, directory, **kwargs):
            """保留源码并把新增帧纳入当前报告和后续清单。"""
            output, report = await super().validate(
                candidate, spec, directory, **kwargs
            )
            for frame in kwargs.get("extra_frames", []):
                report.frames.append(frame)
                Image.new("RGBA", (64, 64), "white").save(
                    directory / f"frame-{frame}.png"
                )
            report.frames = sorted(set(report.frames))
            return output, report

    class EvidenceProvider(ScriptedProvider):
        """只有收到真实补充帧才给出已知结论。"""

        async def ask(self, output, system, prompt, budget, **kwargs):
            """首次缺帧，后续从实际模型输入核对帧号及图片路径。"""
            result = await super().ask(output, system, prompt, budget, **kwargs)
            frames = json.loads(prompt)["frames"]
            if 1 not in frames:
                result.checks[1].status = "unknown"
                result.checks[1].missing_evidence = ["Need frame 1 to inspect layout."]
                result.checks[1].requested_frames = [1]
            else:
                assert any(path.name == "frame-1.png" for path in kwargs["images"])
            return result

    renderer = SupplementalRenderer()
    output, report, path = asyncio.run(
        Harness(EvidenceProvider(spec), renderer).inspect(
            candidate, spec, tmp_path / "attempt", [], Budget()
        )
    )
    assert report.passed and renderer.calls == 2
    assert output.tsx_code == candidate.tsx_code
    assert path == tmp_path / "attempt" / "evidence-1"
    assert "frame-1.png" in report.artifacts


def test_renderer_failure_never_asks_actor_to_rewrite(candidate, spec, tmp_path):
    """环境失败保留具体分类与报告，不把环境问题交给模型修改 TSX。"""
    from server.remotion_templates.provider import ExecutionFailure

    class UnavailableRenderer(ScriptedRenderer):
        """模拟已完成报告中的环境失败。"""

        async def validate(self, candidate, spec, directory, **kwargs):
            """使宿主环境不可用，避免真实系统调用。"""
            output, report = await super().validate(
                candidate, spec, directory, **kwargs
            )
            report.checks.append(
                Check(
                    name="renderer_environment", status="fail", detail="browser missing"
                )
            )
            return output, report

    provider = ScriptedProvider(spec)
    with pytest.raises(ExecutionFailure) as exc:
        asyncio.run(
            Harness(provider, UnavailableRenderer()).inspect(
                candidate, spec, tmp_path / "attempt", [], Budget()
            )
        )
    assert exc.value.code == "renderer_unavailable" and not provider.prompts


@pytest.mark.parametrize("failure", ["protocol", "unknown", "missing_artifact"])
def test_unavailable_review_or_evidence_never_rewrites_candidate(
    spec, settings, failure
):
    """持续评审故障、补采样仍未知或产物缺失均有限停止，API 保留空成功指针与私有诊断。"""
    from server.remotion_templates.provider import ModelContractFailure

    class UnavailableProvider(ScriptedProvider):
        """控制评审失败，其他模型动作使用正常生成路径。"""

        reviews = 0

        async def ask(self, output, system, prompt, budget, **kwargs):
            """未知结果不能被猜成通过，协议失败不能被送给 Actor 重写。"""
            result = await super().ask(output, system, prompt, budget, **kwargs)
            if output is VisualReview:
                self.reviews += 1
                if failure == "protocol":
                    raise ModelContractFailure("invalid visual JSON")
                result.checks[1].status = "unknown"
                result.checks[1].missing_evidence = ["Cannot determine placement"]
            return result

    class EvidenceRenderer(ScriptedRenderer):
        """真实记录渲染次数并按需丢失宿主证据。"""

        async def validate(self, candidate, spec, directory, **kwargs):
            """补采样仍保持原源码；丢帧模拟宿主证据丢失。"""
            output, report = await super().validate(
                candidate, spec, directory, **kwargs
            )
            for frame in kwargs.get("extra_frames", []):
                report.frames.append(frame)
                Image.new("RGBA", (64, 64), "white").save(
                    directory / f"frame-{frame}.png"
                )
            report.frames = sorted(set(report.frames))
            if failure == "missing_artifact":
                (directory / f"frame-{report.frames[0]}.png").unlink()
            return output, report

    provider, renderer = UnavailableProvider(spec), EvidenceRenderer()
    application = create_app(settings, provider=provider, renderer=renderer)
    with TestClient(application) as client:
        created = client.post(
            "/api/templates/works",
            json={"description": "标题", "composition": spec.composition.model_dump()},
        ).json()
        result = wait_job(client, created["job"]["id"])
        assert result["status"] == "failed" and result["result_version_id"] is None
        store = application.state.template_app.state.runtime.store
        job = store.job(result["id"])
        assert job.error.code == (
            "review_unavailable" if failure == "protocol" else "evidence_unavailable"
        )
        assert renderer.calls == (2 if failure == "unknown" else 1)
        assert (
            provider.reviews
            == {"protocol": 3, "unknown": 2, "missing_artifact": 0}[failure]
        )
        assert (
            client.get(f"/api/templates/works/{created['work']['id']}").json()[
                "current_version_id"
            ]
            is None
        )
        assert client.get(f"/api/templates/jobs/{job.id}/artifacts").json() == []
        audit = [
            json.loads(line)
            for line in (store.job_dir(job.id) / "audit.jsonl").read_text().splitlines()
        ]
        assert audit[-1]["event"] == "run_finished"
        assert audit[-1]["error"]["code"] == job.error.code
        assert (
            len([entry for entry in audit if entry["event"] == "actor_response"]) == 1
        )


def test_progress_ignores_review_wording_and_repeated_reads(candidate, spec):
    """评审换措辞、通过项数值噪声或反复读当前候选不算新进展；新的通过结果才重置。"""
    from server.remotion_templates.trajectory import DecisionProgress

    progress = DecisionProgress()
    report = evidence(candidate, spec, visual_text="fail")
    for check in report.checks:
        if check.name.startswith("visual_"):
            check.source = "visual_model"
    assert progress.observe(report, read_current=True)
    for index in range(4):
        for check in report.checks:
            check.detail = f"Varying observation {index}"
        assert not progress.observe(report, read_current=True)
    assert progress.stalled_turns == 4
    next(
        check for check in report.checks if check.name == "visual_text"
    ).status = "pass"
    assert progress.observe(report) and progress.stalled_turns == 0


@pytest.mark.parametrize(
    "changed_detail",
    [
        "Export.tsx(99,7): error TS2322: missing property 'title'.",
        "Export.tsx(99,7): error TS2740: missing property 'color'.",
        "contract.tsx(99,7): error TS2740: missing property 'title'.",
    ],
)
def test_compiler_progress_ignores_only_locations(candidate, spec, changed_detail):
    """编译行列号不算进展，但错误码、字段或文件变化仍保留为新诊断；原报告不变。"""
    from server.remotion_templates.trajectory import DecisionProgress

    progress = DecisionProgress()
    report = evidence(candidate, spec, typescript="fail")
    check = next(c for c in report.checks if c.name == "typescript")
    check.detail = "Export.tsx(1,2): error TS2740: missing property 'title'."
    assert progress.observe(report)
    check.detail = "Export.tsx(99,7): error TS2740: missing property 'title'."
    before = report.model_dump()
    assert not progress.observe(report)
    assert progress.stalled_turns == 1 and report.model_dump() == before
    check.detail = changed_detail
    assert progress.observe(report) and progress.stalled_turns == 0
    check.status = "pass"
    assert progress.observe(report)


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


def test_answer_outcome_is_nonempty_and_exclusive(spec):
    """纯回答必须非空且不能同时宣称生成、参数修改或追问。"""
    output = DialogueOutput
    assert output(answer="当前使用托管字体。").answer == "当前使用托管字体。"
    for payload in (
        {"answer": " "},
        {"answer": "回答", "spec": spec},
        {"answer": "回答", "questions": ["问题"]},
        {"answer": "回答", "parameters": {"0_text": "修改"}},
    ):
        with pytest.raises(ValidationError):
            output(**payload)


@pytest.mark.parametrize("initial", [True, False])
def test_api_answer_preserves_versions_and_allows_next_request(settings, spec, initial):
    """首轮或成功模板后的问答持久化为终态，不渲染、不产生版本事件，随后可继续制作。"""

    class AnswerProvider(ScriptedProvider):
        """在指定轮次只回答，后续恢复正常生成与修改。"""

        answering = initial

        async def turn(self, system, context, tools, budget, **kwargs):
            """Actor 直接看原图并回答；不会再调用 Planner。"""
            if self.answering:
                assert len(kwargs["images"]) == 1
                with Image.open(kwargs["images"][0]) as reference:
                    assert reference.size == (16, 16)
                budget.calls += 1
                return actor_tool(
                    "respond", {"answer": "当前支持托管字体 Noto Sans CJK SC。"}
                )
            return await super().turn(system, context, tools, budget, **kwargs)

        async def ask(self, output, system, prompt, budget, **kwargs):
            """回答 Judge 独立读取原始事实和图片。"""
            if output is AnswerReview:
                assert kwargs.get("context") is None
                assert len(kwargs["images"]) == 1
            return await super().ask(output, system, prompt, budget, **kwargs)

    provider, renderer = AnswerProvider(spec), ScriptedRenderer()
    with TestClient(
        create_app(settings, provider=provider, renderer=renderer)
    ) as client:
        reference = BytesIO()
        Image.new("RGB", (16, 16), "white").save(reference, "PNG")
        asset = client.post(
            "/api/templates/assets",
            files={"file": ("reference.png", reference.getvalue(), "image/png")},
        )
        assert asset.status_code == 201
        created = client.post(
            "/api/templates/works",
            json={
                "description": "支持什么字体？" if initial else "标题",
                "composition": spec.composition.model_dump(),
                "image": {"asset_id": asset.json()["id"]},
            },
        ).json()
        work = created["work"]["id"]
        result = wait_job(client, created["job"]["id"])
        previous = result["result_version_id"]
        if not initial:
            assert result["status"] == "succeeded"
            provider.answering = True
            response = client.post(
                f"/api/templates/works/{work}/messages",
                json={"instruction": "支持什么字体？"},
            )
            assert response.status_code == 202
            result = wait_job(client, response.json()["id"])
        assert result["status"] == "answered", result
        assert result["result_version_id"] is None and result["questions"] == []
        assert result["message"] == "当前支持托管字体 Noto Sans CJK SC。"
        assert renderer.calls == (0 if initial else 1)
        snapshot = client.get(f"/api/templates/works/{work}/session").json()
        assert snapshot["work"]["current_version_id"] == previous
        assert snapshot["messages"][-1]["text"] == result["message"]
        assert snapshot["job"]["status"] == "answered"
        service = client.app.state.template_app.state.runtime
        answer_events = [
            e
            for e in service.store.work_events(created["work"]["id"], 0)
            if str(e.data.get("job_id", e.data.get("id"))) == result["id"]
        ]
        assert not any(e.type == "version.ready" for e in answer_events)
        assert service.store.job(result["id"]).attempts == 0
        assert client.get(f"/api/templates/jobs/{result['id']}/artifacts").json() == []
        assert (
            client.post(f"/api/templates/jobs/{result['id']}/cancel").json()["status"]
            == "answered"
        )
        assert (
            client.post(f"/api/templates/jobs/{result['id']}/retry").status_code == 409
        )
        provider.answering = False
        next_response = client.post(
            f"/api/templates/works/{work}/messages",
            json={"instruction": "把文字改成修改后"},
        )
        assert next_response.status_code == 202
        assert wait_job(client, next_response.json()["id"])["status"] == "succeeded"


@pytest.mark.parametrize("verdict", ["fail", "unknown"])
def test_answer_cannot_bypass_independent_review(spec, verdict):
    """虚假完成承诺或缺少证据的回答被 steer，不能以问答绕过真实修改。"""

    class AnswerActor(ActionProvider):
        """先错误宣称完成，再按拒绝反馈真正生成候选。"""

        async def ask(self, output, system, prompt, budget, **kwargs):
            """回答未通过时只返回真实判断，不继承 Actor 的自述上下文。"""
            if output is AnswerReview:
                assert kwargs.get("context") is None
                budget.calls += 1
                return AnswerReview(
                    status=verdict,
                    detail="No edit was executed; generate the requested template.",
                )
            return await super().ask(output, system, prompt, budget, **kwargs)

    provider = AnswerActor(
        spec,
        [
            ("respond", {"answer": "已经改成黄色了。"}),
            ("submit_candidate", {"tsx_code": SAMPLE_CODE, "spec": spec.model_dump()}),
        ],
    )
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as directory:
        _, actual, report, _ = asyncio.run(
            Harness(provider, ScriptedRenderer()).generate(
                spec,
                Budget(),
                Path(directory),
                [],
                lambda *_: None,
                intent={"instruction": "制作黄色标题"},
                context=Conversation(),
            )
        )
    assert actual == spec and report.passed
    assert "No edit was executed" in " ".join(provider.snapshots[1]["steer"])


@pytest.mark.parametrize("clarify", [False, True])
def test_failed_candidate_cannot_finish_as_answer(settings, spec, clarify):
    """失败候选后拒绝普通回答，即使 Judge 会放行；仍允许必要澄清或修复后发布。"""
    submission = (
        "submit_candidate",
        {"tsx_code": SAMPLE_CODE, "spec": spec.model_dump()},
    )
    provider = ActionProvider(
        spec,
        [
            submission,
            ("respond", {"answer": "已提交候选，下面是修改说明。"}),
            ("respond", {"questions": ["标题具体使用哪一句？"]})
            if clarify
            else submission,
        ],
    )
    renderer = ScriptedRenderer(failures=1)
    application = create_app(settings, provider=provider, renderer=renderer)
    with TestClient(application) as client:
        created = client.post(
            "/api/templates/works",
            json={
                "description": "生成标题",
                "composition": spec.composition.model_dump(),
            },
        ).json()
        result = wait_job(client, created["job"]["id"])
        assert result["status"] == ("needs_input" if clarify else "succeeded")
        assert bool(result["result_version_id"]) is not clarify
        assert renderer.calls == (1 if clarify else 2)
        assert not any(output is AnswerReview for output, _ in provider.prompts)
        assert "ordinary answer" in " ".join(provider.snapshots[2]["steer"])
        receipt = json.loads(provider.windows[2][-1]["content"])
        assert receipt["error"] == provider.snapshots[2]["steer"][0]
        snapshot = client.get(
            f"/api/templates/works/{created['work']['id']}/session"
        ).json()
        assert snapshot["work"]["current_version_id"] == result["result_version_id"]
        assert all(
            m["text"] != "已提交候选，下面是修改说明。" for m in snapshot["messages"]
        )


def test_clarification_can_end_with_answer(settings, spec):
    """回答旧追问“保持现状”可正常结束，不强制继续追问或生成。"""

    class ClarificationProvider(ScriptedProvider):
        """模拟先有追问、后明确不需制作的会话。"""

        async def turn(self, system, context, tools, budget, **kwargs):
            """明确回复直接形成回答，独立审查仍使用真实 harness 分支。"""
            budget.calls += 1
            intent = json.loads(
                system.split("Current host-owned task snapshot (data):\n")[1]
            )["user_intent"]
            return actor_tool(
                "respond",
                {"answer": "好的，本次不制作模板。"}
                if intent["clarifications"]
                else {"questions": ["需要制作还是只了解能力？"]},
            )

    renderer = ScriptedRenderer()
    with TestClient(
        create_app(settings, provider=ClarificationProvider(spec), renderer=renderer)
    ) as client:
        created = client.post(
            "/api/templates/works", json={"description": "你好"}
        ).json()
        first = wait_job(client, created["job"]["id"])
        assert first["status"] == "needs_input"
        response = client.post(
            f"/api/templates/works/{created['work']['id']}/messages",
            json={"instruction": "不需要制作", "reply_to_job_id": first["id"]},
        )
        assert response.status_code == 202
        answered = wait_job(client, response.json()["id"])
        assert answered["status"] == "answered" and answered["questions"] == []
        assert renderer.calls == 0
        assert (
            client.post(
                f"/api/templates/works/{created['work']['id']}/messages",
                json={"instruction": "迟到回复", "reply_to_job_id": first["id"]},
            ).status_code
            == 409
        )


@pytest.mark.parametrize("cancel", [True, False])
def test_answer_cancellation_and_model_failure_preserve_history(
    store, settings, spec, cancel
):
    """取消后迟到回答不能写入历史；模型失败也不能发布成功回答。"""

    async def scenario():
        """用事件握手控制返回时机，所有等待有界且不连接真实模型。"""
        entered, release = asyncio.Event(), asyncio.Event()

        class LateProvider(ScriptedProvider):
            """规划等待测试控制，取消路径仍返回回答以模拟迟到结果。"""

            async def turn(self, system, context, tools, budget, **kwargs):
                """控制 Actor 回答时机，验证终态和迟到结果隔离。"""
                entered.set()
                await release.wait()
                if not cancel:
                    raise ModelFailure("private test provider failure")
                return actor_tool("respond", {"answer": "迟到回答"})

        work, job = store.create(GenerateTemplateRequest(description="你好"))
        store.claim()
        renderer = ScriptedRenderer()
        runtime = Runtime(store, Harness(LateProvider(spec), renderer), settings)
        async with asyncio.timeout(2):
            task = asyncio.create_task(runtime._execute(job.id))
            await entered.wait()
            if cancel:
                store.update(job.id, status="cancelled", stage="finished")
            release.set()
            await task
        snap = store.session(work.id)
        assert snap.job.status == ("cancelled" if cancel else "failed")
        assert snap.work.current_version_id is None and renderer.calls == 0
        assert all(m.text != "迟到回答" for m in snap.messages)
        assert "private test provider failure" not in snap.model_dump_json()

    asyncio.run(scenario())


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
    trajectory = Trajectory()
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


def test_judge_gets_complete_plan_and_repairs_ungrounded_verdict(
    candidate, spec, tmp_path
):
    """完整方案仅解释实现；伪造要求导致 Judge 纠错，候选与渲染证据不变。"""
    spec.assumptions = ["底条属于标题局部装饰"]

    class ScopedProvider(ScriptedProvider):
        """第一次扩大局部要求，第二次在同一证据上撤回无依据的否决。"""

        async def ask(self, output, system, prompt, budget, **kwargs):
            """检查新方案全部字段及评审纠错输入，禁止将方案估计作为新要求。"""
            data = json.loads(prompt)
            assert data["candidate_plan"] == spec.model_dump()
            assert data["user_intent"]["instruction"] == "标题加底条"
            result = await super().ask(output, system, prompt, budget, **kwargs)
            if len(self.prompts) == 1:
                result.checks[2].status = "fail"
                result.checks[2].requirement_quote = "整个画布必须有底色"
                result.checks[2].target = "canvas"
            else:
                assert "not present" in str(data["correction"]["errors"])
            return result

    provider, renderer = ScopedProvider(spec), ScriptedRenderer()
    output, report, _ = asyncio.run(
        Harness(provider, renderer).inspect(
            candidate,
            spec,
            tmp_path / "attempt",
            [],
            Budget(),
            intent={"instruction": "标题加底条"},
        )
    )
    assert report.passed and renderer.calls == 1 and len(provider.prompts) == 2
    assert output.tsx_code == candidate.tsx_code


def test_judge_requested_existing_but_unseen_frame_is_supplied(
    candidate, spec, tmp_path
):
    """代表帧未覆盖的已有帧也能被请求，补证据不能因文件已存在而跳过或仍不发送。"""
    spec.composition.duration_in_frames = 60
    spec.text_layers[0].end_frame = 60

    class ManyFrames(ScriptedRenderer):
        """保存完整帧集合，模型只接收其代表子集。"""

        async def validate(self, candidate, spec, directory, **kwargs):
            """物化全部受控图像，保留清单校验和渲染次数。"""
            output, report = await super().validate(
                candidate, spec, directory, **kwargs
            )
            report.frames = list(range(60))
            for frame in report.frames:
                Image.new("RGBA", (64, 64), "white").save(
                    directory / f"frame-{frame}.png"
                )
            return output, report

    class MissingView(ScriptedProvider):
        """明确请求一个尚未展示的现有帧，收到后才通过。"""

        requested = None

        async def ask(self, output, system, prompt, budget, **kwargs):
            """核对图片路径、序号映射及补采样，避免元数据声称发送但实际缺图。"""
            data = json.loads(prompt)
            result = await super().ask(output, system, prompt, budget, **kwargs)
            assert [p.name for p in kwargs["images"]] == [
                f"frame-{frame}.png" for frame in data["frames"]
            ]
            if self.requested is None:
                assert len(data["frames"]) <= 12 and len(data["available_frames"]) == 60
                self.requested = next(
                    frame
                    for frame in data["available_frames"]
                    if frame not in data["frames"]
                )
                result.checks[1].status = "unknown"
                result.checks[1].missing_evidence = ["需要观察具体时刻"]
                result.checks[1].requested_frames = [self.requested]
            else:
                assert self.requested in data["frames"]
            return result

    renderer = ManyFrames()
    _, report, _ = asyncio.run(
        Harness(MissingView(spec), renderer).inspect(
            candidate,
            spec,
            tmp_path / "attempt",
            [],
            Budget(),
            intent={"instruction": "标题"},
        )
    )
    assert report.passed and len(report.frames) == 60 and renderer.calls == 2


def test_model_receipts_compact_passes_without_losing_private_evidence(spec, tmp_path):
    """Actor 只读取失败详情与通过项名称，完整检查报告仍保存于审计和候选文件。"""
    provider = ActionProvider(
        spec, [("submit_candidate", {"tsx_code": SAMPLE_CODE})] * 2
    )
    context = Conversation()
    _, _, report, directory = asyncio.run(
        Harness(provider, ScriptedRenderer(failures=1)).generate(
            spec, Budget(), tmp_path / "run", [], lambda *_: None, context=context
        )
    )
    assert report.passed and directory.name == "attempt-2"
    receipt = json.loads(
        next(m["content"] for m in provider.windows[1] if m["role"] == "tool")
    )
    assert receipt["checks"] and all(c["status"] != "pass" for c in receipt["checks"])
    assert "configuration" in receipt["passed_checks"]
    assert "configuration" in provider.snapshots[1]["passed_checks"]
    events = [
        json.loads(line)
        for line in (tmp_path / "run/audit.jsonl").read_text().splitlines()
    ]
    full = next(e["result"] for e in events if e["event"] == "tool_result")
    assert any(c["status"] == "pass" and c["detail"] for c in full["checks"])
    assert any(c["status"] == "fail" for c in full["checks"])


def test_judge_correction_continues_past_disabled_quotas(candidate, spec, tmp_path):
    """超过旧配额后无效 Judge 仍可在相同证据上纠错；不重渲染、不改候选或放行无效结论。"""
    requests = []

    def respond(request):
        """先返回无效要求引用，再纠正评审；验证两次请求的图片与候选一致。"""
        body = json.loads(request.content)
        requests.append(body)
        payload = json.loads(body["messages"][1]["content"][0]["text"])
        checks = [
            VisualCheck(name=name, status="pass", detail="Observed requested content")
            for name in ["text", "layout", "style", "motion", "scope"]
        ]
        if len(requests) == 1:
            checks[2] = VisualCheck(
                name="style",
                status="fail",
                detail="Unverified styling claim",
                requirement_source="/user_intent/original_request/image",
                requirement_quote="yellow bar",
                target=spec.text_layers[0].id,
                observed="bar width",
                mismatch="too wide",
            )
        else:
            assert len(requests) == 2 and payload["correction"]["errors"]
            original = json.loads(requests[0]["messages"][1]["content"][0]["text"])
            assert payload["candidate_plan"] == original["candidate_plan"]
            assert (
                body["messages"][1]["content"][1:]
                == requests[0]["messages"][1]["content"][1:]
            )
        assert body["max_tokens"] == 32000
        return httpx.Response(
            200,
            json={
                "usage": {"total_tokens": 19573},
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": VisualReview(checks=checks).model_dump_json(),
                        },
                    }
                ],
            },
        )

    provider = Provider(
        Settings(
            _env_file=None,
            actor_api_key=SecretStr("fixture"),
            vision_model="offline",
        ),
        transport=httpx.MockTransport(respond),
    )
    renderer = ScriptedRenderer()
    budget = Budget(
        calls=50, tokens=200000, phases={"judge": {"calls": 12, "tokens": 60000}}
    )
    output, report, directory = asyncio.run(
        Harness(provider, renderer).inspect(
            candidate,
            spec,
            tmp_path / "attempt",
            [],
            budget,
            intent={"instruction": "标题"},
        )
    )
    assert report.passed and renderer.calls == 1 and len(requests) == 2
    assert output.tsx_code == candidate.tsx_code
    assert budget.summary()["judge_calls"] == 14 and budget.tokens == 239146
    reviews = [
        json.loads(line)
        for line in (directory / "reviews.jsonl").read_text().splitlines()
    ]
    assert reviews[0]["errors"] and not reviews[1]["errors"]


def test_unexplained_unknown_is_corrected_before_sampling(candidate, spec, tmp_path):
    """缺少证据说明先纠正 Judge，同一候选不触发重渲染或 Actor 修复。"""

    class UnexplainedProvider(ScriptedProvider):
        """首轮只请求帧号但不解释缺什么，第二轮纠正该无效评审。"""

        async def ask(self, output, system, prompt, budget, **kwargs):
            """确认协议纠错沿用相同帧集合与方案。"""
            result = await super().ask(output, system, prompt, budget, **kwargs)
            payload = json.loads(prompt)
            if len(self.prompts) == 1:
                result.checks[1].status = "unknown"
                result.checks[1].requested_frames = [1]
            else:
                assert "missing_evidence" in str(payload["correction"]["errors"])
                original = json.loads(self.prompts[0][1])
                assert payload["frames"] == original["frames"]
                assert payload["candidate_plan"] == original["candidate_plan"]
            return result

    provider, renderer = UnexplainedProvider(spec), ScriptedRenderer()
    output, report, directory = asyncio.run(
        Harness(provider, renderer).inspect(
            candidate,
            spec,
            tmp_path / "attempt",
            [],
            Budget(),
            intent={"instruction": "标题"},
        )
    )
    assert report.passed and output.tsx_code == candidate.tsx_code
    assert len(provider.prompts) == 2 and renderer.calls == 1
    assert not list(directory.glob("evidence-*"))
