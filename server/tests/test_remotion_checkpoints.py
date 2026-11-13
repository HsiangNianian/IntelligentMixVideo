"""Failure recovery preserves completed edits and resumes the Executor, with fresh save/review guards.

Run: uv run --locked pytest tests/test_remotion_checkpoints.py. SQLite, scripted model turns and render doubles
isolate all user data, credentials and network access; the Agent cases use the locked local bundler.
"""

import asyncio
from copy import deepcopy
import json
import shutil
from types import SimpleNamespace
from uuid import uuid4

import pytest

from server.remotion_templates.agent import AgentRun, Layer
from server.remotion_templates.checkpoints import capture, from_audit
from server.remotion_templates.context import AssistantMessage
from server.remotion_templates.fonts import FontStore
from server.remotion_templates.models import GenerateTemplateRequest, TaskMessage
from server.remotion_templates.planning import PlanAction
from server.remotion_templates.provider import Budget, ModelFailure
from server.remotion_templates.runtime import Runtime
from server.remotion_templates.settings import Settings
from server.remotion_templates.store import NotFound, Store
from server.remotion_templates.tools.session import ToolSession
from server.remotion_templates.tools.workspace import CODE, MANIFEST, starter_files

from .conftest import DEVICE
from .test_remotion_free_effects import FakeHarness, build, call, needs_bundler, report, stub_checks


class ScriptedHarness(FakeHarness):
    """Inject transport failures or cancellation at a selected model request."""

    async def _turn(self, *args, **kwargs):
        """Record the actual request and either return its scripted response or interrupt it."""
        result = await super()._turn(*args, **kwargs)
        if isinstance(result, BaseException):
            raise result
        return result


@pytest.fixture
def checks(monkeypatch):
    """Use real workspace/save handlers with local deterministic render evidence."""
    monkeypatch.setattr(FontStore, "active", SimpleNamespace(families=lambda: ["Noto Sans CJK SC"]))
    original = ToolSession.__init__

    def initialize(self, *args, **kwargs):
        """Install render doubles only after the real workspace is initialized."""
        original(self, *args, **kwargs)
        stub_checks(self)

    monkeypatch.setattr(ToolSession, "__init__", initialize)


@pytest.fixture
def store(tmp_path, template_db):
    """Use a durable SQLite job row and disposable local render directory."""
    result = Store(tmp_path / "store")
    result.initialize()
    return result


