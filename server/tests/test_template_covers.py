"""在临时数据库与内存对象存储中验证模板 PNG 发布与恢复，运行 pytest server/tests/test_template_covers.py。"""

import sqlite3
from io import BytesIO
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, ReadTimeoutError
from PIL import Image
from sqlalchemy import select

from server.template import covers, store

from .template_wire import post_template


def png(color: str) -> bytes:
    """生成有明确像素的竖屏测试封面。"""
    image = BytesIO()
    Image.new("RGB", (90, 160), color).save(image, format="PNG")
    return image.getvalue()


def test_publish_retry_replace_and_revalidate(client, template_payload, cover_storage):
    """上传失败保留模板，换视频更新同一分享地址且读取独立的私有 PNG；条件 GET 只查版本。"""
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
    key = storage.put_object.call_args.kwargs["Key"]
    assert key.startswith(f"imv/template-covers/{template_id}/")
    assert saved["cover"] == {"url": str(client.base_url).rstrip("/") + base, "source": "builtin",
                              "revision": revision, "publishedRevision": revision}
    uploaded = storage.put_object.call_args.kwargs
    assert uploaded["ACL"] == "private" and uploaded["ContentType"] == "image/png"
    assert uploaded["CacheControl"] == "no-cache"
    with Image.open(BytesIO(uploaded["Body"])) as image:
        assert image.format == "PNG" and image.getpixel((10, 10)) == (255, 0, 0)
    cached = client.get(base)
    assert cached.status_code == 200 and cached.content == uploaded["Body"]
    assert cached.headers["content-type"] == "image/png"
    reads = storage.get_object.call_count
    assert client.get(base, headers={"If-None-Match": cached.headers["etag"]}).status_code == 304
    assert storage.get_object.call_count == reads
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
    assert uploaded["Key"] != key and uploaded["ACL"] == "private"
    refreshed = client.get(base, headers={"If-None-Match": cached.headers["etag"]})
    assert refreshed.status_code == 200 and refreshed.headers["etag"] != cached.headers["etag"]
    with Image.open(BytesIO(refreshed.content)) as image:
        assert image.getpixel((10, 10)) == (0, 0, 255)
    assert client.get(saved["cover"]["url"]).content == refreshed.content
    alternate = client.get("/template", headers={"host": "shared.example:20070"}).json()["templates"][0]
    assert alternate["cover"]["url"] == "http://shared.example:20070" + base
    assert "objectId" not in alternate["cover"]
    assert client.delete(f"/template/{template_id}").status_code == 204
    for version_key in (key, uploaded["Key"]):
        with pytest.raises(ClientError, match="NoSuchKey"):
            storage.get_object(Key=version_key)
    assert client.get(base).status_code == 404


def test_delete_preserves_cover_on_rollback_and_retries_storage_cleanup(client, template_db, template_payload, cover_storage, monkeypatch):
    """删除事务回滚可继续分享；删除已提交但 ZOS 失败时，重复 DELETE 能清理剩余对象。"""
    key = post_template(client, template_payload).json()["template"]["templateId"]
    template = f"/template/{key}"
    base = f"{template}/cover"
    assert client.put(f"{base}/{key}", files={"file": ("red.png", png("red"), "image/png")}).is_success
    image_key = cover_storage.put_object.call_args.kwargs["Key"]

    def roll_back(connection):
        """数据库拒绝删除，此时存储中的已发布版本应完整保留。"""
        connection.rollback()
        raise sqlite3.OperationalError("controlled delete rollback")

    with monkeypatch.context() as patch:
        patch.setattr(template_db.dialect, "do_commit", roll_back)
        assert client.delete(template).status_code == 503
    assert client.get(base).content == covers.encode_cover(png("red"))
    cover_storage.get_paginator.assert_not_called()
    with monkeypatch.context() as patch:
        patch.setattr(cover_storage.delete_objects, "side_effect", ClientError({"Error": {"Code": "AccessDenied"}}, "DeleteObjects"))
        assert client.delete(template).status_code == 503
    assert client.get(base).status_code == 404
    assert cover_storage.get_object(Key=image_key)["Body"].read()
    assert client.delete(template).status_code == 404
    with pytest.raises(ClientError, match="NoSuchKey"):
        cover_storage.get_object(Key=image_key)


