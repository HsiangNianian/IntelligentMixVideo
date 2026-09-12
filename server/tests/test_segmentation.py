"""单函数切片回归：在 server/ 执行 uv run --locked pytest tests/test_segmentation.py -v。"""

import json
import random
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
from openai import APIConnectionError, APITimeoutError
import pytest

from server.sub_api import segmentation


def payload(script, transcript=None, step=200):
    """构造每字符一个词的单调时间轴，避免大型静态 ASR 样本。"""
    return {
        "script": script,
        "asr_result": {
            "sentences": [
                {
                    "words": [
                        {"text": c, "begin_time_ms": i * step, "end_time_ms": (i + 1) * step}
                        for i, c in enumerate(script if transcript is None else transcript)
                    ]
                }
            ]
        },
    }


@pytest.fixture
def model(monkeypatch):
    """隔离环境和 .env，用 SDK 上下文替身返回切点与可回溯关键词。"""
    import os

    for key in list(os.environ):
        if key.upper().startswith("IMV_"):
            monkeypatch.delenv(key)
    monkeypatch.setattr(segmentation, "dotenv_values", lambda _: {})
    for key, value in {"BASE_URL": "https://example.test/v1", "API_KEY": "test", "MODEL": "test"}.items():
        monkeypatch.setenv("IMV_LLM_" + key, value)
    client = MagicMock()

    def respond(**kwargs):
        """按输入形状识别规划阶段，关键词包含重复及不存在的候选以验证过滤。"""
        content = json.loads(kwargs["messages"][1]["content"])
        data = (
            {"boundaries_after": []}
            if isinstance(content[0], dict)
            else {"keywords": [[s[:2], s[:1], s[:2], "不存在的词"] for s in content]}
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))])

    client.chat.completions.create.side_effect = respond
    factory = MagicMock()
    factory.return_value.__enter__.return_value = client
    monkeypatch.setattr(segmentation, "OpenAI", factory)
    return factory, client


@pytest.mark.parametrize(
    "script,transcript,cost",
    [
        ("甲乙丙丁", "甲乙丙丁", 0),
        ("甲乙丙丁", "甲错丙丁", 1),
        ("甲乙丙丁", "甲丙丁", 1),
        ("甲乙丙丁", "甲乙多丙丁", 1),
        ("甲甲乙", "甲乙", 1),
        ("甲乙", "甲甲乙", 1),
        ("ＡＢ甲乙", "ab甲乙", 0),
    ],
)
def test_alignment_and_contract(model, script, transcript, cost):
    """替换、增删、重复字和归一化保持最优代价、文本覆盖及合法时间。"""
    result = segmentation.segment(payload(script, transcript))
    assert isinstance(result, dict)
    assert result["trace"]["edit_cost"] == cost
    assert "".join(s["text"] for s in result["segments"]) == script
    previous = 0
    for item in result["segments"]:
        assert previous <= item["start_time_ms"] < item["end_time_ms"]
        previous = item["end_time_ms"]
        for word in item["keywords"]:
            assert item["text"][word["start"] : word["end"]] == word["text"]
    assert result["segments"][0]["start_time_ms"] == 0
    assert previous == len(transcript) * 200
    assert model[0].return_value.__exit__.call_count == 1
    assert model[0].call_args.kwargs["max_retries"] == 1


def test_wavefront_matches_independent_dp(model):
    """随机小差异文本与完整 DP 对照，防止内联迁移损坏搜索与回溯。"""
    rng = random.Random(42)
    for _ in range(100):
        script = "".join(rng.choices("甲乙丙丁", k=12))
        transcript = list(script)
        transcript[rng.randrange(12)] = "错"
        transcript.insert(rng.randrange(12), "多")
        del transcript[rng.randrange(len(transcript))]
        table = [list(range(len(transcript) + 1))]
        for i, char in enumerate(script, 1):
            row = [i]
            for j, other in enumerate(transcript, 1):
                row.append(min(row[-1] + 1, table[-1][j] + 1, table[-1][j - 1] + (char != other)))
            table.append(row)
        result = segmentation.segment(payload(script, "".join(transcript)))
        assert result["trace"]["edit_cost"] == table[-1][-1]


