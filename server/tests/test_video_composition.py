"""合成 API、持久化、编排与恢复测试；真实 SQLite 事务和本地模板，HTTP/ASR/IMS 全部隔离。

执行 uv run --locked pytest tests/test_video_composition*.py -v；不访问真实 MySQL 或付费云服务。
"""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from server.app import app
from server.video_composition import ims, service, store
from server.video_composition.schema import MatchCallback

BASE = "/api/v1/video-compositions"


@pytest.fixture
def upstreams(monkeypatch, composition_settings, composition_case):
    """提供可控异步上游，默认完整成功；所有真实网络入口均由内存替身接管。"""
    release = Event()
    release.set()
    notification_release = Event()
    notification_release.set()
    playback_release = Event()
    playback_release.set()
    monkeypatch.setattr(service, "NOTIFICATION_RETRY_DELAYS", (0.01, 0.01, 0.01))
    state = {"asr_calls": 0, "segment_calls": 0, "posts": [], "gets": [], "submits": [], "renders": [],
             "release": release, "entered": Event(), "failure": None, "submit_errors": 0,
             "render_states": ["Success"], "playbacks": [], "callback": True, "query_status": "done",
             "notifications": [], "notification_code": 204, "notification_error": None, "http_clients": [],
             "notification_entered": Event(), "notification_release": notification_release,
             "playback_entered": Event(), "playback_release": playback_release, "playback_errors": 0}
    raw = {"properties": {"original_duration_in_milliseconds": 8000}, "transcripts": [{"sentences": [{"words": [
        {"text": "甲乙丙丁。", "begin_time": 1000, "end_time": 3000},
        {"text": "戊己庚辛。", "begin_time": 4000, "end_time": 6000},
    ]}]}]}
    state["raw"] = raw

    async def transcribe(url, wait_seconds):
        """模拟原生 await，允许调用方在 ASR 等待期间并发查询本地任务。"""
        state["asr_calls"] += 1
        state["entered"].set()
        assert url == composition_case["request"]["audioUrl"] and wait_seconds > 0
        while not release.is_set():
            await asyncio.sleep(0.005)
        if state["failure"] == "asr":
            raise RuntimeError("private-key-should-never-appear")
        return raw

    def segment(body):
        """检查完整 ASR 原对象直传，不接受仅提取的 sentences 或 HTTP 回环。"""
        state["segment_calls"] += 1
        assert body["asr_result"] is raw
        assert set(body) == {"script", "asr_result"}
        if state["failure"] == "segmentation":
            raise ValueError("private-key-should-never-appear")
        return {"segments": deepcopy(composition_case["segments"]), "warnings": [{"code": "example-warning"}], "trace": {"example": True}}

    async def handle(request):
        """默认在提交响应前回调，覆盖回调早到与后台条件更新的竞争。"""
        if request.url.host == "notify.example.test":
            body = json.loads(request.content)
            saved = store.get(body["taskId"])
            state["notifications"].append({"url": str(request.url), "method": request.method,
                                           "headers": dict(request.headers), "body": body,
                                           "stored_status": saved["status"], "stored_stage": saved["stage"],
                                           "stored_notification": saved["data"].get("notification_status")})
            state["notification_entered"].set()
            while not notification_release.is_set():
                await asyncio.sleep(0.005)
            if state["notification_error"]:
                raise state["notification_error"]("private-notification-error")
            codes = state.get("notification_codes")
            code = codes.pop(0) if codes else state["notification_code"]
            return httpx.Response(code, headers={"Location": "https://notify.example.test/redirected"})
        if request.method == "POST":
            state["posts"].append(json.loads(request.content))
            if state["failure"] == "matching-post":
                raise httpx.ReadTimeout("private-key-should-never-appear")
            if state["callback"]:
                body = state["posts"][-1]
                callback_url = body["callback_url"]
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as receiver:
                    response = await receiver.post(callback_url, json={
                        "taskId": "upstream", "status": "failed" if state["failure"] == "matching" else "success",
                        "result": {"segments": deepcopy(composition_case["matches"])},
                    })
                assert response.status_code == 200
            if state["failure"] == "matching-response":
                raise httpx.ReadTimeout("response lost after callback")
            return httpx.Response(202, json={"taskId": "upstream", "status": "processing"})
        state["gets"].append(str(request.url))
        if state["failure"] == "query":
            return httpx.Response(503, json={})
        return httpx.Response(200, json={"task_id": "upstream", "task_type": "match",
                                        "status": "failed" if state["failure"] == "matching" else state["query_status"],
                                        "result": {"segments": deepcopy(composition_case["matches"])}})

    async def submit(body):
        """记录稳定 token 和 payload，可模拟提交超时后第二次成功。"""
        state["submits"].append(deepcopy(body))
        if len(state["submits"]) <= state["submit_errors"]:
            raise httpx.ReadTimeout("unknown acceptance")
        return {"JobId": "ims-job"}

    async def get(job_id):
        """返回官方 IMS 层级与实际 Duration，不把预定输出地址当作已经成功。"""
        state["renders"].append(job_id)
        statuses = state["render_states"]
        status = statuses.pop(0) if len(statuses) > 1 else statuses[0]
        return {"MediaProducingJob": {"JobId": job_id, "Status": status, "Duration": state.get("render_duration", 8.02),
                                      "MediaId": "ims-media", "VodMediaId": "vod-media"}}

    async def storage_location():
        """模拟账号中已有的 VOD 存储，保持合成请求使用真实协议结构。"""
        if state["failure"] == "storage":
            raise ValueError("no VOD storage")
        return "test-vod.oss-cn-shanghai.aliyuncs.com"

    async def result_url(media_id):
        """可阻塞或暂时失败；每次返回不同签名，验证通知复用与 GET 刷新。"""
        state["playbacks"].append(media_id)
        state["playback_entered"].set()
        while not playback_release.is_set():
            await asyncio.sleep(0.005)
        if state["failure"] == "playback" or len(state["playbacks"]) <= state["playback_errors"]:
            raise ValueError("private-key-should-never-appear")
        return f"https://test-output.oss-cn-shanghai.aliyuncs.com/actual.mp4?Signature={len(state['playbacks'])}"

    def http_client(**kwargs):
        """统一隔离素材与终态通知的 HTTP，并保留客户端以检查取消和超时后的关闭。"""
        client = httpx.AsyncClient(transport=httpx.MockTransport(handle), **kwargs)
        state["http_clients"].append(client)
        return client

    monkeypatch.setattr("server.asr.transcribe", transcribe)
    monkeypatch.setattr(service, "segment", segment)
    monkeypatch.setattr(service, "httpx", SimpleNamespace(AsyncClient=http_client))
    monkeypatch.setattr(ims, "IMS", lambda settings, **kwargs: SimpleNamespace(submit=submit, get=get, storage_location=storage_location, result_url=result_url))
    return state


def finished(client, task_id, headers=None):
    """最多三十秒观察本地终态，留足慢 CI 落库余量，成功后立即返回。"""
    end = monotonic() + 30
    while monotonic() < end:
        response = client.get(f"{BASE}/{task_id}", headers=headers)
        assert response.status_code == 200
        data = response.json()
        if data["status"] in ("succeeded", "failed"):
            return data
        sleep(0.005)
    pytest.fail(f"任务未在测试预算内结束：{data}")


@pytest.mark.parametrize("stage,delay", [("matching", 0.15), ("submitting", 5.1)])
def test_success_survives_slow_stage_persistence(upstreams, client, composition_case, monkeypatch, stage, delay):
    """正常流程容忍慢 CI 的落库回执，不因测试专用短期限失败或重复提交上游。"""
    advance = store.advance
    delayed = []

    def slow_advance(record, target, **data):
        """只延迟一次阶段初始化回执，真实 SQLite 事务、状态版本和服务时钟保持原样。"""
        updated = advance(record, target, **data)
        initial = "match_request" in data if stage == "matching" else "ims_request" in data
        if target == stage and initial:
            delayed.append(target)
            sleep(delay)
        return updated

    monkeypatch.setattr(store, "advance", slow_advance)
    response = client.post(BASE, json=composition_case["request"])
    assert response.status_code == 200
    result = finished(client, response.json()["data"])
    assert delayed == [stage]
    assert result["status"] == "succeeded" and result["error"] is None
    assert len(upstreams["posts"]) == len(upstreams["submits"]) == 1
    assert upstreams["gets"] == []


def test_async_acceptance_queries_and_persisted_success(upstreams, client, composition_case):
    """200 受理后可查询持久化任务；ASR 等待不阻塞 GET，成功仅返回实际云结果和北京时间。"""
    upstreams["release"].clear()
    try:
        response = client.post(BASE, json=composition_case["request"])
        assert response.status_code == 200
        task_id = response.json()["data"]
        assert response.json() == {"code": 200, "message": "操作成功", "data": task_id}
        assert response.headers["Location"] == f"{BASE}/{task_id}"
        assert store.get(task_id)["data"]["request"]["audioUrl"] == composition_case["request"]["audioUrl"]
        assert upstreams["entered"].wait(2)
        for _ in range(3):
            processing = client.get(f"{BASE}/{task_id}").json()
            assert processing["status"] == "processing" and processing["stage"] == "asr"
            assert processing["result"] is None and processing["error"] is None
        assert upstreams["asr_calls"] == 1 and upstreams["posts"] == []
    finally:
        upstreams["release"].set()
    result = finished(client, task_id)
    assert result["status"] == "succeeded" and result["stage"] == "completed"
    assert result["result"]["durationSeconds"] == 8.02 and result["error"] is None
    assert "/actual.mp4?Signature=" in result["result"]["videoUrl"]
    assert set(result) == {"taskId", "status", "stage", "result", "error", "createdAt", "updatedAt"}
    assert datetime.fromisoformat(result["createdAt"]).utcoffset() == timedelta(hours=8)
    snapshot = store.get(task_id)["data"]
    assert snapshot["template"]["tracks"][1]["editor"]["titleIn"] == "in/fade_in"
    assert snapshot["segmentation"]["warnings"] == [{"code": "example-warning"}]
    assert snapshot["ims_request"]["client_token"] == task_id
    assert snapshot["result"] == {"mediaId": "ims-media", "durationSeconds": 8.02,
                                  "videoUrl": "https://test-output.oss-cn-shanghai.aliyuncs.com/actual.mp4?Signature=1"}
    assert len(upstreams["submits"]) == 1
    assert upstreams["gets"] == []
    assert snapshot["match_request"]["callback_url"].startswith(f"http://testserver{BASE}/{task_id}/segment-match-callback?token=")
    before = deepcopy(upstreams["renders"])
    refreshed = client.get(f"{BASE}/{task_id}").json()
    assert upstreams["renders"] == before
    assert refreshed["result"]["videoUrl"] != result["result"]["videoUrl"]
    assert store.get(task_id)["data"] == snapshot
    assert "test-secret" not in json.dumps(snapshot)


