"""Remotion 字效保存为 Sprite 资产：发布、目录与预览经存储、本地对象目录与 HTTP 的回归。

不访问模型、浏览器、ZOS 或真实 MySQL（作品与发布记录使用 conftest 的临时 SQLite 替身，未配置 ZOS 时对象
落在数据目录的 objects/）；发布版本由离线渲染替身落盘。
真实 MySQL 的建表、排序规则与唯一键见 test_mysql_integration.py。
在 server/ 目录执行 `uv run --locked pytest tests/test_remotion_sprite_publish.py -v`。
"""

import asyncio
import hashlib
import json
import logging
import shutil
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from generated.imv.sprite.v1 import sprite_pb2 as pb
from sqlalchemy import Engine, select, update

from server.app import app
from server.remotion_templates import sprite_store, sprites
from server.remotion_templates.categories import HEX_COLOR, KEYWORD_COLOR
from server.remotion_templates.harness import Harness
from server.remotion_templates.models import GenerateTemplateRequest
from server.remotion_templates.object_store import ObjectStoreError
from server.remotion_templates.provider import Budget
from server.remotion_templates.publication import PREVIEW_PROTOCOL, current_bundle
from server.remotion_templates.settings import Settings
from server.remotion_templates.sprite_router import sprite_runtime
from server.remotion_templates.store import Store
from server.remotion_templates.tables import versions
from server.remotion_templates.tools.contracts import SpriteDraft

from .conftest import DEVICE, DEVICE_HEADERS, seed_sprite
from .test_remotion_version_diagnostics import (
    CodeOnlyRenderer,
    OfflineRenderer,
    _SavedSprite,
    runtime_report,
)

PROTOBUF = {"Content-Type": "application/x-protobuf"}
COLOR = {"type": "string", "pattern": HEX_COLOR}
# 一个字效版本的顶层参数：字效必备参数、关键词参数，再加视频特效必备的 speed，便于同一版本按多种类型发布。
SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "minLength": 1, "maxLength": 100, "title": "标题文字"},
        "keywords": {"type": "array", "items": {"type": "string", "maxLength": 100}, "maxItems": 20, "title": "关键词"},
        "keywordBold": {"type": "boolean", "title": "关键词加粗"},
        "keywordItalic": {"type": "boolean", "title": "关键词斜体"},
        "keywordUnderline": {"type": "boolean", "title": "关键词下划线"},
        "keywordStrikeout": {"type": "boolean", "title": "关键词删除线"},
        "keywordColor": {"type": "string", "pattern": KEYWORD_COLOR, "title": "关键词颜色"},
        "keywordSize": {"type": "integer", "minimum": 0, "maximum": 300, "title": "关键词字号"},
        "fontSize": {"type": "number", "minimum": 24, "maximum": 240, "title": "字号"},
        "fontWeight": {"type": "integer", "enum": [400, 700], "title": "字重"},
        "color": {**COLOR, "title": "文字颜色"},
        "speed": {"type": "number", "minimum": 0.25, "maximum": 4, "title": "速度"},
        "shadow": {"type": "boolean", "title": "阴影"},
        "title": {"type": "object", "properties": {"glow": {"type": "number"}}, "additionalProperties": False},
    },
    "required": ["text", "keywords", "keywordBold", "keywordItalic", "keywordUnderline", "keywordStrikeout", "keywordColor", "keywordSize", "fontSize", "fontWeight", "color", "speed", "shadow"],
    "additionalProperties": False,
}
DEFAULTS = {"text": "标题", "keywords": [], "keywordBold": False, "keywordItalic": False, "keywordUnderline": False, "keywordStrikeout": False, "keywordColor": "", "keywordSize": 0, "fontSize": 64, "fontWeight": 400, "color": "#FFFFFF", "speed": 1, "shadow": False, "title": {"glow": 0.5}}
BOUND = ["text", "keywords", "keywordBold", "keywordItalic", "keywordUnderline", "keywordStrikeout", "keywordColor", "keywordSize", "fontSize", "fontWeight", "color", "speed", "shadow"]
CODE = 'import React from "react";\nexport default function Sprite(p: {text: string}) { return <div>{p.text}</div>; }\n'
# 内容区域：唯一实例的布局，宿主变换绕它的中心。
LAYOUT = {"x": 90, "y": 760, "width": 900, "height": 400, "z_index": 0}
# 当前宿主构建的预览包以协议行开头。
CURRENT = PREVIEW_PROTOCOL.decode() + "\n"


class BundleRebuilder:
    """记录重建与边界抽样请求的渲染替身：写出带协议行的新预览包、报告各样本通过，或按要求失败；不执行真实浏览器或沙箱。"""

    def __init__(self):
        """默认成功；requests 保存重建请求，samples 保存抽样请求。"""
        self.requests: list[dict] = []
        self.samples: list[dict] = []
        self.failure: Exception | None = None
        self.sample_failure: Exception | None = None
        self.failing_sample: str | None = None

    def worker_browser_path(self):
        """抽样请求记录的浏览器路径；替身不启动浏览器。"""
        return "/usr/bin/chromium"

    async def run_worker(self, directory, *, worker=None, timeout_seconds=None):
        """预览包构建写出新包；边界抽样逐个报告样本结果。失败时不写任何文件。"""
        request = json.loads((directory / "request.json").read_text())
        if worker == "tool-validation-worker.mjs":
            assert request["mode"] == "samples"
            self.samples.append(request)
            if self.sample_failure:
                raise self.sample_failure
            return {"passed": True, "checks": [
                {"name": name, "status": "pass"} for name in ("source_policy", "export_source", "typescript")
            ] + [
                {"name": f"sample:{item['name']}", "status": "failed" if item["name"] == self.failing_sample else "passed", "message": "frame 15: boom"}
                for item in request["samples"]
            ]}
        assert worker == "presentation-worker.mjs"
        self.requests.append(request)
        if self.failure:
            raise self.failure
        (directory / "interactive.js").write_text(CURRENT + "// rebuilt")
        return {}


@pytest.fixture
def published(tmp_path, template_db):
    """发布一个带分类与顶层参数的字效版本，并让主应用的 Sprite 路由使用同一份临时数据；发布记录写入共享库替身。"""
    yield from published_version(tmp_path, SCHEMA, DEFAULTS, "text", BOUND)


@pytest.fixture
def legacy_published(tmp_path, template_db):
    """本次改动之前组合的版本：没有效果分类，也没有顶层参数绑定。"""
    yield from published_version(tmp_path, SCHEMA, DEFAULTS, None, [])


