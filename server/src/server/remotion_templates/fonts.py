"""Fonts for Remotion effects: a MySQL registry, ZOS objects and a verified local cache.

Three kinds of font share one registry (`font_store.py`): the system default (installed with the OS, so it has no
file here), the preinstalled catalog (`font_catalog.json`; the files ship inside this package as `bundled_fonts/*.xz`,
each pinned by size and SHA-256, and are copied into the object store once, so no network is needed) and fonts users
upload. A registry row is one font file; the file is an object under `imv/remotion/fonts/<id>/` (ZOS when configured,
the data folder otherwise, see `object_store.py`). A font card is a family with up to four styles (slots): regular,
bold, italic and bold italic, one file each, all rendered under the card's family so CSS `font-weight: 700` and
`font-style: italic` pick the real file. The first file of a card is keyed by the family itself and every later one
by `family<TAB>slot` (`face_key`), so the unique family column still reserves the card's name. An upload is a new
file (by SHA-256) that joins its family's uploaded card when that slot is free; another weight (Thin, Black) is a card
of its own named after it, and another version of a filled slot gets a distinct family.

The render sandbox has no network, so the backend keeps every font in a local cache (`<data dir>/fonts/files`, a file
is listed only when its SHA-256 matches the registry) next to a generated `fonts.conf`. The sandbox mounts that folder
read-only and points fontconfig at it, so Chrome in every worker (validation, preview build, overlay render, video
composition) resolves a family by its CSS name; the config lists each file under its registry family only. The
browser preview cannot use fontconfig: it registers lazy `FontFace`s served by `GET /api/templates/fonts/{id}`. Uploaded font bytes are never parsed by the backend beyond a
bounds-checked read of the font's names; only the sandboxed Chrome and the user's own browser decode them.
"""

import asyncio
import hashlib
import itertools
import json
import logging
import lzma
import os
import re
import struct
import subprocess
import threading
from collections.abc import Collection, Iterable, Iterator
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Literal, NamedTuple
from uuid import uuid4
from xml.sax.saxutils import escape

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
# Windows language IDs of Chinese names, in the order an upload's display name prefers them: zh-CN, zh-SG, zh-TW,
# zh-HK, zh-MO.
CHINESE_LANGUAGES = (0x0804, 0x1004, 0x0404, 0x0C04, 0x1404)
# The version number in a font's version string (name ID 5), such as 2.000 in "Version 2.000;hotconv 1.0".
VERSION = re.compile(r"\d{1,6}(?:\.\d{1,6}){0,3}")
# Style names (name ID 17 or 2) of a family's default style; any other weight is a card of its own named after it.
REGULAR_STYLES = frozenset({"regular", "normal", "roman", "standard"})
# Style words of a slanted file; the rest of its style names the weight.
ITALIC_STYLES = frozenset({"italic", "oblique"})
# The styles one font card holds, in display order, each a separate file rendered under the card's family.
Slot = Literal["regular", "bold", "italic", "bold_italic"]
SLOTS: tuple[Slot, ...] = ("regular", "bold", "italic", "bold_italic")
# Separates the family from the slot in the registry key of a card's later files (`face_key`); family names are
# printable, so no family contains it.
FACE_SEPARATOR = "\t"
# Preview strips are drawn black on a transparent background and tinted by the page through a CSS mask.
PREVIEW_MAX = (640, 96)


class FontRejected(ValueError):
    """An uploaded font or preview was refused; the message is safe to show to the user."""


class FontExists(FontRejected):
    """The uploaded file is already in the library (same SHA-256) as `font`; the upload route answers with its card."""

    def __init__(self, message: str, font: "Font") -> None:
        """Keep the font whose file has the same bytes."""
        super().__init__(message)
        self.font = font


