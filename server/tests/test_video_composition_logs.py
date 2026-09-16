"""执行日志的脱敏、事务、时间和并发测试；pytest 使用隔离 SQLite，不访问真实服务。"""

import asyncio
from copy import deepcopy
import json

from pydantic import BaseModel, ValidationError
import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from server.video_composition import store
from server.video_composition.execution_log import exception_details, sanitize


def test_log_redacts_nested_credentials_and_url_queries():
    """日志保留业务字段与媒体路径，但递归移除凭证、签名及异常消息中的鉴权。"""
    data = {"Authorization": "Bearer hidden-auth", "api_key": "hidden-api", "accessKeyId": "hidden-id",
            "nested": [{"password": "hidden-pass", "match_callback_token": "hidden-token"}],
            "url": "https://user:hidden-userpass@media.test/video.mp4?Signature=hidden-query#private",
            "message": 'failed Authorization: Bearer hidden-header; password="hidden-inline"; '
                       'request=https://media.test/a?token=hidden-msg',
            "level": 2, "group_id": [1, 2], "keyword": "关键词", "empty": None, "invalid_time": float("nan")}
    original = deepcopy(data)
    cleaned = sanitize(data)
    assert "hidden-" not in json.dumps(cleaned, ensure_ascii=False)
    assert cleaned["url"] == "https://media.test/video.mp4"
    assert cleaned["group_id"] == [1, 2] and cleaned["keyword"] == "关键词"
    assert cleaned["invalid_time"] == "nan" and cleaned["empty"] is None
    assert data["url"] == original["url"] and data["Authorization"] == original["Authorization"]


def test_exception_log_keeps_root_cause_without_validation_or_sql_inputs():
    """from None 包装仍保留原始失败位置；校验/数据库错误不写输入或 SQL 参数。"""
    try:
        try:
            raise ValueError("VOD 成片源文件暂不可读取")
        except ValueError:
            raise RuntimeError("播放地址获取失败") from None
    except RuntimeError as exc:
        details = exception_details(exc)
    assert [item["type"] for item in details["exceptions"]] == ["RuntimeError", "ValueError"]
    assert details["exceptions"][1]["message"] == "VOD 成片源文件暂不可读取"
    assert details["exceptions"][1]["frames"][-1]["function"] == "test_exception_log_keeps_root_cause_without_validation_or_sql_inputs"

    class Input(BaseModel):
        """构造带敏感原始输入的类型校验失败。"""
        number: int

    try:
        Input(number="hidden-input")
    except ValidationError as exc:
        assert "hidden-input" not in json.dumps(exception_details(exc))
    sql_error = OperationalError("hidden-sql", {"password": "hidden-param"}, Exception("hidden-driver"))
    assert "hidden-" not in json.dumps(exception_details(sql_error))


def test_log_events_keep_task_times_and_cas_history(composition_case, composition_settings, composition_logs):
    """任务创建/完成时间不会被后续通知或查询覆盖，陈旧状态更新不产生成功日志。"""
    store.initialize_schema()
    store.initialize_schema()
    record = store.create(composition_case["request"], composition_settings.output())
    current = store.advance(record, "template", template=composition_case["template"])
    assert store.advance(record, "asr") is None
    final = store.advance(current, "failed", status="failed", error={"stage": "template", "message": "模板不存在"})
    assert store.advance(final, "asr") is None
    assert store.get(record["task_id"])["data"]["template"] == composition_case["template"]
    assert store.get(record["task_id"])["status"] == "failed"
    store.add_log(final, "response_ready", {"http_status": 200})
    rows = composition_logs(record["task_id"])
    assert [row["event"] for row in rows] == ["submitted", "stage_updated", "task_finished", "response_ready"]
    assert all(row["task_created_at"] == record["created_at"].replace(tzinfo=None) for row in rows)
    assert all(row["task_finished_at"] is None for row in rows[:2])
    assert all(row["task_finished_at"] == final["updated_at"].replace(tzinfo=None) for row in rows[2:])
    assert rows[-1]["created_at"] >= rows[-1]["task_finished_at"]


