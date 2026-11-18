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


def cover_url(template_id: str) -> str:
    """在事务内预写公开地址，确认数据库接受更新后才覆盖对象。"""
    try:
        return f"{ZosSettings().zos_web_url.rstrip('/')}/{object_key(template_id)}"
    except ValueError as exc:
        raise HTTPException(503, "封面更新失败，请重试") from exc


def upload_cover(template_id: str, content: bytes) -> None:
    """单次 PUT 覆盖小对象；只有存储确认写入成功才发布新版本。"""
    key = object_key(template_id)
    try:
        settings = ZosSettings()
        with closing(storage_client(settings, 10)) as storage:
            storage.put_object(Bucket=settings.zos_bucket, Key=key, Body=content, ACL="public-read",
                               ContentType="image/png", CacheControl="no-cache")
    except (ValueError, BotoCoreError, ClientError) as exc:
        raise HTTPException(503, "封面更新失败，请重试") from exc


def stored_cover(template_id: str) -> bytes | None:
    """读取固定对象的字节；仅对象缺失返回 None，存储故障仍可重试。"""
    try:
        settings = ZosSettings()
        with closing(storage_client(settings, 10)) as storage:
            result = storage.get_object(Bucket=settings.zos_bucket, Key=object_key(template_id))
            with closing(result["Body"]) as body:
                return body.read()
    except (ValueError, BotoCoreError, ClientError) as exc:
        if isinstance(exc, ClientError) and exc.response["Error"]["Code"] in {"NoSuchKey", "NotFound", "404"}:
            return None
        raise HTTPException(503, "封面读取失败，请重试") from exc


def read_cover(template_id: str) -> bytes:
    """把已发布对象的缺失告知客户端，以便单独补图。"""
    content = stored_cover(template_id)
    if content is None:
        raise HTTPException(404, detail={"code": "cover_object_missing"})
    return content


def delete_cover(template_id: str, *, undo_upload: bool = False) -> None:
    """撤销未提交的新增对象；删除模板时则按存储清理配置处理。"""
    try:
        settings = ZosSettings()
        if not undo_upload and not settings.zos_delete_objects:
            return
        with closing(storage_client(settings, 10)) as storage:
            storage.delete_object(Bucket=settings.zos_bucket, Key=object_key(template_id))
    except (ValueError, BotoCoreError, ClientError) as exc:
        message = "封面更新失败，请重试" if undo_upload else "删除封面失败，请重试删除模板"
        raise HTTPException(503, message) from exc
