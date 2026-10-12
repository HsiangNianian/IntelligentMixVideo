"""PR76 decorator registry: derive strict schemas and dispatch only declared handlers."""

from dataclasses import dataclass
from difflib import get_close_matches
from inspect import signature
from typing import Any, get_type_hints

from pydantic import BaseModel, TypeAdapter, ValidationError

from .contracts import ToolDescriptor, ToolError, ToolFailure


class ToolFault(ValueError):
    """A handler-facing failure with a stable contract code and optional field pointer."""

    def __init__(self, code: str, message: str, *, field: str | None = None, details: Any = None):
        """Keep public tool errors separate from implementation tracebacks."""
        super().__init__(message)
        values: dict[str, Any] = {"code": code, "message": message}
        if field is not None:
            values["field"] = field
        if details is not None:
            values["details"] = details
        self.error = ToolError(**values)


@dataclass(frozen=True)
class RegisteredTool:
    """One decorated handler and its complete input/output descriptor."""

    name: str
    input_model: type[BaseModel]
    output: TypeAdapter
    function: object
    descriptor: ToolDescriptor
    implemented: bool = True
    starts_generation: bool = False

    @property
    def input(self) -> type[BaseModel]:
        """Expose the request model to the ReAct dispatcher."""
        return self.input_model

    def wire(self) -> dict[str, Any]:
        """Map dotted IDs to provider function names without changing schemas."""
        return {
            "type": "function",
            "function": {
                "name": wire_name(self.name),
                "description": self.descriptor.description,
                "parameters": self.descriptor.input_schema,
            },
        }

    def describe(self) -> dict[str, Any]:
        """Return the registered contract without executing the handler."""
        return self.descriptor.model_dump(mode="json", exclude_unset=True)

    async def invoke(self, owner: object, payload: object) -> dict[str, Any]:
        """Validate both boundaries and normalize handler failures to ToolResult JSON."""
        try:
            try:
                request = (
                    self.input_model.model_validate_json(payload)
                    if isinstance(payload, str)
                    else self.input_model.model_validate(payload)
                )
            except ValidationError as exc:
                error = exc.errors(include_input=False, include_context=False)[0]
                pointer = "/" + "/".join(
                    str(part).replace("~", "~0").replace("/", "~1")
                    for part in error["loc"]
                )
                raise ToolFault("INVALID_ARGUMENT", error["msg"], field=pointer) from exc
            result = await self.function(owner, request)
        except ToolFault as exc:
            result = ToolFailure(ok=False, error=exc.error)
        validated = self.output.validate_python(result)
        return self.output.dump_python(validated, mode="json", exclude_unset=True)


_TOOLS: dict[str, RegisteredTool] = {}


def tool(
    name: str,
    *,
    constraints: list[str] | tuple[str, ...] = (),
    side_effects: list[str] | tuple[str, ...] = (),
    error_codes: list[str] | tuple[str, ...] = (),
    examples: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    implemented: bool = True,
    starts_generation: bool = False,
    contract_version: int = 1,
):
    """Decorate an async ``(owner, request)`` handler and register its exact contract."""

    def decorate(function):
        """Fail at import time when a handler or example is not contract-complete."""
        hints = get_type_hints(function)
        parameters = list(signature(function).parameters.values())
        model = hints.get("request")
        return_hint = hints.get("return")
        if len(parameters) != 2 or not isinstance(model, type) or not issubclass(model, BaseModel):
            raise TypeError(f"{name} handlers must accept (owner, request: BaseModel)")
        if return_hint is None:
            raise TypeError(f"{name} handlers must annotate ToolResult output")
        output = TypeAdapter(return_hint)
        if any(wire_name(name) == wire_name(key) for key in _TOOLS):
            raise ValueError(f"Duplicate tool: {name}")
        for example in examples:
            model.model_validate(example["input"])
            output.validate_python(example["output"])
        description = (function.__doc__ or "").strip() or name
        if not implemented:
            # Deferred contracts stay inspectable, but no layer's window offers them; say that before the rest.
            description = f"[Not callable in this build: contract only, no tool window offers it.] {description}"
        descriptor = ToolDescriptor(
            tool_name=name,
            contract_version=contract_version,
            description=description,
            input_schema=model.model_json_schema(),
            output_schema=output.json_schema(),
            constraints=list(constraints),
            side_effects=list(side_effects),
            error_codes=list(error_codes),
            examples=list(examples),
        )
        _TOOLS[name] = RegisteredTool(name, model, output, function, descriptor, implemented, starts_generation)
        return function

    return decorate


def registered_tools() -> tuple[RegisteredTool, ...]:
    """Return tools in stable declaration order."""
    return tuple(_TOOLS.values())


def wire_name(name: str) -> str:
    """Provider function name for a dotted tool ID; the one place that defines the mapping."""
    return name.replace(".", "_")


def get_tool(name: str, candidates=None):
    """Resolve a tool by dotted ID or provider wire name; anything else is rejected, never guessed.

    Candidates default to the business registry. Dispatch supplies its permitted
    tool window; inspection adds host Plan control. Errors and suggestions use
    only that window, so resolving a name never expands a layer's permissions.
    """
    candidates = registered_tools() if candidates is None else tuple(candidates)
    for item in candidates:
        if name in {item.name, wire_name(item.name)}:
            return item
    close = get_close_matches(str(name), [wire_name(item.name) for item in candidates], n=2, cutoff=0.5)
    hint = f" Closest: {', '.join(close)}." if close else ""
    # A dispatch window only holds implemented tools. Inspection also holds contract-only ones, which can be
    # read but never called, so they are listed apart instead of being offered as available.
    known = ", ".join(item.name for item in candidates if getattr(item, "implemented", True)) or "none"
    reference = [item.name for item in candidates if not getattr(item, "implemented", True)]
    message = f"Unknown or out-of-scope tool: {name}.{hint} Available tools: {known}"
    if reference:
        message += f". Contract-only, not callable: {', '.join(reference)}"
    raise ToolFault("TOOL_NOT_FOUND", message)