@pytest.mark.parametrize("operation", ["create", "advance", "notification"])
def test_log_write_failure_rolls_back_related_state(template_db, composition_case, composition_settings, composition_logs, operation):
    """关键日志插入失败时同事务的任务/通知状态也回滚，避免没有日志的已确认推进。"""
    store.initialize_schema()
    record = store.create(composition_case["request"], composition_settings.output())
    if operation == "notification":
        record = store.advance(record, "completed", status="succeeded", notification_status="pending")
    before = composition_logs()

    def reject_log(connection, cursor, statement, parameters, context, executemany):
        """模拟日志表写入失败，其余数据库操作使用真实事务。"""
        if statement.startswith(("INSERT INTO video_composition_logs", "UPDATE video_composition_logs")):
            raise OperationalError("log insert", {}, Exception("database unavailable"))

    event.listen(template_db, "before_cursor_execute", reject_log)
    try:
        with pytest.raises(OperationalError):
            if operation == "create":
                store.create(composition_case["request"], composition_settings.output())
            elif operation == "advance":
                store.advance(record, "template")
            else:
                store.notification_status(record, "sending")
    finally:
        event.remove(template_db, "before_cursor_execute", reject_log)
    assert composition_logs() == before and store.get(record["task_id"]) == record
    assert len(store.pending([], 10)) == 1


@pytest.mark.anyio
async def test_concurrent_log_append_and_historical_task(composition_case, composition_settings, composition_logs, template_db):
    """历史任务一行聚合；并发合并不丢事件，且不改变任务状态、版本和时间。"""
    store.initialize_schema()
    record = store.create(composition_case["request"], composition_settings.output())
    with template_db.begin() as connection:
        connection.execute(store.execution_logs.delete())
    await asyncio.gather(*(asyncio.to_thread(store.add_log, record, "probe", {"index": i}) for i in range(12)))
    rows = composition_logs(record["task_id"])
    assert len(rows) == 12 and sorted(row["details"]["index"] for row in rows) == list(range(12))
    assert store.get(record["task_id"]) == record
    saved = composition_logs(record["task_id"], raw=True)
    assert len(saved) == 1 and saved[0]["detail"]["记录条数"] == 12
    assert saved[0]["detail"]["从提交开始记录"] is False


@pytest.mark.anyio
async def test_step_failure_and_cancellation_leave_input_log(composition_case, composition_settings, composition_logs, composition_runtime):
    """异常和取消均记录步骤输入及结果事件，异常原样抛出且不记录虚假成功。"""
    store.initialize_schema()
    record = store.create(composition_case["request"], composition_settings.output())

    async def fail():
        """模拟有明确错误原因的步骤失败。"""
        raise ValueError("素材不存在")

    async def cancel():
        """模拟运行期间收到取消。"""
        raise asyncio.CancelledError

    with pytest.raises(ValueError, match="素材不存在"):
        await composition_runtime.step(record, "example", fail, {"text": "测试"})
    with pytest.raises(asyncio.CancelledError):
        await composition_runtime.step(record, "cancel", cancel, {})
    rows = composition_logs(record["task_id"])
    assert [row["event"] for row in rows] == ["submitted", "step_started", "step_failed", "step_started", "step_cancelled"]
    assert rows[1]["details"]["input"] == {"text": "测试"}
    assert rows[2]["details"]["exceptions"][0]["message"] == "素材不存在"


