"""Remotion 字效保存为 Sprite 资产：发布、目录、预览与云端模板绑定的真实 SQLite/文件/HTTP 回归。

不访问模型、浏览器或 MySQL（模板库使用 conftest 的临时 SQLite）；发布版本由离线渲染替身落盘。
在 server/ 目录执行 `uv run --locked pytest tests/test_remotion_sprite_publish.py -v`。
"""

import asyncio
import json
import logging
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from generated.imv.sprite.v1 import sprite_pb2 as pb
from sqlalchemy import Engine, inspect

from server.app import app
from server.remotion_templates import sprites
from server.remotion_templates.harness import Harness
from server.remotion_templates.models import GenerateTemplateRequest
from server.remotion_templates.provider import Budget
from server.remotion_templates.settings import Settings
from server.remotion_templates.sprite_router import sprite_runtime
from server.remotion_templates.store import Conflict, Store
from server.remotion_templates.tools.contracts import SpriteDraft

from .test_remotion_version_diagnostics import (
    CodeOnlyRenderer,
    OfflineRenderer,
    _SavedSprite,
    runtime_report,
)

PROTOBUF = {"Content-Type": "application/x-protobuf"}
SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "keywords": {"type": "string"},
        "color": {"type": "string", "title": "文字颜色"},
        "size": {"type": "number", "minimum": 12, "maximum": 300},
        "weight": {"type": "integer", "enum": [400, 700]},
        "shadow": {"type": "boolean"},
    },
    "required": ["text", "keywords", "color", "size", "weight", "shadow"],
    "additionalProperties": False,
}
DEFAULTS = {"text": "标题", "keywords": "", "color": "#ffffff", "size": 64, "weight": 400, "shadow": False}
CODE = 'import React from "react";\nexport default function Sprite(p: {text: string}) { return <div>{p.text}</div>; }\n'


class BundleRebuilder:
    """记录重建请求的渲染替身：写出带同步标记的新预览包，或按要求失败；不执行真实浏览器或沙箱。"""

    def __init__(self):
        """默认成功，requests 保存宿主写给 worker 的请求。"""
        self.requests: list[dict] = []
        self.failure: Exception | None = None

    async def run_worker(self, directory, *, worker=None, timeout_seconds=None):
        """只接受预览包构建；失败时不写任何文件。"""
        assert worker == "presentation-worker.mjs"
        self.requests.append(json.loads((directory / "request.json").read_text()))
        if self.failure:
            raise self.failure
        (directory / "interactive.js").write_text("// rebuilt imv-preview-sync")
        return {}


NESTED_SCHEMA = {
    "type": "object",
    "properties": {"title_main": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "title": "标题文字"},
            "fontSize": {"type": "number", "title": "字号", "minimum": 8, "maximum": 500},
            "textColor": {"type": "string", "title": "文字颜色"},
        },
        "required": ["title", "fontSize", "textColor"],
        "additionalProperties": False,
    }},
    "required": ["title_main"],
    "additionalProperties": False,
}
NESTED_DEFAULTS = {"title_main": {"title": "今日灵感", "fontSize": 160, "textColor": "#FFD400"}}


@pytest.fixture
def published(tmp_path):
    """发布一个带扁平标量参数的成功版本，并让主应用的 Sprite 路由使用同一份临时数据。"""
    yield from published_version(tmp_path, SCHEMA, DEFAULTS)


@pytest.fixture
def nested_published(tmp_path):
    """发布组合 Sprite 常见的嵌套参数版本：业务文字位于 title_main.title。"""
    yield from published_version(tmp_path, NESTED_SCHEMA, NESTED_DEFAULTS)


