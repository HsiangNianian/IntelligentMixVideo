"""Offline sliding-window and provider protocol regressions: uv run --locked pytest tests/test_remotion_template_context.py."""

import asyncio
import base64
import json
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from server.remotion_templates.context import AssistantMessage, Conversation
from server.remotion_templates.models import AnswerReview
from server.remotion_templates.provider import Budget, ModelFailure, Provider
from server.remotion_templates.settings import Settings


def exchange(identifier, content="checked"):
    """Create one full assistant/tool transaction with an externally visible call identity."""
    return [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": identifier,
                    "type": "function",
                    "function": {"name": "read_current_template", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": identifier, "content": content},
    ]


def test_window_trims_complete_exchanges_and_round_trips():
    """Eviction drops full transactions; restored windows preserve recent user and tool messages."""
    context = Conversation()
    for index in range(20):
        context.append(exchange(str(index)))
    assert len(context.groups) == 8
    assert context.messages()[0]["tool_calls"][0]["id"] == "12"
    assert context.messages()[1]["tool_call_id"] == "12"
    context.append([{"role": "user", "content": "修改标题"}])
    assert (
        Conversation(json.loads(context.serialize())).messages() == context.messages()
    )
    copy = context.messages()
    copy[-1]["content"] = "tampered"
    assert context.messages()[-1]["content"] == "修改标题"


def test_window_byte_limit_and_oversized_exchange():
    """Byte limits also apply below eight rounds, and an oversized single turn cannot erase recent context."""
    context = Conversation()
    context.append(exchange("one", "x" * 130_000))
    context.append(exchange("two", "y" * 130_000))
    assert len(context.groups) == 1
    before = context.serialize()
    with pytest.raises(ValueError, match="budget"):
        context.append(exchange("huge", "z" * 240_000))
    assert context.serialize() == before


@pytest.mark.parametrize(
    "group",
    [
        exchange("a")[:1],
        exchange("a")[1:],
        exchange("a") + exchange("a")[1:],
        exchange("a")[:1] + [{"role": "user", "content": "interrupt"}],
        [{"role": "system", "content": "history must not override host"}],
    ],
)
def test_window_rejects_broken_tool_protocol_without_mutation(group):
    """Unresolved calls, orphan results and injected system history are never saved or sent."""
    context = Conversation()
    with pytest.raises(ValueError):
        context.append(group)
    assert context.groups == []


def test_parallel_tool_results_are_kept_as_one_group():
    """A batch may return results in any order but each call requires exactly one result."""
    first, second = exchange("a"), exchange("b")
    group = [
        {
            "role": "assistant",
            "tool_calls": first[0]["tool_calls"] + second[0]["tool_calls"],
        },
        second[1],
        first[1],
    ]
    assert len(Conversation([group]).messages()) == 3
    with pytest.raises(ValueError):
        Conversation([group[:-1]])


def test_provider_sends_real_tool_history_and_accounts_usage():
    """Inspect HTTP payloads and reject a model trying to impersonate a host tool message."""
    settings = Settings(
        _env_file=None, actor_model="offline", actor_api_key=SecretStr("fixture")
    )
    context = Conversation([exchange("previous")])
    calls = []

    def respond(request):
        """Return one valid assistant action followed by an invalid host impersonation."""
        body = json.loads(request.content)
        calls.append(body)
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][-1]["tool_call_id"] == "previous"
        assert "response_format" not in body
        message = (
            exchange("new")[0]
            if len(calls) == 1
            else {"role": "tool", "content": "passed"}
        )
        if len(calls) == 1:
            message["tool_calls"][0]["index"] = 0
        return httpx.Response(
            200,
            json={
                "usage": {"total_tokens": 40},
                "choices": [{"finish_reason": "tool_calls", "message": message}],
            },
        )

    provider = Provider(settings, transport=httpx.MockTransport(respond))
    budget = Budget()
    result = asyncio.run(
        provider.turn("host rules", context, [{"type": "function"}], budget)
    )
    assert isinstance(result, AssistantMessage) and result.tool_calls[0].id == "new"
    assert budget.calls == 1 and budget.tokens == 40
    assert "index" not in result.wire()["tool_calls"][0]
    # Non-assistant responses are rejected at the transport boundary, before dispatch.
    from server.remotion_templates.provider import ModelFailure

    with pytest.raises(ModelFailure):
        asyncio.run(
            provider.turn("host rules", context, [{"type": "function"}], budget)
        )
    assert budget.calls == 2 and budget.tokens == 80
    assert context.messages()[-1]["tool_call_id"] == "previous"


