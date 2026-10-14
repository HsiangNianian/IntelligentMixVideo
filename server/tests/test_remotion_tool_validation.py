"""Offline tests for PR76 validation report semantics using a fake sandbox worker."""

import asyncio
import json
import os
import shutil
import subprocess
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

import pytest

from server.remotion_templates.boundary import samples
from server.remotion_templates.categories import KEYWORD_COLOR
from server.remotion_templates.publication import PREVIEW_PROTOCOL, current_bundle
from server.remotion_templates.renderer import Renderer
from server.remotion_templates.settings import Settings
from server.remotion_templates.tool_validation import ToolValidator, ValidationUnavailable, render_lengths
from server.remotion_templates.tools.compose import compose_source
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


def transparency_renderer(share: float | None, *, stop_after: int | None = None):
    """A worker double that reports the transparency stage the way the real worker does.

    ``share`` is the opaque share every sampled frame shows; ``None`` omits the stage altogether and
    ``stop_after`` truncates the stages to simulate a worker that quit early.
    """
    class Renderer(FakeRenderer):
        """Record requests and append the transparency stage when the caller asked for it."""

        requests: list[dict] = []

        async def run_worker(self, directory: Path, *, worker: str, timeout_seconds=None):
            """Return the base report plus a transparency check derived from ``share``."""
            payload = await super().run_worker(directory, worker=worker, timeout_seconds=timeout_seconds)
            request = json.loads((directory / "request.json").read_text())
            self.requests.append(request)
            if request["mode"] == "render" and request.get("transparency") and share is not None:
                hidden = share >= 0.98
                payload["checks"].append({
                    "name": "transparency",
                    "status": "failed" if hidden else "passed",
                    "message": "The Sprite hides the video: the canvas is opaque in most sampled frames." if hidden else "The video stays visible.",
                })
                payload["passed"] = not hidden
            if stop_after is not None:
                payload["checks"] = payload["checks"][:stop_after]
            return payload

    return Renderer()


def small_request() -> RenderValidationInput:
    """A 30 frame render validation without behavior scripts."""
    return RenderValidationInput(component=component(), duration_frames=30, tests=[])


def test_transparency_is_measured_only_when_the_caller_asks_for_it(tmp_path):
    """生成路径要求透明度度量；用户手动改参数的路径不要求，也不会被它拦下。"""
    renderer = transparency_renderer(1.0)
    plain = asyncio.run(ToolValidator(renderer, tmp_path).validate_render(small_request()))
    assert plain.passed is True and "transparency" not in [item.name for item in plain.checks]
    measured = asyncio.run(ToolValidator(renderer, tmp_path).validate_render(small_request(), transparency=True))
    assert [request.get("transparency") for request in renderer.requests if request["mode"] == "render"] == [False, True]
    assert measured.passed is False
    check = next(item for item in measured.checks if item.name == "transparency")
    assert check.status == "failed" and "hides the video" in check.message


def test_a_clear_canvas_passes_the_transparency_stage(tmp_path):
    """画面有透出视频的帧时 transparency 阶段通过，整份报告通过。"""
    report = asyncio.run(ToolValidator(transparency_renderer(0.0), tmp_path).validate_render(small_request(), transparency=True))
    assert report.passed is True
    assert [item.name for item in report.checks][-1] == "transparency"


@pytest.mark.parametrize("renderer", [transparency_renderer(None), transparency_renderer(0.0, stop_after=5)], ids=["omitted", "truncated"])
def test_missing_transparency_stage_fails_closed(tmp_path, renderer):
    """要求度量却没有收到该阶段的报告不能被当作通过，而是明确的验证服务故障。"""
    with pytest.raises(ValidationUnavailable, match="incomplete report"):
        asyncio.run(ToolValidator(renderer, tmp_path).validate_render(small_request(), transparency=True))


def test_failed_default_render_is_a_negative_report_even_with_transparency(tmp_path):
    """审计复现：default_render 失败后 worker 仍追加 configured_render=not_run；要求透明度度量时这份完整负报告被当成缺失，真实错误丢失。"""
    error = "Component runtime error: items is not iterable"

    class Renderer(FakeRenderer):
        """Report a failed default render followed by the not_run stage the real worker still lists."""

        async def run_worker(self, directory: Path, *, worker: str, timeout_seconds=None):
            """Return the checks up to the code stages, the failure and the skipped configured render."""
            payload = await super().run_worker(directory, worker=worker, timeout_seconds=timeout_seconds)
            if directory.name.startswith("validate-render-"):
                payload["checks"] = payload["checks"][:3] + [
                    {"name": "default_render", "status": "failed", "message": error},
                    {"name": "configured_render", "status": "not_run", "message": "Default render failed."},
                ]
                payload.update(passed=False, tests=[], custom_tests_executed=0)
            return payload

    for transparency in (False, True):
        report = asyncio.run(ToolValidator(Renderer(), tmp_path).validate_render(small_request(), transparency=transparency))
        assert report.passed is False
        status = {item.name: item.status for item in report.checks}
        assert status["default_render"] == "failed" and status["configured_render"] == "not_run"
        assert next(item for item in report.checks if item.name == "default_render").message == error


