"""显式启用的真实 MySQL API 测试：IMV_TEST_MYSQL=1 uv run --locked pytest tests/test_mysql_integration.py -v。

必须提供临时 MySQL 的 DB_HOST/PORT/USER/PASSWORD；仅允许回环连接，每例新建随机数据库并清理。
默认跳过，不读取 .env、不使用 SQLite 替身、不提交模型或云端任务。
"""

import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import URL, create_engine, func, inspect, select
from sqlalchemy.exc import IntegrityError

from server import database
from server.app import app
from server.remotion_templates import sprite_store
from server.template import store
from .conftest import SPRITE_ID, remotion_track, seed_sprite
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


def summary(templates: list[dict]) -> list[tuple[str, str, int]]:
    """列表摘要中用于比较的字段：模板 ID、名称与轨道数。"""
    return [(item["template_id"], item["name"], item.get("track_count", 0)) for item in templates]


def summary_of(saved: dict) -> tuple[str, str, int]:
    """保存后的完整模板对应的列表摘要字段。"""
    return saved["template_id"], saved["name"], len(saved["tracks"])


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
        assert summary(listed_templates(client.get("/template"))) == [summary_of(saved)]


def test_mysql_stores_remotion_tracks_as_references(mysql_database, template_payload):
    """未发布的 Sprite 被 422 拒绝；片段轨道的引用、作用对象、秒数起点和空 editor 经 MySQL JSON 列往返不变，重启后读取仍通过校验；IMS 轨道不带 sprite_id。"""
    template_payload["tracks"] += [
        remotion_track("clip-a", start=2.5, duration=3, text_target="title"),
        remotion_track("clip-b", text_target="subtitle"),
    ]
    with TestClient(app) as client:
        assert post_template(client, template_payload).status_code == 422
        seed_sprite()
        response = post_template(client, template_payload)
        assert response.status_code == 201
        saved = saved_template(response)
        with database.get_engine().connect() as connection:
            stored = connection.execute(select(store.templates.c.configuration)).scalar_one()
        tracks = {track["id"]: track for track in stored["tracks"]}
        assert tracks["clip-a"] == {
            "id": "clip-a", "target": "remotion", "sprite_id": SPRITE_ID, "text_target": "title",
            "start_mode": "seconds", "start": 2.5, "duration": 3, "editor": {},
        }
        assert tracks["clip-b"]["duration"] is None
        assert all("sprite_id" not in track for track in stored["tracks"] if track["target"] != "remotion")
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
    assert sprite_store.records([first, "unknown"]) == {first: large}  # 未知 ID 不在结果里
    # 不论文字字段取最早的同源同类型发布：只排序标识列，不对整行 JSON 排序。
    assert sprite_store.find_any(version, 1) == large and sprite_store.find_any(version, 2) is None
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
        assert summary(listed_templates(client.get("/template"))) == [summary_of(saved)]
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


def test_mysql_remotion_store_round_trip(mysql_database, tmp_path):
    """真实 MySQL 上的字效存储：未变化的覆盖写入不重复插入，单活动任务唯一键，确认清理、事件与设备过期按 DATETIME 截止。"""
    from datetime import timedelta

    from server.remotion_templates.models import GenerateTemplateRequest, JobInput
    from server.remotion_templates.store import Conflict, Store, now
    from server.remotion_templates.tables import conversations, devices, job_progress, jobs

    database.initialize_database()
    remotion = Store(tmp_path / "remotion")
    remotion.initialize()
    device = uuid4()
    work, first = remotion.create(GenerateTemplateRequest(description="真实库 🎬"), device)
    window = remotion.conversation(work.id)
    # 参数修订不改模型窗口，任务结束时仍保存同一份内容：MySQL 对未变化的 UPDATE 也必须视为已存在。
    for _ in range(2):
        remotion.save_conversation(work.id, window)
    window.append([{"role": "user", "content": "改"}])
    remotion.save_conversation(work.id, window)
    remotion.claim()
    for _ in range(2):
        remotion.progress(first.id, "understanding")
    remotion.round(first.id, {"layer": "outer", "turn": 1, "calls": []})
    with pytest.raises(Conflict):
        remotion.enqueue(work.id, JobInput(mode="edit", instruction="并发"), None)
    remotion.update(first.id, status="answered", stage="finished", answer="好")
    latest = remotion.enqueue(work.id, JobInput(mode="edit", instruction="再改"), None)
    remotion.update(latest.id, status="failed", stage="finished")
    with remotion.reading() as db:
        assert db.execute(select(func.count()).select_from(conversations)).scalar_one() == 1
        assert len(db.execute(select(job_progress.c.job_id)).all()) == 1
    events = remotion.work_events(work.id, 0)
    assert [event.id for event in events] == sorted(event.id for event in events)
    remotion.acknowledge(work.id, events[-1].id, device)
    with remotion.reading() as db:
        assert db.execute(select(jobs.c.id)).scalars().all() == [str(latest.id)]
    assert remotion.inputs(latest.id).instruction == "再改"
    remotion.touch(device)
    remotion.expire_events(now() + timedelta(seconds=1))
    remotion.expire_devices(now() - timedelta(days=1))
    assert remotion.pending_deletions() == []
    remotion.expire_devices(now() + timedelta(seconds=1))
    assert remotion.pending_deletions() == [work.id]
    with remotion.reading() as db:
        assert db.execute(select(devices.c.device_id)).scalars().all() == []
