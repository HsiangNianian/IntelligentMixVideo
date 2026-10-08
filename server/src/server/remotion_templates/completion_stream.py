"""Assemble bounded Chat Completions SSE deltas; incomplete streams never become executable tool calls."""

import json
from collections.abc import Callable

import httpx


class StreamFailure(RuntimeError):
    """A transport-level stream failure stops the task without exposing partial model output."""


class Completion:
    """Collect one choice and indexed tool fragments while retaining only the latest usage receipt."""

    def __init__(self):
        """No generated text or tool arguments are emitted before a terminal finish reason."""
        self.content = []
        self.calls = {}
        self.role = None
        self.finish = None
        self.usage = None
        self.output_bytes = 0

    def fragment(self, value):
        """Validate text deltas and bound accumulated output independently of SSE framing overhead."""
        if not isinstance(value, str):
            raise ValueError("Stream text fragments must be strings")
        self.output_bytes += len(value.encode("utf-8"))
        if self.output_bytes > 2_000_000:
            raise StreamFailure("Model response exceeded the size limit.")
        return value

    def accept(self, payload):
        """Merge a data event; reasoning stays private and cannot masquerade as response content."""
        if not isinstance(payload, dict):
            raise ValueError("Expected a completion event object")
        if payload.get("error") is not None:
            raise StreamFailure("Model endpoint reported an error during streaming.")
        if payload.get("usage") is not None:
            if not isinstance(payload["usage"], dict):
                raise ValueError("Invalid stream usage")
            self.usage = payload["usage"]
        choices = payload.get("choices", [])
        if not isinstance(choices, list) or len(choices) > 1:
            raise ValueError("Expected exactly one completion choice")
        for choice in choices:
            if (
                not isinstance(choice, dict)
                or type(choice.get("index")) is not int
                or choice["index"] != 0
            ):
                raise ValueError("Unexpected stream choice index")
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                raise ValueError("Missing stream delta")
            finish = choice.get("finish_reason")
            if self.finish is not None and (delta or finish is not None):
                raise ValueError("Completion changed after its finish reason")
            if delta.get("role") is not None:
                if delta["role"] != "assistant":
                    raise ValueError("Non-assistant stream role")
                self.role = "assistant"
            if delta.get("content") is not None:
                self.content.append(self.fragment(delta["content"]))
            calls = delta.get("tool_calls") or []
            if not isinstance(calls, list):
                raise ValueError("Invalid tool delta list")
            for call in calls:
                if not isinstance(call, dict):
                    raise ValueError("Invalid tool delta")
                index = call.get("index")
                if type(index) is not int or not 0 <= index < 4:
                    raise ValueError("Invalid stream tool index")
                target = self.calls.setdefault(
                    index,
                    {
                        "id": None,
                        "type": None,
                        "function": {"name": [], "arguments": []},
                    },
                )
                for field in ("id", "type"):
                    if call.get(field) is not None:
                        value = self.fragment(call[field])
                        if target[field] is not None and target[field] != value:
                            raise ValueError("Tool identity changed during streaming")
                        target[field] = value
                function = call.get("function") or {}
                if not isinstance(function, dict):
                    raise ValueError("Invalid tool function delta")
                for field in ("name", "arguments"):
                    if function.get(field) is not None:
                        target["function"][field].append(self.fragment(function[field]))
            if finish is not None:
                if not isinstance(finish, str):
                    raise ValueError("Invalid stream finish reason")
                self.finish = finish

    def payload(self):
        """Return a complete message only after [DONE]; caller still validates finish reason and schemas."""
        if self.finish is None:
            raise StreamFailure(
                "Model stream ended without a finish reason; no partial output was accepted."
            )
        if self.role != "assistant":
            raise ValueError("Stream is missing the assistant role")
        message = {"role": self.role, "content": "".join(self.content) or None}
        if self.calls:
            if sorted(self.calls) != list(range(len(self.calls))):
                raise ValueError("Stream tool indices are incomplete")
            message["tool_calls"] = [
                {
                    "id": call["id"],
                    "type": call["type"],
                    "function": {
                        key: "".join(parts) for key, parts in call["function"].items()
                    },
                }
                for _, call in sorted(self.calls.items())
            ]
        return {
            "choices": [{"message": message, "finish_reason": self.finish}],
            "usage": self.usage,
        }


async def read_completion(response: httpx.Response, record: Callable) -> dict:
    """Consume UTF-8 SSE across arbitrary byte boundaries; retain JSON compatibility without a second request."""
    streaming = (
        response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        == "text/event-stream"
    )
    record("model_response_transport", streaming=streaming)
    limit = 16_000_000 if streaming else 2_000_000
    total = events = 0
    buffer = bytearray()
    data = []
    completion = Completion()
    async for chunk in response.aiter_bytes():
        if total == 0 and chunk:
            record("model_response_first_bytes", streaming=streaming)
        total += len(chunk)
        if total > limit:
            raise StreamFailure("Model response exceeded the size limit.")
        buffer.extend(chunk)
        if not streaming:
            continue
        while b"\n" in buffer:
            raw, _, rest = buffer.partition(b"\n")
            buffer = bytearray(rest)
            line = raw.rstrip(b"\r").decode("utf-8")
            if line.startswith("data:"):
                data.append(line[5:].removeprefix(" "))
            elif not line and data:
                event = "\n".join(data)
                data.clear()
                if event.strip() == "[DONE]":
                    result = completion.payload()
                    record(
                        "model_response_complete",
                        streaming=True,
                        events=events,
                        bytes=total,
                    )
                    return result
                completion.accept(json.loads(event))
                events += 1
    if streaming:
        raise StreamFailure(
            "Model stream disconnected before [DONE]; no partial output was accepted."
        )
    record("model_response_complete", streaming=False, bytes=total)
    return json.loads(buffer)
