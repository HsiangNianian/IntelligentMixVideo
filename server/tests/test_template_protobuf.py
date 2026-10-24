# 使用临时 SQLite 和真实 FastAPI 路由验证模板 Protobuf 请求、默认 JSON 及旧客户端响应；执行 uv run pytest tests/test_template_protobuf.py。

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from generated.imv.template.v1 import template_pb2 as pb
from google.protobuf.json_format import ParseDict
from sqlalchemy import Engine, inspect, select

from server.template import store
from .conftest import SPRITE_ID, remotion_track, seed_sprite, template_track
from .template_wire import post_template as _post


def test_bilingual_roundtrip_retains_disabled_style_and_sprite(client, template_db):
    """真实协议与存储保留停用英文和字效绑定；摘要仍取中文，重新开启不改参数。"""
    seed_sprite()
    chinese = template_track("title", title="中文标题")
    english = {**template_track("title", title="English sample", titleSize=28, titleY=15),
               "id": "title-en", "language": "en", "enabled": False}
    payload = {"name": "双语", "effect_ids": [], "tracks": [chinese, template_track("subtitle"), english,
        remotion_track("english-effect", text_target="title", language="en", editor={"remotion": {"parameters": [{"key": "fontSize", "numberValue": 48}]}})]}
    created = _post(client, payload)
    assert created.status_code == 201, created.text
    record = ParseDict(created.json(), pb.SaveTemplateResponse()).template
    saved_english = record.tracks.tracks[2]
    assert saved_english.language == "en" and saved_english.HasField("enabled") and not saved_english.enabled
    assert saved_english.editor.title_size == 28
    assert record.tracks.tracks[3].language == "en"
    assert not record.tracks.tracks[0].HasField("language")
    assert ParseDict(client.get(f"/template/{record.template_id}").json(), pb.GetTemplateResponse()).template == record
    summary = ParseDict(client.get("/template").json(), pb.ListTemplatesResponse()).templates[0]
    assert summary.title == "中文标题" and not summary.validation_error
    payload["template_id"] = record.template_id
    english["enabled"] = True
    restored = _post(client, payload)
    assert restored.status_code == 200
    tracks = ParseDict(restored.json(), pb.SaveTemplateResponse()).template.tracks.tracks
    assert tracks[2].editor == saved_english.editor
    assert tracks[3] == record.tracks.tracks[3]


@pytest.mark.parametrize("error", ["duplicate", "missing-chinese", "different-time", "disabled-chinese", "missing-english"])
def test_invalid_bilingual_objects_do_not_save(client, error):
    """语言内唯一、中文依赖、时间同步与开关范围都在保存边界检查，不写入无效模板。"""
    chinese = template_track("title")
    english = {**template_track("title"), "id": "title-en", "language": "en"}
    tracks = [chinese, template_track("subtitle"), english]
    if error == "duplicate":
        tracks.append({**english, "id": "title-en-2"})
    elif error == "missing-chinese":
        tracks.remove(chinese)
    elif error == "different-time":
        english["start"] = 1
    elif error == "disabled-chinese":
        chinese["enabled"] = False
    else:
        tracks.remove(english)
        tracks.append(remotion_track(text_target="title", language="en"))
    assert _post(client, {"name": "不合法双语", "tracks": tracks}).status_code == 422
    assert not ParseDict(client.get("/template").json(), pb.ListTemplatesResponse()).templates


def test_transition_list_roundtrip(client: TestClient):
    """多个转场及统一设置经 Protobuf 与数据库保存后保持不变。"""
    from server.template.schema import effect_catalog

    asset = next(item for item in effect_catalog().values() if item.category == "transition/normal")
    tracks = [{**template_track("transition", transition=asset.id), "id": f"transition-{boundary}", "transition_boundary": boundary, "duration": 1.5} for boundary in (1, 2, 3)]
    tracks.extend([template_track("title"), template_track("subtitle")])
    created = _post(client, {"name": "转场列表", "effect_ids": [asset.id], "tracks": tracks, "transition_mode": "random", "transition_enabled": False, "transition_duration_seconds": 1.5})
    assert created.status_code == 201
    record = ParseDict(created.json(), pb.SaveTemplateResponse()).template
    assert [track.transition_boundary for track in record.tracks.tracks[:3]] == [1, 2, 3]
    assert record.transition_mode == "random"
    assert record.HasField("transition_enabled") and not record.transition_enabled
    assert record.transition_duration_seconds == 1.5
    assert all(not track.HasField("start") and not track.start_mode for track in record.tracks.tracks[:2])
    assert ParseDict(client.get(f"/template/{record.template_id}").json(), pb.GetTemplateResponse()).template == record
    tracks[1]["transition_boundary"] = 1
    assert _post(client, {"name": "重复交界处", "effect_ids": [asset.id], "tracks": tracks}).status_code == 422