def test_one_row_groups_complete_io_errors_and_readable_chinese(composition_case, composition_settings, composition_logs, template_db):
    """新任务一行保留提交、步骤输入输出、报错与结束；中文及嵌套 SDK JSON 不被双重编码。"""
    from sqlalchemy import select, text

    store.initialize_schema()
    record = store.create(composition_case["request"], composition_settings.output())
    running = store.advance(record, "template")
    store.add_log(running, "step_started", {"step": "template", "input": {"styleId": "模板编号"}})
    store.add_log(running, "step_finished", {"step": "template", "output": {"Timeline": json.dumps({"Content": "标题中文", "token": "hidden-json"})}})
    failed = store.advance(running, "failed", status="failed", error={"stage": "template", "message": "模板错误"})
    # 陈旧查询仍可记入事件，但不能把聚合行的终态覆盖为 processing。
    store.add_log(running, "response_started", {"source": "query", "input": {"task_id": record["task_id"]}})
    saved, = composition_logs(record["task_id"], raw=True)
    assert saved["status"] == "failed" and saved["task_finished_at"] == failed["updated_at"].replace(tzinfo=None)
    detail = saved["detail"]
    assert detail["从提交开始记录"] is True and detail["记录条数"] == 6
    assert detail["阶段记录"]["任务提交"]["输入"][0]["内容"]["text"] == composition_case["request"]["text"]
    phase = detail["阶段记录"]["读取模板"]
    assert phase["输入"][0]["内容"] == {"styleId": "模板编号"}
    assert phase["输出"][0]["内容"]["Timeline"] == {"Content": "标题中文", "token": "[REDACTED]"}
    assert phase["错误日志"][0]["错误原因"] == "模板错误"
    with template_db.connect() as connection:
        raw = connection.scalar(text("SELECT detail FROM video_composition_logs"))
        assert "标题中文" in raw and "\\u" not in raw and "hidden-json" not in raw
        assert connection.scalar(select(store.tasks.c.version)) == failed["version"]


def test_nested_json_chinese_is_decoded_without_corrupting_plain_text():
    """只展开 JSON 对象/数组，普通中文、反斜杠文本与非 JSON 字符串保持原意。"""
    nested = json.dumps({"items": json.dumps([{"keyword": "关键词", "api_key": "hidden"}])})
    assert sanitize(nested) == {"items": [{"keyword": "关键词", "api_key": "[REDACTED]"}]}
    assert sanitize("普通中文 C:\\users\\name") == "普通中文 C:\\users\\name"
    assert sanitize("[不是 JSON]") == "[不是 JSON]"


@pytest.fixture
def legacy_logs(template_db, composition_case, composition_settings):
    """构建与上一版本一致的事件表，两个任务分别覆盖新提交与只有查询记录的旧任务。"""
    from datetime import UTC, datetime
    from sqlalchemy import JSON, Column, DateTime, Integer, MetaData, String, Table

    store.initialize_schema()
    records = [store.create(composition_case["request"], composition_settings.output()) for _ in range(2)]
    store.execution_logs.drop(template_db)
    old = Table("video_composition_logs", MetaData(),
                Column("id", Integer, primary_key=True), Column("task_id", String(36)),
                Column("event", String(40)), Column("stage", String(16)), Column("status", String(16)),
                Column("details", JSON), Column("created_at", DateTime), Column("task_created_at", DateTime),
                Column("task_finished_at", DateTime))
    old.create(template_db)
    with template_db.begin() as connection:
        for index, (record, event_name) in enumerate(((records[0], "submitted"), (records[0], "step_finished"), (records[1], "response_ready"))):
            connection.execute(old.insert().values(id=index + 1, task_id=record["task_id"], event=event_name,
                stage="queued", status="queued", details={"step": "template", "output": json.dumps({"text": "迁移中文"})},
                created_at=datetime.now(UTC).replace(tzinfo=None), task_created_at=record["created_at"].replace(tzinfo=None)))
    return old, records


def test_legacy_migration_preserves_every_event_and_backup(legacy_logs, template_db, composition_logs):
    """旧三条事件合为两任务行，保留完整备份及顺序，重启不重复合并且可继续追加。"""
    from sqlalchemy import func, inspect, select, text

    old, records = legacy_logs
    store.initialize_schema()
    store.initialize_schema()
    saved = composition_logs(raw=True)
    assert len(saved) == 2 and [r["detail"]["记录条数"] for r in saved] == [2, 1]
    assert [r["detail"]["从提交开始记录"] for r in saved] == [True, False]
    assert [r["event"] for r in composition_logs()] == ["submitted", "step_finished", "response_ready"]
    assert composition_logs()[1]["details"]["output"] == {"text": "迁移中文"}
    with template_db.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM video_composition_logs_events_backup")) == 3
        assert connection.scalar(select(func.count()).select_from(store.execution_logs)) == 2
        assert "detail" in {c["name"] for c in inspect(connection).get_columns(old.name)}
    store.add_log(records[0], "probe", {"input": "迁移后继续"})
    assert composition_logs(records[0]["task_id"], raw=True)[0]["detail"]["记录条数"] == 3