def published_version(tmp_path, schema, defaults):
    """用离线渲染替身封存一个成功版本，并在使用结束后恢复路由依赖。"""
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
            "layout": {"x": 0, "y": 0, "width": 1080, "height": 1920, "z_index": 0},
            "timing": {"start_frame": 0, "duration_frames": 30},
        }],
    )
    _work, job = store.create(GenerateTemplateRequest(description="标题"))
    store.claim()
    result = asyncio.run(harness.finalize_sprite(
        SimpleNamespace(saved_sprite=lambda _id: _SavedSprite(sprite), validation_for=lambda _r: runtime_report(sprite)),
        "saved", Budget(), store.job_dir(job.id),
    ))
    version = store.publish(job.id, *result)
    renderer = BundleRebuilder()
    service = SimpleNamespace(store=store, settings=settings, harness=SimpleNamespace(renderer=renderer))
    app.dependency_overrides[sprite_runtime] = lambda: service
    yield SimpleNamespace(store=store, version=version, renderer=renderer, service=service)
    app.dependency_overrides.pop(sprite_runtime)


def publish_request(version_id, **fields) -> pb.PublishSpriteRequest:
    """默认把版本发布为带关键词字段的文字 Sprite；用例按需覆盖字段。"""
    values = {"kind": pb.SPRITE_KIND_TEXT, "text_prop": "text", "keywords_prop": "keywords", **fields}
    return pb.PublishSpriteRequest(source_version_id=str(version_id), **values)


def publish(client: TestClient, request: pb.PublishSpriteRequest):
    """发送二进制发布请求。"""
    return client.post("/api/sprites/publish", content=request.SerializeToString(), headers=PROTOBUF)


def published_sprite(client: TestClient, version_id, **fields) -> pb.SpriteSummary:
    """发布成功并返回摘要。"""
    response = publish(client, publish_request(version_id, **fields))
    assert response.status_code == 200
    return pb.PublishSpriteResponse.FromString(response.content).sprite


def placement(sprite_id: str, **fields) -> pb.SpritePlacement:
    """默认是从 0 秒开始的一个固定片段；用例按需覆盖字段。"""
    values = {"id": "p1", "start_mode": "seconds", "start": 0, "order": 0, **fields}
    return pb.SpritePlacement(sprite_id=sprite_id, **values)


def save_bindings(client: TestClient, style_id: str, placements: list, expected_revision: int = 0, body_style_id: str | None = None):
    """整体替换一个 style_id 下的 Remotion 片段。"""
    request = pb.SaveStyleSpritesRequest(
        style_id=body_style_id or style_id, placements=placements, expected_revision=expected_revision,
    )
    return client.post(f"/api/sprites/styles/{style_id}", content=request.SerializeToString(), headers=PROTOBUF)


def bindings(response) -> pb.StyleSpriteBindings:
    """解码绑定读取/保存响应。"""
    return pb.GetStyleSpritesResponse.FromString(response.content).bindings


def create_style(client: TestClient, template_payload: dict) -> str:
    """创建一个 IMS 云端模板并返回其 ID，用来证明片段与 IMS 数据互不依赖。"""
    from generated.imv.template.v1 import template_pb2 as template_pb
    from .template_wire import post_template

    response = post_template(client, template_payload)
    assert response.status_code == 201
    return template_pb.SaveTemplateResponse.FromString(response.content).template.template_id


def test_publish_copies_content_and_lists_summary_without_source(client: TestClient, published) -> None:
    """发布后目录只含摘要与可编辑参数；文字与关键词字段归总线，不进入参数。"""
    summary = published_sprite(client, published.version.id)
    assert (summary.name, summary.kind, summary.keywords_supported) == ("标题字效", pb.SPRITE_KIND_TEXT, True)
    assert (summary.canvas.width, summary.canvas.height, summary.canvas.fps, summary.canvas.preview_frames) == (1080, 1920, 30, 30)
    assert summary.source_version_id == str(published.version.id)
    parameters = {item.key: item for item in summary.parameters}
    assert set(parameters) == {"color", "size", "weight", "shadow"}
    assert parameters["color"].label == "文字颜色"
    assert (parameters["size"].minimum, parameters["size"].maximum) == (12, 300)
    assert [item.number_value for item in parameters["weight"].allowed_values] == [400, 700]
    assert all(item.access == pb.OPERATOR_ACCESS_VISIBLE_EDITABLE for item in parameters.values())

    listed = client.get("/api/sprites")
    assert listed.status_code == 200
    assert listed.headers["content-type"] == "application/x-protobuf"
    assert [item.sprite_id for item in pb.ListSpritesResponse.FromString(listed.content).sprites] == [summary.sprite_id]
    assert b"export default" not in listed.content
    assert (published.store.root / "sprites" / summary.sprite_id / "interactive.js").is_file()