@pytest.mark.parametrize("change", ["missing-boundary", "start", "start_mode"])
def test_transition_requires_only_boundary_timing(client: TestClient, change):
    """真实保存路由拒绝缺失交界处及携带计算秒数的转场。"""
    from server.template.schema import effect_catalog

    asset = next(item for item in effect_catalog().values() if item.category == "transition/normal")
    transition = template_track("transition", transition=asset.id)
    if change == "missing-boundary":
        del transition["transition_boundary"]
    else:
        transition[change] = 9 if change == "start" else "seconds"
    response = _post(client, {"name": "转场校验", "effect_ids": [asset.id],
                              "tracks": [template_track("title"), template_track("subtitle"), transition]})
    assert response.status_code == 422
    assert not ParseDict(client.get("/template").json(), pb.ListTemplatesResponse()).templates


@pytest.mark.parametrize("change", ["subtitle-time", "duplicate-title", "invalid-structure", "metadata-in-config"])
def test_invalid_record_does_not_block_collection(client: TestClient, template_db: Engine, template_payload: dict, change):
    """真实存储中混合有效与无效记录，列表逐条报告、详情拒绝、删除只影响目标。"""
    valid = ParseDict(_post(client, template_payload).json(), pb.SaveTemplateResponse()).template
    invalid = ParseDict(_post(client, {**template_payload, "name": "待处理模板"}).json(), pb.SaveTemplateResponse()).template
    with template_db.begin() as connection:
        row = connection.execute(select(store.templates).where(store.templates.c.template_id == invalid.template_id)).one()
        config = dict(row.configuration)
        if change == "subtitle-time":
            next(track for track in config["tracks"] if track["target"] == "subtitle")["duration"] = 10
        elif change == "duplicate-title":
            title = next(track for track in config["tracks"] if track["target"] == "title")
            config["tracks"].append({**title, "id": "second-title"})
        elif change == "metadata-in-config":
            config["name"] = "重复元数据"
        else:
            config["tracks"] = None
        connection.execute(store.templates.update().where(store.templates.c.template_id == invalid.template_id).values(configuration=config))
    listed = client.get("/template")
    assert listed.status_code == 200
    summaries = {item.template_id: item for item in ParseDict(listed.json(), pb.ListTemplatesResponse()).templates}
    assert summaries[valid.template_id].validation_error == ""
    assert summaries[invalid.template_id].validation_error
    assert not summaries[invalid.template_id].HasField("track_count")
    detail = client.get(f"/template/{invalid.template_id}")
    assert detail.status_code == 409 and invalid.template_id in detail.json()["detail"]
    assert client.get(f"/template/{valid.template_id}").status_code == 200
    assert client.delete(f"/template/{invalid.template_id}").status_code == 204
    remaining = ParseDict(client.get("/template").json(), pb.ListTemplatesResponse()).templates
    assert [item.template_id for item in remaining] == [valid.template_id]


@pytest.mark.parametrize(("path", "method", "status"), [
    ("/template", "get", "200"),
    ("/template", "post", "200"),
    ("/template", "post", "201"),
    ("/template/{template_id}", "get", "200"),
])
def test_template_openapi_documents_json_responses(
    client: TestClient, path: str, method: str, status: str,
) -> None:
    """每种模板读取与保存结果都声明 JSON 及旧客户端使用的二进制成功响应。"""
    operation = client.get("/openapi.json").json()["paths"][path][method]
    assert operation["responses"][status]["content"] == {
        "application/json": {"schema": {"type": "object"}},
        "application/x-protobuf": {"schema": {"type": "string", "format": "binary"}},
    }


def test_template_openapi_documents_binary_save_request(client: TestClient) -> None:
    """保存接口要求二进制 Protobuf 请求体。"""
    operation = client.get("/openapi.json").json()["paths"]["/template"]["post"]
    assert operation["requestBody"] == {
        "required": True,
        "content": {
            "application/x-protobuf": {"schema": {"type": "string", "format": "binary"}},
        },
    }


