"""Fonts for Remotion effects: a MySQL registry, ZOS objects and a verified local cache.

Three kinds of font share one registry (`font_store.py`): the system default (installed with the OS, so it has no
file here), the preinstalled catalog (`font_catalog.json`; the files ship inside this package as `bundled_fonts/*.xz`,
each pinned by size and SHA-256, and are copied into the object store once, so no network is needed) and fonts users
upload. The registry
row names the font by a unique family name; the file is an object under `imv/remotion/fonts/<id>/` (ZOS when
configured, the data folder otherwise, see `object_store.py`).

The render sandbox has no network, so the backend keeps every font in a local cache (`<data dir>/fonts/files`, a file
is listed only when its SHA-256 matches the registry) next to a generated `fonts.conf`. The sandbox mounts that folder
read-only and points fontconfig at it, so Chrome in every worker (validation, preview build, overlay render, video
composition) resolves a family by its CSS name. The browser preview cannot use fontconfig: it registers lazy
`FontFace`s served by `GET /api/templates/fonts/{id}`. Uploaded font bytes are never parsed by the backend beyond a
bounds-checked read of the family name; only the sandboxed Chrome and the user's own browser decode them.
"""

import asyncio
import hashlib
import json
import logging
import lzma
import os
import struct
import subprocess
import threading
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Literal
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
from pydantic import BaseModel, Field

from . import font_store
from .categories import FONT_PARAMETERS
from .object_store import PREFIX, Objects
from .settings import Settings

logger = logging.getLogger(__name__)

CATALOG = Path(__file__).with_name("font_catalog.json")
# The catalog fonts, xz-compressed, as `<filename>.xz`; shipped inside the package.
BUNDLE = Path(__file__).with_name("bundled_fonts")
CONFIG_NAME = "fonts.conf"
# Where the sandbox sees the font folder; the generated config refers to this path, never to the host's.
SANDBOX_DIR = "/imv-fonts"
# Always available through the system packages and the default family of every generated text effect.
DEFAULT_FAMILY = "Noto Sans CJK SC"
# Names a user's font may not take: CSS generic families and the default font.
RESERVED_FAMILIES = {DEFAULT_FAMILY.casefold(), "sans-serif", "serif", "monospace", "cursive", "fantasy", "system-ui", "inherit", "initial", "default"}
SYSTEM_FONT_ID = "noto-sans-cjk"
# A stored `fontFamily` parameter is a plain string of at most this many characters (family names are limited likewise).
FONT_FAMILY_MAX_LENGTH = 100
SFNT_MAGIC = (b"\x00\x01\x00\x00", b"OTTO", b"true")
# Preview strips are drawn black on a transparent background and tinted by the page through a CSS mask.
PREVIEW_MAX = (640, 96)


class FontRejected(ValueError):
    """An uploaded font or preview was refused; the message is safe to show to the user."""


class Font(BaseModel):
    """A registered font: what the registry stores and the API shows."""

    id: str
    family: str
    label: str
    style: str
    weights: list[int] = Field(min_length=1, max_length=2)
    filename: str
    sha256: str
    size: int
    source: Literal["system", "builtin", "upload"]
    license: str = ""
    preview: bool = False

    @property
    def weight_text(self) -> str:
        """CSS `font-weight` descriptor: `400` or a range such as `200 900`."""
        return " ".join(str(weight) for weight in self.weights)

    @property
    def has_file(self) -> bool:
        """Whether the font is a file this backend stores and caches; the system default is installed with the OS."""
        return self.source != "system"

    @property
    def suffix(self) -> str:
        """File extension, `.ttf` or `.otf`."""
        return Path(self.filename).suffix.lower()

    @property
    def content_type(self) -> str:
        """Media type of the font file."""
        return "font/ttf" if self.suffix == ".ttf" else "font/otf"

    @property
    def object_key(self) -> str:
        """Object key of the font file."""
        return f"{PREFIX}/fonts/{self.id}/font{self.suffix}"

    @property
    def preview_key(self) -> str:
        """Object key of the preview strip."""
        return f"{PREFIX}/fonts/{self.id}/preview.png"

    @property
    def cache_name(self) -> str:
        """File name in the local cache."""
        return f"{self.id}{self.suffix}"


