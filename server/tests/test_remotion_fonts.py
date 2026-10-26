"""Remotion 字体库：MySQL 注册表 + 对象存储 + 本地校验缓存、用户上传、沙箱挂载、Agent 清单、API 与渲染守卫。

范围：`font_catalog.json` 的结构与随包字体文件、预装字体从字体包复制进对象存储与注册表、从对象存储取回缓存（换机器/丢缓存仍可用）、
被篡改的缓存被剔除、字体名称的读取（显示名优先中文名）与旧上传显示名的修正、上传字体的校验（格式、按文件内容去重（含尚未装进库的预装字体）、大小、预览条）、字体卡片（同一家族的常规/粗体/斜体/粗斜体归入一张卡片、其他字重与已有字形的其他版本另起卡片、旧上传补读字形、按卡片列出的接口）、fontconfig 扫描规则（含真实 fc-match）、`fontFamily` 的分类规则与覆盖校验、
沙箱命令是否挂载字体目录、Agent 字体清单、字体 API 与预览页、渲染前的字体守卫。数据库用 `template_db` 的临时 SQLite，
对象存储用本地目录，字体包用小文件代替（另有一例核对随包的真实字体），不访问网络；真实浏览器里字体是否生效由 `IMV_TEST_RENDERER=1` 的用例验证。
执行：在 `server/` 下运行 `uv run --locked pytest tests/test_remotion_fonts.py -v`。
"""

import asyncio
import hashlib
import json
import logging
import os
import shutil
import struct
import subprocess
from io import BytesIO
from pathlib import Path
from collections.abc import Sequence
from types import SimpleNamespace
from uuid import uuid4
from xml.etree import ElementTree

import lzma
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from generated.imv.sprite.v1 import sprite_pb2 as pb
from server.remotion_templates import font_store
from server.remotion_templates import fonts as fonts_module
from server.remotion_templates import routes
from server.remotion_templates.api import app as remotion_templates_app
from server.remotion_templates.categories import REQUIRED, category_problems
from server.remotion_templates.fonts import CONFIG_NAME, Font, FontExists, FontNames, FontRejected, FontSpec, FontStore, load_catalog, cards, is_font_parameter, open_font_schema, read_names, require_family
from server.remotion_templates.object_store import LocalObjects, ObjectStoreError
from server.remotion_templates.overlay_render import Overlay, OverlayError, require_fonts
from server.remotion_templates.placement import resolve
from server.remotion_templates.renderer import Renderer
from server.remotion_templates.settings import Settings
from server.remotion_templates.sprites import operator_parameters

from server.template.schema import RemotionTrackEditor

from .conftest import DEVICE_HEADERS, published_sprite

PAYLOADS = {"alpha": b"alpha-font-bytes", "beta": b"beta-font-bytes-longer"}


def minimal_font(
    family: str, characters: str = "测试字体效果 Abc", names: Sequence[tuple[int, int, int, int, str | bytes]] = (), advance: int = 1000,
    style: str = "Regular", variable: bool = False, weight: int = 400, italic: bool = False,
) -> bytes:
    """A tiny valid TrueType font: every listed character maps to one empty glyph `advance` units wide (1000, one em).

    Its text width (characters × font size) differs from the default font's, and it exists on no system, so a
    width that changes proves the browser loaded this file. Built by hand to avoid a font-tooling dependency. The name
    table holds Windows US English records for `family` and `style` (name ID 2) followed by the extra `names` records
    (platform, encoding, language, name ID, text); a text is UTF-16 on the Unicode and Windows platforms and Mac Roman
    otherwise, and bytes are written as given. `variable` adds an empty `fvar` table, which only the name reader sees;
    `weight` and `italic` set the OS/2 weight class and the bold and italic flags fontconfig matches a style by.
    """
    def table(tag: bytes, data: bytes) -> tuple[bytes, bytes]:
        return tag, data + b"\0" * (-len(data) % 4)

    codes = sorted({ord(char) for char in characters})
    segments = [(code, code, (1 - code) & 0xFFFF) for code in codes] + [(0xFFFF, 0xFFFF, 1)]
    count = len(segments)
    selector = count.bit_length() - 1
    cmap_subtable = struct.pack(">HHHHHHH", 4, 16 + 8 * count, 0, count * 2, 2 << selector, selector, count * 2 - (2 << selector))
    cmap_subtable += b"".join(struct.pack(">H", end) for _, end, _ in segments) + b"\0\0"
    cmap_subtable += b"".join(struct.pack(">H", start) for start, _, _ in segments)
    cmap_subtable += b"".join(struct.pack(">H", delta) for _, _, delta in segments)
    cmap_subtable += b"\0\0" * count
    entries = [(3, 1, 0x409, name_id, value) for name_id, value in ((1, family), (2, style), (4, family), (6, family.replace(" ", "")))]
    # OS/2 fsSelection: italic (bit 0) and bold (bit 5), or regular (bit 6).
    selection = (italic * 0x01) | (0x20 if weight >= 700 else 0) or 0x40
    entries += names
    encoded = [value if isinstance(value, bytes) else value.encode("utf-16-be" if platform in (0, 3) else "mac_roman") for platform, _, _, _, value in entries]
    strings = b"".join(encoded)
    records, offset = b"", 0
    for (platform, encoding, language, name_id, _), value in zip(entries, encoded):
        records += struct.pack(">HHHHHH", platform, encoding, language, name_id, len(value), offset)
        offset += len(value)
    tables = dict([
        table(b"OS/2", struct.pack(">HhHHHhhhhhhhhhh10sIIII4sHHHhhhHH", 0, 1000, weight, 5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, bytes(10), 0, 0, 0, 0, b"IMV ", selection, 32, 0xFFFF, 800, -200, 0, 800, 200)),
        table(b"cmap", struct.pack(">HHHHI", 0, 1, 3, 1, 12) + cmap_subtable),
        table(b"glyf", bytes(4)),
        table(b"head", struct.pack(">IIIIHHqqhhhhHHhhh", 0x10000, 0x10000, 0, 0x5F0F3CF5, 0, 1000, 0, 0, 0, 0, 0, 0, (weight >= 700) | (italic << 1), 8, 2, 0, 0)),
        table(b"hhea", struct.pack(">IhhhHhhhhhhhhhhhH", 0x10000, 800, -200, 0, advance, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 2)),
        table(b"hmtx", struct.pack(">HhHh", advance, 0, advance, 0)),
        table(b"loca", bytes(6)),
        table(b"maxp", struct.pack(">IHHHHHHHHHHHHHH", 0x10000, 2, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0)),
        table(b"name", struct.pack(">HHH", 0, len(entries), 6 + 12 * len(entries)) + records + strings),
        table(b"post", struct.pack(">IIhhIIIII", 0x30000, 0, -100, 50, 0, 0, 0, 0, 0)),
        *([table(b"fvar", bytes(16))] if variable else []),
    ])
    # A hand-built font's head checksum field stays zero; FreeType does not verify checksums.
    directory = struct.pack(">IHHHH", 0x10000, len(tables), 128, 3, 16 * len(tables) - 128)
    position, body = 12 + 16 * len(tables), b""
    for tag, data in tables.items():
        checksum = sum(struct.unpack(f">{len(data) // 4}I", data)) & 0xFFFFFFFF
        directory += struct.pack(">4sIII", tag, checksum, position + len(body), len(data))
        body += data
    return directory + body


def strip_png(width: int = 200, height: int = 40) -> bytes:
    """A black-on-transparent preview strip like the one the uploader's browser draws."""
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    image.paste((0, 0, 0, 255), (10, 10, width - 10, height - 10))
    encoded = BytesIO()
    image.save(encoded, format="PNG")
    return encoded.getvalue()


def spec(font_id: str, family: str, **changes) -> FontSpec:
    """A pinned catalog fixture font whose bytes are `PAYLOADS[font_id]`."""
    data = PAYLOADS[font_id]
    values = {
        "id": font_id, "family": family, "label": family, "style": "fixture style", "weights": [400],
        "filename": f"{family.replace(' ', '')}.ttf", "url": f"https://fonts.example.test/{font_id}.ttf",
        "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "license": "OFL-1.1",
    }
    return FontSpec(**{**values, **changes})


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch):
    """每个用例开始时没有活动的字体存储，也不保留上一个用例的单例。"""
    monkeypatch.setattr(FontStore, "active", None)
    monkeypatch.setattr(FontStore, "_stores", {})


