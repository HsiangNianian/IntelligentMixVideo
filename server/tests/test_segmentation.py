"""切片核心行为回归，隔离 ASR 与模型；在 server/ 执行 uv run --locked pytest tests/test_segmentation.py -v。"""

import json
import logging
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


def grouped_payload(script, groups, step=200):
    """按 groups 把语料切成多个 ASR 句构造单调时间轴，句间留一个 step 的停顿。"""
    sentences, begin = [], 0
    for group in groups:
        words = []
        for char in group:
            words.append({"text": char, "begin_time": begin, "end_time": begin + step})
            begin += step
        begin += step
        sentences.append({"words": words})
    return {"script": script, "asr_result": {"transcripts": [{"sentences": sentences}]}}


@pytest.fixture
def model(monkeypatch):
    """显式设置测试配置，用 SDK 上下文替身返回切点与可回溯关键词。"""
    for key, value in {"BASE_URL": "https://example.test/v1", "API_KEY": "test", "MODEL": "test"}.items():
        monkeypatch.setenv("IMV_LLM_" + key, value)
    client = MagicMock()

    def respond(**kwargs):
        """按输入形状返回默认切点与单个关键词；特殊模型结果在各用例中设置。"""
        content = json.loads(kwargs["messages"][1]["content"])
        data = (
            {"boundaries_after": []}
            if isinstance(content[0], dict)
            else {"keywords": [[s[:2]] for s in content]}
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))])

    client.chat.completions.create.side_effect = respond
    factory = MagicMock()
    factory.return_value.__enter__.return_value = client
    monkeypatch.setattr(segmentation, "OpenAI", factory)
    return factory, client


@pytest.fixture
def server_logs(caplog):
    """直接捕获 Uvicorn logger，兼容其他测试关闭日志传播的情况。"""
    logger = logging.getLogger("uvicorn.error")
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level("INFO", logger=logger.name):
            yield caplog
    finally:
        logger.removeHandler(caplog.handler)


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
    assert result["trace"]["edit_cost"] == cost
    assert "".join(s["text"] for s in result["segments"]) == script
    previous = 0
    for item in result["segments"]:
        assert previous <= item["start_time"] < item["end_time"]
        previous = item["end_time"]
        if item["keyword"]:
            assert item["keyword"] in item["text"]
    assert result["segments"][0]["start_time"] == 0
    assert previous == len(transcript) * 200 / 1000


def test_subtitle_parts_split_at_punctuation_with_joined_times(model):
    """字幕按原文标点拆开，时间来自下一发音字符，原切片仍供素材匹配。"""
    script = "看新闻了吗？太炸裂了?AI把数学圈搞炸了。"
    result = segment(payload(script))
    assert len(result["segments"]) == 1
    original = result["segments"][0]
    parts = original["subtitle_parts"]
    assert original["text"] == script
    assert [part["text"] for part in parts] == ["看新闻了吗？", "太炸裂了?", "AI把数学圈搞炸了"]
    assert parts[0]["start_time"] == original["start_time"]
    assert parts[-1]["end_time"] == original["end_time"]
    assert all(parts[i]["end_time"] == parts[i + 1]["start_time"] for i in range(len(parts) - 1))


@pytest.mark.parametrize("script,transcript", [("甲乙", "甲丙丁戊己庚辛壬癸乙"), ("甲丙丁戊己庚辛壬癸乙", "甲乙"), ("甲乙", "丙丁")])
def test_low_match_ratio_still_segments(model, script, transcript):
    """低匹配率仍完成对齐和时间投射，只通过 warnings 提示差异。"""
    result = segment(payload(script, transcript, step=500))
    assert "".join(item["text"] for item in result["segments"]) == script
    assert result["segments"][0]["start_time"] == 0
    assert result["segments"][-1]["end_time"] == len(transcript) * 0.5
    assert any(w["code"] == "low_alignment_match_ratio" for w in result["warnings"])