@needs_bundler
@pytest.mark.parametrize("failure", [TimeoutError(), ModelFailure("upstream unavailable"), asyncio.CancelledError()])
def test_retry_continues_checked_files_without_rebuilding(checks, store, tmp_path, failure):
    """校验通过后超时/模型失败/取消：清掉临时目录并重新建 Runtime 后，从红字代码继续保存，不再调用 Outer 或重写文件。"""
    work, job = store.create(GenerateTemplateRequest(description="红色标题"), DEVICE)
    store.claim()
    harness = ScriptedHarness([
        call("delegate", "tools_plan_execute", {"action": "delegate"}),
        call("red", "workspace_edit", {"path": MANIFEST, "edits": [{"old_str": "#FFFFFF", "new_str": "#FF0000"}]}),
        call("checked", "validate_check", {}), failure,
    ], tmp_path)
    first = AgentRun(harness, None, Budget(), store.job_dir(job.id), [], lambda *_: None,
                     intent={"original_request": work.request.model_dump(mode="json")},
                     on_checkpoint=lambda value: store.save_checkpoint(job.id, value))
    with pytest.raises(type(failure)):
        asyncio.run(first.plan_execute())
    assert first.session.checked_hash == first.session.workspace_hash()
    original = store.inputs(job.id).checkpoint
    assert "#FF0000" in original["files"][MANIFEST]
    shutil.rmtree(store.job_dir(job.id), ignore_errors=True)
    store.update(job.id, status="failed", stage="finished")

    restarted_store = Store(store.root)
    runtime = Runtime(restarted_store, None, Settings(_env_file=None))
    runtime.notify = lambda *args: None
    retried = runtime.retry(job.id)
    # Delivery acknowledgement can delete the original job without deleting the copied checkpoint.
    cursor = max(event.id for event in restarted_store.work_events(work.id, 0))
    restarted_store.acknowledge(work.id, cursor, DEVICE)
    with pytest.raises(NotFound):
        restarted_store.job(job.id)
    checkpoint = restarted_store.inputs(retried.id).checkpoint
    resumed_harness = ScriptedHarness([call("save-current", "effect_save", {}), report("finished")], tmp_path)
    resumed = AgentRun(resumed_harness, None, Budget(), store.job_dir(retried.id), [], lambda *_: None,
                       intent={"original_request": work.request.model_dump(mode="json")}, checkpoint=checkpoint)
    assert resumed.session.files == first.session.files
    assert resumed.session.checked_hash is None and resumed.session.saved_hash is None
    result = asyncio.run(resumed.plan_execute())
    assert resumed_harness.roles == ["executor", "executor"]
    assert resumed_harness.published == [result["published"]]
    assert resumed.session.saved_sprite(result["published"]).default_parameters["color"] == "#FF0000"
    assert resumed.session.calls and resumed.session.sheet_seen == result["published"]
    first_window = json.dumps(resumed_harness.windows[0], ensure_ascii=False)
    assert "task_resumed" in first_window and '"checked"' in first_window
    assert str(store.job_dir(job.id)) not in first_window


@needs_bundler
def test_recovered_edit_renders_accepted_parameters_as_its_baseline(checks, tmp_path):
    """恢复后 Plan 回看已验收版本及用户保存参数，不能把未完成红字草稿当作基线，也不能重置草稿。"""
    draft, _ = build(starter_files("text", set(), "Noto Sans CJK SC"))
    spec = SimpleNamespace(sprite=draft, sprite_kind="text")
    base = SimpleNamespace(default_config={**draft.default_parameters, "color": "#00FF00"})
    intent = {"original_request": {"description": "白色字"}, "instruction": "改成红色", "accepted_base": {"name": "v1"}}
    first = AgentRun(FakeHarness([], tmp_path), spec, Budget(), tmp_path / "first", [], lambda *_: None, base=base, intent=intent)
    first._outer_control(PlanAction(action="delegate"))
    first.session.files[MANIFEST] = first.session.files[MANIFEST].replace("#00FF00", "#FF0000")
    first.session.files[CODE] = first.session.files[CODE].replace("props.color", '"#FF0000"')
    checkpoint = capture(first)
    resumed = AgentRun(FakeHarness([], tmp_path), spec, Budget(), tmp_path / "resumed", [], lambda *_: None,
                       base=base, intent=intent, checkpoint=checkpoint)
    mounted = []
    original_render = resumed.session.validator.validate_render

    async def inspect_render(request, **options):
        """Record the real source/defaults passed to the renderer and retain the fixture's image output."""
        mounted.append(request.component)
        return await original_render(request, **options)

    resumed.session.validator.validate_render = inspect_render
    asyncio.run(resumed._switch(Layer.PLAN))
    assert mounted[0].default_parameters["color"] == "#00FF00"
    assert "props.color" in mounted[0].code
    assert resumed.session.base_frames and "accepted version" in resumed.session.base_frames[0][1]
    assert resumed.session.files == checkpoint["files"]
    assert base.default_config["color"] == "#00FF00" and draft.default_parameters["color"] == "#FFFFFF"
    # Reusing the baseline evidence neither renders again nor consumes the draft's preview allowance.
    asyncio.run(resumed.session.base_preview())
    assert len(mounted) == 1 and resumed.session.renders_left == 12


