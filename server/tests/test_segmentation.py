"""切片核心行为回归，隔离 ASR 与模型；在 server/ 执行 uv run --locked pytest tests/test_segmentation.py -v。"""

import json
import logging
import random
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
from openai import APIConnectionError, APITimeoutError
import pytest

from server.segmentation import segment, segmentation, translate


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
        if isinstance(content[0], str):
            data = {"keywords": [[s[:2]] for s in content]}
        else:
            data = {"boundaries_after": []}
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


@pytest.mark.parametrize("script,expected_lengths", [
    ("甲" * 1, [1]), ("甲" * 8, [8]), ("甲" * 9, [3, 6]),
    ("甲" * 16, [3, 6, 7]), ("甲" * 17, [3, 6, 8]), ("甲" * 20, [3, 6, 6, 5]),
    ("abcdefghi", [9]), ("12345678901234567", [17]),
])
def test_secondary_split_length_and_timing(model, client, script, expected_lengths):
    """中文每段至多八字，超长英文数字保持完整，保留时间覆盖且模型只调用两次。"""
    response = client.post("/segmentations", json=payload(script))
    assert response.status_code == 200
    parts = response.json()["segments"]
    lengths = [len(part["text"]) for part in parts]
    assert lengths == expected_lengths
    assert "".join(part["text"] for part in parts) == script
    assert [(part["start_time"], part["end_time"]) for part in parts] == [
        (sum(lengths[:i]) / 5, sum(lengths[:i + 1]) / 5) for i in range(len(lengths))
    ]
    assert [part["keyword"] for part in parts] == [script[:2], *[""] * (len(parts) - 1)]
    assert model[1].chat.completions.create.call_count == 2


@pytest.mark.parametrize("keyword,expected", [
    ("甲乙", ["甲乙", ""]), ("庚辛", ["", "庚辛"]), ("丁？戊", ["", ""]), ("", ["", ""]),
])
def test_secondary_split_preserves_punctuation_and_keyword(model, keyword, expected):
    """标点切点优先于中点且保留原文，跨切点关键词清空，完整关键词继承，标题词不变。"""
    script = " \t甲乙， 丙丁？戊己庚辛壬癸。 "
    model[1].chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))])
        for data in [{"boundaries_after": []}, {"keywords": [[keyword]], "title_keyword": "测试"}]
    ]
    result = segment({**payload(script), "title": "硬切测试"})
    parts = result["segments"]
    assert [s["text"] for s in parts] == [" \t甲乙， 丙丁？", "戊己庚辛壬癸。 "]
    assert "".join(s["text"] for s in parts) == script
    assert [s["keyword"] for s in parts] == expected
    assert [s["level"] for s in parts] == [2 if word else 1 for word in expected]
    assert [s["segment_id"] for s in parts] == [1, 2]
    assert [s["group_id"] for s in parts] == [[1, 2], [2, 2]]
    assert [(s["start_time"], s["end_time"]) for s in parts] == [(0.4, 1.6), (1.8, 3.0)]
    assert result["title_keyword"] == "测试"
    assert model[1].chat.completions.create.call_count == 2
    request = model[1].chat.completions.create.call_args.kwargs
    assert json.loads(request["messages"][1]["content"]) == {"title": "硬切测试", "segments": [script]}


@pytest.mark.parametrize("script,expected", [
    ("就想做点事情的，70，80，90后滴滴我。", ["就想做点事情的，", "70，80，90后", "滴滴我。"]),
    ("为啥？OpenAI说，一道", ["为啥？", "OpenAI说，", "一道"]),
])
def test_secondary_split_regressions(model, script, expected):
    """复现截图中的数字人群与公司名被硬切，验证完整原文、关键词、字符时间及两次模型调用。"""
    keyword = "OpenAI" if "OpenAI" in script else "70，80，90后"
    model[1].chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(output)))])
        for output in [{"boundaries_after": []}, {"keywords": [[keyword]]}]
    ]
    result = segment(payload(script))
    assert [s["text"] for s in result["segments"]] == expected
    assert "".join(s["text"] for s in result["segments"]) == script
    assert [s["keyword"] for s in result["segments"] if s["keyword"]] == [keyword]
    offset = 0
    for item in result["segments"]:
        indices = [offset + i for i, char in enumerate(item["text"])
                   if not char.isspace() and char not in segmentation.PUNCTUATION]
        assert (item["start_time"], item["end_time"]) == (indices[0] / 5, (indices[-1] + 1) / 5)
        offset += len(item["text"])
    assert model[1].chat.completions.create.call_count == 2


