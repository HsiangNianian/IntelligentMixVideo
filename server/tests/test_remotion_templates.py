"""Shared bundler determinism and renderer sandbox regressions.

The shared Preset catalog lives in MySQL only, so every test here uses the temporary SQLite stand-in.
"""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.remotion_templates.provider import Budget
from server.remotion_templates.settings import Settings
from server.remotion_templates.tools.compose import compose_source
from server.remotion_templates.tools.contracts import ComponentDefinition
from server.remotion_templates.tools.session import ToolSession


RENDERER_DEPS = Path(__file__).parents[1] / "src" / "server" / "remotion" / "node_modules" / "esbuild"


@pytest.fixture(autouse=True)
def catalog_database(template_db):
    """Preset catalog reads and writes go to the temporary database standing in for MySQL."""
    return template_db


def session(tmp_path: Path) -> ToolSession:
    """Build a creation-only session without a model or browser dependency."""
    settings = Settings(_env_file=None, data_dir=tmp_path)
    harness = SimpleNamespace(settings=settings, renderer=None)
    return ToolSession(harness, None, None, Budget(), tmp_path / "run", [], lambda *_: None, {})


def test_compose_source_is_stable_across_parameter_key_order():
    """参数键顺序变化必须生成完全相同的源码：模型回显 JSON 时键序合法地会变。

    参数键序变化不能改变源码及对应的校验缓存；同一份工作区需要确定性的输出。
    """
    parameters = {"text": "今日灵感", "size": 64, "color": "#FFFFFF"}

    def instance(values):
        """构造一个最小可组合实例，字段与 compose 契约一致。"""
        return {
            "instance_id": "title",
            "preset": {
                "code": 'import React from "react"; export default function Label(p: {text: string; size: number; color: string}) { return <div>{p.text}</div>; }',
                "parameter_schema": {"type": "object", "properties": {"text": {"type": "string"}, "size": {"type": "number"}, "color": {"type": "string"}}, "additionalProperties": False},
                "default_parameters": values,
                "description": "title",
            },
            "parameters": values,
            "layout": {"x": 0, "y": 0, "width": 1080, "height": 1920, "z_index": 0},
            "timing": {"start_frame": 0, "duration_frames": 30},
        }

    first = compose_source([instance(parameters)])
    reordered = {key: parameters[key] for key in reversed(list(parameters))}
    assert compose_source([instance(reordered)]) == first


@pytest.mark.parametrize(
    ("platform", "present", "expected"),
    [
        ("darwin", {"bwrap", "prlimit", "taskset", "node"}, "darwin"),
        ("linux", {"prlimit", "node"}, "bwrap"),
        ("linux", {"bwrap", "node"}, "prlimit"),
        ("linux", {"bwrap", "prlimit", "taskset"}, "node"),
        ("linux", {"bwrap", "prlimit", "taskset", "node"}, None),
    ],
)
def test_sandbox_problem_names_the_unsupported_piece(monkeypatch, tmp_path, platform, present, expected):
    """非 Linux 先报平台；Linux 逐项报缺少的工具；全部具备时返回 None，保持原流程。"""
    from server.remotion_templates import renderer as renderer_module

    monkeypatch.setattr(renderer_module.sys, "platform", platform)
    monkeypatch.setattr(renderer_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name in present else None)
    problem = renderer_module.Renderer(Settings(_env_file=None, data_dir=tmp_path)).sandbox_problem()
    assert (problem is None) if expected is None else (expected in problem)


def test_validation_unavailable_public_message_is_generic():
    """同一错误码也覆盖 worker 报告无效、超时等原因，公开文案不指向某个具体缺失，也不暴露异常原文。"""
    from server.remotion_templates.progress import RoundCall

    call = RoundCall(tool="preset.create", status="fail", error_code="VALIDATION_UNAVAILABLE", message="[Errno 2] 'bwrap'")
    assert call.error_code == "VALIDATION_UNAVAILABLE"
    assert "校验环境" in call.message and "Linux" not in call.message and "bubblewrap" not in call.message
    assert "Errno" not in call.message


def test_macos_sandbox_problem_mentions_the_development_switch(monkeypatch, tmp_path):
    """macOS 未开启免沙箱时，原因里直接给出可用的开发开关；其他系统不提这个开关。"""
    from server.remotion_templates import renderer as renderer_module

    renderer = renderer_module.Renderer(Settings(_env_file=None, data_dir=tmp_path))
    for platform, mentioned in (("darwin", True), ("win32", False)):
        monkeypatch.setattr(renderer_module.sys, "platform", platform)
        assert ("IMV_ALLOW_UNSANDBOXED_WORKER" in renderer.sandbox_problem()) is mentioned


