"""素材匹配 HTTP 契约测试：内存传输隔离网络，覆盖字段映射、同源 Location 和有界失败。"""

import json

import httpx
import pytest
from pydantic import TypeAdapter

from server.video_composition.errors import CompositionError
from server.video_composition.matching import Matching, payload, query_url, validated_matches
from server.video_composition.schema import CompositionRequest, Segment


@pytest.mark.parametrize("materials", [None, [], [{"fileUrl": "https://media.example.test/a?x=a%2Fb", "type": "image"}]])
def test_match_request_mapping_and_optional_candidates(composition_case, materials):
    """所有候选模式均原样透传编号、秒数、关键词、等级和分组，且只序列化一次。"""
    if materials is not None:
        composition_case["request"]["materials"] = materials
    composition_case["segments"][0]["subtitle_parts"] = [
        {"text": "甲乙", "start_time": 1, "end_time": 2},
        {"text": "丙丁", "start_time": 2, "end_time": 3},
    ]
    body = payload("local-id", CompositionRequest.model_validate(composition_case["request"]),
                   TypeAdapter(list[Segment]).validate_python(composition_case["segments"]), "https://composition.test/callback?token=test")
    assert body["taskId"] == "local-id"
    assert body["text"] == composition_case["request"]["text"]
    segments = json.loads(body["llm"])["segments"]
    assert segments == [
        {"segment_id": 1, "text": "甲乙丙丁。", "start_time": 1, "end_time": 3, "keyword": "甲乙", "level": 2, "group_id": [1, 2]},
        {"segment_id": 2, "text": "戊己庚辛。", "start_time": 4, "end_time": 6, "keyword": "", "level": 1, "group_id": [2, 2]},
    ]
    assert body["asr"] == {}
    assert body["callback_url"] == "https://composition.test/callback?token=test"
    if materials:
        assert body["asset_url_list"] == [{"file_url": materials[0]["fileUrl"], "type": "image"}]
    else:
        assert "asset_url_list" not in body


def test_segment_ids_and_fractional_seconds_are_not_rewritten(composition_case):
    """非从 1 开始的整数编号和小数秒直接进入匹配，结果必须保留原编号及时间。"""
    composition_case["segments"][0].update(segment_id=7, start_time=0.241, end_time=2.841)
    composition_case["segments"][1]["segment_id"] = 9
    for source, matched in zip(composition_case["segments"], composition_case["matches"]):
        matched.update({key: source[key] for key in ("segment_id", "start_time", "end_time")})
    body = payload("local", CompositionRequest.model_validate(composition_case["request"]),
                   TypeAdapter(list[Segment]).validate_python(composition_case["segments"]),
                   "https://composition.test/callback")
    assert json.loads(body["llm"])["segments"] == composition_case["segments"]
    result = validated_matches(composition_case["segments"], {"segments": composition_case["matches"]})
    assert [item["segment_id"] for item in result] == [7, 9]
    composition_case["matches"][0]["segment_id"] = 1
    with pytest.raises(ValueError):
        validated_matches(composition_case["segments"], {"segments": composition_case["matches"]})


@pytest.mark.parametrize("field,value", [
    ("segment_id", "1"), ("segment_id", "seg_001"), ("segment_id", 1.5), ("segment_id", True), ("segment_id", 0),
    ("start_time", -0.1), ("start_time", float("nan")), ("start_time", float("inf")),
    ("start_time", True), ("end_time", "3"), ("end_time", 0), ("end_time", float("inf")),
    ("keyword", []), ("keyword", {"text": "甲乙"}), ("keyword", None),
    ("group_id", []), ("group_id", [1]), ("group_id", [1, 2, 3]), ("group_id", [2, 1]),
    ("group_id", [0, 1]), ("group_id", [1, "2"]), ("group_id", [True, 1]),
    ("level", 0), ("level", "2"), ("level", True),
])
def test_segment_contract_rejects_invalid_fields(composition_case, field, value):
    """错误类型、非有限秒数、旧关键词对象和非法分组不得进入匹配请求。"""
    with pytest.raises(ValueError):
        Segment.model_validate({**composition_case["segments"][0], field: value})


def test_old_segment_contract_is_rejected():
    """不静默混用旧毫秒快照，避免单位错配或丢失关键词、分组。"""
    with pytest.raises(ValueError):
        Segment.model_validate({"segment_id": "seg_001", "text": "甲乙", "start_time_ms": 0,
                                "end_time_ms": 2000, "keywords": [{"text": "甲"}]})


@pytest.mark.parametrize("location,expected", [
    (None, "https://host.test/prefix/api/v1/tasks/upstream"),
    ("/prefix/api/v1/tasks/upstream", "https://host.test/prefix/api/v1/tasks/upstream"),
    ("../tasks/upstream", "https://host.test/prefix/api/v1/tasks/upstream"),
    ("https://host.test/prefix/api/v1/tasks/upstream", "https://host.test/prefix/api/v1/tasks/upstream"),
])
def test_location_preserves_prefix(location, expected):
    """按标准 URL 解析使用同源 Location，缺省路径只追加一次部署前缀。"""
    assert query_url("https://host.test/prefix", location, "upstream") == expected


