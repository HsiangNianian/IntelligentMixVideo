"""Validate and normalize uploaded still images before storage or model access."""

import hashlib
import warnings
from io import BytesIO
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

from .settings import Settings
from .models import Asset
from .store import Store, now


def save_image(content: bytes, store: Store, settings: Settings) -> Asset:
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
    payload = encoded.getvalue()
    asset = Asset(
        id=uuid4(),
        width=normalized.width,
        height=normalized.height,
        sha256=hashlib.sha256(payload).hexdigest(),
        created_at=now(),
    )
    directory = store.root / "assets"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{asset.id}.png"
    path.write_bytes(payload)
    try:
        store.save_asset(asset)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return asset
