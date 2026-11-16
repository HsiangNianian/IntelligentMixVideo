"""在临时数据库与内存对象存储中验证模板 PNG 发布与恢复，运行 pytest server/tests/test_template_covers.py。"""

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock

from botocore.exceptions import EndpointConnectionError
from PIL import Image

from server.template import covers
from .template_wire import post_template


def png(color: str) -> bytes:
    """生成有明确像素的竖屏测试封面。"""
    image = BytesIO()
    Image.new("RGB", (90, 160), color).save(image, format="PNG")
    return image.getvalue()


def test_publish_retry_replace_and_revalidate(client, template_payload, monkeypatch):
    """上传失败保留模板，重试和换视频复用对象名；改元数据、过期请求和条件 GET 不触碰图片。"""
    storage = MagicMock()
    monkeypatch.setattr(covers, "storage_client", lambda *_: storage)
    monkeypatch.setattr(covers, "ZosSettings", lambda: SimpleNamespace(zos_bucket="test", zos_delete_objects=True, zos_web_url="https://images.test"))
    created = post_template(client, {**template_payload, "cover_source": "builtin"})
    assert created.status_code == 201
    record = created.json()["template"]
    template_id = record["templateId"]
    base = f"/template/{template_id}/cover"
    revision = record["cover"]["revision"]
    red = {"file": ("cover.png", png("red"), "image/png")}
    put = f"{base}/{revision}"
    storage.put_object.side_effect = EndpointConnectionError(endpoint_url="test")
    assert client.put(put, files=red).status_code == 503
    assert client.get("/template").json()["templates"][0]["cover"]["publishedRevision"] == ""
    assert client.get(base).status_code == 404
    storage.put_object.side_effect = None
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
    storage.get_object.return_value = {"Body": BytesIO(uploaded["Body"])}
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
    storage.get_object.return_value = {"Body": BytesIO(uploaded["Body"])}
    refreshed = client.get(base, headers={"If-None-Match": cached.headers["etag"]})
    assert refreshed.status_code == 200 and refreshed.headers["etag"] != cached.headers["etag"]
    with Image.open(BytesIO(refreshed.content)) as image:
        assert image.getpixel((10, 10)) == (0, 0, 255)
    assert client.delete(f"/template/{template_id}").status_code == 204
    storage.delete_object.assert_called_once_with(Bucket="test", Key=key)


def test_backfill_keeps_template_order_and_marks_visual_edits(client, template_payload, monkeypatch):
    """旧模板按 ID 补图，保留更新时间；以后改画面即使旧客户端未传额外字段也生成新版本。"""
    record = post_template(client, template_payload).json()["template"]
    template_id = record["templateId"]
    assert "cover" not in record
    monkeypatch.setattr("server.template.store.upload_cover", lambda key, content: f"https://images.test/{key}.png")
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


def test_desktop_can_preflight_cover_put(client):
    """Tauri 主窗口跨域上传 PNG 时，服务端允许浏览器先检查 PUT。"""
    response = client.options("/template/00000000-0000-4000-8000-000000000000/cover/00000000-0000-4000-8000-000000000000", headers={
        "Origin": "tauri://localhost", "Access-Control-Request-Method": "PUT",
    })
    assert response.status_code == 200
    assert "PUT" in response.headers["access-control-allow-methods"]
