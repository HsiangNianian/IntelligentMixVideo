"""模型预算配置与请求边界回归；执行 uv run --locked pytest tests/test_remotion_model_budget.py，隔离模型和 .env。"""

import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from server.remotion_templates.models import DialogueOutput
from server.remotion_templates.provider import Budget, ModelFailure, Provider
from server.remotion_templates.settings import Settings


def test_model_budget_defaults_and_env_precedence(tmp_path, monkeypatch):
    """翻倍默认值可由文件覆盖，进程环境优先；任务和渲染时限保持原值。"""
    defaults = Settings(_env_file=None)
    assert defaults.enforce_model_budget is False
    assert defaults.model_timeout_seconds == 240
    assert defaults.max_model_calls == 32
    assert defaults.max_tokens == 200_000
    assert defaults.max_output_tokens == 32_000
    assert defaults.job_timeout_seconds == 600
    assert defaults.render_timeout_seconds == 180
    env = tmp_path / ".env"
    env.write_text(
        "IMV_ENFORCE_MODEL_BUDGET=true\nIMV_MAX_OUTPUT_TOKENS=24000\nIMV_MAX_TOKENS=150000\nIMV_MAX_MODEL_CALLS=24\nIMV_MODEL_TIMEOUT_SECONDS=180\n"
    )
    configured = Settings(_env_file=env)
    assert configured.enforce_model_budget is True
    monkeypatch.setenv("IMV_ENFORCE_MODEL_BUDGET", "false")
    assert Settings(_env_file=env).enforce_model_budget is False
    assert (
        configured.max_output_tokens,
        configured.max_tokens,
        configured.max_model_calls,
        configured.model_timeout_seconds,
    ) == (24000, 150000, 24, 180)
    monkeypatch.setenv("IMV_MAX_OUTPUT_TOKENS", "48000")
    assert Settings(_env_file=env).max_output_tokens == 48000


@pytest.mark.parametrize("value", [0, -1, "invalid", 1.5, 1_000_001])
def test_invalid_output_budget_rejected(value):
    """单次输出上限拒绝非正数、非整数与越界值。"""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_output_tokens=value)


@pytest.mark.parametrize("vision", [False, True])
@pytest.mark.parametrize(
    "cap,used,expected", [(32000, 0, 32000), (40000, 0, 40000), (32000, 199999, None)]
)
def test_provider_uses_configured_cap_and_remaining_budget(cap, used, expected, vision):
    """Actor 与 Vision 请求采用配置的输出上限，并按剩余总预算缩小；HTTP 超时实际传入传输层。"""
    settings = Settings(
        _env_file=None,
        enforce_model_budget=True,
        actor_model="offline",
        vision_model="offline-vision",
        actor_api_key=SecretStr("test"),
        max_output_tokens=cap,
    )

    def respond(request):
        """检查真实 HTTP 请求参数，返回仅消耗一个 token 的合法回答。"""
        body = json.loads(request.content)
        assert body["max_tokens"] == expected
        assert body["model"] == ("offline-vision" if vision else "offline")
        assert set(request.extensions["timeout"].values()) == {240}
        return httpx.Response(
            200,
            json={
                "usage": {"total_tokens": 1},
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": '{"answer":"你好"}',
                        },
                    }
                ],
            },
        )

    budget = Budget(tokens=used)
    provider = Provider(settings, transport=httpx.MockTransport(respond))
    if expected is None:
        with pytest.raises(ModelFailure, match="estimated input"):
            asyncio.run(
                provider.ask(DialogueOutput, "system", "user", budget, vision=vision)
            )
        assert budget.calls == 0 and budget.tokens == used
        return
    result = asyncio.run(
        provider.ask(DialogueOutput, "system", "user", budget, vision=vision)
    )
    assert result.answer == "你好" and budget.tokens == used + 1


