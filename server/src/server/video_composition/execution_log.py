"""执行日志的脱敏、七列归类、正文引用与旧表备份迁移；数据库写入事务由 store 管理。"""

import json
import re
import traceback
from datetime import UTC, datetime, timedelta, timezone
from math import isfinite
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ValidationError
from sqlalchemy import JSON, Column, MetaData, Table, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError


def sanitize(value, *, media_url: bool = False):
    """递归清理日志数据中的凭证字段和 URL 鉴权；不修改原始业务数据。"""
    sensitive = r"[\w-]*(?:authorization|password|secret|token|signature|api[_-]?key|access[_-]?key)[\w-]*"
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True)
    if isinstance(value, dict):
        return {key: "[REDACTED]" if re.fullmatch(sensitive, key, re.I) else sanitize(child, media_url=key in ("videoUrl", "audioUrl", "fileUrl", "audio_url"))
                for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(child) for child in value]
    if isinstance(value, float) and not isfinite(value):
        return str(value)
    if isinstance(value, str):
        # SDK 的 Timeline 等字段是二次序列化的 JSON；解析后中文可读且内部凭证也能脱敏。
        if value.lstrip().startswith(("{", "[")):
            try:
                decoded = json.loads(value)
            except ValueError:
                pass
            else:
                if isinstance(decoded, (dict, list)):
                    return sanitize(decoded)
        def url(match):
            """业务媒体直链保留实际签名供播放；其他链接删除鉴权查询串，始终删除用户信息。"""
            try:
                parts = urlsplit(match.group())
                return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, parts.query if media_url else "", ""))
            except ValueError:
                return "[INVALID URL]"

        if media_url and value.startswith(("http://", "https://")):
            return url(re.match(r".+", value))
        value = re.sub(r'https?://[^\s\"\'<>]+', url, value)
        value = re.sub(r"(?i)\b(?:Bearer|Basic)\s+[^\s\"',;]+", "[REDACTED]", value)
        # 仅从字段边界匹配；长篇无空格正文不能在每个字符位置重新回溯整段内容。
        return re.sub(rf'''(?i)(?<![\w-])(["']?{sensitive}["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}}]+)''',
                      r"\1[REDACTED]", value)
    return value


def exception_details(exc: Exception) -> dict:
    """保留有限异常链、供应商错误码和调用位置；即使 from None 也可定位被包装的根因。"""
    chain, seen = [], set()
    while exc is not None and id(exc) not in seen and len(chain) < 5:
        seen.add(id(exc))
        item = {"type": type(exc).__name__, "frames": [
            {"file": frame.filename, "line": frame.lineno, "function": frame.name}
            for frame in traceback.extract_tb(exc.__traceback__)[-8:]
        ]}
        if isinstance(exc, ValidationError):
            item["validation"] = exc.errors(include_input=False, include_context=False, include_url=False)
        elif not isinstance(exc, SQLAlchemyError):
            item["message"] = sanitize(str(exc)[:4000])
        for key in ("code", "status_code", "request_id"):
            value = getattr(exc, key, None)
            if isinstance(value, (str, int)):
                item[key] = value
        chain.append(item)
        # SQL 异常链可能含原始语句和参数，不递归记录数据库驱动异常。
        exc = None if isinstance(exc, SQLAlchemyError) else exc.__cause__ or exc.__context__
    return sanitize({"exceptions": chain})


# 合成模块的数据库字段和人读日志统一为北京时间；云端截止时间仍使用带时区的原值。
BEIJING = timezone(timedelta(hours=8))
STAGES = {
    "submission": "任务提交", "queued": "排队等待", "template": "读取模板", "asr": "语音识别",
    "segmentation": "文本切分", "matching": "素材匹配", "match_submit": "提交素材匹配",
    "match_query": "素材匹配超时补查", "assembling": "组装视频时间线", "ims_storage": "选择成片存储",
    "submitting": "准备云端合成", "ims_submit": "提交云端合成", "rendering": "等待云端渲染",
    "ims_query": "查询云端渲染", "playback": "获取成品视频链接", "zos_upload": "转存视频和封面到 ZOS", "notification": "通知调用方",
    "response": "返回合成结果", "completed": "合成完成", "failed": "合成失败",
}
STATUS = {"queued": "排队中", "processing": "执行中", "succeeded": "合成成功", "failed": "合成失败"}


