"""单函数切片回归：在 server/ 执行 uv run --locked pytest tests/test_segmentation.py -v。"""

import json
import random
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
from openai import APIConnectionError, APITimeoutError
import pytest

from server.segmentation import segment, segmentation


def payload(script, transcript=None, step=200):
    """按单音轨 fun-asr 结构构造单调时间轴；默认每字符一个词，也可传入词列表。"""
    return {
        "script": script,
        "asr_result": {
            "transcripts": [{
                "channel_id": 0,
                "sentences": [{
                    "words": [
                        {"text": c, "begin_time": i * step, "end_time": (i + 1) * step}
                        for i, c in enumerate(script if transcript is None else transcript)
                    ]
                }],
            }]
        },
    }


@pytest.fixture
def model(monkeypatch):
    """显式设置测试配置，用 SDK 上下文替身返回切点与可回溯关键词。"""
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
    result = segment(payload(script, transcript))
    assert isinstance(result, dict)
    assert result["trace"]["edit_cost"] == cost
    assert "".join(s["text"] for s in result["segments"]) == script
    previous = 0
    for item in result["segments"]:
        assert previous <= item["start_time_ms"] < item["end_time_ms"]
        previous = item["end_time_ms"]
        for word in item["keywords"]:
            assert set(word) == {"text"}
            assert word["text"] in item["text"]
    assert result["segments"][0]["start_time_ms"] == 0
    assert previous == len(transcript) * 200
    assert model[0].return_value.__exit__.call_count == 1
    assert model[0].call_args.kwargs["max_retries"] == 1


def test_direct_call_requires_dict(model):
    """包入口直接调用也校验请求类型，不依赖 FastAPI 的输入校验。"""
    with pytest.raises(ValueError, match="字典"):
        segment(None)
    model[0].assert_not_called()


@pytest.mark.parametrize("text", ["", " \t\n\u3000", "，。!?", " ，\t。 "])
def test_ignored_asr_words_preserve_timing(model, client, text):
    """首尾及中间的空内容词被忽略，直接调用和 HTTP 均保留有效词的时间。"""
    data = payload("甲乙丙丁", [text, "甲乙", text, "丙丁", text], step=500)
    result = segment(data)
    assert len(result["segments"]) == 1
    assert result["segments"][0]["text"] == "甲乙丙丁"
    assert result["segments"][0]["start_time_ms"] == 500
    assert result["segments"][0]["end_time_ms"] == 2000
    assert result["trace"]["matched_chars"] == 4
    assert result["trace"]["edit_cost"] == 0
    response = client.post("/segmentations", json=data)
    assert response.status_code == 200
    assert response.json() == result


@pytest.mark.parametrize("text", ["", " \t\n\u3000", "，。!?", " ，\t。 "])
def test_all_ignored_asr_words_return_validation_error(model, client, text):
    """全部词均无有效字符时抛 ValueError，HTTP 返回结构化 422，且不调用模型。"""
    data = payload("甲乙丙丁", [text])
    with pytest.raises(ValueError, match="ASR 缺少有效发音字符"):
        segment(data)
    response = client.post("/segmentations", json=data)
    assert response.status_code == 422
    assert response.json() == {"error": {"message": "ASR 缺少有效发音字符。"}}
    model[0].assert_not_called()


@pytest.mark.parametrize(
    "word_index,change",
    [
        (0, {"begin_time": -1}),
        (0, {"end_time": 0}),
        (1, {"begin_time": 100}),
    ],
)
def test_ignored_asr_words_still_validate_timestamps(model, client, word_index, change):
    """空内容词的非法时间和后续词与其重叠均被拒绝，不因忽略文本跳过校验。"""
    data = payload("甲乙丙丁", [" ，\t", "甲乙丙丁"])
    data["asr_result"]["transcripts"][0]["sentences"][0]["words"][word_index].update(change)
    with pytest.raises(ValueError, match="ASR 时间必须"):
        segment(data)
    response = client.post("/segmentations", json=data)
    assert response.status_code == 422
    assert response.json() == {"error": {"message": "ASR 时间必须有限、非负、单调且不重叠。"}}
    model[0].assert_not_called()


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
        result = segment(payload(script, "".join(transcript)))
        assert result["trace"]["edit_cost"] == table[-1][-1]