def test_playback_refresh_failure_preserves_success(upstreams, client, composition_case):
    """刷新地址短暂失败返回 503，再查询可恢复，不回退成功状态或重提合成。"""
    task_id = client.post(BASE, json=composition_case["request"]).json()["data"]
    assert finished(client, task_id)["status"] == "succeeded"
    snapshot = store.get(task_id)
    upstreams["failure"] = "playback"
    response = client.get(f"{BASE}/{task_id}")
    assert response.status_code == 503 and "private-key" not in response.text
    assert store.get(task_id) == snapshot and len(upstreams["submits"]) == 1
    upstreams["failure"] = None
    assert client.get(f"{BASE}/{task_id}").json()["status"] == "succeeded"


@pytest.mark.parametrize("field,value", [
    ("text", " "), ("styleId", "old-external-id"), ("audioUrl", "http://media.test/a"),
    ("videoUrl", "[视频](https://media.test/a)"), ("videoUrl", "https://user:pass@media.test/a"),
    ("materials", [{"fileUrl": "https://media.test/a"}]),
    ("materials", [{"fileUrl": "https://media.test/a", "type": "audio"}]),
    ("packRules", {"backgroundMusic": {"audioSwitch": True}}),
    ("packRules", {"backgroundMusic": {"volume": -0.1}}),
    ("packRules", {"backgroundMusic": {"volume": 1.1}}),
])
def test_invalid_requests_are_not_accepted(client, composition_case, field, value):
    """Pydantic 拒绝非法必填字段、URL、候选和音乐范围，不留下假任务。"""
    response = client.post(BASE, json={**composition_case["request"], field: value})
    assert response.status_code == 422
    assert response.json() == {"code": 422, "message": "请求参数无效", "data": None}
    assert store.pending([], 100) == []


def test_missing_config_unknown_id_and_invalid_id(client, composition_case):
    """缺少合成配置返回 503 而非 200；查询无需云端配置且区分 404/422。"""
    assert client.post(BASE, json=composition_case["request"]).status_code == 503
    assert store.pending([], 100) == []
    assert client.get(f"{BASE}/{uuid4()}").status_code == 404
    assert client.get(f"{BASE}/bad-uuid").status_code == 422


@pytest.mark.parametrize("stage,expected", [
    ("asr", "asr"), ("segmentation", "segmentation"), ("matching", "matching"),
    ("matching-post", "matching"), ("storage", "assembling"), ("render", "rendering"),
])
def test_stage_failures_are_queryable_and_safe(upstreams, client, composition_case, composition_logs, stage, expected):
    """各阶段失败保留输入、错误与终态时间；公开查询仍为 200，且不泄漏原始错误。"""
    upstreams["failure"] = stage
    if stage == "render":
        upstreams["render_states"] = ["Failed"]
    accepted = client.post(BASE, json=composition_case["request"]).json()["data"]
    result = finished(client, accepted)
    assert result["status"] == "failed" and result["result"] is None
    assert result["error"]["stage"] == expected
    assert "private-key" not in json.dumps(result)
    assert len(upstreams["submits"]) == (1 if stage == "render" else 0)
    rows = composition_logs(accepted)
    assert rows[0]["event"] == "submitted" and rows[0]["details"]["input"]["text"] == composition_case["request"]["text"]
    terminal = next(row for row in rows if row["event"] == "task_finished")
    assert terminal["status"] == "failed" and terminal["task_finished_at"] >= terminal["task_created_at"]
    assert terminal["details"]["error"]["stage"] == expected
    if stage in ("asr", "segmentation"):
        assert any(row["event"] == "step_failed" and row["details"]["step"] == stage for row in rows)
    if stage == "matching-post":
        assert result["error"]["code"] == "matching_submission_unknown"
        assert len(upstreams["posts"]) == 1


def test_template_read_is_queryable_before_asr(upstreams, client, composition_case, monkeypatch):
    """阻塞模板读取时 GET 显示 template；模板快照落库后才显示 asr 并调用转写。"""
    entered, release = Event(), Event()
    read_template = service.get_template
    upstreams["release"].clear()

    def blocked_read(template_id):
        """用有界等待模拟慢模板读取，最终仍读取隔离数据库中的实际模板。"""
        entered.set()
        assert release.wait(5)
        return read_template(template_id)

    monkeypatch.setattr(service, "get_template", blocked_read)
    try:
        accepted = client.post(BASE, json=composition_case["request"])
        assert accepted.status_code == 200
        task_id = accepted.json()["data"]
        assert entered.wait(2)
        response = client.get(f"{BASE}/{task_id}")
        assert response.status_code == 200
        assert response.json()["status"] == "processing" and response.json()["stage"] == "template"
        assert response.json()["result"] is None and response.json()["error"] is None
        assert "template" not in store.get(task_id)["data"]
        assert upstreams["asr_calls"] == 0 and upstreams["posts"] == []
        release.set()
        assert upstreams["entered"].wait(2)
        assert client.get(f"{BASE}/{task_id}").json()["stage"] == "asr"
        assert store.get(task_id)["data"]["template"] == composition_case["template"]
    finally:
        release.set()
        upstreams["release"].set()
    assert finished(client, task_id)["status"] == "succeeded"


@pytest.mark.parametrize("error", [HTTPException(503, "private-error"), RuntimeError("private-error")])
def test_template_read_failure_does_not_start_asr(upstreams, client, composition_case, monkeypatch, error):
    """模板读取异常归属 template，错误安全落库，且不调用 ASR、切片或素材匹配。"""
    def failed_read(template_id):
        """模拟模板服务异常，不访问真实数据库或外部服务。"""
        raise error

    monkeypatch.setattr(service, "get_template", failed_read)
    accepted = client.post(BASE, json=composition_case["request"])
    assert accepted.status_code == 200
    result = finished(client, accepted.json()["data"])
    assert result["status"] == "failed" and result["stage"] == "failed"
    assert result["error"]["stage"] == "template" and result["error"]["code"] == "template_error"
    assert "private-error" not in json.dumps(result)
    assert upstreams["asr_calls"] == upstreams["segment_calls"] == 0
    assert upstreams["posts"] == upstreams["submits"] == []


def test_template_snapshot_must_persist_before_asr(upstreams, client, composition_case, monkeypatch):
    """进入 ASR 的事务失败时停留在模板阶段归因，不在快照未落库时发起转写。"""
    advance = store.advance

    def fail_asr_transition(record, stage, **data):
        """仅拒绝模板快照与 asr 阶段的原子写入，允许保存失败终态。"""
        if stage == "asr":
            raise OperationalError("private SQL", {}, Exception("private connection"))
        return advance(record, stage, **data)

    monkeypatch.setattr(store, "advance", fail_asr_transition)
    task_id = client.post(BASE, json=composition_case["request"]).json()["data"]
    result = finished(client, task_id)
    assert result["status"] == "failed"
    assert result["error"]["stage"] == "template" and result["error"]["code"] == "storage_error"
    assert upstreams["asr_calls"] == 0 and upstreams["posts"] == upstreams["submits"] == []
    assert "template" not in store.get(task_id)["data"]


def test_missing_template_fails_before_asr(upstreams, client, composition_case):
    """合法但不存在的模板 UUID 在后台 template 阶段失败，不额外消耗 ASR。"""
    accepted = client.post(BASE, json={**composition_case["request"], "styleId": str(uuid4())}).json()["data"]
    result = finished(client, accepted)
    assert result["error"]["stage"] == "template" and result["error"]["code"] == "template_not_found"
    assert upstreams["asr_calls"] == 0


def test_database_tracks_template_reaches_ims(upstreams, client, composition_case):
    """复现用户数据库配置：无顶层 editor，camelCase 样式与秒制轨道经真实读取进入 IMS。"""
    from server.template import store as template_store
    from server.template.schema import EffectTemplateEditor, effect_catalog

    ids = ["flower/CS0003-000002", "in/wave_in", "out/wave_out"]
    config = {
        "tracks": [{"id": "5da2edc2-38d4-48cd-acae-ff9814ca9485", "target": "title",
                    "start_mode": "seconds", "start": 1.7494356659142214, "duration": 1.0,
                    "editor": EffectTemplateEditor(subtitle="", bubble_text="", title_flower=ids[0],
                                                   title_in=ids[1], title_out=ids[2]).model_dump(by_alias=True)}],
        "effects": [effect_catalog()[key].model_dump(mode="json") for key in ids],
        "effect_ids": ids, "description": "", "transition_duration_seconds": 1.0,
    }
    with template_store.initialize_schema().begin() as connection:
        connection.execute(template_store.templates.update().where(
            template_store.templates.c.template_id == composition_case["request"]["styleId"],
        ).values(configuration=config))
    task_id = client.post(BASE, json=composition_case["request"]).json()["data"]
    assert finished(client, task_id)["status"] == "succeeded"
    timeline = store.get(task_id)["data"]["timeline"]
    assert json.loads(upstreams["submits"][0]["timeline"]) == timeline
    track, = timeline["SubtitleTracks"]
    title, = track["SubtitleTrackClips"]
    assert title["Content"] == composition_case["request"]["title"]
    assert (title["TimelineIn"], title["TimelineOut"]) == (52 / 30, 82 / 30)
    assert (title["FontSize"], title["X"], title["Y"]) == (40, 0.5, 0.08)
    assert title["EffectColorStyle"] == "CS0003-000002"
    assert (title["AaiMotionInEffect"], title["AaiMotionOutEffect"]) == ("wave_in", "wave_out")
    assert (title["AaiMotionIn"], title["AaiMotionOut"]) == (0.5, 0.5)
    assert "让每一帧" not in json.dumps(timeline, ensure_ascii=False)