@pytest.mark.parametrize("accept", [None, "*/*", "application/json"])
def test_template_protobuf_create_read_update_delete(
    client: TestClient, template_db: Engine, template_payload: dict, accept: str | None,
) -> None:
    """未指定格式、通配或请求 JSON 时，四个原有地址默认 JSON 并保留持久化语义。"""
    client.headers.pop("accept", None)
    if accept is not None:
        client.headers["Accept"] = accept
    assert not inspect(template_db).has_table("templates")
    empty = client.get("/template")
    assert empty.status_code == 200
    assert empty.headers["content-type"] == "application/json"
    assert empty.headers["vary"] == "Accept"
    assert empty.json() == {"templates": []}
    assert ParseDict(empty.json(), pb.ListTemplatesResponse()).templates == []

    created = _post(client, template_payload)
    assert created.status_code == 201
    assert created.headers["content-type"] == "application/json"
    assert created.headers["vary"] == "Accept"
    payload = created.json()["template"]
    assert payload["name"] == "测试模板"
    assert "测试模板" in created.text
    assert payload["effectIds"] == ["in/fade_in"]
    assert payload["tracks"]["tracks"][0]["start"] == 0
    assert payload["tracks"]["tracks"][0]["editor"]["titleKeywordBold"] is False
    assert payload["createdAt"].endswith("Z")
    record = ParseDict(created.json(), pb.SaveTemplateResponse()).template
    assert record.name == "测试模板"
    assert record.effect_ids == ["in/fade_in"]
    assert record.tracks.tracks[0].editor.title_in == "in/fade_in"
    assert record.created_at.ToDatetime(tzinfo=UTC) <= datetime.now(UTC)
    assert inspect(template_db).has_table("templates")

    detail = client.get(f"/template/{record.template_id}")
    assert detail.status_code == 200
    assert detail.headers["content-type"] == "application/json"
    assert detail.headers["vary"] == "Accept"
    assert detail.json()["template"] == payload
    assert ParseDict(detail.json(), pb.GetTemplateResponse()).template == record
    listed = client.get("/template")
    assert listed.headers["content-type"] == "application/json"
    assert listed.json()["templates"][0]["templateId"] == record.template_id
    summary, = ParseDict(listed.json(), pb.ListTemplatesResponse()).templates
    assert (summary.template_id, summary.name, summary.track_count, summary.validation_error) == (record.template_id, record.name, 2, "")
    assert summary.title == record.tracks.tracks[0].editor.title
    assert summary.subtitle == record.tracks.tracks[1].editor.subtitle

    updated = _post(client, {**template_payload, "template_id": record.template_id, "name": "修改后的模板"})
    assert updated.status_code == 200
    assert updated.headers["content-type"] == "application/json"
    revised = ParseDict(updated.json(), pb.SaveTemplateResponse()).template
    assert revised.template_id == record.template_id
    assert revised.name == "修改后的模板"

    deleted = client.delete(f"/template/{record.template_id}")
    assert deleted.status_code == 204
    assert deleted.content == b""
    missing = client.get(f"/template/{record.template_id}")
    assert missing.status_code == 404
    assert missing.headers["content-type"] == "application/json"
    assert ParseDict(client.get("/template").json(), pb.ListTemplatesResponse()).templates == []


def test_legacy_client_can_read_and_save_templates(client: TestClient, template_payload: dict) -> None:
    """旧客户端明确请求 Protobuf 时，创建、列表、详情和更新仍可用原解码器读取。"""
    client.headers["Accept"] = "application/x-protobuf"
    created = _post(client, template_payload)
    assert created.status_code == 201
    record = pb.SaveTemplateResponse.FromString(created.content).template
    assert created.headers["content-type"] == "application/x-protobuf"
    assert created.headers["vary"] == "Accept"
    assert record.name == template_payload["name"]

    listed = client.get("/template")
    assert listed.status_code == 200
    assert listed.headers["content-type"] == "application/x-protobuf"
    assert listed.headers["vary"] == "Accept"
    summary, = pb.ListTemplatesResponse.FromString(listed.content).templates
    assert summary.template_id == record.template_id
    detail = client.get(f"/template/{record.template_id}")
    assert detail.status_code == 200
    assert detail.headers["content-type"] == "application/x-protobuf"
    assert detail.headers["vary"] == "Accept"
    assert pb.GetTemplateResponse.FromString(detail.content).template == record

    updated = _post(client, {**template_payload, "template_id": record.template_id, "name": "旧客户端更新"})
    assert updated.status_code == 200
    revised = pb.SaveTemplateResponse.FromString(updated.content).template
    assert revised.template_id == record.template_id
    assert revised.name == "旧客户端更新"
    assert client.delete(f"/template/{record.template_id}").status_code == 204
    missing = client.get(f"/template/{record.template_id}")
    assert missing.status_code == 404
    assert missing.headers["content-type"] == "application/json"
    assert pb.ListTemplatesResponse.FromString(client.get("/template").content).templates == []


def test_template_protobuf_rejects_json_and_damaged_binary(client: TestClient) -> None:
    """拒绝旧 JSON 请求及损坏的二进制消息，且不产生记录。"""
    wrong_media = client.post("/template", json={"name": "旧请求"})
    assert wrong_media.status_code == 415
    assert wrong_media.headers["content-type"] == "application/json"
    invalid = client.post(
        "/template", content=b"\xff", headers={"Content-Type": "application/x-protobuf"},
    )
    assert invalid.status_code == 400
    assert invalid.headers["content-type"] == "application/json"
    assert ParseDict(client.get("/template").json(), pb.ListTemplatesResponse()).templates == []