def test_not_run_stages_do_not_hide_a_missing_failure(tmp_path):
    """not_run 后缀只在前面确有失败阶段时才算负报告；通过阶段之后直接 not_run 仍是缺失报告。"""
    class Renderer(FakeRenderer):
        """Skip stages although the last executed one passed."""

        async def run_worker(self, directory: Path, *, worker: str, timeout_seconds=None):
            """Return a passing default render followed by not_run stages."""
            payload = await super().run_worker(directory, worker=worker, timeout_seconds=timeout_seconds)
            if directory.name.startswith("validate-render-"):
                payload["checks"] = payload["checks"][:4] + [{"name": "configured_render", "status": "not_run"}]
                payload.update(passed=False, tests=[], custom_tests_executed=0)
            return payload

    with pytest.raises(ValidationUnavailable, match="incomplete report"):
        asyncio.run(ToolValidator(Renderer(), tmp_path).validate_render(small_request(), transparency=True))


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
# 浏览器与字体位置在导入阶段读取：autouse 隔离夹具会在用例开始时清除 IMV_* 变量。
BROWSER = {
    name: Path(value)
    for name, value in (
        ("browser_executable", os.environ.get("IMV_BROWSER_EXECUTABLE")),
        ("font_regular", os.environ.get("IMV_FONT_REGULAR")),
        ("font_bold", os.environ.get("IMV_FONT_BOLD")),
    )
    if value
}
needs_node = pytest.mark.skipif(
    not DEPENDENCIES.exists() or shutil.which("node") is None,
    reason="install locked Remotion renderer dependencies and Node",
)
needs_browser = pytest.mark.skipif(
    os.environ.get("IMV_TEST_RENDERER") != "1",
    reason="set IMV_TEST_RENDERER=1 for the Linux browser integration",
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


ERASED_TYPES_PRESET = """import React from "react";

const NO_WORDS: string[] = [];

export default function Tag(props: { label: string; words?: string[] }) {
  const words = props.words ?? NO_WORDS;
  const hits: string[] = [];
  let first: string | null = null;
  let last: string | undefined;
  const counts: Record<string, number> = {};
  let mode: string | null = "idle";
  let { tone }: { tone: string | null } = { tone: "warm" };
  const [active, setActive] = React.useState<string | null>(null);
  const box = React.useRef<HTMLDivElement>(null);
  const seen = new Set<string>();
  for (const word of words) {
    if (!props.label.includes(word)) continue;
    hits.push(word);
    first = first ?? word;
    last = word;
    counts[word] = (counts[word] ?? 0) + 1;
    seen.add(word);
  }
  for (const word of seen) mode = word.toUpperCase();
  if (hits.length === 0) { mode = null; tone = null; }
  React.useEffect(() => { if (active === null && first) setActive(first); box.current?.scrollTo(0, 0); }, [active]);
  const mark = (text: string) => (hits.includes(text) || text === first || text === last ? <b key={text}>{text}</b> : text);
  return <div ref={box} data-mode={mode ?? tone ?? ""}>{props.label.split("").map(mark)}</div>;
}
"""


def compose_and_validate(tmp_path: Path, code: str) -> tuple[dict, dict]:
    """Compose one instance of a Preset, then return the real worker reports of the Preset and of the Sprite."""
    properties = {"label": {"type": "string"}, "words": {"type": "array", "items": {"type": "string"}}}
    preset = {
        "code": code,
        "parameter_schema": {"type": "object", "required": ["label"], "additionalProperties": False, "properties": properties},
        "default_parameters": {"label": "abc"}, "description": "tag",
    }
    [item] = [{
        "instance_id": "tag", "preset": preset, "parameters": {"label": "abc"},
        "layout": {"x": 0, "y": 0, "width": 540, "height": 200, "z_index": 0}, "timing": {"start_frame": 0, "duration_frames": 30},
    }]
    sprite, schema, defaults = compose_source([item])
    (tmp_path / "preset").mkdir()
    (tmp_path / "sprite").mkdir()
    return (
        run_code_worker(tmp_path / "preset", code, properties, {"label": "abc"}),
        run_code_worker(tmp_path / "sprite", sprite, schema["properties"], defaults),
    )


@needs_node
def test_a_composed_sprite_keeps_erased_variable_types_valid_under_strict_typescript(tmp_path):
    """任务 76be0e4a 的 sprite_create 连续三次被拒：合成打包擦掉类型注解与类型参数后，`const hits: string[] = []`、`Record<string, number>` 的 `{}`、字面量联合、解构以及 `useState<T | null>(null)`、`useRef<T>(null)`、`new Set<T>()` 都会在严格模式下报错；合成产物须与原 Preset 一样通过类型检查。"""
    preset, sprite = compose_and_validate(tmp_path, ERASED_TYPES_PRESET)
    assert preset["passed"] is True, preset["diagnostics"]
    assert sprite["passed"] is True, sprite["diagnostics"]


ERASED_CLASS_PRESET = """import React from "react";

class Tally {
  items: string[] = [];
  last: string | null = null;
  declare total: number;
  constructor(private limit: number) {}
  add(word: string) { if (this.items.length < this.limit) this.items.push(word); this.last = word; this.total = this.items.length; }
}

class Label extends React.Component<{ text: string }, { open: boolean }> {
  state = { open: false };
  render() { return <span data-open={String(this.state.open)}>{this.props.text}</span>; }
}

export default function Tag(props: { label: string; words?: string[] }) {
  const tally = new Tally(3);
  for (const word of props.words ?? []) tally.add(word);
  return <Label text={`${props.label} ${tally.last ?? ""} ${tally.total}`} />;
}
"""


@needs_node
def test_a_composed_sprite_keeps_erased_class_member_types_valid_under_strict_typescript(tmp_path):
    """合成打包会擦掉类字段注解、参数属性、`declare` 字段和 `React.Component<P, S>` 的类型参数，合成产物随之报 TS2339/TS2345/TS2322；它须与原 Preset 一样通过严格类型检查。"""
    preset, sprite = compose_and_validate(tmp_path, ERASED_CLASS_PRESET)
    assert preset["passed"] is True, preset["diagnostics"]
    assert sprite["passed"] is True, sprite["diagnostics"]


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
        "excerpt": 'const out = exec("ls");',
    }]