@pytest.mark.parametrize("duration", [None, 0, -1, True, 8.5])
def test_missing_or_invalid_audio_duration_fails(upstreams, client, composition_case, duration):
    """不以末词/末段时间推测音频总长，缺失和非法毫秒总长停止切片。"""
    upstreams["raw"]["properties"]["original_duration_in_milliseconds"] = duration
    accepted = client.post(BASE, json=composition_case["request"]).json()["data"]
    result = finished(client, accepted)
    assert result["error"]["code"] == "audio_duration_missing" and upstreams["segment_calls"] == 0


@pytest.mark.parametrize("failures", [1, 10])
def test_ims_unknown_submission_reuses_token_with_finite_attempts(upstreams, client, composition_case, failures):
    """提交网络结果不明时只以同请求和同 token 重试一次，仍不明则明确失败。"""
    upstreams["submit_errors"] = failures
    accepted = client.post(BASE, json=composition_case["request"]).json()["data"]
    result = finished(client, accepted)
    assert len(upstreams["submits"]) == 2 and upstreams["submits"][0] == upstreams["submits"][1]
    assert result["status"] == ("succeeded" if failures == 1 else "failed")
    if failures > 1:
        assert result["error"]["code"] == "ims_submission_unknown" and upstreams["renders"] == []


def test_ims_pending_states_then_success(upstreams, client, composition_case):
    """Init/Queuing/Processing 继续查询，同一任务只提交一次。"""
    upstreams["render_states"] = ["Init", "Queuing", "Processing", "Success"]
    accepted = client.post(BASE, json=composition_case["request"]).json()["data"]
    assert finished(client, accepted)["status"] == "succeeded"
    assert len(upstreams["submits"]) == 1 and upstreams["renders"] == ["ims-job"] * 4


def test_store_failure_before_acceptance_returns_503(upstreams, client, composition_case, monkeypatch):
    """受理事务失败不返回 200，异常内容不回显连接或密钥。"""
    def fail(*args):
        """模拟数据库写入失败，不影响只读查询与其他 API。"""
        raise OperationalError("secret SQL", {}, Exception("secret connection"))

    monkeypatch.setattr(store, "create", fail)
    response = client.post(BASE, json=composition_case["request"])
    assert response.status_code == 503 and "secret" not in response.text
    assert upstreams["asr_calls"] == 0


@pytest.mark.anyio
async def test_template_stage_recovery_continues_to_completion(upstreams, composition_case, composition_settings, composition_runtime):
    """恢复 template 记录可重新只读模板，再执行一次 ASR、匹配和渲染，不将任务遗留处理中。"""
    store.initialize_schema()
    record = store.create(composition_case["request"], composition_settings.output(), "http://testserver")
    record = store.advance(record, "template")
    upstreams["callback"] = False
    await composition_runtime._execute(store.get(record["task_id"]))
    result = store.get(record["task_id"])
    assert result["status"] == "succeeded" and result["stage"] == "completed"
    assert result["data"]["template"] == composition_case["template"]
    assert upstreams["asr_calls"] == upstreams["segment_calls"] == 1
    assert len(upstreams["posts"]) == len(upstreams["gets"]) == len(upstreams["submits"]) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("stage", ["matching", "assembling", "submitting", "rendering", "asr", "segmentation"])
async def test_recovery_uses_saved_handles_and_payload(upstreams, composition_case, composition_settings, stage, composition_runtime):
    """恢复匹配/IMS 只查已存 ID；无副作用组装可重跑；无法恢复的成本阶段不重放。"""
    store.initialize_schema()
    record = store.create(composition_case["request"], composition_settings.output())
    timeline = {"VideoTracks": []}
    saved = dict(
        template=composition_case["template"], duration_ms=8000,
        segmentation={"segments": composition_case["segments"]}, matches=composition_case["matches"],
        match_id="upstream", match_url=f"{composition_settings.match_base_url}/api/v1/tasks/upstream",
        match_deadline=service.deadline(-1), ims_deadline=service.deadline(10), submit_attempts=1,
        ims_request=ims.submission(record["task_id"], timeline, composition_settings.output(), "test-vod.oss-cn-shanghai.aliyuncs.com"),
        ims_job_id="ims-job",
    )
    record = store.advance(record, stage, **saved)
    await composition_runtime._execute(record)
    result = store.get(record["task_id"])
    if stage in ("asr", "segmentation"):
        assert result["status"] == "failed" and result["data"]["error"]["code"] == "interrupted"
    else:
        assert result["status"] == "succeeded"
    assert upstreams["asr_calls"] == 0 and upstreams["posts"] == []
    assert len(upstreams["submits"]) == (1 if stage in ("matching", "assembling", "submitting") else 0)
    if stage == "submitting":
        assert upstreams["submits"][0] == saved["ims_request"]


@pytest.mark.anyio
async def test_shutdown_waits_for_real_threads_before_database_close(template_db):
    """取消 await 不代表线程结束；退出确实等数据库工作完成且不再继续下一步。"""
    runtime = service.Runtime()
    entered, release, completed = Event(), Event(), Event()

    def database_work():
        """在线程持有独立连接，并用有限屏障模拟尚未结束的事务。"""
        with template_db.connect() as connection:
            connection.execute(select(1))
            entered.set()
            assert release.wait(3)
        completed.set()

    waiting = asyncio.create_task(runtime.sync(database_work))
    try:
        async with asyncio.timeout(2):
            while not entered.is_set():
                await asyncio.sleep(0.005)
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        closing = asyncio.create_task(runtime.close())
        await asyncio.sleep(0.01)
        assert not closing.done() and not completed.is_set()
        release.set()
        await asyncio.wait_for(closing, timeout=2)
        assert completed.is_set() and template_db.pool.checkedout() == 0
    finally:
        release.set()
        await runtime.close()


@pytest.mark.parametrize("callback", [True, False])
def test_remote_segmentation_response_reaches_render(upstreams, composition_case, client, callback):
    """回放 2026-09-14 远端真实 7 段 JSON，验证字段透传、回调/单次补查及后续渲染；外部服务全隔离。"""
    segments = [
        {"segment_id": 1, "group_id": [1, 1], "text": "一朝沐杏雨，一生念师恩。", "keyword": "", "level": 1, "start_time": 0.24, "end_time": 2.84},
        {"segment_id": 2, "group_id": [1, 3], "text": "香佰里火锅祝，", "keyword": "香佰里火锅", "level": 2, "start_time": 2.84, "end_time": 4.28},
        {"segment_id": 3, "group_id": [2, 3], "text": "所有辛勤的老师们，", "keyword": "老师们", "level": 2, "start_time": 4.28, "end_time": 5.92},
        {"segment_id": 4, "group_id": [3, 3], "text": "教师节快乐！三尺讲台育桃李，", "keyword": "三尺讲台", "level": 2, "start_time": 5.92, "end_time": 8.96},
        {"segment_id": 5, "group_id": [1, 1], "text": "辛苦了各位恩师！", "keyword": "恩师", "level": 2, "start_time": 8.96, "end_time": 10.44},
        {"segment_id": 6, "group_id": [1, 2], "text": "欢迎老师们来店里，", "keyword": "老师们", "level": 2, "start_time": 10.44, "end_time": 12.04},
        {"segment_id": 7, "group_id": [2, 2], "text": "热辣火锅暖心暖胃，好好放松一下。", "keyword": "火锅", "level": 2, "start_time": 12.04, "end_time": 15.08},
    ]
    composition_case["segments"] = segments
    composition_case["request"]["text"] = "".join(item["text"] for item in segments)
    composition_case["matches"] = [
        {key: item[key] for key in ("segment_id", "text", "start_time", "end_time")} for item in segments
    ]
    upstreams["raw"]["properties"]["original_duration_in_milliseconds"] = 15220
    upstreams.update(callback=callback, render_duration=15.22)
    accepted = client.post(BASE, json=composition_case["request"])
    assert accepted.status_code == 200
    task_id = accepted.json()["data"]
    result = finished(client, task_id)
    assert result["status"] == "succeeded" and result["result"]["durationSeconds"] == 15.22
    assert len(upstreams["posts"]) == len(upstreams["submits"]) == 1
    assert len(upstreams["gets"]) == (0 if callback else 1)
    assert upstreams["posts"][0]["asr"] == {}
    assert json.loads(upstreams["posts"][0]["llm"])["segments"] == segments
    snapshot = store.get(task_id)["data"]
    assert snapshot["segmentation"]["segments"] == segments
    timeline = json.loads(snapshot["ims_request"]["timeline"])
    subtitles, title, bubbles = [track["SubtitleTrackClips"] for track in timeline["SubtitleTracks"]]
    assert [(s["TimelineIn"], s["TimelineOut"]) for s in subtitles] == [
        (7 / 30, 85 / 30), (85 / 30, 128 / 30), (128 / 30, 178 / 30),
        (178 / 30, 269 / 30), (269 / 30, 313 / 30), (313 / 30, 361 / 30), (361 / 30, 452 / 30),
    ]
    assert [s["Content"] for s in subtitles] == [
        "一朝沐杏雨一生念师恩", "香佰里火锅祝", "所有辛勤的老师们",
        "教师节快乐三尺讲台育桃李", "辛苦了各位恩师", "欢迎老师们来店里",
        "热辣火锅暖心暖胃好好放松一下",
    ]
    assert [s["Content"] for s in bubbles] == ["香佰里火锅", "老师们", "三尺讲台", "恩师", "老师们", "火锅"]
    assert (title[0]["TimelineIn"], title[0]["TimelineOut"]) == (1, 3)
    assert timeline["VideoTracks"][0]["VideoTrackClips"][0]["TimelineOut"] == 15.22