def test_legacy_migration_failure_keeps_original_table(legacy_logs, template_db):
    """合并失败不替换或删除原日志，中间表阻止盲目重跑以免覆盖排错数据。"""
    from sqlalchemy import func, inspect, select

    old, _ = legacy_logs

    def reject_merged(connection, cursor, statement, parameters, context, executemany):
        """只拒绝向中间表写入，原表始终可读取。"""
        if statement.startswith("INSERT INTO video_composition_logs_merged"):
            raise OperationalError("merge failed", {}, Exception("unavailable"))

    event.listen(template_db, "before_cursor_execute", reject_merged)
    try:
        with pytest.raises(OperationalError):
            store.initialize_schema()
    finally:
        event.remove(template_db, "before_cursor_execute", reject_merged)
    with template_db.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(old)) == 3
        assert not inspect(connection).has_table("video_composition_logs_events_backup")
    with pytest.raises(RuntimeError, match="不会覆盖历史数据"):
        store.initialize_schema()


def test_beijing_migration_restores_real_snapshots_once(template_db, composition_case, composition_settings, composition_logs):
    """UTC 跨日转换一次，原始请求和已存快照可读；不补造 ASR、执行事件或未知异常。"""
    from datetime import datetime, timedelta
    from sqlalchemy import select

    store.initialize_schema()
    record = store.create(composition_case["request"], composition_settings.output())
    old_created = datetime(2026, 9, 14, 20, 10, 0)
    data = {**record["data"], "template": composition_case["template"],
            "segmentation": {"segments": composition_case["segments"]}, "matches": composition_case["matches"],
            "match_request": {"taskId": "旧匹配任务"}, "timeline": {"VideoTracks": []},
            "ims_request": {"timeline": '{"VideoTracks": []}'}, "timeline_warnings": ["转场已缩短"],
            "result": {"mediaId": "real-snapshot-id", "durationSeconds": 8.02},
            "notification_status": "failed", "ims_deadline": "2026-09-15T00:00:00+00:00"}
    data.pop("storage_timezone")
    with template_db.begin() as connection:
        connection.execute(store.execution_logs.delete())
        connection.execute(store.tasks.update().values(status="succeeded", stage="completed", data=data,
                           created_at=old_created, updated_at=old_created + timedelta(minutes=1)))
    store.initialize_schema()
    converted = store.get(record["task_id"])
    saved, = composition_logs(raw=True)
    assert converted["created_at"].isoformat() == "2026-09-15T04:10:00+08:00"
    assert saved["task_finished_at"] == datetime(2026, 9, 15, 4, 11)
    assert saved["detail"]["原始输入"] == composition_case["request"]
    assert set(saved["detail"]["历史补录"]) == {"说明", "语音识别原始输出", "通知错误", "时间线警告"}
    assert saved["detail"]["历史补录"]["时间线警告"] == ["转场已缩短"]
    assert "无法" in saved["detail"]["历史补录"]["语音识别原始输出"]
    assert "通知失败" in saved["detail"]["历史补录"]["通知错误"]
    assert saved["detail"]["记录条数"] == 0
    assert saved["detail"]["阶段记录"]["文本切分"]["输出"][0]["内容"]["segments"] == composition_case["segments"]
    phases = saved["detail"]["阶段记录"]
    assert phases["读取模板"]["输出"][0]["内容"] == composition_case["template"]
    assert phases["提交素材匹配"]["输入"][0]["内容"] == {"taskId": "旧匹配任务"}
    assert phases["素材匹配"]["输出"][0]["内容"] == composition_case["matches"]
    assert phases["组装视频时间线"]["输出"][0]["内容"] == {"VideoTracks": []}
    assert phases["提交云端合成"]["输入"][0]["内容"] == {"timeline": {"VideoTracks": []}}
    assert saved["detail"]["阶段记录"]["通知调用方"]["错误日志"][0]["时间"] is None
    assert saved["detail"]["最终输出"]["视频链接"] is None
    store.initialize_schema()
    assert store.get(record["task_id"]) == converted and composition_logs(raw=True) == [saved]
    assert converted["data"]["ims_deadline"] == data["ims_deadline"]
    with template_db.connect() as connection:
        from sqlalchemy import text
        assert connection.scalar(text("SELECT created_at FROM video_compositions_utc_backup")) == str(old_created) + ".000000"
        assert connection.scalar(select(store.tasks.c.version)) == record["version"]


