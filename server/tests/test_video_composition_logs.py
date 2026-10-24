"""文件执行日志的脱敏、并发与容量清理测试；仅使用临时文件和隔离 SQLite。"""

import asyncio
from copy import deepcopy
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from ssl import SSLCertVerificationError
from threading import Event, Thread

from pydantic import BaseModel, ValidationError
from pymysql.err import OperationalError as MySQLOperationalError
import pytest
from sqlalchemy import event, inspect, text
from sqlalchemy.exc import OperationalError

from server.video_composition import execution_log, service, store
from server.video_composition.execution_log import (
    MODULES, add_event, compact_columns, exception_details, expand_columns, sanitize,
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


@pytest.mark.parametrize("errno,reason", [(1045, "ACCESS_DENIED_ERROR"), (1064, "PARSE_ERROR"), (2013, "CR_SERVER_LOST"), (99999, "UNKNOWN_ERROR")])
def test_database_diagnostics_keep_native_code_without_sql_or_driver_inputs(errno, reason):
    """数据库原生错误码与固定名称进入错误日志，驱动正文、SQL 和参数均不泄漏。"""
    error = OperationalError("hidden-sql", {"text": "hidden-param"}, MySQLOperationalError(errno, "hidden-driver"), connection_invalidated=True)
    details = exception_details(error)
    item = details["exceptions"][0]
    assert item["mysql_errno"] == errno and item["connection_invalidated"] is True
    assert item["message"] == f"MySQL {errno}: {reason}"
    columns = add_event(None, "step_failed", {"stage": "template", "status": "processing"}, details, datetime.now())
    assert columns["template"]["error_log"][0]["error"]["message"] == item["message"]
    assert "hidden-" not in json.dumps(columns)


@pytest.mark.parametrize("failure", ["refused", "timeout", "certificate"])
def test_database_diagnostics_keep_connection_failure_cause(failure):
    """连接拒绝、读超时和证书失败保留底层类型及错误码，不读取驱动正文。"""
    driver = MySQLOperationalError(2003, "hidden-driver")
    if failure == "certificate":
        driver.original_exception = SSLCertVerificationError(1, "hidden-certificate")
        driver.original_exception.verify_code = 10
        expected = {"type": "SSLCertVerificationError", "errno": 1, "verify_code": 10}
    elif failure == "timeout":
        driver.__context__ = TimeoutError("hidden-host")
        expected = {"type": "TimeoutError"}
    else:
        driver.original_exception = ConnectionRefusedError(111, "hidden-host")
        expected = {"type": "ConnectionRefusedError", "errno": 111}
    item = exception_details(OperationalError(None, None, driver))["exceptions"][0]
    assert item["driver_cause"] == expected
    assert "hidden-" not in json.dumps(item)


def test_exception_log_keeps_caller_above_deep_driver_stack():
    """驱动调用层数较深时仍保留业务入口，但不记录源码、局部变量或 SQL 输入。"""
    def query(depth):
        """用多层调用复现真实 SQLAlchemy/PyMySQL 堆栈对业务入口的遮挡。"""
        if depth:
            return query(depth - 1)
        raise OperationalError("hidden-sql", {"text": "hidden-param"}, MySQLOperationalError(1054, "hidden-driver"))

    try:
        query(10)
    except OperationalError as exc:
        item = exception_details(exc)["exceptions"][0]
    assert item["frames"][0]["function"] == "test_exception_log_keeps_caller_above_deep_driver_stack"
    assert all(set(frame) == {"file", "line", "function"} for frame in item["frames"])
    assert "hidden-" not in json.dumps(item)


@pytest.mark.anyio
@pytest.mark.parametrize("write_fails", [False, True])
async def test_dispatch_failure_is_logged_without_database_and_keeps_retrying(monkeypatch, caplog, write_fails):
    """数据库查询失败仍记录控制台和独立文件；文件写入失败也不阻止下一轮调度。"""
    runtime, attempts = service.Runtime(), 0
    loop = asyncio.get_running_loop()

    def pending(exclude, limit):
        """先模拟断连及鉴权失败再恢复，用唤醒事件避免测试等待固定轮询时间。"""
        nonlocal attempts
        attempts += 1
        loop.call_soon_threadsafe(runtime.wake.set)
        if attempts <= 2:
            raise OperationalError("hidden-sql", {"text": "hidden-param"}, MySQLOperationalError(2013 if attempts == 1 else 1045, "hidden-driver"))
        runtime.stopping = True
        return []

    def reject_write(*args, **kwargs):
        """模拟日志目录不可写，错误不得传播到调度循环。"""
        raise OSError("hidden-disk-error")

    monkeypatch.setattr(store, "pending", pending)
    if write_fails:
        monkeypatch.setattr(Path, "write_text", reject_write)
    try:
        await asyncio.wait_for(runtime._dispatch(), timeout=3)
    finally:
        await runtime.close()
    assert attempts == 3 and "hidden-" not in caplog.text
    assert "MySQL 2013: CR_SERVER_LOST" in caplog.text
    assert "MySQL 1045: ACCESS_DENIED_ERROR" in caplog.text
    if write_fails:
        assert "调度诊断写入失败" in caplog.text
    else:
        path = execution_log.LOG_ROOT / "video_composition" / "dispatch-error.json"
        entry = json.loads(path.read_text())
        assert entry["action"] == "dispatch_failed" and entry["status"] == "failed"
        item = entry["error"]["exceptions"][0]
        assert item["mysql_errno"] == 1045 and item["frames"][-1]["function"] == "pending"
        assert "hidden-" not in path.read_text()
        assert list(path.parent.iterdir()) == [path]


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
def test_log_write_failure_keeps_committed_state(composition_case, composition_settings, monkeypatch, caplog, operation):
    """文件写入失败不回滚已提交任务或通知状态，异常仍报告到运行日志。"""
    store.initialize_schema()
    record = store.create(composition_case["request"], composition_settings.output())
    if operation == "notification":
        record = store.advance(record, "completed", status="succeeded", notification_status="pending")

    def reject_write(*args, **kwargs):
        """模拟磁盘无可用空间。"""
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", reject_write)
    if operation == "create":
        saved = store.create(composition_case["request"], composition_settings.output())
    elif operation == "advance":
        saved = store.advance(record, "template")
        assert saved["stage"] == "template"
    else:
        saved = store.notification_status(record, "sending")
        assert saved["data"]["notification_status"] == "sending"
    assert store.get(saved["task_id"]) == saved
    execution_log.LOG_QUEUE.join()
    assert "disk full" in caplog.text


@pytest.mark.anyio
async def test_concurrent_log_append(composition_case, composition_settings, composition_logs):
    """七文件聚合并发事件不丢失，且不改变业务任务状态、版本和时间。"""
    store.initialize_schema()
    record = store.create(composition_case["request"], composition_settings.output())
    execution_log.LOG_QUEUE.join()
    shutil.rmtree(execution_log.LOG_ROOT / "video_composition" / record["task_id"])
    await asyncio.gather(*(asyncio.to_thread(store.add_log, record, "probe", {"input": {"index": i}}) for i in range(12)))
    rows = composition_logs(record["task_id"])
    assert len(rows) == 12 and sorted(row["details"]["input"]["index"] for row in rows) == list(range(12))
    assert store.get(record["task_id"]) == record
    saved = composition_logs(record["task_id"], raw=True)
    assert len(saved) == 1 and len(saved[0]["request"]["execute_log"]) == 12
    assert len(saved[0]["request"]["input"]) == 12


@pytest.mark.anyio
async def test_step_failure_and_cancellation_leave_input_log(composition_case, composition_settings, composition_logs, composition_runtime, caplog):
    """异常和取消均记录步骤输入及结果事件，异常原样抛出且不记录虚假成功。"""
    store.initialize_schema()
    record = store.create(composition_case["request"], composition_settings.output())

    async def fail():
        """模拟有明确错误原因的步骤失败。"""
        raise ValueError("素材不存在")

    async def cancel():
        """模拟运行期间收到取消。"""
        raise asyncio.CancelledError

    with caplog.at_level("INFO", logger=service.logger.name):
        with pytest.raises(ValueError, match="素材不存在"):
            await composition_runtime.step(record, "example", fail, {"text": "测试"})
        with pytest.raises(asyncio.CancelledError):
            await composition_runtime.step(record, "cancel", cancel, {})
    rows = composition_logs(record["task_id"])
    assert [row["event"] for row in rows] == ["submitted", "step_started", "step_failed", "step_started", "step_cancelled"]
    assert rows[1]["details"]["input"] == {"text": "测试"}
    assert rows[2]["details"]["exceptions"][0]["message"] == "素材不存在"
    metrics = [entry for entry in caplog.records if entry.msg.startswith("Composition performance")]
    assert [entry.args[1] for entry in metrics] == ["example", "cancel"]
    assert all(entry.args[-1] >= 0 for entry in metrics)
    assert all("测试" not in entry.getMessage() and "素材不存在" not in entry.getMessage() for entry in metrics)


@pytest.mark.anyio
@pytest.mark.parametrize("module,step", [("request", "notification"), ("template", "template"), ("asr", "asr"),
    ("segmentation", "segmentation"), ("matching", "matching"), ("timeline", "assembling"), ("zos", "zos_upload")])
async def test_each_module_writes_error_detail_file(module, step, composition_case, composition_runtime, composition_logs):
    """各模块错误写真实文件并保存相对路径；重复失败不覆盖，成功事件不生成文件，凭证仍脱敏。"""
    store.initialize_schema()
    record = store.create(composition_case["request"], {})

    async def fail():
        """包含嵌套根因及凭证，验证详细文件保留诊断但不泄漏密钥。"""
        try:
            raise ValueError("上游连接失败 password=hidden-detail")
        except ValueError:
            raise RuntimeError("调用失败") from None

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await composition_runtime.step(record, step, fail, {})
    saved, = composition_logs(record["task_id"], raw=True)
    folder = execution_log.LOG_ROOT / "video_composition" / record["task_id"]
    entries = saved[module]["error_log"]
    assert len(entries) == 2 and len(list((folder / "errors").iterdir())) == 2
    assert len({entry["error"]["detail_log_path"] for entry in entries}) == 2
    for entry in entries:
        relative = Path(entry["error"]["detail_log_path"])
        assert not relative.is_absolute() and relative.parent == Path("errors")
        detail = (folder / relative).read_text()
        stored = json.loads(detail)
        assert stored["time"] == entry["time"] and stored["action"] == entry["action"]
        assert stored["error"]["exceptions"] == entry["error"]["exceptions"]
        assert stored["error"]["exceptions"][-1]["frames"][-1]["function"] == "fail"
        assert "上游连接失败" in detail and "hidden-detail" not in detail
    store.add_log(record, "step_finished", {"step": step, "output": {"ok": True}})
    execution_log.LOG_QUEUE.join()
    assert len(list((folder / "errors").iterdir())) == 2


def test_detail_file_write_failure_keeps_json_error(composition_case, composition_logs, monkeypatch, caplog):
    """仅详细文件写入失败时仍保存 JSON 错误摘要，并继续消费后续事件，不写悬空路径。"""
    store.initialize_schema()
    record = store.create(composition_case["request"], {})
    original = Path.write_text

    def reject_detail(path, *args, **kwargs):
        """只拒绝 .log 文件，保留 JSON 写入以检查降级行为。"""
        if path.suffix == ".log":
            raise OSError("detail disk unavailable")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", reject_detail)
    store.add_log(record, "step_failed", {"step": "asr", "reason": "识别失败"})
    store.add_log(record, "probe", {})
    saved, = composition_logs(record["task_id"], raw=True)
    error = saved["asr"]["error_log"][0]["error"]
    assert error["message"] == "识别失败" and "detail_log_path" not in error
    assert saved["request"]["execute_log"][-1]["action"].endswith(".probe")
    assert "详细错误日志保存失败" in caplog.text


def test_module_files_keep_io_errors_and_only_three_execution_fields(composition_case, composition_settings, composition_logs, template_db):
    """七文件各自存储；陈旧日志不改任务终态，中文和嵌套 JSON 保持可读。"""
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
    assert not inspect(template_db).has_table("video_composition_logs")
    folder = execution_log.LOG_ROOT / "video_composition" / record["task_id"]
    assert {path.name for path in folder.iterdir()} == {f"{name}.json" for name in MODULES} | {"errors"}
    raw = (folder / "template.json").read_text(encoding="utf-8")
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


def test_nested_json_and_media_urls_keep_content_without_credentials():
    """递归展开 SDK JSON，保留媒体 URL，回调凭证仍脱敏，普通文本不变。"""
    nested = json.dumps({"items": json.dumps([{"keyword": "关键词", "api_key": "hidden"}])})
    assert sanitize(nested) == {"items": [{"keyword": "关键词", "api_key": "[REDACTED]"}]}
    payload = {"videoUrl": "https://media.test/v.mp4?Signature=actual", "callbackUrl": "https://notify.test/?token=hidden"}
    assert sanitize(payload) == {"videoUrl": payload["videoUrl"], "callbackUrl": "https://notify.test/"}
    assert sanitize("普通中文 C:\\users\\name") == "普通中文 C:\\users\\name"
    assert sanitize("[不是 JSON]") == "[不是 JSON]"


def test_module_references_preserve_payloads_events_and_upstream_sources():
    """上游原文、不同版本、重试与错误无损；嵌套键可转义，业务 $ref 不误解析。"""

    columns = {name: {key: [] for key in ("input", "output", "execute_log", "error_log")} for name in MODULES}
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


def test_timeline_requested_fields_remain_inline_even_when_parent_repeats():
    """materials、packRules 子树与父请求均可直接阅读，其他重复字段继续引用。"""

    columns = {name: {key: [] for key in ("input", "output", "execute_log", "error_log")} for name in MODULES}
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

    columns = {name: {key: [] for key in ("input", "output", "execute_log", "error_log")} for name in MODULES}
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


@pytest.fixture
def logged_task(composition_case, composition_settings):
    """创建真实任务与七文件日志，可设置终态、通知状态和文件年龄。"""
    store.initialize_schema()

    def create(*, status="succeeded", notification=None, age_days=0):
        """仅调整测试文件时间，不借用目录时间决定清理顺序。"""
        record = store.create(composition_case["request"], composition_settings.output())
        if status != "queued":
            record = store.advance(record, "completed" if status == "succeeded" else "matching", status=status,
                                   **({"notification_status": notification} if notification else {}))
        execution_log.LOG_QUEUE.join()
        folder = execution_log.LOG_ROOT / "video_composition" / record["task_id"]
        stamp = datetime.now().timestamp() - age_days * 86400
        for path in folder.rglob("*"):
            os.utime(path, (stamp, stamp))
        return record, folder

    return create


def log_size(path):
    """返回实际文件字节数，测试容量边界无需创建 50 MB 数据。"""
    return sum(file.stat().st_size for file in path.rglob("*") if file.is_file())


def test_under_limit_keeps_expired_logs_without_querying_tasks(logged_task, monkeypatch):
    """不足或恰好达到容量上限时，即使过期也不删除，不查询任务清理名单。"""
    _, folder = logged_task(age_days=30)

    def unexpected_query():
        """未超容量不得查询数据库终态。"""
        pytest.fail("不应查询清理名单")

    execution_log.cleanup(unexpected_query)
    monkeypatch.setattr(execution_log, "MAX_BYTES", log_size(execution_log.LOG_ROOT))
    execution_log.cleanup(unexpected_query)
    assert folder.exists()


@pytest.mark.parametrize("old_age", [2, 10])
def test_over_limit_deletes_oldest_finished_directory(logged_task, monkeypatch, old_age):
    """无满七天的任务也按文件最新时间清理；达到上限立即停止，目录时间不影响排序。"""
    _, old = logged_task(age_days=old_age)
    _, recent = logged_task(age_days=1)
    os.utime(old, None)
    monkeypatch.setattr(execution_log, "MAX_BYTES", log_size(execution_log.LOG_ROOT) - log_size(old))
    execution_log.cleanup(store.finished_tasks)
    assert not old.exists() and recent.exists()
    assert log_size(execution_log.LOG_ROOT) == execution_log.MAX_BYTES


@pytest.mark.parametrize("status,notification", [("queued", None), ("processing", None),
    ("succeeded", "pending"), ("failed", "sending")])
def test_over_limit_protects_active_and_notifying_tasks(logged_task, monkeypatch, status, notification):
    """活动任务及通知未结束的任务不删，只有这些任务时允许超限。"""
    _, protected = logged_task(status=status, notification=notification, age_days=30)
    _, finished = logged_task(age_days=1)
    monkeypatch.setattr(execution_log, "MAX_BYTES", 1)
    execution_log.cleanup(store.finished_tasks)
    assert protected.exists() and not finished.exists()
    assert log_size(execution_log.LOG_ROOT) > execution_log.MAX_BYTES


@pytest.mark.parametrize("notification", [None, "sent", "failed"])
def test_failed_tasks_with_finished_notifications_can_be_deleted(logged_task, monkeypatch, notification):
    """失败任务和通知最终失败均视作已结束，可整体清理。"""
    _, folder = logged_task(status="failed", notification=notification)
    assert list((folder / "errors").glob("*.log"))
    monkeypatch.setattr(execution_log, "MAX_BYTES", 1)
    execution_log.cleanup(store.finished_tasks)
    assert not folder.exists()


def test_first_write_counts_existing_files_and_whole_log_root(logged_task, monkeypatch):
    """启动不清理；后续写入统计已有目录和其他日志，但只删除已结束合成任务。"""
    _, old = logged_task(age_days=20)
    active, protected = logged_task(status="processing")
    other = execution_log.LOG_ROOT / "other.log"
    other.write_bytes(b"x" * 20000)
    monkeypatch.setattr(execution_log, "MAX_BYTES", 20000)
    store.initialize_schema()
    assert old.exists()
    store.add_log(active, "probe", {"input": {"text": "重启后写入"}})
    execution_log.LOG_QUEUE.join()
    assert not old.exists() and protected.exists() and other.exists()
    assert execution_log.read_log(active["task_id"])["matching"]["input"][-1]["data"] == {"text": "重启后写入"}


def test_partial_temporary_write_preserves_previous_json(logged_task, monkeypatch):
    """临时文件写入中途失败保留原七文件，清除残余临时文件。"""
    record, folder = logged_task(status="processing")
    before = {path.name: path.read_bytes() for path in folder.iterdir()}
    original = Path.write_text

    def fail_asr(path, *args, **kwargs):
        """前两个模块临时写入成功后模拟磁盘写失败。"""
        if path.name == "asr.tmp":
            raise OSError("disk full")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_asr)
    store.add_log(record, "probe", {"input": {"text": "写入失败"}})
    execution_log.LOG_QUEUE.join()
    assert {path.name: path.read_bytes() for path in folder.iterdir()} == before


