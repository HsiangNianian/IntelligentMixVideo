"""把模板上下色栏生成透明 PNG 并转存 ZOS；在线程池调用，不改变源视频与文字。"""

from math import floor
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw

from .settings import ZosSettings
from .zos import upload_public


def upload_mask(mask: dict, width: int, height: int, task_id: str, settings: ZosSettings,
                timeout: float, prefix: str) -> str | None:
    """使用已校验的模板快照，按成片像素四舍五入；关闭或零高度时不生成与上传。"""
    rects = []
    for edge in ("top", "bottom"):
        bar = mask[edge]
        size = floor(height * bar["height"] / 100 + 0.5) if bar["enabled"] else 0
        if size:
            rects.append((0 if edge == "top" else height - size, size, bar["color"]))
    if not rects:
        return None
    with TemporaryDirectory() as directory:
        path = Path(directory) / "frame-mask.png"
        with Image.new("RGBA", (width, height)) as image:
            draw = ImageDraw.Draw(image)
            for y, size, color in rects:
                draw.rectangle((0, y, width - 1, y + size - 1), fill=color)
            image.save(path)
        return upload_public([(path, f"{prefix}/{task_id}-mask.png", "image/png")], settings, timeout)[0]
