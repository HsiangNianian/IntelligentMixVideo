"""执行日志的脱敏、事务、时间和并发测试；pytest 使用隔离 SQLite，不访问真实服务。"""

import asyncio
from copy import deepcopy
import json

from pydantic import BaseModel, ValidationError
import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from server.video_composition import store
from server.video_composition.execution_log import (
    MODULES, add_event, append_event, compact_columns, exception_details, expand_columns,
    sanitize, split_detail, update_summary,
)


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
    await asyncio.gather(*(asyncio.to_thread(store.add_log, record, "probe", {"input": {"index": i}}) for i in range(12)))
    rows = composition_logs(record["task_id"])
    assert len(rows) == 12 and sorted(row["details"]["input"]["index"] for row in rows) == list(range(12))
    assert store.get(record["task_id"]) == record
    saved = composition_logs(record["task_id"], raw=True)
    assert len(saved) == 1 and len(saved[0]["request"]["execute_log"]) == 12
    assert len(saved[0]["request"]["input"]) == 12


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


def test_module_columns_keep_io_errors_and_only_three_execution_fields(composition_case, composition_settings, composition_logs, template_db):
    """七列各自存储，不新增 detail；陈旧事件不能覆盖任务终态，中文和嵌套 JSON 保持可读。"""
    from sqlalchemy import inspect, text

    store.initialize_schema()
    record = store.create(composition_case["request"], composition_settings.output())
    running = store.advance(record, "template")
    store.add_log(running, "step_started", {"step": "template", "input": {"styleId": "模板编号"}})
    store.add_log(running, "step_finished", {"step": "template", "output": {"nested": json.dumps({"text": "中文", "token": "hidden"})}})
    failed = store.advance(running, "failed", status="failed", error={"stage": "template", "message": "模板错误"})
    store.add_log(running, "response_started", {"input": {"task_id": record["task_id"]}})
    saved, = composition_logs(raw=True)
    assert saved["status"] == "failed" and saved["task_finished_at"] == failed["updated_at"].replace(tzinfo=None)
    for name in MODULES:
        assert set(saved[name]) == {"input", "output", "execute_log", "error_log"}
        assert all(set(entry) == {"time", "action", "status"} for entry in saved[name]["execute_log"])
    assert saved["template"]["input"][0]["data"] == {"styleId": "模板编号"}
    assert saved["template"]["output"][0]["data"] == {"nested": {"text": "中文", "token": "[REDACTED]"}}
    assert saved["template"]["error_log"][0]["error"]["message"] == "模板错误"
    assert all(not items for items in saved["asr"].values())
    with template_db.connect() as connection:
        assert "detail" not in {c["name"] for c in inspect(connection).get_columns("video_composition_logs")}
        raw = connection.scalar(text("SELECT template FROM video_composition_logs"))
        assert "中文" in raw and "\\u" not in raw and "hidden" not in raw


def test_final_submitted_timeline_and_long_asr_are_complete(composition_case, composition_settings, composition_logs):
    """不把中间组装结果当最终时间线；超过一 MB 的 ASR、重复调用与末尾字段均完整保留。"""
    store.initialize_schema()
    record = store.create(composition_case["request"], composition_settings.output())
    raw = {"text": "完整文字" * 100000, "tail": "结束"}
    for _ in range(2):
        store.add_log(record, "step_finished", {"step": "asr", "output": raw})
    store.add_log(record, "step_finished", {"step": "assembling", "output": {"timeline": {"Content": "中间版"}, "warnings": []}})
    assert composition_logs(raw=True)[0]["timeline"]["output"] == []
    timeline = {"Content": "最终版", "EffectColorStyle": "CS0003-000003", "AdaptMode": "AutoWrap"}
    payload = {"timeline": json.dumps(timeline), "output_media_config": '{"Width":1080}', "client_token": "hidden"}
    current = store.advance(record, "submitting", ims_request=payload)
    store.add_log(current, "step_started", {"step": "ims_submit", "input": payload})
    saved, = composition_logs(raw=True)
    assert saved["asr"]["output"][1]["data"] == {"$log_ref": "#/asr/output/0/data"}
    assert [item["data"] for item in expand_columns(saved)["asr"]["output"]] == [raw, raw]
    assert [item["data"] for item in saved["timeline"]["output"]] == [timeline]
    assert saved["timeline"]["input"][0]["data"] == {"output_media_config": {"Width": 1080}, "client_token": "[REDACTED]"}
    assert "中间版" not in json.dumps(saved, ensure_ascii=False, default=str)
    assert "hidden" not in json.dumps(saved, default=str)