def test_template_protobuf_keeps_business_validation(client: TestClient, template_payload: dict) -> None:
    """校验无效效果、重名和不存在的更新 ID，确认失败不写入其他记录。"""
    invalid = _post(client, {**template_payload, "effect_ids": ["in/unknown"]})
    assert invalid.status_code == 422
    created = _post(client, template_payload)
    assert created.status_code == 201
    duplicate = _post(client, template_payload)
    assert duplicate.status_code == 409
    missing = _post(client, {**template_payload, "template_id": str(uuid4())})
    assert missing.status_code == 404
    assert len(ParseDict(client.get("/template").json(), pb.ListTemplatesResponse()).templates) == 1


def test_template_protobuf_update_preserves_identity_and_restores_defaults(
    client: TestClient, template_db: Engine, template_payload: dict,
) -> None:
    """完整更新保留 ID 和创建时间，省略可选字段后恢复业务默认值。"""
    original = ParseDict(_post(client, template_payload).json(), pb.SaveTemplateResponse()).template
    changed = _post(client, {
        "template_id": original.template_id,
        "name": "更新后的模板",
        "effect_ids": ["in/blur_in"],
        "tracks": [template_track("subtitle", subtitleIn="in/blur_in", subtitleKeywordBold=True,
                                  subtitleKeywordUnderline=True, subtitleKeywordColor="#12AB34",
                                  subtitleKeywordSize=64), template_track("title")],
    })
    assert changed.status_code == 200
    revised = ParseDict(changed.json(), pb.SaveTemplateResponse()).template
    assert revised.template_id == original.template_id
    assert revised.created_at == original.created_at
    assert revised.name == "更新后的模板"
    assert revised.description == ""
    assert revised.transition_duration_seconds == 1
    assert revised.tracks.tracks[0].editor.subtitle_in == "in/blur_in"
    assert revised.tracks.tracks[0].editor.subtitle_keyword_bold
    assert revised.tracks.tracks[0].editor.subtitle_keyword_underline
    assert not revised.tracks.tracks[0].editor.subtitle_keyword_italic
    assert revised.tracks.tracks[0].editor.subtitle_keyword_color == "#12AB34"
    assert revised.tracks.tracks[0].editor.subtitle_keyword_size == 64
    assert revised.effects[0].parameters == {"AaiMotionInEffect": "blur_in"}
    assert ParseDict(client.get(f"/template/{original.template_id}").json(), pb.GetTemplateResponse()).template == revised
    with template_db.connect() as connection:
        assert len(connection.execute(select(store.templates)).all()) == 1


def test_title_keyword_style_roundtrips_through_protobuf(client: TestClient, template_payload: dict) -> None:
    """标题关键词配置经过真实二进制保存、详情读取及服务端校验。"""
    payload = {**template_payload, "tracks": [template_track(
        titleIn="in/fade_in", titleKeywordBold=True, titleKeywordColor="#12AB34", titleKeywordSize=72,
    ), template_track("subtitle")]}
    response = _post(client, payload)
    assert response.status_code == 201
    record = ParseDict(response.json(), pb.SaveTemplateResponse()).template
    editor = record.tracks.tracks[0].editor
    assert (editor.title_keyword_bold, editor.title_keyword_color, editor.title_keyword_size) == (True, "#12AB34", 72)
    detail = ParseDict(client.get(f"/template/{record.template_id}").json(), pb.GetTemplateResponse()).template
    assert detail == record


def test_template_protobuf_invalid_effects_preserve_data(
    client: TestClient, template_payload: dict,
) -> None:
    """未知效果不能覆盖已保存的模板。"""
    original = ParseDict(_post(client, template_payload).json(), pb.SaveTemplateResponse()).template
    payload = {
        **template_payload,
        "template_id": original.template_id,
        "name": "不应保存",
        "effect_ids": ["in/unknown"],
        "tracks": [template_track(titleIn="in/unknown")],
    }
    assert _post(client, payload).status_code == 422
    assert ParseDict(client.get(f"/template/{original.template_id}").json(), pb.GetTemplateResponse()).template == original


def test_template_protobuf_rejects_loop_conflict(
    client: TestClient, template_payload: dict,
) -> None:
    """同一文字对象的循环与入场效果保持互斥。"""
    response = _post(client, {
        **template_payload,
        "effect_ids": ["loop/normal_display", "in/fade_in"],
        "tracks": [template_track("title", titleLoop="loop/normal_display", titleIn="in/fade_in")],
    })
    assert response.status_code == 422
    assert ParseDict(client.get("/template").json(), pb.ListTemplatesResponse()).templates == []


