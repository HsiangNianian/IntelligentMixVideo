"""模板保存对象时间规则和可信效果快照，视频信息由应用时提供；Remotion 片段只保存对已发布 Sprite 的引用和秒数起点。"""

import json
import math
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, StrictBool, StrictStr, field_serializer,
    field_validator, model_validator,
)
from pydantic.alias_generators import to_camel

EffectCategory = Literal[
    "flower",
    "in",
    "out",
    "loop",
    "bubble",
    "filter",
    "vfx/normal",
    "transition/normal",
]

CATEGORY_PARAMETERS = {
    "flower": "EffectColorStyle",
    "in": "AaiMotionInEffect",
    "out": "AaiMotionOutEffect",
    "loop": "AaiMotionLoopEffect",
    "bubble": "BubbleStyleId",
    "filter": "SubType",
    "vfx/normal": "SubType",
    "transition/normal": "SubType",
}


class EffectAsset(BaseModel):
    """由可信 SDK 目录生成的效果信息，客户端不能提交渲染参数。"""

    id: str = Field(
        description="素材目录唯一 ID，含分类前缀；用于预览 catalog_id 和模板 effect_ids。"
    )
    category: EffectCategory
    name: str
    effect_id: str = Field(
        pattern=r"^[A-Za-z0-9_-]+$",
        description="IMS 官方特效标识，不含分类前缀；不是预览接口的 catalog_id。",
    )
    parameters: dict[str, str]
    preview_url: str = ""


class _RemotionModel(BaseModel):
    """Remotion 摆放参数：模板 JSON（驼峰）与 Protobuf 转出的字典（下划线）都能解析；未知字段拒绝。"""

    model_config = ConfigDict(extra="forbid", alias_generator=to_camel, populate_by_name=True)


def _finite(value):
    """数字字段只接受有限的 JSON 数字：布尔值、字符串、NaN 与无穷都拒绝。"""
    if value is None:
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("必须是有限数字")
    return float(value)


# 一个字符串覆盖值的长度上限；Sprite 发布时可绑定的字符串参数 maxLength 也不能超过它。
OVERRIDE_STRING_MAX_LENGTH = 2000


class RemotionStringList(_RemotionModel):
    """字符串列表参数（关键词）的值。"""

    values: list[StrictStr] = Field(default_factory=list, max_length=100)


class RemotionParameterOverride(_RemotionModel):
    """一个参数覆盖；恰好设置一个值字段。"""

    key: StrictStr = Field(min_length=1, max_length=255)
    string_value: StrictStr | None = Field(default=None, max_length=OVERRIDE_STRING_MAX_LENGTH)
    number_value: float | None = None
    bool_value: StrictBool | None = None
    string_list_value: RemotionStringList | None = None

    @field_validator("number_value", mode="before")
    @classmethod
    def finite_value(cls, value):
        """数字值必须是有限的 JSON 数字。"""
        return _finite(value)

    @model_validator(mode="after")
    def one_value(self) -> "RemotionParameterOverride":
        """覆盖必须恰好给出一个值。"""
        given = [self.string_value, self.number_value, self.bool_value, self.string_list_value]
        if sum(item is not None for item in given) != 1:
            raise ValueError(f"参数 {self.key} 必须恰好给出一个值")
        return self

    def value(self):
        """覆盖值的 JSON 形式。"""
        if self.string_list_value is not None:
            return list(self.string_list_value.values)
        return next(item for item in (self.string_value, self.number_value, self.bool_value) if item is not None)


class RemotionTransform(_RemotionModel):
    """宿主变换；未设置的字段取默认值。"""

    x: float | None = None
    y: float | None = None
    scale: float | None = None
    rotation: float | None = None
    opacity: float | None = None

    @field_validator("x", "y", "scale", "rotation", "opacity", mode="before")
    @classmethod
    def finite_values(cls, value):
        """变换字段必须是有限的 JSON 数字。"""
        return _finite(value)


class RemotionTrackEditor(_RemotionModel):
    """一次摆放的参数覆盖与宿主变换。"""

    parameters: list[RemotionParameterOverride] = Field(default_factory=list, max_length=64)
    transform: RemotionTransform | None = None

    @model_validator(mode="after")
    def unique_keys(self) -> "RemotionTrackEditor":
        """同一参数只能覆盖一次。"""
        keys = [item.key for item in self.parameters]
        if len(keys) != len(set(keys)):
            raise ValueError("参数覆盖的 key 不能重复")
        return self


