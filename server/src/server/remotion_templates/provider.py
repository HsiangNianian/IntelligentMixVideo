"""Bounded OpenAI-compatible JSON calls for actor and vision roles, with private credentials."""

import base64
import json
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import TypeVar

import httpx
from PIL import Image
from pydantic import BaseModel, ValidationError

from ..settings import Settings
from .context import AssistantMessage, Conversation

Output = TypeVar("Output", bound=BaseModel)


def model_image(path: Path) -> bytes:
    """Composite alpha onto neutral gray for model visibility; leave stored source/preview bytes intact."""
    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (128, 128, 128, 255))
        background.alpha_composite(rgba)
        buffer = BytesIO()
        background.convert("RGB").save(buffer, format="PNG")
        return buffer.getvalue()


def _image_content(paths: list[Path], *, label: str) -> list[dict]:
    """Encode ephemeral model images in order, enforcing the shared 24 MiB raw-image limit."""
    content = []
    size = 0
    for path in paths:
        data = model_image(path)
        size += len(data)
        if size > 24 * 1024 * 1024:
            raise ModelFailure(f"{label} images exceed the bounded model request size.")
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64," + base64.b64encode(data).decode(),
                    "detail": "high",
                },
            }
        )
    return content


class ModelFailure(RuntimeError):
    """A sanitized provider, budget or output-contract failure safe to expose to API clients."""


class ModelContractFailure(ModelFailure):
    """A recoverable model protocol error; the harness may steer another turn within the same budget."""


class ExecutionFailure(ModelFailure):
    """A domain recovery boundary stopped execution with a specific, public-safe reason."""

    def __init__(self, code: str, message: str):
        """Keep recovery classification separate from provider transport errors."""
        super().__init__(message)
        self.code = code


def request_cost(body: dict) -> tuple[int, int, int]:
    """Estimate input with UTF-8 bytes/2 and 2048 tokens per image; provider-independent heuristic, not billing."""
    images = 0

    def without_images(value):
        """Count image blocks without treating their base64 transport bytes as text tokens."""
        nonlocal images
        if isinstance(value, dict):
            if value.get("type") == "image_url":
                images += 1
                return {"type": "image_url"}
            return {key: without_images(item) for key, item in value.items()}
        if isinstance(value, list):
            return [without_images(item) for item in value]
        return value

    size = len(json.dumps(without_images(body), ensure_ascii=False).encode())
    return (size + 1) // 2 + images * 2048, images, size


@dataclass
class Budget:
    """One run shares accounting across analysis, actor generation, repairs and visual review."""

    calls: int = 0
    tokens: int = 0
    phases: dict[str, dict[str, int]] = field(default_factory=dict)
    audit_path: Path | None = None
    on_progress: Callable[[str], None] | None = None
    active_phase: str | None = None
    _start: tuple[int, int] = (0, 0)

    def progress(self, phase: str) -> None:
        """Send host-selected phase codes to this run's persistence boundary, never model prose."""
        if self.on_progress is not None:
            self.on_progress(phase)

    def record(self, event: str, **data) -> None:
        """Append private execution facts independently of the bounded model conversation."""
        if self.audit_path is not None:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {"time": datetime.now(UTC).isoformat(), "event": event, **data},
                        ensure_ascii=False,
                    )
                    + "\n"
                )

    def summary(self) -> dict[str, int]:
        """Expose flat integer counters compatible with the stored job usage contract."""
        result = {"calls": self.calls, "tokens": self.tokens}
        for name, usage in self.phases.items():
            result.update({f"{name}_{key}": value for key, value in usage.items()})
        return result

    @contextmanager
    def phase(self, name: str):
        """Account one model invocation, including malformed outputs, failures and cancellation."""
        if self.active_phase is not None:
            raise RuntimeError("Model accounting phases cannot overlap")
        self.active_phase, self._start = name, (self.calls, self.tokens)
        outcome = "returned"
        try:
            yield
        except BaseException as exc:
            outcome = type(exc).__name__
            raise
        finally:
            usage = self.phases.setdefault(name, {"calls": 0, "tokens": 0})
            calls, tokens = self.calls - self._start[0], self.tokens - self._start[1]
            usage["calls"] += calls
            usage["tokens"] += tokens
            self.active_phase = None
            self.record(
                "model_call",
                phase=name,
                calls=calls,
                tokens=tokens,
                outcome=outcome,
                usage=self.summary(),
            )

    def remaining(self, settings: Settings) -> int | None:
        """Return no quota when enforcement is disabled; otherwise check global and role limits."""
        if not settings.enforce_model_budget:
            return None
        if self.calls >= settings.max_model_calls or self.tokens >= settings.max_tokens:
            raise ModelFailure("Model call or token budget exhausted.")
        remaining = settings.max_tokens - self.tokens
        if self.active_phase:
            name = self.active_phase
            usage = self.phases.get(name, {"calls": 0, "tokens": 0})
            calls = usage["calls"] + self.calls - self._start[0]
            tokens = usage["tokens"] + self.tokens - self._start[1]
            if calls >= getattr(settings, f"max_{name}_calls") or tokens >= getattr(
                settings, f"max_{name}_tokens"
            ):
                raise ExecutionFailure(
                    "phase_budget_exhausted", f"{name} model budget exhausted."
                )
            remaining = min(remaining, getattr(settings, f"max_{name}_tokens") - tokens)
        return remaining