def test_private_storage_without_delete_permission(client, template_payload, cover_storage, monkeypatch):
    """关闭对象清理时发布和删除均无需存储删除权限，失败的私有候选无法从分享链接读取。"""
    settings = covers.ZosSettings()
    settings.zos_delete_objects = False
    monkeypatch.setattr(covers, "ZosSettings", lambda: settings)
    key = post_template(client, template_payload).json()["template"]["templateId"]
    base = f"/template/{key}/cover"
    files = {"file": ("cover.png", png("green"), "image/png")}
    write = cover_storage.put_object.side_effect

    def lose_ack(**kwargs):
        """确认以私有 ACL 存入对象，再丢失本次 PUT 回执。"""
        assert kwargs["ACL"] == "private"
        write(**kwargs)
        raise ReadTimeoutError(endpoint_url="https://storage.test")

    cover_storage.put_object.side_effect = lose_ack
    assert client.put(f"{base}/{key}", files=files).status_code == 503
    assert client.get(base).status_code == 404
    cover_storage.put_object.side_effect = write
    assert client.put(f"{base}/{key}", files=files).is_success
    assert client.delete(f"/template/{key}").status_code == 204
    assert client.get(base).status_code == 404
    cover_storage.delete_object.assert_not_called()
    cover_storage.get_paginator.assert_not_called()


def test_missing_published_object_can_be_repaired(client, template_payload, cover_storage):
    """数据库保留发布记录时，只有对象缺失允许补图；修复后同一分享地址可读取新版本。"""
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
    assert repaired.json()["url"] == published["url"]
    assert [put.kwargs["ACL"] for put in storage.put_object.call_args_list] == ["private", "private"]
    uploaded = storage.put_object.call_args.kwargs
    storage.get_object.side_effect = read
    restored = client.get(base)
    assert restored.status_code == 200 and restored.content == uploaded["Body"]
    assert storage.get_object.call_args.kwargs["Key"] == uploaded["Key"]


@pytest.mark.parametrize("legacy", ["untagged", "backup", "private"])
def test_lost_object_put_ack_preserves_shared_image(client, template_db, template_payload, cover_storage, legacy):
    """旧版无标签图片、私有回滚副本及新私有图均可读取。新 PUT 回执丢失不影响已发布图。"""
    record = post_template(client, {**template_payload, "cover_source": "builtin"}).json()["template"]
    key = record["templateId"]
    revision = record["cover"]["revision"]
    base = f"/template/{key}/cover"
    put = f"{base}/{revision}"
    write = cover_storage.put_object.side_effect
    if legacy == "private":
        assert client.put(put, files={"file": ("old.png", png("red"), "image/png")}).is_success
    else:
        old_key = f"imv/template-covers/{key}.png"
        with template_db.begin() as connection:
            connection.execute(store.templates.update().values(cover={
                "revision": revision, "published_revision": revision, "source": "builtin",
                "url": "https://images.test/" + old_key,
            }))
        if legacy == "backup":
            write(Key=old_key, Body=png("green"), Metadata={"revision": "uncommitted"})
            write(Key=old_key.replace(".png", ".previous.png"), Body=png("red"), Metadata={"revision": revision})
        else:
            write(Key=old_key, Body=png("red"))
    old = client.get(base)
    assert old.is_success and Image.open(BytesIO(old.content)).getpixel((1, 1)) == (255, 0, 0)

    def lose_ack(**kwargs):
        """对象已接收，调用方未收到结果。"""
        write(**kwargs)
        raise ReadTimeoutError(endpoint_url="https://storage.test")

    cover_storage.put_object.side_effect = lose_ack
    response = client.put(put, files={"file": ("new.png", png("blue"), "image/png")}, data={"refresh": "true"})
    served = client.get(base)
    assert response.status_code == 503 and served.content == old.content
    assert served.headers["etag"] == old.headers["etag"]
    assert client.get(base, headers={"If-None-Match": old.headers["etag"]}).status_code == 304
    cover_storage.put_object.side_effect = write
    published = client.put(put, files={"file": ("new.png", png("green"), "image/png")}, data={"refresh": "true"})
    assert published.is_success and published.json()["url"] == str(client.base_url).rstrip("/") + base
    assert client.get(base).content == covers.encode_cover(png("green"))


