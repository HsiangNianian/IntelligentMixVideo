"""Offline tests for PR76 validation report semantics using a fake sandbox worker."""

import asyncio
import json
import os
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from server.remotion_templates.tool_validation import ToolValidator, ValidationUnavailable
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


@pytest.mark.parametrize("runtime_error", [None, "ENOENT: no such file or directory, mkdtemp '/work/.tmp/puppeteer_dev_chrome_profile-test'"])
def test_incomplete_render_report_preserves_runtime_error(tmp_path, monkeypatch, runtime_error):
    """Browser startup errors reach the caller; unexplained omissions still fail closed."""
    renderer = FakeRenderer()
    original = renderer.run_worker

    async def incomplete_report(directory, **kwargs):
        """Simulate a worker that stops before recording either base render check."""
        payload = await original(directory, **kwargs)
        if directory.name.startswith("validate-render-"):
            payload["checks"] = payload["checks"][:3]
            if runtime_error:
                payload["checks"].append({"name": "runtime", "status": "error", "message": runtime_error})
            payload.update(passed=False, tests=[], custom_tests_executed=0)
        return payload

    monkeypatch.setattr(renderer, "run_worker", incomplete_report)
    request = RenderValidationInput(component=component(), duration_frames=30, tests=[])
    with pytest.raises(ValidationUnavailable) as failure:
        asyncio.run(ToolValidator(renderer, tmp_path).validate_render(request))
    expected = runtime_error or "Render validation worker returned an incomplete report"
    assert expected in str(failure.value)


@pytest.mark.parametrize(
    "checks",
    [
        [{"name": "source_policy", "status": "failed", "message": "Import not permitted: node:fs"}],
        [
            {"name": "source_policy", "status": "pass"},
            {"name": "export_source", "status": "failed", "message": "Default component export rejected"},
        ],
    ],
)
def test_failing_stage_report_returns_diagnostics_instead_of_an_outage(tmp_path, checks):
    """worker 在语法、源码策略或导出检查失败时提前返回，此时只报告已执行的阶段。

    这是正常的负面结论：诊断必须回到 Executor，任务不能按基础设施故障结束。
    """
    message = checks[-1]["message"]

    class EarlyExitRenderer(FakeRenderer):
        """Emit the worker's early-exit shape, which stops at the failing stage."""

        async def run_worker(self, directory: Path, *, worker: str, timeout_seconds=None):
            """Return only the stages the real worker reached."""
            return {
                "passed": False,
                "checks": deepcopy(checks),
                "diagnostics": [{"source": "contract", "severity": "error", "message": message}],
            }

    report = asyncio.run(ToolValidator(EarlyExitRenderer(), tmp_path).validate_code(component()))
    assert report.passed is False
    assert [item.message for item in report.diagnostics if item.message == message]


def test_stopped_stage_report_must_name_the_failure(tmp_path):
    """只缺少后续阶段并不构成可读报告；没有失败阶段时仍按缺失报告处理。"""
    class TruncatedRenderer(FakeRenderer):
        """Drop later stages without reporting a failure."""

        async def run_worker(self, directory: Path, *, worker: str, timeout_seconds=None):
            """Return a report that stops after a passing stage."""
            return {
                "passed": False,
                "checks": [{"name": "source_policy", "status": "pass"}],
                "diagnostics": [],
            }

    with pytest.raises(ValidationUnavailable, match="incomplete report"):
        asyncio.run(ToolValidator(TruncatedRenderer(), tmp_path).validate_code(component()))


