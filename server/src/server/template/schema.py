"""运营特效模板：请求只接受目录 ID，特效参数由服务端解析并保存快照。"""

from datetime import datetime
from functools import lru_cache
import json
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
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


class EffectTemplateEditor(BaseModel):
    """SDK 5.2.2 编辑配置；文字内容用于模板示例，正式字幕内容由剪辑计划提供。"""

    model_config = ConfigDict(extra="forbid", alias_generator=to_camel, populate_by_name=True)

    title: str = Field(default="让每一帧 都有风格", max_length=60)
    subtitle: str = Field(default="选择花字、滤镜和特效，看看组合效果", max_length=100)
    bubble_text: str = Field(default="超值特惠", max_length=40)
    title_size: int = Field(default=40, ge=12, le=120)
    subtitle_size: int = Field(default=26, ge=12, le=120)
    bubble_size: int = Field(default=32, ge=12, le=120)
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


class TemplateData(BaseModel):
    """完整模板配置；API 和数据库读取共用字段定义。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    editor: EffectTemplateEditor
    effect_ids: list[str] = Field(min_length=1, max_length=20)
    transition_duration_seconds: float = Field(default=0.5, ge=0.1, le=3, allow_inf_nan=False)


class TemplateSave(TemplateData):
    """POST 请求：无 ID 创建，有 ID 更新；只接受已知效果的正确组合。"""

    template_id: UUID | None = None

    @model_validator(mode="after")
    def validate_effects(self) -> "TemplateSave":
        """拒绝重复 ID、未知效果和编辑配置不一致，参数只在服务端解析。"""
        selected = self.editor.selected_effects()
        if len(self.effect_ids) != len(set(self.effect_ids)):
            raise ValueError("同一个特效不能重复添加")
        if set(selected.values()) != set(self.effect_ids):
            raise ValueError("所选特效与编辑配置不一致")
        expected = {
            "title_flower": "flower", "subtitle_flower": "flower", "bubble": "bubble",
            "filter": "filter", "vfx": "vfx/normal", "transition": "transition/normal",
            **{f"{role}_{motion}": motion for role in ("title", "subtitle", "bubble")
               for motion in ("in", "out", "loop")},
        }
        catalog = effect_catalog()
        for key, value in selected.items():
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
