"""公开的 ReAct 每轮记录：工具名与结论、白名单校验、终态与去重，以及供前端审计的有界细节（模型给出的原始参数、工具真实错误、无调用轮次的模型回复与失败原因）。

不调用真实模型、浏览器或 Chroma；Agent 循环用脚本化替身驱动。
在 server/ 目录执行 `uv run --locked pytest tests/test_remotion_loop_rounds.py -v`。
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from sqlalchemy import func, select

from server.remotion_templates import history
from server.remotion_templates.agent import AgentRun, Layer
from server.remotion_templates.context import AssistantMessage
from server.remotion_templates.models import GenerateTemplateRequest
from server.remotion_templates.planning import PlanAction, default_plan
from server.remotion_templates.progress import LoopRound
from server.remotion_templates.provider import Budget, ModelContractFailure, ModelFailure
from server.remotion_templates.store import Store
from server.remotion_templates.tables import job_rounds, work_events
from server.remotion_templates.tools.registry import ToolFault
from .conftest import DEVICE

ROUND = {
    "layer": "executor",
    "turn": 3,
    "calls": [{"tool": "sprite.compose", "status": "pass"}],
}


@pytest.fixture
def round_store(tmp_path, template_db):
    """使用每例独立的 MySQL 替身与本地对象目录，不读取用户模板或模型配置。"""
    store = Store(tmp_path / "rounds")
    store.initialize()
    return store


def running_job(store):
    """创建一个正在运行的任务，只有运行中的任务接受每轮记录。"""
    work, job = store.create(GenerateTemplateRequest(description="标题"), DEVICE)
    store.claim()
    return work, job


def rounds(store, job):
    """客户端收到的公开任务视图里的轮次记录。"""
    with store.reading() as db:
        return history.public_job(db, store.job(job.id)).rounds


def cursor(store, work):
    """该作品最新的待送达事件编号。"""
    with store.reading() as db:
        return db.execute(select(func.coalesce(func.max(work_events.c.id), 0)).where(work_events.c.work_id == str(work.id))).scalar_one()


def test_round_is_published_scoped_and_durable(round_store):
    """每轮记录写入公开任务视图与待送达事件，只含声明的字段，并在重新打开存储后保持一致。"""
    store = round_store
    work, job = running_job(store)
    store.round(job.id, ROUND)
    assert rounds(store, job) == [LoopRound.model_validate(ROUND)]
    # 同一份记录也作为 job.round 事件送达客户端，刷新和重连看到一致结果。
    assert store.work_events(work.id, 0)[-1].data["round"] == LoopRound.model_validate(ROUND).model_dump(mode="json")
    reopened = Store(store.root)
    reopened.initialize()
    assert rounds(reopened, job) == rounds(store, job)


def test_public_payload_is_whitelisted_and_clipped(round_store):
    """公开事件只含声明的字段；超长的模型回复、参数与错误被截断，错误码与固定说明仍由宿主决定。"""
    store = round_store
    work, job = running_job(store)
    after = cursor(store, work)
    store.round(
        job.id,
        ROUND
        | {
            "message": "回" * 9000,
            "calls": [
                {
                    "tool": "sprite.create",
                    "status": "fail",
                    "error_code": "COMPOSITION_FAILED",
                    "message": "ignored",
                    "detail": "真实错误 " * 1000,
                    "arguments": json.dumps({"draft_id": "d1", "pad": "x" * 9000}),
                }
            ],
        },
    )
    events = store.work_events(work.id, after)
    assert [event.type for event in events] == ["job.round"]
    published = events[-1].data["round"]
    assert {key for key, value in published.items() if value is not None} == {"layer", "turn", "calls", "message"}
    assert len(published["message"]) == 4000 and published["message"].endswith("…")
    call_ = published["calls"][0]
    assert {key for key, value in call_.items() if value is not None} == {"tool", "status", "error_code", "message", "detail", "arguments"}
    assert call_["message"] == "组合生成失败。"
    assert len(call_["detail"]) == 2000 and len(call_["arguments"]) == 4000
    with pytest.raises(ValidationError):
        LoopRound.model_validate(ROUND | {"feedback": "extra"})


def test_unknown_layer_is_rejected_and_not_stored(round_store):
    """白名单之外的角色被拒绝，且不会写入任何记录。"""
    store = round_store
    work, job = running_job(store)
    with pytest.raises(ValidationError):
        store.round(job.id, {"layer": "judge", "turn": 1})
    assert rounds(store, job) == []


def test_repeated_layer_turn_is_recorded_once(round_store):
    """同一层同一轮重复上报只留一条，重试的处理流程不会写出重复轮次。"""
    store = round_store
    work, job = running_job(store)
    store.round(job.id, ROUND)
    first = cursor(store, work)
    store.round(job.id, ROUND | {"calls": []})
    assert cursor(store, work) == first
    store.round(job.id, ROUND | {"turn": 4})
    assert [item.turn for item in rounds(store, job)] == [3, 4]


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled", "interrupted"])
def test_terminal_job_accepts_no_more_rounds(round_store, status):
    """终态任务不再接受记录，迟到上报不能改写已结束的历史。"""
    store = round_store
    work, job = running_job(store)
    store.round(job.id, ROUND)
    store.update(job.id, status=status)
    after = cursor(store, work)
    store.round(job.id, {"layer": "outer", "turn": 9})
    assert cursor(store, work) == after
    assert [item.turn for item in rounds(store, job)] == [3]


class FakeSession:
    """只暴露 AgentRun 用到的任务级接口，不访问模型或渲染器。"""

    def __init__(self, _harness, _spec, _base, _budget, directory, images, _stage, intent):
        self.directory = Path(directory)
        self.images = images
        self.intent = intent or {"original_request": {"description": "demo"}}
        self.latest_sprite_id = "sprite-1"
        self.saved_sprites = {"sprite-1": {"sprite_id": "sprite-1"}}

    def snapshot(self, **_options):
        """返回确定性的宿主快照。"""
        return {"user_intent": self.intent, "sprites": sorted(self.saved_sprites)}

    def saved_sprite(self, identifier):
        """只解析本任务已保存的 Sprite。"""
        return self.saved_sprites[identifier]

    async def execute(self, name, args, _catalog):
        """记录一次确定性的业务工具观察；读取预设回执带 preset_id，sprite_create 像真实工具一样保存新 Sprite。"""
        if name == "sprite.create":
            identifier = f"sprite-{len(self.saved_sprites) + 1}"
            self.saved_sprites[identifier] = {"sprite_id": identifier}
            self.latest_sprite_id = identifier
        return {
            "tool": name, "args": args.model_dump(mode="json"), "preset": {"preset_id": "preset-1"},
            "observation": "RESULT_ONLY_MARKER",
        }


class FailingSession(FakeSession):
    """让业务工具按契约失败，用于验证结论、错误码与错误说明进入记录，而结构化 details 不进入。"""

    async def execute(self, name, args, _catalog):
        """以真实工具的错误码和说明失败，不返回任何结果。"""
        raise ToolFault(
            "CODE_VALIDATION_FAILED",
            "Preset code validation failed.",
            details={"validation": {"tsx_code": "DETAILS_ONLY_SOURCE"}},
        )


class FakeHarness:
    """脚本化模型轮次，并记录每一层被请求的顺序。"""

    def __init__(self, responses):
        self.responses = iter(responses)
        self.roles = []
        self.fonts = SimpleNamespace(guide=lambda: "")
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


def report(identifier, status="step_done", summary="已完成"):
    """构造 Executor 的 tools_report_step 调用。"""
    return call(identifier, "tools_report_step", {"status": status, "summary": summary})


def test_real_loop_records_every_turn_with_its_tools(monkeypatch, tmp_path):
    """真实三层循环每轮都留一条记录，带该轮实际调用的工具与结论；标准计划无需 Plan 模型参与。"""
    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FakeSession)
    harness = FakeHarness(
        [
            call("outer-1", "tools_plan_execute", {"action": "delegate", "reason": "暖色调滤镜"}),
            call("exec-1", "preset_search", {"preset_id": "preset-1"}),
            AssistantMessage(content=json.dumps({"status": "step_done", "summary": "预设已就绪"})),
            call("exec-2", "sprite_create", {"draft_id": "PRIVATE_DRAFT"}),
            report("exec-3"),
        ]
    )
    seen: list[dict] = []
    run = AgentRun(
        harness, None, Budget(on_round=seen.append), tmp_path, [], lambda *_: None,
        intent={"original_request": {"description": "demo"}},
    )
    assert asyncio.run(run.plan_execute()) == {"published": "sprite-2"}
    assert [(item["layer"], item["turn"]) for item in seen] == [
        ("outer", 1), ("executor", 2), ("executor", 3), ("executor", 4), ("executor", 5),
    ]
    # 控制工具与业务工具都按契约里的点号 ID 记录，而不是模型侧的调用名。
    assert [(item["calls"][0]["tool"], item["calls"][0]["status"]) for item in (seen[0], seen[1], seen[4])] == [
        ("tools.plan_execute", "pass"), ("preset.search", "pass"), ("tools.report_step", "pass"),
    ]
    # 每次调用带模型给出的原始参数（有意公开），前端展开即可核对；工具成功结果仍只在私有审计。
    assert "RESULT_ONLY_MARKER" not in json.dumps(seen)
    search = seen[1]["calls"][0]
    assert json.loads(search["arguments"]) == {"preset_id": "preset-1"}
    assert "PRIVATE_DRAFT" in json.dumps(seen[3]["calls"][0], ensure_ascii=False)
    assert json.loads(seen[0]["calls"][0]["arguments"]) == {"action": "delegate", "reason": "暖色调滤镜"}
    # 只回消息、没有调用工具的轮次没有调用，但保留模型回复，不再是空白。
    assert seen[2]["calls"] == []
    assert json.loads(seen[2]["message"])["summary"] == "预设已就绪"


def test_failed_call_is_recorded_with_its_error_code(monkeypatch, tmp_path):
    """失败的工具调用按轮记录错误码与说明，而不是只标一个失败。"""
    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FailingSession)
    harness = FakeHarness(
        [
            call("outer-1", "tools_plan_execute", {"action": "delegate", "reason": "标题"}),
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
            report("exec-2", "blocked", "保存失败"),
            call("plan-1", "tools_plan_execute", {"action": "complete"}),
        ]
    )
    seen: list[dict] = []
    run = AgentRun(
        harness, None, Budget(on_round=seen.append), tmp_path, [], lambda *_: None,
        intent={"original_request": {"description": "demo"}},
    )
    asyncio.run(run.plan_execute())
    failed = seen[1]
    assert failed["layer"] == "executor" and failed["turn"] == 2
    (failure,) = failed["calls"]
    assert {key: failure[key] for key in ("tool", "status", "error_code", "message")} == {
        "tool": "preset.create",
        "status": "fail",
        "error_code": "CODE_VALIDATION_FAILED",
        "message": "代码校验未通过。",
    }
    # 工具抛出的真实说明与模型给出的参数（含候选源码）一并公开，方便定位失败原因；ToolFault 的结构化 details 不是公开字段。
    assert failure["detail"] == "Preset code validation failed."
    arguments = json.loads(failure["arguments"])
    assert arguments["description"] == "标题"
    assert arguments["code"] == "export default function Label(){return null}"
    assert "details" not in failure and "DETAILS_ONLY_SOURCE" not in json.dumps(seen)


def test_narration_beside_calls_is_not_published(monkeypatch, tmp_path):
    """带工具调用的轮次不公开模型的旁白；只有没有调用的轮次才带模型回复 `message`。"""
    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FakeSession)
    narrated = AssistantMessage(
        content="NARRATION_ONLY",
        tool_calls=call("outer-1", "tools_plan_execute", {"action": "delegate", "reason": "标题"}).tool_calls,
    )
    harness = FakeHarness([
        narrated,
        AssistantMessage(content="只回复的内容"),
        call("exec-1", "tools_report_step", {"status": "blocked", "summary": "无法继续"}),
        call("plan-1", "tools_plan_execute", {"action": "complete"}),
    ])
    seen: list[dict] = []
    run = AgentRun(
        harness, None, Budget(on_round=seen.append), tmp_path, [], lambda *_: None,
        intent={"original_request": {"description": "demo"}},
    )
    asyncio.run(run.plan_execute())
    assert seen[0]["calls"] and "message" not in seen[0]
    assert "NARRATION_ONLY" not in json.dumps(seen, ensure_ascii=False)
    assert seen[1]["calls"] == [] and seen[1]["message"] == "只回复的内容"


def test_plan_goals_and_candidate_source_are_published_verbatim(round_store):
    """参数原文有意公开：计划步骤目标与候选源码不被剥离，经事件送达并随任务视图保留。"""
    store = round_store
    work, job = running_job(store)
    after = cursor(store, work)
    plan = {"action": "update_plan", "plan": {"steps": [{"goal": "Reuse a fitting stored Preset", "done_when": "preset reused"}]}}
    source = {"code": "export default function Label(){return null}"}
    store.round(job.id, ROUND | {"calls": [
        {"tool": "tools.plan_execute", "status": "pass", "arguments": json.dumps(plan)},
        {"tool": "preset.create", "status": "pass", "arguments": json.dumps(source)},
    ]})
    published = store.work_events(work.id, after)[-1].data["round"]["calls"]
    assert json.loads(published[0]["arguments"]) == plan
    assert json.loads(published[1]["arguments"]) == source
    assert [json.loads(call_.arguments) for call_ in rounds(store, job)[0].calls] == [plan, source]


def test_model_failure_reason_is_published(monkeypatch, tmp_path):
    """模型请求失败时，该轮记录 MODEL_FAILED 和（已净化的）失败原因，再把异常交给上层。"""
    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FakeSession)
    harness = FakeHarness([ModelFailure("Model request timed out.")])
    seen: list[dict] = []
    run = AgentRun(
        harness, None, Budget(on_round=seen.append), tmp_path, [], lambda *_: None,
        intent={"original_request": {"description": "demo"}},
    )
    with pytest.raises(ModelFailure):
        asyncio.run(run.plan_execute())
    assert seen == [{
        "layer": "outer", "turn": 1, "calls": [],
        "error_code": "MODEL_FAILED", "error_detail": "Model request timed out.",
    }]


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
    assert rounds(store, job) == [
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
        call("ask-wrong-layer", "tools_inspect", {"tool_name": "sprite.create"}),
        ModelContractFailure("Model returned an invalid tool-call contract."),
        AssistantMessage(content=json.dumps({"action": "complete", "sprite_id": "sprite-1"})),
    ])
    run = AgentRun(harness, None, Budget(on_round=lambda record: round_store.round(job.id, record)), tmp_path, [], lambda *_: None)
    asyncio.run(run.plan_execute())
    recorded = rounds(round_store, job)
    assert [item.turn for item in recorded] == [1, 2, 3]
    assert recorded[1].error_code == "MODEL_CONTRACT_FAILED"
    assert recorded[1].calls == []
    # 协议失败的原因（异常文本，与生产一致的固定句）公开给前端，说明这一轮为什么没有调用工具；宿主下一轮的纠错指示不公开。
    assert recorded[1].error_detail == "Model returned an invalid tool-call contract."
    assert recorded[0].calls[0].status == "fail"


def test_unresolved_names_do_not_break_rounds(monkeypatch, tmp_path, round_store):
    """协议允许的四个调用仍可恢复：长名称与 Pydantic 输入仅留在私有上下文。"""
    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FakeSession)
    work, job = running_job(round_store)
    calls = [call("valid", "tools_inspect", {"tool_name": "sprite.create"}).tool_calls[0]]
    calls += [call("invalid", "preset_create", {"code": "PRIVATE_SOURCE"}).tool_calls[0]]
    calls += [call(f"unknown-{i}", "PRIVATE_TOOL_" * 6, {}).tool_calls[0] for i in range(2)]
    harness = FakeHarness([
        AssistantMessage(tool_calls=calls),
        report("blocked", "blocked", "无法继续"),
        call("plan-stop", "tools_plan_execute", {"action": "complete"}),
    ])
    harness.settings.max_tooluse = 10
    run = AgentRun(harness, None, Budget(on_round=lambda record: round_store.round(job.id, record)), tmp_path, [], lambda *_: None)
    # Business tools run only in the Executor; start the host's standard plan as a delegation would.
    run.state.control(PlanAction(action="update_plan", plan=default_plan(edit=False)))
    run.layer = Layer.EXECUTOR
    asyncio.run(run.plan_execute())
    recorded = rounds(round_store, job)
    assert len(recorded[0].calls) == 4
    assert recorded[0].calls[0].status == "pass"
    assert recorded[0].calls[1].error_code == "INVALID_ARGUMENT"
    assert all(item.tool == "unknown" for item in recorded[0].calls[2:])
    # 未知名称仍记为 unknown，但模型请求的原名（截断到 64 字）与宿主的纠错说明保留，才能看出叫错了什么。
    assert recorded[0].calls[2].requested_tool.startswith("PRIVATE_TOOL_")
    assert len(recorded[0].calls[2].requested_tool) == 64
    assert "Unknown or out-of-scope tool" in recorded[0].calls[2].detail
    assert recorded[0].calls[1].tool == "preset.create" and "PRIVATE_SOURCE" in recorded[0].calls[1].arguments
    assert "PRIVATE_SOURCE" in run.executor_context.serialize()


def test_round_storage_does_not_limit_recorded_receipts(round_store):
    """持久化接受宿主的全部回执，不重复设置模型入口已有的四调用协议限制。"""
    work, job = running_job(round_store)
    round_store.round(job.id, ROUND | {"calls": ROUND["calls"] * 6})
    assert len(rounds(round_store, job)[0].calls) == 6


def test_round_messages_and_error_codes_are_host_owned(round_store):
    """`message` 与错误码由宿主决定：调用方给的原始异常、内部路径和任意错误码在存储边界被固定文案替换（真实错误只走 `detail`）。"""
    work, job = running_job(round_store)
    for turn, code in enumerate(["COMPOSITION_FAILED", "PRIVATE_CODE_/home/key"], 1):
        round_store.round(job.id, ROUND | {"turn": turn, "calls": [{
            "tool": "sprite.compose", "status": "fail", "error_code": code,
            "message": "PRIVATE_SOURCE /home/internal/secret.tsx " * 100,
        }]})
    assert [item.calls[0].error_code for item in rounds(round_store, job)] == ["COMPOSITION_FAILED", "TOOL_FAILED"]
    events = " ".join(event.model_dump_json() for event in round_store.work_events(work.id, 0))
    assert "PRIVATE_" not in events and "/home/" not in events


def test_round_storage_and_events_grow_linearly(round_store):
    """两百轮每轮只保存和推送一条；阶段与终态事件不重复携带轮次。"""
    work, job = running_job(round_store)
    after = cursor(round_store, work)
    for turn in range(1, 201):
        round_store.round(job.id, ROUND | {"turn": turn})
    round_store.progress(job.id, "preparing")
    round_store.update(job.id, status="answered", answer="完成")
    events = []
    while batch := round_store.work_events(work.id, after):
        events.extend(batch)
        after = batch[-1].id
    deltas = [event for event in events if event.type == "job.round"]
    assert len(deltas) == 200
    assert all("rounds" not in event.data for event in events)
    sizes = [len(event.model_dump_json()) for event in deltas]
    assert max(sizes) < min(sizes) + 20
    with round_store.reading() as db:
        assert db.execute(select(func.count()).select_from(job_rounds)).scalar_one() == 200
    assert len(rounds(round_store, job)) == 200
