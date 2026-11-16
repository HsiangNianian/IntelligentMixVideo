"""Workspace tool contracts and historical Sprite serialization; retired tool input models are not retained."""
from __future__ import annotations

from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import (
    BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StrictFloat,
    StrictInt, StrictStr, StringConstraints,
)
from pydantic.json_schema import SkipJsonSchema

from ..builtin_effects import EffectBuiltin

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


# 文字组合里 Preset 担任的角色：text 画一栏文字（宿主传入文字、切分好的片段、关键词组件与字体），keyword 画一个关键词命中，
# effect 包住文字块的某个位置画入场、出场或持续特效。不填的是自由组件（滤镜、特效，以及更早按固定框组合的字效）。
PresetRole = Literal["text", "keyword", "effect"]
# 宿主传给各角色 Preset 的 prop：它们不属于 Preset 自己的参数，不能写进 parameter_schema。effect 收到被包住的内容 children、
# 进度 progress（enter/exit 的 0 为隐藏、1 为完整显示；cue 的 0 为初态、1 为终态）与宿主的外层元素 Box（块级或行内）。
# text Preset 可以画宿主排好的这一栏 children（关键词命中已由宿主包好，逐字特效时每个字是一个单元），也可以按 segments 自己画；
# 只有前者能用逐字位置。keyword Preset 同样可以画宿主交来的这个命中的各个字 children（逐字特效时每个字是一个单元），
# 只画 text 的旧写法没有关键词逐字位置。effect 另收 index、count：逐字位置是这个字在本栏该位置的序号与字数，各栏时是栏序号与栏数，其余为 0 与 1。
# completedCount 是本栏该位置中局部进度达到 1 的单元个数，所有单元共享；非逐字位置完成时为 count、此前为 0。
ROLE_PROPS: dict[str, tuple[str, ...]] = {
    "text": ("text", "segments", "Keyword", "children", "fontFamily", "fontWeight", "fontSize"),
    "keyword": ("text", "children"),
    "effect": ("children", "progress", "Box", "index", "count", "completedCount"),
}


class EffectUsage(ContractModel):
    """特效 Preset 的默认用法：中文名称、类型、作用位置与（入场、出场、下栏出现的）时长；组合时特效条目没给的就取这里的，
    做成功的特效因此只要检索、引用就能原样复用。"""

    title: EffectTitle
    slot: EffectSlot
    target: EffectTarget
    seconds: Omittable[Annotated[FiniteNumber, Field(ge=0.1, le=3)]]
    # 逐字位置的入场、出场与下栏出现须明确选定；组合时可覆盖，旧配方缺失时由组合条目给出。
    stagger: Omittable[Annotated[FiniteNumber, Field(ge=0, le=1)]]


class PresetDraft(ComponentDefinition):
    """尚未入库的完整预设，可标记直接来源（组合时取自库内记录的快照记下那条记录）、文字组合里的角色与特效的默认用法。"""

    description: Description
    source_preset_id: Omittable[PresetId]
    role: Omittable[PresetRole]
    usage: Omittable[EffectUsage]


class PresetRecord(PresetDraft):
    """通过入库代码校验后保存的预设。"""

    preset_id: PresetId
    created_at: Timestamp

# create 承接 Agent 已写好的完整定义；输入模型与 PresetDraft 完全相同。


# Read-only library contracts; historical roles and usage remain searchable.

# create 承接 Agent 已写好的完整定义；输入模型即 PresetDraft。

class PresetSearchInput(ContractModel):
    """可选关键词（分词后按命中的词数排序，一个词都没命中的不列出）、角色、特效类型与结果数量上限；同分时生成过成功版本、存进资产库的
    排在前面，再按时间由新到旧；留空则按这个顺序列出全部；给出 preset_id 则读取该完整记录。"""

    query: str = ""
    role: Omittable[PresetRole]
    slot: Omittable[EffectSlot]
    limit: Annotated[PositiveInteger, Field(le=100)] = 20
    preset_id: Omittable[PresetId]


class PresetSummary(ContractModel):
    """供 Agent 自行挑选的预设摘要；完整记录用 preset_id 再取。"""

    preset_id: PresetId
    description: Description
    parameter_names: list[str]
    role: Omittable[PresetRole]
    usage: Omittable[EffectUsage]
    # 用到它的成功版本数，以及其中被存进资产库的版本数。
    accepted_uses: NonNegativeInteger = 0
    published_uses: NonNegativeInteger = 0


class PresetSearchOutput(ContractModel):
    """摘要列表，允许为空。"""

    presets: list[PresetSummary]
    preset: Omittable[PresetRecord]
    has_more: bool = False


class CodeEdit(ContractModel):
    """副本代码里的一处精确替换：old_str 从源代码原样复制，且在当时的代码中恰好出现一次。"""

    old_str: NonEmptyString
    new_str: str


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