def test_republishing_returns_the_original_sprite(client: TestClient, published) -> None:
    """相同来源与字段选择重复发布幂等；不同字段选择产生独立资产。"""
    first = published_sprite(client, published.version.id)
    again = published_sprite(client, published.version.id)
    other = published_sprite(client, published.version.id, keywords_prop="")
    assert again.sprite_id == first.sprite_id
    assert other.sprite_id != first.sprite_id and not other.keywords_supported
    assert len(pb.ListSpritesResponse.FromString(client.get("/api/sprites").content).sprites) == 2


def test_published_sprite_survives_deleting_its_source_version(client: TestClient, published) -> None:
    """源版本的封存目录与记录被清理后，目录条目和预览仍来自发布自己的副本。"""
    summary = published_sprite(client, published.version.id)
    shutil.rmtree(published.store.root / "accepted" / str(published.version.id))
    with published.store.connection() as db:
        db.execute("DELETE FROM versions")
    assert pb.ListSpritesResponse.FromString(client.get("/api/sprites").content).sprites[0].sprite_id == summary.sprite_id
    preview = client.get(summary.preview_url)
    assert preview.status_code == 200
    assert "rebuilt imv-preview-sync" in preview.text
    assert "sandbox allow-scripts" in preview.headers["content-security-policy"]


@pytest.mark.parametrize(("fields", "status"), [
    ({"kind": pb.SPRITE_KIND_UNSPECIFIED}, 422),
    ({"text_prop": "size"}, 422),
    ({"text_prop": "missing"}, 422),
    ({"keywords_prop": "text"}, 422),
    ({"keywords_prop": "missing"}, 422),
    ({"kind": pb.SPRITE_KIND_VIDEO_OVERLAY}, 422),
], ids=["no-kind", "non-string-text", "unknown-text", "same-keywords", "unknown-keywords", "visual-with-text-fields"])
def test_publish_rejects_invalid_field_choices(client: TestClient, published, fields, status) -> None:
    """类型必须明确，文字字段必须是字符串参数，视觉类型不能声明文字字段。"""
    response = publish(client, publish_request(published.version.id, **fields))
    assert response.status_code == status
    assert client.get("/api/sprites").content == b""


def test_publish_rejects_unknown_malformed_or_tampered_sources(client: TestClient, published) -> None:
    """未知或格式错误的版本、被改写的封存产物都不能发布。"""
    assert publish(client, publish_request("00000000-0000-4000-8000-000000000000")).status_code == 404
    assert publish(client, publish_request("not-a-uuid")).status_code == 422
    (published.store.root / "accepted" / str(published.version.id) / "interactive.js").write_text("tampered")
    assert publish(client, publish_request(published.version.id)).status_code == 409
    assert client.post("/api/sprites/publish", json={}).status_code == 415
    assert client.post("/api/sprites/publish", content=b"\xff\xff", headers=PROTOBUF).status_code == 400


def test_style_bindings_roundtrip_with_revision_lock(client: TestClient, published) -> None:
    """未保存时为版本 0；保存递增 revision，过期 revision 返回 409 且不改动已存片段。"""
    sprite = published_sprite(client, published.version.id)
    style_id = str(uuid4())
    empty = client.get(f"/api/sprites/styles/{style_id}")
    assert empty.status_code == 200 and (bindings(empty).revision, list(bindings(empty).placements)) == (0, [])

    first = save_bindings(client, style_id, [
        placement(sprite.sprite_id, start=2, duration=1),
        placement(sprite.sprite_id, id="p2", start=7.5, order=1),
    ])
    assert first.status_code == 200
    saved = bindings(first)
    assert saved.revision == 1 and [item.id for item in saved.placements] == ["p1", "p2"]
    assert (saved.placements[0].start, saved.placements[0].duration) == (2, 1)
    assert saved.placements[1].start == 7.5 and not saved.placements[1].HasField("duration")
    assert saved.updated_at.seconds > 0

    stale = save_bindings(client, style_id, [], expected_revision=0)
    assert stale.status_code == 409
    again = bindings(client.get(f"/api/sprites/styles/{style_id}"))
    assert again.revision == 1 and len(again.placements) == 2
    cleared = bindings(save_bindings(client, style_id, [], expected_revision=1))
    assert cleared.revision == 2 and list(cleared.placements) == []


