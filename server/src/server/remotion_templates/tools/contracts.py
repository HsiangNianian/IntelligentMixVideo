"""PR76 工具契约：严格校验入出参，为装饰器提供完整 Schema；业务实现位于同目录。"""
from __future__ import annotations

from typing import Annotated, Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import (
    BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StrictFloat,
    StrictInt, StrictStr, StringConstraints, model_validator,
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
# 任务内草稿句柄：只在创建它的任务会话中有效。
DraftId = NonEmptyString
# 草稿内容摘要：内容相同则摘要相同，与每次随机生成的 draft_id 无关。
DraftDigest = NonEmptyString
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
PresetCreateInput = PresetDraft

class PresetCreateOutput(ContractModel):
    """新记录和本次入库的代码校验报告。"""

    preset: PresetRecord
    validation: CodeValidationReport



# 工具入参以文档化的 JSON 字符串传递；宿主内部继续用 UUID 对象引用同一素材。
AssetId = Annotated[UUID, BeforeValidator(lambda value: value if isinstance(value, UUID) else UUID(str(value)))]


# 用户上传的参考图由服务端登记为参考素材；图片工具按该引用读取，不使用 URL。
class ImageReference(ContractModel):
    """已上传并登记的参考图片。"""

    asset_id: AssetId


# 图片工具直接读取任务参考图：用户上传后已由服务端登记为参考素材，无需 URL。
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


class PresetChanges(ContractModel):
    """仅替换显式提供的字段；edits 按顺序在源代码上精确替换，与整份 code 互斥；工具还须检查至少提供一项。"""

    code: Omittable[str]
    edits: Omittable[Annotated[list[CodeEdit], Field(min_length=1)]]
    description: Omittable[Description]
    parameter_schema: Omittable[JsonSchema]
    default_parameters: Omittable[JsonObject]
    usage: Omittable[EffectUsage]


class PresetModifyInput(ContractModel):
    """原记录 ID 和 Agent 已经改好的替换内容。"""

    preset_id: PresetId
    changes: PresetChanges


class PresetDraftSummary(ContractModel):
    """宿主保存的预设副本摘要；完整源码不回传，由 draft_id 引用。"""

    description: Description
    parameter_names: list[str]
    source_preset_id: Omittable[PresetId]
    role: Omittable[PresetRole]


class PresetModifyOutput(ContractModel):
    """副本句柄与摘要，不含新记录 ID；source_preset_id 指向原记录。

    改动行数相对被复制的源代码计算，让小需求被整份重写时在回执里可见；notice 是宿主的固定提示。
    """

    draft_id: DraftId
    digest: DraftDigest
    preset: PresetDraftSummary
    code_lines_removed: NonNegativeInteger
    code_lines_added: NonNegativeInteger
    notice: Omittable[str]


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
    """引用本任务内 preset.modify 返回或宿主登记（编辑基线）的预设副本。"""

    kind: Literal["draft"]
    draft_id: DraftId


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
    """解析来源后保留的完整快照和有效参数；宿主内置特效（builtin）没有 Preset 源码。"""

    instance_id: NonEmptyString
    preset: PresetDraft
    parameters: JsonObject
    layout: InstanceLayout
    timing: InstanceTiming
    # 文字块里由宿主自己画的内置特效（fade、glow…）；有它时 preset 只是占位，不参与打包。
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


class TextTier(ContractModel):
    """文字块的一栏：画这一栏的 text 角色 Preset、它的样式参数，以及这一栏的字体、字重与字号比例（配置的默认值）。"""

    instance_id: NonEmptyString
    source: PresetSource
    parameters: Omittable[JsonObject]
    font_family: NonEmptyString
    font_weight: Literal[400, 700]
    # 这一栏按 fontSize 乘以它渲染；只有双栏使用，单栏恒为 1。
    size_ratio: Omittable[Annotated[FiniteNumber, Field(ge=0.4, le=2)]]


class TextKeyword(ContractModel):
    """关键词字效：画命中词的 keyword 角色 Preset 及其样式参数。"""

    instance_id: NonEmptyString
    source: PresetSource
    parameters: Omittable[JsonObject]


# 特效的类型：enter 入场、exit 出场（宿主按配置的时长算显现进度），loop 持续（进度恒为 1，需要循环时自己读帧），
# cue 下栏出现（只有双栏：下栏出现前为 0，出现后按时长升到 1；这一组没有下栏文字时一直为 0）。
EffectSlot = Literal["enter", "exit", "loop", "cue"]
# 特效作用的位置：block 整个文字块，tiers 各行（各自从出现的帧起），line1～line4 指定物理行（必须存在），逐字位置 chars 每个字、
# keywordChars 关键词里的每个字、plainChars 关键词以外的每个字（依次分享入场、出场进度；只有画 children 的栏有，关键词逐字还要
# 关键词 Preset 画 children），keyword 每个关键词命中。
EffectTarget = Literal["block", "tiers", "line1", "line2", "line3", "line4", "chars", "keywordChars", "plainChars", "keyword"]
# 特效的中文名称，如「淡入」「发光」：宿主据此写它的开关、位置与时长参数的标题。
EffectTitle = Annotated[StrictStr, StringConstraints(strip_whitespace=True, min_length=1, max_length=8)]


class TextEffect(ContractModel):
    """文字块的一个特效：一个 effect 角色 Preset（source）或宿主内置特效（builtin）其一，加样式参数、中文名称、类型，
    以及作用位置、时长与开关（配置的默认值）；名称、类型、位置与时长省略时取 Preset 的 usage 或内置特效的默认值。"""

    instance_id: NonEmptyString
    source: Omittable[PresetSource]
    # 宿主内置特效的名字（fade、rise、pop、slide*、blur、glow、breathing）：只写名字，宿主自己画，不必写 Preset。
    builtin: Omittable[EffectBuiltin]
    parameters: Omittable[JsonObject]
    title: Omittable[EffectTitle]
    slot: Omittable[EffectSlot]
    target: Omittable[EffectTarget]
    # 入场、出场的时长（秒），省略为 0.5；持续特效没有时长。
    seconds: Omittable[Annotated[FiniteNumber, Field(ge=0.1, le=3)]]
    # 入场、下栏出现特效推迟开始的秒数，省略为 0，让特效可以一个接一个；出场与持续特效没有延迟。
    delay: Omittable[Annotated[FiniteNumber, Field(ge=0, le=3)]]
    # 逐字位置的特效里各个字错开的程度（0～1）：1 一个接一个，0 所有字一起，介于其间则前后交叠。
    # 非持续的逐字特效必须在这里或 Preset 的 usage 中明确选择，不按位置暗中推断。
    stagger: Omittable[Annotated[FiniteNumber, Field(ge=0, le=1)]]
    # 是否默认开启，省略为开启；编辑时带回用户关掉的状态。
    enabled: Omittable[StrictBool]

    @model_validator(mode="after")
    def one_source(self):
        """An effect is drawn by exactly one of an effect Preset (source) or a host built-in (builtin)."""
        if (self.source is None) == (self.builtin is None):
            raise ValueError("give exactly one of source (an effect Preset) or builtin (a host built-in effect such as fade)")
        return self


class TextComposition(ContractModel):
    """字效的文字组合：单行、普通双栏或一至两组双语，各物理行交给 text Preset 画，关键词命中交给 keyword Preset 画，
    特效交给 effect Preset 包住它们的位置。"""

    layout: TextLayout
    # 默认文字：单栏一行，双栏为「上栏\n下栏」；双语字效为「中文\n同一句的英文译文」（英文由总线按切片注入 `textEn`）。
    text: Annotated[StrictStr, StringConstraints(min_length=1, max_length=100)]
    font_size: Annotated[FiniteNumber, Field(ge=24, le=240)]
    tiers: list[TextTier] = Field(min_length=1, max_length=4)
    # 双语每组为中文及其英文译文，宿主分别用 text 与 textEn 保存各组原文、译文（多组用换行连接）。
    bilingual: Omittable[Literal[True]]
    # 双栏双语用四行「中文1\n英文1\n中文2\n英文2」；组内中英间距与两组之间的 line_gap 分开，省略为 8 像素。
    translation_gap: Omittable[Annotated[FiniteNumber, Field(ge=0, le=100)]]
    # 只有双栏使用：两栏的排布（stack 上下排列，省略时如此；overlap 两栏叠在同一处、彼此居中、下栏在上层）、
    # 两栏之间的距离（像素，可为负数以让下栏上移叠住上栏，最低 -600；重叠排布时不用，可省略）与各行出现的帧（首项为 0，不递减）。
    # 四行双语的排布与 line_gap 作用于整组；line_starts 同组中英相同，如 [0, 0, 30, 30]。
    arrangement: Omittable[Literal["stack", "overlap"]]
    line_gap: Omittable[Annotated[FiniteNumber, Field(ge=-600, le=300)]]
    line_starts: Omittable[list[Annotated[Integer, Field(ge=0, le=3600)]]]
    keyword: Omittable[TextKeyword]
    # 默认文字里哪些词是关键词（需求或参考图强调的词）：只用于预览，模板没传关键词时，字效工作区预览与回看帧以它们为关键词显示。
    preview_keywords: Omittable[Annotated[list[Annotated[StrictStr, StringConstraints(min_length=1, max_length=12)]], Field(min_length=1, max_length=5)]]
    # 按顺序包住各自位置的特效，先列的在里层；宿主按顺序给它们第 1、2…个特效的配置参数。
    effects: list[TextEffect] = Field(default_factory=list, max_length=6)
    duration_frames: PositiveInteger


class TextBlockEffect(ContractModel):
    """组合后记录的一个特效：实例、中文名称与类型；内置特效另记 builtin。位置、时长与开关是配置参数，默认值在 Sprite 的默认参数里。"""

    instance_id: NonEmptyString
    title: EffectTitle
    slot: EffectSlot
    # 宿主内置特效的名字；有它时编辑据此还原 builtin，而不是引用 base_* 草稿。
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
    # 默认文字里的示例关键词，见 TextComposition.preview_keywords。
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

class SpriteComposeInput(ContractModel):
    """组合描述、效果分类、显式编排的预设实例或字效的文字组合，以及提升为顶层的样式参数。"""

    description: Description
    kind: SpriteKindName
    instances: list[PresetInstance] = Field(default_factory=list)
    text: Omittable[TextComposition]
    parameters: list[SpriteParameterBinding] = Field(default_factory=list, max_length=32)


class InstanceSummary(ContractModel):
    """组合结果中一个实例的来源、参数名和编排；不含预设源码。"""

    instance_id: NonEmptyString
    preset_id: Omittable[PresetId]
    draft_id: Omittable[DraftId]
    parameter_names: list[str]
    layout: InstanceLayout
    timing: InstanceTiming


class SpriteComposeOutput(ContractModel):
    """宿主保存的组合草稿句柄与摘要；完整源码不回传，sprite.create 按 draft_id 保存。"""

    draft_id: DraftId
    digest: DraftDigest
    description: Description
    composition: Composition
    kind: SpriteKindName
    instances: list[InstanceSummary]
    parameter_names: list[str]
    default_parameters: JsonObject
    code_bytes: NonNegativeInteger


class SpriteCreateInput(ContractModel):
    """compose 返回的草稿句柄。"""

    draft_id: DraftId


class ReviewFrame(ContractModel):
    """回看的一帧：下一轮图片序号（参考图在前）、Sprite 帧号与采样时机；标签不代表视觉结论。"""

    image: PositiveInteger
    frame: NonNegativeInteger
    label: NonEmptyString


class SpriteReview(ContractModel):
    """保存后供 Agent 对照要求查看的几帧（灰底合成、按内容区域裁切），以及之后还有几次回看。默认没有关键词的字效按示例关键词
    （优先文字块的 preview_keywords，与字效工作区预览相同；未指定时退回默认文字首个非空行开头至多两个字，与排版检查 text_fit 的挂载相同）截取，keywords 列出它们。"""

    frames: list[ReviewFrame]
    rounds_left: NonNegativeInteger
    keywords: Omittable[list[str]]


class SpriteCreateOutput(ContractModel):
    """新 sprite 的 ID 与入库代码校验报告；完整记录由宿主保存。还有回看次数时附上它的几帧画面。"""

    sprite_id: SpriteId
    created_at: Timestamp
    composition: Composition
    validation: CodeValidationReport
    review: Omittable[SpriteReview]


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
    keywords 是示例里要当关键词显示的词，line_starts 是各行出现的帧（多行才用），keyword_look 表示组件给关键词画了自己的外观。"""

    sample: NonEmptyString
    keywords: list[NonEmptyString] = Field(default_factory=list, max_length=5)
    line_starts: Omittable[list[NonNegativeInteger]]
    bilingual: StrictBool = False
    keyword_look: StrictBool = False
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


class EffectManifest(ContractModel):
    """effect.json：效果分类、描述、内容区域（画布上的框，宿主的整体变换围绕它）、字效的示例内容与配置，以及样式参数。"""

    kind: SpriteKindName
    description: Description
    layout: TextLayout
    text: Omittable[EffectText]
    params: list[EffectParameter] = Field(default_factory=list, max_length=32)


# 只读的运行时源码：Agent 可以读 `imv` 的类型与实现，但不能改它。
RuntimePath = Literal["imv-runtime.tsx"]


class WorkspaceReadInput(ContractModel):
    """读取工作区里的一个文件，或只读的内容运行时源码 imv-runtime.tsx；给出 version 时读该历史版本的文件（序号见快照的 versions）。"""

    path: WorkspacePath | RuntimePath
    version: Omittable[PositiveInteger]


class WorkspaceFile(ContractModel):
    """文件内容与行数。"""

    path: WorkspacePath | RuntimePath
    content: str
    lines: NonNegativeInteger


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
    """这次挂载的长度、从运行中的代码量出的运动事实，与截到的帧（附在后续轮次的图片里，灰底不是内容）。"""

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
    """保存的候选、从运行中的代码量出的运动事实，与它的中间帧（附在后续轮次的图片里）。"""

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
    """裁出的局部在后续轮次图片里的序号与像素尺寸，以及原图尺寸。"""

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