@pytest.fixture
def catalog(monkeypatch, tmp_path) -> tuple[FontSpec, FontSpec]:
    """两个小字体替换随包目录，并把它们的 .xz 写进临时字体包（内容不是真字体，只用于哈希与存储）。"""
    specs = (spec("alpha", "Alpha Sans"), spec("beta", "Beta Serif", weights=[200, 900]))
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for item in specs:
        (bundle / f"{item.filename}.xz").write_bytes(lzma.compress(PAYLOADS[item.id]))
    monkeypatch.setattr(fonts_module, "load_catalog", lambda: specs)
    monkeypatch.setattr(fonts_module, "BUNDLE", bundle)
    return specs


@pytest.fixture
def settings(tmp_path, catalog, template_db) -> Settings:
    """开启预装字体安装的设置，数据目录在临时目录，注册表用临时 SQLite。"""
    return Settings(_env_file=None, data_dir=tmp_path / "state", install_bundled_fonts=True)


@pytest.fixture
def objects(tmp_path) -> LocalObjects:
    """本地对象存储，代替 ZOS。"""
    return LocalObjects(tmp_path / "objects")


@pytest.fixture
def store(settings, objects) -> FontStore:
    """已绑定对象存储并读过（空）注册表的字体存储。"""
    fonts = FontStore.for_settings(settings)
    fonts.attach(objects)
    fonts.refresh()
    return fonts


def test_the_bundled_catalog_pins_every_font() -> None:
    """随包目录：条目结构合法，ID 与字体名唯一，下载地址为 https，哈希为 64 位十六进制，并写明许可证。"""
    specs = load_catalog()
    assert len(specs) >= 10
    assert {item.license for item in specs} == {"OFL-1.1"}
    assert all(item.url.startswith("https://") and len(item.sha256) == 64 for item in specs)


def test_every_catalog_font_ships_in_the_package() -> None:
    """随包字体与目录一致：每个目录条目都有对应的 .xz，解压后的大小与 SHA-256 正是目录里固定的值，包里没有多余的字体文件，并附有每个字体的 OFL 许可证。"""
    specs = load_catalog()
    for item in specs:
        digest, size = hashlib.sha256(), 0
        with lzma.open(fonts_module.BUNDLE / f"{item.filename}.xz") as packed:
            while chunk := packed.read(1 << 20):
                digest.update(chunk)
                size += len(chunk)
        assert (size, digest.hexdigest()) == (item.size, item.sha256), item.id
    assert {path.name for path in fonts_module.BUNDLE.glob("*.xz")} == {f"{item.filename}.xz" for item in specs}
    # OFL requires the license and copyright notice to travel with the font files.
    licenses = (fonts_module.BUNDLE / "LICENSES.txt").read_text(encoding="utf-8")
    assert all(f"{item.family}  ({item.filename})" in licenses for item in specs)
    assert licenses.upper().count("SIL OPEN FONT LICENSE") >= len(specs)


def test_the_family_name_is_read_without_trusting_the_file() -> None:
    """家族名只读 name 表（带边界检查）：合法字体读出名称；不是 sfnt（如 WOFF）、被截断、没有名称表或名称超过 100 个字符的文件抛 FontRejected。"""
    data = minimal_font("Zed Display")
    assert read_names(data) == FontNames(family="Zed Display", label="Zed Display", style="Regular")
    for broken in (b"wOFF" + data[4:], data[:20], data[:40], b"", b"\x00\x01\x00\x00" + b"\xff" * 40, minimal_font("x" * 101)):
        with pytest.raises(FontRejected):
            read_names(broken)


def test_the_display_name_is_the_fonts_own_chinese_name() -> None:
    """显示名取字体自己的名称：中文名优先（简体先于繁体），同一语言里字体家族（ID 16）先于旧式家族（ID 1），英文先于其他语言；
    家族名（CSS 名）仍是英文名。Mac 平台的中文编码记录不按 Roman 解码（否则是乱码），只有 Mac Roman 名称时显示名就是家族名。
    与客户端 `client/tests/font-names.test.ts` 是同一组情形，两边规则一致。"""
    assert read_names(minimal_font("Smiley Sans", names=[(3, 1, 0x0804, 1, "得意黑")])) == FontNames(family="Smiley Sans", label="得意黑", style="Regular")
    assert read_names(minimal_font("Font", names=[(3, 1, 0x0404, 1, "繁體名"), (3, 1, 0x0804, 1, "简体名")])).label == "简体名"
    assert read_names(minimal_font("Font", names=[(3, 1, 0x0C04, 1, "香港名"), (3, 1, 0x0404, 1, "台灣名")])).label == "台灣名"
    assert read_names(minimal_font("Alibaba PuHuiTi 2.0 85 Bold", names=[(3, 1, 0x0409, 16, "Alibaba PuHuiTi 2.0")])) == FontNames(
        family="Alibaba PuHuiTi 2.0", label="Alibaba PuHuiTi 2.0", legacy="Alibaba PuHuiTi 2.0 85 Bold", style="Regular",
    )
    assert read_names(minimal_font("Latin Name", names=[(3, 1, 0x0411, 1, "日本語名")])).label == "Latin Name"
    assert read_names(minimal_font("Clean", names=[(3, 1, 0x0804, 1, "  "), (3, 1, 0x0804, 16, "bad\u0001name")])).label == "Clean"
    gb2312 = minimal_font("Mac Font", names=[(1, 25, 33, 1, "宋体".encode("gb2312"))])
    assert read_names(gb2312) == FontNames(family="Mac Font", label="Mac Font", style="Regular")
    # The Windows records carry an unprintable name, so only the Mac Roman record is usable.
    assert read_names(minimal_font("\u0001", names=[(1, 0, 0, 1, "Mac Only")])) == FontNames(family="Mac Only", label="Mac Only", style="Regular")


def test_sync_installs_the_bundled_fonts_into_the_object_store_and_the_registry(store, objects) -> None:
    """默认字体登记为 system（没有文件）排在最前；预装字体从随包字体包安装一次：对象存储里有字体文件，注册表有记录（来源 builtin），
    本机缓存校验通过并生成沙箱用的配置；字体包不在了再次同步也不受影响（已安装的不再读取字体包）。"""
    asyncio.run(store.sync())
    assert [font.id for font in store.available()] == ["noto-sans-cjk", "alpha", "beta"]
    assert {record["id"] for record in font_store.listing()} == {"noto-sans-cjk", "alpha", "beta"}
    assert [font.source for font in store.listing()] == ["system", "builtin", "builtin"]
    assert (objects.root / "imv/remotion/fonts/alpha/font.ttf").read_bytes() == PAYLOADS["alpha"]
    assert (store.files / "alpha.ttf").read_bytes() == PAYLOADS["alpha"]
    config = (store.root / CONFIG_NAME).read_text(encoding="utf-8")
    assert "<dir>/imv-fonts/files</dir>" in config and "/etc/fonts/fonts.conf" in config
    assert not list(store.files.glob("*.part"))
    shutil.rmtree(fonts_module.BUNDLE)
    asyncio.run(store.sync())
    assert [font.id for font in store.available()] == ["noto-sans-cjk", "alpha", "beta"]


def test_another_host_caches_from_the_object_store_without_the_bundle(store, objects, tmp_path) -> None:
    """对象存储与注册表是唯一来源：换一台机器（空缓存、没有字体包）同步后字体照样可用。"""
    asyncio.run(store.sync())
    shutil.rmtree(fonts_module.BUNDLE)
    fresh = FontStore(Settings(_env_file=None, data_dir=tmp_path / "other-host", install_bundled_fonts=False))
    fresh.attach(objects)
    asyncio.run(fresh.sync())
    assert [font.id for font in fresh.available()] == ["noto-sans-cjk", "alpha", "beta"]


def test_a_tampered_cache_file_is_dropped_and_fetched_again(store) -> None:
    """本机缓存里被改动（哈希与注册表不符）的字体在刷新时删除、不可用，下次同步从对象存储取回正确文件。"""
    asyncio.run(store.sync())
    (store.files / "beta.ttf").write_bytes(b"x" * len(PAYLOADS["beta"]))
    store.refresh()
    assert [font.id for font in store.available()] == ["noto-sans-cjk", "alpha"] and not (store.files / "beta.ttf").exists()
    asyncio.run(store.sync())
    assert (store.files / "beta.ttf").read_bytes() == PAYLOADS["beta"]


