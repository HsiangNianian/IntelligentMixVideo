"""PR76 工具契约：严格校验入出参，为装饰器提供完整 Schema；业务实现位于同目录。"""
from __future__ import annotations

from typing import Annotated, Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import (
    BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StrictFloat,
    StrictInt, StrictStr, StringConstraints,
)
from pydantic.json_schema import SkipJsonSchema

# 显式严格类型也用于递归 JSON，避免参数中的值被隐式转换。
Integer = Annotated[StrictInt, Field(ge=-(2**53 - 1), le=2**53 - 1)]
PositiveInteger = Annotated[Integer, Field(gt=0)]
NonNegativeInteger = Annotated[Integer, Field(ge=0)]
FiniteNumber = Annotated[StrictFloat, Field(allow_inf_nan=False)]
NonEmptyString = Annotated[StrictStr, Field(min_length=1)]
Description = Annotated[
    StrictStr, StringConstraints(strip_whitespace=True, min_length=1)
]
type JsonValue = None | StrictBool | StrictInt | FiniteNumber | StrictStr | list[JsonValue] | dict[str, JsonValue]
JsonObject = dict[str, JsonValue]
JsonSchema = JsonObject
PresetId = NonEmptyString
SpriteId = NonEmptyString
Timestamp = NonEmptyString  # 输出还须符合下文的 UTC ISO 8601 约定。
T = TypeVar("T")


def reject_explicit_none(value: Any) -> Any:
    """允许省略字段，但拒绝调用方显式传入 null。"""
    if value is None:
        raise ValueError("字段可省略，但不能为 null")
    return value


def omit_placeholder_default(schema: dict[str, Any]) -> None:
    """内部 None 只表示未传，不向 JSON Schema 声明 null 默认值。"""
    schema.pop("default", None)


Omittable = Annotated[
    T | SkipJsonSchema[None],
    BeforeValidator(reject_explicit_none),
    Field(default=None, validate_default=False, json_schema_extra=omit_placeholder_default),
]


class ContractModel(BaseModel):
    """公共结构模型：拒绝未知字段和隐式类型转换。"""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class ToolError(ContractModel):
    """工具未完成操作时的结构化错误。"""

    code: NonEmptyString
    message: str
    field: Omittable[str]
    details: Omittable[JsonObject]


class ToolSuccess(ContractModel, Generic[T]):
    """成功执行回执；validate 的业务结论另见 data.passed。"""

    ok: Literal[True]
    data: T


class ToolFailure(ContractModel):
    """失败执行回执，不携带可用的成功数据。"""

    ok: Literal[False]
    error: ToolError


type ToolResult[T] = Annotated[ToolSuccess[T] | ToolFailure, Field(discriminator="ok")]

class ComponentDefinition(ContractModel):
    """单文件组件源码及其外部参数契约。"""

    code: str
    parameter_schema: JsonSchema
    default_parameters: JsonObject


class PresetDraft(ComponentDefinition):
    """尚未入库的完整预设，可标记直接来源。"""

    description: Description
    source_preset_id: Omittable[PresetId]


class PresetRecord(PresetDraft):
    """通过入库代码校验后保存的预设。"""

    preset_id: PresetId
    created_at: Timestamp

# create 承接 Agent 已写好的完整定义；输入模型与 PresetDraft 完全相同。
PresetCreateInput = PresetDraft

class PresetCreateOutput(ContractModel):
    """新记录和本次入库的代码校验报告。"""

    preset: PresetRecord
    validation: CodeValidationReport



# 工具入参以文档化的 JSON 字符串传递；宿主内部继续用 UUID 对象引用同一素材。
AssetId = Annotated[UUID, BeforeValidator(lambda value: value if isinstance(value, UUID) else UUID(str(value)))]


# 用户上传的参考图由服务端登记为本地素材；图片工具按该引用读取，不使用 URL。
class ImageReference(ContractModel):
    """已上传并登记的参考图片。"""

    asset_id: AssetId


# 图片工具直接读取任务参考图：用户上传后已由服务端登记为本地素材，无需 URL、不上传云端。
class ImageInfoInput(ContractModel):
    """待读取的任务参考图片。"""

    image: ImageReference


