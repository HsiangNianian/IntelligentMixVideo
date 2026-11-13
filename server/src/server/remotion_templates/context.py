"""Replayable model history and task-local Executor windows; completed tool exchanges stay atomic."""

import json
from copy import deepcopy
from typing import Literal

from pydantic import Field, model_validator

from .models import Contract, GenerationJob


class ToolFunction(Contract):
    """Untrusted function name and JSON arguments; only the harness can execute them."""

    name: str = Field(min_length=1, max_length=80)
    arguments: str = Field(max_length=120_000)


class ToolCall(Contract):
    """A provider call identity paired with exactly one host-produced result."""

    id: str = Field(min_length=1, max_length=200)
    type: Literal["function"] = "function"
    function: ToolFunction


class AssistantMessage(Contract):
    """Actor prose is not evidence; tool calls remain pending until the host executes them."""

    role: Literal["assistant"] = "assistant"
    content: str | None = Field(default=None, max_length=120_000)
    tool_calls: list[ToolCall] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def valid_turn(self):
        """Reject empty turns and duplicate call IDs before any tool side effects."""
        if not self.content and not self.tool_calls:
            raise ValueError("assistant must provide content or tool calls")
        if len({call.id for call in self.tool_calls}) != len(self.tool_calls):
            raise ValueError("tool call IDs must be unique")
        return self

    def wire(self) -> dict:
        """Serialize without an empty tool_calls field, for compatible chat endpoints."""
        data = self.model_dump()
        if not self.tool_calls:
            data.pop("tool_calls")
        return data


# The Outer window entry the runtime adds when a free-form task ends, so later turns know what was built and how it ended.
OUTCOME_PREFIX = "Host record (data): "


def outcome_record(job: GenerationJob) -> dict:
    """The Outer window entry recording how a free-form task ended: its reply, questions or failure."""
    outcome = {"status": job.status}
    if job.reply is not None:
        outcome |= job.reply.model_dump(mode="json")
    if job.questions:
        outcome["questions"] = list(job.questions)
    if job.error is not None:
        outcome["error"] = job.error.message
    return {"role": "user", "content": OUTCOME_PREFIX + json.dumps({"task_outcome": outcome}, ensure_ascii=False)}


class Conversation:
    """Keep eight complete exchanges within a UTF-8 budget, with no generated summaries."""

    # Leading groups a request trimmed for the token budget must keep.
    pinned = 0

    def __init__(self, groups: list[list[dict]] | None = None) -> None:
        """Restore complete exchanges for one work; external checkpoints must pass through from_client."""
        self.groups: list[list[dict]] = []
        for group in groups or []:
            self.append(group)

    @classmethod
    def from_client(cls, state: str) -> "Conversation":
        """Read a bounded text-only checkpoint without changing message text, order or tool arguments.

        Client data cannot introduce system roles, multimodal blocks or local image paths. Only complete
        exchanges made by the outer Agent are portable; task-local image windows remain server-owned.
        """
        if len(state.encode()) > 240_000:
            raise ValueError("model context exceeds the byte limit")
        try:
            groups = json.loads(state)
        except (ValueError, RecursionError):
            raise ValueError("invalid model context JSON") from None
        if not isinstance(groups, list) or len(groups) > 8:
            raise ValueError("model context must contain at most eight exchange groups")
        fields = {
            "user": {"role", "content"},
            "assistant": {"role", "content", "tool_calls"},
            "tool": {"role", "content", "tool_call_id"},
        }
        for group in groups:
            if not isinstance(group, list) or not group:
                raise ValueError("model context groups must be nonempty lists")
            for message in group:
                if not isinstance(message, dict) or not isinstance(message.get("role"), str) or message["role"] not in fields:
                    raise ValueError("invalid model context message role")
                role = message["role"]
                if set(message) - fields[role]:
                    raise ValueError("unexpected model context message fields")
                content = message.get("content")
                if not isinstance(content, str) and not (role == "assistant" and content is None):
                    raise ValueError("model context messages must contain text only")
                if role == "tool" and not isinstance(message.get("tool_call_id"), str):
                    raise ValueError("model context tool result requires a call ID")
        restored = cls(groups)
        if restored.groups != groups:
            raise ValueError("model context exceeds the serialized byte limit")
        return restored

    def append(self, group: list[dict]) -> None:
        """Validate call/result pairing, then evict whole old exchanges; never truncate source."""
        pending: set[str] = set()
        seen: set[str] = set()
        for message in group:
            role = message.get("role")
            if role == "tool":
                identifier = message.get("tool_call_id")
                if identifier not in pending:
                    raise ValueError("orphan or duplicate tool result")
                pending.remove(identifier)
            elif role in {"user", "assistant"}:
                if pending:
                    raise ValueError("tool results must precede another message")
                if role == "assistant":
                    actor = AssistantMessage.model_validate(message)
                    identifiers = {call.id for call in actor.tool_calls}
                    if identifiers & seen:
                        raise ValueError("duplicate tool call ID in exchange")
                    pending = identifiers
                    seen |= identifiers
            else:
                raise ValueError("only user, assistant and tool belong in history")
        if pending:
            raise ValueError("incomplete tool exchange")
        if not group:
            return
        if len(json.dumps(group, ensure_ascii=False).encode()) > 240_000:
            raise ValueError("single conversation exchange exceeds context budget")
        self.groups.append(deepcopy(group))
        self._evict()

    def _evict(self) -> None:
        """Keep the newest exchanges that fit the window."""
        while len(self.groups) > 8 or len(self.serialize().encode()) > 240_000:
            self.groups.pop(0)

    def messages(self) -> list[dict]:
        """Return detached wire messages so a provider cannot mutate authoritative history."""
        return deepcopy([message for group in self.groups for message in group])

    def serialize(self) -> str:
        """Persist the bounded window only, never an accumulating transcript."""
        return json.dumps(self.groups, ensure_ascii=False)


def image_message(label: str, paths: list) -> dict:
    """A window message holding local images by path; the provider encodes them when it sends the request, so the window stays small."""
    return {"role": "user", "content": [{"type": "text", "text": label}, *({"type": "image_path", "path": str(path)} for path in paths)]}


class StepWindow(Conversation):
    """The Executor's window: append-only, so every request extends the previous one and the provider's prefix cache keeps
    hitting. It never evicts a group turn by turn, because dropping the oldest group changes everything after it; `AgentRun`
    replaces the whole window once it outgrows its budget, which costs one miss."""

    def _evict(self) -> None:
        """Keep every exchange: only the compaction in `AgentRun` shortens this window."""

    def exceeds(self, size: int, images: int) -> bool:
        """Whether the window has outgrown ``size`` bytes (images by path count little) or ``images`` images."""
        count = sum(item.get("type") == "image_path" for message in self.messages() if isinstance(message.get("content"), list) for item in message["content"])
        return count > images or len(self.serialize().encode()) > size