def test_log_root_does_not_follow_working_directory(logged_task, monkeypatch, tmp_path):
    """切换启动工作目录后仍向同一个后端根目录追加文件。"""
    record, folder = logged_task(status="processing")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    store.add_log(record, "probe", {})
    execution_log.LOG_QUEUE.join()
    assert len(execution_log.read_log(record["task_id"])["matching"]["execute_log"]) == 2
    assert folder.exists() and not (elsewhere / ".log").exists()


def test_old_log_tables_are_never_read_modified_or_migrated(template_db, composition_case, composition_settings):
    """旧表保留原样；初始化及新任务不访问它们，不创建日志备份或替代表。"""
    names = ("video_composition_logs", "video_composition_logs_detail_backup")
    original_tables = set(inspect(template_db).get_table_names())
    with template_db.begin() as connection:
        for name in names:
            connection.execute(text(f"CREATE TABLE {name} (detail TEXT)"))
            connection.execute(text(f"INSERT INTO {name} VALUES ('old log')"))

    def reject_access(connection, cursor, statement, parameters, context, executemany):
        """业务路径对旧日志表的任何访问都令测试失败。"""
        assert "video_composition_logs" not in statement

    event.listen(template_db, "before_cursor_execute", reject_access)
    try:
        store.initialize_schema()
        record = store.create(composition_case["request"], composition_settings.output())
        store.advance(record, "completed", status="succeeded")
    finally:
        event.remove(template_db, "before_cursor_execute", reject_access)
    with template_db.connect() as connection:
        for name in names:
            assert connection.execute(text(f"SELECT detail FROM {name}")).scalars().all() == ["old log"]
    assert set(inspect(template_db).get_table_names()) == {*original_tables, *names, "video_compositions"}