def test_remotion_tracks_roundtrip_through_protobuf(client: TestClient, template_payload: dict) -> None:
    """片段轨道经 Protobuf 保存、读取后保持引用、秒数起点、作用的文字对象和空 editor，IMS 对象不带 sprite_id。"""
    seed_sprite()
    tracks = [*template_payload["tracks"], remotion_track("clip-a", start=2.5, duration=3, text_target="title"),
              remotion_track("clip-b", text_target="subtitle")]
    created = _post(client, {**template_payload, "tracks": tracks})
    assert created.status_code == 201
    record = ParseDict(created.json(), pb.SaveTemplateResponse()).template
    title, subtitle, *clips = record.tracks.tracks
    assert title.sprite_id == subtitle.sprite_id == ""
    assert not title.HasField("text_target") and [clip.text_target for clip in clips] == ["title", "subtitle"]
    assert [(clip.id, clip.sprite_id, clip.start_mode, clip.start) for clip in clips] == [
        ("clip-a", SPRITE_ID, "seconds", 2.5), ("clip-b", SPRITE_ID, "seconds", 1),
    ]
    assert clips[0].duration == 3 and not clips[1].HasField("duration")
    assert all(clip.HasField("editor") and not clip.editor.ListFields() for clip in clips)
    detail = client.get(f"/template/{record.template_id}")
    assert ParseDict(detail.json(), pb.GetTemplateResponse()).template == record


def test_legacy_text_binding_can_replace_asset_without_replacing_ims(client: TestClient, template_payload: dict) -> None:
    """旧用途字效更新资产后仍保留独立叠加标记及原 IMS 文字，不自动迁移为替代槽位。"""
    seed_sprite()
    tracks = [*template_payload["tracks"], *[
        remotion_track(f"clip-{target}", editor={"remotion": {"businessTarget": target}})
        for target in ("title", "subtitle")
    ]]
    created = _post(client, {**template_payload, "tracks": tracks})
    assert created.status_code == 201
    original = ParseDict(created.json(), pb.SaveTemplateResponse()).template
    replacement = seed_sprite(str(uuid4()))
    for track in tracks[2:]:
        track["sprite_id"] = replacement
    updated = _post(client, {**template_payload, "template_id": original.template_id, "tracks": tracks})
    assert updated.status_code == 200
    record = ParseDict(client.get(f"/template/{original.template_id}").json(), pb.GetTemplateResponse()).template
    assert record.tracks.tracks[:2] == original.tracks.tracks[:2]
    assert [clip.editor.remotion.business_target for clip in record.tracks.tracks[2:]] == ["title", "subtitle"]
    assert all(clip.sprite_id == replacement and not clip.HasField("text_target") for clip in record.tracks.tracks[2:])


@pytest.mark.parametrize(("change", "reason"), [
    ({"sprite_id": ""}, "缺少 sprite_id"), ({"sprite_id": "not-a-uuid"}, "valid UUID"),
    ({"editor": {"title": "不允许"}}, "只能携带参数覆盖与宿主变换"), ({"editor": None}, "Field required"),
], ids=["no-sprite", "bad-sprite", "explicit-editor", "missing-editor"])
def test_invalid_remotion_tracks_return_422_without_writing(
    client: TestClient, template_payload: dict, change: dict, reason: str,
) -> None:
    """片段轨道缺少引用、引用不是 UUID、携带编辑参数或缺少 editor 消息时返回 422 并说明原因，且不写库。"""
    tracks = [*template_payload["tracks"], remotion_track("clip-a", **change)]
    response = _post(client, {**template_payload, "tracks": tracks})
    assert response.status_code == 422 and reason in response.text
    assert ParseDict(client.get("/template").json(), pb.ListTemplatesResponse()).templates == []


def test_remotion_tracks_must_reference_published_sprites(client: TestClient, template_payload: dict) -> None:
    """引用未发布的 Sprite 返回 422 且不写库；发布后同一个 Sprite 摆放两次可以保存，更新时引用新的未发布 Sprite 仍被拒绝。"""
    tracks = [*template_payload["tracks"], remotion_track("clip-a", text_target="title"), remotion_track("clip-b", start=2, text_target="subtitle")]
    rejected = _post(client, {**template_payload, "tracks": tracks})
    assert rejected.status_code == 422 and SPRITE_ID in rejected.text
    assert ParseDict(client.get("/template").json(), pb.ListTemplatesResponse()).templates == []
    seed_sprite()
    created = _post(client, {**template_payload, "tracks": tracks})
    assert created.status_code == 201
    unknown = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    template_id = ParseDict(created.json(), pb.SaveTemplateResponse()).template.template_id
    updated = _post(client, {**template_payload, "template_id": template_id, "tracks": [*tracks, remotion_track("clip-c", sprite_id=unknown)]})
    assert updated.status_code == 422 and unknown in updated.text


def test_templates_without_clips_never_touch_the_sprite_table(client: TestClient, template_payload: dict, template_db: Engine) -> None:
    """普通模板的保存不查询也不创建 Sprite 表：检查只在模板含 Remotion 片段时发生。"""
    assert _post(client, template_payload).status_code == 201
    assert not inspect(template_db).has_table("remotion_sprites")