def display_time(value: datetime) -> str:
    """无时区时间来自已转换的数据库字段，带时区时间转换为北京时间并显式标注偏移。"""
    return value.replace(tzinfo=value.tzinfo or BEIJING).astimezone(BEIJING).isoformat(sep=" ", timespec="microseconds")


def event_group(event: str, stage: str, details: dict) -> str:
    """按业务事件确定所属阶段；新日志和旧事件迁移共用归类规则。"""
    group = details.get("step") or stage
    if event == "submitted":
        group = "submission"
    elif event.startswith("notification_"):
        group = "notification"
    elif event.startswith("response_"):
        group = "notification" if details.get("source") == "notification" else "response"
    elif event.startswith("playback_"):
        group = "playback"
    elif event.startswith("match_callback_"):
        group = "matching"
    elif event == "task_finished" and details.get("error"):
        group = details["error"].get("stage", stage)
    return group


def error_message(details: dict) -> str:
    """合并已有异常、业务错误和拒绝原因，不把输入输出放入错误摘要。"""
    reasons = [item.get("message") for item in details.get("exceptions", []) if item.get("message")]
    if details.get("error"):
        reasons.insert(0, details["error"].get("message"))
    if details.get("reason"):
        reasons.append(details["reason"])
    return "；".join(str(reason) for reason in reasons if reason) or "原调用未提供错误正文，请查看详情中的状态码或异常链"


def append_event(detail: dict, event: str, stage: str, status: str, details: dict, time: datetime) -> dict:
    """仅供旧英文事件迁移：序号关联旧 detail 的输入、输出与错误。"""
    details = sanitize(details)
    group = event_group(event, stage, details)
    name = STAGES.get(group, group)
    if not detail:
        detail.update({"格式版本": 2, "时区": "北京时间（UTC+08:00）", "从提交开始记录": event == "submitted", "记录条数": 0, "阶段记录": {}})
    detail["记录条数"] += 1
    sequence = detail["记录条数"]
    timestamp = display_time(time)
    failed = event.endswith(("_failed", "_rejected", "_cancelled")) or (event == "task_finished" and status == "failed")
    section = detail["阶段记录"].setdefault(name, {"输入": [], "输出": [], "执行日志": [], "错误日志": []})
    for key, label in (("input", "输入"), ("output", "输出")):
        if key in details:
            section[label].append({"序号": sequence, "时间": timestamp, "内容": details[key]})
    entry = {"序号": sequence, "时间": timestamp, "事件": event, "任务状态": status,
             "详情": {key: value for key, value in details.items() if key not in ("input", "output")}}
    if failed:
        entry["错误原因"] = error_message(details)
    section["错误日志" if failed else "执行日志"].append(entry)
    if event == "response_ready" and isinstance(details.get("output"), dict):
        detail["最终输出"] = details.get("output")
    return detail


def update_summary(detail: dict, record: dict) -> dict:
    """在日志首页展示真实请求和当前结果；没有返回链接时明确说明，不能用媒资 ID 代替链接。"""
    data = record["data"]
    detail["原始输入"] = sanitize(data.get("raw_request", data["request"]))
    detail["任务状态"] = STATUS.get(record["status"], record["status"])
    if (not detail.get("最终输出") or "状态" in detail["最终输出"]
            or detail["最终输出"].get("status") != record["status"]):
        result = data.get("result") or {}
        detail["最终输出"] = {"状态": detail["任务状态"], "视频链接": None,
                             "时长（秒）": result.get("durationSeconds"), "错误": sanitize(data.get("error")),
                             "说明": "尚无已记录的结果响应；获取到成品链接后更新此处"}
    return compact_detail(detail)