@pytest.mark.parametrize("location", [
    "https://evil.test/prefix/tasks/u", "http://host.test/prefix/tasks/u",
    "https://host.test:8443/prefix/tasks/u", "https://user:pass@host.test/prefix/tasks/u",
    "/api/v1/tasks/u", "//evil.test/task", "/prefix/../outside",
])
def test_unsafe_location_is_rejected(location):
    """跨源、凭证 URL 和逃离部署前缀的 Location 不会收到匹配鉴权。"""
    with pytest.raises(ValueError):
        query_url("https://host.test/prefix", location, "upstream")


@pytest.mark.anyio
async def test_submit_then_single_query_done(composition_settings):
    """提交 202 后只用上游 ID 补查一次，查询 done 与回调 success 使用各自契约。"""
    calls = []

    def handle(request):
        """模拟有部署前缀的匹配服务，返回受理与单次补查响应。"""
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer test-only"
        if request.method == "POST":
            return httpx.Response(202, json={"taskId": "upstream", "status": "processing"},
                                  headers={"Location": "/deployment/api/v1/tasks/upstream"})
        return httpx.Response(200, json={"task_id": "upstream", "task_type": "match", "status": "done", "result": {"segments": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        matching = Matching(client, composition_settings)
        task_id, url = await matching.submit({"taskId": "local"})
        assert await matching.query_once(task_id, url) == {"segments": []}
    assert [request.method for request in calls] == ["POST", "GET"]
    assert all(str(request.url).endswith("/tasks/upstream") for request in calls[1:])


@pytest.mark.anyio
@pytest.mark.parametrize("body,http_status,code", [
    ({"task_id": "wrong", "task_type": "match", "status": "done", "result": {}}, 200, "matching_contract_error"),
    ({"task_id": "u", "task_type": "other", "status": "done", "result": {}}, 200, "matching_contract_error"),
    ({"task_id": "u", "task_type": "match", "status": "done"}, 200, "matching_contract_error"),
    ({"task_id": "u", "task_type": "match", "status": "done", "result": None}, 200, "matching_contract_error"),
    ({"task_id": "u", "task_type": "match", "status": "success"}, 200, "matching_contract_error"),
    ({"task_id": "u", "task_type": "match", "status": "failed", "error": "secret"}, 200, "matching_failed"),
    ({"task_id": "u", "task_type": "match", "status": "queued"}, 200, "matching_timeout"),
    ({"task_id": "u", "task_type": "match", "status": "running"}, 200, "matching_timeout"),
    ({}, 404, "matching_query_error"),
    ({}, 503, "matching_unavailable"),
    ([], 200, "matching_contract_error"),
])
async def test_single_query_failure_contract(composition_settings, body, http_status, code):
    """状态、身份、缺结果和上游失败均明确失败；任何响应都只有一次查询。"""
    calls = []

    def handle(request):
        """只允许查询，次数由用例核对。"""
        calls.append(request.method)
        return httpx.Response(http_status, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(CompositionError) as error:
            await Matching(client, composition_settings).query_once("u", query_url(composition_settings.match_base_url, None, "u"))
    assert error.value.error["code"] == code
    assert "secret" not in str(error.value)
    assert calls == ["GET"]


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [httpx.ReadTimeout, TimeoutError, httpx.ConnectError])
async def test_single_query_transport_failure(composition_settings, failure):
    """补查超时或断网也仅调用一次，不继续轮询。"""
    calls = []

    def handle(request):
        """模拟请求失败，并记录真实传输次数。"""
        calls.append(request.method)
        raise failure("private-error")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(CompositionError) as error:
            await Matching(client, composition_settings).query_once("u", query_url(composition_settings.match_base_url, None, "u"))
    assert error.value.error["code"] == "matching_unavailable" and calls == ["GET"]


@pytest.mark.anyio
@pytest.mark.parametrize("response_status,response_body,expected", [
    (400, {}, "matching_rejected"), (500, {}, "matching_submission_unknown"),
    (202, {}, "matching_submission_unknown"), (202, {"taskId": "u", "status": "done"}, "matching_submission_unknown"),
    (200, {"taskId": "u", "status": "processing"}, "matching_submission_unknown"),
])
async def test_submission_failure_is_not_retried(composition_settings, response_status, response_body, expected):
    """未知受理不盲目重发，明确拒绝与结果不明分别暴露固定安全错误。"""
    calls = []

    def handle(request):
        """记录提交次数，并返回指定失败响应。"""
        calls.append(request.method)
        return httpx.Response(response_status, json=response_body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(CompositionError) as error:
            await Matching(client, composition_settings).submit({})
    assert error.value.error["code"] == expected and calls == ["POST"]