@pytest.mark.parametrize("token", [
    "OpenAI", "GPT-4o", "3.14%", "-3.14%", "ＯｐｅｎＡＩ", "１２３．４５",
    "ChatGPT4o", "123456789", "60，70，80，90后",
    "1、2、3", "1.5、2.5、3.5", "第1、2、3、4、5、6、7、8、9、10名",
    "1,299", "1,000,000", "12,345,678,901",
])
def test_secondary_split_never_breaks_protected_expressions(model, token):
    """半角全角英文、数字、小数、千分位和数字列表不可拆，超长表达单独成段，不携带周围中文。"""
    script = f"这次谈到{token}这个对象"
    parts = segment(payload(script))["segments"]
    assert "".join(s["text"] for s in parts) == script
    assert sum(token in s["text"] for s in parts) == 1
    for item in parts:
        size = sum(not char.isspace() and char not in segmentation.PUNCTUATION for char in item["text"])
        assert size <= 8 or item["text"] == token


def test_numeric_list_is_protected_in_first_split(model):
    """即使每个数字都足够两秒，首次候选也不能拆开共享“后”的数字人群表达。"""
    script = "欢迎，70，80，90后滴滴我。"
    result = segment(payload(script, step=1000))
    request = model[1].chat.completions.create.call_args_list[0].kwargs
    assert json.loads(request["messages"][1]["content"]) == [
        {"id": 1, "text": "欢迎，"}, {"id": 2, "text": "70，80，90后滴滴我。"},
    ]
    assert any("70，80，90后" in s["text"] for s in result["segments"])


@pytest.mark.parametrize("script,expected", [
    ("这款售价1,299元起包邮到家", ["这款售价1,299", "元起包邮到家"]),
    ("今天一共售出了1,000,000件商品", ["今天一共售出了", "1,000,000", "件商品"]),
    ("只要99,3件还包邮送到家", ["只要99,", "3件还包邮送到家"]),
])
def test_secondary_split_keeps_thousands_separators(model, script, expected):
    """半角逗号后恰为三位数字时视为千分位，不因标点优先在数字内部切开；其他半角逗号仍是切点。"""
    assert [part["text"] for part in segment(payload(script))["segments"]] == expected


@pytest.mark.parametrize("prefix,suffix", [
    ("今天只要99，", "3件还包邮送到家。"),
    ("库存只剩500，", "3分钟就抢光了。"),
    ("原价199，", "2件到手只要299元。"),
    ("今天只要99，", "10件还包邮送到家。"),
])
@pytest.mark.parametrize("select_boundary", [False, True])
def test_price_and_quantity_keep_comma_boundary(model, prefix, suffix, select_boundary):
    """价格和下句数量不视为数字列表，首次候选与模型合并后的二次切分都保留句界。"""
    model[1].chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(output)))])
        for output in [
            {"boundaries_after": [1] if select_boundary else []},
            {"keywords": [[], []] if select_boundary else [[]]},
        ]
    ]
    parts = segment(payload(prefix + suffix, step=1000))["segments"]
    request = model[1].chat.completions.create.call_args_list[0].kwargs
    assert json.loads(request["messages"][1]["content"]) == [
        {"id": 1, "text": prefix}, {"id": 2, "text": suffix},
    ]
    assert parts[0]["text"] == prefix
    assert "".join(part["text"] for part in parts[1:]) == suffix


