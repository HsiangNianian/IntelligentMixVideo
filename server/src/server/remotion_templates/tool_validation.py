"""
PR76 code and behavior validation orchestration.

This module owns the typed bridge between the Python tool contract and the
networkless renderer worker.  Generated TSX and test scripts never execute in
the API process; the worker bundles them and drives a fixed Chromium Player.
"""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any, NamedTuple
from uuid import uuid4

from .renderer import CPU_SECONDS, Renderer
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
# 字效另要求最长文字排版检查，位于其余可选阶段之后。
_TEXT_FIT_CHECK = "text_fit"
# 自由组件（Effect.tsx）的契约探针：注入内容、关键词选项、中间帧、字号与参数接线，位于排版检查之后。
_EFFECT_CONTRACT_CHECK = "effect_contract"
_FAILED_STATUSES = {"failed", "fail", "error"}

# 总线渲染 Sprite 时传入的时长范围（秒）；Agent 生成时选的时长只用于预览，最终时长由合成决定。
MIN_RENDER_SECONDS = 0.5
MAX_RENDER_SECONDS = 120
# 字效（标题、字幕）显示不足这个时长时不播出入场，定格在预览长度的中间帧（见 sprite_render）：出入场通常共需
# 0.6～1.3 秒，更短的字幕几乎看不到完整画面，还会闪烁。所以字效从不按更短的长度播放。
STATIC_TEXT_SECONDS = 1.5


class FrameCapture(NamedTuple):
    """Still frames of a validated mount for the Agent to look at: the ``frames`` to capture, the canvas ``box`` to cut
    them to at ``scale``, and the ``directory`` that receives them as `frame-<n>.png` once every check has passed;
    ``parameters`` are mounted over the configured values for them (such as sample keywords); ``trace`` lists the frames on which
    the worker also measures each line and keyword from the running code, written to `motion.json` beside the frames."""

    frames: list[int]
    box: dict[str, float]
    scale: float
    directory: Path
    parameters: dict[str, Any] | None = None
    trace: list[int] | None = None


class FitSprite(NamedTuple):
    """One text Sprite to measure: sealed code, canvas, content box and fit or capacity probe items."""

    component: ComponentDefinition
    canvas: dict[str, int]
    box: dict[str, float]
    items: list[dict[str, Any]]


def render_lengths(fps: int, *, text: bool = False) -> list[int]:
    """Sprite 可能按自身长度播放的最短与最长帧数，秒转帧与叠加渲染一致（半帧向后取整）；生成与发布在预览长度之外也挂载它们。

    字效更短时会被定格，所以它的最短长度是 `STATIC_TEXT_SECONDS`，其他分类是 `MIN_RENDER_SECONDS`。
    """
    shortest = STATIC_TEXT_SECONDS if text else MIN_RENDER_SECONDS
    return [max(1, math.floor(seconds * fps + 0.5)) for seconds in (shortest, MAX_RENDER_SECONDS)]


def _reported_stages(checks: list[Any], stages: tuple[str, ...]) -> tuple[str, ...] | None:
    """Return the reported stage names when they form a usable prefix of ``stages``.

    A worker that stops early names the failing stage last, optionally followed by the stages it
    records as ``not_run`` (a failed default render still lists the configured render), so a strict
    prefix whose last executed stage failed is a complete negative report.  Anything else is a
    missing report and must not be read as one.
    """
    names = [str(item.get("name")) for item in checks]
    if not names or names != list(stages[: len(names)]):
        return None
    if len(names) < len(stages):
        executed = [item for item in checks if str(item.get("status")) != "not_run"]
        if not executed or str(executed[-1].get("status")) not in _FAILED_STATUSES:
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


def _valid_size(value: Any) -> bool:
    """A fit answer's optional numeric field (`keyword_size`, `grow_shift`) is absent or a finite number."""
    return value is None or (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value))