def compact_detail(detail: dict) -> dict:
    """只删除可在阶段输入输出中找到完全相同原文的副本；保留独有值、真实调用和错误。"""
    sources = {
        "template": ("读取模板", "输出", "读取模板的输出"),
        "segmentation": ("文本切分", "输出", "文本切分的输出"),
        "match_request": ("提交素材匹配", "输入", "素材匹配的输入"),
        "matches": ("素材匹配", "输出", "素材匹配的输出"),
        "timeline": ("组装视频时间线", "输出", "视频时间线的输出"),
        "ims_request": ("提交云端合成", "输入", "云端合成的输入"),
    }
    phases = detail.get("阶段记录", {})
    history = detail.get("历史补录", {})
    changes = [entry for phase in phases.values() for entry in phase.get("执行日志", [])
               if entry.get("事件") == "stage_updated"]
    for field, (phase, direction, label) in sources.items():
        copies = []
        for item in phases.get(phase, {}).get(direction, []):
            body = item.get("内容")
            copies.append(body.get(field, body) if isinstance(body, dict) else body)
        # 组装输入包含已校验素材结果；回调原文可能还包含额外字段，不能混为同一个值。
        if field == "matches":
            copies.extend(item["内容"]["matches"] for item in phases.get("组装视频时间线", {}).get("输入", [])
                          if isinstance(item.get("内容"), dict) and "matches" in item["内容"])
        if label in history and any(history[label] == value for value in copies):
            del history[label]
        for entry in changes:
            payload = entry.get("详情", {})
            if field in payload and any(payload[field] == value for value in copies):
                del payload[field]
                entry.setdefault("数据位置", {})[label] = f"{phase} → {direction}"
    detail["冗余副本已整理"] = True
    return detail


def upgrade_detail(previous: dict, record: dict) -> dict:
    """将旧事件格式转换为中文视图；从任务快照补录可证明的输入输出，并标明缺失和来源。"""
    detail = previous if previous.get("格式版本") == 2 else {}
    entries = [entry for section in previous.get("stages", {}).values() for items in section.values() for entry in items]
    for entry in sorted(entries, key=lambda item: item["sequence"]):
        payload = {key: value for key, value in entry.items() if key not in ("sequence", "time", "event", "stage", "status")}
        append_event(detail, entry["event"], entry["stage"], entry["status"], payload, datetime.fromisoformat(entry["time"]))
    if not detail:
        detail = {"格式版本": 2, "时区": "北京时间（UTC+08:00）", "从提交开始记录": False, "记录条数": 0, "阶段记录": {}}
    data = record["data"]
    if not detail["从提交开始记录"] and not detail.get("历史补录已分阶段"):
        recovered = {"说明": "以下为任务表中实际保存的数据，属于历史补录，不代表当时已记录执行日志；未保存的输入输出、时间和异常无法恢复。"}
        # 阶段输入输出在下方直接补录；这里仅保留额外诊断，避免先复制整份快照再删除。
        for key, label in (("timeline_warnings", "时间线警告"), ("error", "任务错误"), ("diagnostics", "错误诊断")):
            if key in data:
                recovered[label] = sanitize(data[key])
        recovered["语音识别原始输出"] = "旧任务表未保存 ASR 原始 JSON，无法从数据库恢复"
        if data.get("notification_status") == "failed":
            recovered["通知错误"] = "任务表记录通知失败；具体原因以已保存的异常日志为准，没有记录则无法恢复"
        detail["历史补录"] = recovered
        for key, phase, kind in (("request", "任务提交", "输入"), ("template", "读取模板", "输出"),
                                 ("segmentation", "文本切分", "输出"), ("match_request", "提交素材匹配", "输入"),
                                 ("matches", "素材匹配", "输出"), ("timeline", "组装视频时间线", "输出"),
                                 ("ims_request", "提交云端合成", "输入")):
            if key in data:
                section = detail["阶段记录"].setdefault(phase, {"输入": [], "输出": [], "执行日志": [], "错误日志": []})
                section[kind].append({"时间": None, "来源": "历史补录：来自任务表已存快照，原执行时间未知", "内容": sanitize(data[key])})
                section["历史说明"] = "已补录实际保存的数据；未保存的执行日志无法恢复"
        for phase, reason in ((STAGES.get(data.get("error", {}).get("stage"), "合成失败"), data.get("error", {}).get("message")),
                              ("通知调用方", recovered.get("通知错误"))):
            if reason:
                section = detail["阶段记录"].setdefault(phase, {"输入": [], "输出": [], "执行日志": [], "错误日志": []})
                section["错误日志"].append({"时间": None, "来源": "历史补录：任务表", "说明": "任务表保存的失败信息", "错误原因": reason})
        detail["历史补录已分阶段"] = True
    return update_summary(detail, record)