class ResolvedPresetInstance(ContractModel):
    """解析来源后保留的完整快照和有效参数；宿主内置特效（builtin）没有 Preset 源码。"""

    instance_id: NonEmptyString
    preset: PresetDraft
    parameters: JsonObject
    layout: InstanceLayout
    timing: InstanceTiming
    # 历史文字块的内置特效标记，其完整实现已封存在 Sprite 源码中。
    builtin: Omittable[EffectBuiltin]


# 效果分类：text 为字效，filter_overlay 为滤镜，video_overlay 与 transition_overlay 合为转场与视频特效。
SpriteKindName = Literal["text", "filter_overlay", "video_overlay", "transition_overlay"]
# 顶层参数名：驼峰标识符，可安全用作 JSON 键和界面控件 ID。
ParameterName = Annotated[StrictStr, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9]{0,31}$")]


class ParameterTarget(ContractModel):
    """顶层参数写入的一个实例 prop。"""

    instance_id: NonEmptyString
    parameter: NonEmptyString


class SpriteParameterBinding(ContractModel):
    """把一个或多个实例的同名语义 prop 提升为 Sprite 顶层参数，供模板逐次摆放时修改。"""

    name: ParameterName
    title: Annotated[Description, Field(max_length=40)]
    targets: list[ParameterTarget] = Field(min_length=1)


class TextLayout(ContractModel):
    """文字块在画布上的区域：宿主在其中纵向居中排布各栏，宽度就是换行宽度。"""

    x: FiniteNumber
    y: FiniteNumber
    width: PositiveInteger
    height: PositiveInteger


# 特效的类型：enter 入场、exit 出场（宿主按配置的时长算显现进度），loop 持续（进度恒为 1，需要循环时自己读帧），
# cue 下栏出现（只有双栏：下栏出现前为 0，出现后按时长升到 1；这一组没有下栏文字时一直为 0）。
EffectSlot = Literal["enter", "exit", "loop", "cue"]
# 特效作用的位置：block 整个文字块，tiers 各行（各自从出现的帧起），line1～line4 指定物理行（必须存在），逐字位置 chars 每个字、
# keywordChars 关键词里的每个字、plainChars 关键词以外的每个字（依次分享入场、出场进度；只有画 children 的栏有，关键词逐字还要
# 关键词 Preset 画 children），keyword 每个关键词命中。
EffectTarget = Literal["block", "tiers", "line1", "line2", "line3", "line4", "chars", "keywordChars", "plainChars", "keyword"]
# 特效的中文名称，如「淡入」「发光」：宿主据此写它的开关、位置与时长参数的标题。
EffectTitle = Annotated[StrictStr, StringConstraints(strip_whitespace=True, min_length=1, max_length=8)]


class TextBlockEffect(ContractModel):
    """组合后记录的一个特效：实例、中文名称与类型；内置特效另记 builtin。位置、时长与开关是配置参数，默认值在 Sprite 的默认参数里。"""

    instance_id: NonEmptyString
    title: EffectTitle
    slot: EffectSlot
    # 历史元数据中的内置特效名，只保留反序列化，不再据此生成源码。
    builtin: Omittable[EffectBuiltin]


class TextBlock(ContractModel):
    """组合后记录的文字块结构：区域、各栏实例、关键词实例与特效；配置的默认值在 Sprite 的默认参数里。"""

    layout: TextLayout
    tiers: list[NonEmptyString] = Field(min_length=1, max_length=4)
    keyword: Omittable[NonEmptyString]
    effects: list[TextBlockEffect] = Field(default_factory=list)
    # 宿主把各栏定格在中间帧显示、文字块的动画都由特效 Preset 画（有可配置特效之后的作品；关键词命中照常播放）；
    # 更早的字效没有这一项，它们的 text Preset 自己画的动画照常播放。
    still: Omittable[Literal[True]]
    # 双语每组的第二行是同一句的英文；编辑保留双语标记与全部原文、译文。
    bilingual: Omittable[Literal[True]]
    # 历史文字块保存的预览关键词；转换时复制到 EffectContract。
    preview_keywords: Omittable[list[NonEmptyString]]


class SpriteDraft(ComponentDefinition):
    """组合生成的源码、参数契约和实例快照；字效的文字组合另记文字块结构，自由组件另记它的清单（effect.json）。"""

    description: Description
    composition: Composition
    instances: list[ResolvedPresetInstance] = Field(min_length=1)
    kind: Omittable[SpriteKindName]
    bindings: list[SpriteParameterBinding] = Field(default_factory=list)
    text: Omittable[TextBlock]
    # 自由组件（Agent 写的 Effect.tsx）的清单：编辑时据此和唯一实例的源码还原工作区。
    effect: Omittable[JsonObject]


