"""日志查询 API 的字段内引用、历史兼容和参数边界；仅使用临时目录，不启动数据库或后台调度。

在 server/ 执行 uv run --locked pytest tests/test_video_composition_log_api.py -v。
"""

import json
import os
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from server import database
from server.app import app
from server.video_composition import execution_log, store


@pytest.fixture
def log_client(monkeypatch):
    """不触发应用 lifespan，使用真实路由和异常处理；任何数据库读取都使测试失败。"""
    def forbid_database(*args, **kwargs):
        """日志查询只依赖文件，不能借任务查询访问数据库。"""
        pytest.fail("日志查询不应访问数据库")

    monkeypatch.setattr(database, "get_engine", forbid_database)
    monkeypatch.setattr(store, "get_engine", forbid_database)
    client = TestClient(app, raise_server_exceptions=False)
    yield client
    client.close()


@pytest.fixture
def task_folder():
    """在共享配置夹具隔离的日志根目录中创建任务，不建立数据库记录。"""
    folder = execution_log.LOG_ROOT / "video_composition" / str(uuid4())
    folder.mkdir(parents=True)
    return folder


@pytest.mark.parametrize("module", execution_log.MODULES)
def test_module_returns_complete_content_without_writing(log_client, task_folder, module):
    """九个模块均返回全部四类记录，保留中文、重试与长正文，查询不改写文件。"""
    section = {
        "input": [{"time": "2026-11-03", "action": "asr.step_started", "data": {"text": "完整正文" * 100000, "tail": "结束"}}],
        "output": [{"time": "2026-11-03", "action": "asr.step_finished", "data": {"value": module}}],
        "execute_log": [{"time": "2026-11-03", "action": "asr.step_started", "status": "processing"}] * 2,
        "error_log": [{"time": "2026-11-03", "action": "asr.step_failed", "status": "failed", "error": {"message": "失败原因"}}],
    }
    path = task_folder / f"{module}.json"
    original = json.dumps(section, ensure_ascii=False)
    path.write_text(original, encoding="utf-8")

    response = log_client.get(f"/api/v1/video-compositions/{task_folder.name}/logs", params={"module": module})

    assert response.status_code == 200
    assert response.json() == section
    assert path.read_text(encoding="utf-8") == original
    assert list(task_folder.iterdir()) == [path]


def test_module_expands_cross_file_references(log_client, task_folder):
    """切片输入引用 ASR 输出时返回实际正文，文件中的引用保持原样。"""
    columns = {name: {key: [] for key in ("input", "output", "execute_log", "error_log")} for name in execution_log.MODULES}
    raw = {"transcript": "完整转写", "segments": [{"text": "第一句", "start": 0}]}
    columns["asr"]["output"] = [{"time": "2026-11-03", "action": "asr.step_finished", "data": raw}]
    reference = {"$log_ref": "#/asr/output/0/data"}
    columns["segmentation"]["input"] = [{"time": "2026-11-03", "action": "segmentation.step_started", "data": reference}]
    for name, section in columns.items():
        (task_folder / f"{name}.json").write_text(json.dumps(section), encoding="utf-8")

    response = log_client.get(f"/api/v1/video-compositions/{task_folder.name}/logs", params={"module": "segmentation"})

    assert response.status_code == 200
    assert response.json()["input"][0]["data"] == raw
    assert json.loads((task_folder / "segmentation.json").read_text())["input"][0]["data"] == reference


def test_api_keeps_local_references_and_materializes_legacy_external_sources(log_client, task_folder):
    """新旧日志均按字段内去重返回，各字段先有原文，查询不迁移历史文件或丢弃事件。"""
    empty = {key: [] for key in ("input", "output", "execute_log", "error_log")}
    raw = {"text": "完整业务正文" * 100}
    entry = {"time": "2026-11-03", "action": "asr.step_finished", "data": raw}
    asr = {**empty, "output": [entry, {**entry, "data": {"$log_ref": "#/asr/output/0/data"}}]}
    segmentation = {**empty, "input": [{**entry, "data": {"$log_ref": "#/asr/output/0/data"}}] * 2,
                    "output": [entry]}
    originals = {}
    for module, section in (("asr", asr), ("segmentation", segmentation)):
        path = task_folder / f"{module}.json"
        originals[path] = json.dumps(section, ensure_ascii=False)
        path.write_text(originals[path], encoding="utf-8")
    for module, field in (("asr", "output"), ("segmentation", "input")):
        response = log_client.get(f"/api/v1/video-compositions/{task_folder.name}/logs", params={"module": module})
        assert response.status_code == 200
        body = response.json()
        assert len(body[field]) == 2
        assert body[field][0]["data"] == raw
        assert body[field][1]["data"] == {"$log_ref": f"#/{module}/{field}/0/data"}
        if module == "segmentation":
            assert body["output"][0]["data"] == raw
    assert all(path.read_text(encoding="utf-8") == text for path, text in originals.items())