@pytest.mark.parametrize(
    ("platform", "allowed", "isolated"),
    [("darwin", True, False), ("darwin", False, True), ("linux", True, True), ("win32", True, True)],
)
def test_unsandboxed_worker_is_macos_development_opt_in_only(monkeypatch, tmp_path, platform, allowed, isolated):
    """只有 macOS 且显式开启时生成流程的 worker 才以普通 Node 运行；其他系统即使开启也保持沙箱。"""
    from server.remotion_templates import renderer as renderer_module

    monkeypatch.setattr(renderer_module.sys, "platform", platform)
    monkeypatch.setattr(renderer_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    settings = Settings(_env_file=None, data_dir=tmp_path, allow_unsandboxed_worker=allowed)
    renderer = renderer_module.Renderer(settings)
    for worker in renderer_module.UNSANDBOXED_WORKERS:
        command = renderer.command(tmp_path, worker=worker)
        environment = renderer.worker_environment(tmp_path, worker)
        if isolated:
            assert command[0] == "taskset" and command[3] == "bwrap" and "IMV_WORK_ROOT" not in environment
        else:
            assert renderer.sandbox_problem() is None
            assert "bwrap" not in command and command[-1].endswith(worker)
            assert environment["IMV_WORK_ROOT"] == str(tmp_path.resolve())
            assert environment["IMV_RENDERER_ROOT"] == str(settings.renderer_dir.resolve())
            assert set(environment) == {"PATH", "HOME", "IMV_WORK_ROOT", "IMV_RENDERER_ROOT"}


def test_overlay_worker_keeps_the_sandbox_even_when_opted_out(monkeypatch, tmp_path):
    """叠加渲染（含重启后恢复任务）不在免沙箱名单内：即使开启开关，仍走 bwrap，环境变量也不放开路径。"""
    from server.remotion_templates import renderer as renderer_module

    monkeypatch.setattr(renderer_module.sys, "platform", "darwin")
    monkeypatch.setattr(renderer_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    renderer = renderer_module.Renderer(Settings(_env_file=None, data_dir=tmp_path, allow_unsandboxed_worker=True))
    assert "overlay-worker.mjs" not in renderer_module.UNSANDBOXED_WORKERS
    assert renderer.command(tmp_path, worker="overlay-worker.mjs")[3] == "bwrap"
    assert "IMV_WORK_ROOT" not in renderer.worker_environment(tmp_path, "overlay-worker.mjs")


def test_unsandboxed_macos_still_requires_node(monkeypatch, tmp_path):
    """免沙箱模式仍要求 Node；缺失时给出明确原因而不是启动失败。"""
    from server.remotion_templates import renderer as renderer_module

    monkeypatch.setattr(renderer_module.sys, "platform", "darwin")
    monkeypatch.setattr(renderer_module.shutil, "which", lambda name: None)
    renderer = renderer_module.Renderer(Settings(_env_file=None, data_dir=tmp_path, allow_unsandboxed_worker=True))
    assert renderer.sandbox_problem() == "node is not installed"


def test_session_stays_importable_without_the_validation_module(monkeypatch, tmp_path):
    """部署可裁剪校验模块：session 仍可导入，构造时降级为无校验器，校验返回 VALIDATION_UNAVAILABLE。

    修复前 session 在模块顶层导入 tool_validation，模块缺失时导入阶段就失败，降级路径不可达。
    """
    import importlib
    import sys

    monkeypatch.setitem(sys.modules, "server.remotion_templates.tool_validation", None)
    monkeypatch.delitem(sys.modules, "server.remotion_templates.tools.session")
    module = importlib.import_module("server.remotion_templates.tools.session")
    settings = Settings(_env_file=None, data_dir=tmp_path)
    current = module.ToolSession(
        SimpleNamespace(settings=settings, renderer=None), None, None, Budget(), tmp_path / "run", [], lambda *_: None, {},
    )
    assert current.validator is None
    component = ComponentDefinition(code="export default function C(){return null}", parameter_schema={"type": "object", "properties": {}, "additionalProperties": False}, default_parameters={})
    with pytest.raises(module.ToolFault) as failure:
        asyncio.run(current.validate_code(component))
    assert failure.value.error.code == "VALIDATION_UNAVAILABLE"