class SpriteRecord(SpriteDraft):
    """保存后的完整 sprite。"""

    sprite_id: SpriteId
    created_at: Timestamp


class ReviewFrame(ContractModel):
    """回看的一帧：下一轮图片序号、Sprite 帧号与采样时机；按随后的观察消息从 1 编号。"""

    image: PositiveInteger
    frame: NonNegativeInteger
    label: NonEmptyString


class SpriteReview(ContractModel):
    """保存后供 Agent 对照要求查看的几帧（灰底合成、按内容区域裁切），以及之后还有几次回看。默认没有关键词的字效按示例关键词
    （优先文字块的 preview_keywords，与字效工作区预览相同；未指定时退回默认文字首个非空行开头至多两个字，与排版检查 text_fit 的挂载相同）截取，keywords 列出它们。"""

    frames: list[ReviewFrame]
    rounds_left: NonNegativeInteger
    keywords: Omittable[list[str]]


# 自由组件字效：Agent 在工作区里写 Effect.tsx（默认导出组件，可从 "imv" 导入内容运行时）与 effect.json（清单），宿主据清单
# 生成配置参数、打包成唯一实例的 Sprite 并检查行为；不再有 tiers/keyword/effect 角色组合。
WorkspacePath = Literal["Effect.tsx", "effect.json"]


class EffectFont(ContractModel):
    """一行文字的字体库家族名、字重（400 或 700）与相对 font_size 的字号比例（多行才用，0.4～2）。"""

    family: NonEmptyString
    weight: Integer = 400
    size_ratio: Omittable[FiniteNumber]


class EffectText(ContractModel):
    """字效的示例内容与配置默认值。sample 是预览用的示例文字：一行；双栏两行；双语为中英交替的两行或四行。
    keywords 是示例里要当关键词显示的词，line_starts 是各行出现的帧（多行才用），keyword_look 表示组件有关键词外观，
    keyword_enabled 保留该外观的 keywordEffect 默认开关；关闭开关不移除能力或参数。"""

    sample: NonEmptyString
    keywords: list[NonEmptyString] = Field(default_factory=list, max_length=5)
    line_starts: Omittable[list[NonNegativeInteger]]
    bilingual: StrictBool = False
    keyword_look: StrictBool = False
    keyword_enabled: StrictBool = True
    font_size: FiniteNumber = 96.0
    fonts: list[EffectFont] = Field(min_length=1, max_length=4)
    line_gap: Omittable[FiniteNumber]
    arrangement: Literal["stack", "overlap"] = "stack"
    translation_gap: Omittable[FiniteNumber]


class EffectParameter(ContractModel):
    """组件自己的一个样式参数：名称、JSON Schema、默认值、面板标题与分组（同组参数在面板里成一张卡片）。"""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, populate_by_name=True)

    name: ParameterName
    definition: JsonObject = Field(alias="schema")
    default: JsonValue
    title: Omittable[Annotated[StrictStr, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]]
    group: Omittable[Annotated[StrictStr, StringConstraints(strip_whitespace=True, min_length=1, max_length=16)]]


class EffectBounds(ContractModel):
    """An existing component's content region; fractional unions retain the publication's transform pivot."""

    x: FiniteNumber
    y: FiniteNumber
    width: Annotated[FiniteNumber, Field(gt=0)]
    height: Annotated[FiniteNumber, Field(gt=0)]


class EffectContract(ContractModel):
    """An existing component's complete parameter contract, retained when opening its bundled source for editing.

    Unlike a new effect's generated controls, this may include nested instance props and local schema references.
    Keeping the contract together preserves both manual edits and callers of previously published assets.
    """

    parameter_schema: JsonSchema
    default_parameters: JsonObject
    preview_keywords: list[NonEmptyString] = Field(default_factory=list)
    content_box: Omittable[EffectBounds]
    # Host-owned legacy cue switches, disabled only while measuring text layout; never saved as actual defaults.
    text_fit_parameters: Omittable[dict[str, Literal[False]]]


class EffectManifest(ContractModel):
    """effect.json：效果分类、描述、内容区域（画布上的框，宿主的整体变换围绕它）、字效的示例内容与配置，以及样式参数。"""

    kind: SpriteKindName
    description: Description
    layout: TextLayout
    text: Omittable[EffectText]
    params: list[EffectParameter] = Field(default_factory=list, max_length=32)
    # Converted source already owns the entire canvas and its parameter mapping; no old composer runs again.
    contract: Omittable[EffectContract]


# 只读的运行时源码：Agent 可以读 `imv` 的类型与实现，但不能改它。
RuntimePath = Literal["imv-runtime.tsx"]