@pytest.mark.parametrize("budget", [Budget(calls=32), Budget(tokens=200_000)])
def test_exhausted_budget_never_sends_request(budget):
    """翻倍后达到次数或总 token 上限时仍在发请求前停止。"""

    def unexpected_request(request):
        """任何外发请求都说明预算边界失效。"""
        pytest.fail("exhausted budget sent a request")

    provider = Provider(
        Settings(_env_file=None, enforce_model_budget=True),
        transport=httpx.MockTransport(unexpected_request),
    )
    with pytest.raises(ModelFailure, match="budget exhausted"):
        asyncio.run(provider.ask(DialogueOutput, "system", "user", budget))


def test_phase_limit_preserves_other_roles_and_global_cap(tmp_path):
    """各角色分别限额记账，同时不能绕过全局总量；超额响应不作为有效结果。"""
    from server.remotion_templates.provider import ExecutionFailure

    settings = Settings(
        _env_file=None,
        enforce_model_budget=True,
        actor_model="offline",
        actor_api_key=SecretStr("test"),
        max_judge_calls=1,
        max_actor_tokens=1000,
        max_tokens=3000,
        max_output_tokens=64,
    )
    usage = 600

    def respond(request):
        """返回可独立核算的输入输出总量。"""
        return httpx.Response(
            200,
            json={
                "usage": {"total_tokens": usage},
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": '{"answer":"你好"}',
                        },
                    }
                ],
            },
        )

    provider = Provider(settings, transport=httpx.MockTransport(respond))
    budget = Budget(audit_path=tmp_path / "audit.jsonl")
    with budget.phase("judge"):
        asyncio.run(provider._request({"messages": []}, budget))
    with pytest.raises(ExecutionFailure, match="judge"), budget.phase("judge"):
        asyncio.run(provider._request({"messages": []}, budget))
    with budget.phase("actor"):
        asyncio.run(provider._request({"messages": []}, budget))
    with pytest.raises(ExecutionFailure, match="actor"), budget.phase("actor"):
        asyncio.run(provider._request({"messages": []}, budget))
    assert budget.summary() == {
        "calls": 3,
        "tokens": 1800,
        "judge_calls": 1,
        "judge_tokens": 600,
        "actor_calls": 2,
        "actor_tokens": 1200,
    }
    usage = 1300
    settings.max_actor_tokens = 3000
    with (
        pytest.raises(ModelFailure, match="Model token budget exhausted"),
        budget.phase("actor"),
    ):
        asyncio.run(provider._request({"messages": []}, budget))
    assert budget.summary()["actor_tokens"] == 2500
    assert budget.tokens == 3100
    assert budget.active_phase is None


def test_phase_accounting_survives_cancelled_request(tmp_path):
    """取消正在等待的模型请求后，阶段恢复且已发请求仍进入独立审计。"""

    async def scenario():
        """使用内存传输确认进入请求后取消，无真实网络或无界等待。"""
        entered = asyncio.Event()

        async def respond(request):
            """等待取消，保证已经发出了请求。"""
            entered.set()
            await asyncio.Event().wait()

        budget = Budget(audit_path=tmp_path / "audit.jsonl")
        provider = Provider(
            Settings(
                _env_file=None,
                actor_model="offline",
                actor_api_key=SecretStr("secret-not-audited"),
            ),
            transport=httpx.MockTransport(respond),
        )

        async def call():
            """阶段作用域必须在取消时结算并退出。"""
            with budget.phase("actor"):
                await provider.ask(DialogueOutput, "s", "u", budget)

        async with asyncio.timeout(2):
            task = asyncio.create_task(call())
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert budget.summary()["actor_calls"] == 1
        assert budget.active_phase is None
        audit = budget.audit_path.read_text()
        assert "CancelledError" in audit and "secret-not-audited" not in audit

    asyncio.run(scenario())


