"""Deterministic single-file Sprite source composition for PR76."""
from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

from .registry import ToolFault


def _namespace_schema(schema: dict[str, Any], prefix: str, defs: dict[str, Any]) -> dict[str, Any]:
    """Copy a child schema and namespace local ``$defs``/``definitions`` references."""
    result = deepcopy(schema)
    local_defs = result.pop("$defs", {}) or {}
    local_defs.update(result.pop("definitions", {}) or {})
    mapping = {name: f"{prefix}_{name}" for name in local_defs}

    def rewrite(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, str):
            for source, target in mapping.items():
                value = value.replace(f"#/$defs/{source}", f"#/$defs/{target}")
                value = value.replace(f"#/definitions/{source}", f"#/$defs/{target}")
            return value
        return value

    result = rewrite(result)
    for name, definition in local_defs.items():
        defs[mapping[name]] = rewrite(definition)
    return result


def compose_source(instances: list[dict[str, Any]]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Generate Sprite TSX, nested schema and complete default props."""
    defs: dict[str, Any] = {}
    source_instances: list[dict[str, Any]] = []
    defaults: dict[str, Any] = {}
    properties: dict[str, Any] = {}
    for index, item in enumerate(instances):
        instance_id = item["instance_id"]
        preset = item["preset"]
        params = item["parameters"]
        defaults[instance_id] = params
        properties[instance_id] = _namespace_schema(preset["parameter_schema"], f"imv_{index}", defs)
        layout = item["layout"]
        timing = item["timing"]
        source_instances.append({"instance_id": instance_id, "code": preset["code"], "layout": layout, "timing": timing})
    schema = {"type": "object", "properties": properties, "$defs": defs, "additionalProperties": False}
    payload = {"instances": source_instances, "defaults": defaults}
    script = Path(__file__).resolve().parents[2] / "remotion" / "compose.mjs"
    try:
        process = subprocess.run(
            ["node", str(script)],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolFault("COMPOSITION_FAILED", f"Sprite bundler unavailable: {exc}") from exc
    if process.returncode != 0:
        raise ToolFault("COMPOSITION_FAILED", process.stderr[-2000:] or "Sprite bundling failed")
    try:
        code = json.loads(process.stdout).get("code", "")
    except json.JSONDecodeError as exc:
        raise ToolFault("COMPOSITION_FAILED", "Sprite bundler returned invalid JSON") from exc
    if not code:
        raise ToolFault("COMPOSITION_FAILED", "Sprite bundler returned empty code")
    return code, schema, defaults