class WorkspaceReadInput(ContractModel):
    """分段读取工作区、历史版本或只读运行时源码；offset 是从零开始的 Unicode 字符位置，续读用回执的 next_offset。"""

    path: WorkspacePath | RuntimePath
    version: Omittable[PositiveInteger]
    offset: NonNegativeInteger = 0


class WorkspaceFile(ContractModel):
    """文件的一段内容与全文件行数；next_offset 缺省表示已读至末尾，不能把分段内容当成完整文件写回。"""

    path: WorkspacePath | RuntimePath
    content: str
    lines: NonNegativeInteger
    offset: NonNegativeInteger = 0
    next_offset: Omittable[PositiveInteger]


class WorkspaceWriteInput(ContractModel):
    """整份写入一个文件。"""

    path: WorkspacePath
    content: Annotated[StrictStr, Field(min_length=1, max_length=200_000)]


class WorkspaceEditInput(ContractModel):
    """按顺序精确替换一个文件里的片段：每个 old_str 从文件当前内容原样复制，且恰好出现一次。"""

    path: WorkspacePath
    edits: list[CodeEdit] = Field(min_length=1, max_length=32)


class WorkspaceWriteOutput(ContractModel):
    """写入后的行数、相对写入前删去与新增的行数，以及宿主的说明（例如清单的自动修正）。"""

    path: WorkspacePath
    lines: NonNegativeInteger
    lines_removed: NonNegativeInteger
    lines_added: NonNegativeInteger
    notes: list[str] = Field(default_factory=list)


PreviewVariant = Literal["default", "long", "short", "long_duration"]


class PreviewRenderInput(ContractModel):
    """渲染工作区当前的效果：要看的帧（省略时宿主按画面变化挑选关键帧，并含首尾可见帧与中间帧）、内容变体（long 把文字填到 100 字，
    short 按 1.5 秒、long_duration 按 120 秒的长度挂载）与要当关键词显示的词（省略时用清单的示例关键词）。"""

    frames: Omittable[Annotated[list[NonNegativeInteger], Field(min_length=1, max_length=8)]]
    variant: PreviewVariant = "default"
    keywords: Omittable[Annotated[list[NonEmptyString], Field(max_length=5)]]


class PreviewRenderOutput(ContractModel):
    """这次挂载的长度、从运行中的代码量出的运动事实，与截到的帧；帧号是随后的观察消息内的 1..n，灰底不是内容。"""

    duration_frames: PositiveInteger
    motion: list[str] = Field(default_factory=list)
    frames: list[ReviewFrame]
    keywords: Omittable[list[str]]
    notes: list[str] = Field(default_factory=list)


class EffectProblem(ContractModel):
    """门禁发现的一个问题：哪项检查、说明与修复方向，能定位时附源码行号与该行内容。"""

    check: NonEmptyString
    message: str
    line: Omittable[PositiveInteger]
    excerpt: Omittable[str]


class EffectCheckInput(ContractModel):
    """检查工作区当前的效果，不保存。"""


class EffectCheckOutput(ContractModel):
    """门禁结论、问题清单与宿主的说明。"""

    passed: StrictBool
    problems: list[EffectProblem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class EffectSaveInput(ContractModel):
    """检查并保存工作区当前的效果为本任务的候选。"""


class EffectSaveOutput(ContractModel):
    """保存的候选、从运行中的代码量出的运动事实，与它的中间帧；中间帧在随后的观察消息里编号为 1。"""

    sprite_id: SpriteId
    created_at: Timestamp
    composition: Composition
    motion: list[str] = Field(default_factory=list)
    review: SpriteReview
    notes: list[str] = Field(default_factory=list)


class ImageInspectInput(ContractModel):
    """放大查看一张参考图（序号从 1 起）的局部：x、y、width、height 是 0～1 的比例，省略时看整张。"""

    image: PositiveInteger
    x: Annotated[FiniteNumber, Field(ge=0, le=1)] = 0.0
    y: Annotated[FiniteNumber, Field(ge=0, le=1)] = 0.0
    width: Annotated[FiniteNumber, Field(gt=0, le=1)] = 1.0
    height: Annotated[FiniteNumber, Field(gt=0, le=1)] = 1.0


class ImageInspectOutput(ContractModel):
    """裁出的局部在随后观察消息内的序号（固定为 1）、像素尺寸，以及原图尺寸。"""

    image: PositiveInteger
    width: PositiveInteger
    height: PositiveInteger
    source_width: PositiveInteger
    source_height: PositiveInteger


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
    excerpt: Omittable[str]


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
    "tools.inspect", "tools.plan_execute", "tools.report_step",
    "workspace.read", "workspace.write", "workspace.edit", "preview.render", "validate.check", "effect.save",
    "library.search", "image.inspect",
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