@pytest.mark.parametrize("script,expected", [
    ("再等2，3天就可以拿到了。", ["再等2，3天", "就可以拿到了。"]),
    ("第1、2、3、4、5、6、7、8、9、10名都有奖品。", ["第1、2、3、4、5、6、7、8、9、10名", "都有奖品。"]),
])
def test_contextual_numeric_expressions_stay_together(model, script, expected):
    """等两三天的约数及第几名的序数在两次切分都保持完整，超长序数不留下单字“第”。"""
    parts = segment(payload(script, step=1000))["segments"]
    request = model[1].chat.completions.create.call_args_list[0].kwargs
    assert json.loads(request["messages"][1]["content"]) == [{"id": 1, "text": script}]
    assert [part["text"] for part in parts] == expected


@pytest.mark.parametrize("script,expected", [
    ("嗯，我们今天来聊聊这个产品。", ["嗯，我们今天来", "聊聊这个产品。"]),
    ("我们今天来聊聊这个产品，嗯。", ["我们今天来", "聊聊这个产品，嗯。"]),
    ("用OpenAI做点事", ["用OpenAI", "做点事"]),
    ("用123456789", ["用", "123456789"]),
])
def test_secondary_split_avoids_optional_single_char_parts(model, script, expected):
    """有安全切点时不为标点或词界切出单字，只有硬保护迫使拆分时才允许单字回退。"""
    parts = segment(payload(script))["segments"]
    assert [part["text"] for part in parts] == expected
    if script.startswith("嗯"):
        assert parts[0]["end_time"] == 1.4


def test_long_chinese_word_still_obeys_eight_character_limit(model):
    """中文整词超过八字时仍可拆分；放宽上限只适用于英文数字表达。"""
    script = "中华人民共和国中央人民政府"
    parts = segment(payload(script))["segments"]
    assert "".join(s["text"] for s in parts) == script
    assert all(len(s["text"]) <= 8 for s in parts)


def test_chinese_word_boundaries_precede_midpoint(model):
    """中点落在“不靠”内部时按真实结巴词边界切，保持数学、不靠、灵光、一闪完整。"""
    script = "它不懂数学也不靠灵光一闪解题"
    parts = segment(payload(script))["segments"]
    assert "".join(s["text"] for s in parts) == script
    assert all(len(s["text"]) <= 8 for s in parts)
    for word in ("数学", "不靠", "灵光", "一闪"):
        assert any(word in s["text"] for s in parts)


def test_secondary_split_keeps_asr_groups(model):
    """多个长片段独立二分，保留句间停顿并按首字归属重编 ASR 分组。"""
    groups = ["甲乙丙丁戊己庚辛壬癸子丑。", "寅卯辰巳午未申酉戌亥天地。"]
    model[1].chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(output)))])
        for output in [{"boundaries_after": [1]}, {"keywords": [[], []]}]
    ]
    result = segment(grouped_payload("".join(groups), groups, step=500))
    assert [part["text"] for part in result["segments"]] == [groups[0][:6], groups[0][6:], groups[1][:6], groups[1][6:]]
    assert [part["group_id"] for part in result["segments"]] == [[1, 2], [2, 2], [1, 2], [2, 2]]
    assert [(part["start_time"], part["end_time"]) for part in result["segments"]] == [(0, 3), (3, 6), (7, 10), (10, 13)]
    assert model[1].chat.completions.create.call_count == 2


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
    assert model[1].chat.completions.create.call_count == (6 if failure in ("json", "shape") else 1)


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
    assert [s["text"] for s in result["segments"]] == ["刚才我家人还问我，", "家里不是", "还有鸡蛋吗？", "怎么又买一箱？"]
    assert [(s["start_time"], s["end_time"]) for s in result["segments"]] == [
        (0.16, 1.08), (1.28, 1.72), (1.72, 2.4), (2.52, 3.44),
    ]
    assert "".join(s["text"] for s in result["segments"]) == data["script"]
    assert result["segments"][0]["start_time"] == 0.16
    assert result["segments"][-1]["end_time"] == 3.44
    assert result["trace"]["edit_cost"] == 0
    assert result["warnings"] == []
    # 三个子段归属第一句，末段归属第二句，句间停顿不被填平。
    assert [s["group_id"] for s in result["segments"]] == [[1, 3], [2, 3], [3, 3], [1, 1]]
    assert result["segments"][2]["end_time"] == 2.4
    assert result["segments"][3]["start_time"] == 2.52


