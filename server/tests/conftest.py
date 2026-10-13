"""隔离数据库、切片与 ASR 配置及请求；在 server/ 执行 uv run --locked pytest -v。

路由仍调用真实 schema/store；仅适配排序规则和唯一约束错误码，不模拟 MySQL 行锁或建库。
"""

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pymysql.err import IntegrityError as MySQLIntegrityError
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.exc import IntegrityError

from server import database
from server.app import app
from server.remotion_templates import sprite_store
from server.template import store


@pytest.fixture(autouse=True)
def isolate_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, request: pytest.FixtureRequest) -> None:
    """清除外部配置并将各配置类指向临时 server/.env，保留真实 MySQL 集成凭据。"""
    integration_mysql = request.module.__name__.endswith("test_mysql_integration")
    for key in list(os.environ):
        if integration_mysql and key.upper().startswith("DB_"):
            continue
        if key.upper().startswith(("DB_", "IMV_", "COMPOSITION_", "SEGMENT_MATCH_", "IMS_", "MIX_VIDEO_ALIYUN_IMS_", "ALIBABA_CLOUD_", "ZOS_")) or key.upper() in ("PORT", "DASHSCOPE_API_KEY", "ASR_BASE_URL"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    from server.video_composition import execution_log
    monkeypatch.setattr(execution_log, "LOG_ROOT", tmp_path / ".log")

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
        from server.video_composition.execution_log import close_logs
        close_logs()
        database.close_database()
        engine.dispose()


def template_track(target: str = "title", **editor) -> dict:
    """提供独立对象及明确的时间规则，编辑参数可用于合法与非法输入测试。"""
    content = {"title": "", "subtitle": "", "bubbleText": ""}
    if target in ("title", "subtitle", "bubble"):
        content["bubbleText" if target == "bubble" else target] = "示例文字"
    return {"id": target, "target": target,
            **({"transition_boundary": 1} if target == "transition" else {"start_mode": "seconds", "start": 0}),
            "duration": 0.5 if target == "transition" else None,
            "editor": {**content, **editor}}


# 测试用的已发布 Sprite ID；同一模板可以多次摆放它，各次摆放用不同的轨道 ID。
SPRITE_ID = "6f1c8a52-3d94-4b7e-9a10-2c5e8d7b4f31"


def remotion_track(track_id: str = "clip", **changes) -> dict:
    """提供引用已发布 Sprite 的片段轨道：秒数起点、时长缺省、编辑参数为空对象，可用关键字覆盖字段。"""
    return {"id": track_id, "target": "remotion", "sprite_id": SPRITE_ID, "start_mode": "seconds",
            "start": 1, "duration": None, "editor": {}, **changes}


def published_sprite(sprite_id: str = SPRITE_ID, canvas: tuple[int, int, int] = (1080, 1920, 30), kind: int = 1):
    """一条完整的发布内容（`PublishedSprite`），画布为（宽、高、帧率）。

    可编辑的 text（1～40 字）、fontSize（24～240）、fontWeight（400/700）、color（颜色）、keywords（最多 20 项）、
    整数 particleCount（1～200），一个只读的 glow，以及 (90, 760, 900, 400) 的内容区域。
    """
    from generated.imv.sprite.v1 import sprite_pb2 as sprite_pb

    width, height, fps = canvas
    schema = {"type": "object", "properties": {
        "text": {"type": "string", "minLength": 1, "maxLength": 40},
        "fontSize": {"type": "number", "minimum": 24, "maximum": 240},
        "fontWeight": {"type": "integer", "enum": [400, 700]},
        "color": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$"},
        "keywords": {"type": "array", "items": {"type": "string", "maxLength": 100}, "maxItems": 20},
        "glow": {"type": "number", "minimum": 0, "maximum": 1},
        "particleCount": {"type": "integer", "minimum": 1, "maximum": 200},
    }, "additionalProperties": False}
    defaults = {"text": "标题", "fontSize": 64, "fontWeight": 400, "color": "#FFFFFF", "keywords": [], "glow": 0.5, "particleCount": 20}
    editable = sprite_pb.OPERATOR_ACCESS_VISIBLE_EDITABLE
    number = lambda value: sprite_pb.ScalarValue(number_value=value)
    sprite = sprite_pb.PublishedSprite(
        sprite_id=sprite_id, name="标题", kind=kind,
        canvas=sprite_pb.SpriteCanvas(width=width, height=height, fps=fps, preview_frames=90),
        tsx_code="", code_sha256=hashlib.sha256(b"").hexdigest(),
        config_schema_json=json.dumps(schema), default_config_json=json.dumps(defaults, ensure_ascii=False),
        content_box=sprite_pb.SpriteBox(x=90, y=760, width=900, height=400),
        parameters=[
            sprite_pb.SpriteParameter(key="text", label="文字", access=editable, default_value=sprite_pb.ScalarValue(string_value="标题"), min_length=1, max_length=40),
            sprite_pb.SpriteParameter(key="fontSize", label="字号", access=editable, default_value=number(64), minimum=24, maximum=240),
            sprite_pb.SpriteParameter(key="fontWeight", label="字重", access=editable, default_value=number(400), allowed_values=[number(400), number(700)]),
            sprite_pb.SpriteParameter(key="color", label="颜色", access=editable, default_value=sprite_pb.ScalarValue(string_value="#FFFFFF"), format=sprite_pb.SPRITE_PARAMETER_FORMAT_COLOR),
            sprite_pb.SpriteParameter(key="keywords", label="关键词", access=editable, default_value=sprite_pb.ScalarValue(string_list_value=sprite_pb.StringList()), max_items=20, max_length=100),
            sprite_pb.SpriteParameter(key="glow", label="光晕", access=sprite_pb.OPERATOR_ACCESS_VISIBLE_READ_ONLY, default_value=number(0.5)),
            sprite_pb.SpriteParameter(key="particleCount", label="粒子数", access=editable, default_value=number(20), minimum=1, maximum=200, format=sprite_pb.SPRITE_PARAMETER_FORMAT_INTEGER),
        ],
    )
    return sprite


def seed_sprite(sprite_id: str = SPRITE_ID, canvas: tuple[int, int, int] = (1080, 1920, 30), kind: int = 1) -> str:
    """在当前共享库（替身或真实 MySQL）里放一条 `published_sprite` 发布记录并返回其 ID，供保存引用它的模板。需先启用 template_db 或真实库。"""
    from google.protobuf.json_format import MessageToDict

    sprite = published_sprite(sprite_id, canvas, kind)
    sprite_store.insert(
        sprite_id=sprite_id, source_version_id=str(uuid4()), kind=kind, text_prop="", keywords_prop="",
        published_at=datetime.now(UTC), data=MessageToDict(sprite, preserving_proto_field_name=True),
    )
    return sprite_id


@pytest.fixture
def template_payload() -> dict:
    """提供最小合法模板，每次测试获取独立可修改的 JSON 请求。"""
    return {
        "name": "测试模板",
        "description": "标题淡入",
        "tracks": [template_track(titleIn="in/fade_in"), template_track("subtitle")],
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
    """隔离真实配置和 FFmpeg 探测；接口用例不依赖 CI 安装视频工具。"""
    from server.video_composition import service
    from server.video_composition.settings import Settings

    monkeypatch.setattr(service, "shutil", SimpleNamespace(which=lambda name: "ffmpeg"))
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
                    "styleId": str(template.template_id), "materials": []},
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
    """读取实际 JSON 文件与业务任务时间；默认展平事件，raw=True 检查模块结构。"""
    from sqlalchemy import select
    from server.video_composition.store import tasks
    from server.video_composition.execution_log import LOG_ROOT, LOG_LOCK, LOG_QUEUE, MODULES, expand_columns, read_log

    def read(task_id=None, *, raw=False):
        """用同一时间与动作关联输入输出，错误信息直接来自 error_log。"""
        LOG_QUEUE.join()
        statement = select(tasks.c.task_id, tasks.c.status, tasks.c.stage, tasks.c.created_at,
                           tasks.c.updated_at).order_by(tasks.c.created_at)
        if task_id is not None:
            statement = statement.where(tasks.c.task_id == task_id)
        with template_db.connect() as connection:
            rows = [dict(row) for row in connection.execute(statement).mappings()]
        with LOG_LOCK:
            rows = [{**row, **read_log(row["task_id"]), "task_created_at": row["created_at"],
                     "task_finished_at": row["updated_at"] if row["status"] in ("succeeded", "failed") else None}
                    for row in rows if (LOG_ROOT / "video_composition" / row["task_id"]).exists()]
        if raw:
            return rows
        events = []
        for row in rows:
            row = {**row, **expand_columns(row)}
            for module in MODULES:
                section = row[module]
                inputs = {(item["time"], item["action"]): item["data"] for item in section["input"]}
                outputs = {(item["time"], item["action"]): item["data"] for item in section["output"]}
                for entry in section["execute_log"] + section["error_log"]:
                    if entry["time"] is None:
                        continue
                    group, event = entry["action"].split(".", 1)
                    details = {**entry.get("error", {}), "step": group}
                    key = (entry["time"], entry["action"])
                    if key in inputs:
                        details["input"] = inputs[key]
                    if key in outputs:
                        details["output"] = outputs[key]
                    events.append({
                        "task_id": row["task_id"], "event": event, "stage": group, "status": entry["status"],
                        "created_at": datetime.fromisoformat(entry["time"]).replace(tzinfo=None),
                        "task_created_at": row["task_created_at"],
                        "task_finished_at": row["task_finished_at"] if entry["status"] in ("succeeded", "failed") else None,
                        "details": details,
                    })
        events.sort(key=lambda item: (item["task_created_at"], item["created_at"]))
        return events

    return read