class Font(BaseModel):
    """A registered font file, one style (`slot`) of the card its family names: what the registry stores per file."""

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
    # Files registered before cards had styles are their card's regular file until `FontStore._relabel` reads them.
    slot: Slot = "regular"

    @property
    def weight_text(self) -> str:
        """CSS `font-weight` descriptor: `400` or a range such as `200 900`."""
        return " ".join(str(weight) for weight in self.weights)

    @property
    def italic(self) -> bool:
        """Whether the file is its card's italic or bold italic style (CSS `font-style: italic`)."""
        return self.slot in ("italic", "bold_italic")

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


def registry_font(record: dict) -> Font:
    """The `Font` of a registry record (`font_store.listing`); a card's later file is keyed `family<TAB>slot`."""
    return Font(**{key: value for key, value in record.items() if key != "device_id"} | {"family": record["family"].split(FACE_SEPARATOR)[0]})


def face_key(family: str, slot: Slot) -> str:
    """Registry key of a file that joins an existing card: unique per card and style, never a family name."""
    return f"{family}{FACE_SEPARATOR}{slot}"


def cards(fonts: Iterable[Font]) -> dict[str, list[Font]]:
    """Font files grouped into cards by family, in order of each card's first file, its files in style order."""
    grouped: dict[str, list[Font]] = {}
    for font in fonts:
        grouped.setdefault(font.family, []).append(font)
    return {family: sorted(files, key=lambda font: SLOTS.index(font.slot)) for family, files in grouped.items()}


def refuse_copy(digest: str, fonts: Iterable[Font]) -> None:
    """Raise `FontExists` naming the registered font whose file has this SHA-256, if there is one."""
    if (copy := next((font for font in fonts if font.sha256 == digest), None)) is not None:
        raise FontExists(f"这个字体文件已经在字体库里了：{copy.label}（{copy.family}）", copy)


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


class FontNames(NamedTuple):
    """The names an uploaded font gives itself in its `name` table."""

    # The CSS name the registry stores: the English (or default-language) family.
    family: str
    # The name people read on the card and the strip: the Chinese name when the font has one.
    label: str
    # The English legacy family (name ID 1) when it differs from `family`; it usually names the weight ("… 85 Bold").
    legacy: str = ""
    # The version number of name ID 5 ("2.000" of "Version 2.000;hotconv"), or "".
    version: str = ""
    # The English style (name ID 17, else 2), such as "Bold Italic", "Thin" or "85 Bold", or "".
    style: str = ""
    # Whether the file is a variable font (has an `fvar` table), whose style names only its default instance.
    variable: bool = False


class Face(NamedTuple):
    """Where an uploaded file belongs before name collisions: the style it fills and its card's names."""

    slot: Slot
    # The card's family: the font's family, followed by any weight other than regular and bold ("Montserrat Thin").
    family: str
    # The card's display name: the font's display name followed by that weight.
    label: str


def _name_records(data: bytes) -> tuple[list[tuple[int, int, int, str]], bool]:
    """Decodable family, style and version records (name IDs 1, 16, 2, 17 and 5) of a TrueType/OpenType font as
    (platform, language, name ID, text) in table order, and whether it is a variable font (has an `fvar` table); raises
    `FontRejected` for anything else.

    Every offset is bounds-checked. Unicode (platform 0) and Windows Unicode (platform 3, encodings 0, 1 and 10) records
    are UTF-16; Mac records are read only in the Roman encoding, since decoding a Chinese or Japanese Mac record as
    Roman garbles it. Empty, unprintable or over-long texts are left out. Collections and web fonts (`ttcf`, WOFF) are
    not accepted.
    """
    try:
        if data[:4] not in SFNT_MAGIC:
            raise FontRejected("只支持 TrueType / OpenType 字体文件（.ttf、.otf）")
        tables = struct.unpack_from(">H", data, 4)[0]
        directory = {data[12 + 16 * index:16 + 16 * index]: 12 + 16 * index for index in range(tables)}
        if b"name" not in directory:
            raise FontRejected("字体文件缺少名称表")
        offset, length = struct.unpack_from(">II", data, directory[b"name"] + 8)
        table = data[offset:offset + length]
        count, strings = struct.unpack_from(">HH", table, 2)
        records = []
        for index in range(count):
            platform, encoding, language, name_id, size, start = struct.unpack_from(">HHHHHH", table, 6 + 12 * index)
            unicode = platform == 0 or (platform == 3 and encoding in (0, 1, 10))
            if name_id not in (1, 2, 5, 16, 17) or not (unicode or (platform == 1 and encoding == 0)):
                continue
            raw = table[strings + start:strings + start + size]
            if len(raw) != size:
                continue
            text = raw.decode("utf-16-be" if unicode else "mac_roman", errors="ignore").strip()
            if text and len(text) <= FONT_FAMILY_MAX_LENGTH and text.isprintable():
                records.append((platform, language, name_id, text))
    except (struct.error, IndexError) as exc:
        raise FontRejected("字体文件已损坏或不完整") from exc
    return records, b"fvar" in directory