def test_policy_violation_is_reported_once_with_its_position(tmp_path):
    """worker 已带位置报告的违规不会被同文的失败检查再追加一条无位置的重复诊断。"""
    message = "Unsupported capability: exec (line 3, column 15)"

    class Renderer(FakeRenderer):
        """Fail source policy the way the worker does: one failed check and one located diagnostic."""

        async def run_worker(self, directory: Path, *, worker: str, timeout_seconds=None):
            """Return the early negative report."""
            return {
                "passed": False,
                "checks": [{"name": "source_policy", "status": "failed", "message": message}],
                "diagnostics": [{
                    "source": "contract", "severity": "error", "message": message,
                    "range": {"start": {"line": 2, "character": 14}, "end": {"line": 2, "character": 18}},
                }],
            }

    report = asyncio.run(ToolValidator(Renderer(), tmp_path).validate_code(component()))
    assert report.passed is False
    assert len(report.diagnostics) == 1
    assert report.diagnostics[0].message == message
    assert report.diagnostics[0].range.start.line == 2 and report.diagnostics[0].range.start.character == 14


# ---- real worker: Node plus the locked renderer dependencies, no model, no network ----
RENDERER_DIR = Path(__file__).parents[1] / "src" / "server" / "remotion"
DEPENDENCIES = RENDERER_DIR / "node_modules" / "typescript"
needs_node = pytest.mark.skipif(
    not DEPENDENCIES.exists() or shutil.which("node") is None,
    reason="install locked Remotion renderer dependencies and Node",
)


def run_code_worker(tmp_path: Path, code: str, properties: dict, defaults: dict) -> dict:
    """Run only the compile-time stages of the real worker (source policy, export, TypeScript)."""
    root = tmp_path / "work"
    root.mkdir()
    (root / "request.json").write_text(json.dumps({
        "mode": "code", "code": code, "parameter_schema": {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False},
        "default_parameters": defaults, "composition": {"width": 1080, "height": 1920, "fps": 30, "duration_frames": 1},
    }), encoding="utf-8")
    environment = {**os.environ, "IMV_WORK_ROOT": str(root), "IMV_RENDERER_ROOT": str(RENDERER_DIR)}
    subprocess.run(["node", str(RENDERER_DIR / "tool-validation-worker.mjs")], cwd=RENDERER_DIR, env=environment, check=True, capture_output=True, timeout=120)
    return json.loads((root / "renderer.json").read_text(encoding="utf-8"))


REGEX_COMPONENT = """import React from "react";

/** Colour parsing with RegExp.prototype.exec, which is ordinary code and not a host capability. */
const parse = (value: string): number[] | null => {
  const match = /^#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})$/.exec(value);
  return match ? [1, 2, 3].map((index) => parseInt(match[index], 16)) : null;
};

export default function Tint(props: { color: string }) {
  const rgb = parse(props.color);
  return <div style={{ color: rgb ? `rgb(${rgb.join(",")})` : "white" }}>tint</div>;
}
"""


@needs_node
def test_regexp_exec_is_ordinary_code_for_the_real_worker(tmp_path):
    """16:03 的生成失败：正则的 .exec() 方法曾被当作被禁止的 exec 能力，模型无从定位而放弃。"""
    report = run_code_worker(tmp_path, REGEX_COMPONENT, {"color": {"type": "string"}}, {"color": "#FFAA00"})
    assert report["passed"] is True, report
    assert [item["status"] for item in report["checks"]] == ["pass", "pass", "pass"]


@needs_node
def test_a_bare_exec_call_is_still_refused_and_located(tmp_path):
    """被禁止的 exec 仍然拒绝，并且诊断给出行列号与范围，模型可以直接定位。"""
    code = 'import React from "react";\nexport default function T(props: { t: string }) {\n  const out = exec("ls");\n  return <div>{props.t}</div>;\n}\n'
    report = run_code_worker(tmp_path, code, {"t": {"type": "string"}}, {"t": "x"})
    assert report["passed"] is False
    assert [item["name"] for item in report["checks"]] == ["source_policy"]
    assert report["diagnostics"] == [{
        "source": "contract", "severity": "error", "message": "Unsupported capability: exec (line 3, column 15)",
        "range": {"start": {"line": 2, "character": 14}, "end": {"line": 2, "character": 18}},
    }]