def test_a_bundled_font_that_does_not_match_its_pin_is_not_installed(store, objects) -> None:
    """随包文件解压后与固定哈希不符（或缺失）时该字体不入库、不入对象存储；另一个字体照常安装，修好后下次同步补上。"""
    packed = fonts_module.BUNDLE / "AlphaSans.ttf.xz"
    good = packed.read_bytes()
    packed.write_bytes(lzma.compress(b"tampered-bytes!!!"))
    (fonts_module.BUNDLE / "BetaSerif.ttf.xz").unlink()
    asyncio.run(store.sync())
    assert [font.id for font in store.listing()] == ["noto-sans-cjk"] and not (objects.root / "imv/remotion/fonts/alpha").exists()
    packed.write_bytes(good)
    (fonts_module.BUNDLE / "BetaSerif.ttf.xz").write_bytes(lzma.compress(PAYLOADS["beta"]))
    asyncio.run(store.sync())
    assert {font.id for font in store.listing()} == {"noto-sans-cjk", "alpha", "beta"}


def test_installing_the_bundled_fonts_can_be_switched_off(tmp_path, catalog, template_db, objects) -> None:
    """关闭安装时不读取字体包，字体库里只有默认字体（它不需要安装）。"""
    store = FontStore(Settings(_env_file=None, data_dir=tmp_path / "state", install_bundled_fonts=False))
    store.attach(objects)
    asyncio.run(store.sync())
    assert [font.id for font in store.listing()] == ["noto-sans-cjk"]


def test_upload_registers_a_user_font_in_the_object_store(store, objects) -> None:
    """上传 .ttf：读出家族名与中文显示名并登记（来源 upload、上传设备），字体与预览条都进对象存储，本机立即可用；预览条只保留透明度。"""
    font = store.upload(minimal_font("Zed Display", names=[(3, 1, 0x0804, 1, "我的字体")]), strip_png(), "我的字体", "device-1")
    assert (font.family, font.label, font.source, font.preview) == ("Zed Display", "我的字体", "upload", True)
    assert next(record for record in font_store.listing() if record["id"] == font.id)["device_id"] == "device-1"
    assert (objects.root / font.object_key).exists() and (objects.root / font.preview_key).exists()
    assert store.path(font.id) is not None and [item.family for item in store.available()] == ["Noto Sans CJK SC", "Zed Display"]
    with Image.open(store.preview_path(font.id)) as preview:
        assert preview.mode == "RGBA" and preview.getpixel((20, 20))[:3] == (0, 0, 0)


def test_upload_refuses_unusable_fonts(store) -> None:
    """拒绝：不是 TrueType/OpenType、空文件、超过大小上限、预览条不是 PNG。"""
    store.upload(minimal_font("Zed Display"), None, "", "device-1")
    for content, message in [(b"wOFF" + minimal_font("Other")[4:], "TrueType"), (b"", "上限")]:
        with pytest.raises(FontRejected, match=message):
            store.upload(content, None, "", "device-1")
    store.max_upload = 10
    with pytest.raises(FontRejected, match="上限"):
        store.upload(minimal_font("Big Font"), None, "", "device-1")
    store.max_upload = 10_000_000
    with pytest.raises(FontRejected, match="预览图"):
        store.upload(minimal_font("Preview Bad"), b"not a png", "Preview Bad", "device-1")
    assert [font.family for font in store.listing()] == ["Noto Sans CJK SC", "Zed Display"]


def test_the_display_name_comes_from_the_font_not_the_file_name(store) -> None:
    """显示名取字体自己的名称，不取上传者给的文件名：浏览器若用文件名（如一串哈希）画了预览条，预览条被丢弃，界面改用文字名称。"""
    data = minimal_font("Smiley Sans", names=[(3, 1, 0x0804, 1, "得意黑")])
    font = store.upload(data, strip_png(), "0b7027d34f9546b7993e082b48c22401", "device-1")
    assert (font.family, font.label, font.preview) == ("Smiley Sans", "得意黑", False)
    assert store.preview_path(font.id) is None


def test_sync_relabels_an_upload_named_after_its_file(store, objects, monkeypatch) -> None:
    """旧客户端把文件名（一串哈希）当显示名上传的字体：同步时改用字体自己的名称，旧预览条（画的是文件名）作废、改显示文字名称，
    对象存储里的旧预览条一并删除，家族名不变；显示名已经正确的上传（包括带预览条的新上传）不改动。每个上传在本进程只读一次，
    再次同步既不读字体文件也不写注册表。"""
    named = store.upload(minimal_font("Smiley Sans", names=[(3, 1, 0x0804, 1, "得意黑")]), strip_png(), "得意黑", "device-1")
    fine = store.upload(minimal_font("Zed Display"), strip_png(), "Zed Display", "device-1")
    old = named.model_copy(update={"label": "0b7027d34f9546b7993e082b48c22401"})
    font_store.update_data(old.id, old.model_dump(exclude={"id", "family"}))
    store.refresh()
    # Simulate a row written by an older process: this process relabelled it when the second upload arrived.
    store.labelled.discard(named.id)
    asyncio.run(store.sync())
    relabelled = next(font for font in store.listing() if font.id == named.id)
    assert (relabelled.family, relabelled.label, relabelled.preview) == ("Smiley Sans", "得意黑", False)
    assert next(record for record in font_store.listing() if record["id"] == named.id)["label"] == "得意黑"
    assert store.preview_path(named.id) is None and store.preview_path(fine.id) is not None
    assert not (objects.root / named.preview_key).exists() and (objects.root / fine.preview_key).exists()
    writes: list[str] = []
    reads: list[int] = []
    monkeypatch.setattr(font_store, "update_data", lambda font_id, data: writes.append(font_id))
    monkeypatch.setattr(fonts_module, "read_names", lambda data: reads.append(len(data)) or read_names(data))
    store.refresh()
    asyncio.run(store.sync())
    assert writes == [] and reads == []


def test_the_same_file_is_not_uploaded_twice(store) -> None:
    """去重按文件内容（SHA-256）：同一个文件第二次上传被拒绝，并说明它就是库里的哪个字体；与预装字体相同的文件也一样。注册表里不多出记录。"""
    asyncio.run(store.sync())
    data = minimal_font("Zed Display", names=[(3, 1, 0x0804, 1, "我的字体")])
    first = store.upload(data, None, "", "device-1")
    with pytest.raises(FontExists, match="已经在字体库里了：我的字体（Zed Display）") as existing:
        store.upload(data, None, "", "device-2")
    assert existing.value.font.id == first.id
    with pytest.raises(FontRejected, match="已经在字体库里了：Alpha Sans（Alpha Sans）"):
        store.upload(PAYLOADS["alpha"], None, "", "device-2")
    assert len(font_store.listing()) == 4


def test_a_bundled_font_not_installed_yet_keeps_its_file_and_family(store, catalog) -> None:
    """预装字体还没装进字体库时（同步尚未完成）：与它相同的文件同样被拒绝，与它同名的上传另起家族名，之后同步照常装上预装字体。"""
    with pytest.raises(FontRejected, match="已经在字体库里了：Alpha Sans（Alpha Sans）"):
        store.upload(PAYLOADS["alpha"], None, "", "device-1")
    assert store.upload(minimal_font("Alpha Sans"), None, "", "device-1").family == "Alpha Sans (2)"
    asyncio.run(store.sync())
    assert {font.id: font.family for font in store.available() if font.source == "builtin"} == {"alpha": "Alpha Sans", "beta": "Beta Serif"}


def test_sync_skips_a_bundled_font_whose_family_an_older_upload_took(store, objects, catalog, caplog) -> None:
    """上传还不检查预装目录时，有上传占用了某个预装字体的家族名：同步跳过这个预装字体并记录警告，不再每小时解压、存进对象存储又登记失败；
    其他预装字体照常安装。"""
    font_store.insert("u-old", "Alpha Sans", {**store.listing()[0].model_dump(exclude={"id", "family"}), "source": "upload", "sha256": "1" * 64}, "device-9")
    store.refresh()
    with caplog.at_level(logging.WARNING, logger=fonts_module.__name__):
        asyncio.run(store.sync())
    assert {font.id for font in store.listing() if font.source == "builtin"} == {"beta"}
    assert not (objects.root / catalog[0].font().object_key).exists()
    assert "Font alpha is not installed: another font is registered as 'Alpha Sans'" in caplog.text