def test_ignored_asr_words_preserve_timing(model):
    """首尾及中间的空内容词被忽略，保留有效词的时间。"""
    data = payload("甲乙丙丁", ["", "甲乙", " \t\n\u3000，。!?", "丙丁", "。"], step=500)
    result = segment(data)
    assert len(result["segments"]) == 1
    assert result["segments"][0]["text"] == "甲乙丙丁"
    assert result["segments"][0]["start_time"] == 0.5
    assert result["segments"][0]["end_time"] == 2.0
    assert result["trace"]["matched_chars"] == 4
    assert result["trace"]["edit_cost"] == 0


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


@pytest.mark.parametrize("failure,status", [("json", 502), ("shape", 502), ("connect", 502), ("timeout", 504)])
def test_model_failures_close_client(model, client, failure, status):
    """非法输出、连接失败和超时保留原有错误响应，并关闭 SDK。"""
    if failure in ("json", "shape"):
        model[1].chat.completions.create.side_effect = None
        model[1].chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="oops" if failure == "json" else "[]"))]
        )
    else:
        request = httpx.Request("POST", "https://example.test/v1")
        error = APIConnectionError if failure == "connect" else APITimeoutError
        model[1].chat.completions.create.side_effect = error(request=request)
    response = client.post("/segmentations", json=payload("甲乙丙丁"))
    assert response.status_code == status
    assert response.json()["error"]["message"]
    assert set(response.json()) == {"error"}
    assert model[0].return_value.__exit__.call_count == 1
    assert model[1].chat.completions.create.call_count == 1


def test_real_fun_asr_excerpt(model):
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
    result = segment(data)
    assert json.dumps(data, ensure_ascii=False) == original
    assert len(result["segments"]) == 1
    assert result["segments"][0]["text"] == data["script"]
    assert result["segments"][0]["start_time"] == 0.16
    assert result["segments"][0]["end_time"] == 3.44
    assert result["trace"]["edit_cost"] == 0
    assert result["warnings"] == []
    # 单段横跨两个 ASR 句时按首字归属，仍报第一句且不因此切分文本。
    assert result["segments"][0]["group_id"] == [1, 1]


def test_model_cuts_keywords_and_protected_runs(model):
    """模型切点去重后用于分段；每段只保留原文最靠前的有效词，过滤不存在及重复候选。"""
    responses = iter(
        [
            {"boundaries_after": [1, 1]},
            {"keywords": [["甲乙", "甲", "不存在"], ["QQ", "qq", "QQ", "40%"]]},
        ]
    )
    model[1].chat.completions.create.side_effect = lambda **_: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(next(responses))))]
    )
    result = segment(payload("甲乙丙丁。QQ增长40%。", step=500))
    assert [s["text"] for s in result["segments"]] == ["甲乙丙丁。", "QQ增长40%。"]
    assert [s["keyword"] for s in result["segments"]] == ["甲乙", "QQ"]
    assert result["trace"]["keyword_rejected_count"] == 5


@pytest.mark.parametrize("ids", [None, [True], [0], [2]])
def test_invalid_model_boundaries_fail_without_fallback(model, client, ids):
    """非法切点返回 502，不静默生成整段或继续请求关键词，且关闭 SDK。"""
    model[1].chat.completions.create.side_effect = None
    model[1].chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"boundaries_after": ids})))]
    )
    response = client.post("/segmentations", json=payload("甲乙丙丁。戊己庚辛。", step=500))
    assert response.status_code == 502
    assert "boundaries_after" in response.json()["error"]["message"]
    assert model[1].chat.completions.create.call_count == 1
    assert model[0].return_value.__exit__.call_count == 1