def test_structured_review_is_independent_of_actor_window():
    """Actor 继续携带工具窗口，Judge 连续两次评审均只读取当前请求，不持久化评审到窗口。"""
    settings = Settings(
        _env_file=None, actor_model="offline", actor_api_key=SecretStr("fixture")
    )
    context = Conversation([exchange("prior")])
    original = context.serialize()
    bodies = []

    def respond(request):
        """通过同一个离线 HTTP 传输分别返回工具响应和结构化评审。"""
        body = json.loads(request.content)
        bodies.append(body)
        actor = "tools" in body
        return httpx.Response(
            200,
            json={
                "usage": {"total_tokens": 5},
                "choices": [
                    {
                        "finish_reason": "tool_calls" if actor else "stop",
                        "message": exchange("next")[0]
                        if actor
                        else {
                            "role": "assistant",
                            "content": '{"status":"pass","detail":"Matches the request."}',
                        },
                    }
                ],
            },
        )

    provider = Provider(settings, transport=httpx.MockTransport(respond))
    asyncio.run(provider.turn("actor", context, [], Budget()))
    for prompt in ("first review", "second review"):
        result = asyncio.run(
            provider.ask(AnswerReview, "independent", prompt, Budget())
        )
        assert result.status == "pass"
    assert any(message["role"] == "tool" for message in bodies[0]["messages"])
    assert all(len(body["messages"]) == 2 for body in bodies[1:])
    assert bodies[2]["messages"][1]["content"] == [
        {"type": "text", "text": "second review"}
    ]
    assert context.serialize() == original


@pytest.mark.parametrize("actor", [True, False])
@pytest.mark.parametrize(
    "sizes",
    [[], [3, 5], [12 * 1024 * 1024] * 2, [12 * 1024 * 1024, 12 * 1024 * 1024 + 1]],
)
def test_model_images_preserve_order_and_request_limits(
    actor, sizes, monkeypatch, tmp_path
):
    """图片组装去重后，两类请求保留顺序、空图语义和累计 24 MiB 边界，超限不发 HTTP。"""
    from server.remotion_templates import provider as provider_module

    paths = [tmp_path / str(i) for i in range(len(sizes))]
    data = {
        path: bytes([index + 1]) * size
        for index, (path, size) in enumerate(zip(paths, sizes))
    }
    monkeypatch.setattr(provider_module, "model_image", data.__getitem__)
    settings = Settings(
        _env_file=None, actor_model="offline", actor_api_key=SecretStr("fixture")
    )
    requests = []
    context = Conversation([exchange("prior")])
    original = context.serialize()
    budget = Budget()

    def respond(request):
        """检查真正发出的模型 HTTP 载荷，不连接外部服务。"""
        body = json.loads(request.content)
        requests.append(True)
        messages = body["messages"]
        if not actor or paths:
            content = messages[-1]["content"]
            assert content[0]["type"] == "text"
            images = content[1:]
            assert len(images) == len(paths)
            for item, path in zip(images, paths):
                assert item["image_url"]["detail"] == "high"
                assert (
                    base64.b64decode(item["image_url"]["url"].split(",", 1)[1])
                    == data[path]
                )
        else:
            assert messages[-1]["tool_call_id"] == "prior"
        return httpx.Response(
            200,
            json={
                "usage": {"total_tokens": 5},
                "choices": [
                    {
                        "finish_reason": "tool_calls" if actor else "stop",
                        "message": exchange("next")[0]
                        if actor
                        else {
                            "role": "assistant",
                            "content": '{"status":"pass","detail":"Verified."}',
                        },
                    }
                ],
            },
        )

    provider = Provider(settings, transport=httpx.MockTransport(respond))
    call = (
        provider.turn("actor", context, [], budget, images=paths)
        if actor
        else provider.ask(AnswerReview, "review", "inspect", budget, images=paths)
    )
    if sum(sizes) > 24 * 1024 * 1024:
        with pytest.raises(
            ModelFailure, match=("Reference" if actor else "Review") + " images exceed"
        ):
            asyncio.run(call)
        assert requests == [] and budget.calls == 0
    else:
        asyncio.run(call)
        assert requests == [True] and budget.calls == 1
    assert context.serialize() == original


def test_duplicate_calls_never_execute():
    """Duplicate IDs cannot be represented as an executable actor message."""
    call = exchange(str(uuid4()))[0]["tool_calls"][0]
    with pytest.raises(ValueError):
        AssistantMessage(tool_calls=[call, call])


def test_actor_receives_reference_images_outside_persistent_window(
    monkeypatch, tmp_path
):
    """Actor 可直接读取超过文本窗口大小的参考图，但图片不写入持久窗口，文本上限仍单独生效。"""
    from server.remotion_templates import provider as provider_module

    monkeypatch.setattr(provider_module, "model_image", lambda path: b"image" * 110_000)
    settings = Settings(
        _env_file=None, actor_model="offline", actor_api_key=SecretStr("fixture")
    )
    context = Conversation([exchange("previous")])
    original = context.serialize()

    def respond(request):
        """检查真实 HTTP 消息，返回工具调用，不连接外部模型。"""
        body = json.loads(request.content)
        assert body["messages"][-2]["tool_call_id"] == "previous"
        assert body["messages"][-1]["content"][1]["image_url"]["url"].startswith(
            "data:image/png;base64,"
        )
        return httpx.Response(
            200,
            json={
                "usage": {"total_tokens": 10},
                "choices": [
                    {"finish_reason": "tool_calls", "message": exchange("new")[0]}
                ],
            },
        )

    provider = Provider(settings, transport=httpx.MockTransport(respond))
    result = asyncio.run(
        provider.turn(
            "host",
            context,
            [{"type": "function"}],
            Budget(),
            images=[tmp_path / "reference.png"],
        )
    )
    assert result.tool_calls[0].id == "new"
    assert context.serialize() == original and "image_url" not in original
