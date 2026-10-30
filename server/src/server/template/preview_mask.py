"""SDK 5.2.2 的圆角与圆形羽化蒙版：只处理几何参数，不接收用户媒体。"""

import math
import subprocess
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw
from pydantic import BaseModel, Field, model_validator


class PreviewMask(BaseModel):
    """预览源尺寸、显示窗口及圆角；分辨率在编码前缩至 1920 内。"""

    width: int = Field(ge=2, le=32768)
    height: int = Field(ge=2, le=32768)
    box_width: int = Field(ge=2, le=1080)
    box_height: int = Field(ge=2, le=1920)
    radius: float = Field(gt=0, le=540, allow_inf_nan=False)
    seconds: int = Field(gt=0, le=2147483647)

    @model_validator(mode="after")
    def check_radius(self) -> "PreviewMask":
        """入口拒绝超过可见窗口短边一半的圆角。"""
        if self.radius > min(self.box_width, self.box_height) / 2:
            raise ValueError("圆角不能超过窗口短边的一半")
        return self


class PreviewCircleMask(BaseModel):
    """圆心按源宽高归一化，半径按源短边归一化；只属于预览资源。"""

    width: int = Field(ge=2, le=32768)
    height: int = Field(ge=2, le=32768)
    x: float = Field(ge=0, le=1, allow_inf_nan=False)
    y: float = Field(ge=0, le=1, allow_inf_nan=False)
    radius: float = Field(gt=0, le=0.5, allow_inf_nan=False)
    feather: float = Field(ge=0, le=0.2, allow_inf_nan=False)
    seconds: int = Field(gt=0, le=2147483647)


def circle_mask_image(width: int, height: int, x: float, y: float, radius: float, feather: float) -> Image.Image:
    """圆内白、圆外黑，软边在圆内线性过渡；与客户端 CSS 使用同一几何，不模拟 IMS 私有算法。"""
    import numpy as np

    factor = min(1, 1920 / max(width, height))
    w, h = max(2, math.floor(width * factor / 2) * 2), max(2, math.floor(height * factor / 2) * 2)
    yy, xx = np.ogrid[:h, :w]
    r = radius * min(w, h)
    distance = np.hypot(xx + 0.5 - x * w, yy + 0.5 - y * h)
    alpha = np.clip((r - distance) / max(1, 2 * r * feather), 0, 1)
    return Image.fromarray(np.round(alpha * 255).astype("uint8"))


def mask_image(width: int, height: int, box_width: int, box_height: int, radius: float) -> Image.Image:
    """蒙版在 SDK 源视频坐标里生效；反算 Cover 的中心裁剪，保持输出圆角像素不变。"""
    factor = min(1, 1920 / max(width, height))
    w, h = max(2, math.floor(width * factor / 2) * 2), max(2, math.floor(height * factor / 2) * 2)
    scale = max(box_width / w, box_height / h)
    crop_w, crop_h = box_width / scale, box_height / scale
    x, y = (w - crop_w) / 2, (h - crop_h) / 2
    image = Image.new("L", (w * 2, h * 2))
    ImageDraw.Draw(image).rounded_rectangle(
        (x * 2, y * 2, (x + crop_w) * 2 - 1, (y + crop_h) * 2 - 1),
        radius=radius / scale * 2, fill=255,
    )
    return image.resize((w, h), Image.Resampling.LANCZOS)


@lru_cache(maxsize=32)
def mask_video(width: int, height: int, box_width: int, box_height: int, radius: float, seconds: int) -> bytes:
    """单帧 MP4 持续整个源区间；有限内存缓存避免拖动位置时重复编码，不留下临时文件。"""
    return encode_mask(mask_image(width, height, box_width, box_height, radius), seconds)


@lru_cache(maxsize=32)
def circle_mask_video(width: int, height: int, x: float, y: float, radius: float, feather: float, seconds: int) -> bytes:
    """同一圆形参数复用编码产物；调整圆心或羽化会得到独立的资源缓存键。"""
    return encode_mask(circle_mask_image(width, height, x, y, radius, feather), seconds)


def encode_mask(image: Image.Image, seconds: int) -> bytes:
    """将两类几何蒙版封装成单帧长时长 MP4，释放临时文件。"""
    with TemporaryDirectory(prefix="imv-preview-mask-") as directory:
        root = Path(directory)
        image.save(root / "mask.png")
        # 单帧时长是整数秒；固定秒级封装时基，避免长时长乘默认刻度后溢出。
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-framerate", f"1/{seconds}",
            "-i", str(root / "mask.png"), "-frames:v", "1", "-c:v", "libx264", "-threads", "1",
            "-pix_fmt", "yuv420p", "-video_track_timescale", "1", "-movflags", "+faststart", str(root / "mask.mp4"),
        ], check=True, capture_output=True, timeout=15)
        return (root / "mask.mp4").read_bytes()