def test_model_cuts_keywords_and_protected_runs(model):
    """关键词结构错误反馈模型，五次纠错后按原规则选词，不重复切分。"""
    responses = iter(
        [
            {"boundaries_after": [1, 1]},
            {},
            {"keywords": []},
            {"keywords": [[], [], []]},
            {"keywords": ["甲乙", "QQ"]},
            {"keywords": [[None], []]},
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
    calls = model[1].chat.completions.create.call_args_list
    assert len(calls) == 7
    assert all(call.kwargs["response_format"] == {"type": "json_object"} for call in calls)
    assert calls[1].kwargs["messages"][0]["content"].endswith(json.dumps({"keywords": [[], []]}))
    for previous, retry in zip(calls[1:-1], calls[2:]):
        messages = retry.kwargs["messages"]
        assert messages[:2] == previous.kwargs["messages"][:2]
        assert messages[-2]["role"] == "assistant"
        assert messages[-1]["role"] == "user"
        assert "2 项数组" in messages[-1]["content"]
    assert json.loads(calls[-1].kwargs["messages"][-2]["content"]) == {"keywords": [[None], []]}


@pytest.mark.parametrize("ids", [None, [True], [0], [2]])
def test_invalid_model_boundaries_fail_without_fallback(model, client, ids):
    """非法切点纠错五次仍失败则返回 502，不继续请求关键词，且关闭 SDK。"""
    model[1].chat.completions.create.side_effect = None
    model[1].chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"boundaries_after": ids})))]
    )
    response = client.post("/segmentations", json=payload("甲乙丙丁。戊己庚辛。", step=500))
    assert response.status_code == 502
    assert "boundaries_after" in response.json()["error"]["message"]
    assert model[1].chat.completions.create.call_count == 6
    assert model[0].return_value.__exit__.call_count == 1


def test_keyword_failure_logs_completed_boundary_trace(model, client, server_logs):
    """关键词失败时记录切点和原始回复，错误响应不回显文案。"""
    model[1].chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"boundaries_after":[1]}'))]),
    ] + [SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="oops"))])] * 6
    response = client.post("/segmentations", json=payload("甲乙丙丁。戊己庚辛。", step=500))
    assert response.status_code == 502
    assert response.json() == {"error": {"message": "模型返回非法 JSON。"}}
    assert model[1].chat.completions.create.call_count == 7
    assert "stage=keywords" in server_logs.text
    assert "selected_boundaries_after': [1]" in server_logs.text
    assert "甲乙丙丁。" in server_logs.text
    assert "model_elapsed_ms" in server_logs.text
    assert 'stage=boundaries attempt=1 content=\'{"boundaries_after":[1]}\'' in server_logs.text
    assert all(f"stage=keywords attempt={attempt} content='oops'" in server_logs.text
               for attempt in range(1, 7))
    assert "oops" not in response.text


def test_english_protection_preserves_model_cut(model):
    """英文串只在原文连续范围内受保护，不吞掉标点两侧的模型切点。"""
    model[1].chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))])
        for data in [{"boundaries_after": [1]}, {"keywords": [["Hello"], ["world"]]}]
    ]
    result = segment(payload("Hello， world。", step=400))
    assert [s["text"] for s in result["segments"]] == ["Hello， ", "world。"]