@needs_node
def test_a_type_error_in_reformatted_code_quotes_the_checked_line(tmp_path):
    """类型检查在格式化后的副本上进行，行号可能与提交源码不同；诊断带出所指行的源码，模型仍能在自己的代码里找到它。"""
    code = 'import React from "react"; export default function T(props: { t: string }) { const n: number = props.t; return <div>{n}</div>; }'
    report = run_code_worker(tmp_path, code, {"t": {"type": "string"}}, {"t": "x"})
    assert report["passed"] is False
    [error] = [item for item in report["diagnostics"] if item.get("code") == "2322" and item.get("file") == "Template.tsx"]
    assert error["range"]["start"]["line"] > 0
    assert error["excerpt"] == "const n: number = props.t;"


@needs_node
def test_the_real_player_bundle_declares_the_host_protocol_on_its_first_line(tmp_path):
    """真实 presentation-worker 构建的包以 publication.PREVIEW_PROTOCOL 开头，两端常量不会各改各的；默认文字里的协议字样只在包内出现。"""
    root = tmp_path / "work"
    root.mkdir()
    text = "imv-preview-sync imv-preview-host " + PREVIEW_PROTOCOL.decode()
    (root / "request.json").write_text(json.dumps({
        "code": 'import React from "react"; export default function T(p: {text: string}) { return <div>{p.text}</div>; }',
        "config": {"text": text}, "composition": {"width": 1080, "height": 1920, "fps": 30, "duration_in_frames": 30},
    }), encoding="utf-8")
    environment = {**os.environ, "IMV_WORK_ROOT": str(root), "IMV_RENDERER_ROOT": str(RENDERER_DIR)}
    subprocess.run(["node", str(RENDERER_DIR / "presentation-worker.mjs")], cwd=RENDERER_DIR, env=environment, check=True, capture_output=True, timeout=120)
    bundle = (root / "interactive.js").read_bytes()
    assert current_bundle(bundle) and text.encode() in bundle
    assert not current_bundle(bundle.split(b"\n", 1)[1])


@needs_node
@pytest.mark.parametrize("statement, message, end", [
    ('import {\n  readFileSync,\n} from "node:fs";', "Import not permitted: node:fs", {"line": 3, "character": 17}),
    ('export {\n  readFileSync,\n} from "react";', "Re-exports are not permitted.", {"line": 3, "character": 15}),
], ids=["import", "re-export"])
def test_a_multiline_violation_ends_on_the_line_where_the_node_ends(tmp_path, statement, message, end):
    """审计复现：多行 import/re-export 的 range 终点曾由起始列加节点长度算出，落在起始行甚至超出行长。"""
    code = f'import React from "react";\n{statement}\nexport default function T(props: {{ t: string }}) {{\n  return <div>{{props.t}}</div>;\n}}\n'
    report = run_code_worker(tmp_path, code, {"t": {"type": "string"}}, {"t": "x"})
    assert report["passed"] is False
    [diagnostic] = report["diagnostics"]
    assert diagnostic["message"].startswith(message) and "(line 2, column 1)" in diagnostic["message"]
    assert diagnostic["range"] == {"start": {"line": 1, "character": 0}, "end": end}


# 一个有不透明根背景的「滤镜」，以及去掉背景后只用半透明层的同款，和只在中间帧不透明的转场。
OPAQUE_FILTER = """import React from "react";
import { AbsoluteFill } from "remotion";
export default function Warm(props: { tint: string }) {
  return (
    <AbsoluteFill style={{ backgroundColor: "#1A0F06" }}>
      <AbsoluteFill style={{ background: props.tint, opacity: 0.55 }} />
    </AbsoluteFill>
  );
}
"""
TRANSLUCENT_FILTER = """import React from "react";
import { AbsoluteFill } from "remotion";
export default function Warm(props: { tint: string }) {
  return (
    <AbsoluteFill>
      <AbsoluteFill style={{ background: props.tint, opacity: 0.4 }} />
      <AbsoluteFill style={{ background: "radial-gradient(circle, rgba(0,0,0,0) 40%, rgba(0,0,0,0.6) 100%)" }} />
    </AbsoluteFill>
  );
}
"""
VIEWPORT_COVER = """import React from "react";
export default function Fill(props: { tint: string }) {
  return <div style={{ position: "absolute", left: 0, top: 0, width: "100vw", height: "100vh", background: "#000" }} />;
}
"""
FADE_IN_COVER = """import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
export default function FadeIn(props: { tint: string }) {
  const frame = useCurrentFrame();
  return <AbsoluteFill style={{ backgroundColor: "#000", opacity: interpolate(frame, [0, 5], [0, 1], { extrapolateRight: "clamp" }) }} />;
}
"""
LATE_COVER = """import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
export default function Late(props: { tint: string }) {
  return <AbsoluteFill style={{ backgroundColor: props.tint, opacity: useCurrentFrame() >= 3 ? 1 : 0 }} />;
}
"""
THREE_OF_FIVE_COVER = """import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
export default function Hold(props: { tint: string }) {
  const frame = useCurrentFrame();
  return <AbsoluteFill style={{ backgroundColor: props.tint, opacity: frame >= 5 && frame <= 22 ? 1 : 0 }} />;
}
"""
MIDDLE_ONLY_COVER = """import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
export default function Wipe(props: { tint: string }) {
  const frame = useCurrentFrame();
  return <AbsoluteFill style={{ backgroundColor: props.tint, opacity: frame > 10 && frame < 20 ? 1 : 0 }} />;
}
"""