def test_text_and_clips_template_roundtrips_over_http(client: TestClient, template_payload: dict) -> None:
    """固定文字与已发布片段采用空效果列表，支持创建、读取、列表和更新。"""
    seed_sprite()
    texts = [template_track("title"), template_track("subtitle")]
    payload = {**template_payload, "effect_ids": [], "tracks": [*texts, remotion_track("clip-a", start=1.5, text_target="title")]}
    created = _post(client, payload)
    assert created.status_code == 201
    record = ParseDict(created.json(), pb.SaveTemplateResponse()).template
    assert list(record.effect_ids) == [] and list(record.effects) == []
    assert [(item.id, item.target, item.sprite_id, item.start) for item in record.tracks.tracks[2:]] == [("clip-a", "remotion", SPRITE_ID, 1.5)]
    detail = ParseDict(client.get(f"/template/{record.template_id}").json(), pb.GetTemplateResponse()).template
    assert detail == record
    listed = ParseDict(client.get("/template").json(), pb.ListTemplatesResponse()).templates
    assert [item.template_id for item in listed] == [record.template_id]
    updated = _post(client, {**payload, "template_id": record.template_id, "tracks": [*texts, remotion_track("clip-a", start=4, text_target="title"), remotion_track("clip-b", start=6, text_target="subtitle")]})
    assert updated.status_code == 200
    again = ParseDict(client.get(f"/template/{record.template_id}").json(), pb.GetTemplateResponse()).template
    assert [(item.id, item.start) for item in again.tracks.tracks[2:]] == [("clip-a", 4), ("clip-b", 6)]
    assert list(again.effect_ids) == []


def test_remotion_tracks_must_share_one_canvas(client: TestClient, template_payload: dict) -> None:
    """叠加渲染要求片段画布一致：宽高或帧率不同的 Sprite 放进同一个模板时保存即 422 并列出各画布，且不写库；画布相同的不同 Sprite 可以一起保存。"""
    same, landscape, other_fps = (str(uuid4()) for _ in range(3))
    # 滤镜不占用模板对象，同一模板可以摆放多个。
    filter_kind = 2
    seed_sprite(SPRITE_ID, kind=filter_kind)
    seed_sprite(same, kind=filter_kind)
    seed_sprite(landscape, (1920, 1080, 30), filter_kind)
    seed_sprite(other_fps, (1080, 1920, 60), filter_kind)
    clips = lambda *ids: [template_track("title"), template_track("subtitle"), *[remotion_track(f"clip-{index}", sprite_id=item) for index, item in enumerate(ids)]]
    for mismatched in ((SPRITE_ID, landscape), (SPRITE_ID, other_fps)):
        rejected = _post(client, {**template_payload, "effect_ids": [], "tracks": clips(*mismatched)})
        assert rejected.status_code == 422
        assert "1080x1920@30" in rejected.text and ("1920x1080@30" in rejected.text or "1080x1920@60" in rejected.text)
    assert ParseDict(client.get("/template").json(), pb.ListTemplatesResponse()).templates == []
    created = _post(client, {**template_payload, "effect_ids": [], "tracks": clips(SPRITE_ID, same, SPRITE_ID)})
    assert created.status_code == 201
    template_id = ParseDict(created.json(), pb.SaveTemplateResponse()).template.template_id
    updated = _post(client, {**template_payload, "template_id": template_id, "effect_ids": [], "tracks": clips(SPRITE_ID, landscape)})
    assert updated.status_code == 422


@pytest.mark.parametrize(("kind", "slot", "reason"), [
    (1, {}, "须作用于标题或底部字幕"), (2, {"text_target": "title"}, "不是字效，不能作用于文字"),
    (1, {"editor": {"remotion": {"businessTarget": "decoration"}}}, "须作用于标题或底部字幕"),
    (1, {"editor": {"remotion": {"businessTarget": "title", "parameters": [{"key": "text", "stringValue": "旧文字"}]}}}, "不能覆盖 text"),
    (1, {"editor": {"remotion": {"businessTarget": "subtitle", "parameters": [{"key": "keywords", "stringListValue": {"values": ["新品"]}}]}}}, "不能覆盖 keywords"),
    (2, {"editor": {"remotion": {"businessTarget": "title"}}}, "不是字效，不能作用于文字"),
    (3, {"editor": {"remotion": {"businessTarget": "subtitle"}}}, "不是字效，不能作用于文字"),
    (4, {}, "须指定视频交界处"), (3, {"transition_boundary": 1}, "不是转场，不能占用视频交界处"),
    (1, {"text_target": "title", "editor": {"remotion": {"parameters": [{"key": "text", "stringValue": "旧文字"}]}}}, "不能覆盖 text"),
    (1, {"text_target": "subtitle", "editor": {"remotion": {"parameters": [{"key": "keywords", "stringListValue": {"values": ["新品"]}}]}}},
     "不能覆盖 keywords"),
], ids=["text-unbound", "filter-on-text", "legacy-decoration", "legacy-text-override", "legacy-keywords-override", "legacy-title-on-filter",
         "legacy-subtitle-on-effect", "transition-unbound", "effect-on-boundary", "bound-text-override", "bound-keywords-override"])