def test_long_text_without_candidates_is_split(model):
    """无中文标点时在完整英文词之间切分，空格仍保留在前段。"""
    model[1].chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))])
        for data in [{"boundaries_after": []}, {"keywords": [["world"]]}]
    ]
    result = segment(payload("Hello world", step=900))
    assert [s["text"] for s in result["segments"]] == ["Hello ", "world"]
    assert result["segments"][-1]["end_time"] == 9.9
    assert [s["keyword"] for s in result["segments"]] == ["", "world"]
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
    prompt = request["messages"][0]["content"]
    assert f"本次恰有 {len(texts)} 个片段" in prompt
    assert prompt.endswith(json.dumps({"keywords": [[] for _ in texts]}))
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
    """提词阶段保留 12 字并拒绝 13 字；超出子段上限的关键词拆开后不继承。"""
    script = "甲乙丙丁戊己庚辛壬癸子丑寅"
    model[1].chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))])
        for data in [{"boundaries_after": []}, {"keywords": [[script[:length]]]}]
    ]
    result = segment(payload(script))
    assert all(s["keyword"] == "" and s["level"] == 1 for s in result["segments"])
    assert model[1].chat.completions.create.call_count == 2
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
            },
            {
                "segment_id": 2,
                "group_id": [2, 2],
                "text": "戊己庚辛。",
                "start_time": 3.0,
                "end_time": 5.0,
                "keyword": "戊己",
                "level": 2,
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


@pytest.mark.parametrize("extra", [{}, {"title": None}, {"title": "业务标题\n保留换行"}, {"title": ""}])
def test_title_optional(client, monkeypatch, extra):
    """标题可省略或为 null，字符串原样传入业务函数，不改变响应。"""
    from server.segmentation import router as route

    data = {**payload("甲乙丙丁"), **extra}
    business = MagicMock(return_value={"segments": []})
    monkeypatch.setattr(route, "segment", business)
    response = client.post("/segmentations", json=data)
    assert response.status_code == 200
    assert response.json() == {"segments": []}
    business.assert_called_once_with({"title": None, **data}, config=None)


@pytest.mark.parametrize("title", [123, []])
def test_title_invalid_type(model, client, title):
    """标题非空且不是字符串时返回 422，不调用模型。"""
    data = payload("甲乙丙丁")
    response = client.post("/segmentations", json={**data, "title": title})
    assert response.status_code == 422
    assert any(error["loc"] == ["body", "title"] for error in response.json()["detail"])
    model[0].assert_not_called()


@pytest.mark.parametrize("title,keyword", [("散养土鸡上新", "土鸡"), ("", "")])
def test_title_keyword_shares_model_call(model, client, title, keyword):
    """标题和片段共用一次提词调用，标题关键词独立返回且不改变片段。"""
    model[1].chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(output)))])
        for output in [{"boundaries_after": []}, {"keywords": [["甲乙"]], "title_keyword": keyword}]
    ]
    response = client.post("/segmentations", json={**payload("甲乙丙丁"), "title": title})
    assert response.status_code == 200
    assert response.json()["title_keyword"] == keyword
    assert response.json()["segments"][0]["keyword"] == "甲乙"
    assert model[1].chat.completions.create.call_count == 2
    request = model[1].chat.completions.create.call_args.kwargs
    assert json.loads(request["messages"][1]["content"]) == {"title": title, "segments": ["甲乙丙丁"]}
    assert request["response_format"] == {"type": "json_object"}
    assert request["messages"][0]["content"].endswith(json.dumps({"keywords": [[]], "title_keyword": ""}))


@pytest.mark.parametrize("extra", [{}, {"title": None}])
def test_no_title_keeps_response(model, client, extra):
    """未提供标题时沿用原模型输入和响应字段，不增加模型调用。"""
    response = client.post("/segmentations", json={**payload("甲乙丙丁"), **extra})
    assert response.status_code == 200
    assert set(response.json()) == {"segments", "warnings", "trace"}
    assert model[1].chat.completions.create.call_count == 2
    request = model[1].chat.completions.create.call_args.kwargs
    assert json.loads(request["messages"][1]["content"]) == ["甲乙丙丁"]


