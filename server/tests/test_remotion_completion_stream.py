"""Offline SSE model transport regressions: fragmented tools/JSON, terminal usage, cancellation and truncation."""

import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from server.remotion_templates.context import Conversation
from server.remotion_templates.models import AnswerReview
from server.remotion_templates.provider import (
    Budget,
    ModelContractFailure,
    ModelFailure,
    Provider,
)
from server.remotion_templates.settings import Settings


def event(delta=None, finish=None, **extra):
    """Encode one SSE data event with CRLF to exercise transport-independent framing."""
    payload = {
        "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}],
        **extra,
    }
    return ("data: " + json.dumps(payload, ensure_ascii=False) + "\r\n\r\n").encode()


class BytesStream(httpx.AsyncByteStream):
    """A genuinely asynchronous response can split JSON and UTF-8 at any byte position."""

    def __init__(self, data, *, step=7):
        """Keep all fixture bytes offline and record transport cleanup."""
        self.data, self.step, self.closed = data, step, False

    async def __aiter__(self):
        """Yield fragmented bytes without turning them into complete JSON response fixtures."""
        for start in range(0, len(self.data), self.step):
            await asyncio.sleep(0)
            yield self.data[start : start + self.step]

    async def aclose(self):
        """Expose stream closure on normal completion, parse failure and cancellation."""
        self.closed = True


def provider(stream, *, enforce=False, timeout=600):
    """Assert the actual outgoing streaming options and configured timeout for every offline request."""
    settings = Settings(
        _env_file=None,
        actor_model="glm-5.3-flash",
        vision_model="vision",
        actor_api_key=SecretStr("fixture"),
        enforce_model_budget=enforce,
        model_timeout_seconds=timeout,
    )

    def respond(request):
        """Accept no vendor thinking extension and return only SSE, never a preassembled message."""
        body = json.loads(request.content)
        assert body["stream"] is True
        assert body["stream_options"] == {"include_usage": True}
        assert "thinking" not in body
        assert request.extensions["timeout"]["read"] == timeout
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream; charset=utf-8"},
            stream=stream,
        )

    return Provider(settings, transport=httpx.MockTransport(respond))


def test_actor_assembles_interleaved_tool_fragments_and_accounts_usage_once(tmp_path):
    """多工具名称/参数跨事件交错，中文跨字节拆分；[DONE] 后才返回完整工具调用与最终用量。"""
    data = b": keepalive\r\n\r\n"
    data += event({"role": "assistant", "reasoning_content": "private reasoning"})
    data += event(
        {
            "tool_calls": [
                {
                    "index": 1,
                    "id": "second",
                    "type": "function",
                    "function": {"name": "tools_", "arguments": '{"module":'},
                },
                {
                    "index": 0,
                    "id": "first",
                    "type": "function",
                    "function": {"name": "preset_", "arguments": '{"query":"黄'},
                },
            ]
        },
        usage={"total_tokens": 20},
    )
    data += event(
        {
            "tool_calls": [
                {"index": 0, "function": {"name": "search", "arguments": '色标题"}'}},
                {"index": 1, "function": {"name": "find", "arguments": '"preset"}'}},
            ]
        }
    )
    data += event(finish="tool_calls")
    data += b'data: {"choices":[],\r\ndata: "usage":{"total_tokens":50,"prompt_tokens":30,"completion_tokens":20}}\r\n\r\n'
    data += b"data: [DONE]\r\n\r\n"
    stream = BytesStream(data, step=1)
    budget = Budget(audit_path=tmp_path / "audit.jsonl")
    result = asyncio.run(
        provider(stream, enforce=True).turn("actor", Conversation(), [], budget)
    )
    assert [call.id for call in result.tool_calls] == ["first", "second"]
    assert result.tool_calls[0].function.name == "preset_search"
    assert json.loads(result.tool_calls[0].function.arguments) == {"query": "黄色标题"}
    assert result.tool_calls[1].function.name == "tools_find"
    assert budget.calls == 1 and budget.tokens == 50 and stream.closed
    audit = budget.audit_path.read_text()
    assert "private reasoning" not in audit and '"streaming": true' in audit


def test_judge_reassembles_json_without_publishing_reasoning():
    """视觉 Judge 使用同一流式传输，仅完整 content JSON 进入结果校验。"""
    data = event({"role": "assistant", "reasoning_content": "not answer"})
    data += event({"content": '{"status":"pa'})
    data += event({"content": 'ss","detail":"可读"}'})
    data += event(finish="stop", usage={"total_tokens": 4}) + b"data: [DONE]\n\n"
    stream, budget = BytesStream(data), Budget()
    result = asyncio.run(
        provider(stream).ask(AnswerReview, "review", "inspect", budget, vision=True)
    )
    assert result.status == "pass" and result.detail == "可读"
    assert budget.tokens == 4 and stream.closed