class FontSpec(BaseModel):
    """One catalog font: a bundled file pinned by size and SHA-256 (of the uncompressed font) that is copied into the
    registry once. `url` only records where the file came from; nothing is downloaded at run time."""

    id: str = Field(pattern=r"^[a-z0-9-]{1,40}$")
    family: str
    label: str
    style: str
    weights: list[int] = Field(min_length=1, max_length=2)
    filename: str = Field(pattern=r"^[A-Za-z0-9_.-]+\.(ttf|otf)$")
    url: str = Field(pattern=r"^https://")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(gt=0)
    license: str

    def font(self) -> Font:
        """The registry view of this catalog font."""
        return Font(**self.model_dump(exclude={"url"}), source="builtin", preview=True)


@lru_cache(maxsize=1)
def load_catalog() -> tuple[FontSpec, ...]:
    """Read and validate the bundled catalog once; a bad entry or duplicate id stops the first use loudly."""
    specs = tuple(FontSpec.model_validate(item) for item in json.loads(CATALOG.read_text(encoding="utf-8")))
    if len({spec.id for spec in specs}) != len(specs) or len({spec.family.casefold() for spec in specs}) != len(specs):
        raise ValueError("font catalog ids and families must be unique")
    return specs


def font_root(settings: Settings) -> Path:
    """Folder holding the verified cache files and the generated fontconfig file."""
    return settings.data_dir / "fonts"


def open_font_schema(schema: dict) -> dict:
    """The stored form of a Sprite's font parameters (`fontFamily`, a two-tier text's `line1FontFamily`/`line2FontFamily`):
    the enum the Agent chose from becomes a plain string.

    The Agent may only pick families of the font library, but the library keeps growing with uploads, so a frozen enum
    would refuse fonts added later in every validator that reads the stored schema. Choices are checked against the
    live library instead (`require_family`). Returns the schema unchanged when it has no such parameter.
    """
    properties = schema.get("properties", {})
    opened = {
        name: {key: value for key, value in definition.items() if key != "enum"} | {"maxLength": FONT_FAMILY_MAX_LENGTH}
        for name in FONT_PARAMETERS
        if isinstance(definition := properties.get(name), dict) and "enum" in definition
    }
    return {**schema, "properties": {**properties, **opened}} if opened else schema


def require_family(family: object, name: str = "fontFamily") -> None:
    """Raise a user-facing ValueError unless `family` (the value of font parameter `name`) is a family of the live font
    library (when a library is attached)."""
    store = FontStore.active
    if store is not None and family not in store.families():
        raise ValueError(f"参数 {name} 不是字体库里的字体：{family}")