def test_real_segmentation_with_model_stub(upstreams, composition_case, client, monkeypatch):
    """实际执行本地字符对齐、毫秒投射和关键词逻辑，只有模型 SDK 与上游被替换。"""
    from unittest.mock import MagicMock
    from server.segmentation import segment as real_segment, segmentation

    model = MagicMock()
    model.chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"boundaries_after":[]}'))]),
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"keywords":[["甲乙"]]}'))]),
    ]
    factory = MagicMock()
    factory.return_value.__enter__.return_value = model
    monkeypatch.setattr(segmentation, "OpenAI", factory)

    def cut(body):
        """按真实切片结果建立匹配替身响应，不复制切片算法作为预期值。"""
        result = real_segment(body)
        composition_case["matches"] = [
            {"segment_id": item["segment_id"], "text": item["text"], "start_time": item["start_time"],
             "end_time": item["end_time"], "matched_candidate_url": None}
            for item in result["segments"]
        ]
        return result

    monkeypatch.setattr(service, "segment", cut)
    task_id = client.post(BASE, json=composition_case["request"]).json()["data"]
    assert finished(client, task_id)["status"] == "succeeded"
    snapshot = store.get(task_id)["data"]
    assert snapshot["segmentation"]["trace"]["edit_cost"] == 0
    assert "".join(s["text"] for s in snapshot["segmentation"]["segments"]) == composition_case["request"]["text"]
    assert snapshot["segmentation"]["segments"] == [{
        "segment_id": 1, "text": "甲乙丙丁。戊己庚辛。", "start_time": 1, "end_time": 6,
        "keyword": "甲乙", "level": 2, "group_id": [1, 1],
        "subtitle_parts": [
            {"text": "甲乙丙丁", "start_time": 1, "end_time": 4},
            {"text": "戊己庚辛", "start_time": 4, "end_time": 6},
        ],
    }]
    assert json.loads(upstreams["posts"][0]["llm"])["segments"] == [{
        key: value for key, value in snapshot["segmentation"]["segments"][0].items() if key != "subtitle_parts"
    }]
    assert [clip["Content"] for clip in snapshot["timeline"]["SubtitleTracks"][0]["SubtitleTrackClips"]] == [
        "甲乙丙丁", "戊己庚辛",
    ]
    assert model.chat.completions.create.call_count == 2 and factory.return_value.__exit__.call_count == 1


def test_template_changes_do_not_change_running_snapshot(upstreams, client, composition_case):
    """模板快照取得后，即使模板更新再删除，当前任务仍使用原样式。"""
    upstreams["release"].clear()
    try:
        task_id = client.post(BASE, json=composition_case["request"]).json()["data"]
        assert upstreams["entered"].wait(2)
        template_id = composition_case["request"]["styleId"]
        response = client.post("/template", json={
            "template_id": template_id, "name": "修改后的模板", "tracks": [{
                "id": "title", "target": "title", "start_mode": "seconds", "start": 0, "duration": None,
                "editor": {"title": "标题", "subtitle": "", "bubbleText": "", "titleIn": "in/blur_in"},
            }],
            "effect_ids": ["in/blur_in"],
        })
        assert response.status_code == 200
        assert client.delete(f"/template/{template_id}").status_code == 204
    finally:
        upstreams["release"].set()
    assert finished(client, task_id)["status"] == "succeeded"
    assert store.get(task_id)["data"]["template"] == composition_case["template"]
    assert "fade_in" in upstreams["submits"][0]["timeline"] and "blur_in" not in upstreams["submits"][0]["timeline"]


@pytest.mark.parametrize("storage_delay", [0, 0.2])
def test_duplicate_post_creates_distinct_tasks_with_bounded_concurrency(upstreams, client, composition_case, monkeypatch, storage_delay):
    """重复 POST 独立排队并成功；第二项落库较慢也不应被无关的短匹配预算打断。"""
    monkeypatch.setenv("COMPOSITION_CONCURRENCY", "1")
    # 本用例验证并发而非超时；避免共享夹具的 100ms 预算依赖 CI 磁盘/调度速度。
    monkeypatch.setenv("COMPOSITION_MATCH_WAIT_SECONDS", "10")
    advance = store.advance

    def delayed_advance(record, stage, **data):
        """保留真实事务，仅模拟第二项保存匹配截止时间后数据库返回稍慢。"""
        updated = advance(record, stage, **data)
        if stage == "matching" and "match_request" in data and upstreams["asr_calls"] == 2:
            sleep(storage_delay)
        return updated

    monkeypatch.setattr(store, "advance", delayed_advance)
    upstreams["release"].clear()
    try:
        first = client.post(BASE, json=composition_case["request"]).json()["data"]
        assert upstreams["entered"].wait(2)
        second = client.post(BASE, json=composition_case["request"]).json()["data"]
        assert first != second
        assert client.get(f"{BASE}/{second}").json()["status"] == "queued"
        assert upstreams["asr_calls"] == 1
    finally:
        upstreams["release"].set()
    for task_id in (first, second):
        result = finished(client, task_id)
        assert result["status"] == "succeeded", result
    assert upstreams["asr_calls"] == len(upstreams["posts"]) == len(upstreams["submits"]) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("stage,code", [("matching", "matching_submission_unknown"), ("rendering", "rendering_timeout")])
async def test_recovery_unknown_or_expired_never_reposts(upstreams, composition_settings, composition_case, stage, code, composition_runtime):
    """无匹配句柄和过期 IMS 查询都保留明确错误，不重新发起上游提交。"""
    store.initialize_schema()
    record = store.create(composition_case["request"], composition_settings.output())
    record = store.advance(record, stage, match_deadline=service.deadline(-1), ims_job_id="ims-job", ims_deadline=service.deadline(-1))
    await composition_runtime._execute(record)
    assert store.get(record["task_id"])["data"]["error"]["code"] == code
    assert upstreams["posts"] == upstreams["submits"] == upstreams["renders"] == []


@pytest.mark.anyio
async def test_database_transaction_rolls_back_and_no_cloud_submission(upstreams, composition_settings, composition_case, template_db, composition_runtime):
    """提交阶段事务失败时整次写入回滚，后台保存安全错误，禁止调用 IMS。"""
    store.initialize_schema()
    record = store.create(composition_case["request"], composition_settings.output())
    record = store.advance(record, "assembling", template=composition_case["template"], duration_ms=8000,
                           segmentation={"segments": composition_case["segments"]}, matches=composition_case["matches"])

    def reject_submission(connection, cursor, statement, parameters, context, executemany):
        """仅阻止 assembling 到 submitting 的 SQL 更新，不影响失败状态持久化。"""
        if statement.startswith("UPDATE video_compositions") and "submitting" in parameters:
            raise OperationalError(statement, {}, Exception("test-only SQL error"))

    event.listen(template_db, "before_cursor_execute", reject_submission)
    try:
        await composition_runtime._execute(record)
    finally:
        event.remove(template_db, "before_cursor_execute", reject_submission)
    final = store.get(record["task_id"])
    assert final["status"] == "failed" and final["data"]["error"]["code"] == "storage_error"
    assert "ims_request" not in final["data"] and upstreams["submits"] == []


@pytest.mark.anyio
async def test_lifespan_cancels_asr_and_recovers_as_interrupted(upstreams, composition_settings, composition_case, template_db):
    """真实应用关闭取消 ASR 后先收束再关连接，重启不重放已经开始的成本调用。"""
    from server import database

    upstreams["release"].clear()
    async with app.router.lifespan_context(app):
        runtime = app.state.video_composition
        from server.video_composition.schema import CompositionRequest

        record = await runtime.accept(CompositionRequest.model_validate(composition_case["request"]), "http://testserver")
        async with asyncio.timeout(2):
            while not upstreams["entered"].is_set():
                await asyncio.sleep(0.005)
    assert database._engine is None and not runtime.active and not runtime.threads
    assert upstreams["asr_calls"] == 1 and upstreams["posts"] == []
    database._engine = template_db
    async with app.router.lifespan_context(app):
        async with asyncio.timeout(2):
            while store.get(record["task_id"])["status"] != "failed":
                await asyncio.sleep(0.005)
        assert store.get(record["task_id"])["data"]["error"]["code"] == "interrupted"
    assert upstreams["asr_calls"] == 1


def waiting_match(task_id):
    """有界等待真实提交句柄落库，供手动回调测试使用；不访问外部服务。"""
    end = monotonic() + 2
    while monotonic() < end:
        record = store.get(task_id)
        if record["stage"] == "matching" and record["data"].get("match_id"):
            return record
        sleep(0.005)
    pytest.fail("匹配任务未在预算内完成提交")


@pytest.fixture
def pending_match(upstreams, client, composition_case, monkeypatch):
    """创建已提交、等待手动回调的真实任务，复用成功回调正文并隔离每个测试的修改。"""
    upstreams["callback"] = False
    monkeypatch.setenv("COMPOSITION_MATCH_WAIT_SECONDS", "10")
    accepted = client.post(BASE, json=composition_case["request"])
    assert accepted.status_code == 200
    record = waiting_match(accepted.json()["data"])
    body = {"taskId": "upstream", "status": "success", "result": {"segments": deepcopy(composition_case["matches"])}}
    return record, record["data"]["match_request"]["callback_url"], body


def test_callback_after_acceptance_advances_once(upstreams, client, pending_match):
    """提交后等待回调且不 GET；成功继续 IMS，重复成功和迟到失败不覆盖终态。"""
    record, url, body = pending_match
    task_id = record["task_id"]
    assert upstreams["gets"] == upstreams["submits"] == []
    assert client.post(url, json=body).json() == {"status": "ok"}
    assert finished(client, task_id)["status"] == "succeeded"
    snapshot = store.get(task_id)
    assert client.post(url, json=body).status_code == 200
    assert client.post(url, json={"taskId": "upstream", "status": "failed", "error": "private-error"}).status_code == 200
    assert store.get(task_id) == snapshot
    assert len(upstreams["posts"]) == len(upstreams["submits"]) == 1 and upstreams["gets"] == []
    assert "token" not in client.get(f"{BASE}/{task_id}").text