def switch_tables(engine, current: str, backup: str, replacement: str) -> None:
    """保留原表并切换已校验的新表；MySQL 原子重命名，SQLite 供隔离测试使用。"""
    with engine.begin() as connection:
        if engine.dialect.name == "mysql":
            connection.execute(text(f"RENAME TABLE {current} TO {backup}, {replacement} TO {current}"))
        else:
            connection.execute(text(f"ALTER TABLE {current} RENAME TO {backup}"))
            connection.execute(text(f"ALTER TABLE {replacement} RENAME TO {current}"))


def migrate_logs(engine, target: Table, tasks: Table) -> None:
    """只处理带 details 列的旧表；先构建新表，成功后切换，失败保留原表与中间表供排查。"""
    inspector = inspect(engine)
    if not inspector.has_table(target.name) or "details" not in {c["name"] for c in inspector.get_columns(target.name)}:
        return
    backup_name = "video_composition_logs_events_backup"
    merged_name = "video_composition_logs_merged"
    if inspector.has_table(backup_name) or inspector.has_table(merged_name):
        raise RuntimeError("日志迁移备份或中间表已存在，请先核查上次迁移；不会覆盖历史数据")
    old = Table(target.name, MetaData(), autoload_with=engine)
    merged = target.to_metadata(MetaData(), name=merged_name)
    merged.create(engine)
    with engine.begin() as connection:
        # 按任务与旧事件编号逐条合并，只保留一个任务的 JSON 在内存中。
        task_ids = connection.execute(select(old.c.task_id).distinct()).scalars().all()
        for task_id in task_ids:
            detail = {"stages": {}}
            rows = connection.execute(select(old).where(old.c.task_id == task_id).order_by(old.c.id)).mappings().all()
            for sequence, row in enumerate(rows, start=1):
                detail["stages"].setdefault(row["stage"], {"logs": []})["logs"].append({
                    "sequence": sequence, "time": row["created_at"].replace(tzinfo=UTC).isoformat(),
                    "event": row["event"], "stage": row["stage"], "status": row["status"], **row["details"],
                })
            first, last = rows[0], rows[-1]
            latest = connection.execute(select(tasks).where(tasks.c.task_id == task_id)).mappings().first()
            finished = next((row["task_finished_at"] for row in rows if row["task_finished_at"] is not None), None)
            if latest and latest["status"] in ("succeeded", "failed"):
                finished = latest["updated_at"]
            connection.execute(merged.insert().values(
                task_id=task_id, stage=latest["stage"] if latest else last["stage"],
                status=latest["status"] if latest else last["status"], detail=detail,
                created_at=first["created_at"], updated_at=last["created_at"],
                task_created_at=first["task_created_at"], task_finished_at=finished,
            ))
    switch_tables(engine, target.name, backup_name, merged_name)


