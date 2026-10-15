"""Remotion 字体库：MySQL 注册表 + 对象存储 + 本地校验缓存、用户上传、沙箱挂载、Agent 清单、API 与渲染守卫。

范围：`font_catalog.json` 的结构、预装字体复制进对象存储与注册表、从对象存储取回缓存（换机器/丢缓存仍可用）、
被篡改的缓存被剔除、上传字体的校验（格式、家族名、重名、大小、预览条）、`fontFamily` 的分类规则与覆盖校验、
沙箱命令是否挂载字体目录、Agent 字体清单、字体 API 与预览页、渲染前的字体守卫。数据库用 `template_db` 的临时 SQLite，
对象存储用本地目录，网络用 httpx 内存传输，不访问真实站点；真实浏览器里字体是否生效由 `IMV_TEST_RENDERER=1` 的用例验证。
执行：在 `server/` 下运行 `uv run --locked pytest tests/test_remotion_fonts.py -v`。
"""

import asyncio
import hashlib
import json
import os
import shutil
import struct
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server.remotion_templates import font_store
from server.remotion_templates import fonts as fonts_module
from server.remotion_templates import routes
from server.remotion_templates.api import app as remotion_templates_app
from server.remotion_templates.categories import REQUIRED, category_problems
from server.remotion_templates.fonts import CONFIG_NAME, FontRejected, FontSpec, FontStore, load_catalog, open_font_schema, read_family, require_family
from server.remotion_templates.object_store import LocalObjects
from server.remotion_templates.overlay_render import Overlay, OverlayError, require_fonts
from server.remotion_templates.placement import resolve
from server.remotion_templates.renderer import Renderer
from server.remotion_templates.settings import Settings

from server.template.schema import RemotionTrackEditor

from .conftest import DEVICE_HEADERS, published_sprite

PAYLOADS = {"alpha": b"alpha-font-bytes", "beta": b"beta-font-bytes-longer"}


