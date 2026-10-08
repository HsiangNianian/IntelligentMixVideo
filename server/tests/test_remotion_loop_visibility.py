"""公开的 ReAct 循环位置：层级与计数透出、白名单校验、终态与去重，以及不泄漏模型文本。

不调用真实模型、浏览器或 Chroma；Agent 循环用脚本化替身驱动。
在 server/ 目录执行 `uv run --locked pytest tests/test_remotion_loop_visibility.py -v`。
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from server.remotion_templates.agent import AgentRun, Layer
from server.remotion_templates.context import AssistantMessage
from server.remotion_templates.models import GenerateTemplateRequest, LoopPosition
from server.remotion_templates.planning import Plan
from server.remotion_templates.provider import Budget
from server.remotion_templates.store import Store

POSITION = {"layer": "plan", "turn": 2, "step_index": 1, "step_total": 3}


@pytest.fixture
def loop_store(tmp_path):
    """使用独立本地数据库，不读取用户模板或模型配置。"""
    store = Store(tmp_path / "loop")
    store.initialize()
    return store


def running_job(store):
    """创建一个正在运行的任务，只有运行中的任务接受循环位置更新。"""
    work, job = store.create(GenerateTemplateRequest(description="标题"))
    store.claim()
    return work, job


def test_position_is_published_scoped_and_durable(loop_store):
    """循环位置写入公开快照，只含声明的字段，并在重新打开数据库后保持一致。"""
    store = loop_store
    work, job = running_job(store)
    store.loop(job.id, POSITION)
    snapshot = store.session(work.id)
    assert snapshot.job.loop is not None
    assert snapshot.job.loop.model_dump(mode="json") == POSITION
    # 同一位置也进入历史任务链路，刷新和重连看到一致结果。
    assert snapshot.jobs[0].loop == snapshot.job.loop
    reopened = Store(store.root)
    reopened.initialize()
    assert reopened.session(work.id).job.loop == snapshot.job.loop


def test_public_payload_never_carries_model_text(loop_store):
    """公开事件载荷只有层级与计数，不包含步骤目标、工具参数或模型原文。"""
    store = loop_store
    work, job = running_job(store)
    cursor = store.session(work.id).cursor
    store.loop(job.id, POSITION)
    events = store.work_events(work.id, cursor)
    assert [event.type for event in events] == ["job.updated"]
    payload = json.dumps(events[-1].data, ensure_ascii=False)
    assert set(events[-1].data["loop"]) == {"layer", "turn", "step_index", "step_total"}
    # 计划步骤目标是模型生成的文字，绝不能出现在公开流里。
    for leaked in ("goal", "done_when", "tool_modules", "input_refs", "tsx_code", "usage"):
        assert leaked not in payload


def test_unknown_layer_is_rejected_and_not_stored(loop_store):
    """白名单之外的角色被拒绝，且不会写入任何位置。"""
    store = loop_store
    work, job = running_job(store)
    with pytest.raises(ValidationError):
        store.loop(job.id, {"layer": "judge", "turn": 1})
    assert store.session(work.id).job.loop is None


def test_identical_position_does_not_publish_twice(loop_store):
    """重复上报同一位置不产生新事件，避免刷新时钟刷屏。"""
    store = loop_store
    work, job = running_job(store)
    store.loop(job.id, POSITION)
    first = store.session(work.id)
    store.loop(job.id, dict(POSITION))
    assert store.session(work.id).cursor == first.cursor
    store.loop(job.id, POSITION | {"turn": 3})
    assert store.session(work.id).job.loop.turn == 3


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled", "interrupted"])
def test_terminal_job_keeps_its_last_position(loop_store, status):
    """终态任务保留最后观察到的位置，迟到上报不能改写已结束的历史。"""
    store = loop_store
    work, job = running_job(store)
    store.loop(job.id, POSITION)
    store.update(job.id, status=status)
    after = store.session(work.id)
    store.loop(job.id, {"layer": "executor", "turn": 9})
    assert store.session(work.id) == after
    assert store.session(work.id).job.loop.model_dump(mode="json") == POSITION


def test_step_counters_are_optional_without_a_plan(loop_store):
    """没有计划时只上报层级与轮次，不编造步骤数。"""
    store = loop_store
    work, job = running_job(store)
    store.loop(job.id, {"layer": "outer", "turn": 1})
    position = store.session(work.id).job.loop
    assert position is not None
    assert position.step_index is None and position.step_total is None


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


def test_report_loop_uses_host_counters_without_goals(monkeypatch, tmp_path):
    """_report_loop 只发送宿主自己的层级与计数，不发送计划步骤目标。"""
    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FakeSession)
    run = AgentRun(FakeHarness([]), None, Budget(on_loop=lambda _: None), tmp_path, [], lambda *_: None)
    seen: list[dict] = []
    run.budget = Budget(on_loop=seen.append)
    run.turn, run.layer = 2, Layer.PLAN
    run.state.plan = Plan(
        goal="组合标题",
        steps=[
            {"id": "a", "goal": "第一步", "tool_modules": ["tools"], "done_when": "完成"},
            {"id": "b", "goal": "第二步", "tool_modules": ["tools"], "done_when": "完成"},
        ],
    )
    run.state.index = 1
    run._report_loop()
    assert seen == [{"layer": "plan", "turn": 2, "step_index": 2, "step_total": 2}]
    # 目标文字是模型输出，不能随位置一起上报。
    assert "第一步" not in json.dumps(seen, ensure_ascii=False)
    run.state.plan = None
    run._report_loop()
    assert seen[-1] == {"layer": "plan", "turn": 2, "step_index": None, "step_total": None}


def test_real_loop_reports_every_layer_transition(monkeypatch, tmp_path):
    """真实三层循环逐层上报位置，且不泄漏任何计划目标文字。"""
    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FakeSession)
    plan = Plan(
        goal="inspect",
        steps=[
            {"id": "inspect", "goal": "观察一个工具", "tool_modules": ["tools"], "done_when": "已观察"},
        ],
    )
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
    run = AgentRun(harness, None, Budget(on_loop=seen.append), tmp_path, [], lambda *_: None, intent={"original_request": {"description": "demo"}})
    assert asyncio.run(run.plan_execute()) == {"published": "sprite-1"}
    layers = [item["layer"] for item in seen]
    assert layers == ["outer", "plan", "executor", "executor", "plan", "outer"]
    assert [item["turn"] for item in seen] == [1, 2, 3, 4, 5, 6]
    # 计划由 Plan 的 update_plan 建立，之前两轮没有步骤计数可报。
    assert seen[0]["step_index"] is None and seen[0]["step_total"] is None
    assert seen[1]["step_index"] is None and seen[1]["step_total"] is None
    # 计划存在后每轮都带上已进入的步骤位置，且不预告未进入的步骤。
    assert all(
        item["step_index"] == 1 and item["step_total"] == 1 for item in seen[2:]
    )
    assert "观察一个工具" not in json.dumps(seen, ensure_ascii=False)


def test_loop_positions_survive_a_real_job_round_trip(loop_store):
    """位置经由 Store 往返后仍是同一份白名单结构。"""
    store = loop_store
    work, job = running_job(store)
    for position in (
        {"layer": "outer", "turn": 1},
        {"layer": "plan", "turn": 2, "step_index": 1, "step_total": 2},
        {"layer": "executor", "turn": 3, "step_index": 1, "step_total": 2},
    ):
        store.loop(job.id, position)
        assert store.session(work.id).job.loop == LoopPosition.model_validate(position)