def test_async_log_snapshots_order_time_and_shutdown(logged_task, monkeypatch):
    """慢磁盘不阻止入队；保留提交时数据、时间与顺序，关闭等待积压写完。"""
    record, folder = logged_task(status="processing")
    entered, release, closed = Event(), Event(), Event()
    original = execution_log.write_log

    def slow_write(*args, **kwargs):
        """暂停实际写入，显式验证生产者与关闭行为。"""
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    def close():
        """模拟生产者结束后的正常服务退出。"""
        execution_log.close_logs()
        closed.set()

    monkeypatch.setattr(execution_log, "write_log", slow_write)
    details = {"step": "asr", "output": {"text": "入队原文"}}
    before = datetime.now(execution_log.BEIJING)
    store.add_log(record, "step_finished", details)
    after = datetime.now(execution_log.BEIJING)
    closer = Thread(target=close)
    try:
        assert entered.wait(2)
        details["output"]["text"] = "后续修改"
        store.add_log(record, "probe", {"input": {"index": 2}})
        record["status"] = "failed"
        closer.start()
        assert not closed.wait(0.05)
    finally:
        release.set()
        if closer.ident is not None:
            closer.join(10)
    assert closed.is_set() and execution_log._writer is None
    saved = execution_log.read_log(record["task_id"])
    entry = saved["asr"]["output"][0]
    assert entry["data"] == {"text": "入队原文"}
    assert before <= datetime.fromisoformat(entry["time"]) <= after
    assert saved["asr"]["execute_log"][0]["status"] == "processing"
    assert saved["matching"]["input"][-1]["data"] == {"index": 2}
    assert not execution_log.PENDING and execution_log.LOG_QUEUE.unfinished_tasks == 0


