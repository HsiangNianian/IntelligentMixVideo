"""校验模板预览静态图，将每次候选保存为独立的私有 PNG，按数据库选中的对象和修订读图。"""
from contextlib import closing
from io import BytesIO

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException
from PIL import Image

from ..remotion_templates.object_store import ObjectStoreError, ZosObjects
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


def object_prefix(template_id: str) -> str:
    """完整 UUID 标识本模板的存储前缀，包含私有版本目录和历史固定键。"""
    return f"imv/template-covers/{template_id}"


def upload_cover(key: str, content: bytes, revision: str) -> None:
    """即使请求超时后才真正写入，私有 PNG 也只会出现在本次尝试的独立键。"""
    try:
        settings = ZosSettings()
        with closing(storage_client(settings, 10)) as storage:
            storage.put_object(Bucket=settings.zos_bucket, Key=key, Body=content, ACL="private",
                               Metadata={"revision": revision}, ContentType="image/png", CacheControl="no-cache")
    except (ValueError, BotoCoreError, ClientError) as exc:
        raise HTTPException(503, "封面更新失败，请重试") from exc


def _read(key: str) -> tuple[bytes, str | None] | None:
    """一次 GET 取得字节和修订标签，旧对象可能没有标签，只有确认键缺失才返回 None。"""
    try:
        settings = ZosSettings()
        with closing(storage_client(settings, 10)) as storage:
            result = storage.get_object(Bucket=settings.zos_bucket, Key=key)
            with closing(result["Body"]) as body:
                return body.read(), result.get("Metadata", {}).get("revision")
    except (ValueError, BotoCoreError, ClientError) as exc:
        if isinstance(exc, ClientError) and exc.response["Error"]["Code"] in {"NoSuchKey", "NotFound", "404"}:
            return None
        raise HTTPException(503, "封面读取失败，请重试") from exc


def published_image(template_id: str, revision: str, object_id: str | None) -> bytes | None:
    """新发布只读选中的私有对象。旧库继续识别固定键及旧服务回滚时留下的副本。"""
    if object_id:
        image = _read(f"{object_prefix(template_id)}/{object_id}.png")
        if image is None:
            return None
        if image[1] == revision:
            return image[0]
    else:
        legacy = object_prefix(template_id)
        image = _read(legacy + ".png")
        if image is None:
            return None
        if image[1] in (None, revision):
            return image[0]
        previous = _read(legacy + ".previous.png")
        if previous and previous[1] == revision:
            return previous[0] or None
    raise HTTPException(503, "封面版本不一致，请重试")


def discard_upload(key: str) -> None:
    """按清理配置撤销本次未被数据库选中的私有对象。"""
    try:
        settings = ZosSettings()
        if settings.zos_delete_objects:
            with closing(storage_client(settings, 10)) as storage:
                storage.delete_object(Bucket=settings.zos_bucket, Key=key)
    except (ValueError, BotoCoreError, ClientError) as exc:
        raise HTTPException(503, "清理未发布封面失败，请重试") from exc


def delete_cover(template_id: str, *, had_publication: bool) -> None:
    """删除提交后按完整 UUID 清理。空模板也可能曾上传失败，未配置 ZOS 时仍可删除。"""
    try:
        settings = ZosSettings()
    except ValueError as exc:
        if not had_publication:
            return
        raise HTTPException(503, "模板已删除，清理封面失败，请重试删除") from exc
    try:
        # 此命名空间的模板标识均为固定长度的 UUID；一次前缀查询覆盖版本目录和两个历史固定键。
        ZosObjects(settings).delete_prefix(object_prefix(template_id))
    except ObjectStoreError as exc:
        raise HTTPException(503, "模板已删除，清理封面失败，请重试删除") from exc
