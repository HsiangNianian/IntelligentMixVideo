"""数字人开场与抠像规则；模板只保存样式，不保存某条视频的人脸坐标。"""

from pydantic import BaseModel, ConfigDict, Field, StrictBool
from pydantic.alias_generators import to_camel


class AvatarEffects(BaseModel):
    """开场固定秒数；圆大小相对人脸，抠像只在实际同屏区间生效。"""

    model_config = ConfigDict(extra="forbid", alias_generator=to_camel, populate_by_name=True)
    opening_enabled: StrictBool = False
    opening_duration: float = Field(default=2, ge=0.1, le=300, allow_inf_nan=False)
    circle_scale: float = Field(default=1.6, ge=1, le=3, allow_inf_nan=False)
    feather: float = Field(default=0.02, ge=0, le=0.2, allow_inf_nan=False)
    remove_background: StrictBool = False