def test_keyword_failure_logs_completed_boundary_trace(model, client, server_logs):
    """关键词模型返回非法 JSON 时，服务端日志保留已选切点，错误响应不回显文案。"""
    model[1].chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"boundaries_after":[1]}'))]),
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="oops"))]),
    ]
    response = client.post("/segmentations", json=payload("甲乙丙丁。戊己庚辛。", step=500))
    assert response.status_code == 502
    assert response.json() == {"error": {"message": "模型返回非法 JSON。"}}
    assert "stage=keywords" in server_logs.text
    assert "selected_boundaries_after': [1]" in server_logs.text
    assert "甲乙丙丁。" in server_logs.text
    assert "model_elapsed_ms" in server_logs.text
    assert "oops" not in response.text


def test_english_protection_preserves_model_cut(model):
    """英文串只在原文连续范围内受保护，不吞掉标点两侧的模型切点。"""
    model[1].chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))])
        for data in [{"boundaries_after": [1]}, {"keywords": [["Hello"], ["world"]]}]
    ]
    result = segment(payload("Hello， world。", step=400))
    assert [s["text"] for s in result["segments"]] == ["Hello， ", "world。"]


def test_long_text_without_candidates_stays_whole(model):
    """无中文标点的长文保持整段，不再按时长或空格拆分。"""
    result = segment(payload("Hello world", step=900))
    assert [s["text"] for s in result["segments"]] == ["Hello world"]
    assert result["segments"][0]["end_time"] == 9.9
    assert result["warnings"] == []


def test_retrieval_keywords_have_no_global_quota(model):
    """多段关键词可重复或留空；第二次模型输入与最终片段一致，不验证真实模型语义。"""
    texts = ["散养的土鸡。", "蛋黄很饱满。", "蛋清很透亮。", "五谷杂粮喂养。", "土鸡在觅食。", "土鸡正在散步。", "到手很新鲜。"]
    keywords = ["散养的土鸡", "蛋黄", "蛋清", "五谷杂粮", "土鸡", "土鸡", ""]
    model[1].chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))])
        for data in [
            {"boundaries_after": list(range(1, len(texts)))},
            {"keywords": [[word] if word else [] for word in keywords]},
        ]
    ]
    result = segment(payload("".join(texts), step=400))
    request = model[1].chat.completions.create.call_args.kwargs
    assert json.loads(request["messages"][1]["content"]) == texts
    assert [s["text"] for s in result["segments"]] == texts
    assert [s["keyword"] for s in result["segments"]] == keywords
    assert [s["level"] for s in result["segments"]] == [2 if word else 1 for word in keywords]
    assert result["trace"]["keyword_rejected_count"] == 0


def test_keyword_validation_keeps_first_valid_word(model):
    """单个 keyword 只保留段内最靠前的有效词；过滤不存在、重复、空白和超长候选并区分全半角。"""
    model[1].chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))])
        for data in [
            {"boundaries_after": []},
            {"keywords": [["甲", "ＡＢ", "AB", "Ａ", "ＡＢ", "", " ", "ＡＢ甲乙丙丁戊己庚辛壬癸子", "乙"]]},
        ]
    ]
    result = segment(payload("ＡＢ甲乙丙丁戊己庚辛壬癸子。", step=100))
    assert result["segments"][0]["keyword"] == "ＡＢ"
    assert result["trace"]["keyword_rejected_count"] == 8


@pytest.mark.parametrize("length", [12, 13])
def test_fixed_keyword_length_boundary(model, length):
    """固定词长边界：12 字原文词保留，13 字原文词丢弃且不截断。"""
    script = "甲乙丙丁戊己庚辛壬癸子丑寅"
    model[1].chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))])
        for data in [{"boundaries_after": []}, {"keywords": [[script[:length]]]}]
    ]
    result = segment(payload(script))
    assert result["segments"][0]["keyword"] == (script[:length] if length == 12 else "")
    assert result["trace"]["keyword_rejected_count"] == (0 if length == 12 else 1)


def test_timeline_outside_repair(model):
    """局部插字只修复所在块，保留块外时间范围。"""
    result = segment(payload("甲乙丙丁戊己。庚辛壬癸。", "甲乙丁戊己。庚辛壬癸。", step=800))
    assert result["trace"]["repair_block_count"] == 1
    assert result["segments"][0]["start_time"] == 0
    assert result["segments"][-1]["end_time"] == 8.0  # 末字继承原 ASR 时间，尾部标点不参与对齐