def migrate_beijing_logs(engine, logs: Table, tasks: Table) -> None:
    """备份后事务转换 UTC 字段并补录历史快照；数据标记保证重启不重复加八小时。"""
    with engine.connect() as connection:
        records = connection.execute(select(tasks)).mappings().all()
        saved = {row["task_id"]: dict(row) for row in connection.execute(select(logs)).mappings()}
    pending = [record for record in records if record["data"].get("storage_timezone") != "Asia/Shanghai"
               or saved.get(record["task_id"], {}).get("detail", {}).get("格式版本") != 2
               or not saved.get(record["task_id"], {}).get("detail", {}).get("冗余副本已整理")
               or ("历史补录" in saved.get(record["task_id"], {}).get("detail", {})
                   and not saved[record["task_id"]]["detail"].get("历史补录已分阶段"))]
    if not pending:
        return
    # 备份只保留每行第一次转换前的数据；DDL 在数据事务之前，失败可安全重试。
    task_backup = tasks.to_metadata(MetaData(), name="video_compositions_utc_backup")
    log_backup = logs.to_metadata(MetaData(), name="video_composition_logs_utc_backup")
    task_backup.create(engine, checkfirst=True)
    log_backup.create(engine, checkfirst=True)
    with engine.begin() as connection:
        for original in pending:
            task_id = original["task_id"]
            if connection.execute(select(task_backup.c.task_id).where(task_backup.c.task_id == task_id)).first() is None:
                connection.execute(task_backup.insert().values(**dict(original)))
            previous = saved.get(task_id)
            if previous and connection.execute(select(log_backup.c.task_id).where(log_backup.c.task_id == task_id)).first() is None:
                connection.execute(log_backup.insert().values(**previous))
            record = dict(original)
            if record["data"].get("storage_timezone") != "Asia/Shanghai":
                record.update(created_at=record["created_at"] + timedelta(hours=8),
                              updated_at=record["updated_at"] + timedelta(hours=8),
                              data={**record["data"], "storage_timezone": "Asia/Shanghai"})
                connection.execute(tasks.update().where(tasks.c.task_id == task_id).values(
                    created_at=record["created_at"], updated_at=record["updated_at"], data=record["data"]))
            now = datetime.now(BEIJING).replace(tzinfo=None)
            offset = timedelta(hours=8) if previous and previous["detail"].get("格式版本") != 2 else timedelta()
            values = dict(stage=record["stage"], status=record["status"],
                          detail=upgrade_detail(previous["detail"] if previous else {}, record),
                          created_at=previous["created_at"] + offset if previous else now,
                          updated_at=previous["updated_at"] + offset if previous else now,
                          task_created_at=record["created_at"],
                          task_finished_at=record["updated_at"] if record["status"] in ("succeeded", "failed") else None)
            if previous:
                connection.execute(logs.update().where(logs.c.task_id == task_id).values(**values))
            else:
                connection.execute(logs.insert().values(task_id=task_id, **values))


# 模块列即持久化契约；未执行的模块保留四个空数组。
MODULES = ("request", "template", "asr", "segmentation", "matching", "timeline", "zos")
GROUPS = {
    "template": "template", "asr": "asr", "segmentation": "segmentation",
    "matching": "matching", "match_submit": "matching", "match_query": "matching",
    "assembling": "timeline", "ims_storage": "timeline", "submitting": "timeline", "ims_submit": "timeline",
    "rendering": "zos", "ims_query": "zos", "playback": "zos", "zos_upload": "zos",
}


