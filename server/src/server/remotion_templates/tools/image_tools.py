"""Deterministic PR76 image info/resize/crop tools; super-resolution is intentionally out of scope."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from uuid import uuid4

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from .contracts import ImageCropInput, ImageInfoInput, ImageResizeInput


class ImageToolError(ValueError):
    """Structured image failure that the Agent dispatcher can expose as a failed receipt."""

    def __init__(self, code: str, message: str, *, field: str | None = None):
        """Keep a stable code separate from the human diagnostic."""
        super().__init__(f"{code}: {message}")
        self.code, self.field = code, field


def _root(settings) -> Path:
    """Return the task-independent private image directory."""
    root = settings.data_dir if settings.data_dir.is_absolute() else Path(settings.data_dir)
    return root / "tool-images"


def _url(settings, identifier: str) -> str:
    """Build a local API URL that can be fetched by subsequent tool calls during debugging."""
    base = getattr(settings, "tool_asset_base_url", None) or "http://127.0.0.1:20070/api/templates/tool-assets"
    return str(base).rstrip("/") + f"/{identifier}.png"


async def _fetch(request: ImageInfoInput, settings) -> tuple[Image.Image, str, int, bool]:
    """Fetch one bounded still image and normalize orientation without changing the source URL."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            response = await client.get(request.image_url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ImageToolError("IMAGE_FETCH_FAILED", "图片地址读取失败。") from exc
    payload = response.content
    if len(payload) > settings.max_upload_bytes:
        raise ImageToolError("RESOURCE_LIMIT_EXCEEDED", "图片超过大小限制。")
    try:
        source = Image.open(BytesIO(payload))
        if source.format not in {"PNG", "JPEG", "WEBP"} or getattr(source, "n_frames", 1) != 1:
            raise ImageToolError("UNSUPPORTED_IMAGE", "只支持单帧 PNG、JPEG 或 WebP 图片。")
        if source.width * source.height > settings.max_image_pixels:
            raise ImageToolError("RESOURCE_LIMIT_EXCEEDED", "图片像素数超过限制。")
        source.load()
        has_alpha = "A" in source.getbands()
        return ImageOps.exif_transpose(source).convert("RGBA"), source.format.lower(), len(payload), has_alpha
    except ImageToolError:
        raise
    except (OSError, UnidentifiedImageError) as exc:
        raise ImageToolError("IMAGE_DECODE_FAILED", "图片解码失败。") from exc


def _save(image: Image.Image, settings) -> dict:
    """Write a new metadata-free PNG and return the PR76 ImageInfo shape."""
    identifier = uuid4().hex
    root = _root(settings)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{identifier}.png"
    encoded = BytesIO()
    image.save(encoded, format="PNG")
    payload = encoded.getvalue()
    path.write_bytes(payload)
    return {
        "image_url": _url(settings, identifier),
        "width": image.width,
        "height": image.height,
        "mime_type": "image/png",
        "size_bytes": len(payload),
        "has_alpha": "A" in image.getbands(),
    }


async def execute_image(name: str, args, settings) -> dict:
    """Execute image.info, image.resize or image.crop with shared bounded decoding."""
    image, source_format, source_size, source_alpha = await _fetch(args, settings)
    if name == "image.info":
        return {
            "image_url": args.image_url,
            "width": image.width,
            "height": image.height,
            "mime_type": f"image/{source_format}",
            "size_bytes": source_size,
            "has_alpha": source_alpha,
        }
    if name == "image.resize":
        assert isinstance(args, ImageResizeInput)
        return _save(image.resize((args.width, args.height), Image.Resampling.LANCZOS), settings)
    if name == "image.crop":
        assert isinstance(args, ImageCropInput)
        if args.x + args.width > image.width or args.y + args.height > image.height:
            raise ImageToolError("CROP_OUT_OF_BOUNDS", "裁剪区域超出图片边界。")
        return _save(image.crop((args.x, args.y, args.x + args.width, args.y + args.height)), settings)
    raise ImageToolError("INVALID_ARGUMENT", f"不支持的图片工具：{name}")