@pytest.mark.parametrize(
    "key,value",
    [
        ("IMV_LLM_BASE_URL", "https://["),
    ],
)
def test_invalid_configuration(model, monkeypatch, key, value):
    """非法模型地址在创建 SDK 前被拒绝。"""
    monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError):
        segment(payload("甲乙丙丁"))
    model[0].assert_not_called()


def test_dotenv_configuration_and_environment_priority(model, client, monkeypatch, tmp_path):
    """真实 .env 提供模型配置，环境覆盖文件；每次调用重读文件且不修改进程环境。"""
    for key in ("BASE_URL", "API_KEY", "MODEL"):
        monkeypatch.delenv("IMV_LLM_" + key)
    env_file = tmp_path / "server/.env"
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
    ("LLM_API_KEY", ""), ("LLM_TIMEOUT_SECONDS", "nan"),
    ("LLM_MAX_RETRIES", "4"), ("LLM_TIMEOUT_SECONDS", "invalid-secret"),
])
def test_settings_validation_returns_safe_error(model, client, monkeypatch, key, value):
    """缺失内容、非法类型及越界配置返回固定 502，不泄露配置值，也不创建 SDK。"""
    monkeypatch.setenv("IMV_" + key, value)
    response = client.post("/segmentations", json=payload("甲乙丙丁"))
    assert response.status_code == 502
    assert response.json() == {"error": {"message": "模型或切片配置缺失或不合法，请检查 IMV_ 配置。"}}
    model[0].assert_not_called()


@pytest.mark.parametrize("value,allowed", [(None, True), ("false", False)])
def test_settings_boolean_http_authorization(model, client, monkeypatch, value, allowed):
    """代码默认允许远程 HTTP；显式配置 false 时仍可禁用。"""
    monkeypatch.setenv("IMV_LLM_BASE_URL", "http://remote.test/v1")
    if value is not None:
        monkeypatch.setenv("IMV_ALLOW_INSECURE_LLM_HTTP", value)
    response = client.post("/segmentations", json=payload("甲乙丙丁"))
    assert response.status_code == (200 if allowed else 502)
    if not allowed:
        model[0].assert_not_called()


@pytest.mark.parametrize("groups,expected", [
    (["甲乙丙丁", "戊己庚辛壬癸子丑"], [[1, 1], [1, 2], [2, 2]]),
    (["甲乙丙丁戊", "己庚辛壬癸子丑"], [[1, 2], [2, 2], [1, 1]]),
])
def test_group_id_counts_segments_within_asr_sentence(model, groups, expected):
    """验证句内序号及总数；跨 ASR 句的片段归属首字所在句。"""
    model[1].chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))])
        for data in [{"boundaries_after": [1, 2]}, {"keywords": [[], [], []]}]
    ]
    result = segment(grouped_payload("甲乙丙丁。戊己庚辛。壬癸子丑。", groups, step=500))
    assert [s["text"] for s in result["segments"]] == ["甲乙丙丁。", "戊己庚辛。", "壬癸子丑。"]
    assert [s["group_id"] for s in result["segments"]] == expected