@pytest.mark.parametrize("data,keyword", [
    ({"title": "散养土鸡上新"}, "土鸡"),
    ({"title": "散养土鸡上新", "asr_result": None}, "不在原标题内而且长度超过十二个字的模型结果"),
    ({"title": "散养土鸡上新", "script": "不参与切分的文案", "asr_result": None}, ""),
    ({"title": ""}, ""),
])
def test_title_only_calls_model_once(model, client, data, keyword):
    """ASR 缺省或 null 时只提标题词，不切正文，不校验关键词的长度或原文匹配。"""
    model[1].chat.completions.create.side_effect = None
    model[1].chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"title_keyword": keyword})))])
    response = client.post("/segmentations", json=data)
    assert response.status_code == 200
    assert response.json() == {"title_keyword": keyword}
    model[1].chat.completions.create.assert_called_once()
    assert model[1].chat.completions.create.call_args.kwargs["messages"][1]["content"] == data["title"]
    assert model[0].return_value.__exit__.call_count == 1


@pytest.mark.parametrize("raw,status,expected", [
    ('{}', 200, {"title_keyword": ""}),
    ('{"title_keyword":null}', 200, {"title_keyword": ""}),
    ('{"title_keyword":123}', 200, {"title_keyword": ""}),
    ('{"title_keyword":true}', 200, {"title_keyword": ""}),
    ('{"title_keyword":["土鸡"]}', 200, {"title_keyword": ""}),
    ('{"title_keyword":{"word":"土鸡"}}', 200, {"title_keyword": ""}),
    ('{broken', 502, {"error": {"message": "模型标题提词响应格式无效。"}}),
])
def test_title_only_missing_keyword_and_broken_json(model, client, raw, status, expected):
    """标题词缺失或非字符串时保持空词；无法解析响应时明确失败且关闭客户端，不额外重试。"""
    model[1].chat.completions.create.side_effect = None
    model[1].chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=raw))])
    response = client.post("/segmentations", json={"title": "散养土鸡上新"})
    assert response.status_code == status and response.json() == expected
    model[1].chat.completions.create.assert_called_once()
    assert model[0].return_value.__exit__.call_count == 1


@pytest.mark.parametrize("extra", [{}, {"title_keyword": None}, {"title_keyword": []},
                                    {"title_keyword": "不存在"}, {"title_keyword": "甲乙丙丁戊己庚辛壬癸子丑寅"}])
def test_invalid_title_keyword_fails_without_fallback(model, client, extra):
    """标题关键词纠错五次仍不符合约束时失败，不补值。"""
    model[1].chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(output)))])
        for output in [{"boundaries_after": []}] + [{"keywords": [["甲乙"]], **extra}] * 6
    ]
    response = client.post("/segmentations", json={**payload("甲乙丙丁"), "title": "甲乙丙丁戊己庚辛壬癸子丑寅"})
    assert response.status_code == 502
    assert "title_keyword" in response.json()["error"]["message"]
    assert model[1].chat.completions.create.call_count == 7
    assert model[0].return_value.__exit__.call_count == 1


def test_internal_error(client, monkeypatch):
    """内部约束异常由路由转换为原有的 500 错误格式。"""
    from server.segmentation import router as route

    monkeypatch.setattr(route, "segment", MagicMock(side_effect=AssertionError("片段未完整覆盖文案。")))
    response = client.post("/segmentations", json=payload("甲乙丙丁"))
    assert response.status_code == 500
    assert response.json() == {"error": {"message": "片段未完整覆盖文案。"}}