@pytest.mark.parametrize("build", [
    lambda sid: [placement(sid, target=pb.SPRITE_TARGET_TITLE)],
    lambda sid: [placement(sid, overrides=[pb.SpriteParameterOverride(key="size", value=pb.ScalarValue(number_value=88))])],
    lambda sid: [placement(sid, start_mode="frames")],
    lambda sid: [placement(sid, start_mode="percent", start=50)],
    lambda sid: [placement(sid, start=-1)],
    lambda sid: [placement(sid, start=float("inf"))],
    lambda sid: [placement(sid, duration=0)],
    lambda sid: [placement(sid, order=1)],
    lambda sid: [placement(sid), placement(sid, order=1)],
    lambda sid: [placement(sid, id="")],
    lambda sid: [placement(sid, id="x" * 65)],
    lambda sid: [placement(sid, id=f"p{index}", order=index) for index in range(101)],
    lambda sid: [placement("00000000-0000-4000-8000-000000000000")],
], ids=[
    "object-target", "style-override", "bad-start-mode", "percent-start", "negative-start", "infinite-start",
    "zero-duration", "order-gap", "duplicate-id", "empty-id", "long-id", "too-many", "unknown-sprite",
])
def test_invalid_placements_are_rejected_without_saving(client: TestClient, published, build) -> None:
    """片段必须是引用已有资产的固定内容：作用对象、样式覆盖、非秒起点、非法时间与顺序都返回 422，且不产生记录。"""
    sprite = published_sprite(client, published.version.id)
    style_id = str(uuid4())
    assert save_bindings(client, style_id, build(sprite.sprite_id)).status_code == 422
    assert bindings(client.get(f"/api/sprites/styles/{style_id}")).revision == 0


def test_every_published_kind_can_be_placed_without_imv_object_rules(client: TestClient, published) -> None:
    """IMS 的对象规则不属于 Remotion：没有关键词的文字资产、没有固定时长的视觉叠加都只需起点就能放置。"""
    visual = published_sprite(client, published.version.id, kind=pb.SPRITE_KIND_VIDEO_OVERLAY, text_prop="", keywords_prop="")
    plain_text = published_sprite(client, published.version.id, keywords_prop="")
    style_id = str(uuid4())
    response = save_bindings(client, style_id, [placement(visual.sprite_id), placement(plain_text.sprite_id, id="p2", order=1)])
    assert response.status_code == 200
    assert [item.sprite_id for item in bindings(response).placements] == [visual.sprite_id, plain_text.sprite_id]


def test_binding_routes_check_the_body_and_need_no_imv_template(client: TestClient, published) -> None:
    """style_id 只用于关联：没有对应 IMS 模板也能读写；请求体 style_id 与路径不一致、类型或内容错误被拒绝。"""
    sprite = published_sprite(client, published.version.id)
    style_id, other = str(uuid4()), str(uuid4())
    assert save_bindings(client, style_id, [placement(sprite.sprite_id)]).status_code == 200
    assert save_bindings(client, style_id, [], expected_revision=1, body_style_id=other).status_code == 422
    assert client.post(f"/api/sprites/styles/{style_id}", json={}).status_code == 415
    assert client.post(f"/api/sprites/styles/{style_id}", content=b"\xff\xff", headers=PROTOBUF).status_code == 400
    assert client.get("/api/sprites/styles/not-a-uuid").status_code == 422
    assert bindings(client.get(f"/api/sprites/styles/{style_id}")).revision == 1