def test_long_similar_text_and_budget(model, monkeypatch):
    """长文本少量分散错误可处理，预算恰好通过且少一个单位时拒绝。"""
    script = "甲乙丙丁" * 1000
    result = segment(payload(script, "错" + script[1:-1] + "错", step=10))
    assert result["trace"]["substitution_chars"] == 2
    monkeypatch.setenv("IMV_SEGMENT_MAX_ALIGNMENT_WORK", "4")
    assert isinstance(segmentation.segment(payload("甲乙丙丁")), dict)
    monkeypatch.setenv("IMV_SEGMENT_MAX_ALIGNMENT_WORK", "3")
    with pytest.raises(ValueError, match="预算"):
        segment(payload("甲乙丙丁"))


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(script=""),
        lambda p: p.update(script="。 "),
        lambda p: p.update(script="甲" * 20001),
        lambda p: p.update(asr_result={}),
        lambda p: p.update(extra=True),
        lambda p: p.update(script="完全无关文本"),
        lambda p: p["asr_result"]["transcripts"][0]["sentences"][0]["words"][1].update(begin_time=-1),
        lambda p: p["asr_result"]["transcripts"][0]["sentences"][0]["words"][1].update(end_time=True),
        lambda p: p["asr_result"]["transcripts"][0]["sentences"][0]["words"][1].update(end_time=10**400),
    ],
)
def test_invalid_input_never_calls_model(model, client, change):
    """空输入、超长文本、无关文案及非法时间在模型调用前被拒绝。"""
    data = payload("甲乙丙丁")
    change(data)
    with pytest.raises(ValueError):
        segment(data)
    response = client.post("/segmentations", json=data)
    assert response.status_code == 422
    assert response.json()["error"]["message"]
    model[0].assert_not_called()


@pytest.mark.parametrize("failure,status", [("json", 502), ("shape", 502), ("connect", 502), ("timeout", 504)])
def test_model_failures_close_client(model, client, failure, status):
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
    expected = {"json": RuntimeError, "shape": RuntimeError, "connect": APIConnectionError, "timeout": APITimeoutError}
    with pytest.raises(expected[failure]):
        segment(payload("甲乙丙丁"))
    response = client.post("/segmentations", json=payload("甲乙丙丁"))
    assert response.status_code == status
    assert response.json()["error"]["message"]
    assert model[0].return_value.__exit__.call_count == 2
    assert model[1].chat.completions.create.call_count == 2


def test_api_missing_config(model, client, monkeypatch):
    """真实输入结构可通过路由；缺少模型配置返回 502。"""
    data = payload("甲乙丙丁")
    response = client.post("/segmentations", json=data)
    assert response.status_code == 200
    assert response.json()["segments"][0]["text"] == data["script"]
    monkeypatch.delenv("IMV_LLM_API_KEY")
    assert client.post("/segmentations", json=data).status_code == 502


@pytest.mark.parametrize("asr", [
    None, {}, {"sentences": [{"words": []}]},
    {"transcripts": None}, {"transcripts": {}}, {"transcripts": []},
    {"transcripts": [None]}, {"transcripts": [{}]},
    {"transcripts": [{"sentences": []}]},
    {"transcripts": [{"sentences": [{"words": []}]}] * 2},
])
def test_invalid_fun_asr_structure(model, client, asr):
    """拒绝旧外层、非法或空音轨及多音轨；直接调用与 HTTP 均在模型前失败。"""
    data = {"script": "甲乙丙丁", "asr_result": asr}
    with pytest.raises(ValueError) as error:
        segment(data)
    response = client.post("/segmentations", json=data)
    assert response.status_code == 422
    assert response.json() == {"error": {"message": str(error.value)}}
    model[0].assert_not_called()


@pytest.mark.parametrize("fields", [("begin_time",), ("end_time",), ("begin_time", "end_time")])
def test_legacy_asr_time_fields_rejected(model, client, fields):
    """任一真实时间字段缺失时不回退到旧 _ms 别名，返回 422 且不调用模型。"""
    data = payload("甲乙丙丁")
    word = data["asr_result"]["transcripts"][0]["sentences"][0]["words"][0]
    for field in fields:
        word[field + "_ms"] = word.pop(field)
    with pytest.raises(ValueError, match="ASR 时间必须"):
        segment(data)
    response = client.post("/segmentations", json=data)
    assert response.status_code == 422
    assert "ASR 时间必须" in response.json()["error"]["message"]
    model[0].assert_not_called()