def expand_columns(columns: dict) -> dict:
    """展开本行输入输出中的 $log_ref（JSON Pointer）；返回副本供追加、排序和校验。"""
    def expand(value):
        """引用只读取当前行；普通业务 $ref 不作日志引用处理。"""
        if isinstance(value, dict):
            if set(value) == {"$log_ref"}:
                target = columns
                for part in value["$log_ref"].removeprefix("#/").split("/"):
                    key = part.replace("~1", "/").replace("~0", "~")
                    target = target[int(key)] if isinstance(target, list) else target[key]
                return expand(target)
            return {key: expand(child) for key, child in value.items()}
        if isinstance(value, list):
            return [expand(child) for child in value]
        return value

    return {name: {**columns[name], **{
        direction: [{**item, "data": expand(item["data"])} for item in columns[name][direction]]
        for direction in ("input", "output")
    }} for name in MODULES}


def video_links(items: list[dict]) -> list[dict]:
    """从已展开的旧/新 ZOS 输出提取两类视频地址；以转存记录判定来源，同类同址只留一次。"""
    stored = any(item["action"].endswith(".zos_object_key") or item["action"] == "zos_upload.step_finished"
                 or isinstance(item["data"], dict) and "zos_video_url" in item["data"] for item in items)
    links, seen = [], set()
    for item in items:
        data, action = item["data"], item["action"]
        if isinstance(data, dict):
            values = {key: data[key] for key in ("aliyun_video_url", "zos_video_url") if data.get(key)}
            if data.get("videoUrl"):
                key = "aliyun_video_url" if action.startswith("playback.") or not stored else "zos_video_url"
                values[key] = data["videoUrl"]
        elif action == "playback.step_finished" and isinstance(data, str):
            values = {"aliyun_video_url": data}
        elif action == "zos_upload.step_finished" and isinstance(data, list) and len(data) == 2:
            values = {"zos_video_url": data[1]}
        else:
            continue
        for key, url in values.items():
            if isinstance(url, str) and url.startswith(("http://", "https://")) and (key, url) not in seen:
                links.append({**item, "data": {key: url}})
                seen.add((key, url))
    return links


def compact_columns(columns: dict) -> dict:
    """整理模块归属和 ZOS 地址；重复正文用引用，时间线指定字段原文展示，执行/错误不变。"""
    columns = expand_columns(columns)
    for section in columns.values():
        for items in section.values():
            items.sort(key=lambda item: item["time"] or "")
    columns["zos"]["output"] = video_links(columns["zos"]["output"])
    # 旧回调按 HTTP 接收方向记为输入；模块视角属于匹配输出，展开后移动再重建引用。
    matching = columns["matching"]
    callbacks = [item for item in matching["input"] if item["action"] == "matching.match_callback_received"]
    if callbacks:
        matching["input"] = [item for item in matching["input"] if item["action"] != "matching.match_callback_received"]
        matching["output"] = sorted(matching["output"] + callbacks, key=lambda item: item["time"] or "")
    seen = {}

    def compact(value, path):
        """先匹配完整对象，再处理子项；索引只指向仍保留的节点，避免悬空引用。"""
        if path.startswith("#/zos/output/") or (path.startswith("#/timeline/") and path.rsplit("/", 1)[-1] in ("materials", "packRules")):
            return value
        if not isinstance(value, (dict, list)):
            return value
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if encoded in seen:
            reference = {"$log_ref": seen[encoded]}
            # 指定字段的父对象也不能整块引用，否则 Navicat 中仍看不到它们的原文。
            inline = path.startswith("#/timeline/") and any(f'"{key}":' in encoded for key in ("materials", "packRules"))
            if not inline and len(json.dumps(reference, ensure_ascii=False, separators=(",", ":"))) < len(encoded):
                return reference
        else:
            pending.setdefault(encoded, path)
        if isinstance(value, list):
            return [compact(child, f"{path}/{index}") for index, child in enumerate(value)]
        return {key: compact(child, f"{path}/{key.replace('~', '~0').replace('/', '~1')}") for key, child in value.items()}

    # 原始请求、上游输出优先保留，最终响应最后处理。
    locations = [("request", "input")] + [
        (name, direction) for name in MODULES[1:] for direction in ("output", "input")
    ] + [("request", "output")]
    for name, direction in locations:
        for index, item in enumerate(columns[name][direction]):
            pending = {}
            item["data"] = compact(item["data"], f"#/{name}/{direction}/{index}/data")
            seen.update(pending)
    return columns