@pytest.fixture
def legacy_detail_logs(template_db, composition_case, composition_settings):
    """构造待迁移的真实 detail 表与任务，保留完整输入输出和历史缺失说明。"""
    from sqlalchemy import JSON, Column, MetaData, Table

    store.initialize_schema()
    record = store.create(composition_case["request"], composition_settings.output())
    store.execution_logs.drop(template_db)
    old = Table("video_composition_logs", MetaData(), *(
        Column(c.name, c.type, primary_key=c.primary_key, nullable=c.nullable, unique=c.unique)
        for c in store.execution_logs.columns if c.name not in MODULES
    ), Column("detail", JSON, nullable=False))
    old.create(template_db)
    detail = append_event({}, "submitted", "queued", "queued", {"input": composition_case["request"]}, record["created_at"])
    detail = append_event(detail, "step_finished", "asr", "processing", {"step": "asr", "output": {"text": "完整识别"}}, record["updated_at"])
    detail = update_summary(detail, record)
    with template_db.begin() as connection:
        connection.execute(old.insert().values(id=42, task_id=record["task_id"], stage="queued", status="queued", detail=detail,
            created_at=record["created_at"].replace(tzinfo=None), updated_at=record["updated_at"].replace(tzinfo=None),
            task_created_at=record["created_at"].replace(tzinfo=None)))
    return old, record


def test_detail_migration_keeps_exact_backup_and_restarts_once(legacy_detail_logs, template_db, composition_logs):
    """旧表原样保留；ID、输入输出、时间不丢失，重复启动不重复迁移且可继续写日志。"""
    from sqlalchemy import MetaData, Table, select

    old, record = legacy_detail_logs
    with template_db.connect() as connection:
        before = dict(connection.execute(select(old)).mappings().one())
    store.initialize_schema()
    saved, = composition_logs(raw=True)
    assert saved["id"] == 42
    assert saved["asr"]["output"][0]["data"] == {"text": "完整识别"}
    assert saved["request"]["input"][0]["data"] == record["data"]["request"]
    backup = Table("video_composition_logs_detail_backup", MetaData(), autoload_with=template_db)
    with template_db.connect() as connection:
        assert dict(connection.execute(select(backup)).mappings().one()) == before
    store.initialize_schema()
    assert composition_logs(raw=True) == [saved]
    store.add_log(record, "step_failed", {"step": "asr", "exceptions": [{"message": "迁移后错误"}]})
    assert composition_logs(raw=True)[0]["asr"]["error_log"][0]["error"]["message"] == "迁移后错误"
    created = store.create(record["data"]["request"], record["data"]["output"])
    assert composition_logs(created["task_id"], raw=True)[0]["id"] == 43


@pytest.mark.parametrize("failure", ["insert", "readback"])
def test_module_migration_failure_never_replaces_original(legacy_detail_logs, template_db, failure):
    """写入或读回校验失败均保留原表，中间表阻止盲目覆盖；不触碰业务任务。"""
    from sqlalchemy import inspect, select

    old, record = legacy_detail_logs
    with template_db.connect() as connection:
        before = dict(connection.execute(select(old)).mappings().one())

    def reject_copy(connection, cursor, statement, parameters, context, executemany):
        """插入时注入故障，或在读回之前修改新表内容以验证校验有效。"""
        if statement.startswith("INSERT INTO video_composition_logs_modules"):
            if failure == "insert":
                raise OperationalError("copy failed", {}, Exception("unavailable"))
            cursor.execute("UPDATE video_composition_logs_modules SET status='invalid'")

    event.listen(template_db, "after_cursor_execute", reject_copy)
    try:
        with pytest.raises(OperationalError if failure == "insert" else RuntimeError):
            store.initialize_schema()
    finally:
        event.remove(template_db, "after_cursor_execute", reject_copy)
    with template_db.connect() as connection:
        assert dict(connection.execute(select(old)).mappings().one()) == before
        assert not inspect(connection).has_table("video_composition_logs_detail_backup")
    assert store.get(record["task_id"]) == record
    with pytest.raises(RuntimeError, match="不会覆盖历史数据"):
        store.initialize_schema()


