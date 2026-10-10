"""模型请求重试与协议恢复边界；内存 HTTP/SSE 夹具不访问真实模型或凭据。"""

import asyncio
import httpx
import pytest
from pydantic import SecretStr

from server.remotion_templates.models import DialogueOutput
from server.remotion_templates.provider import Budget, ModelContractFailure, ModelFailure, Provider
from server.remotion_templates.settings import Settings


class ResponseStream(httpx.AsyncByteStream):
    """按顺序发送字节或抛出异常，记录连接是否在失败/重试前关闭。"""

    def __init__(self, *items):
        """保存确定的网络事件，不使用计时或真实连接。"""
        self.items = items
        self.closed = False

    async def __aiter__(self):
        """使用真实 HTTPX 响应读取路径注入流片段和传输故障。"""
        for item in self.items:
            if isinstance(item, Exception):
                raise item
            yield item

    async def aclose(self):
        """记录流资源清理，允许 HTTPX 正常关闭响应。"""
        self.closed = True


@pytest.fixture
def retry_waits(monkeypatch):
    """跳过退避时间但保留次数和延迟断言，避免测试真实等待。"""
    delays = []

    async def sleep(delay):
        """收集宿主请求的退避延迟。"""
        delays.append(delay)

    monkeypatch.setattr("server.remotion_templates.provider.asyncio.sleep", sleep)
    return delays


def provider(handler, **settings):
    """使用独立配置和内存传输，真实走 Provider 的解析与分类。"""
    return Provider(Settings(_env_file=None, actor_model="offline", actor_api_key=SecretStr("PRIVATE_KEY"), **settings),
                    transport=httpx.MockTransport(handler))


def answer():
    """构造成功重试的完整 JSON 回执，尚未返回前不得执行任何工具。"""
    return httpx.Response(200, json={"usage": {"total_tokens": 7}, "choices": [{
        "finish_reason": "stop", "message": {"role": "assistant", "content": '{"answer":"完成"}'},
    }]})


@pytest.mark.parametrize("body,content_type", [
    (b'{PRIVATE_INVALID_JSON', 'application/json'),
    (b'data: {PRIVATE_INVALID_JSON\n\n', 'text/event-stream'),
    (b'[]', 'application/json'),
    (b'{"choices":[null]}', 'application/json'),
    (b'{"choices":[{"finish_reason":"stop","message":null}]}', 'application/json'),
    (b'data: {"choices":[{"index":1,"delta":{}}]}\n\n', 'text/event-stream'),
])
def test_malformed_response_remains_recoverable_without_transport_retry(body, content_type, retry_waits):
    """坏 JSON、错误容器和畸形 SSE 仍归为可纠正的协议失败，且不重发同一请求。"""
    requests = []

    def respond(request):
        """返回指定的畸形完整响应。"""
        requests.append(request)
        return httpx.Response(200, content=body, headers={"content-type": content_type})

    with pytest.raises(ModelContractFailure) as failure:
        asyncio.run(provider(respond).ask(DialogueOutput, "s", "u", Budget()))
    assert "PRIVATE_" not in str(failure.value)
    assert len(requests) == 1 and retry_waits == []


@pytest.mark.parametrize("error", [httpx.ReadError, httpx.ReadTimeout])
@pytest.mark.parametrize("started", [False, True])
def test_only_transport_failures_before_response_bytes_are_retried(error, started, retry_waits, tmp_path):
    """已收到内容后中断立即失败；首字节前故障可重试，先关闭旧流且只记一次逻辑调用。"""
    fragment = b'data: {"choices":[{"index":0,"delta":{"role":"assistant","content":"PRIVATE_PARTIAL"}}]}\n\n'
    stream = ResponseStream(*([fragment] if started else []), error("PRIVATE_TRANSPORT"))
    requests = []

    def respond(request):
        """首次请求流式失败，任何重试都必须发生在旧响应关闭后。"""
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})
        assert stream.closed
        return answer()

    budget = Budget(audit_path=tmp_path / "audit.jsonl")
    operation = provider(respond).ask(DialogueOutput, "s", "u", budget)
    if started:
        with pytest.raises(ModelFailure) as failure:
            asyncio.run(operation)
        assert not isinstance(failure.value, ModelContractFailure)
        assert len(requests) == 1 and retry_waits == []
        assert budget.tokens == 0
    else:
        assert asyncio.run(operation).answer == "完成"
        assert len(requests) == 2 and retry_waits == [2.0]
        assert budget.tokens == 7
    assert stream.closed and budget.calls == 1
    assert "PRIVATE_" not in budget.audit_path.read_text()


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 520, 524, 529, 599])
def test_all_documented_server_errors_can_recover(status, retry_waits):
    """含模型网关常见 529 在内的 429/5xx 全部遵循有界重试策略。"""
    requests = []

    def respond(request):
        """首次上游失败后恢复，测试不等待真实网关。"""
        requests.append(request)
        return httpx.Response(status) if len(requests) == 1 else answer()

    assert asyncio.run(provider(respond).ask(DialogueOutput, "s", "u", Budget())).answer == "完成"
    assert len(requests) == 2 and retry_waits == [2.0]