def browser_validate(tmp_path: Path, code: str, *, frames: int = 30):
    """Mount a one-parameter component in the real sandboxed browser with the transparency measurement."""
    settings = Settings(_env_file=None, data_dir=tmp_path, **BROWSER)
    component_ = ComponentDefinition(
        code=code,
        parameter_schema={"type": "object", "properties": {"tint": {"type": "string"}}, "required": ["tint"], "additionalProperties": False},
        default_parameters={"tint": "#FFB347"},
    )
    validator = ToolValidator(Renderer(settings), tmp_path)
    return asyncio.run(validator.validate_render(RenderValidationInput(component=component_, duration_frames=frames), transparency=True))


@needs_node
@needs_browser
@pytest.mark.parametrize(("code", "passed"), [
    (OPAQUE_FILTER, False),
    (VIEWPORT_COVER, False),
    (FADE_IN_COVER, False),
    (LATE_COVER, False),
    (TRANSLUCENT_FILTER, True),
    (MIDDLE_ONLY_COVER, True),
    (THREE_OF_FIVE_COVER, True),
], ids=["opaque-root-background", "viewport-sized-cover", "fading-in-cover", "late-cover", "translucent-layers",
        "opaque-only-in-the-middle", "opaque-in-three-of-five"])
def test_real_browser_measures_whether_the_video_stays_visible(tmp_path, code, passed):
    """用真实浏览器度量：不透明根背景、按 100vw/100vh 铺满、淡入或延后出现的整帧遮罩被拒（5 帧中 4 帧被盖住）；只用半透明层的滤镜、只盖住中间一两帧的转场（含 5 帧中 3 帧）通过。"""
    report = browser_validate(tmp_path, code)
    check = next(item for item in report.checks if item.name == "transparency")
    assert (check.status == "passed") is passed, check.message
    assert report.passed is passed


# 一个只在后面的帧才抛错的组件，以及一个在模块加载时就抛错、宿主根本挂不上的组件。
THROWS_LATE = """import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
export default function Late(props: { tint: string }) {
  const frame = useCurrentFrame();
  if (frame > 10) throw new Error("boom at frame " + frame);
  return <AbsoluteFill style={{ background: props.tint, opacity: 0.4 }} />;
}
"""
THROWS_ON_LOAD = """import React from "react";
const palette = JSON.parse("null").colors;
export default function Boot(props: { tint: string }) {
  return <div style={{ color: palette[0] }}>{props.tint}</div>;
}
"""


@needs_node
@needs_browser
def test_real_browser_reports_an_error_on_a_later_frame_as_the_components_failure(tmp_path):
    """审计复现：只在第 14 帧抛错的组件曾被报成验证服务不可用（runtime 错误），模型拿不到帧号和原因；现在是带帧号的失败检查。"""
    report = browser_validate(tmp_path, THROWS_LATE)
    check = next(item for item in report.checks if item.name == "transparency")
    assert report.passed is False and check.status == "failed"
    assert "frame 14" in check.message and "boom at frame 14" in check.message


@needs_node
@needs_browser
def test_real_browser_reports_an_error_thrown_while_loading_as_a_failed_default_render(tmp_path):
    """审计复现：模块加载时抛错的组件曾等 15 秒后报「Validation host did not initialize」并丢失原因；现在是 default_render 失败并带组件自己的错误。"""
    report = browser_validate(tmp_path, THROWS_ON_LOAD)
    checks = {item.name: item for item in report.checks}
    assert report.passed is False
    assert checks["default_render"].status == "failed" and "Cannot read properties of null" in checks["default_render"].message
    assert checks["configured_render"].status == "not_run"


# 一个带全部关键词参数的字效：命中关键词处按样式渲染；FAIL 为 true 时只在「有命中且样式开启」时抛错。
KEYWORD_TITLE = """import React from "react";
import { AbsoluteFill } from "remotion";
type Props = { text: string; keywords: string[]; keywordBold: boolean; keywordItalic: boolean; keywordUnderline: boolean;
  keywordStrikeout: boolean; keywordColor: string; keywordSize: number };
export default function Title(p: Props) {
  const word = p.keywords.find((item) => item !== "" && p.text.includes(item));
  const at = word === undefined ? -1 : p.text.indexOf(word);
  const styled = p.keywordBold || p.keywordItalic || p.keywordUnderline || p.keywordStrikeout || p.keywordColor !== "" || p.keywordSize !== 0;
  if (FAIL && at >= 0 && styled) throw new Error("styled keyword hit");
  const lines = [p.keywordUnderline ? "underline" : "", p.keywordStrikeout ? "line-through" : ""].filter(Boolean).join(" ");
  return <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
    <div style={{ fontSize: 64, color: "#FFFFFF" }}>
      {at < 0 ? p.text : <>{p.text.slice(0, at)}<span style={{ fontWeight: p.keywordBold ? 700 : 400, fontStyle: p.keywordItalic ? "italic" : "normal",
        textDecoration: lines || "none", color: p.keywordColor || undefined, fontSize: p.keywordSize || undefined }}>{word}</span>{p.text.slice(at + word!.length)}</>}
    </div>
  </AbsoluteFill>;
}
"""
KEYWORD_SCHEMA = {"type": "object", "properties": {
    "text": {"type": "string", "minLength": 1, "maxLength": 12},
    "keywords": {"type": "array", "items": {"type": "string", "maxLength": 4}, "maxItems": 3},
    "keywordBold": {"type": "boolean"}, "keywordItalic": {"type": "boolean"},
    "keywordUnderline": {"type": "boolean"}, "keywordStrikeout": {"type": "boolean"},
    "keywordColor": {"type": "string", "pattern": KEYWORD_COLOR},
    "keywordSize": {"type": "integer", "minimum": 0, "maximum": 300},
}, "additionalProperties": False}
KEYWORD_DEFAULTS = {"text": "今日灵感", "keywords": [], "keywordBold": False, "keywordItalic": False, "keywordUnderline": False,
                    "keywordStrikeout": False, "keywordColor": "", "keywordSize": 0}


