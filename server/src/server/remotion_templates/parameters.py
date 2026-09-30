"""Validate editable JSON Schema controls and bind parameter changes to the acceptance spec."""

import json
from copy import deepcopy

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from .models import TemplateCandidate, TemplateSpec


def _target(document: dict, pointer: str) -> tuple[dict | list, str | int]:
    """Resolve a scalar text or visual-style path; timing and structure remain immutable."""
    parts = pointer.split("/")
    if any(
        part.lstrip("-").isdigit() and (not part.isdigit() or str(int(part)) != part)
        for part in parts
    ):
        raise ValueError("parameter indices must be canonical nonnegative integers")
    if parts[:2] == ["", "visual_parameters"]:
        if len(parts) != 3 or parts[2] not in document.get("visual_parameters", {}):
            raise ValueError("x-imv-target must address a declared visual parameter")
    elif len(parts) < 4 or parts[:2] != ["", "text_layers"]:
        raise ValueError("x-imv-target must address a text or visual parameter")
    if any(
        part in {"id", "start_frame", "end_frame", "motion", "decorations"}
        for part in parts[3:]
    ):
        raise ValueError("timing and structural changes require a code edit")
    if parts[1] == "text_layers" and parts[3] not in {"text", "layout", "style"}:
        raise ValueError("only text, layout, and style parameters are editable")
    current = document
    try:
        for part in parts[1:-1]:
            current = current[int(part)] if isinstance(current, list) else current[part]
        key = int(parts[-1]) if isinstance(current, list) else parts[-1]
        value = current[key]
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise ValueError(f"invalid parameter target: {pointer}") from exc
    if not isinstance(value, (str, int, float, bool)):
        raise ValueError("parameter targets must be scalar values")
    return current, key


def validate_candidate(candidate: TemplateCandidate, spec: TemplateSpec) -> None:
    """Reject remote schemas, missing common controls, and defaults inconsistent with the goal."""
    if spec.schema_version == "2":
        from .tools.contracts import ComponentDefinition
        from .tools.schema import validate_component_contract
        errors = validate_component_contract(ComponentDefinition(code=candidate.tsx_code, parameter_schema=candidate.config_schema, default_parameters=candidate.default_config))
        if errors:
            raise ValueError("; ".join(item.message for item in errors))
        if spec.sprite is None or (candidate.tsx_code, candidate.config_schema) != (spec.sprite.code, spec.sprite.parameter_schema):
            raise ValueError("Version source/schema differs from its Sprite")
        return
    schema = candidate.config_schema
    if len(json.dumps(schema)) > 100_000:
        raise ValueError("configuration schema is too large")
    # Flat scalar controls suffice for this MVP and cannot retrieve remote $ref resources.
    if set(schema) - {
        "$schema",
        "title",
        "description",
        "type",
        "properties",
        "required",
        "additionalProperties",
    }:
        raise ValueError(
            "configuration schema must be a flat object without references"
        )
    if (
        schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
    ):
        raise ValueError(
            "config_schema must declare an object with additionalProperties=false"
        )
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not 1 <= len(properties) <= 600:
        raise ValueError("configuration requires 1-600 controls")
    targets = set()
    document = spec.model_dump()
    for name, definition in properties.items():
        if not isinstance(definition, dict) or definition.get("type") not in {
            "string",
            "number",
            "integer",
            "boolean",
        }:
            raise ValueError("each control must have a scalar JSON Schema type")
        allowed = {
            "type",
            "title",
            "description",
            "default",
            "enum",
            "minimum",
            "maximum",
            "minLength",
            "maxLength",
            "x-imv-target",
        }
        if set(definition) - allowed:
            raise ValueError("unsupported configuration schema keyword")
        pointer = definition.get("x-imv-target")
        if not isinstance(pointer, str) or pointer in targets:
            raise ValueError("each control needs a unique x-imv-target")
        targets.add(pointer)
        parent, key = _target(document, pointer)
        if (
            name not in candidate.default_config
            or candidate.default_config[name] != parent[key]
        ):
            raise ValueError(f"default for {name} must equal its TemplateSpec target")
    for index, _layer in enumerate(spec.text_layers):
        for field in (
            "text",
            "style/font_family",
            "style/font_size",
            "style/color",
            "layout/x",
            "layout/y",
        ):
            if f"/text_layers/{index}/{field}" not in targets:
                raise ValueError(
                    f"missing required editable control: text_layers/{index}/{field}"
                )
    if spec.sprite_kind not in {"text", "subtitle"} and targets != {
        f"/visual_parameters/{key}" for key in spec.visual_parameters
    }:
        raise ValueError("visual Sprite controls must match declared scalar parameters")
    if set(schema.get("required", [])) != set(properties):
        raise ValueError(
            "all declared controls must be required; defaults provide their initial values"
        )
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(candidate.default_config)
    except (SchemaError, ValidationError) as exc:
        raise ValueError(f"invalid template configuration: {exc.message}") from exc


def patch_parameters(
    candidate: TemplateCandidate, spec: TemplateSpec, patch: dict
) -> tuple[TemplateCandidate, TemplateSpec]:
    """Modify only declared controls, preserve code bytes, and update acceptance deterministically."""
    validate_candidate(candidate, spec)
    if spec.schema_version == "2":
        from .tools.schema import merge_parameters, validate_parameters
        config = merge_parameters(candidate.default_config, patch)
        validate_parameters(candidate.config_schema, config)
        return candidate.model_copy(update={"default_config": config}), spec.model_copy(deep=True)
    unknown = set(patch) - set(candidate.default_config)
    if unknown:
        raise ValueError(f"unknown template parameters: {', '.join(sorted(unknown))}")
    config = candidate.default_config | patch
    try:
        Draft202012Validator(candidate.config_schema).validate(config)
    except ValidationError as exc:
        raise ValueError(f"invalid parameter value: {exc.message}") from exc
    document = deepcopy(spec.model_dump())
    for name, value in patch.items():
        pointer = candidate.config_schema["properties"][name]["x-imv-target"]
        parent, key = _target(document, pointer)
        parent[key] = value
    new_spec = TemplateSpec.model_validate(document)
    # Do not carry obsolete prose such as "white title" into a now-yellow revision's goal.
    new_spec.description = (
        "Parameterized revision; current text, layout and style are defined by text_layers."
        if spec.sprite_kind in {"text", "subtitle"}
        else "Parameterized revision; current overlay style is defined by visual_parameters."
    )
    new_candidate = candidate.model_copy(update={"default_config": config})
    validate_candidate(new_candidate, new_spec)
    return new_candidate, new_spec


def parameter_changes(
    baseline: TemplateCandidate, current: TemplateCandidate
) -> list[dict]:
    """Compute net user edits from immutable snapshots; reverted values vanish and history cannot accumulate."""
    return [
        {
            "target": current.config_schema.get("properties", {}).get(name, {}).get("x-imv-target", "/parameters/" + name.replace("~", "~0").replace("/", "~1")),
            "before": before,
            "after": current.default_config[name],
        }
        for name, before in baseline.default_config.items()
        if before != current.default_config[name]
    ]
