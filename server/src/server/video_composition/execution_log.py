"""执行日志先快照入队，再由单线程归类、脱敏并保存七个 JSON；超限时整任务清理。"""

import json
import logging
import re
import shutil
import traceback
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from math import isfinite
from pathlib import Path
from queue import Queue
from threading import Lock, Thread
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ValidationError
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


def display_time(value: datetime) -> str:
    """无时区时间视作北京时间；统一显示北京时间并显式标注偏移。"""
    return value.replace(tzinfo=value.tzinfo or BEIJING).astimezone(BEIJING).isoformat(sep=" ", timespec="microseconds")


def event_group(event: str, stage: str, details: dict) -> str:
    """按业务事件确定所属阶段；供模块输入输出与执行记录共用。"""
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


# 七个模块文件即持久化契约；未执行的模块保留四个空数组。
MODULES = ("request", "template", "asr", "segmentation", "matching", "timeline", "zos")
GROUPS = {
    "template": "template", "asr": "asr", "segmentation": "segmentation",
    "matching": "matching", "match_submit": "matching", "match_query": "matching",
    "assembling": "timeline", "material_probe": "timeline", "ims_storage": "timeline", "submitting": "timeline", "ims_submit": "timeline",
    "rendering": "zos", "ims_query": "zos", "playback": "zos", "zos_upload": "zos",
}


def expand_columns(columns: dict) -> dict:
    """展开同任务输入输出中的 $log_ref（JSON Pointer）；返回副本供追加、排序和校验。"""
    def expand(value):
        """引用只读取同任务七模块；普通业务 $ref 不作日志引用处理。"""
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
    """从已展开的ZOS 输出提取两类视频地址；以转存记录判定来源，同类同址只留一次。"""
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
            # 指定字段的父对象也不能整块引用，否则文件中仍看不到它们的原文。
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


def add_event(previous: dict | None, event: str, record: dict, details: dict, time) -> dict:
    """新事件直接生成七模块内容，沿用输入输出、执行与错误的统一结构。"""
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
    failed = event == "notification_pending" or event.endswith(("_failed", "_rejected", "_cancelled")) or (event == "task_finished" and record["status"] == "failed")
    entry = {"time": time, "action": action, "status": record["status"]}
    if failed:
        entry["error"] = {**payload, "message": error_message(details)}
    columns[GROUPS.get(group, "request")]["error_log" if failed else "execute_log"].append(entry)
    put_snapshots(columns, time, action, payload)
    return compact_columns(columns)


# 路径固定相对于 server/ 项目目录，与启动工作目录无关；只支持现有单进程调度。
LOG_ROOT = Path(__file__).resolve().parents[3] / ".log"
MAX_BYTES = 50 * 1024 * 1024
LOG_LOCK = Lock()
# 队列满时提交线程等待空位，避免无限占用内存或丢日志；正常入队不等待磁盘。
LOG_QUEUE = Queue(maxsize=128)
PENDING = Counter()
WRITER_LOCK = Lock()
_writer: Thread | None = None


def enqueue_log(record: dict, event: str, details: dict, finished_tasks) -> None:
    """固定事件时间和独立输入输出快照；工作线程按入队顺序写入，不复制整个业务任务。"""
    global _writer
    time = datetime.now(BEIJING)
    snapshot = {key: record[key] for key in ("task_id", "stage", "status")}
    item = (snapshot, event, deepcopy(details), finished_tasks, time)
    with WRITER_LOCK:
        if _writer is None:
            _writer = Thread(target=_consume_logs, name="composition-logs", daemon=True)
            _writer.start()
        PENDING[record["task_id"]] += 1
    LOG_QUEUE.put(item)


def _consume_logs() -> None:
    """消费至退出标记；单条失败只报告异常，继续处理后续日志并释放队列计数。"""
    while True:
        item = LOG_QUEUE.get()
        try:
            if item is None:
                return
            record, event, details, finished_tasks, time = item
            with WRITER_LOCK:
                PENDING[record["task_id"]] -= 1
                if not PENDING[record["task_id"]]:
                    del PENDING[record["task_id"]]
            write_log(record, event, details, finished_tasks, time=time)
        except Exception:
            logging.getLogger(__name__).exception("合成执行日志写入或清理失败")
        finally:
            LOG_QUEUE.task_done()


def close_logs() -> None:
    """调用方停止提交后，等已入队日志全部写完再退出；必须早于数据库关闭。"""
    global _writer
    if _writer is not None:
        LOG_QUEUE.put(None)
        _writer.join()
        _writer = None


def read_log(task_id: str) -> dict:
    """读取同任务七文件供追加；缺少文件视为空模块，不读取或迁移旧数据库日志。"""
    folder = LOG_ROOT / "video_composition" / task_id
    return {name: json.loads(path.read_text(encoding="utf-8")) if (path := folder / f"{name}.json").exists()
            else {key: [] for key in ("input", "output", "execute_log", "error_log")} for name in MODULES}


def write_log(record: dict, event: str, details: dict, finished_tasks, *, time=None) -> None:
    """锁内追加并以临时文件替换 JSON；写入后检查容量，结束任务由调用方查询确认。"""
    with LOG_LOCK:
        task_id = record["task_id"]
        columns = add_event(read_log(task_id), event, record, details, time or datetime.now(BEIJING))
        folder = LOG_ROOT / "video_composition" / task_id
        folder.mkdir(parents=True, exist_ok=True)
        try:
            for name, section in columns.items():
                temporary = folder / f"{name}.tmp"
                temporary.write_text(json.dumps(section, ensure_ascii=False, indent=2), encoding="utf-8")
            for name in MODULES:
                (folder / f"{name}.tmp").replace(folder / f"{name}.json")
        finally:
            for name in MODULES:
                (folder / f"{name}.tmp").unlink(missing_ok=True)
        cleanup(finished_tasks)


def cleanup(finished_tasks) -> None:
    """写入锁内统计整个 .log；仅超限时按文件最新修改时间删除已结束任务目录。"""
    files = [(path, path.stat()) for path in LOG_ROOT.rglob("*") if path.is_file() and not path.is_symlink()]
    total = sum(stat.st_size for _, stat in files)
    if total <= MAX_BYTES:
        return
    sizes, modified = {}, {}
    for path, stat in files:
        parts = path.relative_to(LOG_ROOT).parts
        if len(parts) >= 3 and parts[0] == "video_composition":
            task_id = parts[1]
            sizes[task_id] = sizes.get(task_id, 0) + stat.st_size
            modified[task_id] = max(modified.get(task_id, 0), stat.st_mtime_ns)
    finished = finished_tasks()
    with WRITER_LOCK:
        for task_id in sorted((set(sizes) & finished) - PENDING.keys(), key=modified.get):
            shutil.rmtree(LOG_ROOT / "video_composition" / task_id)
            total -= sizes[task_id]
            if total <= MAX_BYTES:
                break