@pytest.mark.parametrize("data,field,error_type", [
    ({"script": 1, "asr_result": {}}, "script", "string_type"),
    ({"script": "甲", "asr_result": []}, "asr_result", "dict_type"),
])
def test_required_field_types(model, client, data, field, error_type):
    """Pydantic 在 HTTP 入口拒绝错误字段类型，返回字段位置且不调用模型。"""
    response = client.post("/segmentations", json=data)
    assert response.status_code == 422
    assert any(
        error["loc"] == ["body", field] and error["type"] == error_type
        for error in response.json()["detail"]
    )
    model[0].assert_not_called()


@pytest.mark.parametrize("configuration", ["valid", "missing", "invalid"])
@pytest.mark.parametrize("data", [{}, {"script": "甲"}, {"script": "甲", "asr_result": None}, {"asr_result": {}},
                                  {"script": " 。\t", "asr_result": {}}, {"title": "标题", "asr_result": {}}])
def test_incomplete_segmentation_input_precedes_config(model, client, monkeypatch, data, configuration):
    """普通切片输入不完整时优先报 422，即使配置缺失或损坏也不调用模型。"""
    if configuration == "missing":
        for name in ("IMV_LLM_BASE_URL", "IMV_LLM_API_KEY", "IMV_LLM_MODEL"):
            monkeypatch.delenv(name)
    elif configuration == "invalid":
        monkeypatch.setenv("IMV_LLM_TIMEOUT_SECONDS", "invalid")
    assert client.post("/segmentations", json=data).status_code == 422
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
    }]
    assert len(data["asr_result"]["transcripts"]) == 2


def test_text_exceeding_former_length_limit(model):
    """超过原 20000 字符和词数上限的同文时间轴仍输出完整文本与时间。"""
    script = "甲" * 20001
    result = segment(payload(script, step=1))
    assert "".join(s["text"] for s in result["segments"]) == script
    assert result["trace"]["matched_chars"] == len(script)
    assert result["segments"][0]["start_time"] == 0
    assert result["segments"][-1]["end_time"] == 20.001
    assert all(1 <= len(s["text"]) <= 8 for s in result["segments"])
    assert model[1].chat.completions.create.call_count == 2


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
    """script 类型错误时框架的完整请求 input 不能把密钥回显出去。"""
    response = client.post("/segmentations", json={"script": 123, "config": {
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


# 翻译用例的切片结果：时间与 trace 不发给模型，原样保留在返回值里。
SEGMENTED = {
    "title_keyword": "土鸡",
    "segments": [
        {"segment_id": 1, "group_id": [1, 2], "text": "今天上架", "start_time": 0.0, "end_time": 0.8,
         "keyword": "", "level": 1},
        {"segment_id": 2, "group_id": [2, 2], "text": "散养的土鸡。", "start_time": 0.8, "end_time": 2.0,
         "keyword": "散养的土鸡", "level": 2},
    ],
    "warnings": [],
    "trace": {"segment_count": 2},
}


def reply(model, output):
    """让模型替身只返回一次给定 JSON。"""
    model[1].chat.completions.create.side_effect = None
    model[1].chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(output)))])


def test_translate_one_call_keeps_both_keywords(model):
    """片段与标题一次调用翻译；只发送文字与关键词，英文关键词不限长度，原结果不被修改。"""
    snapshot = json.loads(json.dumps(SEGMENTED))
    reply(model, {
        "segments": [{"text_en": "On sale today", "keyword_en": ""},
                     {"text_en": "free-range chickens.", "keyword_en": "free-range chickens"}],
        "title_en": "New free-range chickens", "title_keyword_en": "free-range chickens",
    })
    result = translate(SEGMENTED, title="散养土鸡上新")
    model[1].chat.completions.create.assert_called_once()
    assert model[0].return_value.__exit__.call_count == 1
    assert json.loads(model[1].chat.completions.create.call_args.kwargs["messages"][1]["content"]) == {
        "segments": [{"segment_id": 1, "text": "今天上架", "keyword": ""},
                     {"segment_id": 2, "text": "散养的土鸡。", "keyword": "散养的土鸡"}],
        "title": "散养土鸡上新", "title_keyword": "土鸡",
    }
    assert [(s["text_en"], s["keyword_en"]) for s in result["segments"]] == [
        ("On sale today", ""), ("free-range chickens.", "free-range chickens")]
    assert result["title_en"] == "New free-range chickens"
    assert result["title_keyword_en"] == "free-range chickens"
    assert [{k: v for k, v in s.items() if not k.endswith("_en")} for s in result["segments"]] == snapshot["segments"]
    assert result["trace"] == snapshot["trace"] and SEGMENTED == snapshot


