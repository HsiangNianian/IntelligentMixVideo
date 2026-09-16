"""模型预算配置与请求边界回归；执行 uv run --locked pytest tests/test_remotion_model_budget.py，隔离模型和 .env。"""

import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from server.remotion_templates.models import DialogueOutput
from server.remotion_templates.provider import Budget, ModelFailure, Provider
from server.settings import Settings


def test_model_budget_defaults_and_env_precedence(tmp_path, monkeypatch):
    """翻倍默认值可由文件覆盖，进程环境优先；任务和渲染时限保持原值。"""
    defaults = Settings(_env_file=None)
    assert defaults.model_timeout_seconds == 240
    assert defaults.max_model_calls == 32
    assert defaults.max_tokens == 200_000
    assert defaults.max_output_tokens == 32_000
    assert defaults.job_timeout_seconds == 600
    assert defaults.render_timeout_seconds == 180
    env = tmp_path / ".env"
    env.write_text(
        "IMV_MAX_OUTPUT_TOKENS=24000\nIMV_MAX_TOKENS=150000\nIMV_MAX_MODEL_CALLS=24\nIMV_MODEL_TIMEOUT_SECONDS=180\n"
    )
    configured = Settings(_env_file=env)
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
    "cap,used,expected", [(32000, 0, 32000), (40000, 0, 40000), (32000, 199999, 1)]
)
def test_provider_uses_configured_cap_and_remaining_budget(cap, used, expected, vision):
    """Actor 与 Vision 请求采用配置的输出上限，并按剩余总预算缩小；HTTP 超时实际传入传输层。"""
    settings = Settings(
        _env_file=None,
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
        Settings(_env_file=None), transport=httpx.MockTransport(unexpected_request)
    )
    with pytest.raises(ModelFailure, match="budget exhausted"):
        asyncio.run(provider.ask(DialogueOutput, "system", "user", budget))


def test_phase_limit_preserves_other_roles_and_global_cap(tmp_path):
    """各角色分别限额记账，同时不能绕过全局总量；超额响应不作为有效结果。"""
    from server.remotion_templates.provider import ExecutionFailure

    settings = Settings(
        _env_file=None,
        actor_model="offline",
        actor_api_key=SecretStr("test"),
        max_judge_calls=1,
        max_actor_tokens=1000,
        max_tokens=3000,
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
        asyncio.run(provider.ask(DialogueOutput, "s", "u", budget))
    with pytest.raises(ExecutionFailure, match="judge"), budget.phase("judge"):
        asyncio.run(provider.ask(DialogueOutput, "s", "u", budget))
    with budget.phase("actor"):
        asyncio.run(provider.ask(DialogueOutput, "s", "u", budget))
    with pytest.raises(ExecutionFailure, match="actor"), budget.phase("actor"):
        asyncio.run(provider.ask(DialogueOutput, "s", "u", budget))
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
        asyncio.run(provider.ask(DialogueOutput, "s", "u", budget))
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