def test_real_fun_asr_excerpt(model, client):
    """使用用户转写的前两句，验证真实词时间、空格、独立标点及跨句停顿；不访问音频。"""
    # 保留样本原始词边界和毫秒值；无关元数据不参与对齐，原文标点来自 script。
    sentences = [
        {
            "begin_time": 160, "end_time": 2400, "sentence_id": 1,
            "text": "刚才我家人还问我，家里不是还有鸡蛋吗？",
            "words": [
                {"begin_time": a, "end_time": b, "text": text, "punctuation": punctuation}
                for a, b, text, punctuation in [
                    (160, 360, "刚才", ""), (360, 480, "我", ""),
                    (480, 720, "家人", ""), (720, 840, "还", ""),
                    (840, 1080, "问我", "，"), (1280, 1520, "家里", ""),
                    (1520, 1720, "不是", ""), (1720, 1960, "还有", ""),
                    (1960, 2280, "鸡蛋", ""), (2280, 2400, "吗", "？"),
                ]
            ],
        },
        {
            "begin_time": 2520, "end_time": 3440, "sentence_id": 2,
            "text": " 怎么又买一箱？",
            "words": [
                {"begin_time": a, "end_time": b, "text": text, "punctuation": punctuation}
                for a, b, text, punctuation in [
                    (2520, 2640, " 怎", ""), (2640, 2800, "么", ""),
                    (2800, 2960, "又", ""), (2960, 3120, "买", ""),
                    (3120, 3240, "一", ""), (3240, 3440, "箱", "？"),
                ]
            ],
        },
    ]
    data = {
        "script": "刚才我家人还问我，家里不是还有鸡蛋吗？怎么又买一箱？",
        "asr_result": {
            "properties": {"channels": [0], "original_sampling_rate": 24000},
            "transcripts": [{"channel_id": 0, "sentences": sentences}],
        },
    }
    original = json.dumps(data, ensure_ascii=False)
    response = client.post("/segmentations", json=data)
    assert response.status_code == 200
    result = response.json()
    assert result == segment(data)
    assert json.dumps(data, ensure_ascii=False) == original
    assert len(result["segments"]) == 1
    assert result["segments"][0]["text"] == data["script"]
    assert result["segments"][0]["start_time_ms"] == 160
    assert result["segments"][0]["end_time_ms"] == 3440
    assert result["trace"]["edit_cost"] == 0
    assert result["warnings"] == []


def test_model_cuts_keywords_and_protected_runs(model):
    """模型切点去重后用于分段；保留有效长短关键词，过滤不存在及重复候选。"""
    responses = iter(
        [
            {"boundaries_after": [1, 1]},
            {"keywords": [["甲乙", "甲", "不存在"], ["QQ", "qq", "QQ", "40%"]]},
        ]
    )
    model[1].chat.completions.create.side_effect = lambda **_: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(next(responses))))]
    )
    result = segment(payload("甲乙丙丁。QQ增长40%。", step=400))
    assert [s["text"] for s in result["segments"]] == ["甲乙丙丁。", "QQ增长40%。"]
    assert [[k["text"] for k in s["keywords"]] for s in result["segments"]] == [["甲乙", "甲"], ["QQ", "40%"]]
    assert result["trace"]["keyword_rejected_count"] == 3


@pytest.mark.parametrize("ids", [None, "1", [True], [0], [-1], [2], [999], [1.0], ["1"], [1, 999]])
def test_invalid_model_boundaries_fail_without_fallback(model, client, ids):
    """非法切点返回 502，不静默生成整段或继续请求关键词，且关闭 SDK。"""
    model[1].chat.completions.create.side_effect = None
    model[1].chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"boundaries_after": ids})))]
    )
    response = client.post("/segmentations", json=payload("甲乙丙丁。戊己庚辛。"))
    assert response.status_code == 502
    assert "boundaries_after" in response.json()["error"]["message"]
    assert model[1].chat.completions.create.call_count == 1
    assert model[0].return_value.__exit__.call_count == 1


@pytest.mark.parametrize("separator", ["，", "。", "， "])
def test_english_protection_preserves_model_cut(model, separator):
    """英文串只在原文连续范围内受保护，不吞掉标点两侧的模型切点。"""
    model[1].chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))])
        for data in [{"boundaries_after": [1]}, {"keywords": [["Hello"], ["world"]]}]
    ]
    result = segment(payload(f"Hello{separator}world。", step=300))
    assert [s["text"] for s in result["segments"]] == [f"Hello{separator}", "world。"]
    assert result["trace"]["merge_count"] == result["trace"]["split_count"] == 0


def test_duration_split_can_use_english_whitespace(model, monkeypatch):
    """空格是两个英文词之间的合法时长切点，拆分后保留原始空格。"""
    monkeypatch.setenv("IMV_SEGMENT_MAX_DURATION_MS", "2000")
    result = segment(payload("Hello world", step=300))
    assert [s["text"] for s in result["segments"]] == ["Hello ", "world"]
    assert result["trace"]["split_count"] == 1
    assert result["warnings"] == []