def _default_language(platform: int, language: int) -> bool:
    """Whether a record is in the font's default language: Unicode, Windows US English or Mac English."""
    return platform == 0 or (platform, language) in ((3, 0x0409), (1, 0))


def read_names(data: bytes) -> FontNames:
    """The family and display names of a TrueType/OpenType font, read from its `name` table without a font library.

    The typographic family (name ID 16) wins over the legacy one (ID 1) within a language. The family prefers the
    default language (`_default_language`), so it is the font's usual Latin name. The label prefers Chinese (Windows
    zh-CN, zh-SG, zh-TW, zh-HK, zh-MO, in that order), then Unicode or US English, then any other Windows language, and
    is the family when the font has no Unicode name. `client/src/features/fonts/names.ts` applies the same label rule
    so the uploader's browser draws the preview strip with this text. The style, the legacy family and the version,
    read in the default language first, place the file in a card (`face_of`, `place`). A malformed file only raises
    `FontRejected`.
    """
    found, variable = _name_records(data)
    order = {record: index for index, record in enumerate(found)}
    records = [record for record in found if record[2] in (1, 16)]
    if not records:
        raise FontRejected("无法读取字体的家族名称")

    def default_first(record: tuple[int, int, int, str]) -> tuple[bool, bool, int]:
        """Rank of a record as the family, legacy family, style or version; lower is better (typographic IDs 16 and 17
        before the legacy IDs 1 and 2)."""
        return not _default_language(record[0], record[1]), record[2] not in (16, 17), order[record]

    family = min(records, key=default_first)[3]
    legacy = min((record for record in records if record[2] == 1), key=default_first, default=None)
    versions = (match.group() for record in sorted(found, key=default_first) if record[2] == 5 and (match := VERSION.search(record[3])))
    style = min((record for record in found if record[2] in (2, 17)), key=default_first, default=(0, 0, 0, ""))[3]

    def label_rank(record: tuple[int, int, int, str]) -> tuple[int, bool, int]:
        """Rank of a Unicode record as the display name; lower is better."""
        platform, language = record[0], record[1]
        rank = CHINESE_LANGUAGES.index(language) if platform == 3 and language in CHINESE_LANGUAGES else (
            len(CHINESE_LANGUAGES) if platform == 0 or language == 0x0409 else len(CHINESE_LANGUAGES) + 1
        )
        return rank, record[2] != 16, order[record]

    unicode = [record for record in records if record[0] in (0, 3)]
    return FontNames(
        family=family, label=min(unicode, key=label_rank)[3] if unicode else family,
        legacy=legacy[3] if legacy and legacy[3] != family else "", version=next(versions, ""), style=style,
        variable=variable,
    )


def _suffixed(name: str, suffix: str) -> str:
    """`name` cut to leave room for `suffix` within `FONT_FAMILY_MAX_LENGTH`, then the suffix."""
    return name[:max(0, FONT_FAMILY_MAX_LENGTH - len(suffix))].rstrip() + suffix


def slot_weights(slot: Slot) -> list[int]:
    """The CSS weight an uploaded file of this style is registered with: 700 for bold styles, otherwise 400."""
    return [700] if slot in ("bold", "bold_italic") else [400]


