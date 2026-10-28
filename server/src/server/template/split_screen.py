"""模板同屏配置：在保存边界校验窗口和字幕坐标，几何以 1080×1920 画布为基准。"""

from typing import Literal
from math import floor

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator
from pydantic.alias_generators import to_camel


class ScreenPoint(BaseModel):
    """画布百分比坐标；字幕横坐标表示中心，纵坐标表示顶部。"""

    model_config = ConfigDict(extra="forbid", alias_generator=to_camel, populate_by_name=True)
    x: float = Field(default=50, ge=0, le=100, allow_inf_nan=False)
    y: float = Field(default=62, ge=0, le=100, allow_inf_nan=False)


class ScreenWindow(ScreenPoint):
    """窗口左上角及尺寸用画布百分比，圆角和内边框用基准画布像素。"""

    width: float = Field(gt=0, le=100, allow_inf_nan=False)
    height: float = Field(gt=0, le=100, allow_inf_nan=False)
    fit: Literal["Contain", "Cover"] = "Contain"
    radius: float = Field(default=0, ge=0, allow_inf_nan=False)
    border_width: float = Field(default=0, ge=0, allow_inf_nan=False)
    border_color: str = Field(default="#FFFFFF", pattern=r"^#[0-9a-fA-F]{6}$")

    @model_validator(mode="after")
    def within_canvas(self) -> "ScreenWindow":
        """窗口不得越出画布，圆角与内边框不能超过窗口短边的一半。"""
        if self.x + self.width > 100 or self.y + self.height > 100:
            raise ValueError("同屏窗口必须完整位于画布内")
        limit = min(self.width * 10.8, self.height * 19.2) / 2
        if self.radius > limit or self.border_width > limit:
            raise ValueError("圆角和边框不能超过窗口短边的一半")
        return self


class SplitScreen(BaseModel):
    """独立的同屏布局；关闭保留参数，缺省旧模板不启用。"""

    model_config = ConfigDict(extra="forbid", alias_generator=to_camel, populate_by_name=True)
    enabled: StrictBool = False
    layout: Literal["split", "overlay", "circle"] = "split"
    background_color: str = Field(default="#000000", pattern=r"^#[0-9a-fA-F]{6}$")
    material: ScreenWindow = Field(default_factory=lambda: ScreenWindow(x=8, y=12, width=84, height=48))
    avatar: ScreenWindow = Field(default_factory=lambda: ScreenWindow(x=35, y=68, width=30, height=21, radius=30, border_width=6))
    subtitle: ScreenPoint = Field(default_factory=ScreenPoint)
    subtitle_en: ScreenPoint = Field(default_factory=lambda: ScreenPoint(y=65.5))


def screen_rect(window: ScreenWindow, source: list[float], width: int, height: int) -> dict:
    """保持比例时居中缩进，返回真实可见画面及装饰尺寸；与客户端同名函数一致。"""
    x, y = window.x * width / 100, window.y * height / 100
    w, h = window.width * width / 100, window.height * height / 100
    if window.fit == "Contain":
        scale = min(w / source[0], h / source[1])
        x, y = x + (w - source[0] * scale) / 2, y + (h - source[1] * scale) / 2
        w, h = source[0] * scale, source[1] * scale
    w, h = max(2, floor(w + 0.5)), max(2, floor(h + 0.5))
    factor = min(width / 1080, height / 1920)
    return dict(x=floor(x + 0.5), y=floor(y + 0.5), width=w, height=h,
                radius=min(window.radius * factor, w / 2, h / 2), border=min(window.border_width * factor, w / 2, h / 2))