def test_migration_restores_only_existing_snapshots_and_marks_missing_asr(legacy_detail_logs, template_db, composition_logs):
    """无日志旧任务从真实快照补录，UTC 只转换一次，ASR 未保存事实不得伪造成输出。"""
    from datetime import datetime

    old, record = legacy_detail_logs
    data = {**record["data"], "template": {"name": "旧模板"}, "ims_request": {"timeline": '{"Content":"最终提交"}'},
            "notification_status": "failed", "result": {"videoUrl": "https://video.test/final.mp4"}}
    data.pop("storage_timezone")
    with template_db.begin() as connection:
        connection.execute(old.delete())
        connection.execute(store.tasks.update().values(data=data, status="succeeded", stage="completed",
                           created_at=datetime(2026, 9, 14, 20), updated_at=datetime(2026, 9, 14, 21)))
    store.initialize_schema()
    saved, = composition_logs(raw=True)
    assert saved["task_created_at"] == datetime(2026, 9, 15, 4)
    assert saved["task_finished_at"] == datetime(2026, 9, 15, 5)
    assert saved["template"]["output"][0]["data"] == {"name": "旧模板"}
    assert saved["timeline"]["output"][0]["data"] == {"Content": "最终提交"}
    assert saved["asr"]["output"] == []
    assert saved["zos"]["output"][0]["data"] == {"aliyun_video_url": data["result"]["videoUrl"]}
    assert "无法" in json.dumps(saved["request"]["output"], ensure_ascii=False)
    assert saved["request"]["error_log"][0]["time"] is None
    store.initialize_schema()
    assert composition_logs(raw=True) == [saved]


def test_old_aggregate_utc_errors_are_preserved(legacy_detail_logs, template_db, composition_logs):
    """兼容更早的英文聚合格式；错误和原始输入保留，时间统一转换为北京时间。"""
    from datetime import datetime

    old, _ = legacy_detail_logs
    detail = {"stages": {"asr": {"logs": [
        {"sequence": 1, "event": "step_started", "time": "2026-09-14T10:00:00+00:00", "stage": "asr", "status": "processing", "step": "asr", "input": {"audio_url": "https://media.test/tts.wav"}}],
        "errors": [{"sequence": 2, "event": "step_failed", "time": "2026-09-14T10:00:01+00:00", "stage": "asr", "status": "processing", "step": "asr", "exceptions": [{"message": "音频无法下载"}]}]}}}
    with template_db.begin() as connection:
        connection.execute(old.update().values(detail=detail, created_at=datetime(2026, 9, 14, 10), updated_at=datetime(2026, 9, 14, 10, 0, 1)))
    store.initialize_schema()
    saved, = composition_logs(raw=True)
    assert saved["asr"]["error_log"][0]["error"]["message"] == "音频无法下载"
    assert saved["asr"]["input"][0]["time"] == "2026-09-14 18:00:00.000000+08:00"
    assert saved["created_at"] == datetime(2026, 9, 14, 18)


def test_legacy_event_table_migrates_with_all_original_rows_backed_up(legacy_detail_logs, template_db, composition_logs):
    """兼容最早的一事件一行表；原始事件备份和 detail 备份均保留，步骤输出可继续读取。"""
    from datetime import datetime
    from sqlalchemy import JSON, Column, DateTime, Integer, MetaData, String, Table, text

    old, record = legacy_detail_logs
    old.drop(template_db)
    events = Table(old.name, MetaData(), Column("id", Integer, primary_key=True), Column("task_id", String(36)),
                   Column("stage", String(16)), Column("status", String(16)), Column("event", String(40)),
                   Column("details", JSON), Column("created_at", DateTime), Column("task_created_at", DateTime),
                   Column("task_finished_at", DateTime))
    events.create(template_db)
    with template_db.begin() as connection:
        connection.execute(events.insert().values(id=1, task_id=record["task_id"], stage="asr", status="processing",
            event="step_finished", details={"step": "asr", "output": {"text": "迁移中文"}},
            created_at=datetime(2026, 9, 14, 10), task_created_at=datetime(2026, 9, 14, 10)))
    store.initialize_schema()
    assert composition_logs(raw=True)[0]["asr"]["output"][0]["data"] == {"text": "迁移中文"}
    with template_db.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM video_composition_logs_events_backup")) == 1
        assert connection.scalar(text("SELECT COUNT(*) FROM video_composition_logs_detail_backup")) == 1


def test_nested_json_and_media_urls_keep_content_without_credentials():
    """递归展开 SDK JSON，保留媒体 URL，回调凭证仍脱敏，普通文本不变。"""
    nested = json.dumps({"items": json.dumps([{"keyword": "关键词", "api_key": "hidden"}])})
    assert sanitize(nested) == {"items": [{"keyword": "关键词", "api_key": "[REDACTED]"}]}
    payload = {"videoUrl": "https://media.test/v.mp4?Signature=actual", "callbackUrl": "https://notify.test/?token=hidden"}
    assert sanitize(payload) == {"videoUrl": payload["videoUrl"], "callbackUrl": "https://notify.test/"}
    assert sanitize("普通中文 C:\\users\\name") == "普通中文 C:\\users\\name"
    assert sanitize("[不是 JSON]") == "[不是 JSON]"