def test_old_directory_and_error_payload_are_unchanged(log_client, task_folder):
    """旧七模块可读，新模块为空，旧内嵌异常详情和文件字节均不迁移。"""
    legacy = {key: [] for key in ("input", "output", "execute_log", "error_log")}
    legacy["error_log"] = [{"time": "2026-10-01", "action": "remotion_render.step_failed", "status": "processing",
                            "error": {"exceptions": [{"type": "ValueError", "message": "旧详情"}]}}]
    content = json.dumps(legacy, ensure_ascii=False)
    (task_folder / "timeline.json").write_text(content)
    for module in ("remotion", "ims"):
        assert log_client.get(f"/api/v1/video-compositions/{task_folder.name}/logs", params={"module": module}).json() == {
            key: [] for key in ("input", "output", "execute_log", "error_log")}
    assert log_client.get(f"/api/v1/video-compositions/{task_folder.name}/logs", params={"module": "timeline"}).json() == legacy
    assert (task_folder / "timeline.json").read_text() == content
    assert len(list(task_folder.iterdir())) == 1


def test_error_returns_all_task_error_files(log_client, task_folder):
    """返回普通错误、Remotion 异常和 worker 的完整正文，不读取其他任务或非日志文件。"""
    errors = task_folder / "errors"
    errors.mkdir()
    contents = {
        "first.log": '{"error": "错误一"}', "second.log": '{"error": "重试错误"}',
        "remotion-error.log": "Traceback\n异常详情\n", "remotion-worker.log": "渲染输出\n" * 100000 + "最后一行\n",
    }
    for name, content in contents.items():
        (errors / name).write_text(content, encoding="utf-8")
    (errors / "ignored.tmp").write_text("临时文件", encoding="utf-8")
    other = task_folder.parent / str(uuid4()) / "errors"
    other.mkdir(parents=True)
    (other / "other.log").write_text("其他任务", encoding="utf-8")

    response = log_client.get(f"/api/v1/video-compositions/{task_folder.name}/logs", params={"module": "error"})

    assert response.status_code == 200
    assert response.json() == {"files": [{"name": name, "content": contents[name]} for name in sorted(contents)]}
    assert {path.name: path.read_text(encoding="utf-8") for path in errors.glob("*.log")} == contents


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="目录权限回归需以非 root 用户在 POSIX 上运行")
def test_error_directory_permission_failure_and_recovery(log_client, task_folder):
    """错误目录不可枚举时返回 500；恢复权限后可读取原文件，测试结束前恢复目录权限。"""
    errors = task_folder / "errors"
    errors.mkdir()
    content = "错误详情\n"
    (errors / "existing.log").write_text(content, encoding="utf-8")
    url = f"/api/v1/video-compositions/{task_folder.name}/logs"
    errors.chmod(0o000)
    try:
        response = log_client.get(url, params={"module": "error"})
        assert response.status_code == 500
        assert response.json() == {"detail": "服务端内部错误，请稍后重试"}
    finally:
        errors.chmod(0o700)

    response = log_client.get(url, params={"module": "error"})
    assert response.status_code == 200
    assert response.json() == {"files": [{"name": "existing.log", "content": content}]}


@pytest.mark.parametrize("module", ["asr", "error"])
def test_empty_logs_and_missing_task_directory(log_client, task_folder, module):
    """目录存在但暂无所选日志时返回空内容，目录不存在时返回 404 且不创建目录。"""
    response = log_client.get(f"/api/v1/video-compositions/{task_folder.name}/logs", params={"module": module})
    assert response.status_code == 200
    assert response.json() == ({"files": []} if module == "error" else {key: [] for key in ("input", "output", "execute_log", "error_log")})
    task_folder.rmdir()
    response = log_client.get(f"/api/v1/video-compositions/{task_folder.name}/logs", params={"module": module})
    assert response.status_code == 404
    assert "任务日志不可用" in response.json()["detail"]
    assert not task_folder.exists()


@pytest.mark.parametrize("task_id,params", [
    ("bad-uuid", {"module": "asr"}),
    (str(uuid4()), {"module": "translation"}),
    (str(uuid4()), {"module": "../../dispatch-error"}),
    (str(uuid4()), {}),
])
def test_invalid_parameters_are_rejected_before_file_access(log_client, monkeypatch, task_id, params):
    """非法 UUID、非模块阶段、路径形式和缺失模块均在 HTTP 边界返回 422。"""
    def forbid_query(*args, **kwargs):
        """无效请求不能进入文件读取。"""
        pytest.fail("无效参数不应进入日志读取")

    monkeypatch.setattr(execution_log, "query_log", forbid_query)
    response = log_client.get(f"/api/v1/video-compositions/{task_id}/logs", params=params)
    assert response.status_code == 422
    assert response.json()["detail"]


def test_unreadable_log_is_not_reported_as_empty(log_client, task_folder):
    """已存在的日志损坏时返回服务端错误，不把读取失败伪装成暂无日志。"""
    (task_folder / "asr.json").write_text("{broken", encoding="utf-8")
    response = log_client.get(f"/api/v1/video-compositions/{task_folder.name}/logs", params={"module": "asr"})
    assert response.status_code == 500
    assert response.json() == {"detail": "服务端内部错误，请稍后重试"}


def test_openapi_exposes_ten_required_english_options(log_client):
    """真实 OpenAPI 提供必填英文枚举，供 /docs 渲染模块下拉框。"""
    response = log_client.get("/openapi.json")
    assert response.status_code == 200
    operation = response.json()["paths"]["/api/v1/video-compositions/{task_id}/logs"]["get"]
    parameter = next(item for item in operation["parameters"] if item["name"] == "module")
    assert parameter["required"] is True
    assert parameter["schema"]["enum"] == [*execution_log.MODULES, "error"]