def published_version(tmp_path, schema, defaults, kind, bound):
    """用离线渲染替身封存一个成功版本（``kind`` 为空表示旧版组合），并在使用结束后恢复路由依赖。"""
    font = tmp_path / "font.ttc"
    font.write_bytes(b"fixture-font")
    settings = Settings(_env_file=None, data_dir=tmp_path / "state", font_regular=font, font_bold=font)
    harness = Harness(SimpleNamespace(settings=settings), OfflineRenderer(settings, CodeOnlyRenderer(settings)))
    store = Store(settings.data_dir)
    store.initialize()
    sprite = SpriteDraft(
        description="标题字效",
        code=CODE,
        parameter_schema=schema,
        default_parameters=defaults,
        composition={"width": 1080, "height": 1920, "fps": 30, "duration_frames": 30},
        instances=[{
            "instance_id": "title",
            "preset": {"code": CODE, "parameter_schema": schema, "default_parameters": defaults, "description": "标题"},
            "parameters": defaults,
            "layout": LAYOUT,
            "timing": {"start_frame": 0, "duration_frames": 30},
        }],
        bindings=[{"name": name, "title": name, "targets": [{"instance_id": "title", "parameter": name}]} for name in bound],
        **({"kind": kind} if kind else {}),
    )
    _work, job = store.create(GenerateTemplateRequest(description="标题"), DEVICE)
    store.claim()
    result = asyncio.run(harness.finalize_sprite(
        SimpleNamespace(saved_sprite=lambda _id: _SavedSprite(sprite), validation_for=lambda _r: runtime_report(sprite)),
        "saved", Budget(), store.job_dir(job.id),
    ))
    version = store.publish(job.id, *result)
    renderer = BundleRebuilder()
    service = SimpleNamespace(store=store, settings=settings, harness=SimpleNamespace(renderer=renderer), sandbox_problem=lambda: None)
    app.dependency_overrides[sprite_runtime] = lambda: service
    yield SimpleNamespace(store=store, version=version, renderer=renderer, service=service, settings=settings)
    app.dependency_overrides.pop(sprite_runtime)


def objects(store: Store) -> Path:
    """未配置 ZOS 时对象存储所在的本地目录。"""
    return store.root / "objects" / "imv" / "remotion"


def bundle_copy(store: Store, sprite_id: str) -> Path:
    """一个资产的播放器包副本（对象）。"""
    return objects(store) / "sprites" / sprite_id / "interactive.js"


def drop_bundle(store: Store, sprite_id: str) -> None:
    """丢失资产的播放器包副本：对象与本机读取缓存一起删除。"""
    shutil.rmtree(objects(store) / "sprites" / sprite_id)
    shutil.rmtree(store.root / "cache" / "sprites" / sprite_id, ignore_errors=True)


def reseal(published, contents: str) -> None:
    """改写源版本封存的 interactive.js（对象与缓存）并把新哈希写回版本记录，模拟以该包封存的版本。"""
    from server.remotion_templates.evidence import digest

    version_id = str(published.version.id)
    sealed = objects(published.store) / "versions" / version_id / "interactive.js"
    sealed.write_text(contents)
    shutil.rmtree(published.store.root / "cache" / "accepted" / version_id, ignore_errors=True)
    published.version.validation.artifacts["interactive.js"] = digest(sealed)
    with published.store.transaction() as db:
        db.execute(update(versions).where(versions.c.id == version_id).values(data=published.version.model_dump_json()))


def publish_request(version_id, **fields) -> pb.PublishSpriteRequest:
    """默认把版本发布为带关键词字段的文字 Sprite；用例按需覆盖字段。"""
    values = {"kind": pb.SPRITE_KIND_TEXT, "text_prop": "text", "keywords_prop": "keywords", **fields}
    return pb.PublishSpriteRequest(source_version_id=str(version_id), **values)


def publish(client: TestClient, request: pb.PublishSpriteRequest, device: dict = DEVICE_HEADERS):
    """以作品所属设备发送二进制发布请求。"""
    return client.post("/api/sprites/publish", content=request.SerializeToString(), headers={**PROTOBUF, **device})


def published_sprite(client: TestClient, version_id, **fields) -> pb.SpriteSummary:
    """发布成功并返回摘要。"""
    response = publish(client, publish_request(version_id, **fields))
    assert response.status_code == 200
    return pb.PublishSpriteResponse.FromString(response.content).sprite


def test_publish_copies_content_and_lists_summary_without_source(client: TestClient, published) -> None:
    """发布后目录只含摘要：顶层参数（含文字与关键词）都是可编辑控件，实例内部参数不暴露；记录内容区域。"""
    summary = published_sprite(client, published.version.id)
    assert (summary.name, summary.kind, summary.keywords_supported) == ("标题字效", pb.SPRITE_KIND_TEXT, True)
    assert (summary.canvas.width, summary.canvas.height, summary.canvas.fps, summary.canvas.preview_frames) == (1080, 1920, 30, 30)
    assert summary.source_version_id == str(published.version.id)
    assert (summary.content_box.x, summary.content_box.y, summary.content_box.width, summary.content_box.height) == (90, 760, 900, 400)
    parameters = {item.key: item for item in summary.parameters}
    assert list(parameters) == BOUND
    assert (parameters["color"].label, parameters["color"].format) == ("文字颜色", pb.SPRITE_PARAMETER_FORMAT_COLOR)
    assert (parameters["fontSize"].minimum, parameters["fontSize"].maximum) == (24, 240)
    assert [item.number_value for item in parameters["fontWeight"].allowed_values] == [400, 700]
    # number_value cannot tell an integer from a number: the format keeps integer parameters integral.
    assert parameters["fontWeight"].format == pb.SPRITE_PARAMETER_FORMAT_INTEGER
    assert parameters["fontSize"].format == pb.SPRITE_PARAMETER_FORMAT_UNSPECIFIED
    assert (parameters["text"].default_value.string_value, parameters["text"].min_length, parameters["text"].max_length) == ("标题", 1, 100)
    keywords = parameters["keywords"]
    assert keywords.default_value.HasField("string_list_value") and list(keywords.default_value.string_list_value.values) == []
    assert (keywords.max_items, keywords.max_length) == (20, 100)
    assert parameters["shadow"].default_value.bool_value is False and parameters["shadow"].default_value.HasField("bool_value")
    assert all(item.access == pb.OPERATOR_ACCESS_VISIBLE_EDITABLE for item in parameters.values())

    listed = client.get("/api/sprites")
    assert listed.status_code == 200
    assert listed.headers["content-type"] == "application/x-protobuf"
    assert [item.sprite_id for item in pb.ListSpritesResponse.FromString(listed.content).sprites] == [summary.sprite_id]
    assert b"export default" not in listed.content
    assert bundle_copy(published.store, summary.sprite_id).is_file()


