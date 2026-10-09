# Remotion Agent 工具函数契约

本文维护已注册工具的数据契约；注册契约不代表业务实现已启用，当前实现范围见 [服务端说明](../server/src/server/remotion_templates/README.md)。交付范围是函数职责、输入输出数据模型、约束、错误和示例；不包含业务实现代码。

## 1. 范围与已确认决定

- 上层采用 `ReActLoop(PlanLoop(ReActLoop))`。本文不定义框架、循环实现、模型调度、重试策略或工具注册 SDK，所有函数契约均与框架无关。
- Agent 理解用户描述和参考图片、编写或修改代码、填写参数、明确编排、编写需求相关测试；工具执行确定性的图片处理、检索、数据处理、组合、校验和存储。
- Agent 整体目标仍是代码和预览。预览界面、视频下载形式及产物展示不属于本文，不新增预览发布工具。
- 主画布固定为 **宽 1080、高 1920、9:16 竖屏、30 fps**。主画布宽高和帧率不是调用方可修改的配置。
- preset 为单文件 TSX、参数 Schema 和默认参数构成的代码原子；sprite 组合多个 preset 实例。
- preset.create 承接 Agent 已经写好的代码和描述。search 按描述通过 ChromaDB 语义检索。
- preset.modify 承接 Agent 修改后的字段，返回副本，不修改原记录、不自动入库。需要长期复用时再调用 create。
- sprite.compose 生成组合定义与代码，sprite.create 保存组合结果；本期不定义 sprite 嵌套组合。
- 两个 create 均在内部执行代码校验，通过后才入库。无需传入之前的校验回执，也不在工具内调用模型修复。
- validate 提供代码检查和运行行为测试。工具提供统一测试上下文，Agent 编写自定义断言；不截图、不从视频抽帧、不做视觉 Judge。
- 图片使用用户上传后由服务端登记的 `image.asset_id` 引用，不接收远程 URL。resize 强制缩放到目标宽高，**不保持比例**；crop 使用像素坐标。处理后返回新的素材引用，保留原图。
- 工具自描述只提供 inspect，暂不提供 find。

下文统一采用 `preset`、`create`、`inspect` 拼写。字段命名、公共结果结构和示例默认值在本文中具体化，供实施方按同一契约接入。

## 2. 术语与职责

| 术语 | 定义 |
| --- | --- |
| PresetDraft | 完整的预设代码定义，含描述、TSX、参数 Schema、默认参数；不代表已经入库或通过代码校验 |
| PresetRecord | 已通过入库代码校验并保存的预设，有唯一 preset_id |
| Preset 实例 | 一份预设在 sprite 中的一次使用，拥有独立实例 ID、参数、布局和时间；同一预设可实例化多次 |
| SpriteDraft | compose 生成的完整组合定义、TSX、参数 Schema、默认参数和画布时序；尚未保存 |
| SpriteRecord | 通过入库代码校验后保存的 sprite，有唯一 sprite_id |
| 代码校验通过 | 参数契约有效，LSP 未发现阻止运行的代码错误；不代表动画或视觉已经验收 |
| 行为测试通过 | 工具实际执行的基础检查和传入断言全部通过；仅说明本次覆盖的行为 |

description 用于表达预设或模板的能力和适用场景，不是命令。原始用户图片、描述如何转换为代码和断言由上层 Agent 负责。

仓库已有 [sprite.proto](../proto/imv/sprite/v1/sprite.proto)，其 Sprite 语义偏向旧 Agent 成功版本的发布。本文定义的新工具模型不自动映射该协议，不修改旧实现，也不承诺旧记录迁移。

## 3. 公共数据约定

### 3.1 类型与序列化

工具接口统一使用 **Python 3.12 + Pydantic 2** 表达。以下是文档内的模型声明与函数签名，不是工具实现；所有输入输出仍通过 JSON 传递，与上层 loop 框架无关。Remotion TSX 和浏览器端 TypeScript 测试脚本作为字符串传递，不因工具接口使用 Python 而改变语言。

```python
"""工具契约公共模型；仅描述数据校验和序列化，不执行业务操作。"""
from __future__ import annotations

from typing import Annotated, Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import (
    BaseModel, BeforeValidator, ConfigDict,
    Field, StrictBool, StrictFloat, StrictInt, StrictStr, StringConstraints,
    TypeAdapter,
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


# JSON 使用 UUID 字符串；宿主内部保留同一素材的 UUID 值。
AssetId = Annotated[UUID, BeforeValidator(lambda value: value if isinstance(value, UUID) else UUID(str(value)))]


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
```

- `Integer` 必须是 JSON 安全整数，其他数值必须有限；不接受 NaN、Infinity 或以字符串代替数值。
- ID 是工具生成的非空不透明字符串。调用方不依赖 ID 的格式或通过 ID 推导存储位置。
- 时间戳为带时区的 ISO 8601 字符串，工具统一返回 UTC。
- URL 为工具执行环境可访问的绝对 HTTP(S) 地址；不接受本地路径、Base64 内容或资源 ID 代替 URL。
- `Omittable[T]` 表示字段可省略，显式 null 会被拒绝；内部 None 仅作未传占位。具有业务默认值的字段直接写默认值，例如 `limit = 5`。参数对象内的 null 和断言 actual/expected 的 null 是合法 JSON 值，是否满足业务参数要求仍由 parameter_schema 决定。
- `code` 必须原样承接调用方提交的 TSX，不通过修改文案、参数或动画“修好”代码。compose 生成的是新的组合源码。
- `async def` 只表达可能等待 I/O，函数体省略号是文档占位，不规定 HTTP、MCP、队列或其他传输方式。

传输时使用 `model_dump(mode="json", exclude_unset=True)` 或对应的 `model_dump_json(exclude_unset=True)`，省略未传字段，同时保留显式 JSON null。不要使用全局 `exclude_none=True` 删除参数或断言中的合法 null。modify 通过 `changes.model_fields_set` 或 `changes.model_dump(exclude_unset=True)` 确定实际提供的字段。