def upload_font(store: FontStore, name: str, characters: str, style: str = "Regular", *records, **flags) -> Font:
    """Upload a hand-built font with these names, style, glyphs and `minimal_font` flags, without a preview strip."""
    return store.upload(minimal_font(name, characters, names=records, style=style, **flags), None, "", "device-1")


def test_the_styles_of_a_family_share_one_card_in_any_order(store) -> None:
    """同一家族的常规、粗体、斜体、粗斜体文件不论上传顺序都进同一张卡片（同一个家族名，渲染时按字重与斜体选文件）：粗体先上传也由它建立卡片；
    Italic 或 Oblique 是卡片的斜体。注册表里卡片的第一个文件以家族名本身登记，之后的文件以“家族名<TAB>字形”登记；字体库的家族列表每张卡片只出现一次。"""
    bold = upload_font(store, "Zed Serif", "测试", "Bold", weight=700)
    italic = upload_font(store, "Zed Serif", "效果", "Italic", italic=True)
    regular = upload_font(store, "Zed Serif", "字体")
    bold_italic = upload_font(store, "Zed Serif", "Abc", "Bold Italic", weight=700, italic=True)
    assert {font.family for font in (bold, italic, regular, bold_italic)} == {"Zed Serif"}
    card = cards(store.listing())["Zed Serif"]
    assert [(font.slot, font.weights) for font in card] == [("regular", [400]), ("bold", [700]), ("italic", [400]), ("bold_italic", [700])]
    keys = {record["id"]: record["family"] for record in font_store.listing()}
    assert [keys[font.id] for font in (bold, italic, regular, bold_italic)] == ["Zed Serif", "Zed Serif\titalic", "Zed Serif\tregular", "Zed Serif\tbold_italic"]
    assert store.families().count("Zed Serif") == 1
    smiley = [(3, 1, 0x0409, 16, "Smiley Sans"), (3, 1, 0x0409, 17, "Oblique"), (3, 1, 0x0804, 16, "得意黑"), (3, 1, 0x0804, 17, "斜体")]
    oblique = upload_font(store, "Smiley Sans Oblique", "测试", "Regular", *smiley)
    assert (oblique.family, oblique.slot, oblique.label) == ("Smiley Sans", "italic", "得意黑")


def test_other_weights_and_filled_styles_make_cards_of_their_own(store, catalog) -> None:
    """不属于卡片字形的字重（Thin、Black 等）单独成卡片，家族名与显示名带字重，它的斜体（Thin Italic）进同一张卡片；家族名已以字重结尾时不重复；
    可变字体只看是否斜体（字重名只是默认实例）。已经有的字形再上传另一个版本时另起卡片（家族名加版本号）；预装字体的卡片不会被上传加入。"""
    thin = upload_font(store, "Zed Sans Thin", "测试", "Regular", (3, 1, 0x0409, 16, "Zed Sans"), (3, 1, 0x0409, 17, "Thin"))
    assert (thin.family, thin.slot, thin.label) == ("Zed Sans Thin", "regular", "Zed Sans Thin")
    thin_italic = upload_font(store, "Zed Sans Thin", "效果", "Italic", (3, 1, 0x0409, 16, "Zed Sans"), (3, 1, 0x0409, 17, "Thin Italic"), italic=True)
    assert (thin_italic.family, thin_italic.slot) == ("Zed Sans Thin", "italic")
    assert upload_font(store, "Zed Black", "测试", "Black").family == "Zed Black"
    variable = upload_font(store, "Zed Sans Thin", "字体", "Regular", (3, 1, 0x0409, 16, "Zed Sans"), (3, 1, 0x0409, 17, "Thin"), variable=True)
    assert (variable.family, variable.slot) == ("Zed Sans", "regular")
    upload_font(store, "Zed Serif", "测试", "Bold", weight=700)
    again = upload_font(store, "Zed Serif", "效果", "Bold", (3, 1, 0x0409, 5, "Version 2.1.5"), weight=700)
    assert (again.family, again.slot) == ("Zed Serif v2.1.5", "bold")
    bold = upload_font(store, "Alpha Sans", "测试", "Bold", weight=700)
    assert (bold.family, bold.slot) == ("Alpha Sans (2)", "bold")


def test_sync_gives_an_older_upload_its_style_so_its_family_can_complete_the_card(store) -> None:
    """卡片有字形之前上传的文件都当作常规体：同步时读出它的字形（如粗体），字重随之改为 700，家族名不变；之后上传的常规体加入这张卡片。"""
    bold = upload_font(store, "Zed Serif", "测试", "Bold", weight=700)
    font_store.update_data(bold.id, {key: value for key, value in bold.model_dump(exclude={"id", "family", "slot"}).items()} | {"weights": [400]})
    store.refresh()
    assert next(font.slot for font in store.listing() if font.id == bold.id) == "regular"
    asyncio.run(store.sync())
    assert next((font.slot, font.weights) for font in store.listing() if font.id == bold.id) == ("bold", [700])
    regular = upload_font(store, "Zed Serif", "字体")
    assert regular.family == "Zed Serif" and [font.slot for font in cards(store.listing())["Zed Serif"]] == ["regular", "bold"]


def test_a_new_upload_reads_an_older_files_style_before_placing_it(store) -> None:
    """旧上传在同步补字形之前，新上传会先读旧文件的样式；同一字形的另一版本不会占用同一个 slot。"""
    old = upload_font(store, "Zed Serif", "测试", "Bold", weight=700)
    font_store.update_data(old.id, {key: value for key, value in old.model_dump(exclude={"id", "family", "slot"}).items()} | {"weights": [400]})
    store.refresh()
    assert next(font.slot for font in store.listing() if font.id == old.id) == "regular"
    again = upload_font(store, "Zed Serif", "效果", "Bold", weight=700)
    assert (again.family, again.slot) == ("Zed Serif (2)", "bold")
    assert [(font.id, font.slot) for font in cards(store.listing())["Zed Serif"]] == [(old.id, "bold")]
    assert [font.slot for font in cards(store.listing())["Zed Serif (2)"]] == ["bold"]


def test_style_can_be_camel_case_or_punctuated_and_a_family_keeps_its_weight(store) -> None:
    """样式词可写成 BoldItalic 或 Bold, Italic；family 已带 Black 等字重时不再重复拼字重（斜体进该 family 的斜体槽）。"""
    black = upload_font(store, "Foo Black Italic", "测试", "Black Italic", weight=900, italic=True)
    assert (black.family, black.slot) == ("Foo Black Italic", "italic")
    bold_italic = upload_font(store, "Bar Sans", "效果", "BoldItalic", weight=700, italic=True)
    assert (bold_italic.family, bold_italic.slot) == ("Bar Sans", "bold_italic")
    comma = upload_font(store, "Baz Sans", "字体", "Bold, Italic", weight=700, italic=True)
    assert (comma.family, comma.slot) == ("Baz Sans", "bold_italic")


def test_other_weights_and_versions_of_a_family_get_their_own_family(store) -> None:
    """同一家族的其他版本、只在旧式家族名里写明字重的文件，都能上传：先上传的用字体自己的家族名，后来的依次用旧式家族名（带字重）、
    家族名加版本号、家族名加序号（大小写不同也算同名）；显示名仍是字体自己的名称。"""
    def family(name: str, characters: str, *records) -> str:
        """Upload a font with these names and glyphs and return its registry family."""
        return store.upload(minimal_font(name, characters, names=records), None, "", "device-1").family

    assert family("Smiley Sans", "测试") == "Smiley Sans"
    assert family("Smiley Sans", "效果", (3, 1, 0x0409, 5, "Version 2.000;hotconv 1.0.109")) == "Smiley Sans v2.000"
    assert family("Smiley Sans", "字体") == "Smiley Sans (2)"
    assert family("smiley sans", "Abc") == "smiley sans (3)"
    assert family("Alibaba PuHuiTi 2.0 55 Regular", "测试", (3, 1, 0x0409, 16, "Alibaba PuHuiTi 2.0")) == "Alibaba PuHuiTi 2.0"
    assert family("Alibaba PuHuiTi 2.0 85 Bold", "测试", (3, 1, 0x0409, 16, "Alibaba PuHuiTi 2.0")) == "Alibaba PuHuiTi 2.0 85 Bold"
    assert family("x" * 100, "测试") == "x" * 100
    assert family("x" * 100, "效果") == "x" * 96 + " (2)"
    assert [font.label for font in store.listing() if font.family.startswith("Smiley Sans")] == ["Smiley Sans"] * 3