def sha256_of(path: Path) -> str:
    """Hash a file in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_family(data: bytes) -> str:
    """The family name of a TrueType/OpenType font, read from its `name` table without a font library.

    Every offset is bounds-checked, so a malformed file only raises `FontRejected`. The typographic family (name ID 16)
    wins over the legacy one (ID 1). Collections and web fonts (`ttcf`, WOFF) are not accepted.
    """
    try:
        if data[:4] not in SFNT_MAGIC:
            raise FontRejected("只支持 TrueType / OpenType 字体文件（.ttf、.otf）")
        tables = struct.unpack_from(">H", data, 4)[0]
        name_table = next(
            (struct.unpack_from(">II", data, 12 + 16 * index + 8) for index in range(tables) if data[12 + 16 * index:16 + 16 * index] == b"name"),
            None,
        )
        if name_table is None:
            raise FontRejected("字体文件缺少名称表")
        offset, length = name_table
        table = data[offset:offset + length]
        count, strings = struct.unpack_from(">HH", table, 2)
        found: dict[int, str] = {}
        for index in range(count):
            platform, _encoding, _language, name_id, size, start = struct.unpack_from(">HHHHHH", table, 6 + 12 * index)
            if name_id not in (1, 16) or name_id in found:
                continue
            raw = table[strings + start:strings + start + size]
            if len(raw) != size:
                continue
            text = raw.decode("utf-16-be" if platform in (0, 3) else "mac_roman", errors="ignore").strip()
            if text:
                found[name_id] = text
        family = found.get(16) or found.get(1)
    except (struct.error, IndexError) as exc:
        raise FontRejected("字体文件已损坏或不完整") from exc
    if not family or len(family) > 100 or not family.isprintable():
        raise FontRejected("无法读取字体的家族名称")
    return family


@lru_cache(maxsize=1)
def system_families() -> frozenset[str]:
    """Case-folded family names of the fonts installed on this host (what the sandbox's /usr provides), or empty."""
    try:
        listed = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True, timeout=15, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    return frozenset(name.strip().casefold() for line in listed.splitlines() for name in line.split(","))


def render_preview(path: Path, text: str) -> bytes:
    """Draw `text` in a trusted font as a black-on-transparent PNG strip (used for the preinstalled catalog only)."""
    font = ImageFont.truetype(str(path), 48)
    try:
        # A variable font opens at its lightest instance; the strip shows the regular weight like the effect's default.
        axes = font.get_variation_axes()
        font.set_variation_by_axes([
            min(max(400, axis["minimum"]), axis["maximum"]) if "weight" in str(axis["name"]).lower() else axis["default"] for axis in axes
        ])
    except (OSError, AttributeError):
        pass  # a static font has no axes
    left, top, right, _bottom = font.getbbox(text)
    image = Image.new("RGBA", (min(PREVIEW_MAX[0], right - left + 16), 64), (0, 0, 0, 0))
    ImageDraw.Draw(image).text((8 - left, 8 - top), text, font=font, fill=(0, 0, 0, 255))
    encoded = BytesIO()
    image.save(encoded, format="PNG")
    return encoded.getvalue()


def normalize_preview(content: bytes) -> bytes:
    """Keep only the alpha of a client-drawn preview strip, black on transparent and within `PREVIEW_MAX`."""
    try:
        with Image.open(BytesIO(content)) as source:
            if source.format != "PNG" or source.width > PREVIEW_MAX[0] * 2 or source.height > PREVIEW_MAX[1] * 2:
                raise FontRejected("预览图必须是不超过 1280×192 的 PNG")
            alpha = source.convert("RGBA").getchannel("A")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise FontRejected("预览图无效") from exc
    alpha.thumbnail(PREVIEW_MAX)
    image = Image.new("RGBA", alpha.size, (0, 0, 0, 0))
    image.putalpha(alpha)
    encoded = BytesIO()
    image.save(encoded, format="PNG")
    return encoded.getvalue()


class FontStore:
    """The registry, its objects and the verified local cache the sandbox mounts."""

    _stores: dict[Path, "FontStore"] = {}
    # The store the running Remotion runtime attached; render guards use it without needing settings.
    active: "FontStore | None" = None

    def __init__(self, settings: Settings) -> None:
        """Construct without touching the data directory or the database; `attach` and `refresh` do."""
        self.root = font_root(settings)
        self.install_bundled = settings.install_bundled_fonts
        self.max_upload = settings.font_upload_max_bytes
        self.system_file = settings.font_regular
        self.known: dict[str, Font] = {}
        self.ready: set[str] = set()
        self.objects: Objects | None = None
        self._lock = threading.Lock()

    @classmethod
    def for_settings(cls, settings: Settings) -> "FontStore":
        """The one store of a font folder, shared by the Runtime, the Agent prompt and the routes."""
        return cls._stores.setdefault(font_root(settings), cls(settings))

    def attach(self, objects: Objects) -> None:
        """Bind the object store and make this the store render guards consult."""
        self.objects = objects
        FontStore.active = self

    @property
    def files(self) -> Path:
        """Folder of verified cache files."""
        return self.root / "files"

    def refresh(self) -> None:
        """Reload the registry, then re-verify the cache against it and rewrite the fontconfig file. Blocking.

        The system default is registered first when missing. A cached file that is missing or whose size or hash
        differs from its registry row is not listed (a wrong one is deleted so the next sync fetches it again).
        """
        records = font_store.listing()
        if self.objects is not None and not any(record["family"] == DEFAULT_FAMILY for record in records):
            self._register_system()
            records = font_store.listing()
        # The default first, then the preinstalled catalog, then uploads; each group keeps its registration order.
        records.sort(key=lambda record: ("system", "builtin", "upload").index(record["source"]))
        with self._lock:
            self.known = {
                record["id"]: Font(**{key: value for key, value in record.items() if key != "device_id"}) for record in records
            }
            self.files.mkdir(parents=True, exist_ok=True)
            ready: set[str] = {font.id for font in self.known.values() if not font.has_file}
            for font in self.known.values():
                if not font.has_file:
                    continue
                path = self.files / font.cache_name
                try:
                    if path.stat().st_size == font.size and sha256_of(path) == font.sha256:
                        ready.add(font.id)
                        continue
                except OSError:
                    continue
                logger.warning("Cached font %s does not match its registry row; deleting it", font.id)
                path.unlink(missing_ok=True)
            self.ready = ready
            self._write_config()

    def _register_system(self) -> None:
        """Register the system default font so the library the Agent chooses from is never empty; blocking.

        It has no file to store, only a preview strip drawn from the installed font (a missing strip just means the
        picker shows the name in plain text).
        """
        assert self.objects is not None
        font = Font(
            id=SYSTEM_FONT_ID, family=DEFAULT_FAMILY, label="思源黑体（默认）", weights=[400, 700], source="system",
            style="default clean sans-serif (Hei) typeface, neutral and readable; weights 400 and 700",
            filename="system.ttc", sha256="", size=0, preview=True,
        )
        self.root.mkdir(parents=True, exist_ok=True)
        strip = self.root / f"{font.id}.preview.png"
        try:
            strip.write_bytes(render_preview(self.system_file, font.label))
            self.objects.put(font.preview_key, strip, "image/png")
        except (OSError, ValueError) as exc:
            logger.warning("The default font has no preview strip: %s", exc)
            font = font.model_copy(update={"preview": False})
        finally:
            strip.unlink(missing_ok=True)
        font_store.insert(font.id, font.family, font.model_dump(exclude={"id", "family"}), None)

    def _write_config(self) -> None:
        """Atomically write the fontconfig file the sandbox loads: the system config plus the font folder."""
        text = (
            '<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n<fontconfig>\n'
            '  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>\n'
            f"  <dir>{SANDBOX_DIR}/files</dir>\n</fontconfig>\n"
        )
        target = self.root / CONFIG_NAME
        temporary = target.with_suffix(".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, target)

    def _accept(self, font: Font) -> None:
        """List a registered, cached font and rewrite the config so workers started from now on see it."""
        with self._lock:
            self.known[font.id] = font
            self.ready.add(font.id)
            self._write_config()

    def available(self) -> list[Font]:
        """Registered fonts whose verified file is in the local cache, in registration order."""
        return [font for font in self.known.values() if font.id in self.ready]

    def listing(self) -> list[Font]:
        """Every registered font, whether or not this host has cached it yet."""
        return list(self.known.values())

    def families(self) -> list[str]:
        """Family names of the whole library: the enum the Agent chooses from and `fontFamily` values are checked against."""
        return [font.family for font in self.known.values()]

    def path(self, font_id: str) -> Path | None:
        """Cached file of an available font, or None (also for the system default, which has no file here)."""
        font = self.known.get(font_id)
        return self.files / font.cache_name if font and font.has_file and font.id in self.ready else None

    def named(self, source: str) -> list[Font]:
        """Registered fonts whose family `source` (a render's code and parameter values) mentions."""
        return [font for font in self.known.values() if font.family in source]

    def _cache(self, font: Font) -> bool:
        """Copy a font from the object store into the cache if its hash matches the registry; blocking."""
        if self.objects is None:
            return False
        self.files.mkdir(parents=True, exist_ok=True)
        temporary = self.files / f"{font.cache_name}.{uuid4().hex}.part"
        try:
            self.objects.fetch(font.object_key, temporary)
            if temporary.stat().st_size != font.size or sha256_of(temporary) != font.sha256:
                raise ValueError("does not match the registry hash")
            os.replace(temporary, self.files / font.cache_name)
        except (OSError, ValueError) as exc:
            logger.warning("Font %s could not be cached: %s", font.id, exc)
            return False
        finally:
            temporary.unlink(missing_ok=True)
        self._accept(font)
        return True

    def ensure(self, font_id: str) -> Path | None:
        """The cached file of a registered font, fetching it from the object store first when needed; blocking."""
        font = self.known.get(font_id)
        if font is None or not font.has_file:
            return None
        return self.path(font_id) or (self.files / font.cache_name if self._cache(font) else None)

    async def require(self, named: list[Font]) -> list[str]:
        """Cache whichever of the `named` fonts is not available yet; returns the families still missing.

        A family that is named but not cached would be drawn in the default font without any error, so a render
        waits here and refuses to continue with the missing ones.
        """
        for font in named:
            if font.id not in self.ready:
                await asyncio.to_thread(self._cache, font)
        return [font.family for font in named if font.id not in self.ready]

    async def sync(self) -> None:
        """Install the bundled catalog fonts the registry lacks (unless switched off), then cache every registered font.

        One failure (missing or corrupt bundled file, object store) is logged and leaves that font for the next sync;
        the others proceed.
        """
        if self.objects is None:
            return
        await asyncio.to_thread(self.refresh)
        for spec in load_catalog() if self.install_bundled else ():
            if spec.id in self.known:
                continue
            try:
                await asyncio.to_thread(self._seed, spec)
            except Exception as exc:
                logger.warning("Font %s was not installed: %s", spec.id, exc)
        for font in list(self.known.values()):
            if font.has_file and font.id not in self.ready:
                await asyncio.to_thread(self._cache, font)

    def _seed(self, spec: FontSpec) -> None:
        """Unpack one bundled font, check its size and SHA-256 against the catalog, then register it; blocking."""
        font = spec.font()
        self.files.mkdir(parents=True, exist_ok=True)
        staged = self.files / f"{font.cache_name}.{uuid4().hex}.part"
        digest, received = hashlib.sha256(), 0
        try:
            with lzma.open(BUNDLE / f"{spec.filename}.xz") as packed, staged.open("wb") as handle:
                while chunk := packed.read(1 << 20):
                    received += len(chunk)
                    if received > spec.size:
                        raise ValueError("larger than the pinned size")
                    digest.update(chunk)
                    handle.write(chunk)
            if received != spec.size or digest.hexdigest() != spec.sha256:
                raise ValueError("does not match the pinned size and SHA-256")
            self._register(font, staged, None)
        finally:
            staged.unlink(missing_ok=True)

    def _register(self, font: Font, staged: Path, device: str | None) -> bool:
        """Store a verified font file (and the preview strip when `font.preview`) as objects, add the registry row and
        cache the file; blocking.

        A catalog font's strip is drawn here from the pinned file; an uploaded font's strip was stored by `upload`
        already. Returns False when the registry already has this ID or family (another sync or upload won). A strip
        that cannot be drawn only means the picker shows the name in plain text.
        """
        assert self.objects is not None
        if font.source == "builtin":
            strip = staged.with_suffix(".png")
            try:
                strip.write_bytes(render_preview(staged, font.label))
                self.objects.put(font.preview_key, strip, "image/png")
            except (OSError, ValueError) as exc:
                logger.warning("Font %s has no preview strip: %s", font.id, exc)
                font = font.model_copy(update={"preview": False})
            finally:
                strip.unlink(missing_ok=True)
        self.objects.put(font.object_key, staged, font.content_type)
        if not font_store.insert(font.id, font.family, font.model_dump(exclude={"id", "family"}), device):
            return False
        self.files.mkdir(parents=True, exist_ok=True)
        os.replace(staged, self.files / font.cache_name)
        self._accept(font)
        return True

    def upload(self, content: bytes, preview: bytes | None, label: str, device: str) -> Font:
        """Validate a user's font file, store it and register it; blocking. Raises `FontRejected` with a user message.

        The file must be a plain TrueType/OpenType font within the size limit whose family name is new: it may not
        repeat a registered or installed family or a reserved name, because the family is what templates store and
        what the sandbox resolves. The backend only reads that name; decoding is left to the sandboxed browser. The
        preview strip is drawn by the uploader's own browser (the font is decoded nowhere else) and kept as alpha only.
        """
        if not content or len(content) > self.max_upload:
            raise FontRejected(f"字体文件为空或超过 {self.max_upload // (1024 * 1024)} MiB 上限")
        family = read_family(content)
        taken = {font.family.casefold() for font in self.known.values()} | RESERVED_FAMILIES | system_families()
        if family.casefold() in taken:
            raise FontRejected(f"已存在名为“{family}”的字体，请使用家族名称不同的字体文件")
        label = (label or "").strip() or family
        if len(label) > 100:
            raise FontRejected("字体显示名称最多 100 个字符")
        strip = normalize_preview(preview) if preview else None
        digest = hashlib.sha256(content).hexdigest()
        suffix = ".otf" if content[:4] == b"OTTO" else ".ttf"
        font = Font(
            id=f"u-{digest[:16]}", family=family, label=label, style="uploaded by a user", weights=[400],
            filename=f"{digest[:16]}{suffix}", sha256=digest, size=len(content), source="upload", preview=strip is not None,
        )
        if font.id in self.known:
            raise FontRejected("这个字体文件已经上传过了")
        self.files.mkdir(parents=True, exist_ok=True)
        staged = self.files / f"{font.cache_name}.{uuid4().hex}.part"
        strip_file = staged.with_suffix(".png")
        try:
            staged.write_bytes(content)
            if strip is not None:
                strip_file.write_bytes(strip)
                assert self.objects is not None
                self.objects.put(font.preview_key, strip_file, "image/png")
            if not self._register(font, staged, device):
                raise FontRejected("已存在同名字体或相同文件")
        finally:
            staged.unlink(missing_ok=True)
            strip_file.unlink(missing_ok=True)
        return font

    def preview_path(self, font_id: str) -> Path | None:
        """Cached preview strip of a font, fetched from the object store on first use; blocking."""
        font = self.known.get(font_id)
        if font is None or not font.preview or self.objects is None:
            return None
        path = self.root / "previews" / f"{font.id}.png"
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(f".{uuid4().hex}.part")
            try:
                self.objects.fetch(font.preview_key, temporary)
                os.replace(temporary, path)
            except OSError:
                return None
            finally:
                temporary.unlink(missing_ok=True)
        return path

    async def run(self) -> None:
        """Sync now and then hourly, so a font that failed once or was lost comes back; lives until cancelled."""
        while True:
            try:
                await self.sync()
            except Exception:
                logger.exception("Font sync failed")
            await asyncio.sleep(3600)

    def guide(self) -> str:
        """Prompt section giving the Agent the font library as an enum to choose from."""
        fonts = self.available()
        lines = [f'- "{font.family}" ({font.label}; {font.style}; weight {font.weight_text})' for font in fonts]
        enum = json.dumps([font.family for font in fonts], ensure_ascii=False)
        return (
            "Font library (the only fonts that exist; all are installed for rendering and previews):\n" + "\n".join(lines) + "\n"
            f'Each tier\'s font_family in a text block is the exact family name of one of these fonts (the host exposes it as fontFamily '
            f'{{"type":"string","enum":{enum}}}, or line1FontFamily and line2FontFamily for two tiers; these families only, never others) so users and '
            "templates can switch fonts, and passes it to the tier's text Preset as its fontFamily prop; write it into CSS as fontFamily: "
            "`\"${props.fontFamily}\", \"" + DEFAULT_FAMILY + "\", sans-serif` (quoted, with "
            "the default as fallback). Look at the user's reference image or description (serif or Song, brush, rounded, "
            "handwritten, condensed, Latin-only) and pick the listed font whose style is closest; when none fits, choose "
            f'"{DEFAULT_FAMILY}". A single-weight font has only weight 400 and 700 is synthesized bold; a Latin-only font draws '
            "Chinese characters in the fallback."
        )
