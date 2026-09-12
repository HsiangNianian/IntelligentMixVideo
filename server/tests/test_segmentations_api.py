"""文案切片接口测试。

在 server/ 执行 uv run --locked pytest -v；使用离线样本与替身。
"""

from unittest.mock import patch

import pytest

from fastapi.testclient import TestClient
from server.app import app
from server.core.config import Settings, get_settings
from server.sub_api.segmentation.aligner import (
    align,
    build_asr_chars,
    build_script_chars,
    repair_blocks,
)
from server.sub_api.segmentation.router import get_segment_service
from server.sub_api.segmentation.schemas import AsrResult
from server.sub_api.segmentation.service import SegmentService
from .support import StubPlanner, broken_script, load_asr_payload

MIN_MS = 1200
MAX_MS = 6000


@pytest.fixture(autouse=True)
def segment_service_override(monkeypatch):
    """每个接口用例使用离线规划器，结束后恢复原有依赖覆盖。"""
    monkeypatch.setattr(
        app,
        "dependency_overrides",
        {
            **app.dependency_overrides,
            get_segment_service: lambda: SegmentService(StubPlanner()),
        },
    )


def post(client: TestClient, script: str, asr_payload: dict):
    """通过共享客户端提交文案及词级 ASR 结果。"""
    return client.post(
        "/segmentations", json={"script": script, "asr_result": asr_payload}
    )


def assert_segment_contract(script: str, body: dict) -> None:
    """检查样本响应的文本覆盖、时长、时间连续性和关键词定位。"""
    segments = body["segments"]
    assert segments
    assert "".join((segment["text"] for segment in segments)) == script
    previous_end = None
    for index, segment in enumerate(segments):
        if index:
            assert segment["start_time_ms"] == previous_end  # 首尾相接
        assert segment["segment_id"].startswith("seg_")
        assert segment["start_time_ms"] < segment["end_time_ms"]
        duration = segment["end_time_ms"] - segment["start_time_ms"]
        assert duration >= MIN_MS
        assert duration <= MAX_MS
        previous_end = segment["end_time_ms"]
        for keyword in segment["keywords"]:
            assert segment["text"][keyword["start"] : keyword["end"]] == keyword["text"]


def test_aligns_script_with_asr_timeline(client) -> None:
    """HTTP 接口返回完整切片契约，相同文案保留 ASR 时间。"""
    payload = load_asr_payload()
    script = payload["transcripts"][0]["text"]

    response = post(client, script, payload)

    assert response.status_code == 200
    body = response.json()
    assert_segment_contract(script, body)
    assert body["trace"]["edit_cost"] == 0
    assert body["trace"]["repair_block_count"] == 0
    assert body["segments"][0]["start_time_ms"] == 160
    assert body["segments"][-1]["end_time_ms"] == 36700
    assert any((segment["keywords"] for segment in body["segments"]))


def test_survives_typo_missing_and_extra_chars(client) -> None:
    """混合错字与增删仍返回合法片段及预期诊断统计。"""
    payload = load_asr_payload()
    script = broken_script(payload["transcripts"][0]["text"])

    response = post(client, script, payload)

    assert response.status_code == 200
    body = response.json()
    assert_segment_contract(script, body)
    trace = body["trace"]
    assert trace["substitution_chars"] == 2
    assert trace["asr_extra_chars"] == 3
    assert trace["script_extra_chars"] == 2
    assert trace["repair_block_count"] == 2
    assert trace["edit_cost"] == 7


def test_boundaries_stay_outside_repair_blocks(client) -> None:
    """正确转换操作下标，验证 HTTP 响应切点不在修复块内部。"""
    payload = load_asr_payload()
    script = broken_script(payload["transcripts"][0]["text"])
    script_chars = build_script_chars(script)
    ops = align(script_chars, build_asr_chars(AsrResult.model_validate(payload)))
    block_chars = [
        [op.script_index for op in ops[begin:end] if op.script_index is not None]
        for begin, end in repair_blocks(ops)
    ]
    assert [
        "".join((script_chars[i].char for i in indices)) for indices in block_chars
    ] == ["料吃", "家散养土"]
    block_offsets = [
        (script_chars[indices[0]].index, script_chars[indices[-1]].index + 1)
        for indices in block_chars
    ]

    response = post(client, script, payload)

    assert response.status_code == 200
    body = response.json()
    assert body["trace"]["repair_block_count"] == len(block_offsets)
    assert len(block_offsets) == 2
    cursor = 0
    for segment in body["segments"][:-1]:
        cursor += len(segment["text"])
        for begin, end in block_offsets:
            assert not begin < cursor < end


def test_rejects_asr_without_word_timeline(client) -> None:
    """缺少词级时间戳时拒绝请求，不能推测整段时间轴。"""
    payload = load_asr_payload()
    for sentence in payload["transcripts"][0]["sentences"]:
        sentence.pop("words", None)

    response = post(client, payload["transcripts"][0]["text"], payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "asr_timeline_missing"


def test_rejects_unrelated_script(client) -> None:
    """无关文案被拒绝，不能生成不可靠片段。"""
    response = post(client, "今天讲解 Python 异步编程与协程调度。", load_asr_payload())

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "script_asr_alignment_failed"


def test_configured_work_budget_returns_422_before_model_call(client) -> None:
    """环境工作预算传入服务，超限返回 422 且不请求模型。"""
    with patch.dict("os.environ", {"IMV_SEGMENT_MAX_ALIGNMENT_WORK": "1"}):
        settings = Settings(
            _env_file=None,
            llm_base_url="https://example.com/v1",
            llm_api_key="test-key",
            llm_model="test-model",
        )
    with patch("server.sub_api.segmentation.router.OpenAIChatClient") as model:
        service = get_segment_service(settings)
        assert service.max_alignment_work == 1
        app.dependency_overrides[get_segment_service] = lambda: service
        payload = load_asr_payload()
        response = post(client, payload["transcripts"][0]["text"], payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "alignment_input_too_large"
        assert not model.return_value.mock_calls


def test_requires_model_configuration(client) -> None:
    """模型未配置时返回 502，不能使用外部环境中的真实模型。"""
    app.dependency_overrides.pop(get_segment_service)
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)

    response = post(client, "任意文案", load_asr_payload())

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "llm_provider_error"