def test_queue_full_waits_without_losing_events(logged_task, monkeypatch):
    """磁盘持续慢导致队列满时等待空位，不无限增长或丢失日志。"""
    from queue import Queue

    record, _ = logged_task(status="processing")
    execution_log.close_logs()
    monkeypatch.setattr(execution_log, "LOG_QUEUE", Queue(maxsize=1))
    entered, release, submitted = Event(), Event(), Event()
    original = execution_log.write_log

    def slow_write(*args, **kwargs):
        """暂停消费者，使小队列确定性达到上限。"""
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    def third():
        """额外生产者仅在获得队列空位后返回。"""
        store.add_log(record, "probe", {"input": {"index": 3}})
        submitted.set()

    monkeypatch.setattr(execution_log, "write_log", slow_write)
    producer = Thread(target=third)
    try:
        store.add_log(record, "probe", {"input": {"index": 1}})
        assert entered.wait(2)
        store.add_log(record, "probe", {"input": {"index": 2}})
        producer.start()
        assert not submitted.wait(0.05)
        assert execution_log.LOG_QUEUE.qsize() == 1
    finally:
        release.set()
        if producer.ident is not None:
            producer.join(10)
        execution_log.close_logs()
    assert submitted.is_set()
    saved = execution_log.read_log(record["task_id"])
    assert [e["data"]["index"] for e in saved["matching"]["input"]] == [1, 2, 3]


