# 使用临时 SQLite 验证模板 Protobuf 路由的并发、数据库故障恢复和连接清理。

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from generated.imv.template.v1 import template_pb2 as pb
from httpx import Response
from pymysql.err import OperationalError as MySQLOperationalError
from sqlalchemy import Engine, event, inspect
from sqlalchemy.exc import OperationalError

from server import database
from server.app import app
from server.template import store
from .template_wire import post_template


# 测试并发创建同名模板时只保存一条记录。
def test_concurrent_duplicate_create(client: TestClient, template_payload: dict) -> None:
    """两个请求同时提交，数据库唯一约束只允许其中一个创建成功。"""
    assert pb.ListTemplatesResponse.FromString(client.get("/template").content).templates == []
    barrier = Barrier(2, timeout=5)

    def create_same_name() -> int:
        """等待两个工作线程就绪后发送真实 HTTP 请求。"""
        barrier.wait()
        return post_template(client, template_payload).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(create_same_name) for _ in range(2)]
        statuses = sorted(future.result(timeout=10) for future in futures)
    assert statuses == [201, 409]
    assert len(pb.ListTemplatesResponse.FromString(client.get("/template").content).templates) == 1


# 测试各模板操作在 SQL 执行失败后返回 503，并保留可重试状态。
@pytest.mark.parametrize("operation,sql,success", [
    ("list", "SELECT", 200), ("detail", "SELECT", 200),
    ("create", "INSERT", 201), ("update", "UPDATE", 200), ("delete", "DELETE", 204),
])
def test_database_failure_rolls_back_and_recovers(
    client: TestClient, template_db: Engine, template_payload: dict,
    operation: str, sql: str, success: int,
) -> None:
    """在 SQL 执行后注入连接故障，检查事务回滚和故障解除后的请求。"""
    created = post_template(client, template_payload)
    original = pb.SaveTemplateResponse.FromString(created.content).template
    before = pb.ListTemplatesResponse.FromString(client.get("/template").content).templates
    payload = {**template_payload, "name": "失败后重试"}
    if operation == "update":
        payload["template_id"] = original.template_id
    path = f"/template/{original.template_id}" if operation in ("detail", "delete") else "/template"
    method = "POST" if operation in ("create", "update") else "DELETE" if operation == "delete" else "GET"

    def perform_request() -> Response:
        """使用当前操作对应的原有模板地址发送 Protobuf 或空请求。"""
        return post_template(client, payload) if method == "POST" else client.request(method, path)

    def fail_after_sql(connection, cursor, statement, parameters, context, executemany) -> None:
        """在目标 SQL 已执行时触发连接故障，覆盖事务回滚路径。"""
        if statement.lstrip().upper().startswith(sql):
            raise OperationalError(statement, parameters, MySQLOperationalError(2013, "private-database-detail"))

    event.listen(template_db, "after_cursor_execute", fail_after_sql)
    try:
        failed = perform_request()
    finally:
        event.remove(template_db, "after_cursor_execute", fail_after_sql)
    assert failed.status_code == 503
    assert "private-database-detail" not in failed.text
    assert "数据库操作失败" in failed.json()["detail"]
    assert pb.ListTemplatesResponse.FromString(client.get("/template").content).templates == before

    retried = perform_request()
    assert retried.status_code == success
    remaining = pb.ListTemplatesResponse.FromString(client.get("/template").content).templates
    assert len(remaining) == (2 if operation == "create" else 0 if operation == "delete" else 1)
    if operation == "update":
        assert remaining[0].name == "失败后重试"


# 测试首次建表失败后仍能在下一次请求中创建表。
def test_schema_creation_failure_can_retry(client: TestClient, template_db: Engine) -> None:
    """建表异常返回 503，成功状态不会提前缓存。"""
    def fail_create(connection, cursor, statement, parameters, context, executemany) -> None:
        """只阻止 CREATE TABLE，保留其余数据库操作。"""
        if statement.lstrip().upper().startswith("CREATE TABLE"):
            raise OperationalError(statement, parameters, MySQLOperationalError(2013, "schema-unavailable"))

    event.listen(template_db, "before_cursor_execute", fail_create)
    try:
        failed = client.get("/template")
    finally:
        event.remove(template_db, "before_cursor_execute", fail_create)
    assert failed.status_code == 503
    assert store._ready_engine is None
    assert not inspect(template_db).has_table("templates")
    assert pb.ListTemplatesResponse.FromString(client.get("/template").content).templates == []
    assert inspect(template_db).has_table("templates")


# 测试应用关闭后释放连接，并在重新启动时读取已有模板。
def test_lifespan_releases_connections_and_preserves_data(
    template_db: Engine, template_payload: dict,
) -> None:
    """两个 ASGI 生命周期之间保留数据，同时检查连接池已释放。"""
    with TestClient(app) as first_client:
        created = post_template(first_client, template_payload)
        assert created.status_code == 201
        original = pb.SaveTemplateResponse.FromString(created.content).template
        assert template_db.pool.checkedout() == 0
    assert database._engine is None
    assert template_db.pool.checkedout() == 0

    database._engine = template_db
    with TestClient(app) as second_client:
        response = second_client.get(f"/template/{original.template_id}")
        assert response.status_code == 200
        assert pb.GetTemplateResponse.FromString(response.content).template == original
    assert database._engine is None