def sample_checks(tmp_path: Path, fail: bool) -> dict[str, str]:
    """Render the publish-time boundary samples of the keyword title in the real sandboxed browser."""
    settings = Settings(_env_file=None, data_dir=tmp_path, **BROWSER)
    code = KEYWORD_TITLE.replace("FAIL", "true" if fail else "false")
    drawn = samples(KEYWORD_SCHEMA, KEYWORD_DEFAULTS, list(KEYWORD_DEFAULTS), "text", (1080, 1920))
    checks = asyncio.run(ToolValidator(Renderer(settings), tmp_path).validate_samples(
        ComponentDefinition(code=code, parameter_schema=KEYWORD_SCHEMA, default_parameters=KEYWORD_DEFAULTS),
        {"width": 1080, "height": 1920, "fps": 30, "duration_frames": 30},
        {"x": 90, "y": 760, "width": 900, "height": 400},
        [asdict(item) for item in drawn],
    ))
    return {item.name: item.status for item in checks}


@needs_node
@needs_browser
def test_real_boundary_samples_render_a_styled_keyword_hit(tmp_path):
    """审计复现：low 只有关键词、high 只有样式，组件在「命中且样式开启」时崩溃也能发布；keywords:styled 样本现在真实渲染这条路径并拦下它。"""
    assert sample_checks(tmp_path / "ok", fail=False) == {
        "sample:defaults": "passed", "sample:low": "passed", "sample:high": "passed", "sample:keywords:styled": "passed",
    }
    broken = sample_checks(tmp_path / "broken", fail=True)
    assert broken["sample:keywords:styled"] == "failed"
    assert {name: status for name, status in broken.items() if name != "sample:keywords:styled"} == {
        "sample:defaults": "passed", "sample:low": "passed", "sample:high": "passed",
    }


# 按固定帧数写出入场与出场的字效：时长短于 30 帧（例如 0.5 秒的效果）时 interpolate 的输入区间不再递增而抛错。
EDGES_BY_LENGTH = """import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
export default function Edges(props: { tint: string }) {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const opacity = interpolate(frame, [0, 15, durationInFrames - 15, durationInFrames], [0, 1, 1, 0]);
  return <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}><div style={{ color: props.tint, fontSize: 64, opacity }}>标题</div></AbsoluteFill>;
}
"""
# 入场与出场都不超过时长三分之一的同款，任意长度都能渲染。
EDGES_SCALED = EDGES_BY_LENGTH.replace(
    "const opacity = interpolate(frame, [0, 15, durationInFrames - 15, durationInFrames], [0, 1, 1, 0]);",
    "const edge = Math.max(1, Math.min(15, Math.floor(durationInFrames / 3)));\n  const opacity = interpolate(frame, [0, edge, durationInFrames - edge, durationInFrames], [0, 1, 1, 0]);",
)


@needs_node
@needs_browser
@pytest.mark.parametrize(("code", "passed"), [(EDGES_BY_LENGTH, False), (EDGES_SCALED, True)], ids=["fixed-edges", "scaled-edges"])
def test_real_browser_mounts_the_shortest_and_longest_render_length(tmp_path, code, passed):
    """总线按成片时长渲染（0.5～120 秒；更短的字幕定格在预览长度），不是按预览长度：生成检查在 15 帧与 3600 帧各挂载首、中、尾帧，只在预览长度下成立的写法以 render_lengths 失败并说明长度与帧号。"""
    settings = Settings(_env_file=None, data_dir=tmp_path, **BROWSER)
    component_ = ComponentDefinition(
        code=code,
        parameter_schema={"type": "object", "properties": {"tint": {"type": "string"}}, "required": ["tint"], "additionalProperties": False},
        default_parameters={"tint": "#FFB347"},
    )
    report = asyncio.run(ToolValidator(Renderer(settings), tmp_path).validate_render(
        RenderValidationInput(component=component_, duration_frames=150), transparency=True, lengths=render_lengths(30),
    ))
    check = next(item for item in report.checks if item.name == "render_lengths")
    assert render_lengths(30) == [15, 3600] and render_lengths(30, text=True) == [45, 3600]
    assert report.passed is passed and (check.status == "passed") is passed, check.message
    if not passed:
        assert "Rendered 15 frames long (0.5 s)" in check.message and "at frame 0" in check.message