def test_bindings_and_imv_templates_are_independent(client: TestClient, template_db: Engine, published, template_payload) -> None:
    """Remotion 片段只存在 Remotion 自己的库里：IMS 数据库没有对应表，删除 IMS 模板不影响片段，资产也保留。"""
    sprite = published_sprite(client, published.version.id)
    template_id = create_style(client, template_payload)
    assert save_bindings(client, template_id, [placement(sprite.sprite_id)]).status_code == 200
    assert not inspect(template_db).has_table("style_sprite_bindings")
    assert client.delete(f"/template/{template_id}").status_code == 204
    assert [item.id for item in bindings(client.get(f"/api/sprites/styles/{template_id}")).placements] == ["p1"]
    assert len(pb.ListSpritesResponse.FromString(client.get("/api/sprites").content).sprites) == 1


def test_concurrent_saves_from_the_same_revision_only_accept_one(client: TestClient, published) -> None:
    """两个编辑器同时从版本 0 保存同一个 style_id，只有一个成功，另一个得到版本冲突而不覆盖对方。"""
    sprite = published_sprite(client, published.version.id)
    style_id = uuid4()
    barrier = Barrier(2, timeout=5)

    def save(name: str):
        """并发执行真实 SQLite 事务，返回新版本号或冲突标记。"""
        barrier.wait()
        try:
            return sprites.replace_bindings(published.store, style_id, [{"id": name, "sprite_id": sprite.sprite_id}], 0)[0]
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["a", "b"]))
    assert sorted(map(str, results)) == ["1", "conflict"]
    assert sprites.get_bindings(published.store, style_id)[0] == 1


def test_nested_parameters_publish_with_dot_paths(client: TestClient, nested_published) -> None:
    """组合 Sprite 的业务文字在嵌套对象里：用点号路径发布，文字字段不入样式参数。"""
    summary = published_sprite(client, nested_published.version.id, text_prop="title_main.title", keywords_prop="")
    assert {item.key for item in summary.parameters} == {"title_main.fontSize", "title_main.textColor"}
    size = next(item for item in summary.parameters if item.key == "title_main.fontSize")
    assert (size.minimum, size.maximum, size.default_value.number_value) == (8, 500, 160)



@pytest.mark.parametrize("text_prop", ["title", "title_main", "title_main.missing", "title_main.fontSize"], ids=["flat-name-of-nested", "object", "unknown-path", "non-string"])
def test_nested_publish_rejects_wrong_text_paths(client: TestClient, nested_published, text_prop) -> None:
    """文字字段必须是真实存在的嵌套字符串叶子，顶层同名、对象或数字字段都被拒绝。"""
    assert publish(client, publish_request(nested_published.version.id, text_prop=text_prop, keywords_prop="")).status_code == 422


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
    where = []
    verify = sprites.verify_artifacts

    def probe(*args, **kwargs):
        """工作线程里没有运行中的事件循环，事件循环线程里有；记录所在位置后照常校验。"""
        try:
            asyncio.get_running_loop()
            where.append("event loop")
        except RuntimeError:
            where.append("worker thread")
        return verify(*args, **kwargs)

    monkeypatch.setattr(sprites, "verify_artifacts", probe)
    published_sprite(client, published.version.id)
    assert where == ["worker thread"]


def test_catalog_skips_unreadable_records_and_keeps_the_rest(client: TestClient, published, caplog) -> None:
    """被改写或损坏的发布记录只从目录中隔离并记日志，其余资产照常列出；按 ID 访问仍给出明确错误。"""
    healthy = published_sprite(client, published.version.id, keywords_prop="")
    tampered = published_sprite(client, published.version.id)
    unparsable = published_sprite(client, published.version.id, text_prop="color", keywords_prop="")
    with published.store.connection() as db:
        data = json.loads(db.execute("SELECT data FROM sprites WHERE id=?", (tampered.sprite_id,)).fetchone()["data"])
        data["tsx_code"] += "// edited"
        db.execute("UPDATE sprites SET data=? WHERE id=?", (json.dumps(data), tampered.sprite_id))
        db.execute("UPDATE sprites SET data='{not json' WHERE id=?", (unparsable.sprite_id,))
    with caplog.at_level(logging.WARNING):
        listed = client.get("/api/sprites")
    assert listed.status_code == 200
    assert [item.sprite_id for item in pb.ListSpritesResponse.FromString(listed.content).sprites] == [healthy.sprite_id]
    assert tampered.sprite_id in caplog.text and unparsable.sprite_id in caplog.text
    assert client.get(tampered.preview_url).status_code == 409


