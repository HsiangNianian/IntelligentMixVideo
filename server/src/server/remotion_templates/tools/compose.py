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


def _without_labels(value: Any) -> Any:
    """Compare property schemas by what they accept; titles and descriptions only label a control."""
    if isinstance(value, dict):
        return {key: _without_labels(item) for key, item in value.items() if key not in {"title", "description"}}
    if isinstance(value, list):
        return [_without_labels(item) for item in value]
    return value


def _lift(instances: list[dict[str, Any]], bindings: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, list[dict[str, str]]]]:
    """Resolve top-level parameter bindings into their schemas, defaults and per-instance prop targets.

    Every target must be a declared top-level prop of its instance's Preset, bound at most once; all targets of one
    binding must accept the same values and currently hold the same value, which becomes the default.
    """
    by_id = {item["instance_id"]: item for item in instances}
    schemas: dict[str, Any] = {}
    values: dict[str, Any] = {}
    targets: dict[str, list[dict[str, str]]] = {}
    bound: set[tuple[str, str]] = set()
    for index, binding in enumerate(bindings):
        name, field = binding["name"], f"/parameters/{index}"
        if name in by_id or name in schemas:
            raise ToolFault("INVALID_ARGUMENT", f"Parameter name {name} repeats a parameter or an instance_id", field=f"{field}/name")
        accepted = None
        for position, target in enumerate(binding["targets"]):
            where = f"{field}/targets/{position}"
            instance = by_id.get(target["instance_id"])
            if instance is None:
                raise ToolFault("INVALID_ARGUMENT", f"Unknown instance_id {target['instance_id']}; instances: {list(by_id)}", field=f"{where}/instance_id")
            prop = target["parameter"]
            declared = instance["preset"]["parameter_schema"].get("properties", {})
            if prop not in declared:
                raise ToolFault("INVALID_ARGUMENT", f"Instance {target['instance_id']} has no parameter {prop}; parameters: {list(declared)}", field=f"{where}/parameter")
            if (target["instance_id"], prop) in bound:
                raise ToolFault("INVALID_ARGUMENT", f"{target['instance_id']}.{prop} is already bound", field=where)
            bound.add((target["instance_id"], prop))
            definition = declared[prop]
            if not isinstance(definition, dict):
                # JSON Schema allows `true`/`false` here, but a template control needs a typed, bounded schema.
                raise ToolFault("INVALID_ARGUMENT", f"{target['instance_id']}.{prop} has a boolean schema; give it a typed object schema to bind it", field=where)
            if "$ref" in json.dumps(definition):
                raise ToolFault("INVALID_ARGUMENT", f"{target['instance_id']}.{prop} uses $ref; inline its schema to bind it", field=where)
            if accepted is None:
                accepted, value = definition, instance["parameters"].get(prop)
            elif _without_labels(definition) != _without_labels(accepted) or instance["parameters"].get(prop) != value:
                raise ToolFault("INVALID_ARGUMENT", f"Targets of {name} must share one schema and one value", field=where)
            targets.setdefault(target["instance_id"], []).append({"name": name, "parameter": prop})
        schemas[name] = {**deepcopy(accepted), "title": binding["title"]}
        values[name] = deepcopy(value)
    return schemas, values, targets


def compose_source(instances: list[dict[str, Any]], bindings: list[dict[str, Any]] | None = None) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Generate Sprite TSX, the parameter schema and complete default props.

    Bound parameters are top-level required properties; each instance keeps its remaining props nested under its
    instance_id, and the generated host passes a bound value to every prop it targets.
    """
    lifted, lifted_defaults, targets = _lift(instances, bindings or [])
    defs: dict[str, Any] = {}
    source_instances: list[dict[str, Any]] = []
    defaults: dict[str, Any] = dict(lifted_defaults)
    properties: dict[str, Any] = dict(lifted)
    for index, item in enumerate(instances):
        instance_id = item["instance_id"]
        preset = item["preset"]
        removed = {target["parameter"] for target in targets.get(instance_id, [])}
        nested = deepcopy(preset["parameter_schema"])
        nested["properties"] = {key: value for key, value in nested.get("properties", {}).items() if key not in removed}
        if "required" in nested:
            nested["required"] = [key for key in nested["required"] if key not in removed]
        defaults[instance_id] = {key: value for key, value in item["parameters"].items() if key not in removed}
        properties[instance_id] = _namespace_schema(nested, f"imv_{index}", defs)
        layout = item["layout"]
        timing = item["timing"]
        source_instances.append({
            "instance_id": instance_id, "code": preset["code"], "layout": layout, "timing": timing,
            "bindings": targets.get(instance_id, []),
        })
    schema = {"type": "object", "properties": properties, "$defs": defs, "additionalProperties": False}
    if lifted:
        schema["required"] = list(lifted)
    payload = {"instances": source_instances, "defaults": defaults}
    script = Path(__file__).resolve().parents[2] / "remotion" / "compose.mjs"
    try:
        process = subprocess.run(
            ["node", str(script)],
            # 参数按固定键序序列化：生成的源码只取决于数据，不受调用方字典顺序影响。
            input=json.dumps(payload, ensure_ascii=False, sort_keys=True),
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