class ImageInfo(ContractModel):
    """实际读取到的图片信息。"""

    width: PositiveInteger
    height: PositiveInteger
    mime_type: Literal["image/png", "image/jpeg", "image/webp"]
    size_bytes: PositiveInteger
    has_alpha: bool


class ProcessedImage(ImageInfo):
    """处理后的图片信息；结果同样保存在服务端素材目录，供后续工具继续读取。"""

    image: ImageReference


class ImageResizeInput(ImageInfoInput):
    """指定强制缩放后的目标像素宽高。"""

    width: PositiveInteger
    height: PositiveInteger


class ImageCropInput(ImageInfoInput):
    """相对原图左上角的像素裁剪区域。"""

    x: NonNegativeInteger
    y: NonNegativeInteger
    width: PositiveInteger
    height: PositiveInteger


# --- preset tool contracts (doc section 6) ------------------------------------

# create 承接 Agent 已写好的完整定义；输入模型即 PresetDraft。

class PresetSearchInput(ContractModel):
    """可选关键词（子串匹配）与结果数量上限；留空则列出最近的预设；给出 preset_id 则读取该完整记录。"""

    query: str = ""
    limit: Annotated[PositiveInteger, Field(le=100)] = 20
    preset_id: Omittable[PresetId]


class PresetSummary(ContractModel):
    """供 Agent 自行挑选的预设摘要；完整记录用 preset_id 再取。"""

    preset_id: PresetId
    description: Description
    parameter_names: list[str]


class PresetSearchOutput(ContractModel):
    """摘要列表，允许为空。"""

    presets: list[PresetSummary]
    preset: Omittable[PresetRecord]
    has_more: bool = False


class PresetChanges(ContractModel):
    """仅替换显式提供的字段；工具还须检查至少提供一项。"""

    code: Omittable[str]
    description: Omittable[Description]
    parameter_schema: Omittable[JsonSchema]
    default_parameters: Omittable[JsonObject]


class PresetModifyInput(ContractModel):
    """原记录 ID 和 Agent 已经改好的替换内容。"""

    preset_id: PresetId
    changes: PresetChanges


class PresetModifyOutput(ContractModel):
    """完整副本，不含新记录 ID；source_preset_id 指向原记录。"""

    preset: PresetDraft


class Composition(ContractModel):
    """固定主画布及工具计算的总帧数。"""

    width: Literal[1080]
    height: Literal[1920]
    fps: Literal[30]
    duration_frames: PositiveInteger


class InstanceLayout(ContractModel):
    """实例的局部画布、主画布位置和叠放层级。"""

    x: FiniteNumber
    y: FiniteNumber
    width: PositiveInteger
    height: PositiveInteger
    z_index: Integer


class InstanceTiming(ContractModel):
    """实例在主时间轴的起点与自身持续帧数。"""

    start_frame: NonNegativeInteger
    duration_frames: PositiveInteger


class StoredPresetSource(ContractModel):
    """引用已经保存的预设。"""

    kind: Literal["stored"]
    preset_id: PresetId


class DraftPresetSource(ContractModel):
    """直接携带完整预设副本。"""

    kind: Literal["draft"]
    preset: PresetDraft


PresetSource = Annotated[
    StoredPresetSource | DraftPresetSource, Field(discriminator="kind")
]


class PresetInstance(ContractModel):
    """待组合的预设来源、参数覆盖及编排。"""

    instance_id: NonEmptyString
    source: PresetSource
    parameters: Omittable[JsonObject]
    layout: InstanceLayout
    timing: InstanceTiming


class ResolvedPresetInstance(ContractModel):
    """解析来源后保留的完整快照和有效参数。"""

    instance_id: NonEmptyString
    preset: PresetDraft
    parameters: JsonObject
    layout: InstanceLayout
    timing: InstanceTiming


class SpriteDraft(ComponentDefinition):
    """组合生成的源码、参数契约和实例快照。"""

    description: Description
    composition: Composition
    instances: list[ResolvedPresetInstance] = Field(min_length=1)


