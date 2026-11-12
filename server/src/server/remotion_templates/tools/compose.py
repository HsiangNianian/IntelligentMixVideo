"""Bundle workspace components and generate standard content controls.

The generic instance wrapper lifts props and places a component on the canvas. Historical complete sources
are bundled directly by workspace.py; role composition and built-in effect recipes have been removed.
"""
from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..categories import (
    BILINGUAL, BILINGUAL_PROP, FIXED_TITLES,
    KEYWORD_EFFECT_PROP,
    LINE_GAP, LINE_STARTS, REQUIRED, TEXT_KEYWORDS, TIER_LAYOUT, TIER_LAYOUTS, TIER_SIZE_RATIO, TRANSLATION_GAP,
)
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
                raise ToolFault("INVALID_ARGUMENT", f"Targets of {name} must share one schema and one value; when the instances differ "
                                f"(two tiers with different colours, say), bind one parameter per instance instead", field=where)
            targets.setdefault(target["instance_id"], []).append({"name": name, "parameter": prop})
        schemas[name] = {**deepcopy(accepted), "title": binding["title"]}
        values[name] = deepcopy(value)
    return schemas, values, targets


def text_configuration(block: dict[str, Any], fonts: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The host-owned configuration parameters of a text block: fixed names, schemas and Chinese titles, with the
    defaults the Agent chose in ``block`` (normalized manifest text) and the font library ``fonts`` as the font enum.

    One tier exposes `fontFamily`/`fontWeight`; two tiers expose each tier's font, weight and size ratio plus
    `tierLayout` (stacked, or overlapping, which needs no gap and defaults it to 0), `lineGap` and `lineStartFrames`. A
    bilingual text (双语字幕) puts the Chinese line in `text` and the English translation of the same sentence in
    `textEn`, the lower tier's content. Both expose `text`, `fontSize` and the seven keyword parameters (off by
    default), a block with a keyword Preset also its `keywordEffect` switch (on unless `keyword_enabled` is false), and effect k (from 1, in
    the block's order) its switch `effect{k}`, its position `effect{k}Target`, unless it loops its length
    `effect{k}Seconds`, entering or at the lower tier's cue its start delay `effect{k}Delay`, and placed at a glyph
    position (entering, exiting or at the cue) how far its glyphs are staggered `effect{k}Stagger`, titled with the
    effect's own Chinese name.
    """
    rules = {rule.name: rule for rule in REQUIRED["text"]}
    font = {"type": "string", "enum": fonts}
    weight = rules["fontWeight"].example
    tiers = block["tiers"]
    # 双语字效的默认文字是「中文\n英文」两行：上栏取第一行，下栏的英文另以 `textEn` 交给宿主，合成时按切片注入。
    lines = block["text"].split("\n") if block.get("bilingual") else None
    entries: list[tuple[str, dict[str, Any], Any]] = [
        ("text", rules["text"].example, "\n".join(lines[::2]) if lines else block["text"]),
    ]
    if lines:
        entries.append((BILINGUAL_PROP, BILINGUAL.example, "\n".join(lines[1::2])))
    entries.append(("fontSize", rules["fontSize"].example, block["font_size"]))
    if len(tiers) == 1:
        entries += [("fontFamily", font, tiers[0]["font_family"]), ("fontWeight", weight, tiers[0]["font_weight"])]
    else:
        low, high = TIER_SIZE_RATIO
        for index, tier in enumerate(tiers, start=1):
            entries += [
                (f"line{index}FontFamily", font, tier["font_family"]),
                (f"line{index}FontWeight", weight, tier["font_weight"]),
                (f"line{index}SizeRatio", {"type": "number", "minimum": low, "maximum": high}, tier["size_ratio"]),
            ]
        entries += [(TIER_LAYOUT, {"type": "string", "enum": list(TIER_LAYOUTS)}, block.get("arrangement", "stack")), (LINE_GAP.name, LINE_GAP.example, block.get("line_gap", 0)),
                    (LINE_STARTS.name, {**LINE_STARTS.example, "minItems": len(tiers), "maxItems": len(tiers)}, block["line_starts"])]
    if len(tiers) == 4:
        entries.append((TRANSLATION_GAP.name, TRANSLATION_GAP.example, block.get("translation_gap", 8)))
    entries += [(rule.name, rule.example, rule.off) for rule in TEXT_KEYWORDS]
    if block.get("keyword") is not None:
        entries.append((KEYWORD_EFFECT_PROP, {"type": "boolean"}, block.get("keyword_enabled", True)))
    titles = dict(FIXED_TITLES)
    if len(tiers) == 4:
        # 四个物理行分成上下两组，面板明确每行的语言，避免将英文误称为第二组。
        for index, name in enumerate(("上栏中文", "上栏英文", "下栏中文", "下栏英文"), 1):
            for suffix, label in (("FontFamily", "字体"), ("FontWeight", "字重"), ("SizeRatio", "字号比例")):
                titles[f"line{index}{suffix}"] = name + label
    schemas = {name: {**deepcopy(definition), "title": titles[name]} for name, definition, _ in entries}
    return schemas, {name: deepcopy(value) for name, _, value in entries}


def _bundle(payload: dict[str, Any]) -> dict[str, Any]:
    """Run `compose.mjs` on ``payload`` and return its answer: the bundled Sprite source as ``code`` and, for a text
    block, ``children``, whether each tier's text Preset draws the line the host renders."""
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
        answer = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise ToolFault("COMPOSITION_FAILED", "Sprite bundler returned invalid JSON") from exc
    if not answer.get("code"):
        raise ToolFault("COMPOSITION_FAILED", "Sprite bundler returned empty code")
    return answer


def _nested(instances: list[dict[str, Any]], targets: dict[str, list[dict[str, str]]], defs: dict[str, Any], properties: dict[str, Any], defaults: dict[str, Any]) -> None:
    """Keep each instance's unbound Preset props nested under its instance_id (schema and defaults), in place."""
    for index, item in enumerate(instances):
        instance_id = item["instance_id"]
        removed = {target["parameter"] for target in targets.get(instance_id, [])}
        nested = deepcopy(item["preset"]["parameter_schema"])
        nested["properties"] = {key: value for key, value in nested.get("properties", {}).items() if key not in removed}
        if "required" in nested:
            nested["required"] = [key for key in nested["required"] if key not in removed]
        defaults[instance_id] = {key: value for key, value in item["parameters"].items() if key not in removed}
        properties[instance_id] = _namespace_schema(nested, f"imv_{index}", defs)




def compose_source(instances: list[dict[str, Any]], bindings: list[dict[str, Any]] | None = None) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Generate Sprite TSX, the parameter schema and complete default props.

    Bound parameters are top-level required properties; each instance keeps its remaining props nested under its
    instance_id, and the generated host passes a bound value to every prop it targets.
    """
    lifted, lifted_defaults, targets = _lift(instances, bindings or [])
    defs: dict[str, Any] = {}
    defaults: dict[str, Any] = dict(lifted_defaults)
    properties: dict[str, Any] = dict(lifted)
    _nested(instances, targets, defs, properties, defaults)
    source_instances = [
        {"instance_id": item["instance_id"], "code": item["preset"]["code"], "layout": item["layout"], "timing": item["timing"],
         "bindings": targets.get(item["instance_id"], [])}
        for item in instances
    ]
    schema = {"type": "object", "properties": properties, "$defs": defs, "additionalProperties": False}
    if lifted:
        schema["required"] = list(lifted)
    return _bundle({"instances": source_instances, "defaults": defaults})["code"], schema, defaults
