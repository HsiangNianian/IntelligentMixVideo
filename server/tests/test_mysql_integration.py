"""显式启用的真实 MySQL API 测试：IMV_TEST_MYSQL=1 uv run --locked pytest tests/test_mysql_integration.py -v。

必须提供临时 MySQL 的 DB_HOST/PORT/USER/PASSWORD；仅允许回环连接，每例新建随机数据库并清理。
默认跳过，不读取 .env、不使用 SQLite 替身、不提交模型或云端任务。
"""

import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import URL, create_engine, inspect, select
from sqlalchemy.exc import IntegrityError

from server import database
from server.app import app
from server.remotion_templates import sprite_store
from server.template import store
from .conftest import SPRITE_ID, remotion_track
from .template_wire import detail_template, listed_templates, post_template, saved_template


pytestmark = pytest.mark.skipif(
    os.environ.get("IMV_TEST_MYSQL") != "1",
    reason="Requires an explicitly configured disposable loopback MySQL service.",
)


@pytest.fixture
def mysql_database(monkeypatch):
    """仅操作本例生成的库；真实建库与连接交给应用，finally 释放池并删除测试库。"""
    values = {key: os.environ[f"DB_{key.upper()}"] for key in ("host", "port", "user", "password")}
    assert values["host"] == "127.0.0.1", "Integration tests require a loopback MySQL service"
    name = f"imv_ci_{uuid4().hex}"
    settings = database.DatabaseSettings(_env_file=None, name=name, **values)
    database.close_database()
    monkeypatch.setattr(database, "DatabaseSettings", lambda: settings)
    monkeypatch.setattr(store, "_ready_engine", None)
    admin = create_engine(
        URL.create(
            "mysql+pymysql", username=settings.user,
            password=settings.password.get_secret_value(), host=settings.host, port=settings.port,
        ),
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 5, "read_timeout": 10, "write_timeout": 10},
    )
    try:
        # 拒绝已有库，防止夹具误复用数据；名称仅来自 UUID，不能由外部环境指定。
        with admin.connect() as connection:
            assert name not in inspect(connection).get_schema_names()
        yield name
    finally:
        database.close_database()
        try:
            with admin.connect() as connection:
                connection.exec_driver_sql(f"DROP DATABASE IF EXISTS `{name}`")
        finally:
            admin.dispose()


def test_mysql_bootstrap_unicode_and_persistence(mysql_database, template_payload):
    """真实启动创建 utf8mb4 库与缺失表；中文、emoji、JSON 和 UTC 响应在重启后保持。"""
    with TestClient(app) as client:
        database.initialize_database()
        engine = database.get_engine()
        assert engine.dialect.name == "mysql"
        with engine.connect() as connection:
            charset, collation = connection.exec_driver_sql(
                "SELECT @@character_set_database, @@collation_database"
            ).one()
            assert (charset, collation) == ("utf8mb4", "utf8mb4_bin")
        assert listed_templates(client.get("/template")) == []
        template_payload["name"] = "  中文测试 🎬  "
        response = post_template(client, template_payload)
        assert response.status_code == 201
        saved = saved_template(response)
        assert UUID(saved["template_id"]).version == 4
        assert saved["name"] == "中文测试 🎬"
        assert saved["effects"][0]["parameters"] == {"AaiMotionInEffect": "fade_in"}
        assert saved["created_at"].endswith(("Z", "+00:00"))
    assert database._engine is None
    with TestClient(app) as client:
        response = client.get(f"/template/{saved['template_id']}")
        assert response.status_code == 200 and detail_template(response) == saved
        assert listed_templates(client.get("/template")) == [saved]


def test_mysql_stores_remotion_tracks_as_references(mysql_database, template_payload):
    """片段轨道的引用、秒数起点和空 editor 经 MySQL JSON 列往返不变，重启后读取仍通过校验；IMS 轨道不带 sprite_id。"""
    template_payload["tracks"] += [remotion_track("clip-a", start=2.5, duration=3), remotion_track("clip-b")]
    with TestClient(app) as client:
        response = post_template(client, template_payload)
        assert response.status_code == 201
        saved = saved_template(response)
        with database.get_engine().connect() as connection:
            stored = connection.execute(select(store.templates.c.configuration)).scalar_one()
        assert stored["tracks"][1] == {
            "id": "clip-a", "target": "remotion", "sprite_id": SPRITE_ID,
            "start_mode": "seconds", "start": 2.5, "duration": 3, "editor": {},
        }
        assert stored["tracks"][2]["duration"] is None
        assert "sprite_id" not in stored["tracks"][0]
    with TestClient(app) as client:
        assert detail_template(client.get(f"/template/{saved['template_id']}")) == saved


def test_mysql_sprite_table_keeps_field_names_binary_and_selections_unique(mysql_database):
    """真实 MySQL 建表成功（唯一键在 utf8mb4 的长度限制内）；字段名按二进制比较，同一来源与选择只能发布一次，大 JSON 往返不变。"""
    database.initialize_database()
    engine = sprite_store.initialize_schema()
    with engine.connect() as connection:
        collations = dict(connection.exec_driver_sql(
            "SELECT COLUMN_NAME, COLLATION_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'remotion_sprites' AND COLLATION_NAME IS NOT NULL"
        ).all())
    assert collations["text_prop"] == collations["keywords_prop"] == "utf8mb4_bin"
    version = str(uuid4())

    def publish(sprite_id: str, **fields) -> None:
        """为同一个来源版本写入一条发布记录，字段选择可覆盖。"""
        values = {"source_version_id": version, "kind": 1, "text_prop": "text", "keywords_prop": "", **fields}
        sprite_store.insert(sprite_id=sprite_id, published_at=datetime.now(UTC), data={"sprite_id": sprite_id}, **values)

    first = str(uuid4())
    publish(first)
    publish(str(uuid4()), text_prop="Text")  # 大小写不同是另一个字段名
    with pytest.raises(IntegrityError) as caught:
        publish(str(uuid4()))
    assert caught.value.orig.args[0] == 1062
    large = {"tsx_code": "路径🎬" * 100_000}
    sprite_store.replace_data(first, large)
    assert sprite_store.get(first) == large
    assert sprite_store.missing([first, "unknown"]) == {"unknown"}
    assert [sprite_id for sprite_id, _ in sprite_store.listing()].count(first) == 1


def test_mysql_conflict_rollback_update_and_delete(mysql_database, template_payload):
    """MySQL 唯一约束冲突返回 409，回滚后仍可更新/删除；无效请求不得写入数据。"""
    with TestClient(app) as client:
        response = post_template(client, template_payload)
        assert response.status_code == 201
        saved = saved_template(response)
        path = f"/template/{saved['template_id']}"
        duplicate = post_template(client, template_payload)
        assert duplicate.status_code == 409 and duplicate.json()["detail"]
        assert post_template(client, {**template_payload, "name": " "}).status_code == 422
        assert listed_templates(client.get("/template")) == [saved]
        updated = post_template(client, {
            **template_payload, "template_id": saved["template_id"], "name": "重命名",
        })
        assert updated.status_code == 200
        assert saved_template(updated)["created_at"] == saved["created_at"]
        assert detail_template(client.get(path))["name"] == "重命名"
        assert client.delete(path).status_code == 204
        assert client.get(path).status_code == 404
        assert client.delete(path).status_code == 404
        assert post_template(client, {
            **template_payload, "template_id": saved["template_id"],
        }).status_code == 404
        assert listed_templates(client.get("/template")) == []
