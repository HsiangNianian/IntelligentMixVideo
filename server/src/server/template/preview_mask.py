"""SDK 5.2.2 的圆角适配：只生成黑白几何蒙版，不接收或处理用户媒体。"""

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
    with TemporaryDirectory(prefix="imv-preview-mask-") as directory:
        root = Path(directory)
        mask_image(width, height, box_width, box_height, radius).save(root / "mask.png")
        # 单帧时长是整数秒；固定秒级封装时基，避免长时长乘默认刻度后溢出。
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-framerate", f"1/{seconds}",
            "-i", str(root / "mask.png"), "-frames:v", "1", "-c:v", "libx264", "-threads", "1",
            "-pix_fmt", "yuv420p", "-video_track_timescale", "1", "-movflags", "+faststart", str(root / "mask.mp4"),
        ], check=True, capture_output=True, timeout=15)
        return (root / "mask.mp4").read_bytes()