def test_legacy_response_address_is_available_in_zos():
    """旧成功任务的签名成片地址可能仅保存在最终响应，迁移后也能直接在 ZOS 列查看。"""

    result = {"videoUrl": "https://media.test/final.mp4?Signature=actual", "durationSeconds": 10}
    columns = split_detail({"最终输出": {"status": "succeeded", "result": result}})
    assert columns["zos"]["output"][0]["data"] == {"aliyun_video_url": result["videoUrl"]}
    assert columns["zos"]["execute_log"] == []


def test_module_references_preserve_payloads_events_and_upstream_sources():
    """上游原文、不同版本、重试与错误无损；嵌套键可转义，业务 $ref 不误解析。"""

    columns = split_detail({})
    transcript = {"text": "完整转写" * 100, "$ref": "业务中的普通引用"}
    template = {"tracks": [{"id": "对象" * 80}]}
    segments = [{"text": "切片" * 100}]
    timeline = {"VideoTracks": [{"VideoTrackClips": [{"MediaURL": "https://media.test/" + "x" * 200}]}]}
    result = {"videoUrl": "https://media.test/" + "v" * 200}

    def put(module, direction, data):
        """模拟不同模块的实际输入输出条目，保留每次调用的动作和时间。"""
        columns[module][direction].append({"time": "2026-09-30T10:00:00+08:00", "action": f"{module}.step", "data": data})

    put("asr", "output", transcript)
    put("asr", "output", transcript)
    put("template", "output", template)
    put("segmentation", "input", {"asr_result": transcript})
    put("segmentation", "output", {"a/b~c": segments})
    put("matching", "input", {"llm": {"segments": segments}})
    put("timeline", "input", {"template": template, "segments": segments})
    put("timeline", "output", timeline)
    put("zos", "output", {"MediaProducingJob": {"Timeline": timeline, "Status": "Processing"}})
    put("zos", "output", {"MediaProducingJob": {"Timeline": timeline, "Status": "Success"}})
    put("zos", "output", result)
    put("request", "output", {"result": result})
    columns["zos"]["execute_log"] = [{"time": None, "action": "ims_query.step_finished", "status": "processing"}]
    columns["zos"]["error_log"] = [{"time": None, "action": "ims_query.step_failed", "status": "failed", "error": transcript}]
    before = deepcopy(columns)
    compacted = compact_columns(columns)
    assert columns == before
    expected = deepcopy(before)
    expected["zos"]["output"] = [{**before["zos"]["output"][2], "data": {"aliyun_video_url": result["videoUrl"]}}]
    assert expand_columns(compacted) == expected
    assert compact_columns(compacted) == compacted
    assert compacted["asr"]["output"][0]["data"] == transcript
    assert compacted["asr"]["output"][1]["data"] == {"$log_ref": "#/asr/output/0/data"}
    assert compacted["segmentation"]["input"][0]["data"]["asr_result"] == {"$log_ref": "#/asr/output/0/data"}
    assert compacted["matching"]["input"][0]["data"]["llm"]["segments"] == {"$log_ref": "#/segmentation/output/0/data/a~1b~0c"}
    assert compacted["timeline"]["input"][0]["data"]["template"] == {"$log_ref": "#/template/output/0/data"}
    assert compacted["zos"]["output"][0]["data"] == {"aliyun_video_url": result["videoUrl"]}
    assert compacted["request"]["output"][0]["data"]["result"] == result
    assert len(json.dumps(compacted)) < len(json.dumps(before))


def test_append_rebuilds_references_after_sorting_and_keeps_distinct_versions():
    """较早事件插入导致数组位置变化后仍指向原值；快照补录去重在展开后判断。"""
    from datetime import datetime

    record = {"stage": "asr", "status": "processing"}
    raw = {"text": "第一版" * 100}
    columns = add_event(None, "step_finished", record, {"step": "asr", "output": raw}, datetime.fromisoformat("2026-09-30T10:00:00+08:00"))
    columns = add_event(columns, "step_started", record, {"step": "segmentation", "input": {"asr_result": raw}}, datetime.fromisoformat("2026-09-30T11:00:00+08:00"))
    changed = {"text": "另一版" * 100}
    columns = add_event(columns, "step_finished", record, {"step": "asr", "output": changed}, datetime.fromisoformat("2026-09-30T09:00:00+08:00"))
    assert columns["segmentation"]["input"][0]["data"]["asr_result"] == {"$log_ref": "#/asr/output/1/data"}
    expanded = expand_columns(columns)
    assert [item["data"] for item in expanded["asr"]["output"]] == [changed, raw]
    assert expanded["segmentation"]["input"][0]["data"]["asr_result"] == raw
    assert len(expanded["asr"]["execute_log"]) == 2