def test_publications_are_rows_of_the_shared_database_with_an_object_bundle(client: TestClient, published, template_db: Engine) -> None:
    """发布记录是共享库 remotion_sprites 里的一行（标识列与 JSON）；预览包是对象存储里的副本，数据目录不再有 sprites/<id> 文件夹。"""
    summary = published_sprite(client, published.version.id)
    with template_db.connect() as connection:
        row = connection.execute(select(sprite_store.sprites)).one()
    assert (row.sprite_id, row.source_version_id, row.kind, row.text_prop, row.keywords_prop) == (
        summary.sprite_id, str(published.version.id), pb.SPRITE_KIND_TEXT, "text", "keywords",
    )
    assert row.data["tsx_code"] == CODE and row.data["sprite_id"] == summary.sprite_id
    assert not (published.store.root / "sprites" / summary.sprite_id).exists()
    assert bundle_copy(published.store, summary.sprite_id).is_file()


def test_catalog_lists_the_newest_publication_first(client: TestClient, published) -> None:
    """目录按发布时间倒序：排序在应用里完成（MySQL 对带整个 JSON 的行排序可能超出排序缓冲区）。"""
    first = published_sprite(client, published.version.id)
    second = published_sprite(client, published.version.id, kind=pb.SPRITE_KIND_TRANSITION_OVERLAY, text_prop="", keywords_prop="")
    third = published_sprite(client, published.version.id, kind=pb.SPRITE_KIND_VIDEO_OVERLAY, text_prop="", keywords_prop="")
    listed = pb.ListSpritesResponse.FromString(client.get("/api/sprites").content).sprites
    assert [item.sprite_id for item in listed] == [third.sprite_id, second.sprite_id, first.sprite_id]


def test_republishing_returns_the_original_sprite(client: TestClient, published) -> None:
    """相同来源与字段选择重复发布幂等；改选分类（不同字段选择）产生独立资产。"""
    first = published_sprite(client, published.version.id)
    again = published_sprite(client, published.version.id)
    other = published_sprite(client, published.version.id, kind=pb.SPRITE_KIND_VIDEO_OVERLAY, text_prop="", keywords_prop="")
    assert again.sprite_id == first.sprite_id and first.keywords_supported
    assert other.sprite_id != first.sprite_id and not other.keywords_supported
    assert len(pb.ListSpritesResponse.FromString(client.get("/api/sprites").content).sprites) == 2


def test_published_sprite_survives_deleting_its_source_version(client: TestClient, published) -> None:
    """源版本的封存对象、缓存与记录被清理后，目录条目和预览仍来自发布自己的副本。"""
    summary = published_sprite(client, published.version.id)
    shutil.rmtree(objects(published.store) / "versions" / str(published.version.id))
    shutil.rmtree(published.store.root / "cache" / "accepted" / str(published.version.id))
    with published.store.transaction() as db:
        db.execute(versions.delete())
    assert pb.ListSpritesResponse.FromString(client.get("/api/sprites").content).sprites[0].sprite_id == summary.sprite_id
    preview = client.get(summary.preview_url)
    assert preview.status_code == 200
    assert "// rebuilt" in preview.text
    assert "sandbox allow-scripts" in preview.headers["content-security-policy"]


def test_losing_a_publish_race_returns_the_winner_and_cleans_up(published) -> None:
    """并发的相同发布在写库时撞上唯一键：落败者返回胜者的资产，并删除自己刚上传的副本与暂存文件。"""
    request = publish_request(published.version.id)
    first = sprites._create(published.store, request, published.version, b"bundle")
    second = sprites._create(published.store, request, published.version, b"bundle")
    assert second.sprite_id == first.sprite_id
    assert [item.name for item in (objects(published.store) / "sprites").iterdir()] == [first.sprite_id]
    assert list((published.store.root / "sprites").iterdir()) == []


def test_field_names_longer_than_the_column_are_a_422(published) -> None:
    """text_prop 与 keywords_prop 受列宽限制，在写库之前以 422 拒绝。"""
    name = "n" * (sprite_store.NAME_LENGTH + 1)
    with pytest.raises(HTTPException) as caught:
        sprites._validated_request(pb.PublishSpriteRequest(kind=pb.SPRITE_KIND_TEXT, text_prop=name), published.version)
    assert caught.value.status_code == 422 and str(sprite_store.NAME_LENGTH) in caught.value.detail


def test_a_lost_preview_bundle_is_rebuilt_from_the_published_source(client: TestClient, published, template_db: Engine) -> None:
    """副本丢失（换主机、对象被清理）时预览按发布记录里的源码、默认参数与画布重建，保存新副本并记录新哈希；源版本被删也不影响。"""
    summary = published_sprite(client, published.version.id)
    drop_bundle(published.store, summary.sprite_id)
    published.renderer.requests.clear()
    shutil.rmtree(objects(published.store) / "versions" / str(published.version.id))
    preview = client.get(summary.preview_url)
    assert preview.status_code == 200 and "// rebuilt" in preview.text
    [request] = published.renderer.requests
    assert (request["code"], request["config"]) == (CODE, DEFAULTS)
    assert request["composition"] == {"width": 1080, "height": 1920, "fps": 30, "duration_in_frames": 30}
    assert current_bundle(bundle_copy(published.store, summary.sprite_id).read_bytes())
    with template_db.connect() as connection:
        data = connection.execute(select(sprite_store.sprites.c.data)).scalar_one()
    from server.remotion_templates.evidence import digest
    assert data["preview_sha256"] == digest(bundle_copy(published.store, summary.sprite_id))
    # The rebuilt copy is reused: a second preview starts no worker.
    assert "// rebuilt" in client.get(summary.preview_url).text and len(published.renderer.requests) == 1


def test_preview_is_unavailable_when_a_lost_bundle_cannot_be_rebuilt(client: TestClient, published) -> None:
    """副本丢失且重建失败（例如没有隔离环境）时预览 404，目录仍列出该资产（叠加渲染只用 TSX，不受影响）。"""
    summary = published_sprite(client, published.version.id)
    drop_bundle(published.store, summary.sprite_id)
    published.renderer.failure = RuntimeError("sandbox unavailable")
    assert client.get(summary.preview_url).status_code == 404
    assert [item.sprite_id for item in pb.ListSpritesResponse.FromString(client.get("/api/sprites").content).sprites] == [summary.sprite_id]


def slow_rebuilds(published) -> None:
    """让重建在事件循环上停留片刻，使并发的预览或删除必然在它完成前到达。"""
    run_worker = published.renderer.run_worker

    async def slow_worker(*args, **kwargs):
        """延迟后照常重建。"""
        await asyncio.sleep(0.05)
        return await run_worker(*args, **kwargs)

    published.renderer.run_worker = slow_worker