def test_cleanup_protects_queued_task_until_last_write(logged_task, monkeypatch):
    """终态已有后续日志排队时整目录保留；最后一条写完后才允许按容量删除。"""
    record, folder = logged_task()
    entered, release = Event(), Event()
    original = execution_log.write_log
    remaining = []

    def slow_write(*args, **kwargs):
        """首条写入完成清理后检查文件仍在，第二条完成后可删除。"""
        entered.set()
        assert release.wait(10)
        original(*args, **kwargs)
        remaining.append(folder.exists())

    monkeypatch.setattr(execution_log, "write_log", slow_write)
    monkeypatch.setattr(execution_log, "MAX_BYTES", 1)
    try:
        store.add_log(record, "probe", {})
        assert entered.wait(2)
        store.add_log(record, "probe", {})
    finally:
        release.set()
        execution_log.LOG_QUEUE.join()
    assert remaining == [True, False]


def test_background_write_failure_does_not_stop_consumer(logged_task, monkeypatch, caplog):
    """单条故障记录到运行日志，后台继续处理后续事件。"""
    record, _ = logged_task(status="processing")
    original = execution_log.write_log

    def fail_once(record, event, *args, **kwargs):
        """只让指定事件失败，其余执行真实文件写入。"""
        if event == "broken":
            raise OSError("disk failure")
        return original(record, event, *args, **kwargs)

    monkeypatch.setattr(execution_log, "write_log", fail_once)
    store.add_log(record, "broken", {})
    store.add_log(record, "probe", {"input": {"ok": True}})
    execution_log.LOG_QUEUE.join()
    assert "disk failure" in caplog.text
    assert execution_log.read_log(record["task_id"])["matching"]["input"][-1]["data"] == {"ok": True}