def test_recovery_settings_read_environment(tmp_path):
    """恢复次数与阶段预算来自 .env，而非写死在 agent loop 中。"""
    path = tmp_path / ".env"
    path.write_text(
        "IMV_MAX_REVIEW_RETRIES=1\nIMV_MAX_EVIDENCE_RETRIES=2\nIMV_MAX_NO_PROGRESS_TURNS=3\nIMV_MAX_JUDGE_CALLS=5\nIMV_MAX_JUDGE_TOKENS=12000\n"
    )
    settings = Settings(_env_file=path)
    assert (
        settings.max_review_retries,
        settings.max_evidence_retries,
        settings.max_no_progress_turns,
        settings.max_judge_calls,
        settings.max_judge_tokens,
    ) == (1, 2, 3, 5, 12000)


def test_input_budget_stops_before_http_and_records_reason(tmp_path):
    """大输入超过剩余额度时不发请求，不扣调用次数，并保留无密钥的预检诊断。"""
    settings = Settings(
        _env_file=None,
        enforce_model_budget=True,
        actor_model="offline",
        actor_api_key=SecretStr("hidden-key"),
        max_tokens=1000,
    )
    provider = Provider(
        settings, transport=httpx.MockTransport(lambda _: pytest.fail("must not send"))
    )
    budget = Budget(audit_path=tmp_path / "audit.jsonl")
    with pytest.raises(ModelFailure, match="estimated input"):
        asyncio.run(provider.ask(DialogueOutput, "system", "用户要求" * 1000, budget))
    assert budget.calls == 0 and budget.tokens == 0
    audit = budget.audit_path.read_text()
    assert '"estimated_input_tokens"' in audit and "hidden-key" not in audit


def test_usage_breakdown_and_output_reservation(tmp_path):
    """输入预算预留后输出额度小于剩余总量，真实回执仍按总量记账并记录输入输出明细。"""
    requests = []

    def respond(request):
        """固定返回有明细的响应，避免依赖真实分词器与模型。"""
        body = json.loads(request.content)
        requests.append(body)
        assert 512 <= body["max_tokens"] < 2000
        return httpx.Response(
            200,
            json={
                "usage": {
                    "total_tokens": 900,
                    "prompt_tokens": 800,
                    "completion_tokens": 100,
                },
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": '{"answer":"完成"}',
                        },
                    }
                ],
            },
        )

    settings = Settings(
        _env_file=None,
        enforce_model_budget=True,
        actor_model="offline",
        actor_api_key=SecretStr("fixture"),
        max_tokens=2000,
    )
    provider = Provider(settings, transport=httpx.MockTransport(respond))
    budget = Budget(audit_path=tmp_path / "audit.jsonl")
    assert asyncio.run(provider.ask(DialogueOutput, "s", "u", budget)).answer == "完成"
    assert budget.tokens == 900
    events = [json.loads(line) for line in budget.audit_path.read_text().splitlines()]
    usage = next(e for e in events if e["event"] == "model_usage")
    assert (usage["input_tokens"], usage["output_tokens"], usage["image_count"]) == (
        800,
        100,
        0,
    )


@pytest.mark.parametrize("enforced", [False, True])
def test_actor_budget_trims_whole_exchanges_without_mutating_history(
    tmp_path, enforced
):
    """关闭配额保留完整请求，开启后只裁剪旧工具组；两者都不修改持久历史。"""
    from server.remotion_templates.context import Conversation

    def exchange(identifier, content="checked"):
        """一组必须同时保留或移除的调用及回执。"""
        return [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": identifier,
                        "type": "function",
                        "function": {
                            "name": "read_current_template",
                            "arguments": "{}",
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": identifier, "content": content},
        ]

    context = Conversation([exchange("old", "long" * 5000), exchange("current")])
    before = context.serialize()

    def respond(request):
        """验证 HTTP 中没有孤立工具回执，且最新候选交互仍在。"""
        body = json.loads(request.content)
        assert [
            m.get("tool_call_id") for m in body["messages"] if m["role"] == "tool"
        ] == (["current"] if enforced else ["old", "current"])
        return httpx.Response(
            200,
            json={
                "usage": {"total_tokens": 200},
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "继续"},
                    }
                ],
            },
        )

    provider = Provider(
        Settings(
            _env_file=None,
            enforce_model_budget=enforced,
            actor_model="offline",
            actor_api_key=SecretStr("fixture"),
            max_tokens=2000,
        ),
        transport=httpx.MockTransport(respond),
    )
    budget = Budget(audit_path=tmp_path / "audit.jsonl")
    asyncio.run(provider.turn("snapshot", context, [], budget))
    assert context.serialize() == before
    assert ('"dropped_groups": 1' in budget.audit_path.read_text()) is enforced