class SpriteRecord(SpriteDraft):
    """保存后的完整 sprite。"""

    sprite_id: SpriteId
    created_at: Timestamp

class SpriteComposeInput(ContractModel):
    """组合描述及显式编排的预设实例。"""

    description: Description
    instances: list[PresetInstance] = Field(min_length=1)


class SpriteComposeOutput(ContractModel):
    """尚未保存的完整组合结果。"""

    sprite: SpriteDraft



class SpriteCreateInput(ContractModel):
    """接收 compose 返回的完整组合定义。"""

    sprite: SpriteDraft


class SpriteCreateOutput(ContractModel):
    """新 sprite 记录及入库代码校验报告。"""

    sprite: SpriteRecord
    validation: CodeValidationReport



class TextPosition(ContractModel):
    """LSP 零基行号与 UTF-16 字符偏移。"""

    line: NonNegativeInteger
    character: NonNegativeInteger


class TextRange(ContractModel):
    """诊断范围，结束位置排他。"""

    start: TextPosition
    end: TextPosition


class CodeDiagnostic(ContractModel):
    """契约或 LSP 返回的一条诊断。"""

    source: Literal["contract", "lsp"]
    severity: Literal["error", "warning", "information", "hint"]
    message: str
    file: Omittable[str]
    code: Omittable[str]
    range: Omittable[TextRange]
    field: Omittable[str]


class CodeValidationInput(ContractModel):
    """需要校验的完整组件定义。"""

    component: ComponentDefinition


class CodeValidationReport(ContractModel):
    """代码校验结论与真实诊断。"""

    passed: bool
    diagnostics: list[CodeDiagnostic]



class TestScript(ContractModel):
    """Agent 提交的命名测试，code 内容仍为 TypeScript。

    名称以 Test 开头会被 pytest 当作测试类收集，显式关闭收集。
    """

    __test__ = False

    name: NonEmptyString
    code: str


class RenderValidationInput(ContractModel):
    """组件、测试时长、参数覆盖和自定义测试脚本。"""

    component: ComponentDefinition
    duration_frames: PositiveInteger
    parameters: Omittable[JsonObject]
    tests: list[TestScript] = Field(default_factory=list)


CheckStatus = Literal["passed", "failed", "error", "not_run"]


class CheckResult(ContractModel):
    """基础检查或脚本的执行状态。"""

    name: NonEmptyString
    status: CheckStatus
    message: Omittable[str]


class AssertionResult(ContractModel):
    """真实断言记录，actual/expected 可显式为 JSON null。"""

    message: str
    passed: bool
    actual: JsonValue = None
    expected: JsonValue = None


class TestScriptResult(CheckResult):
    """单份脚本的执行状态与断言记录。"""

    assertions: list[AssertionResult]


class RenderValidationReport(ContractModel):
    """本次运行校验报告，不代表未覆盖行为通过。"""

    passed: bool
    composition: Composition
    code_validation: CodeValidationReport
    checks: list[CheckResult]
    tests: list[TestScriptResult]
    custom_tests_executed: NonNegativeInteger



ToolName = Literal[
    "image.info", "image.resize", "image.crop",
    "preset.search", "preset.create", "preset.modify",
    "validate.code", "validate.render", "sprite.compose", "sprite.create",
    "tools.inspect", "tools.plan_execute",
]


class ToolInspectInput(ContractModel):
    """点号 ID 或模型可见下划线名；未知名称返回 TOOL_NOT_FOUND。"""

    tool_name: NonEmptyString


class ToolExample(ContractModel):
    """符合契约的完整 JSON 输入输出示例。"""

    input: JsonObject
    output: JsonObject


class ToolDescriptor(ContractModel):
    """工具模型及不能只靠 Schema 表达的行为规则。"""

    tool_name: ToolName
    contract_version: PositiveInteger = 1
    description: Description
    input_schema: JsonSchema
    output_schema: JsonSchema
    constraints: list[str]
    side_effects: list[str]
    error_codes: list[str]
    examples: list[ToolExample]




# Resolve documented forward references only after every model is declared.
for _model in tuple(globals().values()):
    if isinstance(_model, type) and issubclass(_model, ContractModel):
        _model.model_rebuild()