def test_an_installed_or_reserved_family_is_renamed_for_an_upload(store, monkeypatch) -> None:
    """家族名与沙箱已装字体（如 Liberation Sans）、默认字体或 CSS 通用族名相同的上传换一个家族名，渲染时不会与它们混淆。"""
    monkeypatch.setattr(fonts_module, "system_families", lambda: frozenset({"liberation sans"}))
    assert store.upload(minimal_font("Liberation Sans"), None, "", "device-1").family == "Liberation Sans (2)"
    assert store.upload(minimal_font("Noto Sans CJK SC"), None, "", "device-1").family == "Noto Sans CJK SC (2)"
    assert store.upload(minimal_font("serif"), None, "", "device-1").family == "serif (2)"


def test_fontconfig_lists_each_font_file_under_its_registry_family_only(store) -> None:
    """生成的 fonts.conf 为每个字体文件（预装与上传）加一条扫描规则：按沙箱里的文件路径把家族名替换成注册表里的家族名（XML 转义），
    同名的两个文件因此各自只对应自己的家族名；没有文件的系统默认字体不加规则。"""
    asyncio.run(store.sync())
    first = store.upload(minimal_font("A&B <Sans>", "测试"), None, "", "device-1")
    second = store.upload(minimal_font("A&B <Sans>", "效果"), None, "", "device-1")
    text = (store.root / CONFIG_NAME).read_text(encoding="utf-8")
    assert "A&amp;B &lt;Sans&gt; (2)" in text
    rules = {
        rule.find("test/string").text: [edit.find("string").text for edit in rule.findall("edit")]
        for rule in ElementTree.fromstring(text).findall("match")
    }
    assert rules == {
        "/imv-fonts/files/alpha.ttf": ["Alpha Sans", "en"],
        "/imv-fonts/files/beta.ttf": ["Beta Serif", "en"],
        f"/imv-fonts/files/{first.cache_name}": ["A&B <Sans>", "en"],
        f"/imv-fonts/files/{second.cache_name}": ["A&B <Sans> (2)", "en"],
    }


@pytest.mark.skipif(shutil.which("fc-match") is None, reason="needs fontconfig's fc-match")
def test_a_bundled_fonts_internal_alias_does_not_shadow_an_upload(store, monkeypatch, tmp_path) -> None:
    """真实 fontconfig：随包的 Montserrat 可变字体文件里还带着默认实例的旧式家族名“Montserrat Thin”。静态的 Montserrat Thin
    上传登记为“Montserrat Thin”后，这个名称只解析到上传的文件，不会被预装字体顶替；“Montserrat”仍解析到预装字体。
    同一张卡片的常规与粗体共用家族名，按字重解析到各自的文件。
    fonts.conf 的路径改指本机缓存目录（沙箱里它挂在 /imv-fonts），并去掉系统配置，只看字体库里的文件（本机装的字体不参与）。"""
    monkeypatch.setattr(fonts_module, "SANDBOX_DIR", str(store.root.resolve()))
    monkeypatch.setattr(fonts_module, "system_families", lambda: frozenset())
    bundled = next(item for item in load_catalog() if item.family == "Montserrat")
    staged = tmp_path / "montserrat.ttf"
    staged.write_bytes(lzma.decompress((Path(fonts_module.__file__).with_name("bundled_fonts") / f"{bundled.filename}.xz").read_bytes()))
    assert store._register(bundled.font(), staged, None)
    names = [(3, 1, 0x0409, 16, "Montserrat"), (3, 1, 0x0409, 17, "Thin")]
    upload = store.upload(minimal_font("Montserrat Thin", names=names), None, "", "device-1")
    assert upload.family == "Montserrat Thin"
    regular = upload_font(store, "Zed Card", "测试")
    bold = upload_font(store, "Zed Card", "效果", "Bold", weight=700)
    generated = (store.root / CONFIG_NAME).read_text(encoding="utf-8")
    config = tmp_path / "library-only.conf"
    config.write_text(generated.replace('  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>\n', ""), encoding="utf-8")
    assert "/etc/fonts" not in config.read_text(encoding="utf-8")
    environment = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "XDG_CACHE_HOME": str(tmp_path / "cache"), "FONTCONFIG_FILE": str(config)}

    def match(pattern: str) -> str:
        """The file fontconfig picks for a pattern under the generated config."""
        return subprocess.run(["fc-match", "--format", "%{file}", pattern], env=environment, capture_output=True, text=True, timeout=60, check=True).stdout

    assert match("Montserrat Thin").endswith(upload.cache_name)
    assert match("Montserrat Thin:weight=400").endswith(upload.cache_name)
    assert match("Montserrat:weight=700").endswith(bundled.font().cache_name)
    assert match("Zed Card").endswith(regular.cache_name) and match("Zed Card:weight=700").endswith(bold.cache_name)


def test_an_upload_racing_another_host_is_renamed_or_refused(store) -> None:
    """另一台主机刚登记、本机还没同步到的情况：家族名被占用时换下一个名称再登记；同一个文件已被登记时拒绝并说明是哪个字体。"""
    other = minimal_font("Zed Display", "测试")
    font_store.insert("u-elsewhere", "Zed Display", {**store.listing()[0].model_dump(exclude={"id", "family"}), "sha256": "0" * 64}, "device-9")
    assert store.upload(minimal_font("Zed Display", "效果"), None, "", "device-1").family == "Zed Display (2)"
    copy = hashlib.sha256(other).hexdigest()
    font_store.insert("u-copy", "Copy Sans", {**store.listing()[0].model_dump(exclude={"id", "family"}), "label": "副本", "sha256": copy}, "device-9")
    with pytest.raises(FontExists, match="已经在字体库里了：副本（Copy Sans）") as existing:
        store.upload(other, None, "", "device-1")
    assert existing.value.font.id == "u-copy"


def test_the_agent_guide_is_the_font_library_as_an_enum(store) -> None:
    """Agent 拿到的是数据库里字体库的枚举：一开始只有默认字体（库不会是空的）；同步预装字体和上传后都出现在枚举里，并带风格与字重说明。"""
    guide = store.guide()
    assert '"enum":["Noto Sans CJK SC"]' in guide and "fontFamily" in guide and "never others" in guide
    asyncio.run(store.sync())
    store.upload(minimal_font("Zed Display"), None, "", "device-1")
    guide = store.guide()
    assert '"enum":["Noto Sans CJK SC", "Alpha Sans", "Beta Serif", "Zed Display"]' in guide
    assert '"Beta Serif" (Beta Serif; fixture style; styles regular 200 900)' in guide


def test_text_effects_must_expose_a_font_family_enum_when_generated() -> None:
    """生成路径（传入字体库家族名）要求字效暴露 fontFamily 枚举：取值都在字体库里、默认值是枚举之一、不能只给 maxLength；发布更早的版本（不传）不要求。"""
    rules = REQUIRED["text"]
    properties = {rule.name: rule.example for rule in rules}
    defaults = {rule.name: rule.off for rule in rules if rule.off is not None}
    bound = set(properties)
    library = {"Noto Sans CJK SC", "Zed Display"}
    check = lambda definition, default: category_problems(
        "text", {"properties": {**properties, **({"fontFamily": definition} if definition else {})}},
        bound | ({"fontFamily"} if definition else set()), {**defaults, "fontFamily": default}, library,
    )
    assert category_problems("text", {"properties": properties}, bound, defaults) == []
    assert [(item.name, item.reason) for item in check(None, "x")] == [("fontFamily", "missing")]
    assert check({"type": "string", "enum": ["Zed Display", "Noto Sans CJK SC"]}, "Zed Display") == []
    assert "not in the font library" in check({"type": "string", "enum": ["Zed Display", "Ghost"]}, "Zed Display")[0].reason
    assert "must default" in check({"type": "string", "enum": ["Zed Display"]}, "Noto Sans CJK SC")[0].reason
    assert check({"type": "string", "maxLength": 100}, "Zed Display")[0].reason == "has the wrong schema"
    assert category_problems("filter_overlay", {"properties": {}}, set(), {}, library)[0].name == "intensity"


def test_a_font_family_override_must_name_a_library_font(store) -> None:
    """模板里 fontFamily 的覆盖值必须是字体库里的家族名，否则保存时就被拒绝，而不是渲染时悄悄回落。"""
    store.upload(minimal_font("Zed Display"), None, "", "device-1")
    require_family("Zed Display")
    require_family("Noto Sans CJK SC")
    with pytest.raises(ValueError, match="不是字体库里的字体"):
        require_family("Nonexistent Font")


