"""
PR76 code and behavior validation orchestration.

This module owns the typed bridge between the Python tool contract and the
networkless renderer worker.  Generated TSX and test scripts never execute in
the API process; the worker bundles them and drives a fixed Chromium Player.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from .renderer import Renderer
from .tools.contracts import (
    AssertionResult,
    CheckResult,
    CodeDiagnostic,
    CodeValidationReport,
    ComponentDefinition,
    Composition,
    RenderValidationInput,
    RenderValidationReport,
    TestScriptResult,
)

from .tools.schema import merge_parameters as _merge_parameters
from .tools.schema import validate_component_contract as _validate_component_contract
from .tools.schema import validate_parameters as _validate_parameters


class ValidationUnavailable(RuntimeError):
    """The isolated compiler/browser service could not produce a conclusion."""


# worker 按这个顺序执行检查，任一阶段失败或无法继续即停止，报告只会是它的前缀。
_CODE_CHECKS = ("source_policy", "export_source", "typescript")
_RENDER_CHECKS = _CODE_CHECKS + ("default_render", "configured_render")
# 调用方要求透明度度量时，worker 在两次基础渲染之后追加这一阶段。
_TRANSPARENCY_CHECKS = _RENDER_CHECKS + ("transparency",)
_FAILED_STATUSES = {"failed", "fail", "error"}


def _reported_stages(checks: list[Any], stages: tuple[str, ...]) -> tuple[str, ...] | None:
    """Return the reported stage names when they form a usable prefix of ``stages``.

    A worker that stops early always names the failing stage last, so a strict
    prefix ending in a failure is a complete negative report.  Anything else is
    a missing report and must not be read as one.
    """
    names = [str(item.get("name")) for item in checks]
    if not names or names != list(stages[: len(names)]):
        return None
    if len(names) < len(stages) and str(checks[-1].get("status")) not in _FAILED_STATUSES:
        return None
    return tuple(names)


def _diagnostic(value: Any) -> CodeDiagnostic:
    """Normalize a helper diagnostic into the public strict LSP-like shape."""
    if isinstance(value, CodeDiagnostic):
        return value
    if isinstance(value, str):
        return CodeDiagnostic(source="contract", severity="error", message=value)
    data = dict(value)
    data.setdefault("source", "contract")
    data.setdefault("severity", "error")
    data.setdefault("message", "Invalid component contract")
    if data.get("range") is not None:
        data["range"] = dict(data["range"])
    return CodeDiagnostic(**data)


def _contract_diagnostics(component: ComponentDefinition) -> list[CodeDiagnostic]:
    """Call the shared strict schema/default validator and normalize its result."""
    return [_diagnostic(item) for item in _validate_component_contract(component)]


def _merge(defaults: dict[str, Any], patch: dict[str, Any] | None) -> dict[str, Any]:
    """Apply a JSON object patch without mutating defaults."""
    return _merge_parameters(defaults, patch)


def _parameter_errors(schema: dict[str, Any], values: dict[str, Any]) -> list[str]:
    """Return parameter validation messages, preserving strict no-coercion semantics."""
    try:
        _validate_parameters(schema, values)
        return []
    except Exception as exc:
        return [str(exc)]


def _check_status(value: Any) -> str:
    """Map worker check vocabulary to the PR76 report vocabulary."""
    return {"pass": "passed", "fail": "failed"}.get(str(value), str(value))


class ToolValidator:
    """Run PR76 code checks and DOM behavior scripts in isolated renderer attempts."""

    def __init__(self, renderer: Renderer, directory: Path) -> None:
        """Keep renderer ownership outside the tool session and isolate each request."""
        self.renderer = renderer
        self.directory = Path(directory)

    def _attempt(self, name: str) -> Path:
        """Create a unique directory for one validation so scripts cannot share state."""
        path = self.directory / f"{name}-{uuid4().hex}"
        path.mkdir(parents=True, exist_ok=False)
        return path

    async def validate_code(self, component: ComponentDefinition) -> CodeValidationReport:
        """Validate schema/defaults and run source-policy plus TypeScript diagnostics.

        A worker that stops after the failing stage still reports real
        diagnostics; only a report that cannot be read at all is an
        infrastructure failure, so the Executor keeps the chance to fix code.
        """
        return await self.code_report(component)

    async def code_report(
        self, component: ComponentDefinition, *, export_code: str | None = None
    ) -> CodeValidationReport:
        """Re-run only the isolated code checks for an already contracted component.

        Used by the read-only diagnostics route so viewing an accepted version costs
        one language-service attempt instead of a full browser behavior run.  The
        contract is re-checked here anyway: callers may hold older records, and a
        contract failure must never be reported as a clean typecheck.
        """
        diagnostics = _contract_diagnostics(component)
        if diagnostics:
            return CodeValidationReport(passed=False, diagnostics=diagnostics)
        try:
            directory = self._attempt("validate-code")
            request = {
                "mode": "code",
                "code": component.code,
                "parameter_schema": component.parameter_schema,
                "default_parameters": component.default_parameters,
                "composition": {"width": 1080, "height": 1920, "fps": 30, "duration_frames": 1},
                "browser": self.renderer.worker_browser_path(),
            }
            if export_code is not None:
                request["export_code"] = export_code
            (directory / "request.json").write_text(json.dumps(request), encoding="utf-8")
            payload = await self.renderer.run_worker(directory, worker="tool-validation-worker.mjs")
        except (OSError, RuntimeError, TimeoutError) as exc:
            raise ValidationUnavailable(str(exc)) from exc
        if not isinstance(payload, dict):
            raise ValidationUnavailable("Code validation worker returned an invalid report")
        checks = payload.get("checks")
        if not isinstance(checks, list) or not checks or not all(isinstance(item, dict) for item in checks):
            raise ValidationUnavailable("Code validation worker returned no checks")
        # A worker startup/runtime error explains missing checks; preserve its diagnostic.
        for item in checks:
            if item.get("name") == "runtime" and item.get("status") == "error":
                raise ValidationUnavailable(str(item.get("message") or "Code validation worker runtime error"))
        if _reported_stages(checks, _CODE_CHECKS) is None:
            raise ValidationUnavailable("Code validation worker returned an incomplete report")
        try:
            for item in payload.get("diagnostics", []):
                diagnostics.append(_diagnostic(item))
        except (TypeError, ValueError) as exc:
            raise ValidationUnavailable("Code validation worker returned invalid diagnostics") from exc
        for check in checks:
            if check.get("status") in _FAILED_STATUSES and check.get("name") not in {"typescript"}:
                message = str(check.get("message") or "Code check failed")
                # The worker normally reported this failure as a diagnostic already, with its position.
                if not any(item.message == message for item in diagnostics):
                    diagnostics.append(CodeDiagnostic(source="contract", severity="error", message=message))
        has_errors = any(item.severity == "error" for item in diagnostics)
        worker_passed = payload.get("passed") is not False and all(
            check.get("status") in {"pass", "passed"} for check in checks
        )
        return CodeValidationReport(passed=worker_passed and not has_errors, diagnostics=diagnostics)

    async def validate_render(self, request: RenderValidationInput, *, transparency: bool = False) -> RenderValidationReport:
        """Run fixed-canvas default/configured mounts and each independent behavior script.

        With ``transparency`` the worker also measures, on sampled frames, whether the canvas stays see-through
        and fails the ``transparency`` check when every sampled frame is opaque almost everywhere. Only the
        generation path asks for it: a user's own parameter edits are never blocked by it.
        """
        if request.duration_frames > 216000:
            raise ValueError("duration_frames exceeds the validation resource limit")
        names = [test.name for test in request.tests]
        if len(names) != len(set(names)):
            raise ValueError("test names must be unique within one request")
        code = await self.validate_code(request.component)
        composition = Composition(width=1080, height=1920, fps=30, duration_frames=request.duration_frames)
        if not code.passed:
            checks = [
                CheckResult(name="code_contract", status="failed", message="Code validation failed."),
                CheckResult(name="parameters", status="not_run", message="Code validation failed."),
                CheckResult(name="default_render", status="not_run", message="Code validation failed."),
                CheckResult(name="configured_render", status="not_run", message="Code validation failed."),
            ]
            tests = [TestScriptResult(name=test.name, status="not_run", message="Code validation failed.", assertions=[]) for test in request.tests]
            return RenderValidationReport(passed=False, composition=composition, code_validation=code, checks=checks, tests=tests, custom_tests_executed=0)
        parameters = _merge(request.component.default_parameters, request.parameters)
        parameter_errors = _parameter_errors(request.component.parameter_schema, parameters)
        if parameter_errors:
            checks = [
                CheckResult(name="code_contract", status="passed"),
                CheckResult(name="parameters", status="failed", message="; ".join(parameter_errors)[:6000]),
                CheckResult(name="default_render", status="not_run", message="Parameters are invalid."),
                CheckResult(name="configured_render", status="not_run", message="Parameters are invalid."),
            ]
            tests = [TestScriptResult(name=test.name, status="not_run", message="Parameters are invalid.", assertions=[]) for test in request.tests]
            return RenderValidationReport(passed=False, composition=composition, code_validation=code, checks=checks, tests=tests, custom_tests_executed=0)
        directory = self._attempt("validate-render")
        payload_request = {
            "mode": "render",
            "code": request.component.code,
            "parameter_schema": request.component.parameter_schema,
            "default_parameters": request.component.default_parameters,
            "parameters": parameters,
            "composition": composition.model_dump(mode="json"),
            "tests": [test.model_dump(mode="json") for test in request.tests],
            "browser": self.renderer.worker_browser_path(),
            "transparency": transparency,
        }
        (directory / "request.json").write_text(json.dumps(payload_request), encoding="utf-8")
        try:
            payload = await self.renderer.run_worker(directory, worker="tool-validation-worker.mjs")
        except (OSError, RuntimeError, TimeoutError) as exc:
            raise ValidationUnavailable(str(exc)) from exc
        raw_checks = payload.get("checks")
        if not isinstance(raw_checks, list):
            raise ValidationUnavailable("Render validation worker returned no checks")
        # A worker startup/runtime error explains missing checks; preserve its diagnostic.
        for item in raw_checks:
            if isinstance(item, dict) and item.get("name") == "runtime" and item.get("status") == "error":
                raise ValidationUnavailable(str(item.get("message") or "Render validation worker runtime error"))
        if _reported_stages(raw_checks, _TRANSPARENCY_CHECKS if transparency else _RENDER_CHECKS) is None:
            raise ValidationUnavailable("Render validation worker returned an incomplete report")
        expected_tests = [test.name for test in request.tests]
        raw_tests = payload.get("tests")
        if not isinstance(raw_tests, list) or [item.get("name") for item in raw_tests] != expected_tests:
            raise ValidationUnavailable("Render validation worker returned an incomplete test report")
        if any(
            not isinstance(item, dict)
            or item.get("status") not in {"passed", "failed", "error", "not_run"}
            or not isinstance(item.get("assertions"), list)
            for item in raw_tests
        ):
            raise ValidationUnavailable("Render validation worker returned an invalid test result")
        if int(payload.get("custom_tests_executed", -1)) != len(request.tests):
            raise ValidationUnavailable("Render validation worker returned an invalid test count")
        checks = [
            CheckResult(name="code_contract", status="passed"),
            CheckResult(name="parameters", status="passed"),
        ]
        for item in raw_checks:
            name = str(item.get("name", "runtime"))
            if name in {"source_policy", "export_source", "typescript"}:
                continue
            checks.append(CheckResult(name=name, status=_check_status(item.get("status")), **({"message": str(item["message"])} if item.get("message") else {})))
        tests: list[TestScriptResult] = []
        for item in payload.get("tests", []):
            assertions = [AssertionResult(**assertion) for assertion in item.get("assertions", [])]
            values = {"name": item.get("name", "test"), "status": item.get("status", "error"), "assertions": assertions}
            if item.get("message"):
                values["message"] = str(item["message"])
            tests.append(TestScriptResult(**values))
        executed = int(payload.get("custom_tests_executed", len(tests)))
        passed = bool(payload.get("passed", False)) and all(check.status == "passed" for check in checks) and all(test.status == "passed" for test in tests)
        return RenderValidationReport(passed=passed, composition=composition, code_validation=code, checks=checks, tests=tests, custom_tests_executed=executed)