def test_concurrent_previews_of_a_lost_bundle_rebuild_it_once(client: TestClient, published) -> None:
    """资产库同时打开多个预览时，丢失或哈希不符的副本只重建一次，其余请求读取重建结果。"""
    summary = published_sprite(client, published.version.id)
    bundle_copy(published.store, summary.sprite_id).write_text("tampered")
    shutil.rmtree(published.store.root / "cache" / "sprites" / summary.sprite_id, ignore_errors=True)
    published.renderer.requests.clear()
    slow_rebuilds(published)

    async def together():
        """在同一事件循环里同时预览三次。"""
        return await asyncio.gather(*(sprites.preview_script(published.service, summary.sprite_id) for _ in range(3)))

    scripts = asyncio.run(together())
    assert all("// rebuilt" in script for script, _ in scripts)
    assert len(published.renderer.requests) == 1


def test_a_failed_rebuild_is_not_retried_by_every_preview(client: TestClient, published, monkeypatch) -> None:
    """重建失败后在冷却时间内预览直接 404、不再启动隔离 worker；冷却过后再次尝试。"""
    summary = published_sprite(client, published.version.id)
    drop_bundle(published.store, summary.sprite_id)
    published.renderer.requests.clear()
    published.renderer.failure = RuntimeError("sandbox unavailable")
    assert client.get(summary.preview_url).status_code == 404
    assert client.get(summary.preview_url).status_code == 404
    assert len(published.renderer.requests) == 1
    published.renderer.failure = None
    monkeypatch.setattr(sprites, "REBUILD_COOLDOWN", 0.0)
    assert "// rebuilt" in client.get(summary.preview_url).text
    assert len(published.renderer.requests) == 2


def test_deleting_during_a_rebuild_leaves_no_bundle_behind(client: TestClient, published) -> None:
    """删除等待进行中的重建结束再删除副本，重建不会在删除之后把公开的预览包写回。"""
    from server.remotion_templates.sprite_router import delete_sprite

    summary = published_sprite(client, published.version.id)
    drop_bundle(published.store, summary.sprite_id)
    slow_rebuilds(published)

    async def preview_then_delete():
        """预览开始重建后立即删除资产。"""
        preview = asyncio.create_task(sprites.preview_script(published.service, summary.sprite_id))
        await asyncio.sleep(0.01)
        await delete_sprite(summary.sprite_id, published.service)
        await preview

    asyncio.run(preview_then_delete())
    assert not (objects(published.store) / "sprites" / summary.sprite_id).exists()


def test_an_unreachable_object_store_is_a_503_without_a_rebuild(client: TestClient, published, monkeypatch) -> None:
    """对象存储暂时不可达不等于副本丢失：预览回答 503，不启动重建。"""
    summary = published_sprite(client, published.version.id)
    shutil.rmtree(published.store.root / "cache" / "sprites" / summary.sprite_id, ignore_errors=True)
    published.renderer.requests.clear()

    def unreachable(key, destination):
        """模拟 ZOS 不可达。"""
        raise ObjectStoreError("unreachable")

    monkeypatch.setattr(published.store.objects, "fetch", unreachable)
    assert client.get(summary.preview_url).status_code == 503
    assert published.renderer.requests == []


@pytest.mark.parametrize(("fields", "detail"), [
    ({"kind": pb.SPRITE_KIND_UNSPECIFIED}, "kind 必须是"),
    ({"text_prop": "color"}, "text_prop 必须是 text"),
    ({"keywords_prop": "text"}, "keywords_prop 在该版本声明了关键词参数时必须为 keywords"),
    ({"kind": pb.SPRITE_KIND_VIDEO_OVERLAY}, "只有字效可以声明"),
    ({"kind": pb.SPRITE_KIND_FILTER_OVERLAY, "text_prop": "", "keywords_prop": ""}, "intensity（强度，0～1 的数字）；tintColor（色调，十六进制颜色）"),
], ids=["no-kind", "other-text", "other-keywords", "visual-with-text-fields", "filter-without-its-parameters"])
def test_publish_rejects_invalid_field_choices(client: TestClient, published, fields, detail) -> None:
    """类型必须明确；字效的文字与关键词字段名固定；改选的分类缺少必备参数时说明缺什么，并提示回到聊天补充。"""
    response = publish(client, publish_request(published.version.id, **fields))
    assert response.status_code == 422 and detail in response.json()["detail"]
    assert client.get("/api/sprites").content == b""


@pytest.mark.parametrize("limit", [6, 101])
def test_publish_requires_text_max_length_100(published, limit) -> None:
    """发布同样拒绝非 100 的文字上限，防止旧成功版本绕过生成入口。"""
    version = published.version.model_copy(deep=True)
    version.candidate.config_schema["properties"]["text"]["maxLength"] = limit
    with pytest.raises(HTTPException) as caught:
        sprites._validated_request(publish_request(version.id), version)
    assert caught.value.status_code == 422 and "maxLength 必须为 100" in caught.value.detail


def test_a_text_version_without_its_keyword_parameters_is_refused(published) -> None:
    """字效必须声明关键词参数：缺少 keywords 绑定的版本在发布时被拒绝并点名缺少的参数。"""
    version = published.version.model_copy(deep=True)
    version.spec.sprite.bindings = [item for item in version.spec.sprite.bindings if item.name != "keywords"]
    with pytest.raises(HTTPException) as caught:
        sprites._validated_request(publish_request(version.id), version)
    assert caught.value.status_code == 422 and "keywords" in caught.value.detail


def test_a_text_version_whose_keyword_options_default_to_on_is_refused(published) -> None:
    """审计复现：关键词默认命中「标题」、样式开启、红色 80 号的版本曾能发布；现在 422 点名每个非关闭默认值。"""
    version = published.version.model_copy(deep=True)
    version.candidate.default_config.update(
        keywords=["标题"], keywordBold=True, keywordItalic=True, keywordUnderline=True, keywordStrikeout=True, keywordColor="#FF0000", keywordSize=80,
    )
    with pytest.raises(HTTPException) as caught:
        sprites._validated_request(publish_request(version.id), version)
    assert caught.value.status_code == 422
    # 审计复现：这条 422 曾说成「缺少必备参数」；现在说明默认值必须关闭，并指向参数面板而不是聊天。
    detail = caught.value.detail
    assert detail.startswith("关键词参数的默认值必须是关闭状态") and "缺少" not in detail and "参数面板" in detail
    assert all(item in detail for item in ("keywords（应为 []）", "keywordBold（应为 false）", 'keywordColor（应为 ""）', "keywordSize（应为 0）"))


def test_a_text_version_must_publish_its_keywords_field(published) -> None:
    """审计复现：已绑定 keywords 的字效曾能以 keywords_prop="" 再发布一份 keywords_supported=false 的资产；现在必须是 keywords。"""
    with pytest.raises(HTTPException) as caught:
        sprites._validated_request(publish_request(published.version.id, keywords_prop=""), published.version)
    assert caught.value.status_code == 422 and "keywords_prop" in caught.value.detail