@needs_node
@needs_browser
def test_custom_tests_run_at_the_preview_length_after_a_failed_length_check(tmp_path):
    """评审复现：两端长度检查失败后曾停在 15 帧，Actor 的自定义测试定位第 100 帧时报「frame is outside composition」；现在恢复预览长度后再跑测试。"""
    settings = Settings(_env_file=None, data_dir=tmp_path, **BROWSER)
    component_ = ComponentDefinition(
        code=EDGES_BY_LENGTH,
        parameter_schema={"type": "object", "properties": {"tint": {"type": "string"}}, "required": ["tint"], "additionalProperties": False},
        default_parameters={"tint": "#FFB347"},
    )
    script = "export default async function (ctx) { await ctx.set_frame(100); ctx.assert(true, 'frame 100 of the preview'); }"
    report = asyncio.run(ToolValidator(Renderer(settings), tmp_path).validate_render(
        RenderValidationInput(component=component_, duration_frames=150, tests=[TestScript(name="late", code=script)]),
        transparency=True, lengths=render_lengths(30),
    ))
    assert next(item for item in report.checks if item.name == "render_lengths").status == "failed"
    assert [(item.name, item.status) for item in report.tests] == [("late", "passed")], report.tests


def composed_instances() -> dict:
    """组合两个实例的 Sprite：full 覆盖整段 30 帧预览，middle 只在第 10～19 帧出现。"""
    preset = {
        "code": 'import React from "react"; export default function Mark(p: {label: string}) { return <div style={{ color: "#FFFFFF" }}>{p.label}</div>; }',
        "parameter_schema": {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"], "additionalProperties": False},
        "default_parameters": {"label": "x"}, "description": "mark",
    }
    instance = lambda name, start, length: {
        "instance_id": name, "preset": preset, "parameters": {"label": name},
        "layout": {"x": 0, "y": 0, "width": 540, "height": 200, "z_index": 0},
        "timing": {"start_frame": start, "duration_frames": length},
    }
    code, schema, defaults = compose_source([instance("full", 0, 30), instance("middle", 10, 10)])
    return {"code": code, "parameter_schema": schema, "default_parameters": defaults}


@needs_node
@needs_browser
@pytest.mark.parametrize(("length", "frame", "full", "middle"), [
    (300, 200, True, False), (300, 15, True, True), (15, 7, True, True), (15, 2, True, False),
], ids=["longer-full-runs-to-end", "longer-middle-keeps-its-frames", "shorter-scaled-middle", "shorter-before-middle"])
def test_real_browser_lays_out_composed_instances_at_the_render_length(tmp_path, length, frame, full, middle):
    """Agent 按预览长度排布实例，宿主按实际渲染长度排布：更长时开头的实例保持原帧、一直到预览结尾的实例延续到新结尾；更短时全部按比例缩短。"""
    settings = Settings(_env_file=None, data_dir=tmp_path, **BROWSER)
    script = f"""export default async function (ctx) {{
  await ctx.set_frame({frame});
  ctx.assert_equal((await ctx.query('[data-imv-instance="full"]')) !== null, {str(full).lower()}, "full instance");
  ctx.assert_equal((await ctx.query('[data-imv-instance="middle"]')) !== null, {str(middle).lower()}, "middle instance");
}}"""
    report = asyncio.run(ToolValidator(Renderer(settings), tmp_path).validate_render(RenderValidationInput(
        component=ComponentDefinition(**composed_instances()), duration_frames=length, tests=[TestScript(name="layout", code=script)],
    )))
    assert report.passed is True, [*report.checks, *report.tests]


def test_text_fit_is_requested_and_its_stage_is_required_only_for_text_effects(tmp_path):
    """文字检查只在调用方要求时下发到 worker；要求了却没有该阶段的报告是验证服务故障，不能当作通过。"""
    class Renderer(FakeRenderer):
        """Record requests and append the text_fit stage when asked for, as the real worker does."""

        requests: list[dict] = []

        async def run_worker(self, directory: Path, *, worker: str, timeout_seconds=None):
            """Return the base report plus a failed text_fit check."""
            payload = await super().run_worker(directory, worker=worker, timeout_seconds=timeout_seconds)
            request = json.loads((directory / "request.json").read_text())
            self.requests.append(request)
            if request["mode"] == "render" and request.get("text_fit"):
                payload["checks"].append({"name": "text_fit", "status": "failed", "message": "the text is cut off"})
                payload["passed"] = False
            return payload

    renderer = Renderer()
    plain = asyncio.run(ToolValidator(renderer, tmp_path).validate_render(small_request()))
    assert plain.passed is True and "text_fit" not in [item.name for item in plain.checks]
    fitted = asyncio.run(ToolValidator(renderer, tmp_path).validate_render(small_request(), text_fit=True))
    assert [request["text_fit"] for request in renderer.requests if request["mode"] == "render"] == [False, True]
    assert fitted.passed is False
    assert next(item for item in fitted.checks if item.name == "text_fit").message == "the text is cut off"
    with pytest.raises(ValidationUnavailable, match="incomplete report"):
        asyncio.run(ToolValidator(FakeRenderer(), tmp_path).validate_render(small_request(), text_fit=True))


# 带 text 与 fontSize 的字效：972x360 固定框 + overflow hidden（issue #119 的写法）、按字数缩字号并自动换行的写法，
# 只在前几帧被 overflow 遮罩挡住的短文字，以及完全不显示 text 的组件。
FIXED_BOX_TEXT = """import React from "react";
import { AbsoluteFill } from "remotion";
export default function Title(props: { text: string; fontSize: number }) {
  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div style={{ width: 972, height: 360, overflow: "hidden", display: "flex", alignItems: "center", justifyContent: "center", textAlign: "center" }}>
        <span style={{ fontSize: props.fontSize, whiteSpace: "nowrap", color: "#fff" }}>{props.text}</span>
      </div>
    </AbsoluteFill>
  );
}
"""
# 不按字数自行换算字号：100 字在默认的 128px 下排成 2300px 高放不下，只有宿主缩到声明的最小字号 24 时才放得下。
PLAIN_WRAP_TEXT = """import React from "react";
import { AbsoluteFill } from "remotion";
export default function Title(props: { text: string; fontSize: number }) {
  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div style={{ width: 972, textAlign: "center", fontSize: props.fontSize, lineHeight: 1.2, whiteSpace: "normal", wordBreak: "break-all", color: "#fff" }}>{props.text}</div>
    </AbsoluteFill>
  );
}
"""
FITTING_TEXT = """import React from "react";
import { AbsoluteFill } from "remotion";
export default function Title(props: { text: string; fontSize: number }) {
  const size = Math.max(24, Math.min(props.fontSize, Math.floor(Math.sqrt((972 * 1000) / (1.3 * props.text.length)))));
  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div style={{ width: 972, textAlign: "center", fontSize: size, lineHeight: 1.2, whiteSpace: "normal", wordBreak: "break-all", color: "#fff" }}>{props.text}</div>
    </AbsoluteFill>
  );
}
"""
REVEAL_MASK_TEXT = """import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
export default function Title(props: { text: string; fontSize: number }) {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div style={{ width: 972, height: 200, overflow: "hidden", textAlign: "center" }}>
        <div style={{ fontSize: props.fontSize, lineHeight: "200px", color: "#fff", transform: `translateY(${frame < 10 ? 300 : 0}px)` }}>{props.text}</div>
      </div>
    </AbsoluteFill>
  );
}
"""
# 与 FITTING_TEXT 同样换行缩字号，但只渲染前 10 个字（截断）、把文字放进 display: none 的元素，或把每个字拆成一个 span。
SLICED_TEXT = FITTING_TEXT.replace("{props.text}</div>", "{props.text.slice(0, 10)}</div>")
HIDDEN_TEXT = FITTING_TEXT.replace('color: "#fff" }}>', 'color: "#fff", display: "none" }}>')
PER_GLYPH_TEXT = FITTING_TEXT.replace("{props.text}</div>", '{props.text.split("").map((glyph, index) => <span key={index}>{glyph}</span>)}</div>')
# 一行文字放在恰好一行高的 overflow hidden 遮罩里（lineHeight 1）：字形没有被裁，但字体的上下伸部超出行框。
LINE_MASK_TEXT = """import React from "react";
import { AbsoluteFill } from "remotion";
export default function Title(props: { text: string; fontSize: number }) {
  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div style={{ width: 972, height: props.fontSize, overflow: "hidden", textAlign: "center" }}>
        <div style={{ fontSize: props.fontSize, lineHeight: 1, whiteSpace: "nowrap", color: "#fff" }}>{props.text}</div>
      </div>
    </AbsoluteFill>
  );
}
"""
# 972x360 的固定框裁掉长文字，只在最后两帧把文字缩到 30% 退场（20 字时 768px 宽，仍可读）；以及先逐字打出、后半段完整显示的打字机效果。
SCALE_EXIT_TEXT = """import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from "remotion";
export default function Title(props: { text: string; fontSize: number }) {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const scale = frame >= durationInFrames - 2 ? 0.3 : 1;
  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div style={{ width: 972, height: 360, overflow: "hidden" }}>
        <span style={{ display: "inline-block", fontSize: props.fontSize, whiteSpace: "nowrap", color: "#fff", transformOrigin: "left center", transform: `scale(${scale})` }}>{props.text}</span>
      </div>
    </AbsoluteFill>
  );
}
"""
# 更早封存的组合 Sprite 的写法：每个实例放在按布局大小定位、overflow hidden 的盒子里（盒子的子元素带 data-imv-instance），内容自己没有固定框。
SEALED_INSTANCE_BOX_TEXT = """import React from "react";
import { AbsoluteFill } from "remotion";
export default function Title(props: { text: string; fontSize: number }) {
  return (
    <AbsoluteFill>
      <div style={{ position: "absolute", left: 80, top: 810, width: 920, height: 300, overflow: "hidden" }}>
        <div data-imv-instance="title" style={{ width: "100%", height: "100%" }}>
          <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
            <div style={{ width: 920, textAlign: "center", fontSize: props.fontSize, lineHeight: 1.2, whiteSpace: "normal", wordBreak: "break-all", color: "#fff" }}>{props.text}</div>
          </AbsoluteFill>
        </div>
      </div>
    </AbsoluteFill>
  );
}
"""
# 与 FIXED_BOX_TEXT 同样的固定框，但用 overflow auto 或 contain: paint 裁切：视频里不会滚动，超出部分同样看不到。
# 8 个 128px 的字（1024px）留在画布内，只被 972px 宽的框裁掉。
SCROLL_BOX_TEXT = FIXED_BOX_TEXT.replace('overflow: "hidden"', 'overflow: "auto"')
PAINT_CONTAINED_TEXT = FIXED_BOX_TEXT.replace('overflow: "hidden"', 'contain: "paint"')
TYPEWRITER_TEXT = FITTING_TEXT.replace(
    "{props.text}</div>", "{props.text.slice(0, Math.ceil((props.text.length * (useCurrentFrame() + 1)) / 15))}</div>",
).replace('import { AbsoluteFill } from "remotion";', 'import { AbsoluteFill, useCurrentFrame } from "remotion";')
IGNORES_TEXT = """import React from "react";
import { AbsoluteFill } from "remotion";
export default function Title(props: { text: string; fontSize: number }) {
  return <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", fontSize: props.fontSize, color: "#fff" }}>fixed</AbsoluteFill>;
}
"""


def browser_text_validate(tmp_path: Path, code: str, max_length: int, *, minimum: int = 24, defaults: dict | None = None):
    """Mount a text effect with the generation-path measurements in the real sandboxed browser."""
    settings = Settings(_env_file=None, data_dir=tmp_path, **BROWSER)
    component_ = ComponentDefinition(
        code=code,
        parameter_schema={
            "type": "object",
            "properties": {"text": {"type": "string", "minLength": 1, "maxLength": max_length}, "fontSize": {"type": "number", "minimum": minimum, "maximum": 240}},
            "required": ["text", "fontSize"],
            "additionalProperties": False,
        },
        default_parameters=defaults or {"text": "标题", "fontSize": 128},
    )
    validator = ToolValidator(Renderer(settings), tmp_path)
    request = RenderValidationInput(component=component_, duration_frames=30)
    return asyncio.run(validator.validate_render(request, transparency=True, text_fit=True))


@needs_node
@needs_browser
@pytest.mark.parametrize(("code", "max_length", "passed", "reason"), [
    (FIXED_BOX_TEXT, 100, False, "overflow hidden"),
    (FITTING_TEXT, 100, True, ""),
    (REVEAL_MASK_TEXT, 4, True, ""),
    (IGNORES_TEXT, 100, False, "never renders"),
    (SLICED_TEXT, 100, False, "only part of it is rendered"),
    (HIDDEN_TEXT, 100, False, "never renders"),
    (PER_GLYPH_TEXT, 100, True, ""),
    (LINE_MASK_TEXT, 4, True, ""),
    (SCALE_EXIT_TEXT, 100, False, "overflow"),
    (TYPEWRITER_TEXT, 100, True, ""),
    (SCROLL_BOX_TEXT, 100, False, "overflow auto"),
    (PAINT_CONTAINED_TEXT, 100, False, "contain: paint"),
    (SEALED_INSTANCE_BOX_TEXT, 15, True, ""),
    (PLAIN_WRAP_TEXT, 100, True, "smallest fontSize 24"),
], ids=["fixed-box-clips-long-title", "wrapped-and-shrunk", "reveal-mask-only-early", "ignores-text", "sliced-text", "display-none", "one-span-per-glyph",
        "line-height-1-mask", "clipped-until-scaled-out", "typewriter", "overflow-auto-box", "paint-contained-box", "sealed-instance-box", "plain-wrap-fits-at-smallest-size"])
def test_real_browser_requires_the_longest_text_to_be_shown_in_full(tmp_path, code, max_length, passed, reason):
    """issue #119：972x360 的 overflow hidden 固定框在 100 字标题下被裁切而被拒；按字数缩小字号并换行的写法、只在开头几帧被遮罩挡住的文字通过；
    不显示 text 的组件被拒。只渲染一段截断的文字、或文字在 display: none 里没有排版框，都不算完整显示；逐字拆成 span 的完整文字仍算。
    lineHeight 1 的一行文字放在恰好一行高的遮罩里不算裁切：按行框而不是字体的上下伸部度量。
    文字只在退场缩小时才放得下仍被拒：按文字显示最大的帧判断；逐字打出的文字按完整显示的帧判断，通过。
    overflow auto 与 contain: paint 的固定框同样算裁切。

    文字按声明的最小字号（24）填满 maxLength 后再度量：宿主渲染时会把长文字缩到这个下限，所以只在大字号下放不下的写法通过，
    裁切用例都用 100 字，在最小字号下仍被固定框裁掉。"""
    report = browser_text_validate(tmp_path, code, max_length)
    check = next(item for item in report.checks if item.name == "text_fit")
    assert (check.status == "passed") is passed, check.message
    assert reason in check.message
    if code == FIXED_BOX_TEXT:
        assert "Keep text maxLength exactly 100" in check.message
    assert report.passed is passed


# 场景：资产声明的 fontSize 最小值低于 24（如 8）。宿主渲染时最多把文字缩到 24（再小的行高算不可读），生成关卡也在 24 下量，
# 所以不按字数换算字号的写法通过，而不是被“行高太小”误拒。
@needs_node
@needs_browser
def test_real_browser_gate_measures_at_the_readable_size_when_the_declared_minimum_is_smaller(tmp_path):
    """最小值 8：关卡在 24 下量 100 字并通过。"""
    check = next(item for item in browser_text_validate(tmp_path, PLAIN_WRAP_TEXT, 100, minimum=8).checks if item.name == "text_fit")
    assert check.status == "passed", check.message
    assert "smallest fontSize 24" in check.message


# 场景：默认文字在默认字号下就被固定高度的框裁掉（8 个字、字号 240 排成两行 576px，框只有 360px），而 100 字在 24px 下放得下：
# 用户首先看到的默认示例不完整，关卡拒绝并点明默认文字；默认字号小一点（128）时通过。
@needs_node
@needs_browser
@pytest.mark.parametrize(("font_size", "passed"), [(240, False), (128, True)])
def test_real_browser_gate_requires_the_default_text_to_fit_at_the_default_size(tmp_path, font_size, passed):
    """默认 8 字：字号 240 被拒并提到 default text；字号 128 通过。"""
    code = PLAIN_WRAP_TEXT.replace('textAlign: "center",', 'textAlign: "center", height: 360, overflow: "hidden",')
    report = browser_text_validate(tmp_path, code, 100, defaults={"text": "测" * 8, "fontSize": font_size})
    check = next(item for item in report.checks if item.name == "text_fit")
    assert (check.status == "passed") is passed, check.message
    assert passed or "default text" in check.message