def test_remotion_slot_must_match_sprite_kind(client: TestClient, template_payload: dict, kind: int, slot: dict, reason: str) -> None:
    """片段按 Sprite 分类占用模板对象：字效作用于文字且不覆盖文字与关键词、转场占用交界处，其他分类两者都不占用；不符时 422 且不写库。"""
    seed_sprite(kind=kind)
    response = _post(client, {**template_payload, "tracks": [*template_payload["tracks"], remotion_track("clip-a", **slot)]})
    assert response.status_code == 422 and reason in response.json()["detail"]
    assert ParseDict(client.get("/template").json(), pb.ListTemplatesResponse()).templates == []


def test_remotion_transitions_and_effects_share_ims_limits(client: TestClient, template_payload: dict) -> None:
    """Remotion 转场与 IMS 转场共用两个交界处，Remotion 视频特效与 IMS VFX 合计最多一个；不冲突时一起保存，交界处随模板读回。"""
    from server.template.schema import effect_catalog

    transition, effect = str(uuid4()), str(uuid4())
    seed_sprite(transition, kind=4)
    seed_sprite(effect, kind=3)
    ims_transition = next(item for item in effect_catalog().values() if item.category == "transition/normal")
    ims_vfx = next(item for item in effect_catalog().values() if item.category == "vfx/normal")
    clip = remotion_track("remotion-transition", sprite_id=transition, transition_boundary=1)
    tracks = [*template_payload["tracks"], {**template_track("transition", transition=ims_transition.id), "id": "ims-transition", "transition_boundary": 1}, clip]
    payload = {**template_payload, "effect_ids": [*template_payload["effect_ids"], ims_transition.id], "tracks": tracks}
    rejected = _post(client, payload)
    assert rejected.status_code == 422 and "转场列表序号不能重复" in rejected.text
    clip["transition_boundary"] = 2
    created = _post(client, payload)
    assert created.status_code == 201
    record = ParseDict(created.json(), pb.SaveTemplateResponse()).template
    assert [(track.id, track.transition_boundary) for track in record.tracks.tracks[2:]] == [("ims-transition", 1), ("remotion-transition", 2)]
    with_vfx = {**payload, "template_id": record.template_id, "effect_ids": [*payload["effect_ids"], ims_vfx.id],
                "tracks": [*tracks, template_track("vfx", vfx=ims_vfx.id), remotion_track("remotion-effect", sprite_id=effect)]}
    rejected = _post(client, with_vfx)
    assert rejected.status_code == 422 and rejected.json()["detail"] == "VFX 最多一个，Remotion 视频特效也占用这个名额"
    assert ParseDict(client.get(f"/template/{record.template_id}").json(), pb.GetTemplateResponse()).template == record


@pytest.mark.parametrize("missing", [("title",), ("subtitle",), ("title", "subtitle")])
def test_missing_fixed_text_rejected_on_create_and_update(client: TestClient, template_payload: dict, missing) -> None:
    """缺少固定文字时创建和更新都返回 422，更新失败完整保留已有记录。"""
    payload = {**template_payload, "effect_ids": [], "tracks": [template_track("title"), template_track("subtitle")]}
    invalid = {**payload, "tracks": [track for track in payload["tracks"] if track["target"] not in missing]}
    rejected = _post(client, invalid)
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "保存模板需要标题和底部字幕各一个"
    assert ParseDict(client.get("/template").json(), pb.ListTemplatesResponse()).templates == []
    created = _post(client, payload)
    assert created.status_code == 201
    record = ParseDict(created.json(), pb.SaveTemplateResponse()).template
    rejected = _post(client, {**invalid, "template_id": record.template_id})
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "保存模板需要标题和底部字幕各一个"
    detail = client.get(f"/template/{record.template_id}")
    assert detail.status_code == 200
    assert ParseDict(detail.json(), pb.GetTemplateResponse()).template == record


@pytest.mark.parametrize("field,value", [
    ("name", " "), ("tracks", []),
])
def test_template_protobuf_rejects_invalid_fields(
    client: TestClient, template_payload: dict, field: str, value: object,
) -> None:
    """空白名称和空对象列表按业务规则返回校验错误。"""
    response = _post(client, {**template_payload, field: value})
    assert response.status_code == 422
    assert ParseDict(client.get("/template").json(), pb.ListTemplatesResponse()).templates == []