@pytest.mark.parametrize("ending", [b"", b"data: [DONE]\n\n"])
def test_partial_tool_call_never_returns_before_terminal_completion(ending):
    """只有部分参数或缺少 finish_reason 的流不能执行工具，即使已收到 [DONE]。"""
    data = (
        event(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "partial",
                        "type": "function",
                        "function": {
                            "name": "preset_create",
                            "arguments": '{"tsx_code":',
                        },
                    }
                ],
            }
        )
        + ending
    )
    stream = BytesStream(data)
    with pytest.raises(ModelFailure, match="no partial"):
        asyncio.run(provider(stream).turn("actor", Conversation(), [], Budget()))
    assert stream.closed


@pytest.mark.parametrize("finish", ["length", "content_filter", "stop"])
def test_abnormal_finish_or_missing_done_is_rejected(finish):
    """截断、内容过滤及缺少 [DONE] 均不能将片段误判为已完成回答。"""
    data = event(
        {"role": "assistant", "content": '{"status":"pass","detail":"x"}'}
    ) + event(finish=finish)
    if finish != "stop":
        data += b"data: [DONE]\n\n"
    stream = BytesStream(data)
    with pytest.raises(ModelFailure):
        asyncio.run(provider(stream).ask(AnswerReview, "s", "p", Budget()))
    assert stream.closed


@pytest.mark.parametrize(
    "delta",
    [
        {"role": "tool"},
        {"role": "assistant", "tool_calls": [{"index": 4}]},
        {"role": "assistant", "tool_calls": [{"index": True}]},
    ],
)
def test_invalid_roles_and_tool_indices_are_protocol_failures(delta):
    """服务端流不可伪造 tool 角色或用非法索引绕过单消息工具数量约束。"""
    stream = BytesStream(event(delta) + event(finish="stop") + b"data: [DONE]\n\n")
    with pytest.raises(ModelContractFailure):
        asyncio.run(provider(stream).turn("actor", Conversation(), [], Budget()))
    assert stream.closed


@pytest.mark.parametrize("enforce", [False, True])
def test_missing_stream_usage_obeys_existing_budget_policy(enforce):
    """无 usage 的上游流在关闭预算时记未知，开启预算时拒绝，不伪造 token 用量。"""
    data = (
        event({"role": "assistant", "content": '{"status":"pass","detail":"x"}'})
        + event(finish="stop")
        + b"data: [DONE]\n\n"
    )
    stream, budget = BytesStream(data), Budget()
    call = provider(stream, enforce=enforce).ask(AnswerReview, "s", "p", budget)
    if enforce:
        with pytest.raises(ModelFailure, match="omitted valid token usage"):
            asyncio.run(call)
    else:
        assert asyncio.run(call).status == "pass"
    assert budget.calls == 1 and budget.tokens == 0 and stream.closed


def test_stream_error_sanitized_without_retry():
    """HTTP 200 后的上游流错误仍终止任务，日志不泄露上游原文或密钥。"""
    stream = BytesStream(b'data: {"error":{"message":"private upstream token"}}\n\n')
    budget = Budget()
    with pytest.raises(ModelFailure, match="error during streaming") as error:
        asyncio.run(provider(stream).turn("s", Conversation(), [], budget))
    assert "private upstream token" not in str(error.value)
    assert budget.calls == 1 and stream.closed


def test_cancellation_after_first_delta_closes_stream_without_executing():
    """已经收到首段工具参数后取消，仍关闭传输并丢弃未完成调用，窗口不被污染。"""

    async def scenario():
        """用事件握手代替真实模型延迟，所有等待均有界。"""
        entered = asyncio.Event()

        class WaitingStream(BytesStream):
            """首段返回后保持连接，模拟长时间生成中的可取消 SSE。"""

            async def __aiter__(self):
                """部分响应不足以让 Provider 返回可执行结果。"""
                yield event(
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "pending",
                                "type": "function",
                                "function": {"name": "preset_create", "arguments": "{"},
                            }
                        ],
                    }
                )
                entered.set()
                await asyncio.Event().wait()

        stream, budget, context = WaitingStream(b""), Budget(), Conversation()
        async with asyncio.timeout(2):
            task = asyncio.create_task(provider(stream).turn("s", context, [], budget))
            await entered.wait()
            assert not task.done()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert stream.closed and budget.calls == 1 and context.messages() == []

    asyncio.run(scenario())


def test_stream_output_size_is_bounded():
    """SSE 帧开销允许更大，但累计输出内容仍遵循原 2 MB 上限。"""
    stream = BytesStream(
        event({"role": "assistant", "content": "x" * 2_000_001}), step=65536
    )
    with pytest.raises(ModelFailure, match="size limit"):
        asyncio.run(provider(stream).turn("s", Conversation(), [], Budget()))
    assert stream.closed