def test_a_version_composed_before_categories_must_be_edited_first(client: TestClient, legacy_published) -> None:
    """旧版组合没有分类和参数绑定：不能直接保存到资产，提示在聊天中改出新版本。"""
    response = publish(client, publish_request(legacy_published.version.id))
    assert response.status_code == 422 and "请在聊天中继续修改" in response.json()["detail"]


def test_publications_without_a_content_box_transform_around_the_whole_canvas() -> None:
    """本次改动之前的发布记录没有内容区域：摘要按整块画布给出，模板编辑与渲染据此计算默认位置。"""
    summary = sprites.summarize(pb.PublishedSprite(sprite_id="s", canvas=pb.SpriteCanvas(width=1080, height=1920, fps=30, preview_frames=30)))
    assert (summary.content_box.x, summary.content_box.y, summary.content_box.width, summary.content_box.height) == (0, 0, 1080, 1920)


def test_publish_rejects_unknown_malformed_or_tampered_sources(client: TestClient, published) -> None:
    """未知或格式错误的版本、被改写的封存产物都不能发布。"""
    assert publish(client, publish_request("00000000-0000-4000-8000-000000000000")).status_code == 404
    assert publish(client, publish_request("not-a-uuid")).status_code == 422
    version_id = str(published.version.id)
    for path in (objects(published.store) / "versions" / version_id, published.store.root / "cache" / "accepted" / version_id):
        (path / "interactive.js").write_text("tampered")
    assert publish(client, publish_request(published.version.id)).status_code == 409
    assert client.post("/api/sprites/publish", json={}, headers=DEVICE_HEADERS).status_code == 415
    assert client.post("/api/sprites/publish", content=b"\xff\xff", headers={**PROTOBUF, **DEVICE_HEADERS}).status_code == 400


def test_another_device_cannot_publish_a_version_it_does_not_own(client: TestClient, published) -> None:
    """版本属于创建作品的设备：其他设备发布返回 404，不带设备请求头返回 422，均不保存资产。"""
    other = {"X-IMV-Device": str(uuid4())}
    assert publish(client, publish_request(published.version.id), other).status_code == 404
    assert client.post("/api/sprites/publish", content=publish_request(published.version.id).SerializeToString(), headers=PROTOBUF).status_code == 422
    assert client.get("/api/sprites").content == b""


def test_style_binding_routes_are_gone(client: TestClient) -> None:
    """片段随云端模板的 remotion 轨道保存，没有单独的绑定接口：旧的读取与保存路径不再存在。"""
    style_id = uuid4()
    assert client.get(f"/api/sprites/styles/{style_id}").status_code == 404
    assert client.post(f"/api/sprites/styles/{style_id}", content=b"", headers=PROTOBUF).status_code == 404


def test_preview_page_lifts_the_clipping_of_composed_instance_boxes(client: TestClient, published) -> None:
    """较早封存的组合 Sprite 的实例盒子带 overflow hidden：预览页（透明叠加与普通预览）都带覆盖规则，大字号下关键词与下划线不被盒子裁掉。

    Issue #142：覆盖只针对 data-imv-instance 的父元素，内容自己的固定框不受影响。
    """
    summary = published_sprite(client, published.version.id)
    rule = "div:has(> [data-imv-instance]){overflow:visible !important}"
    assert rule in client.get(summary.preview_url).text
    assert rule in client.get(summary.preview_url, params={"overlay": "true"}).text


def test_overlay_preview_is_transparent_and_serves_managed_fonts(client: TestClient, published) -> None:
    """模板编辑叠加使用透明背景页；普通预览保留检查棋盘格。字体路由随页面相对路径可用，缺失资产或字重返回 404。"""
    summary = published_sprite(client, published.version.id)
    plain = client.get(summary.preview_url)
    overlay = client.get(summary.preview_url, params={"overlay": "true"})
    assert "conic-gradient" in plain.text and "conic-gradient" not in overlay.text
    assert "background:transparent" in overlay.text
    assert "sandbox allow-scripts" in overlay.headers["content-security-policy"]
    font = client.get(f"/api/sprites/{summary.sprite_id}/fonts/400")
    assert (font.status_code, font.content) == (200, b"fixture-font")
    assert font.headers["access-control-allow-origin"] == "*"
    assert client.get(f"/api/sprites/{summary.sprite_id}/fonts/500").status_code == 404
    assert client.get("/api/sprites/00000000-0000-4000-8000-000000000000/fonts/400").status_code == 404


def test_publish_hashes_and_copies_off_the_event_loop(client: TestClient, published, monkeypatch) -> None:
    """发布要哈希全部封存产物并复制播放器包，必须在工作线程执行，不能占住事件循环（SSE 与取消都依赖它）。"""
    from server.remotion_templates import store as store_module

    where = []
    verify = store_module.verify_artifacts

    def probe(*args, **kwargs):
        """工作线程里没有运行中的事件循环，事件循环线程里有；记录所在位置后照常校验。"""
        try:
            asyncio.get_running_loop()
            where.append("event loop")
        except RuntimeError:
            where.append("worker thread")
        return verify(*args, **kwargs)

    monkeypatch.setattr(store_module, "verify_artifacts", probe)
    published_sprite(client, published.version.id)
    assert where == ["worker thread"]


def test_catalog_skips_unreadable_records_and_keeps_the_rest(client: TestClient, published, template_db: Engine, caplog) -> None:
    """被改写或形状损坏的发布记录只从目录中隔离并记日志，其余资产照常列出；按 ID 访问仍给出明确错误。

    MySQL 的 JSON 列保证内容是合法 JSON，所以损坏表现为形状不对：哈希对不上、字段类型错误或根本不是对象（如一个数字）。
    """
    # One version publishes as three kinds at most, so the fourth record is seeded directly.
    healthy = SimpleNamespace(sprite_id=seed_sprite("0d3c7a51-6b2e-4f19-8a40-5e9c1b7d2f63"))
    tampered = published_sprite(client, published.version.id)
    malformed = published_sprite(client, published.version.id, kind=pb.SPRITE_KIND_TRANSITION_OVERLAY, text_prop="", keywords_prop="")
    not_an_object = published_sprite(client, published.version.id, kind=pb.SPRITE_KIND_VIDEO_OVERLAY, text_prop="", keywords_prop="")
    table = sprite_store.sprites
    with template_db.begin() as connection:
        data = connection.execute(select(table.c.data).where(table.c.sprite_id == tampered.sprite_id)).scalar_one()
        for sprite_id, damaged in (
            (tampered.sprite_id, {**data, "tsx_code": data["tsx_code"] + "// edited"}),
            (malformed.sprite_id, {"sprite_id": 5}),
            (not_an_object.sprite_id, 5),
        ):
            connection.execute(update(table).where(table.c.sprite_id == sprite_id).values(data=damaged))
    with caplog.at_level(logging.WARNING):
        listed = client.get("/api/sprites")
    assert listed.status_code == 200
    assert [item.sprite_id for item in pb.ListSpritesResponse.FromString(listed.content).sprites] == [healthy.sprite_id]
    assert all(broken.sprite_id in caplog.text for broken in (tampered, malformed, not_an_object))
    assert client.get(tampered.preview_url).status_code == 409