@pytest.mark.parametrize("size", [11, 301])
def test_template_protobuf_rejects_font_size(client: TestClient, template_payload: dict, size: int) -> None:
    """字号必须处于允许区间，失败请求不建立模板。"""
    template_payload["tracks"][0]["editor"]["titleSize"] = size
    assert _post(client, template_payload).status_code == 422
    assert ParseDict(client.get("/template").json(), pb.ListTemplatesResponse()).templates == []


def test_template_protobuf_requires_tracks_message(client: TestClient) -> None:
    """省略必填的 TrackList 时返回业务校验错误。"""
    message = pb.SaveTemplateRequest(name="测试", effect_ids=["in/fade_in"])
    response = client.post(
        "/template", content=message.SerializeToString(),
        headers={"Content-Type": "application/x-protobuf"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/json"
    assert any(error["loc"] == ["body", "tracks"] for error in response.json()["detail"])


def test_remotion_overrides_and_transform_roundtrip_through_protobuf(client: TestClient, template_payload: dict, template_db: Engine) -> None:
    """片段的参数覆盖与宿主变换随模板保存、读回不变，库里的配置以驼峰 JSON 存在 editor.remotion 下。"""
    # 视频特效不作用于文字，可以覆盖 text 与 keywords；字效的这两个参数取自所作用的对象。
    seed_sprite(kind=3)
    remotion = {
        "parameters": [
            {"key": "text", "stringValue": "新品上市"},
            {"key": "fontWeight", "numberValue": 700},
            {"key": "keywords", "stringListValue": {"values": ["新品"]}},
        ],
        "transform": {"x": 540, "y": 300, "scale": 1.5, "opacity": 0.8},
    }
    created = _post(client, {**template_payload, "tracks": [*template_payload["tracks"], remotion_track("clip-a", editor={"remotion": remotion})]})
    assert created.status_code == 201
    record = ParseDict(created.json(), pb.SaveTemplateResponse()).template
    clip = record.tracks.tracks[2].editor.remotion
    assert [item.key for item in clip.parameters] == ["text", "fontWeight", "keywords"]
    assert clip.parameters[0].string_value == "新品上市" and clip.parameters[1].number_value == 700
    assert list(clip.parameters[2].string_list_value.values) == ["新品"]
    assert (clip.transform.x, clip.transform.y, clip.transform.scale, clip.transform.opacity) == (540, 300, 1.5, 0.8)
    assert not clip.transform.HasField("rotation")
    with template_db.connect() as connection:
        stored = connection.exec_driver_sql("SELECT configuration FROM templates").scalar_one()
    stored = stored if isinstance(stored, dict) else json.loads(stored)
    assert stored["tracks"][2]["editor"] == {"remotion": {
        "parameters": [
            {"key": "text", "stringValue": "新品上市"}, {"key": "fontWeight", "numberValue": 700.0},
            {"key": "keywords", "stringListValue": {"values": ["新品"]}},
        ],
        "transform": {"x": 540.0, "y": 300.0, "scale": 1.5, "opacity": 0.8},
    }}
    assert ParseDict(client.get(f"/template/{record.template_id}").json(), pb.GetTemplateResponse()).template == record


@pytest.mark.parametrize(("remotion", "reason"), [
    ({"parameters": [{"key": "fontSize", "numberValue": 999}]}, "Remotion 片段「clip-a」：参数 fontSize 超出允许范围"),
    ({"parameters": [{"key": "glow", "numberValue": 0.1}]}, "参数 glow 不可修改"),
    ({"transform": {"rotation": 270}}, "rotation 超出允许范围"),
], ids=["out-of-range", "read-only", "transform"])
def test_remotion_overrides_outside_the_contract_return_422_without_writing(
    client: TestClient, template_payload: dict, remotion: dict, reason: str,
) -> None:
    """不符合所引用 Sprite 参数契约的覆盖或变换返回 422，说明片段与原因，且不写库。"""
    seed_sprite()
    tracks = [*template_payload["tracks"], remotion_track("clip-a", editor={"remotion": remotion})]
    response = _post(client, {**template_payload, "tracks": tracks})
    assert response.status_code == 422 and reason in response.json()["detail"]
    assert ParseDict(client.get("/template").json(), pb.ListTemplatesResponse()).templates == []


def test_only_remotion_tracks_carry_remotion_overrides(client: TestClient, template_payload: dict) -> None:
    """IMS 对象的 editor 不能带 Remotion 参数覆盖。"""
    track = {**template_payload["tracks"][0]}
    track["editor"] = {**track["editor"], "remotion": {"transform": {"opacity": 0.5}}}
    response = _post(client, {**template_payload, "tracks": [track]})
    assert response.status_code == 422 and "只有 Remotion 片段可以设置参数覆盖与宿主变换" in response.text
