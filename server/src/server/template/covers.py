"""校验模板预览静态图，以固定对象名覆盖 ZOS 的 PNG，让浏览器再次使用时按 ETag 校验。"""
from contextlib import closing
from dataclasses import dataclass
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


def object_key(template_id: str, *, backup: bool = False) -> str:
    """公开地址固定，每个模板最多另存一份私有的已发布图片。"""
    suffix = ".previous" if backup else ""
    return f"imv/template-covers/{template_id}{suffix}.png"


def cover_url(template_id: str) -> str:
    """在事务内预写公开地址，确认数据库接受更新后才覆盖对象。"""
    try:
        return f"{ZosSettings().zos_web_url.rstrip('/')}/{object_key(template_id)}"
    except ValueError as exc:
        raise HTTPException(503, "封面更新失败，请重试") from exc


def upload_cover(template_id: str, content: bytes, revision: str, *, backup: bool = False) -> None:
    """一次 PUT 写入字节及其修订号；私有副本的空内容记录原图缺失。"""
    key = object_key(template_id, backup=backup)
    try:
        settings = ZosSettings()
        with closing(storage_client(settings, 10)) as storage:
            storage.put_object(Bucket=settings.zos_bucket, Key=key, Body=content, ACL="private" if backup else "public-read",
                               Metadata={"revision": revision}, ContentType="image/png", CacheControl="no-cache")
    except (ValueError, BotoCoreError, ClientError) as exc:
        raise HTTPException(503, "封面更新失败，请重试") from exc


@dataclass(frozen=True)
class StoredCover:
    """一次对象读取返回的 PNG 和修订号；升级前的公开图没有修订标签。"""

    content: bytes
    revision: str | None


class CoverRevisionMismatch(HTTPException):
    """数据库读取后两份对象都已换版，允许调用方在行锁内重新读取。"""

    def __init__(self):
        """对象版本仍不一致时向客户端报告可重试的错误。"""
        super().__init__(503, "封面版本已变化，请重试")


def stored_cover(template_id: str, *, backup: bool = False) -> StoredCover | None:
    """同一 GET 返回对象及其版本，仅对象缺失返回 None。"""
    try:
        settings = ZosSettings()
        with closing(storage_client(settings, 10)) as storage:
            result = storage.get_object(Bucket=settings.zos_bucket, Key=object_key(template_id, backup=backup))
            with closing(result["Body"]) as body:
                return StoredCover(body.read(), result.get("Metadata", {}).get("revision"))
    except (ValueError, BotoCoreError, ClientError) as exc:
        if isinstance(exc, ClientError) and exc.response["Error"]["Code"] in {"NoSuchKey", "NotFound", "404"}:
            return None
        raise HTTPException(503, "封面读取失败，请重试") from exc


def published_image(template_id: str, revision: str, *, before_replace: bool = False) -> bytes | None:
    """读取指定已发布版本。写方持行锁先备份它；遇到尚未提交的公开图时沿用原副本。"""
    image = stored_cover(template_id)
    if image is None or image.revision in (None, revision):
        content = image.content if image else None
        if before_replace:
            upload_cover(template_id, content or b"", revision, backup=True)
        return content
    previous = stored_cover(template_id, backup=True)
    if previous is None or previous.revision != revision:
        raise CoverRevisionMismatch()
    return previous.content or None


def delete_cover(template_id: str, *, undo_upload: bool = False) -> None:
    """撤销未提交的新增对象；删除模板时则按存储清理配置处理。"""
    try:
        settings = ZosSettings()
        if not undo_upload and not settings.zos_delete_objects:
            return
        with closing(storage_client(settings, 10)) as storage:
            if not undo_upload:
                storage.delete_object(Bucket=settings.zos_bucket, Key=object_key(template_id, backup=True))
            storage.delete_object(Bucket=settings.zos_bucket, Key=object_key(template_id))
    except (ValueError, BotoCoreError, ClientError) as exc:
        message = "封面更新失败，请重试" if undo_upload else "删除封面失败，请重试删除模板"
        raise HTTPException(503, message) from exc