@pytest.mark.parametrize("case,status", [
    ("missing-token", 403), ("wrong-token", 403), ("unicode-token", 403), ("unknown-local", 404),
    ("wrong-upstream", 409), ("missing-result", 422), ("empty-result", 422), ("empty-segments", 422),
    ("bad-type", 422), ("wrong-text", 422), ("wrong-time", 422), ("missing-segment", 422),
    ("unknown-status", 422),
])
def test_invalid_callback_cannot_advance(upstreams, client, pending_match, case, status):
    """鉴权、上游身份和业务契约失败均返回明确状态，任务继续等待有效结果。"""
    record, url, body = pending_match
    task_id = record["task_id"]
    if case == "missing-token":
        url = url.split("?")[0]
    elif case in ("wrong-token", "unicode-token"):
        url = url.split("?")[0] + "?token=" + ("错误" if case == "unicode-token" else "wrong")
    elif case == "unknown-local":
        url = url.replace(task_id, str(uuid4()))
    elif case == "wrong-upstream":
        body["taskId"] = "wrong"
    elif case == "missing-result":
        del body["result"]
    elif case == "empty-result":
        body["result"] = {}
    elif case == "empty-segments":
        body["result"]["segments"] = []
    elif case == "bad-type":
        body["result"]["segments"][0].update(matched_candidate_url="https://media.test/a", matched_candidate_type="audio")
    elif case == "wrong-text":
        body["result"]["segments"][0]["text"] = "其他任务"
    elif case == "wrong-time":
        body["result"]["segments"][0]["end_time"] = 3.1
    elif case == "missing-segment":
        body["result"]["segments"].pop()
    else:
        body["status"] = "done"
    assert client.post(url, json=body).status_code == status
    assert store.get(task_id) == record and upstreams["gets"] == upstreams["submits"] == []


def test_callback_confirms_lost_submission_response(upstreams, client, composition_case):
    """POST 响应丢失但回调已落库时仍完成合成，不重提匹配也不补查。"""
    upstreams["failure"] = "matching-response"
    task_id = client.post(BASE, json=composition_case["request"]).json()["data"]
    assert finished(client, task_id)["status"] == "succeeded"
    assert len(upstreams["posts"]) == len(upstreams["submits"]) == 1 and upstreams["gets"] == []


@pytest.mark.parametrize("query_status,failure,code", [
    ("done", None, None), ("queued", None, "matching_timeout"),
    ("running", None, "matching_timeout"), ("done", "query", "matching_unavailable"),
])
def test_callback_timeout_queries_once(upstreams, client, composition_case, monkeypatch, query_status, failure, code):
    """缺少回调时仅补查一次；成功继续合成，未完成/5xx 明确失败，迟到回调不重启任务。"""
    monkeypatch.setenv("COMPOSITION_MATCH_WAIT_SECONDS", "1")
    upstreams.update(callback=False, query_status=query_status, failure=failure)
    task_id = client.post(BASE, json=composition_case["request"]).json()["data"]
    result = finished(client, task_id)
    assert result["status"] == ("failed" if code else "succeeded")
    assert result["error"]["code"] == code if code else result["error"] is None
    assert len(upstreams["posts"]) == len(upstreams["gets"]) == 1
    assert len(upstreams["submits"]) == (0 if code else 1)
    snapshot = store.get(task_id)
    assert snapshot["data"]["match_query_started"] is True
    callback = {"taskId": "upstream", "status": "success", "result": {"segments": composition_case["matches"]}}
    assert client.post(snapshot["data"]["match_request"]["callback_url"], json=callback).status_code == 200
    assert store.get(task_id) == snapshot


@pytest.mark.anyio
@pytest.mark.parametrize("public_base", ["", "https://public.example.test/imv/"])
async def test_request_base_url_preserves_deployment_prefix(upstreams, composition_case, monkeypatch, public_base):
    """配置或请求中的部署前缀完整保留，生成的回调 URL 可接收结果并继续合成。"""
    upstreams["callback"] = False
    monkeypatch.setenv("COMPOSITION_PUBLIC_BASE_URL", public_base)
    monkeypatch.setenv("COMPOSITION_MATCH_WAIT_SECONDS", "10")
    expected = public_base.rstrip("/") or "https://composition.test/imv"
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, root_path="/imv"), base_url="https://composition.test/imv") as client:
            invalid = await client.post(BASE, json={})
            assert invalid.status_code == 422 and invalid.json() == {"code": 422, "message": "请求参数无效", "data": None}
            response = await client.post(BASE, json=composition_case["request"])
            assert response.status_code == 200
            task_id = response.json()["data"]
            async with asyncio.timeout(2):
                while not store.get(task_id)["data"].get("match_id"):
                    await asyncio.sleep(0.005)
            record = store.get(task_id)
            assert record["data"]["callback_base_url"] == expected
            url = record["data"]["match_request"]["callback_url"]
            assert url.startswith(f"{expected}{BASE}/{task_id}/segment-match-callback?token=")
            callback = {"taskId": "upstream", "status": "success", "result": {"segments": composition_case["matches"]}}
            assert (await client.post(url, json=callback)).status_code == 200
            async with asyncio.timeout(2):
                while store.get(task_id)["status"] != "succeeded":
                    await asyncio.sleep(0.005)
            assert len(upstreams["submits"]) == 1 and upstreams["gets"] == []


def test_callback_database_failure_is_retryable(client, pending_match, monkeypatch):
    """回调事务失败返回 503，重试成功后继续合成，不将未保存的结果确认成功。"""
    record, url, body = pending_match
    task_id = record["task_id"]
    original = store.advance

    def unavailable(*args, **kwargs):
        """模拟回调持久化不可用，不执行数据库变更。"""
        raise OperationalError("test SQL", {}, Exception("private-database-error"))

    monkeypatch.setattr(store, "advance", unavailable)
    response = client.post(url, json=body)
    assert response.status_code == 503 and "private-database-error" not in response.text
    assert store.get(task_id) == record
    monkeypatch.setattr(store, "advance", original)
    assert client.post(url, json=body).status_code == 200
    assert finished(client, task_id)["status"] == "succeeded"


@pytest.mark.anyio
@pytest.mark.parametrize("query_started", [False, True])
async def test_restart_keeps_deadline_and_queries_at_most_once(upstreams, composition_settings, composition_case, query_started):
    """取消等待再恢复沿用原截止时间；已开始补查的任务不重复 GET，未开始则只补查一次。"""
    store.initialize_schema()
    record = store.create(composition_case["request"], composition_settings.output())
    record = store.advance(record, "matching", template=composition_case["template"], duration_ms=8000,
                           segmentation={"segments": composition_case["segments"]}, match_id="upstream",
                           match_url=f"{composition_settings.match_base_url}/api/v1/tasks/upstream",
                           match_deadline=service.deadline(0.15), match_query_started=query_started)
    runtime = service.Runtime()
    runtime.settings = composition_settings
    pending = asyncio.create_task(runtime._execute(record))
    await asyncio.sleep(0.03)
    assert not pending.done() and upstreams["gets"] == []
    pending.cancel()
    await asyncio.gather(pending, return_exceptions=True)
    await runtime.close()
    assert store.get(record["task_id"]) == record
    resumed = service.Runtime()
    resumed.settings = composition_settings
    try:
        async with asyncio.timeout(2):
            await resumed._execute(store.get(record["task_id"]))
    finally:
        await resumed.close()
    final = store.get(record["task_id"])
    assert final["data"]["match_deadline"] == record["data"]["match_deadline"]
    if query_started:
        assert final["data"]["error"]["code"] == "matching_query_unknown" and upstreams["gets"] == []
    else:
        assert final["status"] == "succeeded" and len(upstreams["gets"]) == 1
    assert upstreams["posts"] == []


@pytest.mark.anyio
@pytest.mark.parametrize("callback_status", ["success", "failed"])
async def test_callback_wins_race_with_query(upstreams, composition_settings, composition_case, monkeypatch, callback_status):
    """补查期间回调先落库，迟到查询不能覆盖结果；重启从保存的阶段继续。"""
    store.initialize_schema()
    record = store.create(composition_case["request"], composition_settings.output())
    record = store.advance(record, "matching", template=composition_case["template"], duration_ms=8000,
                           segmentation={"segments": composition_case["segments"]}, match_id="upstream",
                           match_url=f"{composition_settings.match_base_url}/api/v1/tasks/upstream",
                           match_deadline=service.deadline(-1), match_callback_token="per-task-token")
    runtime = service.Runtime()
    runtime.settings = composition_settings

    async def query(matching, task_id, url):
        """模拟补查在途时收到了有效回调，随后查询才返回相反状态。"""
        assert store.get(record["task_id"])["data"]["match_query_started"] is True
        callback = MatchCallback(taskId="upstream", status=callback_status, result={"segments": composition_case["matches"]})
        await runtime.receive_match_callback(record["task_id"], "per-task-token", callback)
        if callback_status == "success":
            raise service.CompositionError("matching_failed", "迟到补查失败", "matching")
        return {"segments": composition_case["matches"]}

    monkeypatch.setattr(service.Matching, "query_once", query)
    try:
        await runtime._execute(record)
    finally:
        await runtime.close()
    saved = store.get(record["task_id"])
    assert saved["stage"] == ("assembling" if callback_status == "success" else "failed")
    resumed = service.Runtime()
    resumed.settings = composition_settings
    try:
        await resumed._execute(saved)
    finally:
        await resumed.close()
    final = store.get(record["task_id"])
    assert final["status"] == ("succeeded" if callback_status == "success" else "failed")
    assert len(upstreams["submits"]) == (1 if callback_status == "success" else 0)


@pytest.mark.anyio
async def test_concurrent_callbacks_advance_one_version(composition_settings, composition_case, composition_runtime):
    """两份并发重复回调只推进一个版本，结果落库后再回调不会重复推进。"""
    store.initialize_schema()
    record = store.create(composition_case["request"], composition_settings.output())
    record = store.advance(record, "matching", segmentation={"segments": composition_case["segments"]},
                           match_id="upstream", match_callback_token="per-task-token")
    callback = MatchCallback(taskId="upstream", status="success", result={"segments": composition_case["matches"]})
    await asyncio.gather(*(composition_runtime.receive_match_callback(record["task_id"], "per-task-token", callback) for _ in range(2)))
    saved = store.get(record["task_id"])
    assert saved["stage"] == "assembling" and saved["version"] == record["version"] + 1
    assert len(saved["data"]["matches"]) == len(composition_case["matches"])


