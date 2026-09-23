"""隔离数据库、切片与 ASR 配置及请求；在 server/ 执行 uv run --locked pytest -v。

路由仍调用真实 schema/store；仅适配排序规则和唯一约束错误码，不模拟 MySQL 行锁或建库。
"""

import json
import os
import sqlite3
from datetime import datetime
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
    """清除外部配置并将各配置类指向临时 server/.env，避免读取本机文件。"""
    for key in list(os.environ):
        if key.upper().startswith(("DB_", "IMV_", "COMPOSITION_", "SEGMENT_MATCH_", "IMS_", "MIX_VIDEO_ALIYUN_IMS_", "ALIBABA_CLOUD_", "ZOS_")) or key.upper() in ("PORT", "DASHSCOPE_API_KEY", "ASR_BASE_URL"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)

    from server.config_base import CommonSettings
    from server.__main__ import ServerSettings
    from server.remotion_templates.settings import Settings as RemotionSettings
    from server.segmentation.settings import Settings as SegmentationSettings
    from server.video_composition.settings import Settings as CompositionSettings, ZosSettings

    env_file = tmp_path / "server/.env"
    env_file.parent.mkdir()
    for settings_class in (
        CommonSettings, ServerSettings, database.DatabaseSettings,
        RemotionSettings, SegmentationSettings, CompositionSettings, ZosSettings,
    ):
        monkeypatch.setitem(settings_class.model_config, "env_file", env_file)
    # 只导入字段声明；业务首次加载时使用本例隔离文件。
    from server.asr.settings import ASRSettings
    monkeypatch.setitem(ASRSettings.model_config, "env_file", env_file)


@pytest.fixture
def template_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """每个用例使用独立文件与真实表定义；阻止回退到 MySQL，并清理连接池与建表缓存。"""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'templates.sqlite3'}",
        connect_args={"check_same_thread": False},
        json_serializer=lambda value: json.dumps(value, ensure_ascii=False),
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


def template_track(target: str = "title", **editor) -> dict:
    """提供独立对象及明确的时间规则，编辑参数可用于合法与非法输入测试。"""
    content = {"title": "", "subtitle": "", "bubbleText": ""}
    if target in ("title", "subtitle", "bubble"):
        content["bubbleText" if target == "bubble" else target] = "示例文字"
    return {"id": target, "target": target, "start_mode": "seconds",
            "start": 1 if target == "transition" else 0,
            "duration": 0.5 if target == "transition" else None,
            "editor": {**content, **editor}}


