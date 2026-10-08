"""字效成功版本的只读代码诊断接口：真实封存校验、诊断透出与不可用降级。

不访问真实模型、浏览器或 bubblewrap；隔离类型检查用离线 worker 替身驱动，
真实 Linux 沙箱结论由用户在部署机执行。在 server/ 目录执行
`uv run --locked pytest tests/test_remotion_version_diagnostics.py -v`。
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.remotion_templates.harness import Harness
from server.remotion_templates.models import GenerateTemplateRequest
from server.remotion_templates.provider import Budget
from server.remotion_templates.routes import router
from server.remotion_templates.runtime import Runtime
from server.remotion_templates.settings import Settings
from server.remotion_templates.store import Store
from server.remotion_templates.tool_validation import ToolValidator, ValidationUnavailable
from server.remotion_templates.tools.contracts import (
    CodeValidationReport,
    ComponentDefinition,
    RenderValidationReport,
    SpriteDraft,
)

CODE = (
    'import React from "react";\n'
    "export default function Sprite(p: {title: {text: string}}) {\n"
    "  return <div>{p.title.text}</div>;\n"
    "}\n"
)

LSP_WARNING = {
    "source": "lsp",
    "severity": "warning",
    "message": "'unused' is declared but its value is never read.",
    "file": "Template.tsx",
    "code": "6133",
    "range": {"start": {"line": 1, "character": 6}, "end": {"line": 1, "character": 12}},
}


@pytest.fixture
def sprite():
    """组合 Sprite 的源码与嵌套默认参数必须彼此一致，契约检查才会进入隔离 worker。"""
    return SpriteDraft(
        description="标题与说明",
        code=CODE,
        parameter_schema={
            "type": "object",
            "properties": {"title": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False}},
            "required": ["title"],
            "additionalProperties": False,
        },
        default_parameters={"title": {"text": "今日灵感"}},
        composition={"width": 1080, "height": 1920, "fps": 30, "duration_frames": 30},
        instances=[
            {
                "instance_id": "title",
                "preset": {
                    "code": 'import React from "react"; export default function Title(p: {text: string}) { return <div>{p.text}</div>; }',
                    "parameter_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False},
                    "default_parameters": {"text": "今日灵感"},
                    "description": "标题",
                },
                "parameters": {"text": "今日灵感"},
                "layout": {"x": 0, "y": 0, "width": 1080, "height": 1920, "z_index": 0},
                "timing": {"start_frame": 0, "duration_frames": 30},
            }
        ],
    )


def runtime_report(sprite):
    """显式离线运行回执；本文件不宣称真实浏览器或沙箱执行。"""
    return RenderValidationReport(
        passed=True,
        composition=sprite.composition,
        code_validation={"passed": True, "diagnostics": []},
        checks=[{"name": "default_render", "status": "passed"}, {"name": "configured_render", "status": "passed"}],
        tests=[],
        custom_tests_executed=0,
    )


class CodeOnlyRenderer:
    """只实现代码诊断路径的渲染器替身，记录真实请求以便断言宿主传入的字段。"""

    def __init__(self, settings, payload=None, failure=None):
        """保存本次要返回的 worker 报告或要抛出的失败。"""
        self.settings = settings
        self.payload = payload
        self.failure = failure
        self.requests: list[dict] = []

    def worker_browser_path(self):
        """无需真实浏览器可执行文件。"""
        return "/usr/bin/chromium"

    async def run_worker(self, directory, *, worker=None, timeout_seconds=None):
        """读取宿主写入的请求并返回固定诊断，或按要求失败。"""
        self.requests.append(json.loads((directory / "request.json").read_text()))
        if self.failure is not None:
            raise self.failure
        return self.payload


class OfflineRenderer:
    """把发布构建替换为确定性替身，代码诊断转发给可控替身；其余路径保持真实。"""

    def __init__(self, settings, code):
        """注入临时字体与代码诊断替身。"""
        self.settings = settings
        self.code = code

    def worker_browser_path(self):
        """无需真实浏览器可执行文件。"""
        return self.code.worker_browser_path()

    async def run_worker(self, directory, *, worker=None, timeout_seconds=None):
        """代码诊断请求转发给替身，发布构建落盘确定性产物。"""
        if worker == "tool-validation-worker.mjs":
            return await self.code.run_worker(directory, worker=worker)
        assert worker == "presentation-worker.mjs"
        request = json.loads((directory / "request.json").read_text())
        (directory / "Template.tsx").write_text(request["code"])
        (directory / "Export.tsx").write_text(request["code"])
        (directory / "interactive.js").write_text("// offline Player fixture")
        return {"checks": [{"name": "export_source", "status": "pass"}, {"name": "presentation_bundle", "status": "pass"}]}


@pytest.fixture
def published(tmp_path, sprite):
    """发布一个真实封存的成功版本，并返回其 ID、Store 与可注入的代码诊断替身。"""
    font = tmp_path / "font.ttc"
    font.write_bytes(b"fixture-font")
    settings = Settings(_env_file=None, data_dir=tmp_path / "state", font_regular=font, font_bold=font)
    code_renderer = CodeOnlyRenderer(settings)
    harness = Harness(SimpleNamespace(settings=settings), OfflineRenderer(settings, code_renderer))
    store = Store(settings.data_dir)
    store.initialize()
    work, job = store.create(GenerateTemplateRequest(description="标题"))
    store.claim()
    result = asyncio.run(
        harness.finalize_sprite(
            SimpleNamespace(
                saved_sprite=lambda identifier: _SavedSprite(sprite),
                validation_for=lambda requested: runtime_report(sprite),
            ),
            "saved",
            Budget(),
            store.job_dir(job.id),
        )
    )
    version = store.publish(job.id, *result)
    return SimpleNamespace(version=version, store=store, settings=settings, code_renderer=code_renderer, harness=harness)


class _SavedSprite:
    """提供 finalize_sprite 需要的字段子集，避免重复构造入库记录。"""

    def __init__(self, sprite: SpriteDraft):
        """保存待发布的组合定义。"""
        self._sprite = sprite

    def model_dump(self, **kwargs):
        """按调用方要求导出组合定义。"""
        return self._sprite.model_dump(**kwargs)


def client_for(published):
    """构造只挂载模板路由、注入真实 Runtime 的测试应用。"""
    service = Runtime(published.store, published.harness, published.settings)
    application = FastAPI()
    application.include_router(router, prefix="/api/templates")
    application.state.runtime = service
    return TestClient(application)


def test_diagnostics_return_real_lsp_ranges(published):
    """隔离 worker 的 LSP 诊断按原文透出，包含文件、行列与错误码。"""
    published.code_renderer.payload = {
        "passed": False,
        "checks": [
            {"name": "source_policy", "status": "pass"},
            {"name": "export_source", "status": "pass"},
            {"name": "typescript", "status": "failed", "message": "error TS6133"},
        ],
        "diagnostics": [LSP_WARNING],
    }
    with client_for(published) as client:
        response = client.get(f"/api/templates/versions/{published.version.id}/diagnostics")
    assert response.status_code == 200
    payload = response.json()
    assert payload["passed"] is False
    diagnostic = payload["diagnostics"][0]
    assert diagnostic["source"] == "lsp"
    assert diagnostic["severity"] == "warning"
    assert diagnostic["code"] == "6133"
    assert diagnostic["file"] == "Template.tsx"
    assert diagnostic["range"]["start"] == {"line": 1, "character": 6}
    assert diagnostic["range"]["end"] == {"line": 1, "character": 12}
    # 宿主必须把封存的源码、Schema 与默认参数一并交给隔离检查，否则调用点无法构造。
    request = published.code_renderer.requests[0]
    assert request["mode"] == "code"
    assert request["code"] == published.version.candidate.tsx_code
    assert request["default_parameters"] == published.version.candidate.default_config
    assert request["parameter_schema"] == published.version.candidate.config_schema


def test_clean_typecheck_passes_without_diagnostics(published):
    """无诊断时返回 passed=true 与空列表，不伪造结论。"""
    published.code_renderer.payload = {
        "passed": True,
        "checks": [
            {"name": "source_policy", "status": "pass"},
            {"name": "export_source", "status": "pass"},
            {"name": "typescript", "status": "pass"},
        ],
        "diagnostics": [],
    }
    with client_for(published) as client:
        response = client.get(f"/api/templates/versions/{published.version.id}/diagnostics")
    assert response.status_code == 200
    assert response.json() == {"passed": True, "diagnostics": []}


def test_source_policy_failure_becomes_a_contract_error(published):
    """非 typecheck 的失败检查归一化为 error 诊断，不会被当成通过。"""
    published.code_renderer.payload = {
        "passed": False,
        "checks": [
            {"name": "source_policy", "status": "failed", "message": "Import not permitted: node:fs"},
            {"name": "export_source", "status": "pass"},
            {"name": "typescript", "status": "pass"},
        ],
        "diagnostics": [],
    }
    with client_for(published) as client:
        response = client.get(f"/api/templates/versions/{published.version.id}/diagnostics")
    payload = response.json()
    assert payload["passed"] is False
    assert [item["message"] for item in payload["diagnostics"]] == ["Import not permitted: node:fs"]
    assert payload["diagnostics"][0]["source"] == "contract"


def test_tampered_acceptance_artifacts_are_not_diagnosed(published):
    """封存产物被改写后返回 404，不返回与当前字节不符的诊断。"""
    with client_for(published) as client:
        accepted = published.store.root / "accepted" / str(published.version.id)
        (accepted / "Export.tsx").write_text("tampered")
        response = client.get(f"/api/templates/versions/{published.version.id}/diagnostics")
    assert response.status_code == 404
    assert published.code_renderer.requests == []


def test_missing_version_returns_not_found(published):
    """未知版本直接 404，不启动隔离 worker。"""
    with client_for(published) as client:
        response = client.get(f"/api/templates/versions/{uuid4()}/diagnostics")
    assert response.status_code == 404
    assert published.code_renderer.requests == []


def test_unavailable_sandbox_reports_503(published):
    """隔离运行时不可用时返回 503，调用方据此保留代码显示。"""
    published.code_renderer.failure = RuntimeError("bwrap is not available")
    with client_for(published) as client:
        response = client.get(f"/api/templates/versions/{published.version.id}/diagnostics")
    assert response.status_code == 503
    assert "暂不可用" in response.json()["detail"]


def test_contract_failure_skips_the_isolated_worker(published):
    """契约不成立时只返回契约诊断，不消耗隔离运行时。"""
    component = ComponentDefinition(
        code=CODE,
        parameter_schema={
            "type": "object",
            "properties": {"title": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False}},
            "required": ["title"],
            "additionalProperties": False,
        },
        default_parameters={"title": {"text": 3}},
    )
    report = asyncio.run(ToolValidator(published.code_renderer, published.settings.data_dir / "contract").code_report(component))
    assert report.passed is False
    assert report.diagnostics and all(item.source == "contract" for item in report.diagnostics)
    assert published.code_renderer.requests == []


def test_validate_code_and_code_report_share_one_conclusion(published):
    """create 路径与只读诊断路径使用同一套归一化，结论必须一致。"""
    published.code_renderer.payload = {
        "passed": False,
        "checks": [
            {"name": "source_policy", "status": "pass"},
            {"name": "export_source", "status": "pass"},
            {"name": "typescript", "status": "failed", "message": "error TS6133"},
        ],
        "diagnostics": [LSP_WARNING],
    }
    root = published.settings.data_dir / "shared"
    component = ComponentDefinition(
        code=CODE,
        parameter_schema=published.version.candidate.config_schema,
        default_parameters=published.version.candidate.default_config,
    )
    direct = asyncio.run(ToolValidator(published.code_renderer, root / "a").validate_code(component))
    read_only = asyncio.run(ToolValidator(published.code_renderer, root / "b").code_report(component))
    assert direct == read_only
    assert isinstance(read_only, CodeValidationReport)


def test_unavailable_sandbox_is_reported_as_validation_unavailable(published):
    """sandbox 缺失必须以 ValidationUnavailable 上抛，由路由映射为 503。"""
    published.code_renderer.failure = RuntimeError("bwrap is not available")
    component = ComponentDefinition(
        code=CODE,
        parameter_schema=published.version.candidate.config_schema,
        default_parameters=published.version.candidate.default_config,
    )
    with pytest.raises(ValidationUnavailable):
        asyncio.run(ToolValidator(published.code_renderer, published.settings.data_dir / "failing").code_report(component))


def test_incomplete_worker_report_is_rejected(published):
    """worker 报告缺少必需检查名时不得降级为通过。"""
    published.code_renderer.payload = {"passed": True, "checks": [{"name": "typescript", "status": "pass"}], "diagnostics": []}
    component = ComponentDefinition(
        code=CODE,
        parameter_schema=published.version.candidate.config_schema,
        default_parameters=published.version.candidate.default_config,
    )
    with pytest.raises(ValidationUnavailable):
        asyncio.run(ToolValidator(published.code_renderer, published.settings.data_dir / "incomplete").code_report(component))


def test_diagnostics_do_not_create_versions_or_jobs(published):
    """只读诊断不排队、不发布，作品的成功版本指针保持不变。"""
    published.code_renderer.payload = {
        "passed": True,
        "checks": [
            {"name": "source_policy", "status": "pass"},
            {"name": "export_source", "status": "pass"},
            {"name": "typescript", "status": "pass"},
        ],
        "diagnostics": [],
    }
    before = published.store.project(published.version.project_id).current_version_id
    with client_for(published) as client:
        assert client.get(f"/api/templates/versions/{published.version.id}/diagnostics").status_code == 200
    project = published.store.project(published.version.project_id)
    assert project.current_version_id == before
    assert [item.id for item in published.store.versions(published.version.project_id)] == [published.version.id]
