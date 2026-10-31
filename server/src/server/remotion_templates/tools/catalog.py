"""Register only the Preset-to-Sprite creation tools and host Plan inspection."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter

from ..planning import PlanAction, StepResult
from .contracts import (
    CodeValidationInput,
    CodeValidationReport,
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
    ImageCropInput,
    ImageInfo,
    ImageInfoInput,
    ImageResizeInput,
    JsonObject,
    PresetCreateInput,
    PresetCreateOutput,
    PresetModifyInput,
    PresetModifyOutput,
    PresetSearchInput,
    PresetSearchOutput,
    ProcessedImage,
    RenderValidationInput,
    RenderValidationReport,
    SpriteComposeInput,
    SpriteComposeOutput,
    SpriteCreateInput,
    SpriteCreateOutput,
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


# 延后实现的工具仍按完整契约注册，但只能在宿主显式放开时被调用。
DEFERRED_CODE = "NOT_IMPLEMENTED"


def _deferred(name: str):
    """Return a handler that refuses to fabricate output for a deferred tool."""

    async def handler(owner, request):
        """Report the documented error code instead of pretending the tool ran."""
        return {"ok": False, "error": {"code": DEFERRED_CODE, "message": f"{name} is not implemented in this build."}}

    handler.__doc__ = f"{name} contract is registered; the implementation is deferred."
    return handler

@tool(
    "preset.create",
    constraints=[
        "The complete source, parameter schema and defaults are saved as a new immutable record.",
        "role text, keyword or effect marks a Preset for a text effect's text block. The host passes a text Preset children (the line it renders), fontFamily, fontWeight and fontSize, and still text, segments and Keyword for Presets that draw the line themselves, a keyword Preset text and children (the hit's glyphs) and an effect Preset children, progress, Box, index, count and completedCount; it is type-checked with those props, and its parameter_schema declares only its own style parameters, never these.",
        "An effect Preset records usage {title, slot, target, seconds, stagger}: its short Chinese name and the slot, position and length it is used with by default. text.effects entries may leave those out and take them from it, so a later work reuses the effect by listing only its instance_id and source. Only effect Presets take a usage; a loop has no seconds or stagger. A non-loop glyph usage must explicitly choose stagger (0 together, 1 sequential).",
        "In a new work the host holds each text Preset on its middle frame, so motion of its own never plays: the text's motion belongs in effect Presets, while keyword Presets keep playing.",
    ],
    side_effects=["code validation and preset catalog write"],
    error_codes=["INVALID_ARGUMENT", "CODE_VALIDATION_FAILED", "VALIDATION_UNAVAILABLE", "PRESET_STORE_FAILED"],
    starts_generation=True,
    examples=[{"input": {"description": "静态文字", "code": "export default function Label(){return null}", "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": False}, "default_parameters": {}}, "output": {"ok": False, "error": {"code": "CODE_VALIDATION_FAILED", "message": "example"}}}],
)
async def preset_create(owner, request: PresetCreateInput) -> ToolResult[PresetCreateOutput]:
    """Validate and save a new immutable Preset definition."""
    try:
        return _success(await owner.create_preset(request))
    except Exception as exc:
        return _failure(exc, "PRESET_STORE_FAILED")


@tool(
    "sprite.compose",
    contract_version=10,
    constraints=[
        "The host canvas is fixed at 1080x1920, 30 FPS. When the snapshot has target_duration_frames (a new work), the Sprite must last exactly that long (the last instance's end, or the text block's duration_frames), otherwise the call is refused.",
        "Contract v4: kind is required (text, filter_overlay, video_overlay or transition_overlay). A text effect is composed from text {layout, text, font_size, tiers, line_gap, line_starts, keyword, effects, duration_frames} with role text Presets for the tiers, an optional role keyword Preset and role effect Presets, and no instances; the host lays the block out and provides its configuration parameters. Other kinds place instances by layout and timing.",
        "text.effects (v4) lists up to 6 effects {instance_id, source, parameters, title, slot enter|exit|loop, target block|tiers|line1|line2|chars|keyword, seconds (enter and exit only), enabled}, the first innermost; a host built-in draws the motion itself, so name one with builtin (fade, rise, pop, slideLeft, slideRight, slideUp, slideDown, blur, glow, breathing) instead of source, and give exactly one of source or builtin, and a built-in's own style props (for example a glow's color and intensity) can be bound like a Preset's; tiers, line1 and line2 need two tiers; a block has only the effects listed: the host adds none, so a block without effects shows its text still. Effect k gets the configuration parameters effect{k} (switch), effect{k}Target (position) and, entering or exiting, effect{k}Seconds (length).",
        "v5: a text Preset may draw the line the host passes as children (keyword hits already drawn) instead of drawing segments itself; only such tiers offer the position chars, where every glyph is a unit and the units take an entrance or exit in turn (reading order in, last first out). Every effect Preset also receives index and count (the unit in its tier at chars, the tier at tiers/line1/line2, 0 of 1 elsewhere).",
        "v6: a two-tier text takes arrangement stack (default; line_gap apart) or overlap (both tiers in one place, centred on each other, the lower one on top; line_gap may be left out), which becomes the configuration parameter tierLayout. The slot cue (two tiers only) keeps progress 0 until the lower tier appears and then rises to 1 over seconds; it stays 0 while the lower tier has no text. The element around each tier carries the tier's font size.",
        "v7: a text.effects entry may leave out title, slot, target and seconds; the host takes them from its effect Preset's usage, and an entry without them whose Preset records no usage is refused.",
        "v8: a keyword Preset may draw the hit's glyphs the host passes as children, so a hit's glyphs are units too; the glyph positions are chars (all glyphs), keywordChars (the keyword hits' glyphs, only with such a keyword Preset or none) and plainChars (the other glyphs), each counting its own units. An entrance or cue effect takes delay (0-3 seconds, configuration parameter effect{k}Delay) and starts that much later, so effects can follow one another.",
        "v9: an entrance, exit or cue effect at a glyph position takes stagger (0-1, configuration parameter effect{k}Stagger): 1 one glyph after another, 0 all together, in between overlapping. The text block may name preview_keywords, words of its default text that the review frames and the workspace preview show as keywords while templates pass none.",
        "v10: glyph timing must be chosen explicitly with stagger in the entry or its Preset's usage; omission is refused instead of silently animating all selected text glyph by glyph. usage.stagger is inherited only at a glyph target with enter, exit or cue. Each effect receives completedCount, the count of this target's units in its tier whose local progress is 1. Use index < completedCount for discrete visibility and completedCount - 1 for a following cursor; progress itself remains continuous for fades.",
        "parameters lifts Preset props to required top-level Sprite style parameters that templates may change per placement; each binding targets declared top-level Preset props that share one object schema and one value; a bound number needs minimum and maximum (integers for an integer), a bound string a colour pattern, enum or maxLength of at most 2000; only type, title, description, default, minimum, maximum, enum, minLength, maxLength, the colour pattern (or the keywordColor pattern), maxItems and items.maxLength may be used. A text block's configuration names (text, fontSize, fonts, weights, size ratios, lineGap, lineStartFrames, keyword options, keywordEffect, effect{k}...) are taken, for parameter names and for the tiers', keyword's and effects' instance_id alike.",
        "The host owns position, scale, rotation and opacity of the whole Sprite; do not bind or implement them.",
    ],
    side_effects=["deterministic source generation; no catalog write"],
    error_codes=["INVALID_ARGUMENT", "PRESET_NOT_FOUND", "COMPOSITION_FAILED"],
    starts_generation=True,
    examples=[{"input": {"description": "双栏标题", "kind": "text", "text": {
        "layout": {"x": 54.0, "y": 700.0, "width": 972, "height": 520}, "text": "判断一个人\n选择花字、滤镜和特效", "font_size": 96,
        "tiers": [
            {"instance_id": "upper", "source": {"kind": "stored", "preset_id": "preset_tier_001"}, "parameters": {"color": "#FFFFFF"}, "font_family": "Noto Sans CJK SC", "font_weight": 700, "size_ratio": 0.5},
            {"instance_id": "lower", "source": {"kind": "stored", "preset_id": "preset_tier_001"}, "parameters": {"color": "#F5B93C"}, "font_family": "Noto Sans CJK SC", "font_weight": 700, "size_ratio": 1},
        ],
        "line_gap": 24, "line_starts": [0, 30], "keyword": {"instance_id": "keyword", "source": {"kind": "stored", "preset_id": "preset_glow_001"}},
        "effects": [
            {"instance_id": "fadeIn", "source": {"kind": "stored", "preset_id": "preset_fade_001"}, "title": "淡入", "slot": "enter", "target": "tiers", "seconds": 0.5},
            {"instance_id": "fadeOut", "source": {"kind": "stored", "preset_id": "preset_fade_001"}, "title": "淡出", "slot": "exit", "target": "block"},
        ], "duration_frames": 150},
        "parameters": [
            {"name": "line1Color", "title": "上栏颜色", "targets": [{"instance_id": "upper", "parameter": "color"}]},
            {"name": "line2Color", "title": "下栏颜色", "targets": [{"instance_id": "lower", "parameter": "color"}]},
            {"name": "keywordEffectColor", "title": "关键词发光颜色", "targets": [{"instance_id": "keyword", "parameter": "glowColor"}]},
        ]}, "output": {"ok": False, "error": {"code": "PRESET_NOT_FOUND", "message": "example"}}}],
)
async def sprite_compose(owner, request: SpriteComposeInput) -> ToolResult[SpriteComposeOutput]:
    """Resolve Presets (stored preset_id, or a task draft_id from preset_modify or the accepted base) and keep the composed Sprite as a host draft; returns its draft_id and a code-free summary."""
    try:
        return _success(await owner.compose_sprite(request))
    except Exception as exc:
        return _failure(exc, "COMPOSITION_FAILED")


@tool(
    "sprite.create",
    contract_version=2,
    constraints=[
        "Takes the draft_id that sprite.compose returned in this task; the host keeps the composed source, so it is never re-sent.",
        "The Sprite is mounted once in the browser and must leave the user's video visible: a canvas that is opaque in most sampled frames (4 of 5) is refused with CODE_VALIDATION_FAILED.",
        "The Sprite must expose its kind's required top-level parameters (text: a text block provides its configuration and needs the style parameter color for one tier or line1Color and line2Color for two tiers; filter_overlay: intensity, tintColor; video_overlay and transition_overlay: color, speed), otherwise CATEGORY_PARAMETERS_MISSING lists what to bind.",
        "v2: for the first two Sprites a task saves, the same mount captures up to six still frames (cut to the content, composited onto neutral #808080 gray, which is not content), listed in review.frames with their sampling times. The transition's before/during/after frames and the exit come first, then one frame per entrance effect, then the generic middle frame. They are attached after the reference images to the following Executor turns; compare each subject before/during/after with the original request and fix mismatches. A technical pass is not a visual verdict. review.rounds_left counts the reviews still to come.",
    ],
    side_effects=["code validation, one browser mount with a transparency measurement and review frames, and sprite catalog write"],
    error_codes=["INVALID_ARGUMENT", "CODE_VALIDATION_FAILED", "CATEGORY_PARAMETERS_MISSING", "VALIDATION_UNAVAILABLE", "SPRITE_STORE_FAILED"],
    starts_generation=True,
    examples=[{"input": {"draft_id": "sprite_draft_0123456789ab"}, "output": {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": "Unknown draft_id sprite_draft_0123456789ab; drafts in this task: none", "field": "/draft_id"}}}],
)
async def sprite_create(owner, request: SpriteCreateInput) -> ToolResult[SpriteCreateOutput]:
    """Validate and persist the composed Sprite a sprite.compose draft_id refers to."""
    try:
        return _success(await owner.create_sprite(request.draft_id))
    except Exception as exc:
        return _failure(exc, "SPRITE_STORE_FAILED")


def _inspectable(name: str) -> RegisteredTool | HostTool:
    """Resolve one registered descriptor by dotted ID or by the wire name the model is shown.

    Inspection stays contract-only: it includes the host control tools without granting
    permission to invoke them. It accepts the same names the model sees in its own
    tool window. `resolve()` is not reused here because it hides tools that
    are registered with a contract but no implementation yet, and describing those
    contracts is exactly what this tool is for.
    """
    return get_tool(name, (*registered_tools(), PLAN_TOOL, REPORT_TOOL))


@tool(
    "tools.inspect",
    constraints=["The name is a dotted tool ID or the wire name shown in this tool window; inspection never discovers or executes tools."],
    side_effects=["registry read only"],
    error_codes=["INVALID_ARGUMENT", "TOOL_NOT_FOUND"],
    examples=[{"input": {"tool_name": "sprite.compose"}, "output": {"ok": False, "error": {"code": "TOOL_NOT_FOUND", "message": "example"}}}],
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


@tool(
    "image.info",
    constraints=["Reads the task reference image already uploaded and registered by the server."],
    side_effects=["local asset read only"],
    error_codes=["INVALID_ARGUMENT", "IMAGE_FETCH_FAILED", "UNSUPPORTED_IMAGE", "IMAGE_DECODE_FAILED", "RESOURCE_LIMIT_EXCEEDED", "TIMEOUT"],
    examples=[{"input": {"image": {"asset_id": "3f2504e0-4f89-41d3-9a0c-0305e82c3301"}}, "output": {"ok": False, "error": {"code": "NOT_IMPLEMENTED", "message": "example"}}}],
    implemented=False,
)
async def image_info(owner, request: ImageInfoInput) -> ToolResult[ImageInfo]:
    """Read image dimensions, format, byte size and alpha presence."""
    return await _deferred("image.info")(owner, request)


@tool(
    "image.resize",
    constraints=["Forces the exact target pixels; aspect ratio is not preserved and no padding or cropping is added."],
    side_effects=["local asset read and PNG write"],
    error_codes=["INVALID_ARGUMENT", "IMAGE_FETCH_FAILED", "UNSUPPORTED_IMAGE", "IMAGE_DECODE_FAILED", "IMAGE_PROCESSING_FAILED", "IMAGE_STORE_FAILED", "RESOURCE_LIMIT_EXCEEDED", "TIMEOUT"],
    examples=[{"input": {"image": {"asset_id": "3f2504e0-4f89-41d3-9a0c-0305e82c3301"}, "width": 600, "height": 600}, "output": {"ok": False, "error": {"code": "NOT_IMPLEMENTED", "message": "example"}}}],
    implemented=False,
)
async def image_resize(owner, request: ImageResizeInput) -> ToolResult[ProcessedImage]:
    """Scale to the requested pixels and return the new image information."""
    return await _deferred("image.resize")(owner, request)


@tool(
    "image.crop",
    constraints=["Crop region is [x, x+width) x [y, y+height); out-of-bounds requests fail instead of clamping."],
    side_effects=["local asset read and PNG write"],
    error_codes=["INVALID_ARGUMENT", "IMAGE_FETCH_FAILED", "UNSUPPORTED_IMAGE", "IMAGE_DECODE_FAILED", "CROP_OUT_OF_BOUNDS", "IMAGE_PROCESSING_FAILED", "IMAGE_STORE_FAILED", "RESOURCE_LIMIT_EXCEEDED", "TIMEOUT"],
    examples=[{"input": {"image": {"asset_id": "3f2504e0-4f89-41d3-9a0c-0305e82c3301"}, "x": 100, "y": 50, "width": 400, "height": 300}, "output": {"ok": False, "error": {"code": "NOT_IMPLEMENTED", "message": "example"}}}],
    implemented=False,
)
async def image_crop(owner, request: ImageCropInput) -> ToolResult[ProcessedImage]:
    """Crop the requested pixel region without scaling."""
    return await _deferred("image.crop")(owner, request)


@tool(
    "preset.search",
    contract_version=3,
    constraints=["Contract v3: the optional query is cut into words (Chinese needs no spaces) and the summaries come best match first: by the number of query words a Preset's description or usage title contains (a Preset matching none is left out), then by how many accepted versions used it and how many were saved to the asset library (published_uses, accepted_uses), then newest first; an empty query lists every Preset in that order. Optional role (text, keyword or effect) and slot (an effect's usage slot) filter it; summaries carry an effect's usage; limit is 1-100. Results also have a 40000-byte serialized content budget; has_more indicates omitted summaries. Use a narrower query or preset_id to read a full record. An oversized full record fails without truncating code."],
    side_effects=["read-only catalog listing"],
    error_codes=["INVALID_ARGUMENT", "PRESET_NOT_FOUND", "PRESET_STORE_FAILED", "RESOURCE_LIMIT_EXCEEDED"],
    examples=[{"input": {"query": "描边", "role": "text", "limit": 5}, "output": {"ok": True, "data": {"presets": []}}}],
)
async def preset_search(owner, request: PresetSearchInput) -> ToolResult[PresetSearchOutput]:
    """List Preset summaries (id, description, parameter names), optionally filtered by keyword."""
    try:
        return _success(await asyncio.to_thread(owner.search_presets, request))
    except Exception as exc:
        return _failure(exc, "PRESET_STORE_FAILED")


@tool(
    "preset.modify",
    constraints=[
        "The original record never changes and no new preset_id is created. preset_id may also be a task draft handle (base_* or an earlier draft_id). The copy is kept by the host: compose it with source {kind: \"draft\", draft_id}.",
        "changes.edits replaces exact text in the copied code, in order: each old_str is copied verbatim from the code as the earlier edits left it and must occur there exactly once, otherwise nothing is copied and the error names the edit. edits and code are exclusive; code replaces the whole source. description, parameter_schema, default_parameters and usage replace whole fields.",
        "The reply counts the code lines the copy removed and added relative to its source, and carries a notice when an edit task copies a stored Preset the accepted version does not draw with instead of its base_* draft.",
    ],
    side_effects=["read-only copy of one stored Preset"],
    error_codes=["INVALID_ARGUMENT", "PRESET_NOT_FOUND", "PRESET_STORE_FAILED"],
    examples=[{"input": {"preset_id": "base_title", "changes": {"edits": [{"old_str": "color: props.color,", "new_str": "color: props.color, WebkitTextStroke: \"2px #000000\","}]}}, "output": {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": "example", "field": "/changes/edits/0/old_str"}}}],
)
async def preset_modify(owner, request: PresetModifyInput) -> ToolResult[PresetModifyOutput]:
    """Copy a stored Preset or a task draft (base_*, an earlier draft_id) with changes, and keep the copy as a host draft. To change its code, prefer edits [{old_str, new_str}]: exact replacements of the lines involved, each old_str copied verbatim from the source and occurring there once; code replaces the whole source."""
    try:
        return _success(await asyncio.to_thread(owner.modify_preset, request))
    except Exception as exc:
        return _failure(exc, "PRESET_STORE_FAILED")


@tool(
    "validate.code",
    constraints=["Checks the single-file default export, the parameter schema and the default parameters, then reports real LSP diagnostics."],
    side_effects=["isolated TypeScript language service"],
    error_codes=["INVALID_ARGUMENT", "VALIDATION_UNAVAILABLE", "RESOURCE_LIMIT_EXCEEDED", "TIMEOUT"],
    examples=[{"input": {"component": {"code": "export default function C(){return null}", "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": False}, "default_parameters": {}}}, "output": {"ok": False, "error": {"code": "NOT_IMPLEMENTED", "message": "example"}}}],
    implemented=False,
)
async def validate_code(owner, request: CodeValidationInput) -> ToolResult[CodeValidationReport]:
    """Run contract and LSP checks; a failing check is a result, not a tool error."""
    return await _deferred("validate.code")(owner, request)


@tool(
    "validate.render",
    constraints=["Fixed 1080x1920 at 30 FPS; frame 0 may be fully transparent. Nothing is written back to the tested component."],
    side_effects=["isolated browser execution of the supplied component and scripts"],
    error_codes=["INVALID_ARGUMENT", "VALIDATION_UNAVAILABLE", "RESOURCE_LIMIT_EXCEEDED", "TIMEOUT"],
    examples=[{"input": {"component": {"code": "export default function C(){return null}", "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": False}, "default_parameters": {}}, "duration_frames": 150, "tests": []}, "output": {"ok": False, "error": {"code": "NOT_IMPLEMENTED", "message": "example"}}}],
    implemented=False,
)
async def validate_render(owner, request: RenderValidationInput) -> ToolResult[RenderValidationReport]:
    """Run the four base checks and any supplied assertion scripts."""
    return await _deferred("validate.render")(owner, request)

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
        "The frames reach the next turn as a message of the conversation (on neutral #808080 gray, which is not content).",
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
    """Check the workspace without saving: TypeScript, required parameters, a see-through canvas, 1.5 s and 120 s renders, 100-character text fit, and that the effect shows injected text and keywords, keeps its lines whole on the middle frame, shrinks with fontSize and uses every parameter."""
    try:
        return _success(await owner.check_effect(request))
    except Exception as exc:
        return _failure(exc, "VALIDATION_UNAVAILABLE")


@tool(
    "effect.save",
    constraints=[
        "Runs the checks of validate.check; a failure saves nothing and lists every problem.",
        "A success saves the workspace as this task's latest candidate, which the host publishes when the plan finishes, and returns the measured motion and sends the middle frame to the next turn.",
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
    constraints=["Read-only access to the code earlier works built (the Preset library), ranked as preset.search ranks it; give preset_id to read one record's full code. Reuse means copying what helps into Effect.tsx."],
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
    constraints=["Crops a region of a reference image (fractions of its width and height) and enlarges it as the next observation image."],
    side_effects=["local reference image read and PNG write"],
    error_codes=["INVALID_ARGUMENT", "IMAGE_DECODE_FAILED"],
    examples=[{"input": {"image": 1, "x": 0.1, "y": 0.6, "width": 0.8, "height": 0.2}, "output": {"ok": True, "data": {"image": 3, "width": 1024, "height": 256, "source_width": 1080, "source_height": 1920}}}],
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
        "then runs its standard Preset -> Sprite plan), plan (hand the task to the Plan ReAct to design or revise its steps) or stop. "
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
        "the step really produced its output (a sprite_create success in a step with the sprite module, a ready "
        "Preset in a preset step; for a free-form effect an effect_save whose acceptance sheet you saw, or a passing "
        "validate_check in a workspace step) and then starts the next step or publishes the Sprite. needs_input routes a genuine, "
        "blocking question to Outer, which asks the user and resumes the work; what the request leaves open is otherwise "
        "chosen sensibly and changed afterwards."
    ),
    constraints=(
        "Only the Executor may invoke this tool; the host records the step's Presets and Sprite from tool receipts.",
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
