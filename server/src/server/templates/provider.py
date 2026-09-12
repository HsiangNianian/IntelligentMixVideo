"""Bounded OpenAI-compatible JSON calls for actor and vision roles, with private credentials."""

import base64
import json
from dataclasses import dataclass
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


class ModelFailure(RuntimeError):
    """A sanitized provider, budget or output-contract failure safe to expose to API clients."""


class ModelContractFailure(ModelFailure):
    """A recoverable model protocol error; the harness may steer another turn within the same budget."""


@dataclass
class Budget:
    """One run shares accounting across analysis, actor generation, repairs and visual review."""

    calls: int = 0
    tokens: int = 0


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
        context: Conversation | None = None,
    ) -> Output:
        """Request JSON and validate locally; omitted usage and truncated output fail closed."""
        content = [{"type": "text", "text": prompt}]
        image_bytes = 0
        for image in images or []:
            data = model_image(image)
            image_bytes += len(data)
            if image_bytes > 24 * 1024 * 1024:
                raise ModelFailure(
                    "Review images exceed the bounded model request size."
                )
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,"
                        + base64.b64encode(data).decode(),
                        "detail": "high",
                    },
                }
            )
        body = {
            "messages": [
                {
                    "role": "system",
                    "content": system
                    + "\nTransparent pixels in supplied images are composited onto neutral #808080 gray solely for inspection. This inspection gray is not template content and must not be recreated. Stored frames retain their original alpha."
                    + "\nReturn one JSON object matching this JSON Schema, without Markdown:\n"
                    + json.dumps(output.model_json_schema(), ensure_ascii=False),
                },
                *(context.messages() if context else []),
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
        if context is not None:
            # Image bytes are supplied for this observation only; task snapshots retain asset identity.
            context.append(
                [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": result.model_dump_json()},
                ]
            )
        return result

    async def turn(
        self, system: str, context: Conversation, tools: list[dict], budget: Budget
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
        if (
            budget.calls >= settings.max_model_calls
            or budget.tokens >= settings.max_tokens
        ):
            raise ModelFailure("Model call or token budget exhausted.")
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
            max_tokens=min(16000, settings.max_tokens - budget.tokens),
        )
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
            usage = payload.get("usage", {}).get("total_tokens")
            if not isinstance(usage, int) or isinstance(usage, bool) or usage < 0:
                raise ModelFailure(
                    "Model endpoint omitted valid token usage; budget cannot be verified."
                )
            budget.tokens += usage
            if budget.tokens > settings.max_tokens:
                raise ModelFailure("Model token budget exhausted.")
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
