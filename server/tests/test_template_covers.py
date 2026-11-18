"""在临时数据库与内存对象存储中验证模板 PNG 发布与恢复，运行 pytest server/tests/test_template_covers.py。"""

import sqlite3
from io import BytesIO
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from PIL import Image

from .template_wire import post_template


def png(color: str) -> bytes:
    """生成有明确像素的竖屏测试封面。"""
    image = BytesIO()
    Image.new("RGB", (90, 160), color).save(image, format="PNG")
    return image.getvalue()


def test_publish_retry_replace_and_revalidate(client, template_payload, cover_storage):
    """上传失败保留模板，重试和换视频复用对象名；改元数据、过期请求和条件 GET 不触碰图片。"""
    storage = cover_storage
    created = post_template(client, {**template_payload, "cover_source": "builtin"})
    assert created.status_code == 201
    record = created.json()["template"]
    template_id = record["templateId"]
    base = f"/template/{template_id}/cover"
    revision = record["cover"]["revision"]
    red = {"file": ("cover.png", png("red"), "image/png")}
    put = f"{base}/{revision}"
    write = storage.put_object.side_effect
    storage.put_object.side_effect = EndpointConnectionError(endpoint_url="test")
    assert client.put(put, files=red).status_code == 503
    assert client.get("/template").json()["templates"][0]["cover"]["publishedRevision"] == ""
    assert client.get(base).status_code == 404
    storage.put_object.side_effect = write
    assert client.put(put, files={"file": ("fake.png", b"fake", "image/png")}).status_code == 422
    assert client.put(put, files=red).status_code == 200
    saved = client.get(f"/template/{template_id}").json()["template"]
    key = f"imv/template-covers/{template_id}.png"
    assert saved["cover"] == {"url": "https://images.test/" + key, "source": "builtin",
                              "revision": revision, "publishedRevision": revision}
    uploaded = storage.put_object.call_args.kwargs
    assert uploaded["Key"] == key and uploaded["ContentType"] == "image/png"
    assert uploaded["CacheControl"] == "no-cache"
    with Image.open(BytesIO(uploaded["Body"])) as image:
        assert image.format == "PNG" and image.getpixel((10, 10)) == (255, 0, 0)
    cached = client.get(base)
    assert cached.status_code == 200 and cached.content == uploaded["Body"]
    assert cached.headers["content-type"] == "image/png"
    assert client.get(base, headers={"If-None-Match": cached.headers["etag"]}).status_code == 304
    assert storage.get_object.call_count == 1
    assert client.put(put, files=red).status_code == 200
    edited = {**template_payload, "template_id": template_id, "name": "新名字", "description": "新说明"}
    assert post_template(client, edited).json()["template"]["cover"] == saved["cover"]
    changed = post_template(client, {**edited, "cover_source": "1" * 64}).json()["template"]["cover"]
    assert changed["revision"] != revision and changed["publishedRevision"] == revision
    assert client.put(put, files=red).status_code == 409
    assert storage.put_object.call_count == 2
    latest = client.put(f"{base}/{changed['revision']}", files={"file": ("cover.png", png("blue"), "image/png")})
    assert latest.status_code == 200 and latest.json()["url"] == saved["cover"]["url"]
    uploaded = storage.put_object.call_args.kwargs
    assert uploaded["Key"] == key
    refreshed = client.get(base, headers={"If-None-Match": cached.headers["etag"]})
    assert refreshed.status_code == 200 and refreshed.headers["etag"] != cached.headers["etag"]
    with Image.open(BytesIO(refreshed.content)) as image:
        assert image.getpixel((10, 10)) == (0, 0, 255)
    assert client.delete(f"/template/{template_id}").status_code == 204
    storage.delete_object.assert_called_once_with(Bucket="test", Key=key)