@pytest.mark.parametrize("conflicts,expected", [(2, 200), (3, 503)])
def test_callback_conditional_conflicts_are_bounded(upstreams, client, pending_match, monkeypatch, conflicts, expected):
    """短暂版本竞争重新读取后可保存，持续竞争返回可重试 503，绝不虚报落库。"""
    record, url, body = pending_match
    task_id = record["task_id"]
    original, calls = store.advance, []

    def conflict(*args, **kwargs):
        """在限定次数内模拟版本冲突，之后使用真实事务。"""
        calls.append(1)
        return None if len(calls) <= conflicts else original(*args, **kwargs)

    monkeypatch.setattr(store, "advance", conflict)
    assert client.post(url, json=body).status_code == expected
    monkeypatch.setattr(store, "advance", original)
    if expected == 503:
        assert len(calls) == 3 and store.get(task_id) == record
        assert client.post(url, json=body).status_code == 200
    assert finished(client, task_id)["status"] == "succeeded"
    assert len(upstreams["submits"]) == 1


@pytest.mark.anyio
async def test_legacy_queued_task_without_base_fails_before_cost(upstreams, composition_settings, composition_case, composition_runtime):
    """旧 queued 快照无受理地址时明确失败，避免成本调用后才发现无法生成回调。"""
    store.initialize_schema()
    record = store.create(composition_case["request"], composition_settings.output())
    await composition_runtime._execute(record)
    assert store.get(record["task_id"])["data"]["error"]["code"] == "callback_address_missing"
    assert upstreams["asr_calls"] == 0 and upstreams["posts"] == upstreams["submits"] == []


@pytest.mark.parametrize('public_base', ['', 'https://public.example.test/'])
def test_callback_uses_public_base_frozen_at_acceptance(upstreams, client, composition_case, monkeypatch, public_base):
    """从 localhost 提交时优先使用公网配置；后续地址变更不能改变已受理任务的回调。"""
    monkeypatch.setenv('COMPOSITION_PUBLIC_BASE_URL', public_base)
    upstreams['release'].clear()
    try:
        response = client.post(BASE, json=composition_case['request'])
        assert response.status_code == 200
        task_id = response.json()['data']
        expected = public_base.rstrip('/') or 'http://testserver'
        assert store.get(task_id)['data']['callback_base_url'] == expected
        assert upstreams['entered'].wait(2)
        monkeypatch.setenv('COMPOSITION_PUBLIC_BASE_URL', 'https://later.example.test')
    finally:
        upstreams['release'].set()
    assert finished(client, task_id)['status'] == 'succeeded'
    assert upstreams['posts'][0]['callback_url'].startswith(f'{expected}{BASE}/{task_id}/segment-match-callback?token=')
    assert upstreams['gets'] == [] and len(upstreams['submits']) == 1


def test_invalid_public_base_prevents_acceptance(upstreams, client, composition_case, monkeypatch):
    """公网地址非法时返回 503，不保存任务也不启动 ASR 或匹配。"""
    monkeypatch.setenv('COMPOSITION_PUBLIC_BASE_URL', 'https://user:secret@public.example.test')
    response = client.post(BASE, json=composition_case['request'])
    assert response.status_code == 503 and 'secret' not in response.text
    assert store.pending([], 1) == [] and upstreams['asr_calls'] == 0 and upstreams['posts'] == []


def notified(task_id):
    """只观察隔离数据库中的通知结果，不用 GET 代替回调；最多三十秒，成功后立即返回。"""
    end = monotonic() + 30
    while monotonic() < end:
        record = store.get(task_id)
        if record["data"].get("notification_status") in ("sent", "failed"):
            return record
        sleep(0.005)
    pytest.fail("终态通知未在测试预算内结束")


@pytest.fixture
def notification_task(composition_case, composition_settings):
    """创建真实落库且尚未通知的成功终态，用于恢复、并发和持久化故障测试。"""
    store.initialize_schema()
    record = store.create({**composition_case["request"], "callbackUrl": "https://notify.example.test/result"},
                          composition_settings.output(), "https://composition.example.test")
    return store.advance(record, "completed", status="succeeded", result={"mediaId": "ims-media", "durationSeconds": 8.02})


def test_success_notification_uses_flat_callback_contract(upstreams, client, composition_case):
    """成功终态落库后自动通知，带可用地址；GET 与迟到上游回调均不重复通知或渲染。"""
    url = "https://notify.example.test/result?token=a%2Fb&source=composition"
    accepted = client.post(BASE, json={**composition_case["request"], "callbackUrl": url})
    assert accepted.status_code == 200
    task_id = accepted.json()["data"]
    record = notified(task_id)
    assert record["status"] == "succeeded" and record["data"]["notification_status"] == "sent"
    assert len(upstreams["notifications"]) == 1
    notification = upstreams["notifications"][0]
    assert notification["url"] == url and notification["method"] == "POST"
    assert notification["stored_status"] == "succeeded" and notification["stored_stage"] == "completed"
    assert notification["stored_notification"] == "sending"
    assert "authorization" not in notification["headers"]
    assert notification["headers"]["content-type"] == "application/json"
    body = notification["body"]
    queried = client.get(accepted.headers["Location"]).json()
    assert body == {"taskId": task_id, "status": "succeed",
                    "videoUrl": record["data"]["result"]["videoUrl"], "errorMessage": None}
    assert queried["status"] == "succeeded" and queried["taskId"] == task_id
    assert queried["result"]["durationSeconds"] == 8.02
    assert body["videoUrl"] != queried["result"]["videoUrl"]
    assert body["videoUrl"].endswith("Signature=1")
    assert client.post(record["data"]["match_request"]["callback_url"], json={"taskId": "upstream", "status": "failed"}).status_code == 200
    assert client.get(accepted.headers["Location"]).status_code == 200
    assert store.get(task_id) == record
    assert len(upstreams["notifications"]) == len(upstreams["submits"]) == 1


@pytest.mark.parametrize("failure", ["asr", "segmentation", "matching", "storage", "render"])
def test_failure_notification_contains_safe_terminal_result(upstreams, client, composition_case, failure):
    """各阶段失败只通知四个约定字段；错误摘要与 GET 一致，不泄漏原始异常。"""
    upstreams["failure"] = failure
    if failure == "render":
        upstreams["render_states"] = ["Failed"]
    accepted = client.post(BASE, json={**composition_case["request"], "callbackUrl": "https://notify.example.test/result"})
    task_id = accepted.json()["data"]
    record = notified(task_id)
    assert record["status"] == "failed" and record["data"]["notification_status"] == "sent"
    assert len(upstreams["notifications"]) == 1
    body = upstreams["notifications"][0]["body"]
    queried = client.get(accepted.headers["Location"]).json()
    assert body == {"taskId": task_id, "status": "failed", "videoUrl": None,
                    "errorMessage": queried["error"]["message"]}
    assert body["errorMessage"]
    assert "private-key" not in json.dumps(body) and upstreams["playbacks"] == []


@pytest.mark.parametrize("callback", [{}, {"callbackUrl": None}])
def test_omitted_notification_retains_query_only_behavior(upstreams, client, composition_case, callback):
    """省略或 null 不发送终态通知，也不安排历史任务通知；原查询接口照常工作。"""
    task_id = client.post(BASE, json={**composition_case["request"], **callback}).json()["data"]
    assert finished(client, task_id)["status"] == "succeeded"
    assert "notification_status" not in store.get(task_id)["data"]
    assert upstreams["notifications"] == [] and store.pending([], 10) == []


@pytest.mark.parametrize("url", [
    "", "notify.example.test", "ftp://notify.example.test/result", "[回调](https://notify.example.test/result)",
    "https://user:secret@notify.example.test/result", "https://notify.example.test/result#fragment",
    " https://notify.example.test/result", "https://notify.example.test/invalid path",
])
def test_invalid_notification_url_rejected_before_acceptance(upstreams, client, composition_case, url):
    """Pydantic 在受理前拒绝无效回调地址，既不保存任务也不调用外部服务。"""
    response = client.post(BASE, json={**composition_case["request"], "callbackUrl": url})
    assert response.status_code == 422 and store.pending([], 10) == []
    assert upstreams["asr_calls"] == 0 and upstreams["notifications"] == []


def test_notification_url_openapi_uses_camel_case(client):
    """公开请求契约只声明可选 callbackUrl，与其他字段保持 camelCase。"""
    schema = client.get("/openapi.json").json()["components"]["schemas"]["CompositionRequest"]
    assert "callbackUrl" in schema["properties"] and "callback_url" not in schema["properties"]
    assert "callbackUrl" not in schema["required"]


@pytest.mark.parametrize("code,error,expected", [
    (200, None, "sent"), (204, None, "sent"), (302, None, "failed"),
    (400, None, "failed"), (500, None, "failed"),
    (204, httpx.ConnectError, "failed"), (204, httpx.ReadTimeout, "failed"),
])
def test_notification_delivery_retries_three_times_without_changing_result(upstreams, client, composition_case, code, error, expected):
    """只把 2xx 视为送达；HTTP 或传输失败额外重试三次，保留终态且不泄漏异常。"""
    upstreams.update(notification_code=code, notification_error=error)
    accepted = client.post(BASE, json={**composition_case["request"], "callbackUrl": "https://notify.example.test/result"})
    record = notified(accepted.json()["data"])
    assert record["data"]["notification_status"] == expected and record["status"] == "succeeded"
    body = client.get(accepted.headers["Location"]).json()
    assert body["status"] == "succeeded" and "private-notification" not in json.dumps(body)
    attempts = 1 if expected == "sent" else 4
    assert len(upstreams["notifications"]) == record["data"]["notification_attempts"] == attempts
    assert len(upstreams["submits"]) == 1
    assert all(item["body"] == upstreams["notifications"][0]["body"] for item in upstreams["notifications"])
    assert store.pending([], 10) == [] and store.get(record["task_id"]) == record