def test_stale_player_bundle_is_rebuilt_from_sealed_source(client: TestClient, published) -> None:
    """封存包缺少同步支持时发布会用封存的源码、默认参数和画布重建；原版本目录不被改动。"""
    summary = published_sprite(client, published.version.id)
    request = published.renderer.requests[0]
    assert request["code"] == published.version.candidate.tsx_code
    assert request["config"] == published.version.candidate.default_config
    assert request["composition"]["duration_in_frames"] == 30
    assert current_bundle(bundle_copy(published.store, summary.sprite_id).read_bytes())
    assert b"offline Player fixture" in (objects(published.store) / "versions" / str(published.version.id) / "interactive.js").read_bytes()
    assert not [item for item in (published.store.root / "sprites").iterdir() if item.name.startswith(".build-")]


def test_current_sealed_bundle_is_copied_without_rebuilding(client: TestClient, published) -> None:
    """封存包声明当前宿主协议时直接复制，不启动任何构建。"""
    reseal(published, CURRENT + "// sealed")
    summary = published_sprite(client, published.version.id)
    assert published.renderer.requests == []
    assert client.get(summary.preview_url).text.count("// sealed") == 1


def test_a_bundle_without_placement_support_is_rebuilt(client: TestClient, published) -> None:
    """可逐帧同步但不认识参数覆盖与宿主变换的旧包同样在发布时重建，即使它的生成代码或默认文字里恰好出现协议字样。

    PR #111 审查复现：曾在整个包里搜索 imv-preview-host，默认文字为该字样的旧同步包被当作当前包直接复制，叠加预览不读页面注入的覆盖。
    """
    reseal(published, '// imv-preview-sync\nvar config={"text":"imv-preview-host ' + PREVIEW_PROTOCOL.decode() + '"};')
    summary = published_sprite(client, published.version.id)
    assert len(published.renderer.requests) == 1
    assert "// rebuilt" in client.get(summary.preview_url).text


def test_preview_page_injects_the_published_defaults_and_content_box(client: TestClient, published) -> None:
    """资产预览页把发布时的默认参数与内容区域写成 JSON 数据块，播放器据此起步；脚本无法借数据提前结束标签。"""
    summary = published_sprite(client, published.version.id)
    page = client.get(summary.preview_url, params={"overlay": "true"}).text
    data = page.split('<script type="application/json" id="imv-preview-host">', 1)[1].split("</script>", 1)[0]
    assert json.loads(data) == {"config": DEFAULTS, "box": {"x": 90, "y": 760, "width": 900, "height": 400}}


def test_page_data_cannot_close_its_script_element() -> None:
    """注入的数据含 </script>、& 与行分隔符时全部转义，解析结果不变。"""
    from server.remotion_templates.routes import player_page

    value = {"config": {"text": "</script><script>alert(1)</script>&\u2028\u2029"}}
    page = player_page("void 0", overlay=True, host=value).body.decode()
    data = page.split('id="imv-preview-host">', 1)[1].split("</script>", 1)[0]
    assert "<" not in data and "&" not in data and "\u2028" not in data
    assert json.loads(data) == value


def test_rebuild_failure_still_saves_and_republishing_refreshes_the_old_copy(client: TestClient, published) -> None:
    """重建失败不阻止保存（仅无法叠加预览）；之后重新保存同一资产会把旧副本刷新为新包，其余内容不变。"""
    published.renderer.failure = RuntimeError("sandbox unavailable")
    first = published_sprite(client, published.version.id)
    assert "offline Player fixture" in client.get(first.preview_url).text
    published.renderer.failure = None
    again = published_sprite(client, published.version.id)
    assert again.sprite_id == first.sprite_id
    assert "// rebuilt" in client.get(first.preview_url).text
    assert len(pb.ListSpritesResponse.FromString(client.get("/api/sprites").content).sprites) == 1


def test_previewing_an_asset_saved_with_an_outdated_player_rebuilds_it_once(client: TestClient, published) -> None:
    """资产保存时播放器包早于宿主变换协议（无协议首行），预览不必等重新保存：第一次预览按发布源码重建并记录哈希，之后复用。

    Issue #142：这样的旧包不回报 host 能力，模板编辑里调整水平、垂直位置时预览画面不动；重建失败时仍提供旧包。
    """
    published.renderer.failure = RuntimeError("sandbox unavailable")
    summary = published_sprite(client, published.version.id)
    published.renderer.requests.clear()
    assert "offline Player fixture" in client.get(summary.preview_url).text
    assert len(published.renderer.requests) == 1
    published.renderer.failure = None
    assert "// rebuilt" in client.get(summary.preview_url).text
    copied = objects(published.store) / "sprites" / summary.sprite_id / "interactive.js"
    assert current_bundle(copied.read_bytes())
    assert "// rebuilt" in client.get(summary.preview_url).text
    assert len(published.renderer.requests) == 2


def test_failed_hash_update_keeps_the_old_player_copy_and_republishing_finishes_the_refresh(client: TestClient, published, template_db: Engine) -> None:
    """刷新旧副本时记录新哈希的数据库更新失败（锁超时、进程中断，应用答复 503）：缓存的旧副本与旧哈希仍一致、预览可用且不留暂存文件；数据库恢复后再次保存完成刷新。"""
    published.renderer.failure = RuntimeError("sandbox unavailable")
    summary = published_sprite(client, published.version.id)
    published.renderer.failure = None
    with template_db.begin() as connection:
        connection.exec_driver_sql("CREATE TRIGGER fail_refresh BEFORE UPDATE ON remotion_sprites BEGIN SELECT RAISE(ABORT, 'database is locked'); END")
    assert publish(client, publish_request(published.version.id)).status_code == 503
    assert "offline Player fixture" in client.get(summary.preview_url).text
    assert [item.name for item in (objects(published.store) / "sprites" / summary.sprite_id).iterdir()] == ["interactive.js"]
    assert list((published.store.root / "sprites").iterdir()) == []
    with template_db.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER fail_refresh")
    assert published_sprite(client, published.version.id).sprite_id == summary.sprite_id
    assert "// rebuilt" in client.get(summary.preview_url).text