def test_long_similar_text_and_budget(model, monkeypatch):
    """长文本少量分散错误可处理，预算恰好通过且少一个单位时拒绝。"""
    script = "甲乙丙丁" * 1000
    result = segmentation.segment(payload(script, "错" + script[1:-1] + "错", step=10))
    assert result["trace"]["substitution_chars"] == 2
    monkeypatch.setenv("IMV_SEGMENT_MAX_ALIGNMENT_WORK", "4")
    assert isinstance(segmentation.segment(payload("甲乙丙丁")), dict)
    monkeypatch.setenv("IMV_SEGMENT_MAX_ALIGNMENT_WORK", "3")
    assert segmentation.segment(payload("甲乙丙丁")).status_code == 422


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(script=""),
        lambda p: p.update(script="。 "),
        lambda p: p.update(script="甲" * 20001),
        lambda p: p.update(asr_result={}),
        lambda p: p.update(extra=True),
        lambda p: p.update(script="完全无关文本"),
        lambda p: p["asr_result"]["sentences"][0]["words"][1].update(begin_time_ms=-1),
        lambda p: p["asr_result"]["sentences"][0]["words"][1].update(end_time_ms=True),
        lambda p: p["asr_result"]["sentences"][0]["words"][1].update(end_time_ms=10**400),
    ],
)
def test_invalid_input_never_calls_model(model, change):
    """空输入、超长文本、无关文案及非法时间在模型调用前被拒绝。"""
    data = payload("甲乙丙丁")
    change(data)
    assert segmentation.segment(data).status_code == 422
    model[0].assert_not_called()


@pytest.mark.parametrize("failure,status", [("json", 502), ("shape", 502), ("connect", 502), ("timeout", 504)])
def test_model_failures_close_client(model, failure, status):
    """非法模型输出、连接失败和超时明确返回错误，并关闭 SDK 上下文。"""
    if failure in ("json", "shape"):
        model[1].chat.completions.create.side_effect = None
        model[1].chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="oops" if failure == "json" else "[]"))]
        )
    else:
        request = httpx.Request("POST", "https://example.test/v1")
        error = APIConnectionError if failure == "connect" else APITimeoutError
        model[1].chat.completions.create.side_effect = error(request=request)
    assert segmentation.segment(payload("甲乙丙丁")).status_code == status
    assert model[0].return_value.__exit__.call_count == 1
    assert model[1].chat.completions.create.call_count == 1


def test_api_aliases_and_missing_config(model, client, monkeypatch):
    """路由正确注册，兼容 fun-asr 外层与时间字段别名，缺少模型配置返回 502。"""
    data = payload("甲乙丙丁")
    asr = data["asr_result"]
    for word in asr["sentences"][0]["words"]:
        word["begin_time"] = word.pop("begin_time_ms")
        word["end_time"] = word.pop("end_time_ms")
    data["asr_result"] = {"transcripts": [asr]}
    response = client.post("/segmentations", json=data)
    assert response.status_code == 200
    assert response.json()["segments"][0]["text"] == data["script"]
    monkeypatch.delenv("IMV_LLM_API_KEY")
    assert client.post("/segmentations", json=data).status_code == 502


def test_model_cuts_keywords_and_protected_runs(model):
    """模型切点用于分段，过滤越界编号；关键词精确匹配，英文数字不被拆开。"""
    responses = iter(
        [
            {"boundaries_after": [1, 1, True, -1, 999]},
            {"keywords": [["甲乙", "甲", "不存在"], ["QQ", "qq", "QQ", "40%"]]},
        ]
    )
    model[1].chat.completions.create.side_effect = lambda **_: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(next(responses))))]
    )
    result = segmentation.segment(payload("甲乙丙丁。QQ增长40%。", step=400))
    assert [s["text"] for s in result["segments"]] == ["甲乙丙丁。", "QQ增长40%。"]
    assert [[k["text"] for k in s["keywords"]] for s in result["segments"]] == [["甲乙"], ["QQ", "40%"]]
    assert result["trace"]["keyword_rejected_count"] == 4


def test_timeline_outside_repair_and_duration_warnings(model):
    """局部插字不改变块外范围，超长不可拆字串告警而非拆断受保护文本。"""
    result = segmentation.segment(payload("甲乙丙丁戊己。庚辛壬癸。", "甲乙丁戊己。庚辛壬癸。", step=800))
    assert result["trace"]["repair_block_count"] == 1
    assert result["segments"][0]["start_time_ms"] == 0
    assert result["segments"][-1]["end_time_ms"] == 8000  # 末字继承原 ASR 时间，尾部标点不参与对齐
    result = segmentation.segment(payload("ABCDEFGHIJKLMNOPQRSTUVWXYZ", step=500))
    assert len(result["segments"]) == 1
    assert any(w["code"] == "segment_duration_out_of_range" for w in result["warnings"])


@pytest.mark.parametrize(
    "key,value",
    [
        ("IMV_LLM_BASE_URL", "http://remote.test/v1"),
        ("IMV_LLM_BASE_URL", "https://["),
        ("IMV_SEGMENT_MIN_DURATION_MS", "bad"),
        ("IMV_LLM_TIMEOUT_SECONDS", "nan"),
        ("IMV_SEGMENT_MAX_ALIGNMENT_WORK", "0"),
    ],
)
def test_invalid_configuration(model, monkeypatch, key, value):
    """配置错误与未授权的远程明文传输在创建 SDK 前被拒绝。"""
    monkeypatch.setenv(key, value)
    assert segmentation.segment(payload("甲乙丙丁")).status_code == 502
    model[0].assert_not_called()