def test_notification_timeout_allows_one_query_without_new_render(upstreams, client, composition_case, monkeypatch):
    """接收端无响应时回调请求有界结束；调用方等待后只 GET 一次即可取结果，无须再合成。"""
    monkeypatch.setenv("COMPOSITION_HTTP_TIMEOUT_SECONDS", "1")
    upstreams["notification_release"].clear()
    accepted = client.post(BASE, json={**composition_case["request"], "callbackUrl": "https://notify.example.test/result"})
    try:
        assert upstreams["notification_entered"].wait(3)
        record = notified(accepted.json()["data"])
        assert record["data"]["notification_status"] == "failed"
        response = client.get(accepted.headers["Location"])
        assert response.status_code == 200 and response.json()["status"] == "succeeded"
        assert len(upstreams["notifications"]) == 4 and len(upstreams["submits"]) == 1
        assert store.pending([], 10) == []
        assert all(client.is_closed for client in upstreams["http_clients"])
    finally:
        upstreams["notification_release"].set()


def test_execution_logs_cover_inputs_outputs_and_notification(upstreams, client, composition_case, composition_logs):
    """真实编排的每个步骤输入输出、原始素材回调原因和最终通知均落库，保留实际媒体链接但隐藏回调凭证。"""
    composition_case["matches"][0]["matched_candidate_reason"] = "no_candidates"
    request = {**composition_case["request"], "callbackUrl": "https://notify.example.test/result?token=hidden-callback"}
    accepted = client.post(BASE, json=request)
    assert accepted.status_code == 200
    task_id = accepted.json()["data"]
    record = notified(task_id)
    response = client.get(f"{BASE}/{task_id}")
    assert response.status_code == 200 and record["data"]["notification_status"] == "sent"
    saved, = composition_logs(task_id, raw=True)
    phases = saved["detail"]["阶段记录"]
    assert saved["detail"]["从提交开始记录"] is True
    assert {"任务提交", "读取模板", "语音识别", "文本切分", "提交素材匹配", "组装视频时间线", "提交云端合成", "查询云端渲染", "获取成品视频链接", "通知调用方", "返回合成结果"} <= phases.keys()
    assert phases["返回合成结果"]["输出"][-1]["内容"]["result"]["videoUrl"] == response.json()["result"]["videoUrl"]
    rows = composition_logs(task_id)
    started = {row["details"]["step"]: row["details"]["input"] for row in rows if row["event"] == "step_started"}
    outputs = {row["details"]["step"]: row["details"]["output"] for row in rows if row["event"] == "step_finished"}
    assert set(started) == set(outputs) == {"template", "asr", "segmentation", "match_submit", "assembling", "ims_storage", "ims_submit", "ims_query", "playback"}
    assert started["template"] == {"style_id": request["styleId"]}
    assert outputs["template"] == composition_case["template"]
    assert outputs["asr"] == upstreams["raw"] == started["segmentation"]["asr_result"]
    assert outputs["segmentation"]["segments"] == composition_case["segments"]
    assert started["assembling"]["segments"] == composition_case["segments"]
    assert outputs["assembling"]["timeline"]["SubtitleTracks"][0]["SubtitleTrackClips"][0]["Content"] == "甲乙丙丁"
    assert started["ims_submit"]["timeline"] and outputs["ims_submit"] == {"JobId": "ims-job"}
    assert outputs["ims_query"]["MediaProducingJob"]["Status"] == "Success"
    callback = next(row for row in rows if row["event"] == "match_callback_received")
    assert callback["details"]["input"]["result"]["segments"][0]["matched_candidate_reason"] == "no_candidates"
    http = [row["details"] for row in rows if row["event"] == "http_response"]
    assert any(item["step"] == "matching" and item["output"]["http_status"] == 202 for item in http)
    assert any(item["step"] == "notification" and item["output"]["http_status"] == 204 for item in http)
    queried = [row for row in rows if row["event"] == "response_ready" and row["details"]["source"] == "query"][-1]
    assert queried["details"]["output"]["result"]["videoUrl"] == response.json()["result"]["videoUrl"]
    assert "Signature=" in response.json()["result"]["videoUrl"]
    text = json.dumps(rows, default=str)
    assert "hidden-callback" not in text
    assert "Signature=" in text and "token=a%2Fb" in text
    assert saved["detail"]["原始输入"]["text"] == request["text"]
    assert set(saved["detail"]["原始输入"]) == set(request)
    assert saved["detail"]["最终输出"] == response.json()
    assert len([row for row in rows if row["event"] == "task_finished"]) == 1
    assert queried["task_finished_at"] == record["updated_at"].replace(tzinfo=None)


def test_playback_must_be_ready_before_success_and_notification(upstreams, client, composition_case, composition_logs):
    """云端 Success 后取址两次失败仍保持 processing；取得地址后才成功并复用地址通知。"""
    upstreams["playback_errors"] = 2
    upstreams["playback_release"].clear()
    task_id = client.post(BASE, json={**composition_case["request"], "callbackUrl": "https://notify.example.test/result"}).json()["data"]
    try:
        assert upstreams["playback_entered"].wait(3)
        record = store.get(task_id)
        assert record["status"] == "processing" and record["stage"] == "rendering"
        assert "notification_status" not in record["data"] and upstreams["notifications"] == []
        response = client.get(f"{BASE}/{task_id}")
        assert response.status_code == 200 and response.json()["result"] is None
    finally:
        upstreams["playback_release"].set()
    record = notified(task_id)
    rows = composition_logs(task_id)
    failures = [row for row in rows if row["event"] == "step_failed" and row["details"]["step"] == "playback"]
    assert len(failures) == 2 and all(row["details"]["exceptions"][0]["type"] == "ValueError" for row in failures)
    saved, = composition_logs(task_id, raw=True)
    assert saved["detail"]["阶段记录"]["获取成品视频链接"]["错误日志"]
    assert record["status"] == "succeeded" and record["data"]["notification_status"] == "sent"
    assert len(upstreams["playbacks"]) == 3 and len(upstreams["renders"]) == 1
    assert upstreams["notifications"][0]["body"]["videoUrl"].endswith("Signature=3")
    events = [row["event"] for row in rows]
    assert events.index("task_finished") < events.index("notification_started")
    upstreams["failure"] = "playback"
    assert client.get(f"{BASE}/{task_id}").status_code == 503
    upstreams["failure"] = None
    assert client.get(f"{BASE}/{task_id}").status_code == 200
    assert store.get(task_id) == record and len(upstreams["notifications"]) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("expired", [False, True])
async def test_playback_resume_and_timeout(upstreams, composition_case, composition_runtime, expired):
    """恢复已渲染任务只获取地址；取址期限耗尽记录明确失败，不发送成功通知或重提渲染。"""
    store.initialize_schema()
    record = store.create({**composition_case["request"], "callbackUrl": "https://notify.example.test/result"},
                          composition_runtime.settings.output(), "https://composition.test")
    record = store.advance(record, "rendering", ims_deadline=service.deadline(0.1 if expired else 10),
                           result={"mediaId": "ims-media", "durationSeconds": 8.02})
    upstreams["failure"] = "playback" if expired else None
    await composition_runtime._execute(record)
    current = store.get(record["task_id"])
    assert current["status"] == ("failed" if expired else "succeeded")
    if expired:
        assert current["data"]["error"]["code"] == "playback_timeout"
    await composition_runtime._execute(current)
    body = upstreams["notifications"][0]["body"]
    assert body["status"] == ("failed" if expired else "succeed")
    assert (body["videoUrl"] is None) == expired
    assert upstreams["renders"] == upstreams["submits"] == []


@pytest.mark.anyio
async def test_pending_retry_recovers_and_stops_on_success(upstreams, notification_task, composition_runtime, monkeypatch):
    """失败重试的时间和次数落库；未到时间不能抢发，新进程沿用次数，成功立即停止。"""
    monkeypatch.setattr(service, "NOTIFICATION_RETRY_DELAYS", (1, 1, 1))
    upstreams["notification_codes"] = [500, 204]
    await composition_runtime._execute(notification_task)
    current = store.get(notification_task["task_id"])
    assert current["data"]["notification_attempts"] == 1
    assert current["data"]["notification_status"] == "pending"
    assert datetime.fromisoformat(current["data"]["notification_next_at"]) > datetime.now(UTC)
    assert store.pending([], 10) == []
    await composition_runtime._execute(current)
    assert len(upstreams["notifications"]) == 1
    async with app.router.lifespan_context(app):
        async with asyncio.timeout(5):
            while store.get(current["task_id"])["data"]["notification_status"] != "sent":
                await asyncio.sleep(0.01)
        saved = store.get(current["task_id"])
        assert store.pending([], 10) == []
    assert len(upstreams["notifications"]) == saved["data"]["notification_attempts"] == 2
    assert saved["updated_at"] == notification_task["updated_at"]


@pytest.mark.anyio
@pytest.mark.parametrize("expired", [False, True])
async def test_notification_reuses_ready_url_until_expiry(upstreams, composition_case, composition_runtime, expired):
    """通知复用可用地址；恢复时地址过期才重新取址，不返回过期链接或再次渲染。"""
    store.initialize_schema()
    record = store.create({**composition_case["request"], "callbackUrl": "https://notify.example.test/result"},
                          composition_runtime.settings.output(), "https://composition.test")
    cached = "https://media.example.test/ready.mp4?Signature=cached"
    record = store.advance(record, "completed", status="succeeded",
                           result={"mediaId": "ims-media", "durationSeconds": 8.02, "videoUrl": cached},
                           video_url_expires_at=service.deadline(-1 if expired else 3600))
    upstreams["failure"] = None if expired else "playback"
    await composition_runtime._execute(record)
    saved = store.get(record["task_id"])
    assert saved["data"]["notification_status"] == "sent"
    assert len(upstreams["playbacks"]) == int(expired)
    url = upstreams["notifications"][0]["body"]["videoUrl"]
    assert (url == cached) == (not expired)
    assert upstreams["renders"] == upstreams["submits"] == []


