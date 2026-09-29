"""Strict PR76 component parameter helpers.

This module owns the data-only part of the tool contract: JSON Schema
validation, recursive parameter overlays, and diagnostics.  It never runs
TSX or mutates a stored Preset/Sprite.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from jsonschema import Draft202012Validator, SchemaError, ValidationError

from .contracts import CodeDiagnostic, ComponentDefinition


def _diagnostic(message: str, *, field: str | None = None) -> CodeDiagnostic:
    """Build a contract diagnostic with a JSON Pointer when one is known."""
    values: dict[str, Any] = {
        "source": "contract",
        "severity": "error",
        "message": message,
    }
    if field is not None:
        values["field"] = field
    return CodeDiagnostic(**values)


def _pointer(path: Any) -> str:
    """Convert jsonschema's deque path into an escaped JSON Pointer."""
    parts = [str(item).replace("~", "~0").replace("/", "~1") for item in path]
    return "/" + "/".join(parts) if parts else ""


def validate_parameters(schema: dict[str, Any], values: dict[str, Any]) -> None:
    """Raise ``ValidationError`` when a complete parameter object is invalid."""
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(values)


def merge_parameters(defaults: dict[str, Any], patch: dict[str, Any] | None) -> dict[str, Any]:
    """Recursively merge objects while replacing arrays and scalar values.

    The input objects are copied, so neither a default object nor a caller
    patch can be mutated by a tool invocation.
    """
    if patch is None:
        return deepcopy(defaults)
    if not isinstance(defaults, dict) or not isinstance(patch, dict):
        return deepcopy(patch)
    result = deepcopy(defaults)
    for key, value in patch.items():
        old = result.get(key)
        if isinstance(old, dict) and isinstance(value, dict):
            result[key] = merge_parameters(old, value)
        else:
            result[key] = deepcopy(value)
    return result


def validate_component_contract(component: ComponentDefinition) -> list[CodeDiagnostic]:
    """Validate schema shape and complete defaults without executing TypeScript."""
    diagnostics: list[CodeDiagnostic] = []
    if not component.code.strip():
        diagnostics.append(_diagnostic("组件源码不能为空。", field="/code"))
    if "export default" not in component.code:
        diagnostics.append(_diagnostic("组件源码必须包含默认导出。", field="/code"))
    if not isinstance(component.parameter_schema, dict):
        diagnostics.append(_diagnostic("parameter_schema 必须是 JSON Schema 对象。", field="/parameter_schema"))
        return diagnostics
    try:
        Draft202012Validator.check_schema(component.parameter_schema)
    except SchemaError as exc:
        diagnostics.append(_diagnostic(f"parameter_schema 不是有效的 JSON Schema：{exc.message}", field="/parameter_schema"))
        return diagnostics
    if component.parameter_schema.get("type") != "object":
        diagnostics.append(_diagnostic("parameter_schema 根类型必须为 object。", field="/parameter_schema/type"))
    if component.parameter_schema.get("additionalProperties") is not False:
        diagnostics.append(_diagnostic("parameter_schema 必须拒绝未知参数。", field="/parameter_schema/additionalProperties"))
    try:
        validate_parameters(component.parameter_schema, component.default_parameters)
    except ValidationError as exc:
        diagnostics.append(_diagnostic(f"默认参数不满足 parameter_schema：{exc.message}", field="/default_parameters" + _pointer(exc.absolute_path)))
    except (SchemaError, TypeError) as exc:
        diagnostics.append(_diagnostic(f"参数 Schema 无法验证默认参数：{exc}", field="/parameter_schema"))
    return diagnostics


def contract_passed(component: ComponentDefinition) -> bool:
    """Return whether the data-only component contract has no errors."""
    return not validate_component_contract(component)