def test_missing_published_object_can_be_repaired(client, template_payload, cover_storage):
    """数据库保留发布记录时，只有对象缺失允许补图；强制写回同一键后可读取新的 PNG。"""
    storage = cover_storage
    read = storage.get_object.side_effect
    row = post_template(client, {**template_payload, "cover_source": "builtin"}).json()["template"]
    base = f"/template/{row['templateId']}/cover"
    put = f"{base}/{row['cover']['revision']}"
    files = {"file": ("cover.png", png("green"), "image/png")}
    published = client.put(put, files=files).json()
    for code in ("NoSuchBucket", "AccessDenied"):
        storage.get_object.side_effect = ClientError({"Error": {"Code": code}}, "GetObject")
        assert client.get(base).status_code == 503
    storage.get_object.side_effect = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
    missing = client.get(base)
    assert missing.status_code == 404 and missing.json()["detail"] == {"code": "cover_object_missing"}
    assert client.get("/template").json()["templates"][0]["cover"]["publishedRevision"] == published["revision"]
    repaired = client.put(put, files=files, data={"refresh": "true"})
    assert repaired.status_code == 200 and repaired.json()["revision"] != published["revision"]
    assert repaired.json()["url"] == published["url"] and storage.put_object.call_count == 2
    uploaded = storage.put_object.call_args.kwargs
    storage.get_object.side_effect = read
    restored = client.get(base)
    assert restored.status_code == 200 and restored.content == uploaded["Body"]
    assert storage.get_object.call_args.kwargs["Key"] == uploaded["Key"]


@pytest.mark.parametrize("failure", ["update", "commit_rolled_back", "commit_ack_lost", "rollback_missing_object", "newer_publish"])
def test_failed_publication_keeps_bytes_and_etag_together(client, template_db, template_payload, cover_storage, monkeypatch, failure):
    """数据库拒绝更新或提交回滚时保留旧图；已提交但回执丢失时确认新图，对象原先缺失则恢复缺失状态。"""
    record = post_template(client, {**template_payload, "cover_source": "builtin"}).json()["template"]
    base = f"/template/{record['templateId']}/cover"
    old_revision = record["cover"]["revision"]
    put = f"{base}/{old_revision}"
    assert client.put(put, files={"file": ("old.png", png("red"), "image/png")}).status_code == 200
    before = client.get(base)
    if failure == "rollback_missing_object":
        cover_storage.delete_object(Key=cover_storage.put_object.call_args.kwargs["Key"])
    if failure == "update":
        with template_db.begin() as connection:
            connection.exec_driver_sql("CREATE TRIGGER reject_cover BEFORE UPDATE ON templates "
                                       "BEGIN SELECT RAISE(ABORT, 'test update rejected'); END")
    else:
        commit = template_db.dialect.do_commit

        def fail_commit(connection):
            """分别模拟数据库已接受提交和明确回滚，两种情况客户端都收不到提交回执。"""
            if failure == "commit_ack_lost":
                commit(connection)
            else:
                connection.rollback()
            if failure == "newer_publish":
                # 在失败请求重新锁行之前，另一客户端保存并成功发布了一张绿图。
                monkeypatch.setattr(template_db.dialect, "do_commit", commit)
                edit = post_template(client, {**template_payload, "template_id": record["templateId"], "cover_source": "a" * 64})
                revision = edit.json()["template"]["cover"]["revision"]
                assert client.put(f"{base}/{revision}", files={"file": ("later.png", png("green"), "image/png")}).status_code == 200
            raise sqlite3.OperationalError("test commit acknowledgement lost")

        monkeypatch.setattr(template_db.dialect, "do_commit", fail_commit)
    response = client.put(put, files={"file": ("new.png", png("blue"), "image/png")}, data={"refresh": "true"})
    cover = client.get(f"/template/{record['templateId']}").json()["template"]["cover"]
    served = client.get(base)
    cached = client.get(base, headers={"If-None-Match": before.headers["etag"]})
    if failure == "commit_ack_lost":
        assert response.status_code == 200 and response.json()["revision"] == cover["publishedRevision"] != old_revision
        assert cached.status_code == 200 and served.headers["etag"] != before.headers["etag"]
        assert Image.open(BytesIO(served.content)).getpixel((1, 1)) == (0, 0, 255)
    elif failure == "newer_publish":
        assert response.status_code == 503 and cached.status_code == 200
        assert served.headers["etag"] == f'"{cover["publishedRevision"]}"' != before.headers["etag"]
        assert Image.open(BytesIO(served.content)).getpixel((1, 1)) == (0, 128, 0)
    else:
        assert response.status_code == 503 and cover["publishedRevision"] == old_revision
        assert cached.status_code == 304
        if failure == "rollback_missing_object":
            assert served.status_code == 404 and served.json()["detail"]["code"] == "cover_object_missing"
        else:
            assert served.headers["etag"] == before.headers["etag"] and served.content == before.content
        if failure == "update":
            assert cover_storage.put_object.call_count == 1