# Splits a style or family into words: "BoldItalic", "Bold, Italic" and "Bold-Italic" all name Bold Italic.
STYLE_SEPARATORS = re.compile(r"[\s,;_-]+")
CAMEL_CASE = re.compile(r"(?<=[a-z])(?=[A-Z])")


def style_words(text: str) -> list[str]:
    """The words of a style or family, splitting punctuation and camel case ("BoldItalic" -> Bold, Italic)."""
    return [word for part in STYLE_SEPARATORS.split(text) for word in CAMEL_CASE.split(part) if word]


def face_of(names: FontNames) -> Face:
    """The style and card of a file. Italic words (Italic, Oblique) in its style make it its card's italic style; the
    other words name the weight. No weight or a regular one (Regular, Normal) is the regular style and Bold the bold
    style of the family's own card; any other weight (Thin, Black, "85 Bold") makes a card of its own named after it,
    unless the family already names the weight (`Foo Black Italic` -> its italic slot, not `Foo Black Italic Black`).
    A variable font's weight is ignored: it only names the default instance, and the file holds every weight."""
    words = style_words(names.style)
    italic = any(word.casefold() in ITALIC_STYLES for word in words)
    weight = " ".join(word for word in words if word.casefold() not in ITALIC_STYLES)
    family_words = {word.casefold() for word in style_words(names.family)}
    weight_words = weight.casefold().split()
    # A family that already carries the weight keeps its own name even when the style adds Italic/Oblique after it.
    family_names_weight = bool(weight_words) and all(word in family_words for word in weight_words)
    if (names.variable or not weight or weight.casefold() in REGULAR_STYLES or family_names_weight):
        return Face("italic" if italic else "regular", names.family, names.label)
    if weight.casefold() == "bold":
        return Face("bold_italic" if italic else "bold", names.family, names.label)
    return Face("italic" if italic else "regular", _suffixed(names.family, f" {weight}"), _suffixed(names.label, f" {weight}"))


def card_names(names: FontNames, face: Face) -> Iterator[str]:
    """The families an upload may take, in order: its card's own, its legacy family (another weight named only there),
    that card's name with the version, then numbered from 2; each printable and at most `FONT_FAMILY_MAX_LENGTH`
    characters, so a long name is cut before a suffix. Never runs out."""
    candidates = [face.family, names.legacy, _suffixed(face.family, f" v{names.version}") if names.version else ""]
    numbered = (_suffixed(face.family, f" ({number})") for number in itertools.count(2))
    return (name for name in itertools.chain(candidates, numbered) if name and len(name) <= FONT_FAMILY_MAX_LENGTH)