def _valid_shift(value: Any) -> bool:
    """A fit answer's optional `shift` is absent or an `{x, y}` of finite numbers (canvas pixels)."""
    return value is None or (
        isinstance(value, dict) and set(value) == {"x", "y"}
        and all(isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item) for item in value.values())
    )


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
                # A text-block Preset is type-checked with the props the host passes it (see the worker's contract call site).
                **({"role": component.role} if getattr(component, "role", None) else {}),
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

    async def validate_render(
        self, request: RenderValidationInput, *, transparency: bool = False, lengths: list[int] | None = None, text_fit: bool = False,
        capture: FrameCapture | None = None, text_fit_parameters: dict[str, bool] | None = None,
        effect_contract: dict[str, Any] | None = None,
    ) -> RenderValidationReport:
        """Run fixed-canvas default/configured mounts and each independent behavior script.

        With ``transparency`` the worker also measures, on sampled frames, whether the canvas stays see-through
        and fails the ``transparency`` check when most sampled frames are opaque almost everywhere. With ``lengths``
        it then mounts the first, middle and last frame at each of those render lengths (frames) and fails the
        ``render_lengths`` check when the component throws. With ``text_fit`` (text effects) it fills the ``text``
        parameter to its ``maxLength`` and fails the ``text_fit`` check when that text is clipped, leaves the canvas or
        is too small to read. Only the generation path asks for these: a user's own parameter edits are never blocked
        by them. With ``capture`` the same mount also captures still frames into ``capture.directory`` when every
        check passes; a failed report captures none. ``text_fit_parameters`` marks host-composed still text and
        disables its cue wrappers for layout measurements; it never changes runtime or review frames.
        ``effect_contract`` runs the contract probes of a free-form effect (`tools/workspace.contract_probes`) after the
        other checks and fails the ``effect_contract`` check with every problem it finds.
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
            "text_fit": text_fit,
        }
        if lengths:
            payload_request["lengths"] = lengths
        if text_fit and text_fit_parameters is not None:
            payload_request["text_fit_parameters"] = text_fit_parameters
        if effect_contract is not None:
            payload_request["effect_contract"] = effect_contract
        if capture:
            payload_request["capture"] = {"frames": capture.frames, "box": capture.box, "scale": capture.scale, "parameters": capture.parameters or {}, **({"trace": capture.trace} if capture.trace else {})}
        (directory / "request.json").write_text(json.dumps(payload_request), encoding="utf-8")
        try:
            payload = await self.renderer.run_worker(directory, worker="tool-validation-worker.mjs")
        except (OSError, RuntimeError, TimeoutError) as exc:
            raise ValidationUnavailable(str(exc)) from exc
        if capture and (directory / "frames").is_dir():
            capture.directory.mkdir(parents=True, exist_ok=True)
            for frame in [*(directory / "frames").glob("frame-*.png"), *(directory / "frames").glob("motion.json")]:
                shutil.move(frame, capture.directory / frame.name)
        raw_checks = payload.get("checks")
        if not isinstance(raw_checks, list):
            raise ValidationUnavailable("Render validation worker returned no checks")
        # A worker startup/runtime error explains missing checks; preserve its diagnostic.
        for item in raw_checks:
            if isinstance(item, dict) and item.get("name") == "runtime" and item.get("status") == "error":
                raise ValidationUnavailable(str(item.get("message") or "Render validation worker runtime error"))
        stages = (_TRANSPARENCY_CHECKS if transparency else _RENDER_CHECKS) + (("render_lengths",) if lengths else ())
        stages += (_TEXT_FIT_CHECK,) if text_fit else ()
        stages += (_EFFECT_CONTRACT_CHECK,) if effect_contract is not None else ()
        if _reported_stages(raw_checks, stages) is None:
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

    async def fit_texts(
        self, sprites: list[FitSprite], composition: dict[str, int],
        timeout_seconds: float | None = None, cpu_seconds: int = CPU_SECONDS,
    ) -> list[list[dict[str, Any]]]:
        """Find, for each text a render really passes, the largest font size that shows it in full.

        ``sprites`` lists, per text Sprite of the render, its component, its ``canvas`` (width and height; the video,
        ``composition``, may scale it to cover), its content ``box`` and its items; all of them are measured in one
        browser run under one deadline. Each item carries complete ``values`` (including the
        text and the requested size), the ``text`` to look for, the name of the size parameter (``size_key``), its
        ``min``, the ``frames`` to measure at the ``length`` its Sprite plays and the host ``transform`` that places it
        inside ``box`` on the canvas. The answer is one list per Sprite with
        ``{index, fits, size, lines[, keyword_size][, shift][, grow_shift]}`` per item: ``size`` is the requested size when
        the text already fits, otherwise the largest smaller whole size that does, and ``lines`` is the number of visual
        lines that size shows (diagnostics; the host enforces ``one_line``). ``keyword_size`` is the proportionally
        smaller ``keywordSize`` when the values set one.
        When even ``min`` leaves the text outside the window the video shows, ``shift`` ``{x, y}`` is the shortest
        move of the placement, in canvas pixels, that brings it back in; ``fits`` is false when not even that helps.
        ``grow_shift`` is a signed downward (+) or upward (-) move of a subtitle text that wrapped, so the extra lines
        grow away from the other language's subtitle.
        Failed items include ``problems`` with the measured reasons for private render diagnostics.
        A worker that cannot answer is an infrastructure failure, never a pass.
        """
        payload = await self._measure_texts("fit", sprites, composition, timeout_seconds, cpu_seconds)
        fits = payload.get("fits")
        if not isinstance(fits, list) or len(fits) != len(sprites) or not all(
            isinstance(answers, list)
            and [answer.get("index") for answer in answers if isinstance(answer, dict)] == [item["index"] for item in sprite.items]
            and all(isinstance(answer.get("fits"), bool) and isinstance(answer.get("size"), (int, float)) and _valid_shift(answer.get("shift")) and _valid_size(answer.get("keyword_size")) and _valid_size(answer.get("grow_shift")) for answer in answers)
            for answers, sprite in zip(fits, sprites)
        ):
            raise ValidationUnavailable("Text fit worker returned an incomplete report")
        return fits

    async def text_capacities(
        self, sprites: list[FitSprite], composition: dict[str, int], *, timeout_seconds: float, cpu_seconds: int,
    ) -> list[list[dict[str, Any]]]:
        """Measure the largest full-width sample that fits each placement without shrinking or moving it.

        Items contain ordered ``samples`` with ``chars`` per line, complete ``text`` and plain/keyword ``values``
        variants, plus ``lines``, ``frames``, ``length`` and ``transform``. Only the sampled preview timing is known
        before segmentation; final render preflight still checks the actual text and timing. Missing or malformed
        results fail closed instead of falling back to the hidden IMS subtitle.
        """
        payload = await self._measure_texts("capacity", sprites, composition, timeout_seconds, cpu_seconds)
        capacities = payload.get("capacities")
        if not isinstance(capacities, list) or len(capacities) != len(sprites) or not all(
            isinstance(answers, list) and len(answers) == len(sprite.items)
            and all(isinstance(answer, dict) and answer.get("index") == item["index"]
                    and type(answer.get("capacity")) is int
                    and 0 <= answer["capacity"] <= item["samples"][-1]["chars"]
                    for answer, item in zip(answers, sprite.items))
            for answers, sprite in zip(capacities, sprites)
        ):
            raise ValidationUnavailable("Text capacity worker returned an incomplete report")
        return capacities

    async def _measure_texts(
        self, mode: str, sprites: list[FitSprite], composition: dict[str, int],
        timeout_seconds: float | None, cpu_seconds: int,
    ) -> dict[str, Any]:
        """Run fit/capacity probes in one isolated browser under the caller's resource grant and deadline."""
        try:
            directory = self._attempt(f"{mode}-texts")
            request = {
                "mode": mode,
                "composition": composition,
                "sprites": [
                    {
                        "code": sprite.component.code, "parameter_schema": sprite.component.parameter_schema,
                        "default_parameters": sprite.component.default_parameters, "canvas": sprite.canvas, "box": sprite.box,
                        "fit": sprite.items,
                    }
                    for sprite in sprites
                ],
                "browser": self.renderer.worker_browser_path(),
            }
            (directory / "request.json").write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
            payload = await self.renderer.run_worker(
                directory, worker="tool-validation-worker.mjs", timeout_seconds=timeout_seconds, cpu_seconds=cpu_seconds,
            )
        except (OSError, RuntimeError, TimeoutError) as exc:
            raise ValidationUnavailable(str(exc)) from exc
        for item in payload.get("checks", []) if isinstance(payload, dict) else []:
            if item.get("name") == "runtime" and item.get("status") == "error":
                raise ValidationUnavailable(str(item.get("message") or "Fit worker runtime error"))
        if not isinstance(payload, dict):
            raise ValidationUnavailable("Text measurement worker returned an incomplete report")
        return payload

    async def validate_samples(
        self, component: ComponentDefinition, composition: dict[str, int], box: dict[str, float], samples: list[dict[str, Any]],
        *, text: bool = False,
    ) -> list[CheckResult]:
        """Mount publish-time boundary samples in one isolated browser and report one check per sample.

        ``composition`` is the version canvas (width, height, fps, duration_frames); each sample carries complete
        ``values`` and a host ``transform`` applied around ``box``, and is mounted at the preview length and at the
        shortest and longest render length (a ``text`` effect's shortest is the static threshold, see `render_lengths`). A failed code stage is reported as that check alone; a report that
        cannot be read at all is an infrastructure failure.
        """
        try:
            directory = self._attempt("validate-samples")
            request = {
                "mode": "samples",
                "code": component.code,
                "parameter_schema": component.parameter_schema,
                "default_parameters": component.default_parameters,
                "composition": composition,
                "box": box,
                "samples": samples,
                "lengths": render_lengths(composition["fps"], text=text),
                "browser": self.renderer.worker_browser_path(),
            }
            (directory / "request.json").write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
            payload = await self.renderer.run_worker(directory, worker="tool-validation-worker.mjs")
        except (OSError, RuntimeError, TimeoutError) as exc:
            raise ValidationUnavailable(str(exc)) from exc
        raw = payload.get("checks") if isinstance(payload, dict) else None
        if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
            raise ValidationUnavailable("Sample validation worker returned no checks")
        for item in raw:
            if item.get("name") == "runtime" and item.get("status") == "error":
                raise ValidationUnavailable(str(item.get("message") or "Sample validation worker runtime error"))
        code = [item for item in raw if item.get("name") in _CODE_CHECKS]
        if any(item.get("status") in _FAILED_STATUSES for item in code):
            failed = next(item for item in code if item.get("status") in _FAILED_STATUSES)
            return [CheckResult(name=str(failed["name"]), status="failed", message=str(failed.get("message") or "Code check failed")[:6000])]
        reported = [item for item in raw if str(item.get("name", "")).startswith("sample:")]
        if [item["name"] for item in reported] != [f"sample:{item['name']}" for item in samples]:
            raise ValidationUnavailable("Sample validation worker returned an incomplete report")
        return [
            CheckResult(name=str(item["name"]), status=_check_status(item.get("status")), **({"message": str(item["message"])[:6000]} if item.get("message") else {}))
            for item in reported
        ]