def test_backfill_keeps_template_order_and_marks_visual_edits(client, template_payload, cover_storage):
    """旧模板按 ID 补图，保留更新时间；以后改画面即使旧客户端未传额外字段也生成新版本。"""
    record = post_template(client, template_payload).json()["template"]
    template_id = record["templateId"]
    assert "cover" not in record
    response = client.put(f"/template/{template_id}/cover/{template_id}", files={"file": ("old.png", png("red"), "image/png")})
    assert response.status_code == 200
    updated = client.get("/template").json()["templates"][0]
    assert updated["updatedAt"] == record["updatedAt"]
    assert updated["cover"]["source"] == "builtin" and updated["cover"]["publishedRevision"] == template_id
    template_payload["tracks"][0]["editor"]["title"] = "新标题"
    edited = post_template(client, {**template_payload, "template_id": template_id}).json()["template"]
    assert edited["cover"]["revision"] != template_id and edited["cover"]["publishedRevision"] == template_id


def test_single_column_migrates_previous_cover_metadata(client, template_db, template_payload, monkeypatch):
    """模拟旧库升级，单列保留成功地址、待更新版本和排序，下一次启动不会覆盖新数据。"""
    from uuid import uuid4
    from sqlalchemy import inspect, select
    from server.template import store

    record = post_template(client, template_payload).json()["template"]
    key = record["templateId"]
    awaiting = str(uuid4())
    assert "template_covers" not in inspect(template_db).get_table_names()
    with template_db.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE templates DROP COLUMN cover")
        connection.exec_driver_sql("CREATE TABLE template_covers (template_id TEXT, revision TEXT, source TEXT, published_revision TEXT, url TEXT)")
        connection.exec_driver_sql("INSERT INTO template_covers VALUES (?, ?, ?, ?, ?)", (key, awaiting, "builtin", key, "https://images.test/old.png"))
    monkeypatch.setattr(store, "_ready_engine", None)
    saved = client.get(f"/template/{key}").json()["template"]
    assert saved["updatedAt"] == record["updatedAt"]
    assert saved["cover"] == {"revision": awaiting, "publishedRevision": key, "source": "builtin", "url": "https://images.test/old.png"}
    changed = post_template(client, {**template_payload, "template_id": key, "cover_source": "1" * 64}).json()["template"]
    monkeypatch.setattr(store, "_ready_engine", None)
    assert client.get(f"/template/{key}").json()["template"]["cover"] == changed["cover"]
    with template_db.connect() as connection:
        assert connection.scalar(select(store.templates.c.cover))["revision"] == changed["cover"]["revision"]


def test_startup_skips_template_backfill(template_db, monkeypatch):
    """API 启动和关闭均不扫描模板库，封面渲染不依赖服务进程的 PATH。"""
    from fastapi.testclient import TestClient
    from server.app import app

    scan = MagicMock(return_value=[])
    monkeypatch.setattr("server.template.store.list_templates", scan)
    monkeypatch.setenv("PATH", "")
    with TestClient(app) as service:
        assert service.get("/").status_code == 200
    scan.assert_not_called()


def test_clients_backfill_legacy_once(client, template_payload, cover_storage):
    """两台客户端首批补同一旧模板时，只发布一次，目标版本推进且不改变排序。"""
    old = post_template(client, template_payload).json()["template"]
    key = old["templateId"]
    put = f"/template/{key}/cover/{key}"
    files = {"file": ("cover.png", png("green"), "image/png")}
    published = client.put(put, files=files, data={"refresh": "true"})
    assert published.status_code == 200
    cover = published.json()
    assert cover["revision"] == cover["published_revision"] != key
    assert client.put(put, files=files, data={"refresh": "true"}).json() == cover
    assert cover_storage.put_object.call_count == 1
    row = client.get("/template").json()["templates"][0]
    assert row["cover"] == {"source": "builtin", "url": cover["url"], "revision": cover["revision"],
                            "publishedRevision": cover["revision"]}
    assert row["updatedAt"] == old["updatedAt"]


def test_desktop_can_preflight_cover_put(client):
    """Tauri 主窗口跨域上传 PNG 时，服务端允许浏览器先检查 PUT。"""
    response = client.options("/template/00000000-0000-4000-8000-000000000000/cover/00000000-0000-4000-8000-000000000000", headers={
        "Origin": "tauri://localhost", "Access-Control-Request-Method": "PUT",
    })
    assert response.status_code == 200
    assert "PUT" in response.headers["access-control-allow-methods"]
