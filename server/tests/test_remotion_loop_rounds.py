"""公开的 ReAct 每轮记录：工具名与结论、白名单校验、终态与去重，以及不泄漏参数与载荷。

不调用真实模型、浏览器或 Chroma；Agent 循环用脚本化替身驱动。
在 server/ 目录执行 `uv run --locked pytest tests/test_remotion_loop_rounds.py -v`。
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from server.remotion_templates.agent import AgentRun, Layer
from server.remotion_templates.context import AssistantMessage
from server.remotion_templates.models import GenerateTemplateRequest
from server.remotion_templates.planning import Plan
from server.remotion_templates.progress import LoopRound
from server.remotion_templates.provider import Budget
from server.remotion_templates.store import Store
from server.remotion_templates.tools.registry import ToolFault

ROUND = {
    "layer": "executor",
    "turn": 3,
    "calls": [{"tool": "sprite.compose", "status": "pass"}],
}


@pytest.fixture
def round_store(tmp_path):
    """使用独立本地数据库，不读取用户模板或模型配置。"""
    store = Store(tmp_path / "rounds")
    store.initialize()
    return store


def running_job(store):
    """创建一个正在运行的任务，只有运行中的任务接受每轮记录。"""
    work, job = store.create(GenerateTemplateRequest(description="标题"))
    store.claim()
    return work, job


def test_round_is_published_scoped_and_durable(round_store):
    """每轮记录写入公开快照，只含声明的字段，并在重新打开数据库后保持一致。"""
    store = round_store
    work, job = running_job(store)
    store.round(job.id, ROUND)
    snapshot = store.session(work.id)
    assert snapshot.job.rounds == [LoopRound.model_validate(ROUND)]
    # 同一份记录也进入历史任务链路，刷新和重连看到一致结果。
    assert snapshot.jobs[0].rounds == snapshot.job.rounds
    reopened = Store(store.root)
    reopened.initialize()
    assert reopened.session(work.id).job.rounds == snapshot.job.rounds


def test_public_payload_never_carries_arguments_or_payloads(round_store):
    """公开事件只带工具名与结论，不含工具参数、候选源码或任何模型原文。"""
    store = round_store
    work, job = running_job(store)
    cursor = store.session(work.id).cursor
    store.round(
        job.id,
        ROUND
        | {
            "calls": [
                {
                    "tool": "sprite.create",
                    "status": "fail",
                    "error_code": "COMPOSITION_FAILED",
                    "message": "Sprite code, schema or defaults do not match its instance definition.",
                }
            ]
        },
    )
    events = store.work_events(work.id, cursor)
    assert [event.type for event in events] == ["job.updated"]
    rounds = events[-1].data["rounds"]
    assert set(rounds[0]) == {"layer", "turn", "calls"}
    assert set(rounds[0]["calls"][0]) == {"tool", "status", "error_code", "message"}
    payload = json.dumps(events[-1].data, ensure_ascii=False)
    for leaked in ("arguments", "details", "tsx_code", "goal", "done_when", "input_refs"):
        assert leaked not in payload


def test_unknown_layer_is_rejected_and_not_stored(round_store):
    """白名单之外的角色被拒绝，且不会写入任何记录。"""
    store = round_store
    work, job = running_job(store)
    with pytest.raises(ValidationError):
        store.round(job.id, {"layer": "judge", "turn": 1})
    assert store.session(work.id).job.rounds == []


def test_repeated_layer_turn_is_recorded_once(round_store):
    """同一层同一轮重复上报只留一条，重试的处理流程不会写出重复轮次。"""
    store = round_store
    work, job = running_job(store)
    store.round(job.id, ROUND)
    first = store.session(work.id)
    store.round(job.id, ROUND | {"calls": []})
    assert store.session(work.id).cursor == first.cursor
    store.round(job.id, ROUND | {"turn": 4})
    assert [item.turn for item in store.session(work.id).job.rounds] == [3, 4]


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled", "interrupted"])
def test_terminal_job_accepts_no_more_rounds(round_store, status):
    """终态任务不再接受记录，迟到上报不能改写已结束的历史。"""
    store = round_store
    work, job = running_job(store)
    store.round(job.id, ROUND)
    store.update(job.id, status=status)
    after = store.session(work.id)
    store.round(job.id, {"layer": "outer", "turn": 9})
    assert store.session(work.id) == after
    assert [item.turn for item in store.session(work.id).job.rounds] == [3]


class FakeSession:
    """只暴露 AgentRun 用到的任务级接口，不访问模型或渲染器。"""

    def __init__(self, _harness, _spec, _base, _budget, directory, images, _stage, intent):
        self.directory = Path(directory)
        self.images = images
        self.intent = intent or {"original_request": {"description": "demo"}}
        self.latest_sprite_id = "sprite-1"
        self.saved_sprites = {"sprite-1": {"sprite_id": "sprite-1"}}

    def snapshot(self):
        """返回确定性的宿主快照。"""
        return {"user_intent": self.intent, "sprites": sorted(self.saved_sprites)}

    def saved_sprite(self, identifier):
        """只解析本任务已保存的 Sprite。"""
        return self.saved_sprites[identifier]

    async def execute(self, name, args, _catalog):
        """记录一次确定性的业务工具观察。"""
        return {"tool": name, "args": args.model_dump(mode="json")}


class FailingSession(FakeSession):
    """让业务工具按契约失败，用于验证结论与错误码进入记录。"""

    async def execute(self, name, args, _catalog):
        """以真实工具的错误码和说明失败，不返回任何结果。"""
        raise ToolFault(
            "CODE_VALIDATION_FAILED",
            "Preset code validation failed.",
            details={"validation": {"tsx_code": "export default function C(){return null}"}},
        )


class FakeHarness:
    """脚本化模型轮次，并记录每一层被请求的顺序。"""

    def __init__(self, responses):
        self.responses = iter(responses)
        self.roles = []
        self.settings = SimpleNamespace(
            max_steps=4,
            max_tooluse=3,
            enforce_no_progress=False,
            max_no_progress_turns=4,
        )

    async def _turn(self, _system, _context, _tools, _budget, _images, *, phase):
        """按角色返回下一条脚本响应。"""
        self.roles.append(phase)
        return next(self.responses)

    async def finalize_sprite(self, _session, identifier, _budget, _directory):
        """返回标记，证明只有 Outer 触发发布。"""
        return {"published": identifier}


def call(identifier, name, arguments):
    """构造 provider 兼容的工具调用消息。"""
    return AssistantMessage(
        tool_calls=[
            {
                "id": identifier,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ]
    )


def plan_for_inspect():
    """构造只观察一个工具的单步计划。"""
    return Plan(
        goal="inspect",
        steps=[
            {"id": "inspect", "goal": "观察一个工具", "tool_modules": ["tools"], "done_when": "已观察"},
        ],
    )


def plan_for_saving():
    """构造保存一个预设的单步计划；Executor 只能看到该步骤声明的模块。"""
    return Plan(
        goal="save",
        steps=[
            {"id": "save", "goal": "保存预设", "tool_modules": ["preset"], "done_when": "已保存"},
        ],
    )


def test_real_loop_records_every_turn_with_its_tools(monkeypatch, tmp_path):
    """真实三层循环每轮都留一条记录，带该轮实际调用的工具与结论。"""
    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FakeSession)
    plan = plan_for_inspect()
    harness = FakeHarness(
        [
            call("outer-1", "tools_plan_execute", {"action": "delegate", "plan": plan.model_dump()}),
            call("plan-1", "tools_plan_execute", {"action": "update_plan", "plan": plan.model_dump()}),
            call("exec-1", "tools_inspect", {"tool_name": "sprite.create"}),
            AssistantMessage(content=json.dumps({"status": "step_done", "summary": "已观察", "sprite_id": "sprite-1"})),
            call("plan-2", "tools_plan_execute", {"action": "complete"}),
            AssistantMessage(content=json.dumps({"action": "complete", "sprite_id": "sprite-1"})),
        ]
    )
    seen: list[dict] = []
    run = AgentRun(
        harness, None, Budget(on_round=seen.append), tmp_path, [], lambda *_: None,
        intent={"original_request": {"description": "demo"}},
    )
    assert asyncio.run(run.plan_execute()) == {"published": "sprite-1"}
    assert [(item["layer"], item["turn"]) for item in seen] == [
        ("outer", 1), ("plan", 2), ("executor", 3), ("executor", 4), ("plan", 5), ("outer", 6),
    ]
    # 控制工具与业务工具都按契约里的点号 ID 记录，而不是模型侧的调用名。
    assert seen[0]["calls"] == [{"tool": "tools.plan_execute", "status": "pass"}]
    assert seen[2]["calls"] == [{"tool": "tools.inspect", "status": "pass"}]
    # 只回消息、没有调用工具的轮次记录为空列表，不编造不存在的调用。
    assert seen[3]["calls"] == []
    assert seen[5]["calls"] == []
    # 计划目标与工具参数都是模型输入，不能随公开记录一起上报。
    leaked = json.dumps(seen, ensure_ascii=False)
    assert "观察一个工具" not in leaked
    assert "sprite.create" not in leaked


def test_failed_call_is_recorded_with_its_error_code(monkeypatch, tmp_path):
    """失败的工具调用按轮记录错误码与说明，而不是只标一个失败。"""
    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FailingSession)
    plan = plan_for_saving()
    harness = FakeHarness(
        [
            call("outer-1", "tools_plan_execute", {"action": "delegate", "plan": plan.model_dump()}),
            call("plan-1", "tools_plan_execute", {"action": "update_plan", "plan": plan.model_dump()}),
            call(
                "exec-1",
                "preset_create",
                {
                    "description": "标题",
                    "code": "export default function Label(){return null}",
                    "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": False},
                    "default_parameters": {},
                },
            ),
            AssistantMessage(content=json.dumps({"status": "step_done", "summary": "保存失败", "sprite_id": "sprite-1"})),
            call("plan-2", "tools_plan_execute", {"action": "complete"}),
            AssistantMessage(content=json.dumps({"action": "complete", "sprite_id": "sprite-1"})),
        ]
    )
    seen: list[dict] = []
    run = AgentRun(
        harness, None, Budget(on_round=seen.append), tmp_path, [], lambda *_: None,
        intent={"original_request": {"description": "demo"}},
    )
    asyncio.run(run.plan_execute())
    failed = seen[2]
    assert failed["layer"] == "executor" and failed["turn"] == 3
    assert failed["calls"] == [
        {
            "tool": "preset.create",
            "status": "fail",
            "error_code": "CODE_VALIDATION_FAILED",
            "message": "Preset code validation failed.",
        }
    ]
    # 失败详情里的候选源码留在私有诊断里，不进入公开记录。
    assert "export default function C()" not in json.dumps(seen, ensure_ascii=False)


def test_round_published_through_the_store_round_trips(round_store):
    """每轮记录经由 Store 往返后仍是同一份白名单结构。"""
    store = round_store
    work, job = running_job(store)
    for record in (
        {"layer": "outer", "turn": 1, "calls": []},
        {"layer": "plan", "turn": 2, "calls": [{"tool": "tools.plan_execute", "status": "pass"}]},
        {"layer": "executor", "turn": 3, "calls": [{"tool": "sprite.compose", "status": "fail", "error_code": "COMPOSITION_FAILED"}]},
    ):
        store.round(job.id, record)
    assert store.session(work.id).job.rounds == [
        LoopRound.model_validate(record) for record in (
            {"layer": "outer", "turn": 1, "calls": []},
            {"layer": "plan", "turn": 2, "calls": [{"tool": "tools.plan_execute", "status": "pass"}]},
            {"layer": "executor", "turn": 3, "calls": [{"tool": "sprite.compose", "status": "fail", "error_code": "COMPOSITION_FAILED"}]},
        )
    ]