@pytest.mark.parametrize("script", ["AB-CD", "12.5%"])
def test_duration_split_keeps_contiguous_tokens(model, script):
    """连字符词和小数百分数不可为满足时长而拆开，无法满足时长时告警。"""
    result = segment(payload(script, step=2000))
    assert [s["text"] for s in result["segments"]] == [script]
    assert result["trace"]["split_count"] == 0
    assert any(w["code"] == "segment_duration_out_of_range" for w in result["warnings"])


def test_keywords_follow_duration_adjustment(model):
    """短片段合并后才请求关键词；第二次模型收到最终文本，输出与最终片段对应。"""
    model[1].chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))])
        for data in [{"boundaries_after": [1]}, {"keywords": [["甲乙", "丁戊"]]}]
    ]
    result = segment(payload("甲乙。丙丁戊己。"))
    assert [s["text"] for s in result["segments"]] == ["甲乙。丙丁戊己。"]
    assert result["trace"]["merge_count"] == 1
    request = model[1].chat.completions.create.call_args.kwargs
    assert json.loads(request["messages"][1]["content"]) == ["甲乙。丙丁戊己。"]
    assert result["segments"][0]["keywords"] == [{"text": "甲乙"}, {"text": "丁戊"}]


def test_keyword_validation_preserves_exact_text_and_limits(model, monkeypatch):
    """关键词保留有效包含词和单字，按原文排序，并检查全半角、长度、去重及数量上限。"""
    monkeypatch.setenv("IMV_SEGMENT_MAX_KEYWORDS", "3")
    monkeypatch.setenv("IMV_SEGMENT_KEYWORD_MAX_LENGTH", "3")
    model[1].chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))])
        for data in [
            {"boundaries_after": []},
            {"keywords": [["甲", "ＡＢ", "AB", "Ａ", "ＡＢ", "", " ", "ＡＢ甲乙", "乙"]]},
        ]
    ]
    result = segment(payload("ＡＢ甲乙。", step=400))
    assert result["segments"][0]["keywords"] == [{"text": "ＡＢ"}, {"text": "Ａ"}, {"text": "甲"}]
    assert result["trace"]["keyword_rejected_count"] == 6


def test_timeline_outside_repair_and_duration_warnings(model):
    """局部插字不改变块外范围，超长不可拆字串告警而非拆断受保护文本。"""
    result = segment(payload("甲乙丙丁戊己。庚辛壬癸。", "甲乙丁戊己。庚辛壬癸。", step=800))
    assert result["trace"]["repair_block_count"] == 1
    assert result["segments"][0]["start_time_ms"] == 0
    assert result["segments"][-1]["end_time_ms"] == 8000  # 末字继承原 ASR 时间，尾部标点不参与对齐
    result = segment(payload("ABCDEFGHIJKLMNOPQRSTUVWXYZ", step=500))
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
    with pytest.raises(RuntimeError):
        segment(payload("甲乙丙丁"))
    model[0].assert_not_called()


def test_dotenv_configuration_and_environment_priority(model, client, monkeypatch, tmp_path):
    """真实 .env 提供模型配置，环境覆盖文件；每次调用重读文件且不修改进程环境。"""
    for key in ("BASE_URL", "API_KEY", "MODEL"):
        monkeypatch.delenv("IMV_LLM_" + key)
    env_file = tmp_path / ".env"
    content = (
        "imv_llm_base_url=https://example.test/v1\n"
        "IMV_LLM_API_KEY=file-secret\nIMV_LLM_MODEL=文件模型\n"
        "IMV_LLM_TIMEOUT_SECONDS=10\nUNRELATED=value\n"
    )
    env_file.write_text(content, encoding="utf-8")
    monkeypatch.setenv("IMV_LLM_TIMEOUT_SECONDS", "2.5")
    data = payload("甲乙丙丁")
    assert client.post("/segmentations", json=data).status_code == 200
    assert model[0].call_args.kwargs == {
        "base_url": "https://example.test/v1", "api_key": "file-secret",
        "timeout": 2.5, "max_retries": 1,
    }
    assert model[1].chat.completions.create.call_args.kwargs["model"] == "文件模型"
    env_file.write_text(content.replace("文件模型", "新模型"), encoding="utf-8")
    assert client.post("/segmentations", json=data).status_code == 200
    assert model[1].chat.completions.create.call_args.kwargs["model"] == "新模型"
    env_file.unlink()
    assert client.post("/segmentations", json=data).status_code == 502