def test_runtime_retains_progress_on_timeout_and_continues_a_followup(store, tmp_path, monkeypatch):
    """实际 Runtime 超时后的 finally 清理草稿目录，但数据库恢复点保留；聊天里的继续指令也继承工作区。"""
    checkpoint = {"version": 1, "files": starter_files("text", set(), "Noto Sans CJK SC")}
    checkpoint["files"][CODE] += "\n// already finished animation\n"
    received = []

    class Harness:
        """Exercise Runtime callbacks without model or renderer processes."""

        renderer = None

        async def generate(self, _spec, _budget, directory, _images, _stage, **options):
            """Checkpoint a completed edit, then fail while the next model request is outstanding."""
            received.append(options)
            directory.joinpath("temporary-render").write_text("scratch")
            options["on_checkpoint"](checkpoint)
            raise TimeoutError()

    runtime = Runtime(store, Harness(), Settings(_env_file=None))
    monkeypatch.setattr(runtime, "notify", lambda *args: None)
    work, job = store.create(GenerateTemplateRequest(description="分裂字效"), DEVICE)
    store.claim()
    asyncio.run(runtime._execute(job.id))
    assert store.job(job.id).error.code == "timeout"
    assert not store.job_dir(job.id).exists()
    assert store.inputs(job.id).checkpoint == checkpoint
    follow = runtime.message(work.id, TaskMessage(instruction="从刚才的代码继续，裂缝再大一点"))
    assert store.inputs(follow.id).checkpoint == checkpoint
    assert store.inputs(follow.id).instruction == "从刚才的代码继续，裂缝再大一点"
    asyncio.run(runtime._execute(follow.id))
    assert received[-1]["checkpoint"] == checkpoint
    # A private recovery snapshot is not emitted as public task state or conversation input.
    # Private recovery is delivered to the owning client, independently of visible chat and job responses.
    delivered = store.client_context(work.id)
    assert delivered.input.checkpoint == checkpoint
    assert "already finished animation" not in json.dumps([event.data for event in store.work_events(work.id, 0) if event.type == "message.created"])
    assert "checkpoint" not in received[-1]["intent"]


def test_restart_and_repeated_retries_keep_latest_snapshot(store, monkeypatch):
    """服务重启、客户端确认清理、连续失败均不会把输入里的最新恢复点变回起始工程。"""
    work, job = store.create(GenerateTemplateRequest(description="标题"), DEVICE)
    store.claim()
    files = starter_files("text", set(), "Noto Sans CJK SC")
    store.save_checkpoint(job.id, {"version": 1, "files": files})
    store.interrupt_unfinished()
    assert store.job(job.id).status == "interrupted"
    runtime = Runtime(store, None, None)
    monkeypatch.setattr(runtime, "notify", lambda *args: None)
    second = runtime.retry(job.id)
    updated = deepcopy(store.inputs(second.id).checkpoint)
    updated["files"][CODE] += "\n// next completed edit\n"
    store.save_checkpoint(second.id, updated)
    store.update(second.id, status="failed")
    third = runtime.retry(second.id)
    assert store.inputs(third.id).checkpoint == updated
    assert store.inputs(job.id).checkpoint["files"][CODE] == files[CODE]


@needs_bundler
def test_saved_candidates_need_new_evidence_after_resume(checks, tmp_path):
    """失败前保存过的候选保留源码引用，但重试不能只上报完成；必须重新保存并接收新回看帧。"""
    first_harness = ScriptedHarness([
        call("delegate", "tools_plan_execute", {"action": "delegate"}),
        call("saved", "effect_save", {}), TimeoutError(),
    ], tmp_path)
    first = AgentRun(first_harness, None, Budget(), tmp_path / "first", [], lambda *_: None)
    with pytest.raises(TimeoutError):
        asyncio.run(first.plan_execute())
    checkpoint = capture(first)
    shutil.rmtree(tmp_path / "first", ignore_errors=True)
    second_harness = ScriptedHarness([
        report("premature"), call("new-save", "effect_save", {}), report("finish"),
    ], tmp_path)
    second = AgentRun(second_harness, None, Budget(), tmp_path / "second", [], lambda *_: None, checkpoint=checkpoint)
    result = asyncio.run(second.plan_execute())
    assert second_harness.published == [result["published"]]
    assert result["published"] != first.session.latest_sprite_id
    assert "files changed after the latest effect_save" in json.dumps(second_harness.windows[1])
    assert not any("image_path" in json.dumps(group) for group in checkpoint["history"])