def test_the_stored_font_schema_is_an_open_string() -> None:
    """保存的 Sprite 把 fontFamily 的枚举换成普通字符串（保留标题等说明），没有该参数或没有枚举的 Schema 原样返回。"""
    schema = {"type": "object", "properties": {"fontFamily": {"type": "string", "title": "字体", "enum": ["A", "B"]}, "text": {"type": "string"}}}
    assert open_font_schema(schema)["properties"]["fontFamily"] == {"type": "string", "title": "字体", "maxLength": 100}
    assert open_font_schema(schema)["properties"]["text"] == {"type": "string"}
    assert schema["properties"]["fontFamily"]["enum"] == ["A", "B"]
    plain = {"type": "object", "properties": {"text": {"type": "string"}}}
    assert open_font_schema(plain) is plain
    # A two-tier text's tier fonts are library fonts too.
    tiers = {"type": "object", "properties": {f"line{tier}FontFamily": {"type": "string", "enum": ["A"]} for tier in (1, 2)}}
    assert all(definition == {"type": "string", "maxLength": 100} for definition in open_font_schema(tiers)["properties"].values())


def font_sprite(name: str = "fontFamily", *, frozen: bool = True, kind: int | None = None):
    """已发布的字效；默认保留旧发布常见的冻结字体枚举，可切换为非文字 Sprite 或开放字符串。"""
    from generated.imv.sprite.v1 import sprite_pb2 as sprite_pb

    sprite = published_sprite(kind=pb.SPRITE_KIND_TEXT if kind is None else kind)
    schema = json.loads(sprite.config_schema_json)
    schema["properties"][name] = (
        {"type": "string", "enum": ["Noto Sans CJK SC"], "title": "字体"} if frozen
        else {"type": "string", "maxLength": 100}
    )
    defaults = json.loads(sprite.default_config_json) | {name: "Noto Sans CJK SC"}
    sprite.config_schema_json, sprite.default_config_json = json.dumps(schema), json.dumps(defaults)
    only = sprite_pb.ScalarValue(string_value="Noto Sans CJK SC")
    sprite.parameters.add(
        key=name, label="字体", access=sprite_pb.OPERATOR_ACCESS_VISIBLE_EDITABLE,
        default_value=only, max_length=100,
        **({} if not frozen else {"allowed_values": [only]}),
    )
    return sprite


def font_editor(family: str, name: str = "fontFamily") -> RemotionTrackEditor:
    """一次摆放把字体参数 ``name`` 覆盖成 family。"""
    return RemotionTrackEditor.model_validate({"parameters": [{"key": name, "stringValue": family}]})


@pytest.mark.parametrize("name", ["fontFamily", "line2FontFamily"])
def test_a_font_added_after_publication_can_be_chosen_for_an_older_sprite(store, name) -> None:
    """旧发布仍带冻结字体枚举，但字体库在发布后增长：摆放覆盖按实时字体库校验，新字体可保存，伪造字体仍被拒绝。"""
    store.upload(minimal_font("Zed Display"), None, "", "device-1")
    sprite = font_sprite(name)
    assert json.loads(sprite.config_schema_json)["properties"][name]["enum"] == ["Noto Sans CJK SC"]
    config, _ = resolve(sprite, font_editor("Zed Display", name))
    assert config[name] == "Zed Display"
    with pytest.raises(ValueError, match=f"参数 {name} 不是字体库里的字体"):
        resolve(sprite, font_editor("Ghost Font", name))


def test_an_unsampled_publication_still_lets_templates_change_its_font(store) -> None:
    """没有渲染过参数边界样本的旧字效只读，但字体来自实时字体库、保存摆放时另行校验，所以在模板里仍可换字体；
    其他参数保持只读，不能借字体例外修改字号；冻结枚举也不能阻止发布后新增的字体。"""
    from generated.imv.sprite.v1 import sprite_pb2 as sprite_pb

    store.upload(minimal_font("Zed Display"), None, "", "device-1")
    sprite = font_sprite()
    sprite.ClearField("content_box")
    access = {item.key: item.access for item in operator_parameters(sprite)}
    assert access["fontFamily"] == sprite_pb.OPERATOR_ACCESS_VISIBLE_EDITABLE
    assert access["fontSize"] == sprite_pb.OPERATOR_ACCESS_VISIBLE_READ_ONLY
    config, _ = resolve(sprite, font_editor("Zed Display"))
    assert config["fontFamily"] == "Zed Display"
    with pytest.raises(ValueError, match="参数 fontSize 不可修改"):
        resolve(sprite, RemotionTrackEditor.model_validate({"parameters": [{"key": "fontSize", "numberValue": 48}]}))
    with pytest.raises(ValueError, match="不是字体库里的字体"):
        resolve(sprite, font_editor("Ghost Font"))


def test_a_font_named_parameter_on_a_non_text_sprite_is_not_a_font(store) -> None:
    """只有文字 Sprite 的 fontFamily 有字体语义；其他分类的同名参数保持原枚举和原可写规则。"""
    from generated.imv.sprite.v1 import sprite_pb2 as sprite_pb

    store.upload(minimal_font("Zed Display"), None, "", "device-1")
    sprite = font_sprite(kind=pb.SPRITE_KIND_VIDEO_OVERLAY)
    sprite.ClearField("content_box")
    access = {item.key: item.access for item in operator_parameters(sprite)}
    assert access["fontFamily"] == sprite_pb.OPERATOR_ACCESS_VISIBLE_READ_ONLY
    assert not is_font_parameter(sprite.kind, "fontFamily")
    with pytest.raises(ValueError, match="参数 fontFamily 不可修改"):
        resolve(sprite, font_editor("Zed Display"))
    assert is_font_parameter(pb.SPRITE_KIND_TEXT, "fontFamily")


def sandbox_arguments(settings: Settings, tmp_path: Path) -> list[str]:
    """渲染沙箱命令行。"""
    return Renderer(settings).command(tmp_path)


def test_the_sandbox_mounts_downloaded_fonts_read_only_and_points_fontconfig_at_them(tmp_path, settings, store) -> None:
    """已有生成的字体配置时，沙箱只读挂载字体目录并设置 FONTCONFIG_FILE；还没有时不挂载，保持原有行为。"""
    (store.root / CONFIG_NAME).unlink()
    before = sandbox_arguments(settings, tmp_path)
    assert "/imv-fonts" not in before and "FONTCONFIG_FILE" not in before
    store.refresh()
    after = sandbox_arguments(settings, tmp_path)
    mount = after.index("/imv-fonts")
    assert after[mount - 2:mount + 1] == ["--ro-bind", str((tmp_path / "state" / "fonts").resolve()), "/imv-fonts"]
    assert after[after.index("FONTCONFIG_FILE") + 1] == f"/imv-fonts/{CONFIG_NAME}"


def clip(code: str, **config) -> Overlay:
    """A one-clip overlay whose code is `code` and whose parameters are `config`."""
    return Overlay(width=1080, height=1920, fps=30, frames=30, instances=[{"code": code, "config": config}])


def test_a_render_guard_caches_a_named_font_before_rendering(store, monkeypatch) -> None:
    """渲染的代码点名了注册表里的字体、本机还没缓存：先从对象存储取回并校验；没有活动的字体存储时不做任何事。"""
    asyncio.run(store.sync())
    (store.files / "beta.ttf").unlink()
    store.refresh()
    assert "beta" not in store.ready
    asyncio.run(require_fonts(clip('fontFamily: "Default Only"')))
    asyncio.run(require_fonts(clip('fontFamily: "Beta Serif, serif"')))
    assert "beta" in store.ready
    monkeypatch.setattr(FontStore, "active", None)
    asyncio.run(require_fonts(clip('fontFamily: "Beta Serif"')))


def test_a_render_is_refused_when_a_named_font_cannot_be_obtained(store, objects) -> None:
    """字体取不回来（对象存储里没有）时，点名该字体的渲染以稳定错误码 font_unavailable 失败，不会悄悄用默认字体渲染。"""
    asyncio.run(store.sync())
    (store.files / "alpha.ttf").unlink()
    (objects.root / "imv/remotion/fonts/alpha/font.ttf").unlink()
    store.refresh()
    with pytest.raises(OverlayError) as failure:
        asyncio.run(require_fonts(clip('fontFamily: "Alpha Sans"')))
    assert failure.value.code == "font_unavailable"