def test_api_response_contract(model, client, server_logs):
    """HTTP 响应保留原有统计契约，详细诊断只写入服务端日志。"""
    model[1].chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))])
        for data in [{"boundaries_after": [1]}, {"keywords": [["甲乙"], ["戊己"]]}]
    ]
    response = client.post("/segmentations", json=payload("甲乙。丙丁。戊己庚辛。", step=500))
    assert response.status_code == 200
    assert model[0].return_value.__exit__.call_count == 1
    assert response.json() == {
        "segments": [
            {
                "segment_id": 1,
                "group_id": [1, 2],
                "text": "甲乙。丙丁。",
                "start_time": 0.0,
                "end_time": 2.5,
                "keyword": "甲乙",
                "level": 2,
                "subtitle_parts": [
                    {"text": "甲乙", "start_time": 0.0, "end_time": 1.5},
                    {"text": "丙丁", "start_time": 1.5, "end_time": 2.5},
                ],
            },
            {
                "segment_id": 2,
                "group_id": [2, 2],
                "text": "戊己庚辛。",
                "start_time": 3.0,
                "end_time": 5.0,
                "keyword": "戊己",
                "level": 2,
                "subtitle_parts": [{"text": "戊己庚辛", "start_time": 3.0, "end_time": 5.0}],
            },
        ],
        "warnings": [],
        "trace": {
            "matched_chars": 8,
            "substitution_chars": 0,
            "script_extra_chars": 0,
            "asr_extra_chars": 0,
            "edit_cost": 0,
            "repair_block_count": 0,
            "segment_count": 2,
            "keyword_rejected_count": 0,
        },
    }
    assert "切片成功" in server_logs.text
    assert "'filtered_boundaries': [{'offset': 3, 'reason': 'min_duration_before'}]" in server_logs.text
    assert "'selected_boundaries_after': [1]" in server_logs.text
    assert "'keyword_candidates': [['甲乙'], ['戊己']]" in server_logs.text
    assert "'model_elapsed_ms': {'boundaries':" in server_logs.text


@pytest.mark.parametrize("script,transcript,message", [
    ("", "甲", "文案缺少有效字符。"),
    ("。 ", "甲", "文案缺少有效字符。"),
    ("甲乙", ["", " ，\t"], "ASR 缺少有效发音字符。"),
])
def test_api_invalid_input(model, client, script, transcript, message):
    """空文案、纯标点及无有效 ASR 字符由业务层返回 422，不调用模型。"""
    response = client.post("/segmentations", json=payload(script, transcript))
    assert response.status_code == 422
    assert response.json() == {"error": {"message": message}}
    model[0].assert_not_called()


@pytest.mark.parametrize("data", [None, []])
def test_framework_validation(client, data):
    """缺失或非对象请求体由框架拒绝，返回 422 与 detail 数组。"""
    response = client.post("/segmentations", json=data)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


def test_internal_error(client, monkeypatch):
    """内部约束异常由路由转换为原有的 500 错误格式。"""
    from server.segmentation import router as route

    monkeypatch.setattr(route, "segment", MagicMock(side_effect=AssertionError("片段未完整覆盖文案。")))
    response = client.post("/segmentations", json=payload("甲乙丙丁"))
    assert response.status_code == 500
    assert response.json() == {"error": {"message": "片段未完整覆盖文案。"}}


@pytest.mark.parametrize("data,field,error_type", [
    ({"script": "甲"}, "asr_result", "missing"),
    ({"asr_result": {}}, "script", "missing"),
    ({"script": 1, "asr_result": {}}, "script", "string_type"),
    ({"script": "甲", "asr_result": []}, "asr_result", "dict_type"),
    ({"script": "甲", "asr_result": None}, "asr_result", "dict_type"),
])
def test_required_field_types(model, client, data, field, error_type):
    """Pydantic 在 HTTP 入口拒绝缺失、空值或错误类型，返回字段位置且不调用模型。"""
    response = client.post("/segmentations", json=data)
    assert response.status_code == 422
    assert any(
        error["loc"] == ["body", field] and error["type"] == error_type
        for error in response.json()["detail"]
    )
    model[0].assert_not_called()


def test_extra_fields_and_first_track(model, client):
    """HTTP 允许额外字段，只处理第一音轨，单字文案也能输出完整片段。"""
    data = payload("甲", step=1500)
    data["extra"] = True
    data["asr_result"]["transcripts"].append({"sentences": []})
    response = client.post("/segmentations", json=data)
    assert response.status_code == 200
    assert response.json()["segments"] == [{
        "segment_id": 1, "group_id": [1, 1], "text": "甲",
        "start_time": 0.0, "end_time": 1.5, "keyword": "甲", "level": 2,
        "subtitle_parts": [{"text": "甲", "start_time": 0.0, "end_time": 1.5}],
    }]
    assert len(data["asr_result"]["transcripts"]) == 2


