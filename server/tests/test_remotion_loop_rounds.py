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
from server.remotion_templates.provider import Budget, ModelContractFailure
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
    assert [event.type for event in events] == ["job.round"]
    rounds = [events[-1].data["round"]]
    assert set(rounds[0]) == {"layer", "turn", "calls", "error_code"}
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
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

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
            "message": "代码校验未通过。",
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


def test_invalid_model_turn_is_recorded_before_recovery(monkeypatch, tmp_path, round_store):
    """无效协议轮次保留失败标记，下一轮继续且不会沿用上一轮调用。"""
    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FakeSession)
    work, job = running_job(round_store)
    harness = FakeHarness([
        call("inspect", "tools_inspect", {"tool_name": "sprite.create"}),
        ModelContractFailure("private model text"),
        AssistantMessage(content=json.dumps({"action": "complete", "sprite_id": "sprite-1"})),
    ])
    run = AgentRun(harness, None, Budget(on_round=lambda record: round_store.round(job.id, record)), tmp_path, [], lambda *_: None)
    asyncio.run(run.plan_execute())
    rounds = round_store.session(work.id).job.rounds
    assert [item.turn for item in rounds] == [1, 2, 3]
    assert rounds[1].error_code == "MODEL_CONTRACT_FAILED"
    assert rounds[1].calls == []
    assert "private model text" not in round_store.session(work.id).model_dump_json()


def test_unresolved_names_do_not_break_rounds(monkeypatch, tmp_path, round_store):
    """协议允许的四个调用仍可恢复：长名称与 Pydantic 输入仅留在私有上下文。"""
    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FakeSession)
    work, job = running_job(round_store)
    calls = [call("valid", "tools_inspect", {"tool_name": "sprite.create"}).tool_calls[0]]
    calls += [call("invalid", "preset_create", {"code": "PRIVATE_SOURCE"}).tool_calls[0]]
    calls += [call(f"unknown-{i}", "PRIVATE_TOOL_" * 6, {}).tool_calls[0] for i in range(2)]
    harness = FakeHarness([
        AssistantMessage(tool_calls=calls),
        AssistantMessage(content=json.dumps({"action": "complete", "sprite_id": "sprite-1"})),
    ])
    harness.settings.max_tooluse = 10
    run = AgentRun(harness, None, Budget(on_round=lambda record: round_store.round(job.id, record)), tmp_path, [], lambda *_: None)
    asyncio.run(run.plan_execute())
    rounds = round_store.session(work.id).job.rounds
    assert len(rounds[0].calls) == 4
    assert rounds[0].calls[0].status == "pass"
    assert rounds[0].calls[1].error_code == "INVALID_ARGUMENT"
    assert all(item.tool == "unknown" for item in rounds[0].calls[2:])
    assert "PRIVATE_SOURCE" in run.outer.serialize()
    public = round_store.session(work.id).model_dump_json()
    assert "PRIVATE_SOURCE" not in public and "PRIVATE_TOOL_" not in public


def test_round_storage_does_not_limit_recorded_receipts(round_store):
    """持久化接受宿主的全部回执，不重复设置模型入口已有的四调用协议限制。"""
    work, job = running_job(round_store)
    round_store.round(job.id, ROUND | {"calls": ROUND["calls"] * 6})
    assert len(round_store.session(work.id).job.rounds[0].calls) == 6


def test_round_messages_and_error_codes_are_host_owned(round_store):
    """原始异常、内部路径和任意错误码在存储边界被固定文案替换。"""
    work, job = running_job(round_store)
    for turn, code in enumerate(["COMPOSITION_FAILED", "PRIVATE_CODE_/home/key"], 1):
        round_store.round(job.id, ROUND | {"turn": turn, "calls": [{
            "tool": "sprite.compose", "status": "fail", "error_code": code,
            "message": "PRIVATE_SOURCE /home/internal/secret.tsx " * 100,
        }]})
    snapshot = round_store.session(work.id)
    assert [item.calls[0].error_code for item in snapshot.job.rounds] == ["COMPOSITION_FAILED", "TOOL_FAILED"]
    events = " ".join(event.model_dump_json() for event in round_store.work_events(work.id, 0))
    assert "PRIVATE_" not in events and "/home/" not in events