@pytest.mark.parametrize("key,value", [
    ("LLM_API_KEY", ""), ("LLM_MODEL", " \t"), ("LLM_BASE_URL", ""),
    ("LLM_TIMEOUT_SECONDS", "nan"), ("LLM_TIMEOUT_SECONDS", "inf"),
    ("LLM_TIMEOUT_SECONDS", "0"), ("LLM_MAX_RETRIES", "-1"), ("LLM_MAX_RETRIES", "4"),
    ("SEGMENT_MIN_DURATION_MS", "199"), ("SEGMENT_MIN_DURATION_MS", "6000"),
    ("SEGMENT_MAX_DURATION_MS", "30001"), ("SEGMENT_MAX_DURATION_MS", "1199"),
    ("SEGMENT_MAX_KEYWORDS", "-1"), ("SEGMENT_MAX_KEYWORDS", "21"),
    ("SEGMENT_KEYWORD_MAX_LENGTH", "1"), ("SEGMENT_KEYWORD_MAX_LENGTH", "31"),
    ("SEGMENT_MAX_ALIGNMENT_WORK", "0"), ("SEGMENT_MAX_ALIGNMENT_WORK", "1.5"),
    ("ALLOW_INSECURE_LLM_HTTP", "invalid-secret"), ("LLM_TIMEOUT_SECONDS", "invalid-secret"),
])
def test_settings_validation_returns_safe_error(model, client, monkeypatch, key, value):
    """缺失内容、非法类型及越界配置返回固定 502，不泄露配置值，也不创建 SDK。"""
    monkeypatch.setenv("IMV_" + key, value)
    response = client.post("/segmentations", json=payload("甲乙丙丁"))
    assert response.status_code == 502
    assert response.json() == {"error": {"message": "模型或切片配置缺失或不合法，请检查 IMV_ 配置。"}}
    model[0].assert_not_called()


@pytest.mark.parametrize("value,allowed", [("true", True), ("1", True), ("false", False), ("0", False)])
def test_settings_boolean_http_authorization(model, client, monkeypatch, value, allowed):
    """Pydantic 解析布尔配置；仅显式启用时允许远程 HTTP 模型地址。"""
    monkeypatch.setenv("IMV_LLM_BASE_URL", "http://remote.test/v1")
    monkeypatch.setenv("IMV_ALLOW_INSECURE_LLM_HTTP", value)
    response = client.post("/segmentations", json=payload("甲乙丙丁"))
    assert response.status_code == (200 if allowed else 502)
    if not allowed:
        model[0].assert_not_called()


def test_api_response_contract(model, client):
    """词级时间轴经 HTTP 返回完整片段、毫秒时间、无偏移关键词及诊断计数。"""
    model[1].chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))])
        for data in [{"boundaries_after": []}, {"keywords": [["世界"]]}]
    ]
    data = {
        "script": "你好世界。",
        "asr_result": {"transcripts": [{"sentences": [
            {"words": [{"text": "你好世界", "begin_time": 0, "end_time": 2000}]}
        ]}]},
    }
    response = client.post("/segmentations", json=data)
    assert response.status_code == 200
    assert response.json() == {
        "segments": [
            {
                "segment_id": "seg_001",
                "text": "你好世界。",
                "start_time_ms": 0,
                "end_time_ms": 2000,
                "keywords": [{"text": "世界"}],
            }
        ],
        "warnings": [],
        "trace": {
            "matched_chars": 4,
            "substitution_chars": 0,
            "script_extra_chars": 0,
            "asr_extra_chars": 0,
            "edit_cost": 0,
            "repair_block_count": 0,
            "merge_count": 0,
            "split_count": 0,
            "segment_count": 1,
            "keyword_rejected_count": 0,
        },
    }


@pytest.mark.parametrize("body", ["", "[]", "{"])
def test_framework_validation(client, body):
    """缺少请求体、非对象和非法 JSON 返回 422 与框架的 detail 数组。"""
    response = client.post("/segmentations", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


def test_internal_error(client, monkeypatch):
    """内部约束异常由路由转换为 500 与 error.message。"""
    from server.sub_api import segmentation as route

    monkeypatch.setattr(route, "segment", MagicMock(side_effect=AssertionError("片段未完整覆盖文案。")))
    response = client.post("/segmentations", json=payload("甲乙丙丁"))
    assert response.status_code == 500
    assert response.json() == {"error": {"message": "片段未完整覆盖文案。"}}