@pytest.fixture
def served(settings, store, client: TestClient):
    """字体路由使用的运行时替身：只带设置与设备登记。"""
    remotion_templates_app.dependency_overrides[routes.runtime] = lambda: SimpleNamespace(
        settings=settings, store=SimpleNamespace(touch=lambda device: None),
    )
    yield store
    remotion_templates_app.dependency_overrides.clear()


def test_the_font_api_lists_cards_with_their_styles(client: TestClient, served) -> None:
    """字体接口按卡片列出：一张卡片含它已有的字形（常规、粗体、斜体、粗斜体的顺序），各带文件与预览条地址、字重、大小与 SHA-256；
    卡片的字重合并各字形，大小为各文件之和，预览条取第一个有预览条的字形。上传一个字形返回它所在的整张卡片。"""
    asyncio.run(served.sync())
    bold = minimal_font("Zed Display", "测试", style="Bold", weight=700)
    first = client.post(
        "/api/templates/fonts", headers=DEVICE_HEADERS, data={"label": "Zed Display"},
        files={"file": ("bold.ttf", bold, "font/ttf"), "preview": ("p.png", strip_png(), "image/png")},
    )
    assert first.status_code == 201 and [face["slot"] for face in first.json()["faces"]] == ["bold"]
    regular = minimal_font("Zed Display", "效果")
    card = client.post("/api/templates/fonts", headers=DEVICE_HEADERS, files={"file": ("regular.ttf", regular, "font/ttf")}).json()
    assert card["family"] == "Zed Display" and [face["slot"] for face in card["faces"]] == ["regular", "bold"]
    assert card["weights"] == [400, 700] and card["size"] == len(bold) + len(regular)
    assert card["faces"][0]["preview_url"] is None and card["preview_url"] == card["faces"][1]["preview_url"] is not None
    assert card["faces"][1]["sha256"] == hashlib.sha256(bold).hexdigest() and client.get(card["faces"][1]["file_url"]).content == bold
    listed = client.get("/api/templates/fonts").json()
    assert [item["family"] for item in listed].count("Zed Display") == 1 and listed[-1] == card


def test_the_font_api_lists_serves_and_uploads(client: TestClient, served, settings, monkeypatch, tmp_path) -> None:
    """字体接口：列表给出家族名、风格、字重、文件与预览地址；文件带 CORS 可被预览 iframe 加载；系统默认字体的文件是服务端安装的常规字重（字体详情的字样用它），
    没有安装时 404 且不给出 file_url；上传（multipart）返回 201 与新字体且立即出现在列表；重复的文件返回 200 与同一张卡片（幂等，超时后重试
    不会被当成失败），超大返回 413，缺少设备头返回 422；未知字体 404。"""
    system_font = tmp_path / "system.ttc"
    system_font.write_bytes(b"system-collection")
    monkeypatch.setattr(settings, "font_regular", system_font)
    asyncio.run(served.sync())
    listed = client.get("/api/templates/fonts").json()
    assert [item["family"] for item in listed] == ["Noto Sans CJK SC", "Alpha Sans", "Beta Serif"]
    assert listed[0]["source"] == "system" and listed[0]["faces"][0]["file_url"] == "/api/templates/fonts/noto-sans-cjk"
    assert listed[2]["weights"] == [200, 900] and listed[1]["file_url"] == "/api/templates/fonts/alpha"
    monkeypatch.setattr(settings, "font_regular", tmp_path / "missing.ttc")
    missing = client.get("/api/templates/fonts").json()
    assert missing[0]["file_url"] is None and missing[0]["faces"][0]["file_url"] is None
    assert client.get("/api/templates/fonts/noto-sans-cjk").status_code == 404
    system_font.write_bytes(b"system-collection")
    monkeypatch.setattr(settings, "font_regular", system_font)
    system = client.get("/api/templates/fonts/noto-sans-cjk")
    assert (system.status_code, system.content, system.headers["content-type"]) == (200, b"system-collection", "font/collection")
    file = client.get("/api/templates/fonts/alpha")
    assert (file.status_code, file.content) == (200, PAYLOADS["alpha"])
    assert file.headers["access-control-allow-origin"] == "*" and file.headers["content-type"] == "font/ttf"
    assert client.get("/api/templates/fonts/unknown").status_code == 404
    upload = client.post(
        "/api/templates/fonts", headers=DEVICE_HEADERS, data={"label": "Zed Display"},
        files={"file": ("zed.ttf", minimal_font("Zed Display"), "font/ttf"), "preview": ("p.png", strip_png(), "image/png")},
    )
    created = upload.json()
    assert upload.status_code == 201 and created["family"] == "Zed Display" and created["source"] == "upload" and created["label"] == "Zed Display"
    assert client.get(created["preview_url"]).headers["content-type"] == "image/png"
    assert client.get(created["file_url"]).content == minimal_font("Zed Display")
    assert [item["family"] for item in client.get("/api/templates/fonts").json()][-1] == "Zed Display"
    duplicate = client.post("/api/templates/fonts", headers=DEVICE_HEADERS, files={"file": ("again.ttf", minimal_font("Zed Display"), "font/ttf")})
    assert duplicate.status_code == 200 and duplicate.json() == client.get("/api/templates/fonts").json()[-1]
    assert [item["family"] for item in client.get("/api/templates/fonts").json()].count("Zed Display") == 1
    served.max_upload = 2 * 1024 * 1024
    big = client.post("/api/templates/fonts", headers=DEVICE_HEADERS, files={"file": ("big.ttf", b"\0" * (2 * 1024 * 1024 + 1), "font/ttf")})
    assert (big.status_code, big.json()["detail"]) == (413, "字体文件超过 2 MiB 上限")
    assert client.post("/api/templates/fonts", files={"file": ("x.ttf", b"x", "font/ttf")}).status_code == 422



# 场景（回归）：客户端等不及（超时、断线）而服务端已经登记了字体，用户重试或别人再传同一个文件，原先得到 422「已经在字体库里了」
# 而界面找不到它；现在重复上传返回它所在的卡片。与尚未装好的预装字体相同时没有卡片可返回，仍是 422。
def test_uploading_a_registered_file_again_returns_its_card(client: TestClient, served) -> None:
    """另一台设备再传同一个文件得到 200 与同一张卡片，注册表不多出记录；与未装好的预装字体相同的文件 422 并说明是哪个字体。"""
    pending = client.post("/api/templates/fonts", headers=DEVICE_HEADERS, files={"file": ("alpha.ttf", PAYLOADS["alpha"], "font/ttf")})
    assert pending.status_code == 422 and "已经在字体库里了：Alpha Sans（Alpha Sans）" in pending.json()["detail"]
    data = minimal_font("Zed Display", names=[(3, 1, 0x0804, 1, "我的字体")])
    created = client.post("/api/templates/fonts", headers=DEVICE_HEADERS, files={"file": ("zed.ttf", data, "font/ttf")})
    assert created.status_code == 201
    again = client.post(
        "/api/templates/fonts", headers={"X-IMV-Device": str(uuid4())}, files={"file": ("copy.ttf", data, "font/ttf")},
    )
    assert again.status_code == 200 and again.json() == created.json()
    assert [record["id"] for record in font_store.listing()].count(created.json()["faces"][0]["id"]) == 1


# 场景（回归）：另一个请求刚登记同一个文件，本机缓存还没同步到——重复上传仍返回 200 与卡片，而不是把缓存里的缺失当成 422。
def test_a_file_registered_elsewhere_is_answered_with_its_card(client: TestClient, served) -> None:
    """本机缓存落后于注册表时，重复上传读取最新注册表并返回已有卡片，不重复登记。"""
    data = minimal_font("Zed Display", names=[(3, 1, 0x0804, 1, "我的字体")])
    created = client.post("/api/templates/fonts", headers=DEVICE_HEADERS, files={"file": ("zed.ttf", data, "font/ttf")})
    assert created.status_code == 201
    font_id = created.json()["faces"][0]["id"]
    # 模拟另一个进程已写入注册表、本机还没刷新缓存：数据库里有这一行，`known` 里没有。
    served.known.pop(font_id)
    again = client.post("/api/templates/fonts", headers=DEVICE_HEADERS, files={"file": ("again.ttf", data, "font/ttf")})
    assert again.status_code == 200 and again.json() == created.json()
    assert [record["id"] for record in font_store.listing()].count(font_id) == 1


