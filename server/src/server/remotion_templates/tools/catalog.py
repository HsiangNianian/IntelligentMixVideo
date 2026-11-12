"""Register the effect workspace tools and host Plan inspection."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter

from ..planning import PlanAction, StepResult
from .contracts import (
    EffectCheckInput,
    EffectCheckOutput,
    EffectSaveInput,
    EffectSaveOutput,
    ImageInspectInput,
    ImageInspectOutput,
    PreviewRenderInput,
    PreviewRenderOutput,
    WorkspaceEditInput,
    WorkspaceFile,
    WorkspaceReadInput,
    WorkspaceWriteInput,
    WorkspaceWriteOutput,
    JsonObject,
    PresetSearchInput,
    PresetSearchOutput,
    ToolDescriptor,
    ToolInspectInput,
    ToolResult,
)
from .registry import RegisteredTool, ToolFault, get_tool, registered_tools, tool, wire_name


def _success(data: Any) -> dict[str, Any]:
    """Build a typed success envelope for one creation operation."""
    return {"ok": True, "data": data}


def _failure(exc: Exception, code: str) -> dict[str, Any]:
    """Convert a deterministic creation failure into the public error envelope."""
    if isinstance(exc, ToolFault):
        return {"ok": False, "error": exc.error}
    return {"ok": False, "error": {"code": code, "message": str(exc) or code}}


def _inspectable(name: str) -> RegisteredTool | HostTool:
    """Resolve one registered descriptor by dotted ID or by the wire name the model is shown.

    Inspection stays contract-only: it includes the host control tools without granting
    permission to invoke them. It accepts the same names the model sees in its own
    tool window. The descriptor includes host controls even when the caller cannot execute them.
    """
    return get_tool(name, (*registered_tools(), PLAN_TOOL, REPORT_TOOL))


@tool(
    "tools.inspect",
    constraints=["The name is a dotted tool ID or the wire name shown in this tool window; inspection never discovers or executes tools."],
    side_effects=["registry read only"],
    error_codes=["INVALID_ARGUMENT", "TOOL_NOT_FOUND"],
    examples=[{"input": {"tool_name": "unknown.tool"}, "output": {"ok": False, "error": {"code": "TOOL_NOT_FOUND", "message": "example"}}}],
)
async def tools_inspect(owner, request: ToolInspectInput) -> ToolResult[ToolDescriptor]:
    """Return one registered tool descriptor by dotted ID or by the wire name this window shows.

    It reads the contract of a name the caller already has and does not search; the IDs listed in an
    unknown-name error only correct a typo.
    """
    try:
        return _success(_inspectable(request.tool_name).describe())
    except ToolFault as exc:
        return _failure(exc, "TOOL_NOT_FOUND")


# Free-form effects: the Executor writes Effect.tsx and effect.json in its workspace; the host checks, previews and saves them.
@tool(
    "workspace.read",
    constraints=["Reads Effect.tsx (the component) or effect.json (its manifest) as they stand in this task's workspace."],
    side_effects=["task workspace read only"],
    error_codes=["INVALID_ARGUMENT"],
    examples=[{"input": {"path": "effect.json"}, "output": {"ok": True, "data": {"path": "effect.json", "content": "{}", "lines": 1}}}],
)
async def workspace_read(owner, request: WorkspaceReadInput) -> ToolResult[WorkspaceFile]:
    """Read Effect.tsx or effect.json from the workspace."""
    try:
        return _success(owner.read_file(request))
    except Exception as exc:
        return _failure(exc, "INVALID_ARGUMENT")


@tool(
    "workspace.write",
    constraints=["Replaces the whole file. effect.json must parse as the manifest, otherwise nothing is written and the error names the field."],
    side_effects=["task workspace write"],
    error_codes=["INVALID_ARGUMENT"],
    starts_generation=True,
    examples=[{"input": {"path": "Effect.tsx", "content": "export default function Effect() { return null; }"}, "output": {"ok": True, "data": {"path": "Effect.tsx", "lines": 1, "lines_removed": 20, "lines_added": 1, "notes": []}}}],
)
async def workspace_write(owner, request: WorkspaceWriteInput) -> ToolResult[WorkspaceWriteOutput]:
    """Write a whole workspace file: Effect.tsx (a default-exported React component, importing only react, remotion and the content runtime "imv") or effect.json (the manifest)."""
    try:
        return _success(owner.write_file(request))
    except Exception as exc:
        return _failure(exc, "INVALID_ARGUMENT")


@tool(
    "workspace.edit",
    constraints=["Each old_str is copied exactly from the file as the earlier edits left it and occurs there exactly once; otherwise nothing changes and the error names the edit."],
    side_effects=["task workspace write"],
    error_codes=["INVALID_ARGUMENT"],
    starts_generation=True,
    examples=[{"input": {"path": "Effect.tsx", "edits": [{"old_str": "opacity: 0.35", "new_str": "opacity: 0.5"}]}, "output": {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": "example", "field": "/edits/0/old_str"}}}],
)
async def workspace_edit(owner, request: WorkspaceEditInput) -> ToolResult[WorkspaceWriteOutput]:
    """Change part of a workspace file with exact replacements [{old_str, new_str}], applied in order; prefer this to rewriting a file."""
    try:
        return _success(owner.edit_file(request))
    except Exception as exc:
        return _failure(exc, "INVALID_ARGUMENT")


@tool(
    "preview.render",
    constraints=[
        "Mounts the workspace as it stands in the isolated browser and captures frames, without the save checks; a component that throws or does not compile is reported.",
        "The frames reach the next turn as one observation message (on neutral #808080 gray, which is not content), numbered 1..n inside that message.",
        "A task has a limited number of previews (previews_left in the snapshot).",
    ],
    side_effects=["isolated browser execution of the workspace"],
    error_codes=["INVALID_ARGUMENT", "CODE_VALIDATION_FAILED", "COMPOSITION_FAILED", "VALIDATION_UNAVAILABLE", "RESOURCE_LIMIT_EXCEEDED"],
    examples=[{"input": {"frames": [10, 75], "variant": "default"}, "output": {"ok": False, "error": {"code": "CODE_VALIDATION_FAILED", "message": "example"}}}],
)
async def preview_render(owner, request: PreviewRenderInput) -> ToolResult[PreviewRenderOutput]:
    """Look at the effect when numbers cannot answer: motion lists what each line and keyword does, measured from your running code; the frames are for appearance. Render chosen frames (default: keyframes the host picks where the picture changes most, plus the first and last visible and the middle), optionally with 100-character text (long), at 1.5 s (short) or 120 s (long_duration), with sample keywords."""
    try:
        return _success(await owner.render_preview(request))
    except Exception as exc:
        return _failure(exc, "VALIDATION_UNAVAILABLE")


@tool(
    "validate.check",
    constraints=["Runs every check effect.save runs on the workspace as it stands and lists the problems with their fixes; it saves nothing."],
    side_effects=["isolated TypeScript service and browser execution of the workspace"],
    error_codes=["VALIDATION_UNAVAILABLE"],
    examples=[{"input": {}, "output": {"ok": True, "data": {"passed": False, "problems": [{"check": "text_fit", "message": "example"}], "notes": []}}}],
)
async def validate_check(owner, request: EffectCheckInput) -> ToolResult[EffectCheckOutput]:
    """Check the workspace without saving: TypeScript, required parameters, a see-through canvas, 1.5 s and 120 s renders, 100-character text fit, injected text and keywords, whole lines on the middle frame and fontSize scaling. Parameter samples without visible changes produce advisory notes, not failures."""
    try:
        return _success(await owner.check_effect(request))
    except Exception as exc:
        return _failure(exc, "VALIDATION_UNAVAILABLE")


@tool(
    "effect.save",
    constraints=[
        "Runs the checks of validate.check; a failure saves nothing and lists every problem.",
        "A success saves the workspace as this task's latest candidate, which the host publishes when the plan finishes, and returns the measured motion and sends image 1 of the next observation message with the middle frame.",
    ],
    side_effects=["code validation, one browser mount with frame capture, task candidate record"],
    error_codes=["CODE_VALIDATION_FAILED", "COMPOSITION_FAILED", "INVALID_ARGUMENT", "VALIDATION_UNAVAILABLE"],
    starts_generation=True,
    examples=[{"input": {}, "output": {"ok": False, "error": {"code": "CODE_VALIDATION_FAILED", "message": "example"}}}],
)
async def effect_save(owner, request: EffectSaveInput) -> ToolResult[EffectSaveOutput]:
    """Check and save the workspace as this task's candidate. The reply's motion lists what each line and keyword does, measured from your running code: compare it with the request before reporting. The middle frame follows in the next turn, to check what cannot be measured (colours, fills, doubled text)."""
    try:
        return _success(await owner.save_effect(request))
    except Exception as exc:
        return _failure(exc, "VALIDATION_UNAVAILABLE")


@tool(
    "library.search",
    constraints=["Read-only access to the code earlier works built (the Preset library), ranked by matching words and accepted uses; give preset_id to read one record's full code. Reuse means copying what helps into Effect.tsx."],
    side_effects=["read-only catalog listing"],
    error_codes=["INVALID_ARGUMENT", "PRESET_NOT_FOUND", "PRESET_STORE_FAILED", "RESOURCE_LIMIT_EXCEEDED"],
    examples=[{"input": {"query": "裂开 揭示"}, "output": {"ok": True, "data": {"presets": []}}}],
)
async def library_search(owner, request: PresetSearchInput) -> ToolResult[PresetSearchOutput]:
    """Search code earlier works built, by Chinese words naming a look or motion (or read one by preset_id), to borrow techniques for Effect.tsx."""
    try:
        return _success(await asyncio.to_thread(owner.search_presets, request))
    except Exception as exc:
        return _failure(exc, "PRESET_STORE_FAILED")


@tool(
    "image.inspect",
    constraints=["Crops a region of a reference image (fractions of its width and height) and enlarges it as image 1 of the next observation message."],
    side_effects=["local reference image read and PNG write"],
    error_codes=["INVALID_ARGUMENT", "IMAGE_DECODE_FAILED"],
    examples=[{"input": {"image": 1, "x": 0.1, "y": 0.6, "width": 0.8, "height": 0.2}, "output": {"ok": True, "data": {"image": 1, "width": 1024, "height": 256, "source_width": 1080, "source_height": 1920}}}],
)
async def image_inspect(owner, request: ImageInspectInput) -> ToolResult[ImageInspectOutput]:
    """Zoom into part of a reference image (to read a stroke, a gradient or a keyword's colour); the enlarged region follows in the next turn."""
    try:
        return _success(await asyncio.to_thread(owner.inspect_image, request))
    except Exception as exc:
        return _failure(exc, "IMAGE_DECODE_FAILED")


@dataclass(frozen=True)
class HostTool:
    """Host-owned control exposed as a provider function; inspectable, never in the business registry."""

    name: str
    input: type
    description: str
    constraints: tuple[str, ...]
    error_codes: tuple[str, ...]
    example: dict

    def wire(self) -> dict[str, Any]:
        """Expose the control schema as a provider function."""
        return {"type": "function", "function": {"name": wire_name(self.name), "description": self.description, "parameters": self.input.model_json_schema()}}

    def describe(self) -> dict[str, Any]:
        """Return the same complete inspection contract as business tools, without changing state."""
        return ToolDescriptor(
            tool_name=self.name,
            contract_version=1,
            description=self.description,
            input_schema=self.input.model_json_schema(),
            output_schema=TypeAdapter(ToolResult[JsonObject]).json_schema(),
            constraints=list(self.constraints),
            side_effects=["task-local Plan and execution state updates"],
            error_codes=list(self.error_codes),
            examples=[self.example],
        ).model_dump(mode="json", exclude_unset=True)


PLAN_TOOL = HostTool(
    name="tools.plan_execute",
    input=PlanAction,
    description=(
        "Host control; never runs a business tool. Outer: answer (a question that asks for no Sprite), ask (a genuine "
        "ambiguity, or a plan_result whose status is needs_input), delegate (the host "
        "then runs its workspace build-and-save step), plan (hand the task to the Plan ReAct to design or revise its steps) or stop. "
        "Plan: update_plan (send only the unfinished steps), continue, "
        "advance, complete (the host publishes the latest Sprite this task saved), ask (route a blocking question to Outer) or stop."
    ),
    constraints=(
        "Only Outer and Plan may invoke this tool; Executor may only inspect it.",
        "Outer answers, asks, delegates, plans or stops; Plan owns ordered step changes and may ask through Outer.",
        "Completion still requires host verification of a saved Sprite; the host, not Outer, publishes it.",
    ),
    error_codes=("INVALID_ARGUMENT", "tool_limit_reached", "tool_budget_exhausted", "agent_stopped"),
    example={
        "input": {"action": "advance"},
        "output": {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": "Advance requires a completed current step"}},
    },
)

REPORT_TOOL = HostTool(
    name="tools.report_step",
    input=StepResult,
    description=(
        "Executor: report the current step as step_done, blocked or needs_input; call it alone. The host checks "
        "the step really produced its output (an effect_save whose acceptance sheet you saw, or a passing "
        "validate_check in a workspace step) and then starts the next step or publishes the Sprite. needs_input routes a genuine, "
        "blocking question to Outer, which asks the user and resumes the work; what the request leaves open is otherwise "
        "chosen sensibly and changed afterwards."
    ),
    constraints=(
        "Only the Executor may invoke this tool; the host records the step's Sprite from tool receipts.",
        "step_done is refused while the step's output is missing; the refusal names the tool to call next.",
    ),
    error_codes=("INVALID_ARGUMENT",),
    example={
        "input": {"status": "step_done", "summary": "已保存暖色调滤镜 Sprite"},
        "output": {"ok": True, "data": {"status": "step_done", "next": "publishing"}},
    },
)


def available(modules=None, *, executor=False):
    """Return the tools the active layer may call.

    Deferred tools keep their registered contracts but are withheld from the
    model while their implementations are absent, so a tool never appears here
    without a handler that can actually run it.
    """
    tools: list[RegisteredTool | HostTool] = []
    if not executor and (modules is None or "tools" in modules):
        tools.append(PLAN_TOOL)
    for item in registered_tools():
        if not item.implemented:
            continue
        if modules is None or item.name == "tools.inspect" or item.name.split(".", 1)[0] in modules:
            tools.append(item)
    return tools


def resolve(name: str, modules=None, *, executor=False):
    """Resolve one permitted tool by dotted or provider-wire name."""
    return get_tool(name, available(modules, executor=executor))