def test_stale_player_bundle_is_rebuilt_from_sealed_source(client: TestClient, published) -> None:
    """封存包缺少同步支持时发布会用封存的源码、默认参数和画布重建；原版本目录不被改动。"""
    summary = published_sprite(client, published.version.id)
    request = published.renderer.requests[0]
    assert request["code"] == published.version.candidate.tsx_code
    assert request["config"] == published.version.candidate.default_config
    assert request["composition"]["duration_in_frames"] == 30
    copied = published.store.root / "sprites" / summary.sprite_id / "interactive.js"
    assert b"imv-preview-sync" in copied.read_bytes()
    assert b"offline Player fixture" in (published.store.root / "accepted" / str(published.version.id) / "interactive.js").read_bytes()
    assert not [item for item in (published.store.root / "sprites").iterdir() if item.name.startswith(".build-")]


def test_current_sealed_bundle_is_copied_without_rebuilding(client: TestClient, published) -> None:
    """封存包已含同步支持时直接复制，不启动任何构建。"""
    (published.store.root / "accepted" / str(published.version.id) / "interactive.js").write_text("// imv-preview-sync")
    from server.remotion_templates.evidence import digest
    published.version.validation.artifacts["interactive.js"] = digest(published.store.root / "accepted" / str(published.version.id) / "interactive.js")
    with published.store.connection() as db:
        db.execute("UPDATE versions SET data=? WHERE id=?", (published.version.model_dump_json(), str(published.version.id)))
    summary = published_sprite(client, published.version.id)
    assert published.renderer.requests == []
    assert client.get(summary.preview_url).text.count("imv-preview-sync") == 1


def test_rebuild_failure_still_saves_and_republishing_refreshes_the_old_copy(client: TestClient, published) -> None:
    """重建失败不阻止保存（仅无法叠加预览）；之后重新保存同一资产会把旧副本刷新为新包，其余内容不变。"""
    published.renderer.failure = RuntimeError("sandbox unavailable")
    first = published_sprite(client, published.version.id)
    assert "offline Player fixture" in client.get(first.preview_url).text
    published.renderer.failure = None
    again = published_sprite(client, published.version.id)
    assert again.sprite_id == first.sprite_id
    assert "rebuilt imv-preview-sync" in client.get(first.preview_url).text
    assert len(pb.ListSpritesResponse.FromString(client.get("/api/sprites").content).sprites) == 1


def test_failed_hash_update_keeps_the_old_player_copy_and_republishing_finishes_the_refresh(client: TestClient, published) -> None:
    """刷新旧副本时记录新哈希的数据库更新失败（锁超时、进程中断）：旧副本与旧哈希仍一致、预览可用且不留临时文件；数据库恢复后再次保存完成刷新。"""
    published.renderer.failure = RuntimeError("sandbox unavailable")
    summary = published_sprite(client, published.version.id)
    published.renderer.failure = None
    with published.store.connection() as db:
        db.execute("CREATE TRIGGER fail_refresh BEFORE UPDATE ON sprites BEGIN SELECT RAISE(ABORT, 'database is locked'); END")
    with pytest.raises(sqlite3.DatabaseError):
        publish(client, publish_request(published.version.id))
    assert "offline Player fixture" in client.get(summary.preview_url).text
    assert [item.name for item in (published.store.root / "sprites" / summary.sprite_id).iterdir()] == ["interactive.js"]
    with published.store.connection() as db:
        db.execute("DROP TRIGGER fail_refresh")
    assert published_sprite(client, published.version.id).sprite_id == summary.sprite_id
    assert "rebuilt imv-preview-sync" in client.get(summary.preview_url).text


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
        return await asyncio.gather(*(sprites.publish(published.service, request) for _ in range(2)))

    first, second = asyncio.run(twice())
    assert first.sprite_id == second.sprite_id
    assert len(published.renderer.requests) == 1
