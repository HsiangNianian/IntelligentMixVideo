"""校验模板预览静态图，以固定对象名覆盖 ZOS 的 PNG，让浏览器再次使用时按 ETag 校验。"""
from contextlib import closing
from io import BytesIO

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException
from PIL import Image

from ..video_composition.settings import ZosSettings
from ..video_composition.zos import storage_client

MAX_BYTES = 4 * 1024 * 1024


def encode_cover(content: bytes) -> bytes:
    """接受内存里的 PNG 竖屏截图；有界解码、统一尺寸并移除来源元数据。"""
    try:
        with Image.open(BytesIO(content)) as image:
            w, h = image.size
            if image.format != "PNG" or image.n_frames != 1 or max(w, h) > 4096 or abs(w * 16 - h * 9) > 20:
                raise ValueError
            image.thumbnail((540, 960), Image.Resampling.LANCZOS)
            buffer = BytesIO()
            pixels = image.convert("RGB")
            pixels.info.clear()
            pixels.save(buffer, format="PNG")
            return buffer.getvalue()
    except (ValueError, OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, "封面必须是 9:16 的单帧 PNG") from exc


def object_key(template_id: str) -> str:
    """同一模板的发布、读取和删除共用固定 PNG 对象。"""
    return f"imv/template-covers/{template_id}.png"


def upload_cover(template_id: str, content: bytes) -> str:
    """单次 PUT 覆盖小对象；只有存储确认写入成功才发布新版本。"""
    key = object_key(template_id)
    try:
        settings = ZosSettings()
        with closing(storage_client(settings, 10)) as storage:
            storage.put_object(Bucket=settings.zos_bucket, Key=key, Body=content, ACL="public-read",
                               ContentType="image/png", CacheControl="no-cache")
        return f"{settings.zos_web_url.rstrip('/')}/{key}"
    except (ValueError, BotoCoreError, ClientError) as exc:
        raise HTTPException(503, "封面更新失败，请重试") from exc


def read_cover(template_id: str) -> bytes:
    """通过固定对象名取当前图片，客户端无需访问跨域对象存储的像素。"""
    try:
        settings = ZosSettings()
        with closing(storage_client(settings, 10)) as storage:
            result = storage.get_object(Bucket=settings.zos_bucket, Key=object_key(template_id))
            with closing(result["Body"]) as body:
                return body.read()
    except (ValueError, BotoCoreError, ClientError) as exc:
        raise HTTPException(503, "封面读取失败，请重试") from exc


def delete_cover(template_id: str) -> None:
    """删除模板时一并释放该模板已发布的图片。"""
    try:
        settings = ZosSettings()
        if not settings.zos_delete_objects:
            return
        with closing(storage_client(settings, 10)) as storage:
            storage.delete_object(Bucket=settings.zos_bucket, Key=object_key(template_id))
    except (ValueError, BotoCoreError, ClientError) as exc:
        raise HTTPException(503, "删除封面失败，请重试删除模板") from exc
