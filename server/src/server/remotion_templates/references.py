"""Select local reference IDs deterministically and materialize only the current task's private PNG copies."""

import base64
import binascii
import json
from pathlib import Path
from uuid import UUID

from .media import normalize_image
from .models import ImagePayload, JobInput
from .settings import Settings

MAX_REFERENCE_IMAGES = 8
MAX_IMAGE_BYTES = 30 * 1024 * 1024
REFERENCE_MEANINGS = {
    "work_original": "Attached to the request that created this work; it describes the original goal.",
    "message": "Attached by the user to the current instruction as a reference for this change.",
    "clarification_answer": "Attached by the user as their answer (or part of it) to clarifications[clarification].",
    "earlier_message": "Attached by the user to an earlier instruction of this work; it shows what they meant then, not a new requirement.",
}


def earlier_images(context, references: list[dict]) -> list[dict]:
    """Append recent references once, preserving IDs and order across local save/reload and retry."""
    seen = {entry["asset_id"] for entry in references}
    found = []
    for message in reversed(context.messages()):
        if message.get("role") != "user" or not isinstance(message.get("content"), str):
            continue
        try:
            request = json.loads(message["content"])
        except ValueError:
            continue
        entries = request.get("reference_images", []) if isinstance(request, dict) else []
        if not isinstance(entries, list):
            raise ValueError("invalid context reference images")
        for entry in entries:
            identifier = entry.get("asset_id") if isinstance(entry, dict) else None
            if identifier and identifier not in seen and entry.get("source") in {"message", "clarification_answer"}:
                if not isinstance(identifier, str):
                    raise ValueError("invalid context reference image ID")
                UUID(identifier)
                seen.add(identifier)
                found.append({"asset_id": identifier, "source": "earlier_message", "meaning": REFERENCE_MEANINGS["earlier_message"]})
    return found[:max(0, MAX_REFERENCE_IMAGES - len(references))]


def reference_images(project, inputs: JobInput) -> list[dict]:
    """Original goal first, then this task's attachments; duplicate IDs are sent only once."""
    request = inputs.request or project.request
    entries = [{"asset_id": str(image.asset_id), "source": "work_original"} for image in request.images]
    entries += [image.model_dump(mode="json", exclude_none=True) for image in inputs.images]
    seen = set()
    result = []
    for entry in entries:
        if entry["asset_id"] not in seen:
            seen.add(entry["asset_id"])
            result.append({**entry, "meaning": REFERENCE_MEANINGS[entry["source"]]})
    original_count = len({str(image.asset_id) for image in request.images})
    return result[:original_count] + result[original_count:][-(MAX_REFERENCE_IMAGES - original_count):]


def stage_images(directory: Path, identifiers: list[UUID], payloads: list[ImagePayload], legacy, settings: Settings) -> None:
    """Validate all supplied bytes before enqueue; only bounded private files survive until task teardown.

    The legacy reader checks device ownership. It is used only while migrating old cloud references; new images
    must arrive from the client's durable local store. No URL, filesystem path or storage key is accepted.
    """
    supplied = {item.id: item.data for item in payloads}
    if len(supplied) != len(payloads):
        raise ValueError("duplicate reference image payload")
    size = 0
    for identifier in dict.fromkeys(identifiers):
        if identifier in supplied:
            try:
                raw = base64.b64decode(supplied[identifier], validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("invalid reference image encoding") from exc
            # POST /assets normalizes once before SQLite storage; repeated PNG encoding is deterministic.
            content = normalize_image(raw, settings.model_copy(update={"max_upload_bytes": MAX_IMAGE_BYTES}))
        else:
            content = legacy(identifier)
        size += len(content)
        if size > MAX_IMAGE_BYTES:
            raise ValueError("reference images exceed the 30 MiB model request budget")
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{identifier}.png").write_bytes(content)
