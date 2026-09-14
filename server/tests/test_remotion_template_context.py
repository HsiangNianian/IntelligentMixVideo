"""Offline sliding-window and provider protocol regressions: uv run --locked pytest tests/test_remotion_template_context.py."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from server.remotion_templates.context import AssistantMessage, Conversation
from server.remotion_templates.models import DialogueOutput
from server.remotion_templates.provider import Budget, Provider
from server.settings import Settings


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


def test_structured_planning_reuses_window_but_reviewer_does_not():
    """Planning appends recent user/assistant turns, while independent visual review has no actor history."""
    settings = Settings(
        _env_file=None, actor_model="offline", actor_api_key=SecretStr("fixture")
    )
    context = Conversation([exchange("prior")])
    bodies = []

    def respond(request):
        """Return a well-formed clarification using the typed provider API."""
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "usage": {"total_tokens": 5},
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": '{"questions":["文字是什么？"]}',
                        },
                    }
                ],
            },
        )

    provider = Provider(settings, transport=httpx.MockTransport(respond))
    asyncio.run(
        provider.ask(DialogueOutput, "plan", "title", Budget(), context=context)
    )
    asyncio.run(provider.ask(DialogueOutput, "independent", "inspect", Budget()))
    assert any(message["role"] == "tool" for message in bodies[0]["messages"])
    assert len(bodies[1]["messages"]) == 2
    assert context.messages()[-2] == {"role": "user", "content": "title"}
    assert context.messages()[-1]["role"] == "assistant"


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
