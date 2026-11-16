"""Validate still images and return normalized PNG bytes; reference images never enter object storage."""

import warnings
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from .settings import Settings


def normalize_image(content: bytes, settings: Settings) -> bytes:
    """Reject videos/animations and decompression bombs; strip metadata through PNG encoding."""
    if not content or len(content) > settings.max_upload_bytes:
        raise ValueError("image is empty or exceeds the upload size limit")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as source:
                if source.format not in {"PNG", "JPEG", "WEBP"}:
                    raise ValueError(
                        "only PNG, JPEG, and WebP still images are supported; video is reserved"
                    )
                if getattr(source, "n_frames", 1) != 1:
                    raise ValueError("animated images are not supported")
                if source.width * source.height > settings.max_image_pixels:
                    raise ValueError("image pixel count exceeds the configured limit")
                source.load()
                normalized = ImageOps.exif_transpose(source).convert("RGBA")
                normalized.thumbnail((2048, 2048))
                encoded = BytesIO()
                normalized.save(encoded, format="PNG")
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError("invalid or oversized image") from exc
    return encoded.getvalue()