@pytest.mark.parametrize("keyword_en,expected", [
    ("chickens", "chickens"), (" chickens ", "chickens"), ("hens", ""), ("Chickens", ""), ("", ""),
])
def test_translate_segment_keyword_reuses_validation(model, keyword_en, expected):
    """英文片段关键词复用中文校验：去空白后须在本段译文中连续出现，否则置空而不失败。"""
    reply(model, {"segments": [{"text_en": "Today", "keyword_en": ""},
                               {"text_en": "free-range chickens.", "keyword_en": keyword_en}]})
    result = translate(SEGMENTED)
    assert result["segments"][1]["keyword_en"] == expected
    assert "title_en" not in result
    assert "title" not in json.loads(model[1].chat.completions.create.call_args.kwargs["messages"][1]["content"])


def test_translate_ignores_keyword_without_chinese_keyword(model):
    """中文关键词为空的片段和标题，模型多给的英文关键词一律丢弃。"""
    reply(model, {"segments": [{"text_en": "Today", "keyword_en": "Today"},
                               {"text_en": "chickens.", "keyword_en": "chickens"}],
                  "title_en": "New chickens", "title_keyword_en": "chickens"})
    result = translate({**SEGMENTED, "title_keyword": ""}, title="散养土鸡上新")
    assert result["segments"][0]["keyword_en"] == "" and result["title_keyword_en"] == ""


@pytest.mark.parametrize("output,field", [
    ({"segments": [{"text_en": "Today", "keyword_en": ""}]}, "text_en"),
    ({"segments": [{"text_en": "Today", "keyword_en": ""}, {"text_en": None, "keyword_en": ""}]}, "text_en"),
    ({"segments": [{"text_en": "Today", "keyword_en": ""}, {"text_en": "chickens."}]}, "keyword_en"),
    ({"segments": [{"text_en": "a", "keyword_en": ""}, {"text_en": "chickens.", "keyword_en": ""}],
      "title_en": "New chickens", "title_keyword_en": "hens"}, "title_keyword_en"),
    ({"segments": [{"text_en": "a", "keyword_en": ""}, {"text_en": "chickens.", "keyword_en": ""}],
      "title_keyword_en": ""}, "title_en"),
    ({"segments": [{"text_en": "Today", "keyword_en": ""}, {"text_en": " ", "keyword_en": ""}]}, "text_en"),
    ({"segments": [{"text_en": "a", "keyword_en": ""}, {"text_en": "chickens.", "keyword_en": ""}],
      "title_en": "", "title_keyword_en": ""}, "title_en"),
])
def test_translate_invalid_output_fails_without_retry(model, output, field):
    """段数或类型不符、译文为空、英文标题关键词不在英文标题中时直接失败，只调用一次并关闭客户端。"""
    reply(model, output)
    with pytest.raises(RuntimeError, match=field):
        translate(SEGMENTED, title="散养土鸡上新")
    model[1].chat.completions.create.assert_called_once()
    assert model[0].return_value.__exit__.call_count == 1


def test_translate_allows_empty_title_translation_for_empty_title(model):
    """原标题本身为空时，英文标题为空不算错误。"""
    reply(model, {"segments": [{"text_en": "Today", "keyword_en": ""},
                               {"text_en": "chickens.", "keyword_en": ""}],
                  "title_en": "", "title_keyword_en": ""})
    result = translate({**SEGMENTED, "title_keyword": ""}, title="")
    assert result["title_en"] == "" and result["title_keyword_en"] == ""