# 场景（回归）：对象存储或数据库失败时上传回答 503，原先服务端没有任何日志，线上只能在访问日志里看到 503。
def test_a_failed_font_upload_is_logged(client: TestClient, served, monkeypatch, caplog) -> None:
    """对象存储写入失败回答 503 与中文说明，根因（含文件名与大小）写服务端日志；字体没有登记。"""

    def unreachable(key, path, content_type):
        """模拟 ZOS 不可达。"""
        raise ObjectStoreError("ZOS upload failed")

    monkeypatch.setattr(served.objects, "put", unreachable)
    data = minimal_font("Zed Display")
    with caplog.at_level(logging.ERROR, logger="server.remotion_templates.routes"):
        response = client.post("/api/templates/fonts", headers=DEVICE_HEADERS, files={"file": ("zed.ttf", data, "font/ttf")})
    assert (response.status_code, response.json()["detail"]) == (503, "字体暂时无法保存，请稍后重试。")
    [record] = [item for item in caplog.records if item.name == "server.remotion_templates.routes"]
    assert f"zed.ttf ({len(data)} bytes)" in record.getMessage() and isinstance(record.exc_info[1], ObjectStoreError)
    assert not [font for font in served.listing() if font.source == "upload"]

def test_the_preview_page_lists_fonts_for_lazy_registration() -> None:
    """预览页以 JSON 元素列出有文件的字体文件（卡片的家族名、CSS 字重与斜体、URL）供播放器注册，系统默认字体不列（预览本来就自带）；
    斜体文件不带旧格式的 weight，卡片有字形之前构建的播放器只认 family/weight/url，会跳过它而不是把斜体当正体注册。没有字体时不输出该元素。"""
    font = spec("beta", "Beta Serif", weights=[200, 900]).font()
    system = font.model_copy(update={"id": "noto-sans-cjk", "family": "Noto Sans CJK SC", "source": "system"})
    italic = font.model_copy(update={"id": "u-italic", "weights": [400], "slot": "italic", "source": "upload"})
    page = routes.player_page("void 0", fonts=[system, font, italic]).body.decode()
    data = page.split('id="imv-preview-fonts">', 1)[1].split("</script>", 1)[0]
    assert json.loads(data) == [
        {"family": "Beta Serif", "weight": "200 900", "descriptors": {"weight": "200 900", "style": "normal"}, "url": "/api/templates/fonts/beta"},
        {"family": "Beta Serif", "descriptors": {"weight": "400", "style": "italic"}, "url": "/api/templates/fonts/u-italic"},
    ]
    assert "imv-preview-fonts" not in routes.player_page("void 0").body.decode()


# 与真实浏览器、沙箱和锁定的渲染依赖相关的用例：需要 IMV_TEST_RENDERER=1，
# 浏览器与字体位置在导入阶段读取（autouse 隔离夹具会清除 IMV_* 变量）。
BROWSER = os.environ.get("IMV_BROWSER_EXECUTABLE")
SYSTEM_FONT = os.environ.get("IMV_FONT_REGULAR")
needs_browser = pytest.mark.skipif(
    os.environ.get("IMV_TEST_RENDERER") != "1" or not BROWSER or not SYSTEM_FONT or shutil.which("bwrap") is None,
    reason="set IMV_TEST_RENDERER=1 with IMV_BROWSER_EXECUTABLE and IMV_FONT_REGULAR on Linux with bubblewrap",
)

FAMILY_PROBE = """import React from "react";
export default function Probe(props: { family: string; weight: number; italic: boolean }) {
  return <div data-probe style={{ position: "absolute", left: 40, top: 40, fontSize: 80, whiteSpace: "nowrap", fontFamily: props.family, fontWeight: props.weight, fontStyle: props.italic ? "italic" : "normal" }}>测试字体效果 Abc</div>;
}
"""
WIDTH_TEST = 'export default async function (ctx) { const found = await ctx.query("[data-probe]"); ctx.assert(true, String(Math.round(found.box.width))); }'


@needs_browser
def test_an_uploaded_font_is_used_by_the_sandboxed_browser(tmp_path, template_db, objects) -> None:
    """真实沙箱里的 Chrome 按字体名使用上传的字体：一个只存在于字体库的自制字体（每个字正好一个字宽）排出的文字宽度与默认字体不同；
    字体名不存在时回落为默认字体的宽度。同一家族名的另一个文件（每个字半个字宽）登记为另一个家族名，两个名称各自用到自己的文件。
    预装字体同样按注册表家族名解析：随包的 Montserrat 可变字体照常可用；一个预装字体文件里的旧式家族名与某个上传的家族名相同时，
    这个名称只用到上传的文件。一张卡片的粗体与斜体文件在 font-weight 700 与 italic 时被选用（而不是由常规体合成）。"""
    from server.remotion_templates.tool_validation import ToolValidator
    from server.remotion_templates.tools.contracts import ComponentDefinition, RenderValidationInput, TestScript

    settings = Settings(
        _env_file=None, data_dir=tmp_path / "state", install_bundled_fonts=False, browser_executable=Path(BROWSER),
        font_regular=Path(SYSTEM_FONT), font_bold=Path(SYSTEM_FONT),
    )
    fonts = FontStore.for_settings(settings)
    fonts.attach(objects)
    fonts.refresh()
    fonts.upload(minimal_font("Imv Probe Sans"), None, "", "device-1")
    assert fonts.upload(minimal_font("Imv Probe Sans", advance=500), None, "", "device-1").family == "Imv Probe Sans (2)"
    montserrat = next(item for item in load_catalog() if item.family == "Montserrat")
    (tmp_path / "montserrat.ttf").write_bytes(lzma.decompress((fonts_module.BUNDLE / f"{montserrat.filename}.xz").read_bytes()))
    assert fonts._register(montserrat.font(), tmp_path / "montserrat.ttf", None)
    probe = minimal_font("Imv Builtin Probe Thin", names=[(3, 1, 0x0409, 16, "Imv Builtin Probe")], advance=250)
    (tmp_path / "probe.ttf").write_bytes(probe)
    builtin = Font(
        id="imv-builtin-probe", family="Imv Builtin Probe", label="Imv Builtin Probe", style="fixture", weights=[400],
        filename="ImvBuiltinProbe.ttf", sha256=hashlib.sha256(probe).hexdigest(), size=len(probe), source="builtin",
    )
    assert fonts._register(builtin, tmp_path / "probe.ttf", None)
    assert fonts.upload(minimal_font("Imv Builtin Probe Thin", advance=750), None, "", "device-1").family == "Imv Builtin Probe Thin"
    assert fonts.upload(minimal_font("Imv Probe Sans", style="Bold", weight=700, advance=250), None, "", "device-1").slot == "bold"
    assert fonts.upload(minimal_font("Imv Probe Sans", style="Italic", italic=True, advance=750), None, "", "device-1").slot == "italic"

    def width(family: str, weight: int = 400, italic: bool = False) -> int:
        """Mount the probe with one CSS family list, weight and style, and read the measured text width."""
        properties = {"family": {"type": "string"}, "weight": {"type": "integer", "minimum": 100, "maximum": 900}, "italic": {"type": "boolean"}}
        component = ComponentDefinition(
            code=FAMILY_PROBE,
            parameter_schema={"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False},
            default_parameters={"family": family, "weight": weight, "italic": italic},
        )
        request = RenderValidationInput(component=component, duration_frames=5, tests=[TestScript(name="width", code=WIDTH_TEST)])
        report = asyncio.run(ToolValidator(Renderer(settings), tmp_path).validate_render(request))
        assert report.passed, report
        return int(report.tests[0].assertions[0].message)

    default = width('"Noto Sans CJK SC", sans-serif')
    assert width('"Missing Family Zzz", "Noto Sans CJK SC", sans-serif') == default
    assert width('"Imv Probe Sans", "Noto Sans CJK SC", sans-serif') == 800
    assert width('"Imv Probe Sans (2)", "Noto Sans CJK SC", sans-serif') == 400
    assert width('"Montserrat", "Noto Sans CJK SC", sans-serif') != default
    assert width('"Imv Builtin Probe", "Noto Sans CJK SC", sans-serif') == 200
    assert width('"Imv Builtin Probe Thin", "Noto Sans CJK SC", sans-serif') == 600
    assert width('"Imv Probe Sans", "Noto Sans CJK SC", sans-serif', weight=700) == 200
    assert width('"Imv Probe Sans", "Noto Sans CJK SC", sans-serif', italic=True) == 600