def minimal_font(family: str, characters: str = "测试字体效果 Abc") -> bytes:
    """A tiny valid TrueType font: every listed character maps to one empty glyph exactly one em wide.

    Its text width (characters × font size) differs from the default font's, and it exists on no system, so a
    width that changes proves the browser loaded this file. Built by hand to avoid a font-tooling dependency.
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
    names = [(1, family), (2, "Regular"), (4, family), (6, family.replace(" ", ""))]
    strings = b"".join(value.encode("utf-16-be") for _, value in names)
    records, offset = b"", 0
    for name_id, value in names:
        size = len(value.encode("utf-16-be"))
        records += struct.pack(">HHHHHH", 3, 1, 0x409, name_id, size, offset)
        offset += size
    tables = dict([
        table(b"OS/2", struct.pack(">HhHHHhhhhhhhhhh10sIIII4sHHHhhhHH", 0, 1000, 400, 5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, bytes(10), 0, 0, 0, 0, b"IMV ", 0x40, 32, 0xFFFF, 800, -200, 0, 800, 200)),
        table(b"cmap", struct.pack(">HHHHI", 0, 1, 3, 1, 12) + cmap_subtable),
        table(b"glyf", bytes(4)),
        table(b"head", struct.pack(">IIIIHHqqhhhhHHhhh", 0x10000, 0x10000, 0, 0x5F0F3CF5, 0, 1000, 0, 0, 0, 0, 0, 0, 0, 8, 2, 0, 0)),
        table(b"hhea", struct.pack(">IhhhHhhhhhhhhhhhH", 0x10000, 800, -200, 0, 1000, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 2)),
        table(b"hmtx", struct.pack(">HhHh", 1000, 0, 1000, 0)),
        table(b"loca", bytes(6)),
        table(b"maxp", struct.pack(">IHHHHHHHHHHHHHH", 0x10000, 2, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0)),
        table(b"name", struct.pack(">HHH", 0, len(names), 6 + 12 * len(names)) + records + strings),
        table(b"post", struct.pack(">IIhhIIIII", 0x30000, 0, -100, 50, 0, 0, 0, 0, 0)),
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
def catalog(monkeypatch) -> tuple[FontSpec, FontSpec]:
    """两个可复制的小字体，替换随包目录（内容不是真字体，只用于哈希与存储）。"""
    specs = (spec("alpha", "Alpha Sans"), spec("beta", "Beta Serif", weights=[200, 900]))
    monkeypatch.setattr(fonts_module, "load_catalog", lambda: specs)
    return specs


@pytest.fixture
def settings(tmp_path, catalog, template_db) -> Settings:
    """开启复制的设置，数据目录在临时目录，注册表用临时 SQLite。"""
    return Settings(_env_file=None, data_dir=tmp_path / "state", font_downloads=True)


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


def serve(monkeypatch, responder) -> list[str]:
    """让来源下载走内存传输；返回按顺序请求过的 URL。"""
    requested: list[str] = []
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return responder(request)

    monkeypatch.setattr(fonts_module.httpx, "AsyncClient", lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs))
    return requested


def origin(request: httpx.Request) -> httpx.Response:
    """来源站点：按 URL 返回对应字体字节。"""
    return httpx.Response(200, content=PAYLOADS[Path(request.url.path).stem])


def test_the_bundled_catalog_pins_every_font() -> None:
    """随包目录：条目结构合法，ID 与字体名唯一，下载地址为 https，哈希为 64 位十六进制，并写明许可证。"""
    specs = load_catalog()
    assert len(specs) >= 10
    assert {item.license for item in specs} == {"OFL-1.1"}
    assert all(item.url.startswith("https://") and len(item.sha256) == 64 for item in specs)


def test_the_family_name_is_read_without_trusting_the_file() -> None:
    """家族名只读 name 表（带边界检查）：合法字体读出名称；不是 sfnt（如 WOFF）、被截断或没有名称表的文件抛 FontRejected。"""
    data = minimal_font("Zed Display")
    assert read_family(data) == "Zed Display"
    for broken in (b"wOFF" + data[4:], data[:20], data[:40], b"", b"\x00\x01\x00\x00" + b"\xff" * 40):
        with pytest.raises(FontRejected):
            read_family(broken)


def test_sync_copies_the_catalog_into_the_object_store_and_the_registry(store, objects, monkeypatch) -> None:
    """默认字体登记为 system（没有文件）排在最前；预装字体从来源复制一次：对象存储里有字体文件，注册表有记录（来源 builtin），本机缓存校验通过并生成沙箱用的配置；再次同步不重复下载。"""
    requested = serve(monkeypatch, origin)
    asyncio.run(store.sync())
    assert [font.id for font in store.available()] == ["noto-sans-cjk", "alpha", "beta"]
    assert {record["id"] for record in font_store.listing()} == {"noto-sans-cjk", "alpha", "beta"}
    assert [font.source for font in store.listing()] == ["system", "builtin", "builtin"]
    assert (objects.root / "imv/remotion/fonts/alpha/font.ttf").read_bytes() == PAYLOADS["alpha"]
    assert (store.files / "alpha.ttf").read_bytes() == PAYLOADS["alpha"]
    config = (store.root / CONFIG_NAME).read_text(encoding="utf-8")
    assert "<dir>/imv-fonts/files</dir>" in config and "/etc/fonts/fonts.conf" in config
    assert not list(store.files.glob("*.part"))
    asyncio.run(store.sync())
    assert len(requested) == 2


def test_another_host_caches_from_the_object_store_without_the_origin(store, objects, tmp_path, monkeypatch) -> None:
    """对象存储与注册表是唯一来源：换一台机器（空缓存、关闭来源复制）同步后字体照样可用，不访问来源站点。"""
    serve(monkeypatch, origin)
    asyncio.run(store.sync())
    requested = serve(monkeypatch, lambda request: httpx.Response(500))
    fresh = FontStore(Settings(_env_file=None, data_dir=tmp_path / "other-host", font_downloads=False))
    fresh.attach(objects)
    asyncio.run(fresh.sync())
    assert [font.id for font in fresh.available()] == ["noto-sans-cjk", "alpha", "beta"] and requested == []


def test_a_tampered_cache_file_is_dropped_and_fetched_again(store, monkeypatch) -> None:
    """本机缓存里被改动（哈希与注册表不符）的字体在刷新时删除、不可用，下次同步从对象存储取回正确文件。"""
    serve(monkeypatch, origin)
    asyncio.run(store.sync())
    (store.files / "beta.ttf").write_bytes(b"x" * len(PAYLOADS["beta"]))
    store.refresh()
    assert [font.id for font in store.available()] == ["noto-sans-cjk", "alpha"] and not (store.files / "beta.ttf").exists()
    asyncio.run(store.sync())
    assert (store.files / "beta.ttf").read_bytes() == PAYLOADS["beta"]


def test_a_catalog_font_that_does_not_match_its_pin_is_not_installed(store, objects, monkeypatch) -> None:
    """来源返回的内容与固定哈希不符时该字体不入库、不入对象存储；另一个字体照常安装，下次同步重试。"""
    served = {"alpha": b"tampered-bytes!!!"}
    serve(monkeypatch, lambda request: httpx.Response(200, content=served.get(Path(request.url.path).stem, PAYLOADS[Path(request.url.path).stem])))
    asyncio.run(store.sync())
    assert [font.id for font in store.listing()] == ["noto-sans-cjk", "beta"] and not (objects.root / "imv/remotion/fonts/alpha").exists()
    served.clear()
    asyncio.run(store.sync())
    assert {font.id for font in store.listing()} == {"noto-sans-cjk", "alpha", "beta"}


def test_seeding_can_be_switched_off(tmp_path, catalog, template_db, objects, monkeypatch) -> None:
    """关闭来源复制时不发任何请求，字体库里只有默认字体（它不需要下载）。"""
    store = FontStore(Settings(_env_file=None, data_dir=tmp_path / "state", font_downloads=False))
    store.attach(objects)
    requested = serve(monkeypatch, lambda request: httpx.Response(500))
    asyncio.run(store.sync())
    assert requested == [] and [font.id for font in store.listing()] == ["noto-sans-cjk"]


def test_upload_registers_a_user_font_in_the_object_store(store, objects) -> None:
    """上传 .ttf：读出家族名并登记（来源 upload、上传设备），字体与预览条都进对象存储，本机立即可用；预览条只保留透明度。"""
    font = store.upload(minimal_font("Zed Display"), strip_png(), "我的字体", "device-1")
    assert (font.family, font.label, font.source, font.preview) == ("Zed Display", "我的字体", "upload", True)
    assert next(record for record in font_store.listing() if record["id"] == font.id)["device_id"] == "device-1"
    assert (objects.root / font.object_key).exists() and (objects.root / font.preview_key).exists()
    assert store.path(font.id) is not None and [item.family for item in store.available()] == ["Noto Sans CJK SC", "Zed Display"]
    with Image.open(store.preview_path(font.id)) as preview:
        assert preview.mode == "RGBA" and preview.getpixel((20, 20))[:3] == (0, 0, 0)


def test_upload_refuses_unusable_fonts(store) -> None:
    """拒绝：不是 TrueType/OpenType、空文件、家族名与已有字体（含大小写不同）或保留名重复、超过大小上限、预览条不是 PNG、显示名过长。"""
    store.upload(minimal_font("Zed Display"), None, "", "device-1")
    for content, message in [
        (b"wOFF" + minimal_font("Other")[4:], "TrueType"),
        (b"", "上限"),
        (minimal_font("zed display"), "已存在"),
        (minimal_font("Noto Sans CJK SC"), "已存在"),
        (minimal_font("serif"), "已存在"),
    ]:
        with pytest.raises(FontRejected, match=message):
            store.upload(content, None, "", "device-1")
    store.max_upload = 10
    with pytest.raises(FontRejected, match="上限"):
        store.upload(minimal_font("Big Font"), None, "", "device-1")
    store.max_upload = 10_000_000
    with pytest.raises(FontRejected, match="预览图"):
        store.upload(minimal_font("Preview Bad"), b"not a png", "", "device-1")
    with pytest.raises(FontRejected, match="100"):
        store.upload(minimal_font("Long Label"), None, "x" * 101, "device-1")
    assert [font.family for font in store.listing()] == ["Noto Sans CJK SC", "Zed Display"]


def test_the_same_file_is_not_uploaded_twice(store) -> None:
    """同一个文件第二次上传被拒绝（家族名已存在），注册表里仍只有一条。"""
    data = minimal_font("Zed Display")
    store.upload(data, None, "", "device-1")
    with pytest.raises(FontRejected):
        store.upload(data, None, "", "device-2")
    assert len(font_store.listing()) == 2


def test_an_installed_system_family_cannot_be_taken_by_an_upload(store, monkeypatch) -> None:
    """沙箱的系统字体里已有的家族名（如 Liberation Sans）不能被上传占用，否则渲染时用哪个文件不确定。"""
    monkeypatch.setattr(fonts_module, "system_families", lambda: frozenset({"liberation sans"}))
    with pytest.raises(FontRejected, match="已存在"):
        store.upload(minimal_font("Liberation Sans"), None, "", "device-1")


def test_the_agent_guide_is_the_font_library_as_an_enum(store, monkeypatch) -> None:
    """Agent 拿到的是数据库里字体库的枚举：一开始只有默认字体（库不会是空的）；同步预装字体和上传后都出现在枚举里，并带风格与字重说明。"""
    guide = store.guide()
    assert '"enum":["Noto Sans CJK SC"]' in guide and "fontFamily" in guide and "never others" in guide
    serve(monkeypatch, origin)
    asyncio.run(store.sync())
    store.upload(minimal_font("Zed Display"), None, "", "device-1")
    guide = store.guide()
    assert '"enum":["Noto Sans CJK SC", "Alpha Sans", "Beta Serif", "Zed Display"]' in guide
    assert '"Beta Serif" (Beta Serif; fixture style; weight 200 900)' in guide


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


def font_sprite():
    """已发布的字效，fontFamily 是普通字符串参数（生成时的枚举已换成 maxLength）。"""
    from generated.imv.sprite.v1 import sprite_pb2 as sprite_pb

    sprite = published_sprite()
    schema = json.loads(sprite.config_schema_json)
    schema["properties"]["fontFamily"] = {"type": "string", "maxLength": 100}
    defaults = json.loads(sprite.default_config_json) | {"fontFamily": "Noto Sans CJK SC"}
    sprite.config_schema_json, sprite.default_config_json = json.dumps(schema), json.dumps(defaults)
    only = sprite_pb.ScalarValue(string_value="Noto Sans CJK SC")
    sprite.parameters.add(key="fontFamily", label="字体", access=sprite_pb.OPERATOR_ACCESS_VISIBLE_EDITABLE, default_value=only, max_length=100)
    return sprite


def font_editor(family: str) -> RemotionTrackEditor:
    """一次摆放把 fontFamily 覆盖成 family。"""
    return RemotionTrackEditor.model_validate({"parameters": [{"key": "fontFamily", "stringValue": family}]})


def test_a_font_added_after_publication_can_be_chosen_for_an_older_sprite(store) -> None:
    """字体库在 Sprite 发布之后还会增加（用户上传）：摆放覆盖按实时字体库校验，所以新上传的字体可以用在已发布的字效上；不在库里的名字仍被拒绝。"""
    store.upload(minimal_font("Zed Display"), None, "", "device-1")
    config, _ = resolve(font_sprite(), font_editor("Zed Display"))
    assert config["fontFamily"] == "Zed Display"
    with pytest.raises(ValueError, match="不是字体库里的字体"):
        resolve(font_sprite(), font_editor("Ghost Font"))


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
    serve(monkeypatch, origin)
    asyncio.run(store.sync())
    (store.files / "beta.ttf").unlink()
    store.refresh()
    assert "beta" not in store.ready
    asyncio.run(require_fonts(clip('fontFamily: "Default Only"')))
    asyncio.run(require_fonts(clip('fontFamily: "Beta Serif, serif"')))
    assert "beta" in store.ready
    monkeypatch.setattr(FontStore, "active", None)
    asyncio.run(require_fonts(clip('fontFamily: "Beta Serif"')))


def test_a_render_is_refused_when_a_named_font_cannot_be_obtained(store, objects, monkeypatch) -> None:
    """字体取不回来（对象存储里没有）时，点名该字体的渲染以稳定错误码 font_unavailable 失败，不会悄悄用默认字体渲染。"""
    serve(monkeypatch, origin)
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


def test_the_font_api_lists_serves_and_uploads(client: TestClient, served, monkeypatch) -> None:
    """字体接口：列表给出家族名、风格、字重、文件与预览地址；文件带 CORS 可被预览 iframe 加载；上传（multipart）返回新字体且立即出现在列表；
    重名返回 422，超大返回 413，缺少设备头返回 422；未知字体 404。"""
    serve(monkeypatch, origin)
    asyncio.run(served.sync())
    listed = client.get("/api/templates/fonts").json()
    assert [item["family"] for item in listed] == ["Noto Sans CJK SC", "Alpha Sans", "Beta Serif"]
    assert listed[0]["source"] == "system" and listed[0]["file_url"] is None
    assert listed[2]["weights"] == [200, 900] and listed[1]["file_url"] == "/api/templates/fonts/alpha"
    assert client.get("/api/templates/fonts/noto-sans-cjk").status_code == 404
    file = client.get("/api/templates/fonts/alpha")
    assert (file.status_code, file.content) == (200, PAYLOADS["alpha"])
    assert file.headers["access-control-allow-origin"] == "*" and file.headers["content-type"] == "font/ttf"
    assert client.get("/api/templates/fonts/unknown").status_code == 404
    upload = client.post(
        "/api/templates/fonts", headers=DEVICE_HEADERS, data={"label": "手写体"},
        files={"file": ("zed.ttf", minimal_font("Zed Display"), "font/ttf"), "preview": ("p.png", strip_png(), "image/png")},
    )
    created = upload.json()
    assert upload.status_code == 201 and created["family"] == "Zed Display" and created["source"] == "upload" and created["label"] == "手写体"
    assert client.get(created["preview_url"]).headers["content-type"] == "image/png"
    assert client.get(created["file_url"]).content == minimal_font("Zed Display")
    assert [item["family"] for item in client.get("/api/templates/fonts").json()][-1] == "Zed Display"
    duplicate = client.post("/api/templates/fonts", headers=DEVICE_HEADERS, files={"file": ("again.ttf", minimal_font("zed display"), "font/ttf")})
    assert duplicate.status_code == 422 and "已存在" in duplicate.json()["detail"]
    served.max_upload = 100
    assert client.post("/api/templates/fonts", headers=DEVICE_HEADERS, files={"file": ("big.ttf", minimal_font("Big One"), "font/ttf")}).status_code == 413
    assert client.post("/api/templates/fonts", files={"file": ("x.ttf", b"x", "font/ttf")}).status_code == 422


def test_the_preview_page_lists_fonts_for_lazy_registration() -> None:
    """预览页以 JSON 元素列出有文件的字体（名称、CSS 字重、绝对路径 URL）供播放器注册，系统默认字体不列（预览本来就自带）；没有字体时不输出该元素。"""
    font = spec("beta", "Beta Serif", weights=[200, 900]).font()
    system = font.model_copy(update={"id": "noto-sans-cjk", "family": "Noto Sans CJK SC", "source": "system"})
    page = routes.player_page("void 0", fonts=[system, font]).body.decode()
    data = page.split('id="imv-preview-fonts">', 1)[1].split("</script>", 1)[0]
    assert json.loads(data) == [{"family": "Beta Serif", "weight": "200 900", "url": "/api/templates/fonts/beta"}]
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
export default function Probe(props: { family: string }) {
  return <div data-probe style={{ position: "absolute", left: 40, top: 40, fontSize: 80, whiteSpace: "nowrap", fontFamily: props.family }}>测试字体效果 Abc</div>;
}
"""
WIDTH_TEST = 'export default async function (ctx) { const found = await ctx.query("[data-probe]"); ctx.assert(true, String(Math.round(found.box.width))); }'