def test_concurrent_identical_publishes_build_the_player_once(published) -> None:
    """重复点击或并发请求相同来源与字段选择时轮流执行：只重建一次播放器包，两次都返回同一个资产。"""
    run_worker = published.renderer.run_worker

    async def slow_worker(*args, **kwargs):
        """让首个重建在事件循环上停留片刻，另一个请求此时必然已经检查完毕。"""
        await asyncio.sleep(0.05)
        return await run_worker(*args, **kwargs)

    published.renderer.run_worker = slow_worker
    request = publish_request(published.version.id)

    async def twice():
        """在同一事件循环里同时发起两次发布。"""
        return await asyncio.gather(*(sprites.publish(published.service, request, DEVICE) for _ in range(2)))

    first, second = asyncio.run(twice())
    assert first.sprite_id == second.sprite_id
    assert len(published.renderer.requests) == 1


def test_a_new_publication_renders_its_boundary_samples_once(client: TestClient, published) -> None:
    """新发布先在隔离浏览器里渲染边界样本（默认值、两端取值和宿主变换），重复保存同一资产不再抽样。"""
    first = published_sprite(client, published.version.id)
    [request] = published.renderer.samples
    names = [item["name"] for item in request["samples"]]
    assert names[:3] == ["defaults", "low", "high"]
    assert request["box"] == {"x": 90, "y": 760, "width": 900, "height": 400}
    assert request["composition"] == {"width": 1080, "height": 1920, "fps": 30, "duration_frames": 30}
    low = request["samples"][1]
    assert low["values"]["text"] == "测" * 100 and low["values"]["keywords"] == ["测"] * 20 and low["values"]["color"] == "#000000"
    assert low["transform"] == {"x": 0, "y": 0, "scale": 0.25, "rotation": -180, "opacity": 0}
    assert published_sprite(client, published.version.id).sprite_id == first.sprite_id
    assert len(published.renderer.samples) == 1
    assert not [item for item in (published.store.root / "sprites").iterdir() if item.name.startswith(".samples-")]


def test_a_failed_boundary_sample_only_warns(client: TestClient, published, caplog) -> None:
    """可用性优先：边界样本渲染出错时仍保存资产，样本名和帧号只写服务端日志。"""
    published.renderer.failing_sample = "high"
    with caplog.at_level("WARNING"):
        response = publish(client, publish_request(published.version.id))
    assert response.status_code == 200
    assert "boundary sample high" in caplog.text and "frame 15: boom" in caplog.text
    assert client.get("/api/sprites").content != b""


def test_publishing_needs_the_isolated_validator(client: TestClient, published) -> None:
    """校验环境缺失时先以 503 拒绝；运行中校验器无法启动同样是 503，且不保存资产。"""
    published.service.sandbox_problem = lambda: "bwrap is not installed"
    response = publish(client, publish_request(published.version.id))
    assert response.status_code == 503 and "bwrap" in response.json()["detail"]
    published.service.sandbox_problem = lambda: None
    published.renderer.sample_failure = RuntimeError("worker crashed")
    assert publish(client, publish_request(published.version.id)).status_code == 503
    assert client.get("/api/sprites").content == b""


def test_publications_made_before_categories_can_still_be_re_saved(client: TestClient, legacy_published, monkeypatch) -> None:
    """旧版组合发布过的资产仍能「重新保存到资产」来刷新播放器包：命中已有发布时不再套用新的发布规则，旧的文字字段路径也能命中。

    修复前先做新规则校验，旧版本一律 422，叠加预览里「请重新保存到资产」的提示无法照做，旧资产永远拿不到新的播放器包。
    """
    def bypass(request, version):
        """Stand in for the publish rules before this change, which accepted any field choice."""

    async def no_samples(*args, **kwargs):
        """Publications before this change rendered no boundary samples."""

    with monkeypatch.context() as patch:
        patch.setattr(sprites, "_validated_request", bypass)
        patch.setattr(sprites, "_sample", no_samples)
        visual = published_sprite(client, legacy_published.version.id, kind=pb.SPRITE_KIND_VIDEO_OVERLAY, text_prop="", keywords_prop="")
        text = published_sprite(client, legacy_published.version.id, text_prop="title.glow", keywords_prop="")
    for sprite_id in (visual.sprite_id, text.sprite_id):
        bundle_copy(legacy_published.store, sprite_id).write_text("// stale imv-preview-sync")
        shutil.rmtree(legacy_published.store.root / "cache" / "sprites" / sprite_id, ignore_errors=True)
    legacy_published.renderer.requests.clear()
    again = published_sprite(client, legacy_published.version.id, kind=pb.SPRITE_KIND_VIDEO_OVERLAY, text_prop="", keywords_prop="")
    # Today's client sends the fixed text field; the old dot-path publication of the same kind is the one refreshed.
    text_again = published_sprite(client, legacy_published.version.id, text_prop="text", keywords_prop="")
    assert (again.sprite_id, text_again.sprite_id) == (visual.sprite_id, text.sprite_id)
    assert legacy_published.renderer.samples == [] and len(legacy_published.renderer.requests) == 2
    assert "// rebuilt" in client.get(again.preview_url).text
    # A kind that was never published from this version is a new publication and gets the new rules.
    assert publish(client, publish_request(legacy_published.version.id, kind=pb.SPRITE_KIND_FILTER_OVERLAY, text_prop="", keywords_prop="")).status_code == 422


def test_retention_sweep_keeps_published_sprites_of_expired_devices(client: TestClient, published) -> None:
    """源设备长期不活跃时其会话被保留期清理删除，已发布的资产记录、预览包与预览照常可用。"""
    from datetime import timedelta

    from server.remotion_templates.runtime import Runtime
    from server.remotion_templates.store import now
    from server.remotion_templates.tables import devices

    summary = published_sprite(client, published.version.id)
    with published.store.transaction() as db:
        db.execute(update(devices).values(last_seen_at=(now() - timedelta(days=200)).replace(tzinfo=None)))
    asyncio.run(Runtime(published.store, None, published.settings).sweep())
    assert published.store.pending_deletions() == []
    assert not (objects(published.store) / "versions" / str(published.version.id)).exists()
    listed = pb.ListSpritesResponse.FromString(client.get("/api/sprites").content).sprites
    assert [item.sprite_id for item in listed] == [summary.sprite_id]
    assert bundle_copy(published.store, summary.sprite_id).is_file()
    assert client.get(summary.preview_url).status_code == 200


def test_deleting_an_unused_sprite_removes_its_record_and_bundle(client: TestClient, published) -> None:
    """未被模板使用的资产可删除：目录与预览不再提供，播放器包对象和缓存一起删除，源版本可以重新保存为新资产。"""
    summary = published_sprite(client, published.version.id)
    assert client.get(summary.preview_url).status_code == 200
    assert client.delete(f"/api/sprites/{summary.sprite_id}").status_code == 204
    assert pb.ListSpritesResponse.FromString(client.get("/api/sprites").content).sprites == []
    assert not (objects(published.store) / "sprites" / summary.sprite_id).exists()
    assert not (published.store.root / "cache" / "sprites" / summary.sprite_id).exists()
    assert client.get(summary.preview_url).status_code == 404
    assert client.delete(f"/api/sprites/{summary.sprite_id}").status_code == 404
    assert published_sprite(client, published.version.id).sprite_id != summary.sprite_id