def test_text_exceeding_former_length_limit(model):
    """超过原 20000 字符和词数上限的同文时间轴仍输出完整文本与时间。"""
    script = "甲" * 20001
    result = segment(payload(script, step=0.1))
    assert "".join(s["text"] for s in result["segments"]) == script
    assert result["trace"]["matched_chars"] == len(script)
    assert result["segments"][0]["start_time"] == 0
    assert result["segments"][-1]["end_time"] == 2.0


@pytest.mark.parametrize("durations,expected", [
    ([1999, 2000], ["甲。乙。"]),
    ([2000, 2000], ["甲。", "乙。"]),
    ([2000, 1999], ["甲。乙。"]),
    ([2000, 1000, 1000, 2000], ["甲。", "乙。丙。", "丁。"]),
    ([2000, 2000, 1000], ["甲。", "乙。丙。"]),
    ([500, 500], ["甲。乙。"]),
    ([7000, 2000], ["甲。", "乙。"]),
])
def test_two_second_candidates(model, durations, expected):
    """候选过滤覆盖阈值两侧、连续短句、短尾及短全文；选全部候选时仍完整保留文本与时间。"""
    words, end = [], 0
    for char, duration in zip("甲乙丙丁", durations):
        words.append({"text": char, "begin_time": end, "end_time": end + duration})
        end += duration
    script = "".join(word["text"] + "。" for word in words)

    def respond(**kwargs):
        """验证模型只看到过滤后的完整文案，并选择全部可用切点。"""
        content = json.loads(kwargs["messages"][1]["content"])
        if isinstance(content[0], dict):
            assert content == [{"id": i, "text": text} for i, text in enumerate(expected, 1)]
            output = {"boundaries_after": list(range(1, len(content)))}
        else:
            assert content == expected
            output = {"keywords": [[] for _ in content]}
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(output)))])

    model[1].chat.completions.create.side_effect = respond
    result = segment({"script": script, "asr_result": {"transcripts": [{"sentences": [{"words": words}]}]}})
    assert [s["text"] for s in result["segments"]] == expected
    assert "".join(s["text"] for s in result["segments"]) == script
    assert result["segments"][0]["start_time"] == 0
    assert result["segments"][-1]["end_time"] == end / 1000
    if len(expected) > 1:
        assert all(s["end_time"] - s["start_time"] >= 2 for s in result["segments"])


def test_model_can_skip_candidates_without_postprocessing(model):
    """模型跳过中间候选后可形成超过 6 秒的片段；停顿时间保留，输出切点不被改动。"""
    data = grouped_payload("甲乙丙丁。戊己庚辛。壬癸子丑。", ["甲乙丙丁", "戊己庚辛", "壬癸子丑"], step=1000)
    model[1].chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(output)))])
        for output in [{"boundaries_after": [2]}, {"keywords": [[], []]}]
    ]
    result = segment(data)
    assert [s["text"] for s in result["segments"]] == ["甲乙丙丁。戊己庚辛。", "壬癸子丑。"]
    assert [(s["start_time"], s["end_time"]) for s in result["segments"]] == [(0, 9), (10, 14)]
    assert result["warnings"] == []


def test_candidate_inside_alignment_repair_is_filtered(model):
    """标点位于增删修复块内时隐藏该候选，不移动到句内其他位置。"""
    result = segment(payload("甲乙。丙丁。戊己。", "甲丙丁戊己", step=2000))
    request = model[1].chat.completions.create.call_args_list[0].kwargs
    assert json.loads(request["messages"][1]["content"]) == [
        {"id": 1, "text": "甲乙。丙丁。"}, {"id": 2, "text": "戊己。"},
    ]
    assert [s["text"] for s in result["segments"]] == ["甲乙。丙丁。戊己。"]
    assert result["trace"]["repair_block_count"] == 1