@pytest.mark.parametrize("error", [httpx.UnsupportedProtocol, httpx.LocalProtocolError, httpx.DecodingError])
def test_deterministic_http_errors_are_sanitized_without_retries(error, retry_waits):
    """非法协议、请求头或编码不是瞬时故障；不重发，也不直接冒出 HTTPX 内部错误。"""
    requests = []

    def respond(request):
        """注入带敏感说明的确定性 HTTP 错误。"""
        requests.append(request)
        raise error("PRIVATE_KEY invalid request", request=request)

    with pytest.raises(ModelFailure) as failure:
        asyncio.run(provider(respond).ask(DialogueOutput, "s", "u", Budget()))
    assert "PRIVATE_KEY" not in str(failure.value)
    assert len(requests) == 1 and retry_waits == []


def test_setting_zero_retries_preserves_single_attempt(retry_waits):
    """显式关闭重试仍按原始单请求路径失败，不等待也不重发。"""
    requests = []

    def respond(request):
        """持续返回限流，以验证禁用配置确实生效。"""
        requests.append(request)
        return httpx.Response(429)

    with pytest.raises(ModelFailure, match="HTTP 429"):
        asyncio.run(provider(respond, model_retries=0).ask(DialogueOutput, "s", "u", Budget()))
    assert len(requests) == 1 and retry_waits == []


@pytest.mark.parametrize("body,content_type", [
    (b'data: {"choices":[{"index":0,"delta":{"role":"assistant","content":"partial"}}]}\n\n', "text/event-stream"),
    (b'x' * 2_000_001, "application/json"),
], ids=["missing-done", "oversized-json"])
def test_incomplete_and_oversized_responses_never_retry(body, content_type, retry_waits):
    """拒绝不完整/超大响应；显式用例名避免 pytest -v 把 2 MB 数据写进 CI 日志。"""
    stream = ResponseStream(body)
    requests = []

    def respond(request):
        """通过真实流读取触发确定性失败，同时检查连接关闭。"""
        requests.append(request)
        return httpx.Response(200, stream=stream, headers={"content-type": content_type})

    with pytest.raises(ModelFailure) as failure:
        asyncio.run(provider(respond).ask(DialogueOutput, "s", "u", Budget()))
    assert not isinstance(failure.value, ModelContractFailure)
    assert stream.closed and len(requests) == 1 and retry_waits == []


def test_task_deadline_interrupts_backoff_and_preserves_phase_accounting(tmp_path):
    """任务总期限不被重试扩展，等待中超时仍按原角色结算一次调用并清理作用域。"""
    requests = []

    def respond(request):
        """一次限流后进入长退避，由外层期限立即终止。"""
        requests.append(request)
        return httpx.Response(429)

    budget = Budget(audit_path=tmp_path / "audit.jsonl")

    async def scenario():
        """模拟 Runtime 的整任务超时，禁止实际等待完整退避周期。"""
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.1):
                with budget.phase("outer"):
                    await provider(respond, model_retry_delay_seconds=30).ask(DialogueOutput, "s", "u", budget)

    asyncio.run(scenario())
    assert len(requests) == 1
    assert budget.summary()["outer_calls"] == 1 and budget.active_phase is None
    assert '"event": "model_retry"' in budget.audit_path.read_text()


@pytest.mark.parametrize("field,value", [
    ("model_retries", -1), ("model_retries", 6), ("model_retries", 1.5),
    ("model_retry_delay_seconds", 0), ("model_retry_delay_seconds", 31),
    ("model_retry_delay_seconds", float("nan")),
])
def test_invalid_retry_settings_are_rejected(field, value):
    """重试次数及延迟的非法值在配置加载时拒绝，不进入执行循环。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_retry_settings_follow_environment_precedence(tmp_path, monkeypatch):
    """环境文件和进程环境可覆盖默认重试策略，客户端配置不包含服务端重试开关。"""
    from server.remotion_templates.settings import ClientSettings

    defaults = Settings(_env_file=None)
    assert (defaults.model_retries, defaults.model_retry_delay_seconds) == (2, 2.0)
    env = tmp_path / "retry.env"
    env.write_text("IMV_MODEL_RETRIES=1\nIMV_MODEL_RETRY_DELAY_SECONDS=0.5\n")
    configured = Settings(_env_file=env)
    assert (configured.model_retries, configured.model_retry_delay_seconds) == (1, 0.5)
    monkeypatch.setenv("IMV_MODEL_RETRIES", "0")
    assert Settings(_env_file=env).model_retries == 0
    assert "model_retries" not in ClientSettings.model_fields