各段 Python 声明共享本节导入和类型，按文档顺序理解；后文定义的类型使用延迟注解，模型全部声明后再 `model_rebuild()`。模型描述结构与字段级约束；Schema 合法性、来源存在性、实例唯一性、跨字段一致性等仍按各工具正文执行，不把 Pydantic 结构校验当作业务实现或 LSP 校验。严格校验及 Schema 导出的用法参见 [Pydantic 严格模式](https://docs.pydantic.dev/latest/concepts/strict_mode/)和 [JSON Schema 文档](https://docs.pydantic.dev/latest/concepts/json_schema/)。

### 3.2 成功、校验失败与执行错误

`ok` 表示工具是否完成其操作。对于 validate，发现被测代码或行为有问题，是正常检查结果，返回 `ok: true` 且 `data.passed: false`。

对于 create，代码不通过就不能完成保存，返回 `ok: false`、`CODE_VALIDATION_FAILED`，并在 `details.validation` 中提供代码校验报告。环境未就绪、图片下载失败等也返回 ToolError。

```json
{
  "ok": false,
  "error": {
    "code": "INVALID_ARGUMENT",
    "message": "width 必须为正整数。",
    "field": "/width"
  }
}
```

`field` 如有提供，使用输入模型的 JSON Pointer；错误不能伪装为可用结果。工具不自行决定上层是否重试，不返回模型编造的检查结论。

### 3.3 组件与参数契约

```python
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
```

| 字段 | 必填 | 约束与含义 |
| --- | --- | --- |
| code | 是 | 非空单文件 TSX；默认导出一个 React 组件，参数从 props 接收；外部宿主负责装载 |
| parameter_schema | 是 | 自包含 JSON Schema 2020-12，根为 object，明确声明可调参数并拒绝未知参数；引用仅在 Schema 文档内部解析 |
| default_parameters | 是 | 完整默认参数对象，必须通过 parameter_schema，并满足组件 props 类型 |
| description | 是 | 去除首尾空白后非空；说明功能、效果、可调内容及适用场景，作为语义检索内容 |
| source_preset_id | 否 | 副本直接来源的库内 preset ID，仅表示来源，不授权修改原记录 |
| preset_id / created_at | 出参 | 由 create 生成，调用方不能指定 |

代码可以实现文字、图片、视频、图形等 Remotion 内容，不延续旧系统“只能生成字效”的业务限制。运行环境提供 React/Remotion；本契约不支持随输入安装额外依赖或附带其他源码文件。具体依赖版本由实施方固定并随工具环境维护。

组件由宿主提供画布和时间上下文，不自行注册根 Composition。默认参数是可调用的参数组合，不保证通过所有用户需求测试。声明了参数但实现未正确使用，需要由行为测试发现，不能仅凭 Schema 存在就视为有效。

**参数覆盖统一规则：**以 default_parameters 为基底，递归合并对象；数组和标量整体替换。省略字段保留默认值，null 按普通值处理并接受 Schema 校验。合并后必须是完整合法参数；不隐式删除键或把不合法值强制转换为合法值。

此规则用于实例参数、validate 的参数覆盖、测试上下文的参数更新及 sprite 运行时 props。preset.modify 的字段替换另见第 6.3 节。

## 4. 工具目录

| 工具 | 输入模型 | 成功数据模型 | 主要副作用 |
| --- | --- | --- | --- |
| image.info | ImageInfoInput | ImageInfo | 读取图片 |
| image.resize | ImageResizeInput | ProcessedImage | 生成并存储新图片 |
| image.crop | ImageCropInput | ProcessedImage | 生成并存储新图片 |
| preset.search | PresetSearchInput | PresetSearchOutput | 语义检索，只读 |
| preset.create | PresetCreateInput | PresetCreateOutput | 代码校验、保存记录、建立可检索索引 |
| preset.modify | PresetModifyInput | PresetModifyOutput | 读取原记录，返回内存副本；不入库 |
| validate.code | CodeValidationInput | CodeValidationReport | 执行契约及 LSP 检查 |
| validate.render | RenderValidationInput | RenderValidationReport | 在测试环境运行组件与脚本 |
| sprite.compose | SpriteComposeInput | SpriteComposeOutput | 读取引用，生成组合定义与代码；不保存 sprite |
| sprite.create | SpriteCreateInput | SpriteCreateOutput | 代码校验并保存 sprite |
| tools.inspect | ToolInspectInput | ToolDescriptor | 读取工具契约 |
| tools.plan_execute（宿主控制） | PlanAction | JsonObject | 调整任务内的计划与执行状态 |

目录包含 11 个业务工具和 1 个宿主控制入口。计划控制由宿主处理，不进入业务工具注册器；检查契约不会授予调用权限。没有 tools.find、自动写代码工具或预览发布工具。后面的函数签名使用下划线作为文档中的语言标识符，实际工具名称以本表的点号形式为准。

## 5. 图片工具

### 5.1 公共图片结果与 info

```python
class ImageReference(ContractModel):
    """用户上传后已登记的本地素材引用。"""

    asset_id: AssetId


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
    """处理后的图片信息，以及供后续工具读取的新素材引用。"""

    image: ImageReference


async def image_info(request: ImageInfoInput) -> ToolResult[ImageInfo]:
    """读取图片信息；函数体由实施方提供。"""
    ...
```

`image.info` 读取真实图片，返回解码后的像素宽高、实际媒体类型、文件字节数和是否含 alpha 通道；has_alpha 不承诺存在实际透明像素。按素材 ID 读取实际文件，不得仅依据文件后缀推测格式。

本期图片操作以单帧 PNG、JPEG、WebP 为输入范围，其他格式或多帧图片明确返回 `UNSUPPORTED_IMAGE`，不默默取第一帧。尺寸和裁剪坐标均以应用图片方向元数据后的可见图像为准；size_bytes 为读取的原文件字节数。

输入示例：

```json
{"image":{"asset_id":"3f2504e0-4f89-41d3-9a0c-0305e82c3301"}}
```

成功示例：

```json
{
  "ok": true,
  "data": {
    "width": 1200,
    "height": 800,
    "mime_type": "image/png",
    "size_bytes": 240000,
    "has_alpha": true
  }
}
```

上述素材 ID 和字节数只是结构示例，实际结果必须读取文件后产生。

### 5.2 resize

```python
class ImageResizeInput(ImageInfoInput):
    """指定强制缩放后的目标像素宽高。"""

    width: PositiveInteger
    height: PositiveInteger


async def image_resize(request: ImageResizeInput) -> ToolResult[ProcessedImage]:
    """缩放并返回新图片信息；函数体由实施方提供。"""
    ...
```

width、height 都必填且为正整数。输出像素尺寸必须精确等于输入目标，允许拉伸，不自动保持比例、不补边、不裁剪。

```json
{
  "image": {"asset_id": "3f2504e0-4f89-41d3-9a0c-0305e82c3301"},
  "width": 600,
  "height": 600
}
```

若原图为 1200×800，结果就是 600×600。成功返回 ProcessedImage，其中 image.asset_id 指向新图片，width、height 均为 600。

### 5.3 crop

```python
class ImageCropInput(ImageInfoInput):
    """相对原图左上角的像素裁剪区域。"""

    x: NonNegativeInteger
    y: NonNegativeInteger
    width: PositiveInteger
    height: PositiveInteger


async def image_crop(request: ImageCropInput) -> ToolResult[ProcessedImage]:
    """裁剪并返回新图片信息；函数体由实施方提供。"""
    ...
```

- x、y 为相对可见图片左上角的非负整数像素；width、height 为正整数。
- 裁剪区域是 `[x, x + width) × [y, y + height)`。
- `x + width <= 原图宽度`，`y + height <= 原图高度`；越界返回 `CROP_OUT_OF_BOUNDS`，不静默截短或补边。
- 裁剪不缩放，输出尺寸等于裁剪宽高。

```json
{
  "image": {"asset_id": "3f2504e0-4f89-41d3-9a0c-0305e82c3301"},
  "x": 100,
  "y": 50,
  "width": 400,
  "height": 300
}
```

resize/crop 统一生成 PNG，保留可用 alpha，存储完成后才返回可供后续工具读取的新素材引用；该引用不得在函数返回后立即失效。原图不被覆盖。本地素材存储实现和部署资源上限由实施方维护，不增加 Agent 可调的图片质量或存储后端字段。

公共错误：`INVALID_ARGUMENT`、`IMAGE_FETCH_FAILED`、`UNSUPPORTED_IMAGE`、`IMAGE_DECODE_FAILED`、`IMAGE_PROCESSING_FAILED`、`IMAGE_STORE_FAILED`、`RESOURCE_LIMIT_EXCEEDED`、`TIMEOUT`。info 不产生处理或存储错误，crop 额外可能返回 `CROP_OUT_OF_BOUNDS`。

## 6. Preset 工具

### 6.1 search

```python
class PresetSearchInput(ContractModel):
    """自然语言检索描述与结果数量上限。"""

    query: Description
    limit: PositiveInteger = 5


class PresetSearchMatch(ContractModel):
    """按相关性排序的完整预设记录。"""

    rank: PositiveInteger
    preset: PresetRecord


class PresetSearchOutput(ContractModel):
    """检索结果列表，允许为空。"""

    matches: list[PresetSearchMatch]


async def preset_search(request: PresetSearchInput) -> ToolResult[PresetSearchOutput]:
    """按描述检索预设；函数体由实施方提供。"""
    ...
```

- query 为去除首尾空白后非空的自然语言需求；limit 为正整数，省略时为 5。
- 以创建时的 description 为预设语义内容，通过 ChromaDB 返回按相关性排序的结果，最多 limit 条，rank 从 1 连续递增。
- 每条结果必须包含完整代码、Schema 和默认参数，以便 Agent 阅读、改写或实例化。
- 匹配结果是候选能力，不表示已经符合当前用户需求。排序不承诺统一的相似度百分比，不暴露含义未统一的“置信度”。
- 没有结果返回 `matches: []`；不会自动创建预设。查询不得写入预设库。

```json
{"query":"可以设置文字和颜色的渐显标题，入场使用淡入动画","limit":5}
```

成功数据形状为 `{"matches": [{"rank": 1, "preset": <完整 PresetRecord>} ]}`。这里的尖括号是说明，实际调用必须提供完整 JSON 对象。

错误：`INVALID_ARGUMENT`、`SEARCH_UNAVAILABLE`、`TIMEOUT`。向量生成、集合配置和排序实现由实施方维护，不作为 Agent 的业务入参。

### 6.2 create

```python
PresetCreateInput = PresetDraft


class PresetCreateOutput(ContractModel):
    """新记录和本次入库的代码校验报告。"""

    preset: PresetRecord
    validation: CodeValidationReport


async def preset_create(request: PresetCreateInput) -> ToolResult[PresetCreateOutput]:
    """校验并保存 Agent 提交的预设；函数体由实施方提供。"""
    ...
```

输入各字段见第 3.3 节。工具承接 Agent 已写好的完整定义，不根据 description 生成代码。

执行约束：

1. 检查描述、Schema、默认参数及来源字段。source_preset_id 若存在，必须引用真实库记录。
2. 内部执行与 validate.code 相同的契约及 LSP 检查。
3. 无阻断错误后保存新记录，并使其描述可用于 ChromaDB 检索。
4. 返回新 preset_id、created_at、完整内容和通过的代码校验报告；警告可以保留。

校验不通过不写库，返回 `CODE_VALIDATION_FAILED`，`details.validation` 为 CodeValidationReport。保存或索引失败不返回成功，不让半完成记录成为可搜索的预设。

create 始终创建新记录，不覆盖来源预设，也不把相同描述视为同一份预设。本期没有新增业务去重或自动 upsert 语义。

输入示例：

```json
{
  "description": "可设置文字和颜色的静态文字原子",
  "code": "/** 静态文字预设，由宿主提供画布。 */\nimport React from 'react';\n/** 使用调用方传入的文字与颜色。 */\nexport default function Label(props: {text: string; color: string}) { return <div data-testid=\"label\" style={{color: props.color}}>{props.text}</div>; }",
  "parameter_schema": {
    "type": "object",
    "properties": {
      "text": {"type": "string"},
      "color": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"}
    },
    "required": ["text", "color"],
    "additionalProperties": false
  },
  "default_parameters": {"text": "示例文字", "color": "#FFFFFF"}
}
```

错误：`INVALID_ARGUMENT`、`PRESET_NOT_FOUND`、`CODE_VALIDATION_FAILED`、`VALIDATION_UNAVAILABLE`、`PRESET_STORE_FAILED`、`SEARCH_UNAVAILABLE`、`TIMEOUT`。

### 6.3 modify

```python
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
    """完整副本，不含新记录 ID。"""

    preset: PresetDraft


async def preset_modify(request: PresetModifyInput) -> ToolResult[PresetModifyOutput]:
    """读取原记录并返回修改副本；函数体由实施方提供。"""
    ...
```

- preset_id 来自 search；工具读取该记录，changes 至少包含一个字段。
- changes.code 是 Agent **已经修改完的完整源码**，不是自然语言修改要求，也不是待工具应用的文本 diff。
- changes 中每个字段整体替换原字段；未传字段保留原值。特别是 Schema 和 default_parameters 整体替换，不递归拼接旧字段。
- 合并后检查数据结构、Schema 和默认参数的一致性，返回完整 PresetDraft，并令 source_preset_id 为输入 preset_id。
- 工具不执行模型调用、不写库、不创建新的 preset_id，不承诺 LSP 或行为检查已通过。可单独调用 validate 获取诊断。
- 修改副本可以直接交给 compose；需要检索复用时，将完整副本交给 create。

```json
{
  "preset_id": "preset_label_001",
  "changes": {
    "description": "带白色描边、文字和颜色可调的文字原子",
    "code": "/** 带描边的文字预设副本。 */\nimport React from 'react';\n/** 保留文字与颜色参数，增加描边。 */\nexport default function Label(props: {text: string; color: string}) { return <div data-testid=\"label\" style={{color: props.color, WebkitTextStroke: '1px white'}}>{props.text}</div>; }"
  }
}
```

错误：`INVALID_ARGUMENT`、`PRESET_NOT_FOUND`。返回副本不能使原记录的代码、描述、默认参数或向量索引发生变化。

## 7. Sprite 组合与保存

### 7.1 画布、时间与实例模型

```python
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
```

| 字段 | 约束 |
| --- | --- |
| instance_id | 非空，同一 sprite 内唯一；用作参数分组键，不作为可执行变量拼接；保留 `__proto__`、`prototype`、`constructor` 不可用 |
| source | stored 引用已保存预设；draft 携带完整副本，两种不能混用 |
| parameters | 参数覆盖；省略时使用该预设默认值，按第 3.3 节规则合并后校验 |
| layout.x / y | 主画布像素坐标，表示实例容器左上角；允许负值及部分出画，用于进出场 |
| layout.width / height | 局部画布像素宽高，正整数；不是改变主画布规格 |
| layout.z_index | 整数，数值越大越靠前；相同时按输入数组顺序，后者在前 |
| timing.start_frame | 非负整数，位于主时间轴 |
| timing.duration_frames | 正整数；结束位置为 start_frame + duration_frames，结束帧不显示 |
| composition.duration_frames | 工具计算的正整数，等于所有实例结束位置的最大值 |

实例之间可以同时显示和重叠；compose 不自动布局、避让、缩放时间或判断哪种构图好看。画布外内容不出现在成片中；实例容器以自身局部画布为显示边界。

在主帧 `F`，实例只在 `start_frame <= F < start_frame + duration_frames` 时显示。实例观察到的局部帧为 `F - start_frame`，局部尺寸为 layout 的 width/height，局部时长为自身 duration_frames，帧率仍为 30。

这是一项必须实现和测试的上下文语义：仅给组件套一个指定宽高的 DOM 容器，并不自动证明组件读取到的 Remotion 配置和时间已经正确切换。

### 7.2 compose

```python
class SpriteComposeInput(ContractModel):
    """组合描述及显式编排的预设实例。"""

    description: Description
    instances: list[PresetInstance] = Field(min_length=1)


class SpriteComposeOutput(ContractModel):
    """尚未保存的完整组合结果。"""

    sprite: SpriteDraft


async def sprite_compose(request: SpriteComposeInput) -> ToolResult[SpriteComposeOutput]:
    """确定性组合实例；函数体由实施方提供。"""
    ...
```

description 非空，instances 非空。主画布配置和 sprite 总帧数均不作为输入；起点之前和实例之间未被内容覆盖的区间保持空画布。

工具执行以下确定性组合：

1. 解析库引用或完整副本，校验参数及编排。
2. 在结果中保留每个实例完整预设快照和合并后的完整参数，避免后续依赖可变的搜索结果。
3. 计算固定画布下的总帧数。
4. 生成单文件 TSX，包含各预设实现和组合宿主；处理导入、组件名及辅助符号冲突，保持各预设原本逻辑。
5. 按 instance_id 生成嵌套参数 Schema 和完整默认参数。
6. 返回 SpriteDraft，不保存 sprite，也不把 draft 来源自动写入 preset 库。

组合后的 props 示例：

```json
{
  "title": {"text": "今日灵感", "color": "#FFFFFF"},
  "subtitle": {"text": "从一个小想法开始", "color": "#FFFF00"}
}
```

title、subtitle 分别对应实例 ID，每组 Schema 来自对应预设。嵌入子 Schema 时必须保持内部引用指向正确位置，不得把某一预设的 `$ref` 意外解释为 sprite 的其他字段。

默认参数来自 compose 时的实例实际参数；调用者可以按第 3.3 节规则覆盖。sprite 组件在空 props 下使用完整默认值，仍然可运行。布局和时间不暴露为这组参数，修改编排需再次 compose。

输入示例，假设两个引用的预设都声明了 text、color 参数：

```json
{
  "description": "标题在上、说明在下的竖屏文字模板",
  "instances": [
    {
      "instance_id": "title",
      "source": {"kind": "stored", "preset_id": "preset_title_001"},
      "parameters": {"text": "今日灵感", "color": "#FFFFFF"},
      "layout": {"x": 100, "y": 200, "width": 880, "height": 300, "z_index": 1},
      "timing": {"start_frame": 0, "duration_frames": 90}
    },
    {
      "instance_id": "subtitle",
      "source": {"kind": "stored", "preset_id": "preset_subtitle_001"},
      "parameters": {"text": "从一个小想法开始", "color": "#FFFF00"},
      "layout": {"x": 100, "y": 600, "width": 880, "height": 240, "z_index": 2},
      "timing": {"start_frame": 30, "duration_frames": 120}
    }
  ]
}
```

输出中的 composition 必须为 `{"width":1080,"height":1920,"fps":30,"duration_frames":150}`，即 5 秒。主帧 30 时 subtitle 的局部帧为 0；主帧 90 时 title 不再显示；主时间轴最后可访问的帧是 149。

副本使用方式：把 modify 返回的 `data.preset` 完整放入 `source: {kind: "draft", preset: ...}`，无需先 create。

错误：`INVALID_ARGUMENT`、`PRESET_NOT_FOUND`、`COMPOSITION_FAILED`。重复实例 ID、非法参数或时间属于输入错误；工具不能完成确定性代码组合时返回组合错误。compose 成功不等于行为测试通过。

### 7.3 create

```python
class SpriteCreateInput(ContractModel):
    """接收 compose 返回的完整组合定义。"""

    sprite: SpriteDraft


class SpriteCreateOutput(ContractModel):
    """新 sprite 记录及入库代码校验报告。"""

    sprite: SpriteRecord
    validation: CodeValidationReport


async def sprite_create(request: SpriteCreateInput) -> ToolResult[SpriteCreateOutput]:
    """校验一致性和代码后保存；函数体由实施方提供。"""
    ...
```

接收 compose 的完整结果。验证固定宽高和帧率、自动计算时长、实例快照、参数结构及组合产物的一致性，并在内部执行代码校验；通过后保存新记录并返回 sprite_id。

不能接受与实例定义脱节的任意改写产物。可重新组合或以等效方式核对 code、Schema、默认值与 instances 的一致性，不静默把传入内容改成另一份模板。修改预设实现或编排，应先得到新的 compose 结果。

存储完整快照，后续不因某个库记录或临时副本发生变化而改变已保存 sprite。不要求副本另行入 preset 库。不覆盖其他 sprite，不新增 sprite 搜索、更新或删除工具。

输入来自 compose 成功回执的 data，即 `{"sprite": <完整 SpriteDraft>}`。成功输出增加 sprite_id、created_at，并附 CodeValidationReport。

错误：`INVALID_ARGUMENT`、`COMPOSITION_FAILED`、`CODE_VALIDATION_FAILED`、`VALIDATION_UNAVAILABLE`、`SPRITE_STORE_FAILED`、`TIMEOUT`。校验失败不保存；create 不运行自定义行为测试，也不声明视觉验收通过。

## 8. 校验工具

### 8.1 validate.code：参数契约与 LSP 校验

```python
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


async def validate_code(request: CodeValidationInput) -> ToolResult[CodeValidationReport]:
    """执行契约和 LSP 检查；函数体由实施方提供。"""
    ...
```

工具检查：

1. TSX 是否符合单文件默认组件导出契约。
2. 参数 Schema 是否有效，默认参数是否匹配 Schema。
3. LSP 的语法、类型、导入解析及实际默认参数调用点诊断；不能只检查文件能否被解析。

LSP 的具体服务实现不在本文规定。有效的代码输入产生 error 级诊断时，工具完成检查但 passed=false；warning/information/hint 不单独阻止通过。

range 采用 LSP 的零基行号和 UTF-16 character 偏移，结束位置排他。file 是逻辑文件名，例如 Component.tsx 或宿主生成的调用点文件；不依赖实施方的本地绝对路径。field 指向输入中的契约字段。

JSON 结构本身不合法返回 `INVALID_ARGUMENT`。能读取的 Schema、默认参数或源码不符合契约，返回 contract/lsp 诊断。服务没有启动或工具依赖不可用返回 `VALIDATION_UNAVAILABLE`，不能把环境故障描述成源码错误。

输入可以由任意 PresetDraft、SpriteDraft 提取以下三个字段构造：

```text
component = { code, parameter_schema, default_parameters }
```

失败检查结果示例：

```json
{
  "ok": true,
  "data": {
    "passed": false,
    "diagnostics": [
      {
        "source": "lsp",
        "severity": "error",
        "message": "组件声明要求 title，但提供的默认参数没有该字段。",
        "file": "props.contract.tsx",
        "code": "TS2741",
        "range": {
          "start": {"line": 4, "character": 16},
          "end": {"line": 4, "character": 21}
        }
      }
    ]
  }
}
```

工具错误：`INVALID_ARGUMENT`、`VALIDATION_UNAVAILABLE`、`RESOURCE_LIMIT_EXCEEDED`、`TIMEOUT`。

### 8.2 validate.render：固定环境中的运行与行为测试

此处 render 指运行 Remotion 组件以取得可检查的运行结果，不要求编码 MP4、生成 PNG、截图或解码视频抽帧。读取某一时间点的 DOM、布局或计算样式仍有运行成本，但没有图片采样及视觉模型调用。

```python
class TestScript(ContractModel):
    """Agent 提交的命名测试，code 内容仍为 TypeScript。"""

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


async def validate_render(request: RenderValidationInput) -> ToolResult[RenderValidationReport]:
    """运行基础检查和自定义测试；函数体由实施方提供。"""
    ...
```

| 输入字段 | 必填 | 含义 |
| --- | --- | --- |
| component | 是 | 被测组件及其参数契约；只读取这次传入的完整内容 |
| duration_frames | 是 | 本次运行的正整数总帧数；测试 sprite 时直接取 compose 出参的值 |
| parameters | 否 | 在组件默认值上应用的参数覆盖，省略时使用默认值 |
| tests | 否 | 自定义 TypeScript 测试脚本；省略时为 []，仅执行基础检查；name 非空且同一请求内唯一 |

主画布宽高和帧率始终固定，不接受输入覆盖。单独测试 preset 时使用固定主画布；需要验证 preset 在局部画布及局部时钟下的行为，应测试包含该实例的 sprite。

测试的参数和时长仅用于本次执行，不写回 preset、sprite 或其默认参数。工具不接受 MP4 地址作为被测主体；被测对象是 Remotion 组件代码及其运行上下文。

固定基础检查至少返回：

| 检查名 | 内容 |
| --- | --- |
| code_contract | 执行 validate.code 的同等检查，报告见 code_validation |
| parameters | 合并后的本次参数满足组件 Schema |
| default_render | 使用完整默认参数挂载组件，在第 0 帧能够完成初始化且无组件运行异常 |
| configured_render | 使用本次有效参数挂载组件，在第 0 帧能够完成初始化且无组件运行异常 |

第 0 帧可以因入场动画而完全透明，不能据此判组件失败。基础检查不声称已覆盖整个时间轴；动画中间过程、参数行为及其他需求由显式测试覆盖。

输出状态约定：

- passed：该项真实执行且满足条件。
- failed：代码/参数检查不通过、实际行为与断言不符，或被测组件运行失败。
- error：测试脚本自身无效、越界调用测试上下文、无断言等，无法形成有效行为结论。
- not_run：前置检查失败或更早阶段终止，没有执行；不能当作通过。

基础检查不通过时，自定义脚本标为 not_run，不继续对不可运行的组件执行断言。单份脚本失败或错误后，其他脚本在可用环境中以独立状态继续；基础设施整体不可用则终止并返回 ToolError。

`passed=true` 要求代码校验通过，所有基础检查和所有提供的脚本均为 passed。tests 为空时 custom_tests_executed=0，只表示基础检查通过，不声称已验证需求行为。custom_tests_executed 统计实际开始执行的脚本，包含运行后失败或出错的脚本。

组件失败、断言失败、脚本错误必须在报告中区分原因；代码/断言不能自行提交“总体通过”覆盖宿主结果。工具整体超时或环境故障返回 `TIMEOUT` / `VALIDATION_UNAVAILABLE`，已知部分报告可放入 error.details，但不能返回成功结论。

工具错误：`INVALID_ARGUMENT`、`VALIDATION_UNAVAILABLE`、`RESOURCE_LIMIT_EXCEEDED`、`TIMEOUT`。

### 8.3 测试上下文契约

每份 TestScript.code 是一个默认导出的 TypeScript 异步函数，接收 TestContext，返回 void。工具提供声明和执行环境；脚本不自行安装依赖、创建浏览器、注册 Composition 或清理宿主。以下 TypeScript 只描述浏览器内脚本上下文，不是 Python 工具的入参或出参模型；它与第 8.4 节测试示例保留原语言。

```typescript
// 浏览器端对应的 JSON 值与主画布快照，语义与 Python 模型一致。
type Integer = number;
type JsonValue = null | boolean | number | string | JsonValue[] | JsonObject;
type JsonObject = { [key: string]: JsonValue };
interface Composition {
  width: 1080;
  height: 1920;
  fps: 30;
  duration_frames: Integer;
}

interface ElementSnapshot {
  text: string;
  box: { x: number; y: number; width: number; height: number };
  styles: Record<string, string>;
}

interface TestContext {
  readonly composition: Composition;
  set_frame(frame: Integer): Promise<void>;
  set_parameters(patch: JsonObject): Promise<void>;
  query(selector: string): Promise<ElementSnapshot | null>;
  query_all(selector: string): Promise<ElementSnapshot[]>;
  assert(condition: boolean, message: string): void;
  assert_equal(actual: JsonValue, expected: JsonValue, message: string): void;
  assert_close(actual: number, expected: number, tolerance: number, message: string): void;
}
```

| 成员 | 行为 |
| --- | --- |
| composition | 当前被测主画布和总帧数的只读快照 |
| set_frame | 设置主时间轴的零基帧号，范围 `[0, duration_frames)`；等待组件与必要资源在该时间点就绪后返回，不输出图片 |
| set_parameters | 按第 3.3 节规则在当前有效参数上合并补丁；校验并等待更新完成，保持当前帧；不改库内数据 |
| query | 在被测组件范围内按 CSS 选择器查询第一个元素，找不到返回 null |
| query_all | 返回全部匹配元素快照，找不到返回 [] |
| assert | 记录布尔断言；false 记录失败并抛出断言异常 |
| assert_equal | 记录 JSON 值深比较；对象键顺序无关，数组顺序有关；不做类型转换 |
| assert_close | 验证 `abs(actual - expected) <= tolerance`；三个数必须有限，tolerance 非负 |

ElementSnapshot.text 为元素 textContent，空内容为 `""`。box 为相对主画布左上角、按实际变换计算的 CSS 像素外接矩形。测试视口使用 1:1 画布尺度，不能把编辑器的显示缩放带入结果。

styles 使用浏览器计算样式的 CSS 属性名及字符串值，例如 opacity、color、font-size。它是该元素的计算样式，不自动合并祖先透明度或证明文字未被遮挡。需要检查祖先、裁切或层间关系时，脚本显式查询相应元素并断言。

compose 必须在每个实例宿主暴露 `data-imv-instance="<instance_id>"`，方便定位实例。预设内部可以由 Agent 添加 data-testid 等测试定位属性；工具不能虚构不存在的元素。

每个脚本开始前，工具重新建立本次输入参数和第 0 帧状态，等待初始化完成；前一个脚本的参数变化、元素状态或局部缓存不得污染后一个脚本。工具负责结束、异常、取消和超时后的资源清理。

每个执行成功的脚本至少记录一条断言；没有断言时为 error。断言记录归工具维护，即使脚本捕获失败断言异常，已记录失败也不能被撤销。脚本只能通过约定上下文观察和驱动被测组件，不提供文件、任意网络请求、系统进程或修改被测源码的接口。

### 8.4 自定义测试示例

下面是一份 TestScript.code 的文档示例。假设 title 实例内存在 data-testid="label" 的元素，其默认参数和动画要求为“今日灵感”、前 15 帧线性淡入；该元素自身设置 opacity。

```typescript
/** 在宿主测试上下文中检查指定标题的文案和淡入行为。 */
export default async function run(ctx: TestContext): Promise<void> {
  const selector = '[data-imv-instance="title"] [data-testid="label"]';
  await ctx.set_frame(0);
  const first = await ctx.query(selector);
  ctx.assert(first !== null, "标题元素存在");
  if (first === null) return;
  ctx.assert_equal(first.text, "今日灵感", "标题文字正确");
  ctx.assert_close(Number(first.styles.opacity), 0, 0.01, "淡入起点透明");

  await ctx.set_frame(7);
  const middle = await ctx.query(selector);
  ctx.assert(middle !== null, "淡入中间存在标题");
  if (middle === null) return;
  ctx.assert_close(Number(middle.styles.opacity), 7 / 15, 0.02, "线性淡入中间状态");

  await ctx.set_frame(15);
  const last = await ctx.query(selector);
  ctx.assert(last !== null, "淡入结束存在标题");
  if (last === null) return;
  ctx.assert_close(Number(last.styles.opacity), 1, 0.01, "淡入结束可见");
}
```

该示例只适用于明确要求线性淡入的情况，不能把线性曲线强加给其他动画需求。TestScript 的 name 例如 `title_fade`，code 字段保存上述函数源码。

运行结果示例，假设基础检查均通过但淡入结束值不正确：

```json
{
  "ok": true,
  "data": {
    "passed": false,
    "composition": {"width":1080,"height":1920,"fps":30,"duration_frames":150},
    "code_validation": {"passed":true,"diagnostics":[]},
    "checks": [
      {"name":"code_contract","status":"passed"},
      {"name":"parameters","status":"passed"},
      {"name":"default_render","status":"passed"},
      {"name":"configured_render","status":"passed"}
    ],
    "tests": [
      {
        "name":"title_fade",
        "status":"failed",
        "message":"淡入结束可见：实际透明度 0.6，预期 1，容差 0.01。",
        "assertions": [
          {"message":"标题元素存在","passed":true,"actual":true,"expected":true},
          {"message":"标题文字正确","passed":true,"actual":"今日灵感","expected":"今日灵感"},
          {"message":"淡入起点透明","passed":true,"actual":0,"expected":0},
          {"message":"淡入中间存在标题","passed":true,"actual":true,"expected":true},
          {"message":"线性淡入中间状态","passed":true,"actual":0.4667,"expected":0.4666666666666667},
          {"message":"淡入结束存在标题","passed":true,"actual":true,"expected":true},
          {"message":"淡入结束可见","passed":false,"actual":0.6,"expected":1}
        ]
      }
    ],
    "custom_tests_executed":1
  }
}
```

实际报告必须列出真实执行的断言，不能用示例中的结果代替执行。测试脚本、通过项数量或某些时间点的样式变化，都不能证明未经检查的视觉效果正确。

## 9. tools.inspect

```python
ToolName = Literal[
    "image.info", "image.resize", "image.crop",
    "preset.search", "preset.create", "preset.modify",
    "validate.code", "validate.render", "sprite.compose", "sprite.create",
    "tools.inspect", "tools.plan_execute",
]


class ToolInspectInput(ContractModel):
    """工具的点号 ID，或模型窗口里显示的下划线名；未知名称返回 TOOL_NOT_FOUND 并列出已注册 ID。"""

    tool_name: NonEmptyString


class ToolExample(ContractModel):
    """符合契约的完整 JSON 输入输出示例。"""

    input: JsonObject
    output: JsonObject


class ToolDescriptor(ContractModel):
    """工具模型及不能只靠 Schema 表达的行为规则。"""

    tool_name: ToolName
    description: Description
    input_schema: JsonSchema
    output_schema: JsonSchema
    constraints: list[str]
    side_effects: list[str]
    error_codes: list[str]
    examples: list[ToolExample]


async def tools_inspect(request: ToolInspectInput) -> ToolResult[ToolDescriptor]:
    """返回指定工具的真实契约；函数体由实施方提供。"""
    ...
```

根据精确工具名返回真实契约，不做语义搜索、不执行被查询工具。未知名称返回 `TOOL_NOT_FOUND`。输入格式错误返回 `INVALID_ARGUMENT`。

input_schema 描述完整入参；output_schema 描述包含 ok/data/error 的完整 ToolResult，而非只描述成功 data。Schema 为自包含 JSON Schema，公共类型可以放入 `$defs`，不能依赖 Agent 自行查找未给出的模型。

实施时，入参 Schema 从对应 Pydantic 模型的 `model_json_schema()` 生成，完整出参 Schema 从 `TypeAdapter(ToolResult[对应成功数据模型]).json_schema()` 生成。例如 resize 对应 `ImageResizeInput.model_json_schema()` 和 `TypeAdapter(ToolResult[ProcessedImage]).json_schema()`。这两者生成的是 Schema 对象，实际回执序列化仍遵循第 3.1 节。ToolInspectInput 接受非空字符串，使未知工具名能进入 TOOL_NOT_FOUND 分支；成功返回的名称由 ToolName 限定。

constraints 必须包含像素坐标、时间单位、默认值、不可变性等 Schema 不能完整表达的规则。side_effects 明确读写与代码执行行为，examples 提供符合两个 Schema 的完整输入输出。示例不能使用 `...` 或省略必要字段。

输入示例：

```json
{"tool_name":"image.resize"}
```

返回描述必须至少表达：

- 工具 image.resize 接收 image.asset_id、width、height。
- width、height 为正整数，按目标宽高强制缩放，不保持比例。
- 返回完整 `ToolResult[ProcessedImage]`，结果携带新素材引用，原图保留。
- 会读取原图片并存储处理后的 PNG。
- 可能返回第 5 节列出的相关错误。

inspect 自身也可被查询。已知工具列表由集成方提供给 Agent；本期不通过 inspect 暗中实现 find、推荐工具或自动选择操作。

### 9.1 宿主计划控制

`tools.plan_execute` / `tools_plan_execute` 可被 inspect 查询，返回同一 ToolDescriptor，
包括 [PlanAction](../server/src/server/remotion_templates/planning.py) 的完整输入 Schema、
`ToolResult[JsonObject]` 输出 Schema、约束、副作用、错误码和示例。
这个入口由宿主处理，Outer 负责委派或完成，Plan 负责步骤更新；Executor 只能检查其契约，
不能执行计划控制。读取描述符不会修改计划，也不会发布 Sprite。

## 10. 端到端数据流示例

用户输入：参考图和“上方放一个渐显标题，下方放说明文字，标题写今日灵感”。主画布与帧率固定。

1. Agent 调用 image.info 读取参考图信息；必要时调用 crop/resize 获取更适合观察的图片 URL。
2. Agent 用自然语言调用 preset.search，读取返回的完整代码和参数契约。
3. 已有预设满足需求时直接实例化并填参数，不调用 modify。
4. 需要改变实现时，由 Agent 先写出修改后的代码，再调用 modify 返回完整副本；原预设不变。如果没有合适预设，Agent 编写完整定义，通过 create 校验后加入库。
5. Agent 明确提供各实例的位置、尺寸、层级和时间，调用 compose。输入可以同时包含 stored 和 draft 两类来源。
6. compose 返回完整 SpriteDraft，示例时间编排得到 150 帧、5 秒。
7. Agent 把 SpriteDraft 的 code、parameter_schema、default_parameters 作为 component，composition.duration_frames 作为测试总帧数，连同需求断言交给 validate.render。
8. 工具返回真实检查报告。上层 Agent 根据诊断修改预设副本、参数或编排，再次 compose 和校验；工具本身不实现修复循环。
9. 满足调用方验收要求后，调用 sprite.create 保存。create 再执行入库代码校验，返回 SpriteRecord。
10. 上层使用保存后的代码、默认参数和 composition 接入自己的预览流程；本契约不规定预览 UI 或下载方式。

校验并不是隐式发布事务：sprite.create 不要求调用方提交行为测试回执，也不会声称已执行该测试。调用顺序和业务验收决策属于上层，工具入库门禁只保证其明确承诺的代码检查与数据一致性。

## 11. 实施方验收用例

以下是契约行为要求，不是已经执行的测试结果。

| 场景 | 必须观察到的结果 |
| --- | --- |
| 1200×800 图片 resize 到 600×600 | 返回新素材引用，实际像素为 600×600，不保持比例 |
| crop 区域超过原图边界 | CROP_OUT_OF_BOUNDS，不产生伪成功的截短结果 |
| 带方向元数据的图片 | info 尺寸与 crop 坐标使用同一可见方向 |
| 图片处理成功 | 新素材引用可被 info/resize/crop 再次读取，原图内容不变 |
| 空描述创建预设 | 拒绝，不能产生无法描述的搜索条目 |
| create 代码有 LSP error | 返回诊断，不保存可搜索记录 |
| create 只有 warning | 允许保存，返回 warning，不宣称行为测试通过 |
| 预设保存或索引失败 | 不返回成功，不暴露半完成预设 |
| search 无匹配结果 | 成功返回空 matches，不自动生成代码 |
| modify 原预设 | 返回完整副本；原记录及索引不变；副本没有新 preset_id |
| modify 只改代码 | 继承原 Schema 和默认值，来源指向原记录；编译结论留给 validate/create |
| 副本直接参与 compose | 能形成完整 sprite，无需把副本加入 preset 库 |
| 同一 preset 使用两次 | 实例 ID、参数和局部时间相互独立 |
| 重复 instance_id | 拒绝，不发生参数覆盖或标识碰撞 |
| 相同 z_index | 输入数组后面的实例在前 |
| subtitle 从主帧 30 开始 | 主帧 29 不显示；主帧 30 时局部帧为 0 |
| 实例分配 880×240 局部画布 | 组件读到该局部宽高与自身时长，fps 为 30 |
| 实例结束帧为 90 | 主帧 90 不显示该实例 |
| 最晚结束位置为 150 | 主画布为 1080×1920、30 fps、150 帧；不能传另一个总时长覆盖 |
| sprite 空 props 运行 | 使用 compose 时生成的完整默认参数 |
| 只覆盖 title.text | 保留 title 的其他默认参数及其他实例参数 |
| 修改布局或时间 | 通过再次 compose 产生新定义，不能靠参数覆盖改写编排 |
| create 接收被篡改而不一致的 SpriteDraft | 拒绝保存，不静默改写为另一份模板 |
| validate.code 报编译错误 | ok=true、passed=false，带结构化诊断 |
| validate 环境不可用 | ToolError，不把环境故障当作代码或行为失败 |
| 测试脚本无断言 | 脚本为 error，整体不通过 |
| 脚本捕获失败断言异常 | 已记录的失败仍导致不通过 |
| 两份脚本先后执行 | 第二份从输入参数与第 0 帧开始，不继承第一份修改 |
| 测试访问结束帧 duration_frames | 越界脚本错误，不自动 clamp 到最后一帧 |
| tests=[] | 仅基础检查，不虚构自定义测试或视觉验收 |
| 自定义测试断言失败 | 返回失败项及实际值，原 preset/sprite 不变 |
| 运行校验 | 不生成截图、不抽帧、不调用视觉 Judge；时间定位本身不输出图片 |
| tools.inspect 已知工具 | 输出可解析的完整输入输出 Schema、规则和示例，不执行目标工具 |
| tools.inspect 未知工具 | TOOL_NOT_FOUND，列出已注册的工具 ID 供纠正名称；不做模糊匹配、不推荐任务方案、不执行 find |

## 12. 实施边界

实施方按 Python + Pydantic 工具契约对接，可以选择存储介质、LSP 服务、测试运行器、图片库以及 ChromaDB 的具体部署和向量配置，但必须满足本文的可观察语义。运行依赖、资源限额及图片素材引用的可用期需要在部署中明确；不得把这些部署选择隐式变成不同的坐标、参数合并或时间规则。

新增代码、外部资源读取和自定义测试需要在实施方的受控执行环境运行；工具错误应提供业务可用诊断，不暴露凭据。本文不设计执行沙箱实现、访问控制系统或额外审批流程。

本期不扩展图片上传工具、preset 删除或覆盖更新、sprite 搜索或修改、工具 find、独立模型角色、任务状态机、SSE、预览发布、旧数据迁移，也不将现有 Actor/Judge/Harness 的实现视为新工具已经完成。