def test_trailing_pause_does_not_make_short_candidate_eligible(model):
    """短句后的长停顿不计入该句时长，不能使不足 2 秒的候选通过。"""
    data = {"script": "甲。乙。", "asr_result": {"transcripts": [{"sentences": [{"words": [
        {"text": "甲", "begin_time": 0, "end_time": 1000},
        {"text": "乙", "begin_time": 5000, "end_time": 7000},
    ]}]}]}}
    result = segment(data)
    request = model[1].chat.completions.create.call_args_list[0].kwargs
    assert json.loads(request["messages"][1]["content"]) == [{"id": 1, "text": "甲。乙。"}]
    assert [s["text"] for s in result["segments"]] == ["甲。乙。"]
    assert result["segments"][0]["end_time"] == 7


def test_client_configuration_is_request_scoped(model, client, monkeypatch):
    """客户端完整配置只作用于本次请求，后续旧请求仍使用服务端值。"""
    config = dict(llm_base_url="https://client.test/v1", llm_api_key="client-secret", llm_model="client-model",
                  llm_timeout_seconds=8.5, llm_max_retries=0)
    response = client.post("/segmentations", json={**payload("甲乙丙丁"), "config": config})
    assert response.status_code == 200
    assert "client-secret" not in response.text
    assert model[0].call_args.kwargs == dict(base_url=config["llm_base_url"], api_key="client-secret", timeout=8.5, max_retries=0)
    assert all(call.kwargs["model"] == "client-model" for call in model[1].chat.completions.create.call_args_list)
    assert client.post("/segmentations", json=payload("甲乙丙丁")).status_code == 200
    assert model[0].call_args.kwargs["api_key"] == "test"
    assert model[1].chat.completions.create.call_args.kwargs["model"] == "test"
    # 客户端配置完整时不要求服务端也配置模型密钥。
    for key in ("BASE_URL", "API_KEY", "MODEL"):
        monkeypatch.delenv("IMV_LLM_" + key)
    assert client.post("/segmentations", json={**payload("甲乙丙丁"), "config": config}).status_code == 200


@pytest.mark.parametrize("change", [
    {"llm_api_key": None}, {"llm_base_url": ""}, {"llm_model": " "},
    {"llm_max_retries": 4}, {"llm_timeout_seconds": 0}, {"allow_insecure_llm_http": True},
])
def test_invalid_client_configuration_never_falls_back(model, client, change):
    """非法客户端配置返回脱敏 422，不回退到已配置的服务端密钥，也不创建 SDK。"""
    config = dict(llm_base_url="https://client.test/v1", llm_api_key="client-secret", llm_model="client-model")
    config.update(change)
    if config["llm_api_key"] is None:
        del config["llm_api_key"]
    response = client.post("/segmentations", json={**payload("甲乙丙丁"), "config": config})
    assert response.status_code == 422
    assert "client-secret" not in response.text
    model[0].assert_not_called()


def test_client_credentials_are_not_echoed_in_top_level_errors(model, client):
    """缺少 script 时框架的完整请求 input 不能把密钥回显出去。"""
    response = client.post("/segmentations", json={"config": {
        "llm_base_url": "https://client.test/v1", "llm_api_key": "client-secret", "llm_model": "client-model",
    }})
    assert response.status_code == 422
    assert "client-secret" not in response.text
    assert all("input" not in error for error in response.json()["detail"])


def test_client_config_keeps_server_http_policy(model, client, monkeypatch):
    """客户端模型参数不能绕过服务端已关闭的远程 HTTP 策略。"""
    monkeypatch.setenv("IMV_ALLOW_INSECURE_LLM_HTTP", "false")
    response = client.post("/segmentations", json={**payload("甲乙丙丁"), "config": {
        "llm_base_url": "http://client.test/v1", "llm_api_key": "client-secret", "llm_model": "client-model",
    }})
    assert response.status_code == 502
    model[0].assert_not_called()