@pytest.fixture
def template_payload() -> dict:
    """提供最小合法模板，每次测试获取独立可修改的 JSON 请求。"""
    return {
        "name": "测试模板",
        "description": "标题淡入",
        "tracks": [template_track(titleIn="in/fade_in")],
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


@pytest.fixture
def composition_settings(monkeypatch, asr_env):
    """合成假配置为正常流程预留 CI 调度余量；超时用例单独缩短期限，不读取真实 .env。"""
    from server.video_composition.settings import Settings

    for key, value in {
        "SEGMENT_MATCH_BASE_URL": "https://matching.example.test/deployment",
        "SEGMENT_MATCH_AUTHORIZATION": "Bearer test-only",
        "ALIBABA_CLOUD_ACCESS_KEY_ID": "test-id",
        "ALIBABA_CLOUD_ACCESS_KEY_SECRET": "test-secret",
        "ZOS_API_ENDPOINT": "https://hangzhou7.zos.ctyun.cn",
        "ZOS_BUCKET": "archives",
        "ZOS_ACCESS_KEY_ID": "test-zos-id",
        "ZOS_SECRET_ACCESS_KEY": "test-zos-secret",
        "ZOS_WEB_URL": "https://archives.hangzhou7.zos.ctyun.cn",
        "ZOS_FORCE_PATH_STYLE": "false",
        "COMPOSITION_POLL_SECONDS": "0.01",
        "COMPOSITION_HTTP_TIMEOUT_SECONDS": "10",
        "COMPOSITION_MATCH_WAIT_SECONDS": "10",
        "IMV_LLM_BASE_URL": "https://llm.example.test/v1",
        "IMV_LLM_API_KEY": "test-key",
        "IMV_LLM_MODEL": "test-model",
    }.items():
        monkeypatch.setenv(key, value)
    return Settings()


@pytest.fixture
def composition_case(template_db, template_payload):
    """真实模板存储生成快照，搭配有前后静音和片段空隙的脱敏业务数据。"""
    from server.template.schema import TemplateSave

    template_payload["tracks"] = [
        template_track("subtitle"),
        {**template_track(titleIn="in/fade_in"), "start": 1, "duration": 2},
        template_track("bubble"),
    ]
    template = store.save_template(TemplateSave.model_validate(template_payload))
    segments = [
        {"segment_id": 1, "text": "甲乙丙丁。", "start_time": 1, "end_time": 3,
         "keyword": "甲乙", "level": 2, "group_id": [1, 2]},
        {"segment_id": 2, "text": "戊己庚辛。", "start_time": 4, "end_time": 6,
         "keyword": "", "level": 1, "group_id": [2, 2]},
    ]
    return {
        "request": {"text": "甲乙丙丁。戊己庚辛。", "title": "业务标题\n保留换行",
                    "videoUrl": "https://media.example.test/avatar.mp4?token=a%2Fb&x=1",
                    "audioUrl": "https://media.example.test/tts.wav?token=c%2Bd",
                    "styleId": str(template.template_id)},
        "template": template.model_dump(mode="json", by_alias=True),
        "segments": segments,
        "matches": [
            {"segment_id": item["segment_id"], "text": item["text"], "start_time": item["start_time"],
             "end_time": item["end_time"], "matched_candidate_url": None}
            for item in segments
        ],
        "duration_ms": 8000, "width": 1080, "height": 1920, "fps": 30,
    }


@pytest.fixture
async def composition_runtime(composition_settings, template_db):
    """复用手动推进用的调度器；断言失败也先收束线程，再释放隔离数据库，不启动后台扫描。"""
    from server.video_composition.service import Runtime

    runtime = Runtime()
    runtime.settings = composition_settings
    try:
        yield runtime
    finally:
        await runtime.close()


@pytest.fixture
def composition_logs(template_db):
    """读取真实聚合行；默认展平阶段事件供已有全链路断言复用，raw=True 检查物理结构。"""
    from sqlalchemy import select
    from server.video_composition.store import execution_logs

    def read(task_id=None, *, raw=False):
        """按 sequence 还原事件顺序，任务最终时间来自聚合行，事件时间来自 detail。"""
        statement = select(execution_logs).order_by(execution_logs.c.id)
        if task_id is not None:
            statement = statement.where(execution_logs.c.task_id == task_id)
        with template_db.connect() as connection:
            rows = [dict(row) for row in connection.execute(statement).mappings()]
        if raw:
            return rows
        events = []
        for row in rows:
            for section in row["detail"]["阶段记录"].values():
                entries = [entry for entry in section["执行日志"] + section["错误日志"] if "事件" in entry]
                inputs = {item["序号"]: item["内容"] for item in section["输入"] if "序号" in item}
                outputs = {item["序号"]: item["内容"] for item in section["输出"] if "序号" in item}
                for entry in entries:
                    details = dict(entry["详情"])
                    if entry["序号"] in inputs:
                        details["input"] = inputs[entry["序号"]]
                    if entry["序号"] in outputs:
                        details["output"] = outputs[entry["序号"]]
                    events.append({
                        "sequence": entry["序号"], "task_id": row["task_id"], "event": entry["事件"],
                        "stage": entry["任务阶段"], "status": entry["任务状态"],
                        "created_at": datetime.fromisoformat(entry["时间"]).replace(tzinfo=None),
                        "task_created_at": row["task_created_at"],
                        "task_finished_at": row["task_finished_at"] if entry["任务状态"] in ("succeeded", "failed") else None,
                        "details": details,
                    })
        events.sort(key=lambda item: (item["task_created_at"], item["sequence"]))
        return events

    return read