def test_completed_plan_steps_survive_without_consumed_budgets(checks, tmp_path):
    """已完成的工作区步骤及其输出继续给后续步骤使用，新的显式重试获得新预算。"""
    run = AgentRun(ScriptedHarness([], tmp_path), None, Budget(), tmp_path, [], lambda *_: None)
    run.state.control(PlanAction.model_validate({"action": "update_plan", "plan": {"goal": "分步制作", "steps": [
        {"id": "shape", "goal": "绘制形状", "tool_modules": ["workspace"], "done_when": "shape checked"},
        {"id": "finish", "goal": "保存现有形状", "tool_modules": ["effect"], "done_when": "saved",
         "input_refs": ["steps.shape.outputs.effect"]},
    ]}}))
    run.state.finish_batch("step_done", summary="形状完成，保留当前源码")
    run.state.control(PlanAction(action="advance"))
    run.state.calls = run.state.total_limit
    checkpoint = capture(run)
    resumed = AgentRun(ScriptedHarness([], tmp_path), None, Budget(), tmp_path, [], lambda *_: None, checkpoint=checkpoint)
    assert resumed.state.step.id == "finish" and resumed.state.index == 1
    assert resumed.state.snapshot()["step_inputs"] == {"steps.shape.outputs.effect": "形状完成，保留当前源码"}
    assert resumed.state.calls == 0 and resumed.layer is Layer.EXECUTOR


def audit_line(name, arguments, result, identifier):
    """Construct a historical host receipt, including exactly what the model asked and the host accepted."""
    return json.dumps({"event": "tool_result", "role": "executor", "tool": name, "arguments": json.dumps(arguments),
                       "call_id": identifier, "result": result}) + "\n"


def test_pre_upgrade_failure_recovers_only_successful_edits(store, monkeypatch):
    """旧版本已删除工作区：从成功 write/edit 回执还原最后源码和校验结果，失败调用与截断日志不能污染恢复点。"""
    work, job = store.create(GenerateTemplateRequest(description="标题"), DEVICE)
    initial = starter_files("text", set(), "Noto Sans CJK SC")
    source = initial[CODE] + "\n// keep this animation\n"
    path = store.audit_path(work.id, job.id)
    path.parent.mkdir(parents=True)
    path.write_text(
        audit_line("workspace_write", {"path": CODE, "content": source}, {"ok": True, "data": {"path": CODE}}, "write")
        + audit_line("workspace_edit", {"path": CODE, "edits": [{"old_str": "keep this", "new_str": "keep finished"}]},
                     {"ok": True, "data": {"path": CODE}}, "edit")
        + audit_line("workspace_write", {"path": CODE, "content": "bad"}, {"ok": False, "error": {"code": "INVALID_ARGUMENT"}}, "bad")
        + audit_line("validate_check", {}, {"ok": True, "data": {"passed": True, "problems": []}}, "checked")
        + '{"event": "model_request",', encoding="utf-8",
    )
    store.update(job.id, status="failed")
    runtime = Runtime(store, None, None)
    monkeypatch.setattr(runtime, "notify", lambda *args: None)
    retried = runtime.retry(job.id)
    checkpoint = store.inputs(retried.id).checkpoint
    assert checkpoint["files"][CODE] == source.replace("keep this", "keep finished")
    assert json.loads(checkpoint["history"][-1][-1]["content"])["data"]["passed"] is True
    assert not store.job_dir(job.id).exists()