def put_data(columns: dict, group: str, direction: str, time, action: str, data, *, fallback=False) -> None:
    """写入模块输入输出；统一提取最终时间线、组装警告和成片结果，快照只补缺。"""
    if group == "ims_submit" and direction == "input":
        data = sanitize(data)
        if "timeline" in data:
            put_data(columns, "timeline", "output", time, action, data["timeline"], fallback=True)
        data = {key: value for key, value in data.items() if key != "timeline"}
        if not data:
            return
    elif group == "assembling" and direction == "output":
        if not isinstance(data, dict) or not data.get("warnings"):
            return
        data = {"warnings": data["warnings"]}
    module = GROUPS.get(group, group if group in MODULES else "request")
    items = columns[module][direction]
    if not fallback or not any(item["data"] == data for item in items):
        items.append({"time": time, "action": action, "data": data})
    if direction == "output" and action.endswith(".response_ready") and isinstance(data, dict) and data.get("result"):
        put_data(columns, "zos", "output", time, action, data["result"], fallback=True)


def put_snapshots(columns: dict, time, action: str, payload: dict) -> None:
    """只补录事件已保存的模块快照，不替代实际调用的输入输出。"""
    for key, group, direction in (("template", "template", "output"), ("segmentation", "segmentation", "output"),
                                  ("match_request", "matching", "input"), ("matches", "matching", "output"),
                                  ("result", "zos", "output"), ("zos_object_key", "zos", "output")):
        if key in payload:
            put_data(columns, group, direction, time, f"{action}.{key}", payload[key], fallback=True)
    if "ims_request" in payload:
        put_data(columns, "ims_submit", "input", time, action, payload["ims_request"], fallback=True)


def split_detail(detail: dict, snapshot: dict | None = None) -> dict:
    """仅用于旧表迁移：先转换实际输入输出，再补快照；缺失历史时间保持未知。"""
    columns = {name: {key: [] for key in ("input", "output", "execute_log", "error_log")} for name in MODULES}
    stages = {label: group for group, label in STAGES.items()}
    for phase, section in detail.get("阶段记录", {}).items():
        group = stages.get(phase, phase)
        entries = {item.get("序号"): item for key in ("执行日志", "错误日志") for item in section.get(key, [])}
        for source, direction in (("输入", "input"), ("输出", "output")):
            for item in section.get(source, []):
                event = entries.get(item.get("序号"), {}).get("事件", "historical_snapshot")
                put_data(columns, group, direction, item.get("时间"), f"{group}.{event}", item["内容"])
    for phase, section in detail.get("阶段记录", {}).items():
        group = stages.get(phase, phase)
        for kind, destination in (("执行日志", "execute_log"), ("错误日志", "error_log")):
            for item in section.get(kind, []):
                action, time = f"{group}.{item.get('事件', 'historical_snapshot')}", item.get("时间")
                entry = {"time": time, "action": action, "status": item.get("任务状态", "unknown")}
                payload = item.get("详情", {})
                if destination == "error_log":
                    entry["error"] = {**payload, "message": item.get("错误原因", item.get("说明"))}
                columns[GROUPS.get(group, "request")][destination].append(entry)
                put_snapshots(columns, time, action, payload)
    for key, direction, action in (("原始输入", "input", "submission"), ("最终输出", "output", "response"),
                                   ("历史补录", "output", "legacy")):
        if key in detail and (key != "历史补录" or detail[key]):
            put_data(columns, "request", direction, None, f"{action}.historical_snapshot", detail[key], fallback=True)
    if detail.get("最终输出", {}).get("result"):
        put_data(columns, "zos", "output", None, "response.historical_snapshot", detail["最终输出"]["result"], fallback=True)
    for key in ("result", "zos_object_key"):
        if snapshot and key in snapshot:
            put_data(columns, "zos", "output", None, f"zos_upload.historical_snapshot.{key}", sanitize(snapshot[key]), fallback=True)
    return compact_columns(columns)