def test_query_log_failure_preserves_success_response(upstreams, client, composition_case, monkeypatch):
    """任务成功后即使日志表不可写，GET 原结果仍可返回，不重渲染也不改终态。"""
    task_id = client.post(BASE, json=composition_case["request"]).json()["data"]
    assert finished(client, task_id)["status"] == "succeeded"
    record = store.get(task_id)

    def fail(*args, **kwargs):
        """模拟单独日志写入不可用。"""
        raise OperationalError("log unavailable", {}, Exception("database unavailable"))

    monkeypatch.setattr(store, "add_log", fail)
    response = client.get(f"{BASE}/{task_id}")
    assert response.status_code == 200 and response.json()["status"] == "succeeded"
    assert store.get(task_id) == record and len(upstreams["submits"]) == 1


@pytest.mark.anyio
async def test_pending_notification_recovers_after_restart(upstreams, notification_task):
    """终态和待通知标记同事务保存；重启调度恢复通知，不重跑 ASR、匹配或 IMS。"""
    async with app.router.lifespan_context(app):
        async with asyncio.timeout(3):
            while store.get(notification_task["task_id"])["data"]["notification_status"] != "sent":
                await asyncio.sleep(0.005)
        current = store.get(notification_task["task_id"])
    assert len(upstreams["notifications"]) == 1
    assert upstreams["asr_calls"] == 0 and upstreams["posts"] == upstreams["submits"] == upstreams["renders"] == []
    assert current["status"] == notification_task["status"] and current["updated_at"] == notification_task["updated_at"]


@pytest.mark.anyio
async def test_concurrent_notification_claims_send_once(upstreams, notification_task, composition_runtime):
    """重复调度同一终态仅一个版本能认领通知，已发任务与陈旧快照都不能重发。"""
    await asyncio.gather(composition_runtime._execute(notification_task), composition_runtime._execute(notification_task))
    current = store.get(notification_task["task_id"])
    await composition_runtime._execute(current)
    await composition_runtime._execute(notification_task)
    assert len(upstreams["notifications"]) == 1 and current["data"]["notification_status"] == "sent"
    assert store.pending([], 10) == [] and store.get(current["task_id"]) == current


@pytest.mark.anyio
@pytest.mark.parametrize("fail_status,expected,calls", [("sending", "pending", 0), ("sent", "sending", 1)])
async def test_notification_storage_failures_never_duplicate_post(upstreams, notification_task, monkeypatch, fail_status, expected, calls, composition_runtime):
    """认领失败禁止网络副作用；送达后保存失败保留已认领状态，恢复不重复 POST。"""
    original = store.notification_status

    def fail(record, status, details=None):
        """在指定事务步骤失败，其他通知状态更新使用真实数据库。"""
        if status == fail_status:
            raise OperationalError("private SQL", {}, Exception("private database"))
        return original(record, status, details)

    monkeypatch.setattr(store, "notification_status", fail)
    await composition_runtime._execute(notification_task)
    current = store.get(notification_task["task_id"])
    assert current["status"] == "succeeded" and current["data"]["notification_status"] == expected
    assert len(upstreams["notifications"]) == calls
    monkeypatch.setattr(store, "notification_status", original)
    await composition_runtime._execute(current)
    assert len(upstreams["notifications"]) == 1
    assert store.get(notification_task["task_id"])["updated_at"] == notification_task["updated_at"]


@pytest.mark.anyio
async def test_cancelled_notification_is_not_replayed_on_restart(upstreams, notification_task, composition_settings):
    """退出取消在途通知并回收协程；受理不明的 sending 状态在重启后不会重复投递。"""
    upstreams["notification_release"].clear()
    runtime = service.Runtime()
    runtime.settings = composition_settings
    task = asyncio.create_task(runtime._execute(notification_task))
    runtime.active[notification_task["task_id"]] = task
    async with asyncio.timeout(2):
        while not upstreams["notification_entered"].is_set():
            await asyncio.sleep(0.005)
    await runtime.close()
    assert task.cancelled() and not runtime.active and not runtime.threads
    assert all(client.is_closed for client in upstreams["http_clients"])
    current = store.get(notification_task["task_id"])
    assert current["data"]["notification_status"] == "sending" and current["status"] == "succeeded"
    assert store.pending([], 10) == []
    upstreams["notification_release"].set()
    async with app.router.lifespan_context(app):
        await app.state.video_composition._execute(current)
    assert len(upstreams["notifications"]) == 1 and upstreams["submits"] == []


@pytest.mark.anyio
async def test_matching_failure_without_active_job_still_notifies(upstreams, composition_settings, composition_case):
    """上游失败回调到达时没有运行中的 Job，终态仍会被已有调度器拾取并通知调用方。"""
    store.initialize_schema()
    record = store.create({**composition_case["request"], "callbackUrl": "https://notify.example.test/result"},
                          composition_settings.output(), "https://composition.example.test")
    record = store.advance(record, "matching", match_id="upstream", match_callback_token="test-token")
    receiver = service.Runtime()
    try:
        await receiver.receive_match_callback(record["task_id"], "test-token", MatchCallback(taskId="upstream", status="failed"))
    finally:
        await receiver.close()
    async with app.router.lifespan_context(app):
        runtime = app.state.video_composition
        async with asyncio.timeout(3):
            while store.get(record["task_id"])["data"].get("notification_status") != "sent":
                await asyncio.sleep(0.005)
        current = store.get(record["task_id"])
        await runtime.receive_match_callback(record["task_id"], "test-token", MatchCallback(taskId="upstream", status="failed"))
        assert store.get(record["task_id"]) == current
    assert len(upstreams["notifications"]) == 1
    assert upstreams["notifications"][0]["body"]["errorMessage"] == "素材匹配任务失败"
    assert upstreams["submits"] == []


@pytest.mark.parametrize("callback", [False, True])
def test_client_ims_credentials_drive_submit_and_playback(upstreams, client, composition_case, monkeypatch, callback):
    """两客户端提交、查询和回调独立；覆盖有/无服务器凭据，秘密不落库。"""
    from urllib.parse import quote
    seen = []
    original = ims.IMS

    def provider(settings, **kwargs):
        """保留完整模拟 SDK 流程，记录实际构造 SDK 的配置边界。"""
        assert settings.composition_concurrency != 99
        seen.append((settings.ims_access_key_id.get_secret_value(), settings.ims_access_key_secret.get_secret_value()))
        return original(settings, **kwargs)

    monkeypatch.setattr(ims, "IMS", provider)
    if not callback:
        monkeypatch.delenv("ALIBABA_CLOUD_ACCESS_KEY_ID")
        monkeypatch.delenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET")
    upstreams["release"].clear()
    requests = []
    for name in ("client-a", "client-b"):
        header = {"X-IMS-Config": quote(json.dumps({"ims_access_key_id": name, "ims_access_key_secret": name + "-private", "composition_concurrency": 99}))}
        payload = composition_case["request"] | ({"callbackUrl": "https://notify.example.test/result"} if callback else {})
        response = client.post(BASE, json=payload, headers=header)
        assert response.status_code == 200
        requests.append((response.json()["data"], header))
    upstreams["release"].set()
    for task_id, header in requests:
        assert finished(client, task_id, header)["status"] == "succeeded"
        if callback:
            assert notified(task_id)["data"]["notification_status"] == "sent"
        record = store.get(task_id)
        assert "private" not in json.dumps(record, default=str)
        assert client.get(f"{BASE}/{task_id}").status_code == 503
    assert set(seen) == {("client-a", "client-a-private"), ("client-b", "client-b-private")}
    assert len(upstreams["notifications"]) == (2 if callback else 0)
    # 不再检查提交账号指纹；查询直接使用本次提供的凭据，访问权限交给 IMS。
    assert client.get(f"{BASE}/{requests[0][0]}", headers=requests[1][1]).status_code == 200
    assert seen[-1] == ("client-b", "client-b-private")
    from server.database import get_engine
    with get_engine().connect() as connection:
        logs = connection.execute(select(store.execution_logs.c.detail)).scalars().all()
    assert "private" not in json.dumps(logs)
    if not callback:
        assert client.post(BASE, json=composition_case["request"]).status_code == 503


@pytest.mark.parametrize("config", [
    {"ims_access_key_secret": "private"},
    {"ims_access_key_id": "id", "ims_access_key_secret": "private", "ims_endpoint": "evil.test"},
])
def test_client_ims_validation_is_private(upstreams, client, composition_case, config):
    """复用原有凭据必填与官方主机校验，输入不进入日志或响应。"""
    from urllib.parse import quote
    response = client.post(BASE, json=composition_case["request"], headers={"X-IMS-Config": quote(json.dumps(config))})
    assert response.status_code == 422
    assert response.json() == {"detail": "客户端服务配置无效，请检查设置"}
    assert "private" not in response.text
    assert upstreams["asr_calls"] == 0
    assert store.pending([], 10) == []


def test_client_ims_restart_does_not_fall_back_to_server(composition_settings, composition_case):
    """缺任务快照明确标记为不可恢复并提示重新提交，不切换到服务端另一个账号。"""
    store.initialize_schema()
    record = store.create(composition_case["request"], composition_settings.output() | {"client_config": True}, "https://callback.test")
    runtime = service.Runtime()
    asyncio.run(runtime._execute(record))
    result = store.get(record["task_id"])
    assert result["status"] == "failed"
    assert result["data"]["error"]["code"] == "client_config_missing"
    assert "重新提交" in result["data"]["error"]["message"]
    assert not runtime.client_configs and not runtime.active


def test_client_ims_restart_fails_notification_without_retry(upstreams, composition_settings, composition_case):
    """重启后尚未取址且凭据快照已丢失，终态通知按最终失败处理，不做注定失败的重试。"""
    store.initialize_schema()
    request = composition_case["request"] | {"callbackUrl": "https://notify.example.test/result"}
    record = store.create(request, composition_settings.output() | {"client_config": True}, "https://callback.test")
    # 云端已完成但只保存了媒资信息，播放地址必须重新取址，因此该通知离不开客户端凭据。
    record = store.advance(record, "completed", status="succeeded", result={"mediaId": "media-1", "durationSeconds": 8})
    assert record["data"]["notification_status"] == "pending"
    runtime = service.Runtime()
    asyncio.run(runtime._execute(record))
    result = store.get(record["task_id"])
    assert result["data"]["notification_status"] == "failed"
    assert result["data"]["notification_attempts"] == 1
    assert upstreams["notifications"] == []
    assert not runtime.client_configs and not runtime.active