def test_old_aggregate_events_become_readable_with_same_instant(template_db, composition_case, composition_settings, composition_logs):
    """保留已有事件和原始输入输出；事件显式 +00:00 转成 +08:00，异常不变成假成功。"""
    from datetime import datetime

    store.initialize_schema()
    record = store.create(composition_case["request"], composition_settings.output())
    old = {"stages": {"asr": {"logs": [
        {"sequence": 1, "event": "step_started", "time": "2026-09-14T10:00:00+00:00", "stage": "asr", "status": "processing", "step": "asr", "input": {"audio_url": "https://media.test/tts.wav"}}],
        "errors": [{"sequence": 2, "event": "step_failed", "time": "2026-09-14T10:00:01+00:00", "stage": "asr", "status": "processing", "step": "asr", "exceptions": [{"message": "音频无法下载"}]}]}}}
    with template_db.begin() as connection:
        connection.execute(store.execution_logs.update().values(detail=old, created_at=datetime(2026, 9, 14, 10), updated_at=datetime(2026, 9, 14, 10, 0, 1)))
    store.initialize_schema()
    saved, = composition_logs(raw=True)
    phase = saved["detail"]["阶段记录"]["语音识别"]
    assert phase["执行日志"][0]["说明"] == "开始语音识别，输入已记录"
    assert phase["错误日志"][0]["错误原因"] == "音频无法下载"
    assert phase["输入"][0]["内容"] == {"audio_url": "https://media.test/tts.wav"}
    assert phase["输出"] == []
    assert phase["输入"][0]["时间"] == "2026-09-14 18:00:00.000000+08:00"
    assert saved["created_at"] == datetime(2026, 9, 14, 18)
    store.initialize_schema()
    assert composition_logs(raw=True) == [saved]


def test_beijing_migration_failure_rolls_back_times_and_detail(template_db, composition_case, composition_settings, composition_logs):
    """历史补录落库失败时任务时间及转换标记一起回滚，下次启动安全重试。"""
    store.initialize_schema()
    record = store.create(composition_case["request"], composition_settings.output())
    data = dict(record["data"])
    data.pop("storage_timezone")
    with template_db.begin() as connection:
        connection.execute(store.tasks.update().values(data=data))
    before_task, before_logs = store.get(record["task_id"]), composition_logs(raw=True)

    def reject_update(connection, cursor, statement, parameters, context, executemany):
        """只拒绝主日志更新，不阻止备份和任务事务准备。"""
        if statement.startswith("UPDATE video_composition_logs SET"):
            raise OperationalError("write failed", {}, Exception("unavailable"))

    event.listen(template_db, "before_cursor_execute", reject_update)
    try:
        with pytest.raises(OperationalError):
            store.initialize_schema()
    finally:
        event.remove(template_db, "before_cursor_execute", reject_update)
    assert store.get(record["task_id"]) == before_task and composition_logs(raw=True) == before_logs
    store.initialize_schema()
    assert store.get(record["task_id"])["data"]["storage_timezone"] == "Asia/Shanghai"


def test_media_urls_preserve_actual_output_without_callback_credentials():
    """用户提供和接口返回的媒体链接保留签名，回调令牌和服务鉴权仍脱敏。"""
    payload = {"videoUrl": "https://media.test/video.mp4?Signature=actual-signature", "Authorization": "secret",
               "callbackUrl": "https://callback.test/result?token=hidden"}
    cleaned = sanitize(payload)
    assert cleaned["videoUrl"] == payload["videoUrl"]
    assert cleaned["callbackUrl"] == "https://callback.test/result"
    assert cleaned["Authorization"] == "[REDACTED]"


