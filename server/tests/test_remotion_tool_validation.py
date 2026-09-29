"""Offline tests for PR76 validation report semantics using a fake sandbox worker."""

import asyncio
from pathlib import Path

from server.remotion_templates.tool_validation import ToolValidator
from server.remotion_templates.tools.contracts import (
    ComponentDefinition,
    RenderValidationInput,
    TestScript,
)


class FakeRenderer:
    """Return deterministic worker payloads without requiring Chromium in unit tests."""

    def worker_browser_path(self):
        """Match Renderer.worker_browser_path without accessing the host executable."""
        return "/usr/bin/chromium"

    async def run_worker(self, directory: Path, *, worker: str, timeout_seconds=None):
        """Emit one successful code or behavior report according to the request mode."""
        import json

        request = json.loads((directory / "request.json").read_text())
        if request["mode"] == "code":
            return {
                "passed": True,
                "checks": [
                    {"name": "source_policy", "status": "pass"},
                    {"name": "export_source", "status": "pass"},
                    {"name": "typescript", "status": "pass"},
                ],
                "diagnostics": [],
            }
        return {
            "passed": True,
            "checks": [
                {"name": "source_policy", "status": "pass"},
                {"name": "export_source", "status": "pass"},
                {"name": "typescript", "status": "pass"},
                {"name": "default_render", "status": "passed"},
                {"name": "configured_render", "status": "passed"},
            ],
            "tests": [
                {"name": test["name"], "status": "passed", "assertions": [{"message": "ok", "passed": True, "actual": True, "expected": True}]}
                for test in request["tests"]
            ],
            "custom_tests_executed": len(request["tests"]),
        }


def component():
    """Build a minimal strict component contract for validator tests."""
    return ComponentDefinition(
        code="import React from 'react'; export default function Template(props: {title: string}) { return <div>{props.title}</div>; }",
        parameter_schema={"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"], "additionalProperties": False},
        default_parameters={"title": "hello"},
    )


def test_code_validation_reports_worker_conclusion(tmp_path):
    """A clean contract and clean worker diagnostics produce passed=true."""
    report = asyncio.run(ToolValidator(FakeRenderer(), tmp_path).validate_code(component()))
    assert report.passed is True
    assert report.diagnostics == []


def test_render_validation_runs_named_scripts_and_preserves_composition(tmp_path):
    """Behavior reports contain actual script assertions and the fixed canvas contract."""
    request = RenderValidationInput(
        component=component(),
        duration_frames=150,
        tests=[TestScript(name="title", code="export default async function run(ctx) { ctx.assert(true, 'ok'); }")],
    )
    report = asyncio.run(ToolValidator(FakeRenderer(), tmp_path).validate_render(request))
    assert report.passed is True
    assert report.composition.model_dump() == {"width": 1080, "height": 1920, "fps": 30, "duration_frames": 150}
    assert report.custom_tests_executed == 1
    assert report.tests[0].assertions[0].passed is True


def test_invalid_defaults_stop_custom_tests(tmp_path):
    """Schema-invalid defaults fail parameters and mark scripts not_run."""
    invalid = component().model_copy(update={"default_parameters": {"title": 3}})
    request = RenderValidationInput(
        component=invalid,
        duration_frames=30,
        tests=[TestScript(name="title", code="export default async function run(ctx) { ctx.assert(true, 'ok'); }")],
    )
    report = asyncio.run(ToolValidator(FakeRenderer(), tmp_path).validate_render(request))
    assert report.passed is False
    assert report.custom_tests_executed == 0
    assert report.tests[0].status == "not_run"
