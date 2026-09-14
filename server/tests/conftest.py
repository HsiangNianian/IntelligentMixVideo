"""隔离数据库、切片与 ASR 配置及请求；在 server/ 执行 uv run --locked pytest -v。

路由仍调用真实 schema/store；仅适配排序规则和唯一约束错误码，不模拟 MySQL 行锁或建库。
"""

import os
import sqlite3
from collections.abc import Iterator
from functools import partial
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pymysql.err import IntegrityError as MySQLIntegrityError
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.exc import IntegrityError

from server import database
from server.app import app
from server.template import store


@pytest.fixture(autouse=True)
def isolate_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """清除外部切片与 ASR 配置并切换临时目录；配置专项测试须显式注入。"""
    for key in list(os.environ):
        if key.upper().startswith("IMV_") or key.upper() in ("DASHSCOPE_API_KEY", "ASR_BASE_URL"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


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


@pytest.fixture
def asr(mocker):
    """首次导入时屏蔽真实 .env 读取；返回 ASR 实现供各用例替换配置。"""
    env_reader = mocker.patch(
        "pydantic_settings.sources.DotEnvSettingsSource._read_env_files",
        return_value={},
    )
    from server.asr import asr

    mocker.stop(env_reader)
    return asr


@pytest.fixture
def asr_env(asr, monkeypatch):
    """为每个用例注入假配置，禁止从文件读取真实凭证。"""
    settings = asr.ASRSettings(_env_file=None, dashscope_api_key="test-key")
    monkeypatch.setattr(asr, "settings", settings)
    return settings


@pytest.fixture
def anyio_backend():
    """使用已有 AnyIO 插件在 asyncio 上运行异步用例，不额外引入测试依赖。"""
    return "asyncio"


@pytest.fixture
def asr_http(asr, mocker):
    """用内存传输替换异步客户端网络，保留资源生命周期并跳过轮询等待。"""
    http = mocker.Mock(side_effect=AssertionError("测试未配置 HTTP 响应"))
    mocker.patch.object(asr.asyncio, "sleep")
    mocker.patch.object(
        asr.httpx,
        "AsyncClient",
        side_effect=partial(httpx.AsyncClient, transport=httpx.MockTransport(http)),
    )
    return http


@pytest.fixture
def asr_responses():
    """提供单音频提交、成功任务及原始字词结果，各用例独立修改。"""
    return [
        {"output": {"task_id": "task-123"}},
        {
            "output": {
                "task_status": "SUCCEEDED",
                "results": [
                    {
                        "subtask_status": "SUCCEEDED",
                        "transcription_url": "https://results.example/result.json",
                    }
                ],
            }
        },
        {
            "transcripts": [
                {
                    "text": "你好",
                    "sentences": [
                        {
                            "words": [
                                {"text": "你好", "begin_time": 100, "end_time": 500},
                            ]
                        }
                    ],
                }
            ]
        },
    ]