def place(names: FontNames, face: Face, fonts: Iterable[Font], taken: Collection[str]) -> tuple[str, bool]:
    """The family an upload registers under and whether it joins the card already there: the first of `card_names`
    that is free (no font has it, ignoring case, and it is not in `taken`, the case-folded installed and reserved
    names) or that names an uploaded card whose `face.slot` is still empty. So the styles of a family share one card in
    any upload order, another version of a filled style gets a card of its own, and bundled and system cards are never
    joined."""
    grouped: dict[str, list[Font]] = {}
    for font in fonts:
        grouped.setdefault(font.family.casefold(), []).append(font)

    def fitting() -> Iterator[tuple[str, bool]]:
        """Each usable name with whether it joins an existing card."""
        for name in card_names(names, face):
            files = grouped.get(name.casefold())
            if files is None and name.casefold() not in taken:
                yield name, False
            elif files and all(font.source == "upload" for font in files) and face.slot not in {font.slot for font in files}:
                yield files[0].family, True

    return next(fitting())


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
        # Uploads whose display name `_relabel` has checked in this process.
        self.labelled: set[str] = set()
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
            self.known = {record["id"]: registry_font(record) for record in records}
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
        """Atomically write the fontconfig file the sandbox loads: the system config, the font folder and one scan rule
        per font file that lists the file under its card's family only.

        Without the rule fontconfig would list a file under every name inside it, which another weight or version of
        the same font, or another file's legacy or localized name, may share (a bundled variable font also answers to
        its default instance's legacy name, such as "Noto Sans SC Thin"); the rule makes the card's family the only
        name the sandbox resolves to this file, and fontconfig picks among a card's files by their own weight and
        slant. Each sandbox scans the folder afresh (its fontconfig cache lives in its own /tmp), so a new rule applies
        to the next worker. An uploaded family comes from the file and is XML-escaped.
        """
        rules = "".join(
            '  <match target="scan">\n'
            f'    <test name="file"><string>{SANDBOX_DIR}/files/{escape(font.cache_name)}</string></test>\n'
            f'    <edit name="family" mode="assign_replace" binding="strong"><string>{escape(font.family)}</string></edit>\n'
            '    <edit name="familylang" mode="assign_replace" binding="strong"><string>en</string></edit>\n'
            "  </match>\n"
            for font in self.known.values() if font.has_file
        )
        text = (
            '<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n<fontconfig>\n'
            '  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>\n'
            f"  <dir>{SANDBOX_DIR}/files</dir>\n{rules}</fontconfig>\n"
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

    def registered(self) -> list[Font]:
        """The registry as it is now, not this host's last refresh; blocking.

        The shared library can be written by another request that has registered a file but not yet updated this
        host's cache. A duplicate upload must still find its card, so read the registry again instead of `known`.
        """
        return [registry_font(record) for record in font_store.listing()]

    def families(self) -> list[str]:
        """Family names of the whole library, one per card: the enum the Agent chooses from and `fontFamily` values are
        checked against. It reads a snapshot taken under the lock, so a concurrent registration cannot change the
        dictionary while it is traversed."""
        with self._lock:
            known = list(self.known.values())
        return list(cards(known))

    def path(self, font_id: str) -> Path | None:
        """Cached file of an available font, or None (also for the system default, which has no file here)."""
        font = self.known.get(font_id)
        return self.files / font.cache_name if font and font.has_file and font.id in self.ready else None

    def named(self, source: str) -> list[Font]:
        """Registered font files whose family `source` (a render's code and parameter values) mentions: every style of
        each card it names."""
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
        return list(dict.fromkeys(font.family for font in named if font.id not in self.ready))

    async def sync(self) -> None:
        """Install the bundled catalog fonts the registry lacks (unless switched off), cache every registered font,
        then give uploads their own display name and style (`_relabel`).

        One failure (missing or corrupt bundled file, object store, database) is logged and leaves that font for the
        next sync; the others proceed.
        """
        if self.objects is None:
            return
        await asyncio.to_thread(self.refresh)
        families = {font.family.casefold() for font in self.known.values()}
        for spec in load_catalog() if self.install_bundled else ():
            if spec.id in self.known:
                continue
            if spec.family.casefold() in families:
                # An upload took the name before uploads checked the catalog; installing it would fail every hour.
                logger.warning("Font %s is not installed: another font is registered as %r", spec.id, spec.family)
                continue
            try:
                await asyncio.to_thread(self._seed, spec)
            except Exception as exc:
                logger.warning("Font %s was not installed: %s", spec.id, exc)
        for font in list(self.known.values()):
            if font.has_file and font.id not in self.ready:
                await asyncio.to_thread(self._cache, font)
        await asyncio.to_thread(self._relabel_ready_uploads)

    def _relabel_ready_uploads(self) -> None:
        """Correct the style of cached uploads registered before cards had styles; blocking.

        Placement (`upload`) calls this before it joins a card, so an older bold or italic file is not mistaken for a
        regular one while the background sync has not reached it yet. Each upload is read once per process (`labelled`).
        """
        for font in list(self.known.values()):
            if font.source != "upload" or font.id not in self.ready or font.id in self.labelled:
                continue
            try:
                self._relabel(font)
            except Exception as exc:
                logger.warning("Font %s was not relabelled: %s", font.id, exc)

    def _relabel(self, font: Font) -> None:
        """Give a cached upload its own display name and style (`face_of`) when it is registered with others: the file
        name that clients sent before the name came from the font, or the regular style every file had before cards
        had styles; blocking.

        A strip drawn with another text than the font's display name (the file name) is dropped (registry, object and
        local copy) and the name is shown as text. The family, which templates store, never changes, so an older upload
        stays a card of its own. A file whose names are already right is left alone, and each upload is read once per
        process (`labelled`), so this writes once per font.
        """
        try:
            names = read_names((self.files / font.cache_name).read_bytes())
        except OSError:
            return
        except FontRejected:
            names = None
        changes: dict = {}
        if names is not None:
            face = face_of(names)
            if face.label != font.label:
                changes["label"] = face.label
                # The strip shows the text the uploader's browser drew: the font's display name, or the file name.
                if font.label != names.label:
                    changes["preview"] = False
            if face.slot != font.slot:
                with self._lock:
                    occupied = any(
                        other.id != font.id and other.family == font.family and other.slot == face.slot
                        for other in self.known.values()
                    )
                if occupied:
                    logger.warning("Font %s keeps style %s: another file of %r already has it", font.id, font.slot, font.family)
                else:
                    changes |= {"slot": face.slot, "weights": slot_weights(face.slot)}
        if changes:
            relabelled = font.model_copy(update=changes)
            font_store.update_data(font.id, relabelled.model_dump(exclude={"id", "family"}))
            with self._lock:
                self.known[font.id] = relabelled
            logger.info("Font %s is shown as %r (%s) instead of %r (%s)", font.id, relabelled.label, relabelled.slot, font.label, font.slot)
            if font.preview and not relabelled.preview and self.objects is not None:
                (self.root / "previews" / f"{font.id}.png").unlink(missing_ok=True)
                try:
                    self.objects.delete_prefix(font.preview_key)
                except OSError as exc:
                    logger.warning("The old preview strip of font %s was not deleted: %s", font.id, exc)
        self.labelled.add(font.id)

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

    def _register(self, font: Font, staged: Path, device: str | None, key: str | None = None) -> bool:
        """Store a verified font file (and the preview strip when `font.preview`) as objects, add the registry row under
        `key` (the family, or `face_key` for a file joining a card) and cache the file; blocking.

        A catalog font's strip is drawn here from the pinned file; an uploaded font's strip was stored by `upload`
        already. Returns False when the registry already has this ID or key (another sync or upload won). A strip
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
        if not font_store.insert(font.id, key or font.family, font.model_dump(exclude={"id", "family"}), device):
            return False
        self.files.mkdir(parents=True, exist_ok=True)
        os.replace(staged, self.files / font.cache_name)
        self._accept(font)
        return True

    def upload(self, content: bytes, preview: bytes | None, label: str, device: str) -> Font:
        """Validate a user's font file, store it and register it; blocking. Raises `FontRejected` with a user message.

        The file must be a plain TrueType/OpenType font within the size limit that is not in the library yet: the same
        bytes (by SHA-256) are refused, whoever added them, including a bundled font that sync has not installed yet.
        Its style (`face_of`) decides its place: the regular, bold, italic or bold italic file of a family joins that
        family's uploaded card when that style is still empty, so a card collects its styles in any order; another
        weight is a card of its own named after it; another version of a filled style, or a name that a bundled,
        installed or reserved font carries, gets a distinct family (`place`). The generated fontconfig lists the file
        under its card's family (`_write_config`). The backend only reads the font's names (`read_names`); decoding is
        left to the sandboxed browser. The display name is the font's own (its Chinese name first), never the file name.
        The preview strip is drawn by the uploader's own browser (the font is decoded nowhere else) and kept as alpha
        only; `label` is the text that browser drew, and a strip whose text is not the display name is dropped.
        """
        if not content or len(content) > self.max_upload:
            raise FontRejected(f"字体文件为空或超过 {self.max_upload / (1024 * 1024):g} MiB 上限")
        digest = hashlib.sha256(content).hexdigest()
        # Bundled fonts that sync has not installed yet keep their file and their family.
        pending = [spec.font() for spec in load_catalog() if spec.id not in self.known] if self.install_bundled else []
        # An older upload may still carry the default regular slot until sync reads its names. Correct it first so a
        # new upload of the same style does not join the same slot twice.
        self._relabel_ready_uploads()
        registered = [*self.known.values(), *pending]
        refuse_copy(digest, registered)
        names = read_names(content)
        face = face_of(names)
        strip = normalize_preview(preview) if preview and (label or "").strip() == names.label else None
        suffix = ".otf" if content[:4] == b"OTTO" else ".ttf"
        font = Font(
            id=f"u-{digest[:16]}", family=face.family, label=face.label, style="uploaded by a user", weights=slot_weights(face.slot),
            filename=f"{digest[:16]}{suffix}", sha256=digest, size=len(content), source="upload", preview=strip is not None, slot=face.slot,
        )
        self.files.mkdir(parents=True, exist_ok=True)
        staged = self.files / f"{font.cache_name}.{uuid4().hex}.part"
        strip_file = staged.with_suffix(".png")
        try:
            staged.write_bytes(content)
            if strip is not None:
                strip_file.write_bytes(strip)
                assert self.objects is not None
                self.objects.put(font.preview_key, strip_file, "image/png")
            # A second try covers another upload or host that registered this file or took the name or style since
            # this host's last sync.
            for _attempt in range(2):
                family, joins = place(names, face, registered, RESERVED_FAMILIES | system_families())
                font = font.model_copy(update={"family": family})
                if self._register(font, staged, device, face_key(family, face.slot) if joins else family):
                    return font
                registered = [*(registry_font(record) for record in font_store.listing()), *pending]
                refuse_copy(digest, registered)
            raise FontRejected("字体名称刚被其他上传占用，请重试")
        finally:
            staged.unlink(missing_ok=True)
            strip_file.unlink(missing_ok=True)

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

    def guide(self, *, free: bool = False) -> str:
        """Prompt section giving the Agent the font library as an enum to choose from, one entry per card with the
        styles it has; ``free`` words it for a free-form effect's effect.json instead of a text block's tiers."""
        library = cards(self.available())
        lines = [
            f'- "{family}" ({files[0].label}; {files[0].style}; styles '
            + ", ".join(f"{font.slot.replace('_', ' ')} {font.weight_text}" for font in files) + ")"
            for family, files in library.items()
        ]
        enum = json.dumps(list(library), ensure_ascii=False)
        usage = (
            "Each line's family in effect.json text.fonts is the exact family name of one of these fonts (these families only, never others); "
            "the host exposes it as fontFamily (one line) or line1FontFamily, line2FontFamily, ... so users and templates can switch fonts, "
            "and TextRoot applies it to the line, so draw the text in the font it inherits. "
        ) if free else (
            f'Each tier\'s font_family in a text block is the exact family name of one of these fonts (the host exposes it as fontFamily '
            f'{{"type":"string","enum":{enum}}}, or line1FontFamily and line2FontFamily for two tiers; these families only, never others) so users and '
            "templates can switch fonts, and passes it to the tier's text Preset as its fontFamily prop; write it into CSS as fontFamily: "
            "`\"${props.fontFamily}\", \"" + DEFAULT_FAMILY + "\", sans-serif` (quoted, with the default as fallback). "
        )
        return (
            "Font library (the only fonts that exist; all are installed for rendering and previews):\n" + "\n".join(lines) + "\n"
            + usage + "Look at the user's reference image or description (serif or Song, brush, rounded, "
            "handwritten, condensed, Latin-only) and pick the listed font whose style is closest; when none fits, choose "
            f'"{DEFAULT_FAMILY}". A font uses its bold style for font_weight 700 and its italic style for italic text when it '
            "has them; without one the browser synthesizes bold or slants the text. A Latin-only font draws Chinese "
            "characters in the fallback."
        )