class EffectTemplateEditor(BaseModel):
    """SDK 5.2.2 编辑配置；文字内容用于模板示例，正式字幕内容由剪辑计划提供。"""

    model_config = ConfigDict(extra="forbid", alias_generator=to_camel, populate_by_name=True)

    title: str = Field(default="让每一帧 都有风格", max_length=60)
    subtitle: str = Field(default="选择花字、滤镜和特效，看看组合效果", max_length=100)
    bubble_text: str = Field(default="超值特惠", max_length=40)
    title_size: int = Field(default=40, ge=12, le=300)
    subtitle_size: int = Field(default=26, ge=12, le=300)
    bubble_size: int = Field(default=32, ge=12, le=300)
    title_x: float = Field(default=50, ge=0, le=100, allow_inf_nan=False)
    title_y: float = Field(default=8, ge=0, le=100, allow_inf_nan=False)
    subtitle_x: float = Field(default=50, ge=0, le=100, allow_inf_nan=False)
    subtitle_y: float = Field(default=82, ge=0, le=100, allow_inf_nan=False)
    bubble_x: float = Field(default=25, ge=0, le=100, allow_inf_nan=False)
    bubble_y: float = Field(default=32, ge=0, le=100, allow_inf_nan=False)
    title_flower: str = Field(default="", max_length=200)
    subtitle_flower: str = Field(default="", max_length=200)
    bubble: str = Field(default="", max_length=200)
    filter: str = Field(default="", max_length=200)
    vfx: str = Field(default="", max_length=200)
    transition: str = Field(default="", max_length=200)
    title_in: str = Field(default="", max_length=200)
    title_out: str = Field(default="", max_length=200)
    title_loop: str = Field(default="", max_length=200)
    subtitle_in: str = Field(default="", max_length=200)
    subtitle_out: str = Field(default="", max_length=200)
    subtitle_loop: str = Field(default="", max_length=200)
    bubble_in: str = Field(default="", max_length=200)
    bubble_out: str = Field(default="", max_length=200)
    bubble_loop: str = Field(default="", max_length=200)
    title_in_duration: float = Field(default=0.5, ge=0.1, le=3, allow_inf_nan=False)
    title_out_duration: float = Field(default=0.5, ge=0.1, le=3, allow_inf_nan=False)
    subtitle_in_duration: float = Field(default=0.5, ge=0.1, le=3, allow_inf_nan=False)
    subtitle_out_duration: float = Field(default=0.5, ge=0.1, le=3, allow_inf_nan=False)
    bubble_in_duration: float = Field(default=0.5, ge=0.1, le=3, allow_inf_nan=False)
    bubble_out_duration: float = Field(default=0.5, ge=0.1, le=3, allow_inf_nan=False)
    title_keyword: str = Field(default="", max_length=60)
    title_keyword_bold: bool = Field(default=False, strict=True)
    title_keyword_italic: bool = Field(default=False, strict=True)
    title_keyword_underline: bool = Field(default=False, strict=True)
    title_keyword_strikeout: bool = Field(default=False, strict=True)
    title_keyword_color: str = Field(default="", pattern=r"^(?:|#[0-9A-Fa-f]{6})$")
    title_keyword_size: int = Field(default=0, ge=0, le=300, strict=True)
    subtitle_keyword_bold: bool = Field(default=False, strict=True)
    subtitle_keyword_italic: bool = Field(default=False, strict=True)
    subtitle_keyword_underline: bool = Field(default=False, strict=True)
    subtitle_keyword_strikeout: bool = Field(default=False, strict=True)
    subtitle_keyword_color: str = Field(default="", pattern=r"^(?:|#[0-9A-Fa-f]{6})$")
    subtitle_keyword_size: int = Field(default=0, ge=0, le=300, strict=True)
    # 仅 Remotion 片段使用：参数覆盖与宿主变换；IMS 对象不输出该字段，JSON 与引入前保持一致。
    remotion: RemotionTrackEditor | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("title_keyword_size", "subtitle_keyword_size")
    @classmethod
    def validate_keyword_size(cls, value: int) -> int:
        """局部字号为零时沿用文字字号；启用时要求 12～300 像素。"""
        if value != 0 and value < 12:
            raise ValueError("关键词字号须为 12～300 的整数")
        return value

    @model_validator(mode="after")
    def exclusive_motions(self) -> "EffectTemplateEditor":
        """按文字角色拒绝循环与入场、出场混用。"""
        for role in ("title", "subtitle", "bubble"):
            if getattr(self, f"{role}_loop") and (
                getattr(self, f"{role}_in") or getattr(self, f"{role}_out")
            ):
                raise ValueError("同一类文字的循环动效不能与入场、出场同时使用")
        return self

    def selected_effects(self) -> dict[str, str]:
        """收集非空效果选择，供保存时核对分类和目录 ID。"""
        return {
            key: value
            for key, value in {
                "title_flower": self.title_flower,
                "subtitle_flower": self.subtitle_flower,
                "bubble": self.bubble,
                "filter": self.filter,
                "vfx": self.vfx,
                "transition": self.transition,
                **{
                    f"{role}_{motion}": getattr(self, f"{role}_{motion}")
                    for role in ("title", "subtitle", "bubble")
                    for motion in ("in", "out", "loop")
                },
            }.items()
            if value
        }