def add_event(previous: dict | None, event: str, record: dict, details: dict, time) -> dict:
    """新事件直接生成七列内容；不构造 detail，不经过旧格式转换。"""
    columns = expand_columns(previous) if previous else {
        name: {key: [] for key in ("input", "output", "execute_log", "error_log")} for name in MODULES
    }
    if details.get("step") == "playback" and isinstance(details.get("output"), str):
        details = {**details, "output": {"videoUrl": details["output"]}}  # 媒体字段保留实际链接签名。
    details = sanitize(details)
    group, time = event_group(event, record["stage"], details), display_time(time)
    action = f"{group}.{event}"
    for direction in ("input", "output"):
        if direction in details:
            put_data(columns, group, direction, time, action, details[direction])
    payload = {key: value for key, value in details.items() if key not in ("input", "output")}
    failed = event.endswith(("_failed", "_rejected", "_cancelled")) or (event == "task_finished" and record["status"] == "failed")
    entry = {"time": time, "action": action, "status": record["status"]}
    if failed:
        entry["error"] = {**payload, "message": error_message(details)}
    columns[GROUPS.get(group, "request")]["error_log" if failed else "execute_log"].append(entry)
    put_snapshots(columns, time, action, payload)
    return compact_columns(columns)


def migrate_modules(engine, target: Table, tasks: Table) -> None:
    """仅在单进程调度启动前迁移；旧表完整留档，复制或校验失败不切换、不覆盖。"""
    inspector = inspect(engine)
    exists = inspector.has_table(target.name)
    if exists and "request" in {column["name"] for column in inspector.get_columns(target.name)}:
        return
    with engine.connect() as connection:
        has_tasks = connection.execute(select(tasks.c.task_id).limit(1)).first() is not None
    if not exists and not has_tasks:
        target.create(engine)
        return
    backup_name, staging_name = "video_composition_logs_detail_backup", "video_composition_logs_modules"
    if inspector.has_table(backup_name) or inspector.has_table(staging_name):
        raise RuntimeError("日志迁移备份或中间表已存在，请核查上次迁移；不会覆盖历史数据")
    # 复用旧版本的事件合并、UTC 转换和缺失快照补录，保留其原有备份机制。
    legacy = Table(target.name, MetaData(), *(
        Column(c.name, c.type, primary_key=c.primary_key, nullable=c.nullable, unique=c.unique)
        for c in target.columns if c.name not in MODULES
    ), Column("detail", JSON, nullable=False), mysql_charset="utf8mb4")
    migrate_logs(engine, legacy, tasks)
    legacy.create(engine, checkfirst=True)
    migrate_beijing_logs(engine, legacy, tasks)
    staging = target.to_metadata(MetaData(), name=staging_name)
    staging.create(engine)
    with engine.begin() as connection:
        ids = connection.execute(select(legacy.c.id)).scalars().all()
        for row_id in ids:
            row = dict(connection.execute(select(legacy).where(legacy.c.id == row_id)).mappings().one())
            detail = row.pop("detail")
            snapshot = connection.execute(select(tasks.c.data).where(tasks.c.task_id == row["task_id"])).scalar_one_or_none()
            converted = {**row, **split_detail(detail, snapshot=snapshot)}
            connection.execute(staging.insert().values(**converted))
            saved = dict(connection.execute(select(staging).where(staging.c.id == row_id)).mappings().one())
            if saved != converted:
                raise RuntimeError("日志迁移读回校验失败，原表未切换")
    switch_tables(engine, target.name, backup_name, staging_name)
