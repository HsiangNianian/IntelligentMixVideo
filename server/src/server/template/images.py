"""模板蒙版静态图片：入口解码并居中裁成 9:16，保留裁剪区原像素与 Alpha，以内容哈希长期保存。"""

from hashlib import sha256
from io import BytesIO
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
import warnings

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException
import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from ..video_composition.settings import ZosSettings
from ..video_composition.zos import upload_public


# 上传边界的固定限制；不提供环境变量覆盖。
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
STORAGE_TIMEOUT_SECONDS = 20


def save_image(content: bytes) -> str:
    """仅接受有界单帧图片；纠正方向、居中裁剪并去除元数据，不缩图、不去除透明度。"""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as source:
                if source.format not in {"PNG", "JPEG", "WEBP"} or getattr(source, "n_frames", 1) != 1:
                    raise ValueError("只支持静态 PNG、JPEG、WebP 图片")
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    raise ValueError("图片像素数量超过限制")
                source.load()
                normalized = ImageOps.exif_transpose(source).convert("RGBA")
                width = min(normalized.width, max(1, int(normalized.height * 9 / 16 + 0.5)))
                height = min(normalized.height, max(1, int(normalized.width * 16 / 9 + 0.5)))
                left, top = (normalized.width - width) // 2, (normalized.height - height) // 2
                normalized = normalized.crop((left, top, left + width, top + height))
                normalized.info.clear()
                encoded = BytesIO()
                normalized.save(encoded, format="PNG")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("图片损坏或尺寸过大，无法读取") from exc
    payload = encoded.getvalue()
    image_id = sha256(payload).hexdigest()
    try:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "image.png"
            path.write_bytes(payload)
            upload_public([(path, f"imv/template-mask/{image_id}.png", "image/png")],
                          ZosSettings(), STORAGE_TIMEOUT_SECONDS)
    except (OSError, ValueError, BotoCoreError, ClientError, httpx.HTTPError) as exc:
        logging.getLogger(__name__).exception("Template mask image upload failed")
        raise HTTPException(503, "蒙版图片上传失败，请检查服务端存储配置后重试") from exc
    return image_id


def read_image(image_id: str) -> bytes:
    """仅从服务端配置的固定对象目录读取已校验哈希；图片不随模板切换或删除而回收。"""
    try:
        settings = ZosSettings()
        with httpx.Client(timeout=STORAGE_TIMEOUT_SECONDS, follow_redirects=False) as client:
            response = client.get(f"{settings.zos_web_url.rstrip('/')}/imv/template-mask/{image_id}.png")
            if response.status_code == 404:
                raise HTTPException(404, "蒙版图片不存在，请重新上传")
            response.raise_for_status()
            content = response.content
        if sha256(content).hexdigest() != image_id:
            raise ValueError("stored image hash mismatch")
        return content
    except (ValueError, httpx.HTTPError) as exc:
        logging.getLogger(__name__).exception("Template mask image read failed")
        raise HTTPException(503, "蒙版图片读取失败，请重试或重新上传") from exc
