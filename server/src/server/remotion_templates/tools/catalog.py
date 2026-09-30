"""Register only the Preset-to-Sprite creation tools and host Plan inspection."""

from dataclasses import dataclass
from typing import Any

from ..planning import PlanAction
from .contracts import (
    CodeValidationInput,
    CodeValidationReport,
    ImageCropInput,
    ImageInfo,
    ImageInfoInput,
    ImageResizeInput,
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
from .registry import RegisteredTool, ToolFault, get_tool, registered_tools, tool


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
    constraints=["The complete source, parameter schema and defaults are saved as a new immutable record."],
    side_effects=["code validation and preset catalog write"],
    error_codes=["INVALID_ARGUMENT", "CODE_VALIDATION_FAILED", "PRESET_STORE_FAILED"],
    examples=[{"input": {"description": "静态文字", "code": "export default function Label(){return null}", "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": False}, "default_parameters": {}}, "output": {"ok": False, "error": {"code": "CODE_VALIDATION_FAILED", "message": "example"}}}],
)
async def preset_create(owner, request: PresetCreateInput) -> ToolResult[PresetCreateOutput]:
    """Validate and save a new immutable Preset definition."""
    try:
        return _success(await owner.create_pr76_preset(request))
    except Exception as exc:
        return _failure(exc, "PRESET_STORE_FAILED")


@tool(
    "sprite.compose",
    constraints=["The host canvas is fixed at 1080x1920, 30 FPS; duration is derived from instances."],
    side_effects=["deterministic source generation; no catalog write"],
    error_codes=["INVALID_ARGUMENT", "PRESET_NOT_FOUND", "COMPOSITION_FAILED"],
    examples=[{"input": {"description": "组合", "instances": [{"instance_id": "title", "source": {"kind": "draft", "preset": {"description": "文字", "code": "export default function Label(){return null}", "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": False}, "default_parameters": {}}}, "layout": {"x": 0.0, "y": 0.0, "width": 100, "height": 100, "z_index": 0}, "timing": {"start_frame": 0, "duration_frames": 1}}]}, "output": {"ok": False, "error": {"code": "COMPOSITION_FAILED", "message": "example"}}}],
)
async def sprite_compose(owner, request: SpriteComposeInput) -> ToolResult[SpriteComposeOutput]:
    """Resolve Presets and produce a complete immutable Sprite draft."""
    try:
        return _success({"sprite": owner.compose_pr76(request)["sprite"]})
    except Exception as exc:
        return _failure(exc, "COMPOSITION_FAILED")


@tool(
    "sprite.create",
    constraints=["Only a compose-produced SpriteDraft with consistent source and parameters may be saved."],
    side_effects=["code validation and sprite catalog write"],
    error_codes=["INVALID_ARGUMENT", "CODE_VALIDATION_FAILED", "SPRITE_STORE_FAILED"],
    examples=[{"input": {"sprite": {"description": "组合", "code": "export default function Sprite(){return null}", "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": False}, "default_parameters": {}, "composition": {"width": 1080, "height": 1920, "fps": 30, "duration_frames": 1}, "instances": [{"instance_id": "title", "preset": {"description": "文字", "code": "export default function Label(){return null}", "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": False}, "default_parameters": {}}, "parameters": {}, "layout": {"x": 0.0, "y": 0.0, "width": 100, "height": 100, "z_index": 0}, "timing": {"start_frame": 0, "duration_frames": 1}}]}}, "output": {"ok": False, "error": {"code": "CODE_VALIDATION_FAILED", "message": "example"}}}],
)
async def sprite_create(owner, request: SpriteCreateInput) -> ToolResult[SpriteCreateOutput]:
    """Validate and persist one composed Sprite draft."""
    try:
        return _success(await owner.create_pr76_sprite(request.sprite))
    except Exception as exc:
        return _failure(exc, "SPRITE_STORE_FAILED")


@tool(
    "tools.inspect",
    constraints=["The name is exact; inspection never discovers or executes tools."],
    side_effects=["registry read only"],
    error_codes=["INVALID_ARGUMENT", "TOOL_NOT_FOUND"],
    examples=[{"input": {"tool_name": "sprite.compose"}, "output": {"ok": False, "error": {"code": "TOOL_NOT_FOUND", "message": "example"}}}],
)
async def tools_inspect(owner, request: ToolInspectInput) -> ToolResult[ToolDescriptor]:
    """Return one exact registered creation-tool descriptor."""
    try:
        return _success(get_tool(request.tool_name).describe())
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
    constraints=["Ranked by description similarity; an empty query result is not an error and never creates a Preset."],
    side_effects=["read-only semantic retrieval"],
    error_codes=["INVALID_ARGUMENT", "SEARCH_UNAVAILABLE", "TIMEOUT"],
    examples=[{"input": {"query": "渐显标题", "limit": 5}, "output": {"ok": False, "error": {"code": "NOT_IMPLEMENTED", "message": "example"}}}],
    implemented=False,
)
async def preset_search(owner, request: PresetSearchInput) -> ToolResult[PresetSearchOutput]:
    """Retrieve Presets by description and return complete records."""
    return await _deferred("preset.search")(owner, request)


@tool(
    "preset.modify",
    constraints=["Replaces whole fields only; the original record and its index never change and no new preset_id is created."],
    side_effects=["read-only copy of one stored Preset"],
    error_codes=["INVALID_ARGUMENT", "PRESET_NOT_FOUND"],
    examples=[{"input": {"preset_id": "preset_title_001", "changes": {"description": "带描边的文字原子"}}, "output": {"ok": False, "error": {"code": "NOT_IMPLEMENTED", "message": "example"}}}],
    implemented=False,
)
async def preset_modify(owner, request: PresetModifyInput) -> ToolResult[PresetModifyOutput]:
    """Return an in-memory copy carrying the caller's replacements."""
    return await _deferred("preset.modify")(owner, request)


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

@dataclass(frozen=True)
class PlanTool:
    """Host-owned orchestration control exposed to Outer and Plan ReAct."""

    name: str = "tools.plan_execute"
    input: type = PlanAction

    def wire(self) -> dict[str, Any]:
        """Expose the Plan transition schema as a provider function."""
        return {"type": "function", "function": {"name": self.name.replace(".", "_"), "description": "Create, continue, advance or finish the ordered Plan; never executes a business tool.", "parameters": self.input.model_json_schema()}}

    def describe(self) -> dict[str, Any]:
        """Describe the host-owned Plan transition without executing it."""
        return {"tool_id": self.name, "version": 1, "description": "Host-owned Plan transition control.", "input_schema": self.input.model_json_schema(), "writes": False, "available": True}


PLAN_TOOL = PlanTool()


def available(modules=None, *, executor=False):
    """Return the tools the active layer may call.

    Deferred tools keep their registered contracts but are withheld from the
    model while their implementations are absent, so a tool never appears here
    without a handler that can actually run it.
    """
    tools: list[RegisteredTool | PlanTool] = []
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
    for item in available(modules, executor=executor):
        if name in {item.name, item.name.replace(".", "_")}:
            return item
    raise ValueError("Unknown or out-of-scope tool")