def test_matching_callback_moves_to_output_without_losing_referenced_data():
    """旧回调及其引用一起迁移；请求仍在输入，匹配失败原文和执行记录完整保留。"""

    columns = split_detail({})
    request = {"text": "匹配请求" * 80}
    callback = {"taskId": "match-1", "status": "failed", "error": "匹配失败" * 80}
    columns["matching"]["input"] = [
        {"time": "1", "action": "match_submit.step_started", "data": request},
        {"time": "2", "action": "matching.http_response", "data": {"body": {"$log_ref": "#/matching/input/0/data"}}},
        {"time": "3", "action": "matching.match_callback_received", "data": callback},
    ]
    columns["matching"]["output"] = [
        {"time": "4", "action": "matching.match_callback_processed", "data": {"http_status": 200}},
        {"time": "5", "action": "matching.historical_snapshot", "data": {"$log_ref": "#/matching/input/2/data"}},
    ]
    columns["matching"]["execute_log"] = [{"time": "3", "action": "matching.match_callback_received", "status": "processing"}]
    original = deepcopy(columns)
    saved = compact_columns(columns)
    expanded = expand_columns(saved)["matching"]
    assert columns == original
    assert [item["action"] for item in expanded["input"]] == ["match_submit.step_started", "matching.http_response"]
    assert expanded["input"][0]["data"] == expanded["input"][1]["data"]["body"] == request
    assert expanded["output"][0]["action"] == "matching.match_callback_received"
    assert expanded["output"][0]["data"] == expanded["output"][2]["data"] == callback
    assert saved["matching"]["output"][2]["data"] == {"$log_ref": "#/matching/output/0/data"}
    assert expanded["execute_log"] == original["matching"]["execute_log"]
    assert compact_columns(saved) == saved


def test_timeline_requested_fields_remain_inline_even_when_parent_repeats():
    """materials、packRules 子树与父请求均可直接阅读，其他重复字段继续引用。"""

    columns = split_detail({})
    request = {"materials": [{"fileUrl": "https://media.test/" + "x" * 150}],
               "packRules": {"backgroundMusic": {"audioUrl": "https://media.test/" + "y" * 150}}}
    entry = {"time": None, "action": "test", "data": request}
    columns["request"]["input"] = [entry, {**entry, "data": {"request": request}}]
    columns["timeline"]["input"] = [{**entry, "data": {"request": request}}] * 2
    saved = compact_columns(columns)
    assert saved["timeline"]["input"] == columns["timeline"]["input"]
    assert expand_columns(saved) == columns
    assert compact_columns(saved) == saved


def test_zos_keeps_only_distinct_video_links_and_preserves_playback_signature():
    """渲染正文丢弃前展开所有引用；两类地址原文保留，错误和每次执行记录不受影响。"""
    from datetime import datetime

    columns = split_detail({})
    url = "https://aliyun.test/result.mp4?Signature=actual"
    public = "https://zos.test/result.mp4"
    payloads = [("ims_query.step_finished", {"MediaProducingJob": {"Timeline": {"VideoTracks": []}, "Status": "Success"}}),
                ("playback.step_finished", url), ("zos_upload.step_finished", ["key.mp4", public]),
                ("completed.task_finished.result", {"videoUrl": public, "durationSeconds": 5}),
                ("completed.task_finished.zos_object_key", "key.mp4"), ("playback.step_finished", url)]
    columns["zos"]["output"] = [{"time": None, "action": action, "data": data} for action, data in payloads]
    columns["request"]["output"] = [{"time": None, "action": "response.response_ready", "data": {"$log_ref": "#/zos/output/3/data"}}]
    columns["zos"]["execute_log"] = [{"time": None, "action": action, "status": "processing"} for action, _ in payloads]
    saved = compact_columns(columns)
    assert [item["data"] for item in saved["zos"]["output"]] == [{"aliyun_video_url": url}, {"zos_video_url": public}]
    assert expand_columns(saved)["request"]["output"][0]["data"] == payloads[3][1]
    assert saved["zos"]["execute_log"] == columns["zos"]["execute_log"]
    assert compact_columns(saved) == saved
    added = add_event(None, "step_finished", {"stage": "rendering", "status": "processing"},
                      {"step": "playback", "output": url}, datetime.now())
    assert added["zos"]["output"][0]["data"] == {"aliyun_video_url": url}