def test_compaction_removes_only_identical_history_copies():
    """删掉历史补录中的同文副本，保留不同版本、无对应阶段的独有数据和完整效果字段。"""
    from server.video_composition.execution_log import compact_detail

    timeline = {"SubtitleTrackClips": [{"EffectColorStyle": "CS0003-000003", "AdaptMode": "AutoWrap", "Content": "中文正文"}]}
    detail = {"历史补录": {"视频时间线的输出": deepcopy(timeline), "云端合成的输入": {"timeline": "独有旧版本"},
                         "读取模板的输出": {"name": "尚无阶段记录"}, "说明": "历史数据，非补造事件"},
              "阶段记录": {"组装视频时间线": {"输入": [], "输出": [{"内容": timeline}], "执行日志": [], "错误日志": []},
                           "提交云端合成": {"输入": [{"内容": {"timeline": timeline}}], "输出": [], "执行日志": [], "错误日志": []}}}
    before_phases = deepcopy(detail["阶段记录"])
    compact_detail(detail)
    assert "视频时间线的输出" not in detail["历史补录"]
    assert detail["历史补录"]["云端合成的输入"] == {"timeline": "独有旧版本"}
    assert detail["历史补录"]["读取模板的输出"] == {"name": "尚无阶段记录"}
    assert detail["阶段记录"] == before_phases
    snapshot = deepcopy(detail)
    assert compact_detail(detail) == snapshot


def test_stage_snapshot_retained_until_actual_output_is_saved(composition_case, composition_settings, composition_logs):
    """阶段输出尚未落库时保留快照；同文输出到达后删除副本，重复真实调用和错误日志仍全部保留。"""
    store.initialize_schema()
    record = store.create(composition_case["request"], composition_settings.output())
    template = composition_case["template"]
    current = store.advance(record, "asr", template=template)
    phase = composition_logs(raw=True)[0]["detail"]["阶段记录"]["语音识别"]
    assert phase["执行日志"][0]["详情"]["template"] == template
    store.add_log(current, "step_finished", {"step": "template", "output": template})
    store.add_log(current, "step_failed", {"step": "template", "exceptions": [{"message": "读取失败，请重试"}]})
    store.add_log(current, "step_finished", {"step": "template", "output": template})
    saved, = composition_logs(raw=True)
    phases = saved["detail"]["阶段记录"]
    assert "template" not in phases["语音识别"]["执行日志"][0]["详情"]
    assert phases["语音识别"]["执行日志"][0]["数据位置"]["读取模板的输出"] == "读取模板 → 输出"
    assert [item["内容"] for item in phases["读取模板"]["输出"]] == [template, template]
    assert phases["读取模板"]["错误日志"][0]["错误原因"] == "读取失败，请重试"
    assert saved["detail"]["记录条数"] == 5
    assert store.get(record["task_id"]) == current


def test_long_json_preserves_complete_effect_values_and_tail(composition_case, composition_settings, composition_logs, template_db):
    """超过常见单元格预览长度的 JSON 完整写入，SDK 嵌套串解析和整理不截断枚举或末尾内容。"""
    from sqlalchemy import select

    store.initialize_schema()
    record = store.create(composition_case["request"], composition_settings.output())
    timeline = {"EffectColorStyle": "CS0003-000003", "AdaptMode": "AutoWrap", "Content": "长文本验证。" * 12000, "末尾": "完整结束"}
    store.add_log(record, "step_finished", {"step": "assembling", "output": {"timeline": json.dumps(timeline)}})
    current = store.advance(record, "submitting", timeline=timeline)
    saved, = composition_logs(raw=True)
    assert saved["detail"]["阶段记录"]["组装视频时间线"]["输出"][0]["内容"]["timeline"] == timeline
    assert "timeline" not in saved["detail"]["阶段记录"]["准备云端合成"]["执行日志"][0]["详情"]
    with template_db.connect() as connection:
        assert connection.scalar(select(store.tasks.c.data))["timeline"] == timeline
    assert store.get(record["task_id"]) == current