class EffectTrack(BaseModel):
    """独立特效实例，文字动画归属于该实例的参数；`remotion` 是引用已发布 Sprite 的片段。"""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    target: Literal["title", "subtitle", "bubble", "filter", "vfx", "transition", "remotion"]
    start_mode: Literal["seconds", "percent"]
    start: float = Field(ge=0, allow_inf_nan=False)
    duration: float | None = Field(gt=0, allow_inf_nan=False)
    editor: EffectTemplateEditor
    # 仅 Remotion 片段引用 Sprite；为空时不输出，其余对象的 JSON 与引入该字段前保持一致。
    sprite_id: UUID | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_serializer("editor", mode="wrap")
    def serialize_editor(self, editor: EffectTemplateEditor, handler: SerializerFunctionWrapHandler) -> dict:
        """Remotion 片段只输出它的参数覆盖与宿主变换（没有时为空对象），而不是一整套 IMS 默认值；其余对象沿用原序列化。"""
        if self.target != "remotion":
            return handler(editor)
        if editor.remotion is None:
            return {}
        return {"remotion": editor.remotion.model_dump(mode="json", by_alias=True, exclude_none=True)}

    @model_validator(mode="after")
    def validate_instance(self) -> "EffectTrack":
        """检查时间规则和对象内容；应用视频时计算实际区间与动画时长。"""
        if self.target == "remotion":
            # 文字和效果规则只适用于 IMS 对象；片段只需要指向 Sprite 的秒数起点，时长缺省取 Sprite 自身。
            if self.sprite_id is None:
                raise ValueError("Remotion 片段缺少 sprite_id")
            if self.start_mode != "seconds":
                raise ValueError("Remotion 片段只能使用秒数起点")
            if self.editor.model_fields_set - {"remotion"}:
                raise ValueError("Remotion 片段只能携带参数覆盖与宿主变换")
            return self
        if self.sprite_id is not None:
            raise ValueError("只有 Remotion 片段可以引用 Sprite")
        if self.editor.remotion is not None:
            raise ValueError("只有 Remotion 片段可以设置参数覆盖与宿主变换")
        if self.start_mode == "percent" and self.start >= 100:
            raise ValueError("开始百分比须小于 100")
        text = self.target in ("title", "subtitle", "bubble")
        keys = ({"bubble" if self.target == "bubble" else f"{self.target}_flower"}
                | {f"{self.target}_{motion}" for motion in ("in", "out", "loop")}) if text else {self.target}
        if set(self.editor.selected_effects()) - keys:
            raise ValueError("轨道包含其他对象的效果")
        if self.target != "subtitle" and any((
            self.editor.subtitle_keyword_bold, self.editor.subtitle_keyword_italic,
            self.editor.subtitle_keyword_underline, self.editor.subtitle_keyword_strikeout,
            bool(self.editor.subtitle_keyword_color),
            bool(self.editor.subtitle_keyword_size),
        )):
            raise ValueError("只有底部字幕可以设置关键词样式")
        if self.target != "title" and any((
            self.editor.title_keyword, self.editor.title_keyword_bold,
            self.editor.title_keyword_italic, self.editor.title_keyword_underline,
            self.editor.title_keyword_strikeout, bool(self.editor.title_keyword_color),
            bool(self.editor.title_keyword_size),
        )):
            raise ValueError("只有顶部标题可以设置标题关键词样式")
        for role, field in (("title", "title"), ("subtitle", "subtitle"), ("bubble", "bubble_text")):
            content = getattr(self.editor, field)
            if role != self.target and content:
                raise ValueError("轨道包含其他对象的文字")
            if role == self.target and not content.strip():
                raise ValueError("文字轨道内容不能为空")
        if not text and not getattr(self.editor, self.target):
            raise ValueError("轨道缺少效果")
        if self.target == "transition" and (self.start <= 0 or self.duration is None or not 0.1 <= self.duration <= 3):
            raise ValueError("转场位置或时长无效")
        return self