class Provider:
    """Cancellation closes the active HTTP request; no hidden retry extends the runtime budget."""

    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        """Allow tests to inject an offline transport, never credentials supplied by API users."""
        self.settings = settings
        self.transport = transport

    async def ask(
        self,
        output: type[Output],
        system: str,
        prompt: str,
        budget: Budget,
        *,
        images: list[Path] | None = None,
        vision: bool = False,
    ) -> Output:
        """Review one independent JSON request without reading or mutating actor history."""
        content = [
            {"type": "text", "text": prompt},
            *_image_content(images or [], label="Review"),
        ]
        body = {
            "messages": [
                {
                    "role": "system",
                    "content": system
                    + "\nTransparent pixels in supplied images are composited onto neutral #808080 gray solely for inspection. This inspection gray is not template content and must not be recreated. Stored frames retain their original alpha."
                    + "\nReturn one JSON object matching this JSON Schema, without Markdown:\n"
                    + json.dumps(output.model_json_schema(), ensure_ascii=False),
                },
                {"role": "user", "content": content},
            ],
            "response_format": {"type": "json_object"},
        }
        message = await self._request(body, budget, vision=vision)
        try:
            result = output.model_validate_json(message["content"])
        except (ValidationError, KeyError, TypeError) as exc:
            raise ModelContractFailure(
                "Model response violated the requested output contract."
            ) from exc
        return result

    async def turn(
        self,
        system: str,
        context: Conversation,
        tools: list[dict],
        budget: Budget,
        *,
        images: list[Path] | None = None,
    ) -> AssistantMessage:
        """Request real function calls with bounded recent conversation; never accept model tool results."""
        body = {
            "messages": [{"role": "system", "content": system}, *context.messages()],
            "tools": tools,
            "tool_choice": "auto",
        }
        if len(json.dumps(body, ensure_ascii=False).encode()) > 512_000:
            raise ModelFailure(
                "Actor task snapshot and window exceed the context budget."
            )
        if images:
            content = [
                {
                    "type": "text",
                    "text": "Images follow the host snapshot mapping: original user references first, then current candidate frames. Candidate frames are observations, not new user requirements. Interpret typography only, not application chrome or scenery.",
                }
            ]
            content.extend(_image_content(images, label="Reference"))
            body["messages"].append({"role": "user", "content": content})
        # Trim whole old exchanges only in this request, preserving the latest exchange and image mapping.
        groups = context.groups
        dropped = 0
        reserve = min(512, self.settings.max_output_tokens)
        remaining = budget.remaining(self.settings)
        while (
            remaining is not None
            and request_cost(body)[0] + reserve > remaining
            and dropped < len(groups) - 1
        ):
            del body["messages"][1 : 1 + len(groups[dropped])]
            dropped += 1
        if dropped:
            budget.record(
                "context_trimmed", phase=budget.active_phase, dropped_groups=dropped
            )
        message = await self._request(body, budget, tools=True)
        try:
            # DeepSeek includes a streaming-style index even in non-streaming tool responses.
            # It is transport metadata, not an action argument or host observation.
            normalized = {
                key: message[key]
                for key in ("role", "content", "tool_calls")
                if key in message and not (key == "tool_calls" and message[key] is None)
            }
            if isinstance(normalized.get("tool_calls"), list):
                normalized["tool_calls"] = [
                    {key: value for key, value in call.items() if key != "index"}
                    if isinstance(call, dict)
                    else call
                    for call in normalized["tool_calls"]
                ]
            return AssistantMessage.model_validate(normalized)
        except ValidationError as exc:
            raise ModelContractFailure(
                "Actor returned an invalid tool-call contract."
            ) from exc

    async def _request(
        self, body: dict, budget: Budget, *, vision: bool = False, tools: bool = False
    ) -> dict:
        """Share transport limits and token accounting across structured review and actor turns."""
        settings = self.settings
        remaining = budget.remaining(settings)
        base = (
            (settings.vision_base_url or settings.actor_base_url)
            if vision
            else settings.actor_base_url
        )
        model = settings.vision_model if vision else settings.actor_model
        key = (
            (settings.vision_api_key or settings.actor_api_key)
            if vision
            else settings.actor_api_key
        )
        if not model or not key.get_secret_value():
            raise ModelFailure(
                "Configure server actor and vision model credentials before submitting work."
            )
        body = dict(
            body,
            model=model,
        )
        estimated, image_count, text_bytes = request_cost(body)
        output_limit = (
            settings.max_output_tokens
            if remaining is None
            else min(settings.max_output_tokens, remaining - estimated)
        )
        budget.record(
            "model_request",
            phase=budget.active_phase,
            estimated_input_tokens=estimated,
            image_count=image_count,
            text_bytes=text_bytes,
            remaining_tokens=remaining,
            max_output_tokens=max(0, output_limit),
        )
        if remaining is not None and output_limit < min(
            512, settings.max_output_tokens
        ):
            raise ExecutionFailure(
                "input_budget_exhausted",
                "Insufficient model budget for estimated input and a useful response; no request sent.",
            )
        body["max_tokens"] = output_limit
        if len(json.dumps(body, ensure_ascii=False).encode()) > 36_000_000:
            raise ModelFailure("Model request exceeds the context size limit.")
        if settings.disable_thinking:
            body["thinking"] = {"type": "disabled"}
        budget.calls += 1
        try:
            async with httpx.AsyncClient(
                timeout=settings.model_timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    "POST",
                    base.rstrip("/") + "/chat/completions",
                    headers={"Authorization": "Bearer " + key.get_secret_value()},
                    json=body,
                ) as response:
                    if response.status_code != 200:
                        raise ModelFailure(
                            f"Model endpoint returned HTTP {response.status_code}; check server model configuration or retry later."
                        )
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 2_000_000:
                            raise ModelFailure(
                                "Model response exceeded the size limit."
                            )
            payload = json.loads(data)
            token_detail = payload.get("usage")
            token_detail = token_detail if isinstance(token_detail, dict) else {}
            usage = token_detail.get("total_tokens")
            if not isinstance(usage, int) or isinstance(usage, bool) or usage < 0:
                if settings.enforce_model_budget:
                    raise ModelFailure(
                        "Model endpoint omitted valid token usage; budget cannot be verified."
                    )
                usage = None
            if usage is not None:
                budget.tokens += usage

            def valid_usage(name):
                """Optional provider breakdown improves diagnostics without inventing missing input/output counts."""
                value = token_detail.get(name)
                return value if type(value) is int and value >= 0 else None

            budget.record(
                "model_usage",
                phase=budget.active_phase,
                total_tokens=usage,
                input_tokens=valid_usage("prompt_tokens"),
                output_tokens=valid_usage("completion_tokens"),
                estimated_input_tokens=estimated,
                image_count=image_count,
            )
            if settings.enforce_model_budget and budget.tokens > settings.max_tokens:
                raise ModelFailure("Model token budget exhausted.")
            if settings.enforce_model_budget and budget.active_phase:
                name = budget.active_phase
                used = (
                    budget.phases.get(name, {"tokens": 0})["tokens"]
                    + budget.tokens
                    - budget._start[1]
                )
                if used > getattr(settings, f"max_{name}_tokens"):
                    raise ExecutionFailure(
                        "phase_budget_exhausted",
                        f"{name} model token budget exhausted.",
                    )
            choice = payload["choices"][0]
            if choice.get("finish_reason") not in (
                {"stop", "tool_calls"} if tools else {"stop"}
            ):
                raise ModelFailure(
                    "Model output did not finish normally; no partial artifact was accepted."
                )
            message = choice["message"]
            if message.get("role") != "assistant":
                raise ModelFailure("Model returned a non-assistant message.")
            return message
        except httpx.TimeoutException as exc:
            raise ModelFailure("Model request timed out.") from exc
        except httpx.HTTPError as exc:
            raise ModelFailure("Model endpoint is unreachable.") from exc
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelContractFailure(
                "Model returned invalid JSON or violated the requested output contract."
            ) from exc