def test_a_sprite_placed_in_a_template_cannot_be_deleted(client: TestClient, published) -> None:
    """云端模板的 remotion 轨道仍引用资产时拒绝删除并列出模板名称，资产与预览保持可用；其他轨道或无效配置不算引用。"""
    from datetime import UTC, datetime

    from server.template.store import initialize_schema, templates

    summary = published_sprite(client, published.version.id)
    stamp = datetime.now(UTC).replace(tzinfo=None)
    rows = [
        ("片头模板", {"tracks": [{"target": "title"}, {"target": "remotion", "sprite_id": summary.sprite_id}]}),
        ("无关模板", {"tracks": [{"target": "remotion", "sprite_id": str(uuid4())}]}),
        ("A 模板", {"tracks": [{"target": "remotion", "sprite_id": summary.sprite_id}]}),
        ("损坏模板", ["not", "an", "object"]),
    ]
    with initialize_schema().begin() as connection:
        for name, configuration in rows:
            connection.execute(templates.insert().values(
                template_id=str(uuid4()), name=name, configuration=configuration, created_at=stamp, updated_at=stamp,
            ))
    response = client.delete(f"/api/sprites/{summary.sprite_id}")
    assert response.status_code == 409
    assert response.json()["detail"] == "资产正被模板使用，请先在这些模板里移除后再删除：A 模板、片头模板"
    assert [item.sprite_id for item in pb.ListSpritesResponse.FromString(client.get("/api/sprites").content).sprites] == [summary.sprite_id]
    assert client.get(summary.preview_url).status_code == 200


@pytest.mark.parametrize("sprite_id", [str(uuid4()), "not-a-uuid"])
def test_deleting_an_unknown_sprite_is_a_404(client: TestClient, published, sprite_id) -> None:
    """不存在或格式错误的资产 ID 删除返回 404，不影响已有资产。"""
    summary = published_sprite(client, published.version.id)
    assert client.delete(f"/api/sprites/{sprite_id}").status_code == 404
    assert len(pb.ListSpritesResponse.FromString(client.get("/api/sprites").content).sprites) == 1
    assert summary.sprite_id


def rename(client: TestClient, sprite_id: str, name: str):
    """发送二进制重命名请求，不带设备头（资产库共享）。"""
    return client.post(f"/api/sprites/{sprite_id}/rename", content=pb.RenameSpriteRequest(name=name).SerializeToString(), headers=PROTOBUF)


def test_renaming_a_sprite_changes_only_its_catalog_name(client: TestClient, published) -> None:
    """重命名去除首尾空白后写入目录并返回新摘要；代码、预览包与哈希不变，重复保存同一版本返回改名后的原资产而不是新建。"""
    summary = published_sprite(client, published.version.id)
    before = sprite_store.get(summary.sprite_id)
    response = rename(client, summary.sprite_id, "  新的名称  ")
    assert response.status_code == 200
    renamed = pb.RenameSpriteResponse.FromString(response.content).sprite
    assert renamed.name == "新的名称" and renamed.sprite_id == summary.sprite_id
    assert [item.name for item in pb.ListSpritesResponse.FromString(client.get("/api/sprites").content).sprites] == ["新的名称"]
    assert sprite_store.get(summary.sprite_id) == {**before, "name": "新的名称"}
    assert client.get(summary.preview_url).status_code == 200
    assert published_sprite(client, published.version.id).name == "新的名称"


def test_a_sprite_placed_in_a_template_can_be_renamed(client: TestClient, published) -> None:
    """模板轨道只保存资产 ID，所以正被云端模板使用的资产也可以重命名，且仍不能删除。"""
    from datetime import UTC, datetime

    from server.template.store import initialize_schema, templates

    summary = published_sprite(client, published.version.id)
    stamp = datetime.now(UTC).replace(tzinfo=None)
    with initialize_schema().begin() as connection:
        connection.execute(templates.insert().values(
            template_id=str(uuid4()), name="片头模板", configuration={"tracks": [{"target": "remotion", "sprite_id": summary.sprite_id}]},
            created_at=stamp, updated_at=stamp,
        ))
    assert rename(client, summary.sprite_id, "改名后").status_code == 200
    assert client.delete(f"/api/sprites/{summary.sprite_id}").status_code == 409


@pytest.mark.parametrize("name", ["", "   ", "测" * 101])
def test_renaming_to_an_empty_or_overlong_name_is_a_422(client: TestClient, published, name) -> None:
    """去除首尾空白后为空或超过 100 个字符的名称返回 422，原名称保持不变；恰好 100 个字符可以。"""
    summary = published_sprite(client, published.version.id)
    original = pb.ListSpritesResponse.FromString(client.get("/api/sprites").content).sprites[0].name
    assert rename(client, summary.sprite_id, name).status_code == 422
    assert pb.ListSpritesResponse.FromString(client.get("/api/sprites").content).sprites[0].name == original
    assert rename(client, summary.sprite_id, "测" * 100).status_code == 200


@pytest.mark.parametrize("sprite_id", [str(uuid4()), "not-a-uuid"])
def test_renaming_an_unknown_sprite_is_a_404(client: TestClient, published, sprite_id) -> None:
    """不存在或格式错误的资产 ID 重命名返回 404，不影响已有资产。"""
    summary = published_sprite(client, published.version.id)
    assert rename(client, sprite_id, "新名称").status_code == 404
    assert pb.ListSpritesResponse.FromString(client.get("/api/sprites").content).sprites[0].name == summary.name


def test_rename_requires_a_protobuf_body(client: TestClient, published) -> None:
    """非 Protobuf 的内容类型返回 415，内容损坏返回 400，名称都不变。"""
    summary = published_sprite(client, published.version.id)
    url = f"/api/sprites/{summary.sprite_id}/rename"
    assert client.post(url, json={"name": "新名称"}).status_code == 415
    assert client.post(url, content=b"\xff\xff\xff", headers=PROTOBUF).status_code == 400
    assert pb.ListSpritesResponse.FromString(client.get("/api/sprites").content).sprites[0].name == summary.name


def test_a_stale_preview_hash_refresh_does_not_undo_a_rename(client: TestClient, published) -> None:
    """预览包刷新时手里的发布记录可能早于重命名：只更新预览哈希，不把旧名称写回。"""
    summary = published_sprite(client, published.version.id)
    stale = sprites.get(summary.sprite_id)
    assert rename(client, summary.sprite_id, "改名后").status_code == 200
    sprites._store_bundle(published.store, stale, b"// refreshed")
    stored = sprite_store.get(summary.sprite_id)
    assert stored["name"] == "改名后"
    assert stored["preview_sha256"] == hashlib.sha256(b"// refreshed").hexdigest()