@needs_browser
def test_an_uploaded_font_is_used_by_the_sandboxed_browser(tmp_path, template_db, objects) -> None:
    """真实沙箱里的 Chrome 按字体名使用上传的字体：一个只存在于字体库的自制字体（每个字正好一个字宽）排出的文字宽度与默认字体不同；
    字体名不存在时回落为默认字体的宽度。"""
    from server.remotion_templates.tool_validation import ToolValidator
    from server.remotion_templates.tools.contracts import ComponentDefinition, RenderValidationInput, TestScript

    settings = Settings(
        _env_file=None, data_dir=tmp_path / "state", font_downloads=False, browser_executable=Path(BROWSER),
        font_regular=Path(SYSTEM_FONT), font_bold=Path(SYSTEM_FONT),
    )
    fonts = FontStore.for_settings(settings)
    fonts.attach(objects)
    fonts.refresh()
    fonts.upload(minimal_font("Imv Probe Sans"), None, "", "device-1")

    def width(family: str) -> int:
        """Mount the probe with one CSS family list and read the measured text width."""
        component = ComponentDefinition(
            code=FAMILY_PROBE,
            parameter_schema={"type": "object", "properties": {"family": {"type": "string"}}, "required": ["family"], "additionalProperties": False},
            default_parameters={"family": family},
        )
        request = RenderValidationInput(component=component, duration_frames=5, tests=[TestScript(name="width", code=WIDTH_TEST)])
        report = asyncio.run(ToolValidator(Renderer(settings), tmp_path).validate_render(request))
        assert report.passed, report
        return int(report.tests[0].assertions[0].message)

    default = width('"Noto Sans CJK SC", sans-serif')
    assert width('"Missing Family Zzz", "Noto Sans CJK SC", sans-serif') == default
    assert width('"Imv Probe Sans", "Noto Sans CJK SC", sans-serif') == 800