def test_round_storage_and_events_grow_linearly(round_store):
    """两百轮每轮只保存和推送一条；阶段与终态事件不重复携带轮次。"""
    work, job = running_job(round_store)
    cursor = round_store.session(work.id).cursor
    for turn in range(1, 201):
        round_store.round(job.id, ROUND | {"turn": turn})
    round_store.progress(job.id, "preparing")
    round_store.update(job.id, status="answered", answer="完成")
    events = []
    while batch := round_store.work_events(work.id, cursor):
        events.extend(batch)
        cursor = batch[-1].id
    deltas = [event for event in events if event.type == "job.round"]
    assert len(deltas) == 200
    assert all("rounds" not in event.data for event in events)
    sizes = [len(event.model_dump_json()) for event in deltas]
    assert max(sizes) < min(sizes) + 20
    with round_store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM job_rounds").fetchone()[0] == 200
    assert len(round_store.session(work.id).job.rounds) == 200
    assert round_store.work_history().items[0].job.rounds == []


def test_legacy_jobs_rounds_and_replay_migrate_without_deleting_history(round_store):
    """旧 loop 字段和列表式轮次自动迁移，旧 SSE 错误脱敏且保留事件游标。"""
    from server.remotion_templates.deletion import finish

    work, job = running_job(round_store)
    other, _ = round_store.create(GenerateTemplateRequest(description="保留会话"))
    legacy_round = ROUND | {"calls": [{"tool": "PRIVATE_NAME", "status": "fail", "error_code": "COMPOSITION_FAILED", "message": "PRIVATE_MESSAGE"}]}
    with round_store.connection() as db:
        db.execute("DROP TABLE job_rounds")
        db.execute("CREATE TABLE job_rounds (job_id TEXT PRIMARY KEY REFERENCES jobs(id), data TEXT NOT NULL)")
        db.execute("INSERT INTO job_rounds VALUES (?,?)", (str(job.id), json.dumps([legacy_round])))
        data = json.loads(db.execute("SELECT data FROM jobs WHERE id=?", (str(job.id),)).fetchone()[0])
        data["loop"] = {"layer": "executor", "turn": 3}
        db.execute("UPDATE jobs SET data=? WHERE id=?", (json.dumps(data), str(job.id)))
        db.execute("UPDATE events SET data=? WHERE job_id=?", (json.dumps(data), str(job.id)))
        event = db.execute("SELECT id,data FROM work_events WHERE work_id=? AND type='job.updated' ORDER BY id DESC LIMIT 1", (str(work.id),)).fetchone()
        state = json.loads(event[1]) | {"loop": data["loop"], "rounds": [legacy_round]}
        db.execute("UPDATE work_events SET data=? WHERE id=?", (json.dumps(state), event[0]))
    reopened = Store(round_store.root)
    reopened.initialize()
    snapshot = reopened.session(work.id)
    assert snapshot.job.id == job.id and snapshot.messages
    assert snapshot.job.rounds[0].calls[0].tool == "unknown"
    replay = reopened.work_events(work.id, 0)
    assert replay[-1].id == event[0]
    assert "PRIVATE_" not in " ".join(item.model_dump_json() for item in replay)
    assert all("loop" not in item.data for item in replay)
    assert all("loop" not in json.loads(data) for _, data in reopened.events(job.id, 0))
    reopened.initialize()
    assert reopened.session(work.id) == snapshot
    reopened.begin_deletion(work.id)
    finish(reopened, work.id)
    assert reopened.project(other.id).id == other.id
    with reopened.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM job_rounds").fetchone()[0] == 0
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