def test_unrecoverable_audit_does_not_silently_restart(tmp_path):
    """成功回执却缺少可还原的前序源码时，拒绝丢弃进度后从头生成。"""
    path = tmp_path / "audit.jsonl"
    path.write_text(audit_line("workspace_edit", {"path": CODE, "edits": [{"old_str": "missing original", "new_str": "new"}]},
                               {"ok": True, "data": {}}, "edit"))
    with pytest.raises(ValueError, match="does not occur"):
        from_audit(path, starter_files("text", set(), "Noto Sans CJK SC"))


def test_interrupted_greeting_does_not_become_an_effect_task(checks, tmp_path):
    """Outer 尚未委派时没有制作进度，重试保留正常对话路由，不能强迫问候消息进入 Executor。"""
    saved = []
    run = AgentRun(ScriptedHarness([TimeoutError()], tmp_path), None, Budget(), tmp_path, [], lambda *_: None,
                   intent={"original_request": {"description": "你好"}}, on_checkpoint=saved.append)
    with pytest.raises(TimeoutError):
        asyncio.run(run.plan_execute())
    assert saved == [None]


@needs_bundler
def test_cancellation_inside_a_batch_keeps_earlier_completed_edits(checks, tmp_path):
    """同一回复先修改文件、随后校验被取消：已完成修改单独持久化，重试直接保存该文件，不重放修改。"""
    edit = call("edit", "workspace_edit", {"path": MANIFEST, "edits": [{"old_str": "#FFFFFF", "new_str": "#FF0000"}]})
    validate = call("validate", "validate_check", {})
    harness = ScriptedHarness([
        call("delegate", "tools_plan_execute", {"action": "delegate"}),
        AssistantMessage(tool_calls=[*edit.tool_calls, *validate.tool_calls]),
    ], tmp_path)
    saved = []
    run = AgentRun(harness, None, Budget(), tmp_path / "first", [], lambda *_: None, on_checkpoint=saved.append)

    async def interrupted(_component):
        """Interrupt validation after the preceding workspace operation finished."""
        raise asyncio.CancelledError()

    run.session.validate_code = interrupted
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run.plan_execute())
    assert any(snapshot and "#FF0000" in snapshot["files"][MANIFEST]
               and len(snapshot["history"][-1]) == 2 for snapshot in saved if snapshot and snapshot["history"])
    checkpoint = saved[-1]
    assert json.loads(checkpoint["history"][-1][-1]["content"])["error"]["code"] == "CANCELLED"
    next_harness = ScriptedHarness([call("save", "effect_save", {}), report("done")], tmp_path)
    resumed = AgentRun(next_harness, None, Budget(), tmp_path / "second", [], lambda *_: None, checkpoint=checkpoint)
    result = asyncio.run(resumed.plan_execute())
    assert resumed.session.saved_sprite(result["published"]).default_parameters["color"] == "#FF0000"


@pytest.mark.parametrize("message", [
    TaskMessage(parameters={"fontSize": 80}),
    TaskMessage(instruction="改这个历史版本", base_version_id=uuid4()),
])
def test_explicit_base_changes_and_manual_parameters_do_not_inherit_drafts(store, monkeypatch, message):
    """用户切换历史基线或手动调参时，不能把另一个失败草稿混入新编辑。"""
    work, job = store.create(GenerateTemplateRequest(description="标题"), DEVICE)
    store.save_checkpoint(job.id, {"version": 1, "files": starter_files("text", set(), "Noto Sans CJK SC")})
    store.update(job.id, status="failed")
    runtime = Runtime(store, None, None)
    edited = []
    monkeypatch.setattr(runtime, "edit", lambda *args: edited.append(args) or "normal edit")
    assert runtime.message(work.id, message) == "normal edit"
    assert len(edited) == 1 and store.latest_job(work.id).id == job.id
