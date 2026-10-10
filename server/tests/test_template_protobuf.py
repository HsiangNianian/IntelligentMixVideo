# 使用临时 SQLite 和真实 FastAPI 路由验证模板 Protobuf 通信；执行 uv run pytest tests/test_template_protobuf.py。

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from generated.imv.template.v1 import template_pb2 as pb
from sqlalchemy import Engine, inspect, select

from server.template import store
from .conftest import template_track
from .template_wire import post_template as _post


@pytest.mark.parametrize(("path", "method", "status"), [
    ("/template", "get", "200"),
    ("/template", "post", "200"),
    ("/template", "post", "201"),
    ("/template/{template_id}", "get", "200"),
])
def test_template_openapi_documents_binary_responses(
    client: TestClient, path: str, method: str, status: str,
) -> None:
    """每种模板读取与保存结果都声明唯一的 Protobuf 二进制成功响应。"""
    operation = client.get("/openapi.json").json()["paths"][path][method]
    assert operation["responses"][status]["content"] == {
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


def test_template_protobuf_create_read_update_delete(
    client: TestClient, template_db: Engine, template_payload: dict,
) -> None:
    """验证四个原有地址、二进制响应、业务字段和持久化副作用。"""
    assert not inspect(template_db).has_table("templates")
    empty = client.get("/template")
    assert empty.status_code == 200
    assert empty.headers["content-type"] == "application/x-protobuf"
    assert pb.ListTemplatesResponse.FromString(empty.content).templates == []

    created = _post(client, template_payload)
    assert created.status_code == 201
    assert created.headers["content-type"] == "application/x-protobuf"
    record = pb.SaveTemplateResponse.FromString(created.content).template
    assert record.name == "测试模板"
    assert record.effect_ids == ["in/fade_in"]
    assert record.tracks.tracks[0].editor.title_in == "in/fade_in"
    assert record.created_at.ToDatetime(tzinfo=UTC) <= datetime.now(UTC)
    assert inspect(template_db).has_table("templates")

    detail = client.get(f"/template/{record.template_id}")
    assert detail.status_code == 200
    assert detail.headers["content-type"] == "application/x-protobuf"
    assert pb.GetTemplateResponse.FromString(detail.content).template == record
    listed = client.get("/template")
    assert listed.headers["content-type"] == "application/x-protobuf"
    assert pb.ListTemplatesResponse.FromString(listed.content).templates == [record]

    updated = _post(client, {**template_payload, "template_id": record.template_id, "name": "修改后的模板"})
    assert updated.status_code == 200
    assert updated.headers["content-type"] == "application/x-protobuf"
    revised = pb.SaveTemplateResponse.FromString(updated.content).template
    assert revised.template_id == record.template_id
    assert revised.name == "修改后的模板"

    deleted = client.delete(f"/template/{record.template_id}")
    assert deleted.status_code == 204
    assert deleted.content == b""
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
    assert pb.ListTemplatesResponse.FromString(client.get("/template").content).templates == []


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
    assert len(pb.ListTemplatesResponse.FromString(client.get("/template").content).templates) == 1


def test_template_protobuf_update_preserves_identity_and_restores_defaults(
    client: TestClient, template_db: Engine, template_payload: dict,
) -> None:
    """完整更新保留 ID 和创建时间，省略可选字段后恢复业务默认值。"""
    original = pb.SaveTemplateResponse.FromString(_post(client, template_payload).content).template
    changed = _post(client, {
        "template_id": original.template_id,
        "name": "更新后的模板",
        "effect_ids": ["in/blur_in"],
        "tracks": [template_track("subtitle", subtitleIn="in/blur_in", subtitleKeywordBold=True,
                                  subtitleKeywordUnderline=True, subtitleKeywordColor="#12AB34",
                                  subtitleKeywordSize=64)],
    })
    assert changed.status_code == 200
    revised = pb.SaveTemplateResponse.FromString(changed.content).template
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
    assert pb.GetTemplateResponse.FromString(
        client.get(f"/template/{original.template_id}").content,
    ).template == revised
    with template_db.connect() as connection:
        assert len(connection.execute(select(store.templates)).all()) == 1


def test_title_keyword_style_roundtrips_through_protobuf(client: TestClient, template_payload: dict) -> None:
    """标题关键词配置经过真实二进制保存、详情读取及服务端校验。"""
    payload = {**template_payload, "tracks": [template_track(
        titleIn="in/fade_in", titleKeywordBold=True, titleKeywordColor="#12AB34", titleKeywordSize=72,
    )]}
    response = _post(client, payload)
    assert response.status_code == 201
    record = pb.SaveTemplateResponse.FromString(response.content).template
    editor = record.tracks.tracks[0].editor
    assert (editor.title_keyword_bold, editor.title_keyword_color, editor.title_keyword_size) == (True, "#12AB34", 72)
    detail = pb.GetTemplateResponse.FromString(
        client.get(f"/template/{record.template_id}").content,
    ).template
    assert detail == record


def test_template_protobuf_invalid_effects_preserve_data(
    client: TestClient, template_payload: dict,
) -> None:
    """未知效果不能覆盖已保存的模板。"""
    original = pb.SaveTemplateResponse.FromString(_post(client, template_payload).content).template
    payload = {
        **template_payload,
        "template_id": original.template_id,
        "name": "不应保存",
        "effect_ids": ["in/unknown"],
        "tracks": [template_track(titleIn="in/unknown")],
    }
    assert _post(client, payload).status_code == 422
    assert pb.GetTemplateResponse.FromString(
        client.get(f"/template/{original.template_id}").content,
    ).template == original


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
    assert pb.ListTemplatesResponse.FromString(client.get("/template").content).templates == []


def test_template_without_effects_can_be_saved_and_read_back(client: TestClient, template_payload: dict) -> None:
    """只放 Remotion 片段的模板没有 IMS 效果：空效果与空对象一致时可以创建、读取并出现在列表中。"""
    created = _post(client, {**template_payload, "effect_ids": [], "tracks": []})
    assert created.status_code == 201
    record = pb.SaveTemplateResponse.FromString(created.content).template
    assert list(record.effect_ids) == [] and list(record.effects) == [] and list(record.tracks.tracks) == []
    detail = client.get(f"/template/{record.template_id}")
    assert detail.status_code == 200
    assert list(pb.GetTemplateResponse.FromString(detail.content).template.effect_ids) == []
    listed = pb.ListTemplatesResponse.FromString(client.get("/template").content).templates
    assert [item.template_id for item in listed] == [record.template_id]


@pytest.mark.parametrize("field,value", [
    ("name", " "), ("tracks", []),
])
def test_template_protobuf_rejects_invalid_fields(
    client: TestClient, template_payload: dict, field: str, value: object,
) -> None:
    """空白名称和空对象列表按业务规则返回校验错误。"""
    response = _post(client, {**template_payload, field: value})
    assert response.status_code == 422
    assert pb.ListTemplatesResponse.FromString(client.get("/template").content).templates == []


@pytest.mark.parametrize("size", [11, 301])
def test_template_protobuf_rejects_font_size(client: TestClient, template_payload: dict, size: int) -> None:
    """字号必须处于允许区间，失败请求不建立模板。"""
    template_payload["tracks"][0]["editor"]["titleSize"] = size
    assert _post(client, template_payload).status_code == 422
    assert pb.ListTemplatesResponse.FromString(client.get("/template").content).templates == []


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