@pytest.mark.parametrize("phase", ["actor", "judge"])
def test_disabled_quotas_allow_over_limit_requests_and_responses(tmp_path, phase):
    """达到全局和角色上限仍发送大输入并接受超额响应；调用和 token 继续如实累计。"""
    settings = Settings(
        _env_file=None,
        actor_model="offline",
        vision_model="offline-vision",
        actor_api_key=SecretStr("fixture"),
        max_model_calls=1,
        max_tokens=1000,
        max_actor_calls=1,
        max_judge_calls=1,
        max_actor_tokens=1000,
        max_judge_tokens=1000,
    )
    budget = Budget(
        calls=50,
        tokens=200_000,
        phases={phase: {"calls": 50, "tokens": 200_000}},
        audit_path=tmp_path / "audit.jsonl",
    )

    def respond(request):
        """模拟大输入的合法回复，输出上限不因已用额度缩小。"""
        body = json.loads(request.content)
        assert body["max_tokens"] == 32000
        return httpx.Response(
            200,
            json={
                "usage": {"total_tokens": 50000},
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": '{"answer":"继续执行"}',
                        },
                    }
                ],
            },
        )

    provider = Provider(settings, transport=httpx.MockTransport(respond))
    for _ in range(2):
        with budget.phase(phase):
            result = asyncio.run(
                provider.ask(
                    DialogueOutput,
                    "rules",
                    "需求" * 20000,
                    budget,
                    vision=phase == "judge",
                )
            )
        assert result.answer == "继续执行"
    assert budget.summary() == {
        "calls": 52,
        "tokens": 300000,
        f"{phase}_calls": 52,
        f"{phase}_tokens": 300000,
    }
    events = [json.loads(line) for line in budget.audit_path.read_text().splitlines()]
    requests = [e for e in events if e["event"] == "model_request"]
    assert len(requests) == 2
    assert all(
        e["remaining_tokens"] is None and e["estimated_input_tokens"] > 1000
        for e in requests
    )


@pytest.mark.parametrize("enforced", [False, True])
@pytest.mark.parametrize(
    "usage", [None, {}, {"total_tokens": -1}, {"total_tokens": True}]
)
def test_missing_usage_is_unknown_without_quotas(tmp_path, enforced, usage):
    """关闭配额时缺失/非法用量只记未知；恢复配额后仍拒绝不可核算的响应。"""

    def respond(request):
        """提供合法回答但不提供可信 token 总量。"""
        return httpx.Response(
            200,
            json={
                "usage": usage,
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": '{"answer":"完成"}',
                        },
                    }
                ],
            },
        )

    provider = Provider(
        Settings(
            _env_file=None,
            enforce_model_budget=enforced,
            actor_model="offline",
            actor_api_key=SecretStr("fixture"),
        ),
        transport=httpx.MockTransport(respond),
    )
    budget = Budget(tokens=100, audit_path=tmp_path / "audit.jsonl")
    if enforced:
        with pytest.raises(ModelFailure, match="valid token usage"):
            asyncio.run(provider.ask(DialogueOutput, "s", "u", budget))
    else:
        assert (
            asyncio.run(provider.ask(DialogueOutput, "s", "u", budget)).answer == "完成"
        )
        events = [
            json.loads(line) for line in budget.audit_path.read_text().splitlines()
        ]
        event = next(e for e in events if e["event"] == "model_usage")
        assert event["total_tokens"] is None
    assert budget.calls == 1 and budget.tokens == 100
