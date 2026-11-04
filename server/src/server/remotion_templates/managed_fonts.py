"""Extract trusted system SC faces for browser use; uploads remain in the separate font-library path.

CSS family aliases cannot select a TTC member. Select by the internal family and weight, then emit a standalone
OpenType font shared by previews, layout probes and renders. Cache by source bytes so replacing a file invalidates it.
"""

import hashlib
import json
import logging
import lzma
import struct
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import NamedTuple

from fontTools.ttLib import TTCollection, TTFont, TTLibError

from .settings import Settings

FAMILY = "Noto Sans CJK SC"
logger = logging.getLogger(__name__)


class ManagedFont(NamedTuple):
    """A verified standalone face and the original source fingerprint used by sealed versions."""

    data: bytes
    postscript_name: str
    source_sha256: str

    @property
    def media_type(self) -> str:
        """Return the actual SFNT format, independent of the configured source extension."""
        return "font/otf" if self.data[:4] == b"OTTO" else "font/ttf"


def managed_font(path: Path, weight: int) -> ManagedFont:
    """Prefer the configured SC face; an absent, corrupt or wrong-region source uses the bundled Chinese font."""
    if weight not in (400, 700):
        raise ValueError("Unsupported managed font weight")
    try:
        data = path.read_bytes()
    except OSError:
        logger.warning("Managed font is unavailable; using bundled SC weight %s", weight)
        return bundled_font(weight)
    return _select(data, weight)


@lru_cache(maxsize=4)
def _select(data: bytes, weight: int) -> ManagedFont:
    """Cache selection and fallback by content, retaining source hashes for existing sealed-version compatibility."""
    try:
        return _extract(data, weight, FAMILY)
    except (ValueError, TTLibError, KeyError, IndexError, AssertionError, EOFError, struct.error):
        logger.warning("Managed font lacks a usable SC face; using bundled SC weight %s", weight)
        return bundled_font(weight)._replace(source_sha256=hashlib.sha256(data).hexdigest())


@lru_cache(maxsize=2)
def bundled_font(weight: int) -> ManagedFont:
    """Use the already shipped, hash-pinned Noto Sans SC variable font without network or registry dependencies."""
    catalog = json.loads(Path(__file__).with_name("font_catalog.json").read_text(encoding="utf-8"))
    spec = next(item for item in catalog if item["family"] == "Noto Sans SC")
    data = lzma.decompress((Path(__file__).with_name("bundled_fonts") / f'{spec["filename"]}.xz').read_bytes())
    if len(data) != spec["size"] or hashlib.sha256(data).hexdigest() != spec["sha256"]:
        raise ValueError("Bundled SC font does not match its catalog")
    return _extract(data, weight, "Noto Sans SC")


def _extract(data: bytes, weight: int, family: str) -> ManagedFont:
    """Decode only trusted fonts; never choose a collection index or rename another regional face."""
    source = BytesIO(data)
    opened = None
    try:
        options = dict(lazy=True, recalcBBoxes=False, recalcTimestamp=False)
        collection = data[:4] == b"ttcf"
        opened = TTCollection(source, **options) if collection else TTFont(source, **options)
        faces = opened.fonts if collection else [opened]
        for face in faces:
            if face["name"].getBestFamilyName() != family:
                continue
            axes = {axis.axisTag: axis for axis in face["fvar"].axes} if "fvar" in face else {}
            axis = axes.get("wght")
            matches_weight = axis.minValue <= weight <= axis.maxValue if axis else face["OS/2"].usWeightClass == weight
            if not matches_weight or face["OS/2"].fsSelection & 1:
                continue
            postscript = face["name"].getDebugName(6)
            if not postscript:
                raise ValueError("Managed SC font has no PostScript name")
            output = BytesIO()
            if collection:
                face.save(output)
            return ManagedFont(output.getvalue() if collection else data, postscript, hashlib.sha256(data).hexdigest())
        raise ValueError(f"Managed font lacks {family} weight {weight}")
    except (TTLibError, KeyError, IndexError) as exc:
        raise ValueError("Invalid managed font") from exc
    finally:
        if opened is not None:
            opened.close()


def prepare_fonts(settings: Settings, directory: Path) -> dict[str, str]:
    """Write the same SC files for all worker loaders and their expected browser identities; return source hashes."""
    public = directory / "public"
    public.mkdir(parents=True, exist_ok=True)
    identities = {}
    fingerprints = {}
    for weight, source in ((400, settings.font_regular), (700, settings.font_bold)):
        font = managed_font(source, weight)
        (public / f"font-{weight}.otf").write_bytes(font.data)
        identities[str(weight)] = font.postscript_name
        fingerprints[f"font_{weight}"] = font.source_sha256
    fallback = bundled_font(400)
    (public / "font-fallback.otf").write_bytes(fallback.data)
    identities["fallback"] = fallback.postscript_name
    (public / "fonts.json").write_text(json.dumps(identities), encoding="utf-8")
    return fingerprints