@pytest.mark.parametrize("edit", [False, True])
def test_late_put_cannot_replace_a_successful_retry(client, template_payload, cover_storage, edit):
    """超时 PUT 在下一次发布之后才生效。同一刷新和编辑后的新版都只能读到成功的绿图。"""
    row = post_template(client, {**template_payload, "cover_source": "builtin"}).json()["template"]
    key, version = row["templateId"], row["cover"]["revision"]
    base = f"/template/{key}/cover"

    def publish(color, refresh):
        """以当前目标版本提交一张纯色截图。"""
        return client.put(f"{base}/{version}", files={"file": ("cover.png", png(color), "image/png")},
                          data={"refresh": str(refresh).lower()})

    assert publish("red", False).is_success
    write = cover_storage.put_object.side_effect
    cover_storage.put_object.side_effect = ReadTimeoutError(endpoint_url="https://storage.test")
    assert publish("blue", True).status_code == 503
    delayed = cover_storage.put_object.call_args.kwargs
    assert delayed["ACL"] == "private"
    cover_storage.put_object.side_effect = write
    if edit:
        saved = post_template(client, {**template_payload, "template_id": key, "cover_source": "builtin"})
        version = saved.json()["template"]["cover"]["revision"]
    winner = publish("green", not edit)
    assert winner.is_success and cover_storage.put_object.call_args.kwargs["Key"] != delayed["Key"]
    write(**delayed)
    served = client.get(winner.json()["url"])
    assert served.content == covers.encode_cover(png("green"))
    assert served.headers["etag"] == f'"{winner.json()["published_revision"]}"'
    assert client.get(base, headers={"If-None-Match": served.headers["etag"]}).status_code == 304
    version = winner.json()["revision"]
    assert publish("green", True).is_success


def test_first_put_ack_lost_removes_unpublished_object(client, template_payload, cover_storage):
    """首版存储回执丢失后清理本次私有对象，分享地址仍返回 404。"""
    row = post_template(client, template_payload).json()["template"]
    key = row["templateId"]
    base = f"/template/{key}/cover"
    write = cover_storage.put_object.side_effect

    def lose_ack(**kwargs):
        """写入对象后模拟回执丢失。"""
        write(**kwargs)
        raise ReadTimeoutError(endpoint_url="https://storage.test")

    cover_storage.put_object.side_effect = lose_ack
    assert client.put(f"{base}/{key}", files={"file": ("first.png", png("blue"), "image/png")}).status_code == 503
    assert client.get(base).status_code == 404
    with pytest.raises(ClientError) as missing:
        cover_storage.get_object(Key=cover_storage.put_object.call_args.kwargs["Key"])
    assert missing.value.response["Error"]["Code"] == "NoSuchKey"


def test_delete_sweeps_a_late_unpublished_private_upload(client, template_payload, cover_storage):
    """首图超时后迟到，模板从未有发布元数据。删除仍按本模板前缀回收它。"""
    key = post_template(client, template_payload).json()["template"]["templateId"]
    template = f"/template/{key}"
    write = cover_storage.put_object.side_effect
    cover_storage.put_object.side_effect = ReadTimeoutError(endpoint_url="https://storage.test")
    response = client.put(f"{template}/cover/{key}", files={"file": ("cover.png", png("blue"), "image/png")})
    assert response.status_code == 503
    attempt = cover_storage.put_object.call_args.kwargs
    write(**attempt)
    assert attempt["ACL"] == "private" and client.get(f"{template}/cover").status_code == 404
    assert client.delete(template).status_code == 204
    with pytest.raises(ClientError, match="NoSuchKey"):
        cover_storage.get_object(Key=attempt["Key"])


def test_slow_reader_keeps_its_committed_snapshot(client, template_payload, cover_storage):
    """读旧元数据的慢请求与后来的两次发布并行，每次响应的 ETag 和像素各自一致。"""
    key = post_template(client, template_payload).json()["template"]["templateId"]
    base = f"/template/{key}/cover"
    assert client.put(f"{base}/{key}", files={"file": ("cover.png", png("red"), "image/png")}).is_success
    read = cover_storage.get_object.side_effect
    revision = key

    def publish_before_read(**kwargs):
        """暂停本次对象读取，模拟其他客户完成两次刷新。"""
        nonlocal revision
        cover_storage.get_object.side_effect = read
        for color in ("blue", "green"):
            saved = client.put(f"{base}/{revision}", files={"file": ("cover.png", png(color), "image/png")}, data={"refresh": "true"})
            assert saved.is_success
            revision = saved.json()["published_revision"]
        return read(**kwargs)

    cover_storage.get_object.side_effect = publish_before_read
    served = client.get(base)
    assert served.status_code == 200 and served.headers["etag"] == f'"{key}"'
    assert served.content == covers.encode_cover(png("red"))
    latest = client.get(base, headers={"If-None-Match": served.headers["etag"]})
    assert latest.headers["etag"] == f'"{revision}"' and latest.content == covers.encode_cover(png("green"))


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
    from sqlalchemy import inspect

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


def test_replay_legacy_backfill(client, template_payload, cover_storage):
    """重放已成功的补图请求时复用发布版本，模板排序保持不变。"""
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
