"""用临时 SQLite 隔离真实数据库并运行 ASGI 生命周期；在 server/ 执行 uv run --locked pytest -v。

路由仍调用真实 schema/store；仅适配排序规则和唯一约束错误码，不模拟 MySQL 行锁或建库。
"""

from collections.abc import Iterator
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient
from pymysql.err import IntegrityError as MySQLIntegrityError
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.exc import IntegrityError

from server import database
from server.app import app
from server.template import store


@pytest.fixture
def template_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """每个用例使用独立文件与真实表定义；阻止回退到 MySQL，并清理连接池与建表缓存。"""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'templates.sqlite3'}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def configure_collation(connection, record) -> None:
        """让 SQLite 接受生产表的大小写敏感排序规则名称；不代表完整 MySQL 字符集兼容。"""
        connection.create_collation(
            "utf8mb4_bin", lambda left, right: (left > right) - (left < right),
        )

    @event.listens_for(engine, "handle_error", retval=True)
    def translate_duplicate_name(context):
        """仅将真实 SQLite UNIQUE 冲突映射为 MySQL 1062，保留事务回滚与服务端 409 处理。"""
        if (
            isinstance(context.original_exception, sqlite3.IntegrityError)
            and context.original_exception.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_UNIQUE
        ):
            return IntegrityError(
                context.statement, context.parameters,
                MySQLIntegrityError(1062, "Duplicate template name"),
            )

    def reject_external_engine(*args, **kwargs):
        """夹具失效时立即报错，不允许测试读取真实配置后连接 MySQL。"""
        pytest.fail("路由测试只能使用 template_db 提供的临时 SQLite")

    monkeypatch.setattr(database, "_engine", engine)
    monkeypatch.setattr(database, "create_engine", reject_external_engine)
    monkeypatch.setattr(store, "_ready_engine", None)
    try:
        yield engine
    finally:
        database.close_database()
        engine.dispose()


@pytest.fixture
def template_payload() -> dict:
    """提供最小合法模板，每次测试获取独立可修改的 JSON 请求。"""
    return {
        "name": "测试模板",
        "description": "标题淡入",
        "editor": {"titleIn": "in/fade_in"},
        "effect_ids": ["in/fade_in"],
        "transition_duration_seconds": 0.5,
    }


@pytest.fixture
def client(template_db: Engine) -> Iterator[TestClient]:
    """运行真实应用启动与清理逻辑，全部数据库访问限定到临时 SQLite，不占用端口。"""
    with TestClient(app) as test_client:
        yield test_client