class TemplateData(BaseModel):
    """完整模板配置；API 和数据库读取共用字段定义。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    # 可以为空：只放 Remotion 片段的模板没有 IMS 效果；效果与对象是否一致仍由 TemplateSave 校验。
    # Protobuf 转字典会省略空数组，所以缺失按空列表处理。
    effect_ids: list[str] = Field(default_factory=list, max_length=500)
    transition_duration_seconds: float = Field(default=1, ge=0.1, le=3, allow_inf_nan=False)
    tracks: list[EffectTrack] = Field(max_length=100)

    @model_validator(mode="after")
    def validate_tracks(self) -> "TemplateData":
        """对象 ID 唯一，转场保留单个切换位置；时间规则独立于视频。"""
        if len({track.id for track in self.tracks}) != len(self.tracks):
            raise ValueError("轨道 ID 重复")
        if sum(track.target == "transition" for track in self.tracks) > 1:
            raise ValueError("两个母版片段只允许一个转场")
        return self


class TemplateSave(TemplateData):
    """POST 请求：无 ID 创建，有 ID 更新；只接受已知效果的正确组合。"""

    template_id: UUID | None = None

    @model_validator(mode="after")
    def validate_effects(self) -> "TemplateSave":
        """拒绝重复 ID、未知效果和编辑配置不一致，参数只在服务端解析。"""
        selections = [track.editor.selected_effects() for track in self.tracks]
        if len(self.effect_ids) != len(set(self.effect_ids)):
            raise ValueError("同一个特效不能重复添加")
        if {value for selected in selections for value in selected.values()} != set(self.effect_ids):
            raise ValueError("所选特效与编辑配置不一致")
        expected = {
            "title_flower": "flower", "subtitle_flower": "flower", "bubble": "bubble",
            "filter": "filter", "vfx": "vfx/normal", "transition": "transition/normal",
            **{f"{role}_{motion}": motion for role in ("title", "subtitle", "bubble")
               for motion in ("in", "out", "loop")},
        }
        catalog = effect_catalog()
        for key, value in (item for selected in selections for item in selected.items()):
            if value not in catalog or catalog[value].category != expected[key]:
                raise ValueError(f"效果不在对应的 SDK 目录中：{value}")
        return self


class Template(TemplateData):
    """完整模板响应；ID、UTC 时间与效果快照由服务端生成。"""

    template_id: UUID
    effects: list[EffectAsset]
    created_at: datetime
    updated_at: datetime


@lru_cache(maxsize=1)
def effect_catalog() -> dict[str, EffectAsset]:
    """加载随包发布的 5.2.2 白名单；前端目录仍由 SDK 和静态动画提供。"""
    directory = Path(__file__).parent
    raw = json.loads((directory / "sdk_catalog.json").read_text(encoding="utf-8"))
    result = {
        f"{category}/{code}": EffectAsset(
            id=f"{category}/{code}", category=category, name=code,
            effect_id=code, parameters={CATEGORY_PARAMETERS[category]: code},
        )
        for category, codes in raw["categories"].items() for code in codes
    }
    for item in json.loads((directory / "motions.json").read_text(encoding="utf-8")):
        result[item["id"]] = EffectAsset.model_validate(item)
    return result
