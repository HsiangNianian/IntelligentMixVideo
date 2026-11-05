"""切片核心行为回归，隔离 ASR 与模型；在 server/ 执行 uv run --locked pytest tests/test_segmentation.py -v。"""

import json
import logging
import random
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock

import httpx
from openai import APIConnectionError, APITimeoutError
import pytest

from server.segmentation import segment, segmentation, translate
from server.segmentation.punctuation import restore as restore_punctuation


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


def reply_with(data):
    """把模型输出包装成 SDK 回复替身；字符串原样作为回复正文。"""
    content = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


@pytest.fixture
def punctuation_model(monkeypatch):
    """隔离本地 CT-Punc，默认不增加标点；恢复与失败场景显式设置回复。"""
    restore = MagicMock(side_effect=lambda text, *, deadline: text)
    monkeypatch.setattr(segmentation.punctuation, "restore", restore)
    return restore


@pytest.fixture
def model(monkeypatch, punctuation_model):
    """显式设置测试配置，用 SDK 上下文替身返回可回溯关键词。"""
    for key, value in {"BASE_URL": "https://example.test/v1", "API_KEY": "test", "MODEL": "test"}.items():
        monkeypatch.setenv("IMV_LLM_" + key, value)
    client = MagicMock()

    def respond(**kwargs):
        """提词请求每段取前两字，带标题时另取标题前两字；特殊回复由用例设置。"""
        content = json.loads(kwargs["messages"][1]["content"])
        segments = content["segments"] if isinstance(content, dict) else content
        data = {"keywords": {str(s["segment_id"]): s["text"][:2] for s in segments}}
        if isinstance(content, dict):
            data["title_keyword"] = content["title"][:2]
        return reply_with(data)

    client.chat.completions.create.side_effect = respond
    factory = MagicMock()
    factory.return_value.__enter__.return_value = client
    monkeypatch.setattr(segmentation, "OpenAI", factory)
    return factory, client


@pytest.fixture
def server_logs(caplog, request):
    """直接捕获 Uvicorn logger，兼容其他测试关闭日志传播的情况。"""
    logger = logging.getLogger("uvicorn.error")
    propagate = logger.propagate
    logger.propagate = False
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(getattr(request, "param", "INFO"), logger=logger.name):
            yield caplog
    finally:
        logger.removeHandler(caplog.handler)
        logger.propagate = propagate


@pytest.fixture(params=[
    pytest.param(SimpleNamespace(), id="missing-choices"),
    pytest.param(SimpleNamespace(choices=None), id="null-choices"),
    pytest.param(SimpleNamespace(choices=[]), id="empty-choices"),
    pytest.param(SimpleNamespace(choices=[SimpleNamespace()]), id="missing-message"),
    pytest.param(SimpleNamespace(choices=[SimpleNamespace(message=None)]), id="null-message"),
    pytest.param(SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace())]), id="missing-content"),
    pytest.param(SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None))]), id="null-content"),
])
def empty_model_response(request):
    """兼容端点省略或置空回复字段时，仍应按模型空回复处理。"""
    return request.param


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


@pytest.mark.parametrize("limit", [0, -1, True, 2.5, "8", None])
def test_invalid_segment_limit(model, client, limit):
    """HTTP 与内部调用都在模型调用之前拒绝非法上限。"""
    body = {**payload("甲乙丙丁"), "max_segment_chars": limit}
    assert client.post("/segmentations", json=body).status_code == 422
    with pytest.raises(ValueError, match="max_segment_chars"):
        segment(body)
    model[0].assert_not_called()


def test_punctuation_split_keeps_original_text(model):
    """句读处切开，句末标点与空白留在前段；CT-Punc 不增加标点时沿用原边界，关键词与标题词按最终片段一次提取。"""
    script = " \t甲乙， 丙丁？戊己庚辛壬癸。 "
    model[1].chat.completions.create.side_effect = [
        reply_with({"keywords": {"1": "甲乙", "3": "庚辛"}, "title_keyword": "测试"})]
    result = segment({**payload(script), "title": "切分测试"})
    parts = result["segments"]
    assert [s["text"] for s in parts] == [" \t甲乙， ", "丙丁？", "戊己庚辛壬癸。 "]
    assert [s["keyword"] for s in parts] == ["甲乙", "", "庚辛"]
    assert [s["level"] for s in parts] == [2, 1, 2]
    assert [s["segment_id"] for s in parts] == [1, 2, 3]
    assert [s["group_id"] for s in parts] == [[1, 3], [2, 3], [3, 3]]
    assert [(s["start_time"], s["end_time"]) for s in parts] == [(0.4, 0.8), (1.2, 1.6), (1.8, 3.0)]
    assert result["title_keyword"] == "测试"
    assert model[1].chat.completions.create.call_count == 1
    request = model[1].chat.completions.create.call_args.kwargs
    assert json.loads(request["messages"][1]["content"]) == {"title": "切分测试", "segments": [
        {"segment_id": i, "text": text} for i, text in enumerate([" \t甲乙， ", "丙丁？", "戊己庚辛壬癸。 "], 1)]}


def test_whitespace_without_punctuation_does_not_split(model):
    """没有标点时空格、换行均不切；原空白保留，超长仍只告警。"""
    script = "人这辈子\n什么都不是你的 唯有健康\n用Claude Code写"
    result = segment({**payload(script), "max_segment_chars": 20})
    assert [s["text"] for s in result["segments"]] == [script]
    assert model[1].chat.completions.create.call_count == 1


@pytest.mark.parametrize("script,expected", [
    ("第1、2、3名，70，80，90后", ["第1、2、3名，", "70，", "80，", "90后"]),
    ("售价1,299.50元，再等2，3天", ["售价1,299.50元，", "再等2，", "3天"]),
    ("甲（乙）丙《丁》", ["甲（乙）丙《丁》"]),
    ('甲"乙"丙[丁]{戊}', ['甲"乙"丙[丁]{戊}']),
    ("GPT-4o和foo_bar、3.14%折扣", ["GPT-4o和foo_bar、3.14%折扣"]),
    ("甲「乙」丙／丁\\戊", ["甲「乙」丙／丁\\戊"]),
    ("（甲？！）乙……丙；：", ["（甲？！）", "乙……丙；："]),
    ("就算你把心肝、脾、肺、肾，全都挖出来。", ["就算你把心肝、脾、肺、肾，", "全都挖出来。"]),
    ("甲；乙;丙：丁:戊…己...庚——辛-壬", ["甲；乙;丙：丁:戊…己...庚——辛-壬"]),
    ("3.14、１．２、1,234,567.89，结束", ["3.14、１．２、1,234,567.89，", "结束"]),
    ("99,3件，1,2999元", ["99,", "3件，", "1,", "2999元"]),
    ("Dr. Smith met the U.S.A. team. Next!", ["Dr. Smith met the U.S.A. team. ", "Next!"]),
    ("例如 e.g. 和 i.e.、Ph.D. 都保留。结束", ["例如 e.g. 和 i.e.、Ph.D. 都保留。", "结束"]),
    ("甲.乙．丙", ["甲.", "乙．", "丙"]),
    ("访问 example.com。完成", ["访问 example.com。", "完成"]),
    ("甲，（乙。）丙", ["甲，", "（乙。）", "丙"]),
    ("甲。 【《乙！》】 丙", ["甲。 ", "【《乙！》】 ", "丙"]),
    ("他说：“你好！”“再见。”结束", ["他说：“你好！”", "“再见。”", "结束"]),
    ('"Hello!" "World." Next.', ['"Hello!" ', '"World." ', 'Next.']),
    ("It's good. 'Next!' Done.", ["It's good. ", "'Next!' ", "Done."]),
    ("？！甲？！）“乙！！”……", ["？！甲？！）", "“乙！！”……"]),
])
def test_boundary_policy_preserves_text_and_timing(model, punctuation_model, script, expected):
    """句读白名单、数字/缩写保护和开闭符号归属不丢正文、标点或对应字时间。"""
    result = segment(payload(script, step=1000))
    assert [s["text"] for s in result["segments"]] == expected
    assert "".join(expected) == script
    punctuation_model.assert_called_once_with(
        "".join(c for c in script if not segmentation._is_punctuation(c)),
        deadline=ANY,
    )
    positions = [i for i, c in enumerate(script) if not c.isspace() and not segmentation._is_punctuation(c)]
    cursor = 0
    for item in result["segments"]:
        length = len(segmentation._core(item["text"]))
        assert length > 0
        assert (item["start_time"], item["end_time"]) == (positions[cursor], positions[cursor + length - 1] + 1)
        cursor += length


def test_restore_merges_original_marks_before_keyword_extraction(model, punctuation_model, client):
    """整段去标点只推理一次；原问号/引号优先，新增标点参与提词，正文与时间保持不变。"""
    script = "他说：“你好世界！”你好吗？我很好"
    punctuation_model.side_effect = None
    punctuation_model.return_value = "他说，你好，世界。你好吗，我很好。"
    response = client.post("/segmentations", json=payload(script))
    assert response.status_code == 200
    result = response.json()
    texts = ["他说：“你好，", "世界！”", "你好吗？", "我很好。"]
    assert [s["text"] for s in result["segments"]] == texts
    punctuation_model.assert_called_once_with("他说你好世界你好吗我很好", deadline=ANY)
    assert segmentation._core("".join(texts)) == segmentation._core(script)
    assert [(s["start_time"], s["end_time"]) for s in result["segments"]] == [
        (0, 1.2), (1.2, 1.6), (2, 2.6), (2.8, 3.4),
    ]
    model[1].chat.completions.create.assert_called_once()
    assert json.loads(model[1].chat.completions.create.call_args.kwargs["messages"][1]["content"]) == [
        {"segment_id": i, "text": text} for i, text in enumerate(texts, 1)
    ]


@pytest.mark.parametrize("limit", [1, 4, 8, 30])
def test_limit_only_warns_and_never_splits_again(model, punctuation_model, client, limit):
    """容量只控制告警，CT-Punc 未断开的长句不硬切、不调用大模型切分，包括英文长串。"""
    script = "能主动退出消耗自己的局，abcdefghijklmnop"
    response = client.post("/segmentations", json={**payload(script), "max_segment_chars": limit})
    assert response.status_code == 200
    result = response.json()
    assert [s["text"] for s in result["segments"]] == ["能主动退出消耗自己的局，", "abcdefghijklmnop"]
    assert [w["code"] for w in result["warnings"]] == (["segment_over_max_chars"] if limit < 16 else [])
    assert result["segments"][-1]["end_time"] == len(script) / 5
    punctuation_model.assert_called_once()
    model[1].chat.completions.create.assert_called_once()


@pytest.mark.parametrize("restored,expected", [
    ("Open，AI", ["Open，", "AI"]),
    ("甲（乙）丙", ["甲（乙）丙"]),
    ("「甲？！乙……」", ["「甲？！", "乙……」"]),
    ("心肝、脾、肺、肾，下一句。", ["心肝、脾、肺、肾，", "下一句。"]),
    ("3.14%，下一句", ["3.14%，", "下一句"]),
    ("甲。“乙。”", ["甲。", "“乙。”"]),
])
def test_predicted_punctuation_uses_same_boundary_policy(model, punctuation_model, restored, expected):
    """预测标点同样只在句读处切开，顿号/括号/小数点保留，连续标点不产生空段。"""
    punctuation_model.side_effect = None
    punctuation_model.return_value = restored
    script = segmentation._core(restored)
    result = segment(payload(script))
    assert [s["text"] for s in result["segments"]] == expected
    assert "".join(expected) == restored


def test_predicted_boundary_inside_repair_still_splits(model, punctuation_model):
    """ASR 增删修复只负责估算时间，不再阻止预测标点切分。"""
    punctuation_model.side_effect = None
    punctuation_model.return_value = "甲乙，丙丁，戊己。"
    result = segment(payload("甲乙丙丁戊己", "甲丙丁戊己", step=2000))
    assert [s["text"] for s in result["segments"]] == ["甲乙，", "丙丁，", "戊己。"]
    assert [(s["start_time"], s["end_time"]) for s in result["segments"]] == [(0, 2.667), (2.667, 6), (6, 10)]


def test_restoration_preserves_original_whitespace(model, punctuation_model):
    """模型规范化空格不影响原文排版，英文词间空格不自动成为切点。"""
    punctuation_model.side_effect = None
    punctuation_model.return_value = "Hello world，你好。世界。"
    result = segment(payload(" Hello   world\n你好 世界 "))
    assert [s["text"] for s in result["segments"]] == [" Hello   world，\n", "你好。 ", "世界。 "]


def test_restoration_case_change_keeps_original_text(model, punctuation_model, client):
    """回归：CT-Punc 把 EPP 改成 Epp 时不再返回 502，照常补标点切片，片段保留原文大小写。"""
    punctuation_model.side_effect = None
    punctuation_model.return_value = "Epp超净除菌，降农残。"
    response = client.post("/segmentations", json=payload("EPP超净除菌降农残"))
    assert response.status_code == 200
    assert [s["text"] for s in response.json()["segments"]] == ["EPP超净除菌，", "降农残。"]


def test_punctuation_failure_stops_before_keywords(model, punctuation_model, client):
    """本地推理失败时返回错误，不继续提词，也不退回大模型切分。"""
    punctuation_model.side_effect = RuntimeError("CT-Punc 标点恢复失败，请检查模型文件及运行环境。")
    response = client.post("/segmentations", json=payload("甲乙丙丁"))
    assert response.status_code == 502
    assert "CT-Punc" in response.json()["error"]["message"]
    model[0].assert_not_called()


def test_punctuation_time_counts_toward_budget(model, punctuation_model, client, monkeypatch):
    """模型加载、等待和推理计入整次软预算，超时后不再请求关键词。"""
    clock = MagicMock(return_value=0.0)
    monkeypatch.setattr(segmentation, "perf_counter", clock)

    def slow_restore(text, *, deadline):
        """模拟推理用完默认 240 秒预算。"""
        clock.return_value = 240
        return text

    punctuation_model.side_effect = slow_restore
    response = client.post("/segmentations", json=payload("甲乙丙丁"))
    assert response.status_code == 504
    assert response.json() == {"error": {"message": "切片处理超时。"}}
    model[0].assert_not_called()


def test_failed_punctuation_over_budget_returns_504_without_retry(model, client, monkeypatch):
    """真实恢复入口的首次推理耗尽预算并失败时返回 504，释放锁且不再推理或提词。"""
    clock = MagicMock(return_value=0.0)
    monkeypatch.setattr(segmentation, "perf_counter", clock)
    monkeypatch.setattr(segmentation.punctuation, "perf_counter", clock)
    monkeypatch.setattr(segmentation.punctuation, "restore", restore_punctuation)
    engine = MagicMock()

    def slow_failure(*, input):
        """模拟一次超过请求截止时间的失败推理，不等待真实时间。"""
        clock.return_value = 241
        raise RuntimeError("private inference failure")

    engine.generate.side_effect = slow_failure
    monkeypatch.setattr(segmentation.punctuation, "_model", lambda: engine)
    response = client.post("/segmentations", json=payload("甲乙丙丁"))
    assert response.status_code == 504
    assert response.json() == {"error": {"message": "切片处理超时。"}}
    engine.generate.assert_called_once()
    assert not segmentation.punctuation._lock.locked()
    model[0].assert_not_called()


def test_title_only_does_not_load_punctuation(model, punctuation_model):
    """仅标题仍只提词，不要求安装模型文件或加载本地推理引擎。"""
    model[1].chat.completions.create.side_effect = [reply_with({"title_keyword": "标题"})]
    assert segment({"title": "标题"}) == {"title_keyword": "标题"}
    punctuation_model.assert_not_called()


def test_client_cannot_override_punctuation_model(model, punctuation_model, client):
    """标点模型固定为服务端官方版本，请求不能覆盖模型来源。"""
    config = dict(llm_base_url="https://example.test/v1", llm_api_key="test", llm_model="test",
                  punc_model="untrusted/model")
    response = client.post("/segmentations", json={**payload("甲乙"), "config": config})
    assert response.status_code == 422
    punctuation_model.assert_not_called()


def test_punctuation_restore_keeps_asr_groups(model, punctuation_model):
    """整段 CT-Punc 补标点后切分，保留句间停顿并按首字归属计算 ASR 分组。"""
    groups = ["甲乙丙丁戊己庚辛壬癸子丑。", "寅卯辰巳午未申酉戌亥天地。"]
    punctuation_model.side_effect = None
    punctuation_model.return_value = "".join(f"{group[:6]}，{group[6:]}" for group in groups)
    result = segment(grouped_payload("".join(groups), groups, step=500))
    assert [part["text"] for part in result["segments"]] == [groups[0][:6] + "，", groups[0][6:], groups[1][:6] + "，", groups[1][6:]]
    assert [part["group_id"] for part in result["segments"]] == [[1, 2], [2, 2], [1, 2], [2, 2]]
    assert [(part["start_time"], part["end_time"]) for part in result["segments"]] == [(0, 3), (3, 6), (7, 10), (10, 13)]
    assert model[1].chat.completions.create.call_count == 1


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


@pytest.mark.parametrize("failure,status,calls", [
    ("json", 502, 3), ("shape", 502, 3), ("connect", 502, 4), ("timeout", 504, 4),
    ("budget_before", 504, 0), ("budget_keywords", 504, 1),
])
def test_model_failures_close_client(model, client, monkeypatch, failure, status, calls):
    """纠错或请求重试耗尽、预算耗尽按原错误路径结束，返回原状态码且关闭 SDK。"""
    script = "甲乙丙丁"
    if failure.startswith("budget"):
        clock = MagicMock(return_value=0.0)
        monkeypatch.setattr(segmentation, "perf_counter", clock)
        if failure == "budget_before":
            settings = segmentation._settings

            def configure(config):
                clock.return_value = 240.0
                return settings(config)

            monkeypatch.setattr(segmentation, "_settings", configure)
        else:
            default = model[1].chat.completions.create.side_effect

            def respond(**kwargs):
                clock.return_value = 240.0
                return default(**kwargs)

            model[1].chat.completions.create.side_effect = respond
    elif failure in ("json", "shape"):
        model[1].chat.completions.create.side_effect = None
        model[1].chat.completions.create.return_value = reply_with("oops" if failure == "json" else "[]")
    else:
        request = httpx.Request("POST", "https://example.test/v1")
        error = APIConnectionError if failure.endswith("connect") else APITimeoutError
        model[1].chat.completions.create.side_effect = error(request=request)
    response = client.post("/segmentations", json=payload(script))
    assert response.status_code == status
    assert response.json()["error"]["message"]
    assert set(response.json()) == {"error"}
    assert model[0].return_value.__exit__.call_count == (0 if failure == "budget_before" else 1)
    assert model[1].chat.completions.create.call_count == calls
    if failure.startswith("budget"):
        assert response.json() == {"error": {"message": "切片处理超时。"}}
    if failure != "budget_before":
        assert model[0].call_args.kwargs["max_retries"] == 0


def test_model_request_recovers_after_three_retries(model, client, monkeypatch):
    """前三次各超时 30 秒，第四次在 120 秒预算内恢复并返回正确关键词。"""
    clock = MagicMock(return_value=0.0)
    monkeypatch.setattr(segmentation, "perf_counter", clock)
    create = model[1].chat.completions.create
    default = create.side_effect
    request = httpx.Request("POST", "https://example.test/v1")

    def respond(**kwargs):
        """模拟三次超时，随后用正常回复完成提词。"""
        if create.call_count <= 3:
            clock.return_value += 30
            raise APITimeoutError(request=request)
        clock.return_value += 1
        return default(**kwargs)

    create.side_effect = respond
    response = client.post("/segmentations", json=payload("甲乙丙丁"))
    assert response.status_code == 200
    assert response.json()["segments"][0]["keyword"] == "甲乙"
    assert create.call_count == 4


def test_model_retry_stops_at_shared_budget(model, client, monkeypatch):
    """请求重试不重置总预算：单次默认 90 秒，后续超时缩短，240 秒耗尽后不再发出第四次请求。"""
    clock = MagicMock(return_value=0.0)
    monkeypatch.setattr(segmentation, "perf_counter", clock)
    request = httpx.Request("POST", "https://example.test/v1")

    def timeout(**kwargs):
        """首个超时含额外处理耗时，后两次只消耗剩余预算允许的请求时间。"""
        clock.return_value += kwargs["timeout"] + (40 if clock.return_value == 0 else 0)
        raise APITimeoutError(request=request)

    model[1].chat.completions.create.side_effect = timeout
    response = client.post("/segmentations", json=payload("甲乙丙丁"))
    assert response.status_code == 504
    assert response.json() == {"error": {"message": "切片处理超时。"}}
    assert [call.kwargs["timeout"] for call in model[1].chat.completions.create.call_args_list] == [90, 90, 20]
    assert model[0].return_value.__exit__.call_count == 1


def test_real_fun_asr_excerpt(model, punctuation_model):
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
    punctuation_model.side_effect = None
    punctuation_model.return_value = "刚才我家人还问我，家里不是，还有鸡蛋吗？怎么又买一箱？"
    result = segment(data)
    assert json.dumps(data, ensure_ascii=False) == original
    assert [s["text"] for s in result["segments"]] == ["刚才我家人还问我，", "家里不是，", "还有鸡蛋吗？", "怎么又买一箱？"]
    assert [(s["start_time"], s["end_time"]) for s in result["segments"]] == [
        (0.16, 1.08), (1.28, 1.72), (1.72, 2.4), (2.52, 3.44),
    ]
    assert "".join(s["text"] for s in result["segments"]) == punctuation_model.return_value
    assert result["segments"][0]["start_time"] == 0.16
    assert result["segments"][-1]["end_time"] == 3.44
    assert result["trace"]["edit_cost"] == 0
    assert result["warnings"] == []
    # 三个子段归属第一句，末段归属第二句，句间停顿不被填平。
    assert [s["group_id"] for s in result["segments"]] == [[1, 3], [2, 3], [3, 3], [1, 1]]
    assert result["segments"][2]["end_time"] == 2.4
    assert result["segments"][3]["start_time"] == 2.52


@pytest.mark.parametrize("invalid", [{}, {"keywords": []}, {"keywords": {"3": "QQ"}},
                                     {"keywords": {"01": "甲乙"}}, {"keywords": {"1": None}}])
@pytest.mark.parametrize("timeout,budget", [(120, 120), (5, 120), (120, 10)])
def test_keywords_retry_respects_timeout_and_budget(model, monkeypatch, invalid, timeout, budget):
    """第三次提词成功，单次默认最多 90 秒；纠错共享服务端配置的预算，不放宽更短的配置。"""
    monkeypatch.setenv("IMV_LLM_TIMEOUT_SECONDS", str(timeout))
    monkeypatch.setenv("IMV_SEGMENTATION_BUDGET_SECONDS", str(budget))
    clock = MagicMock(return_value=0.0)
    monkeypatch.setattr(segmentation, "perf_counter", clock)
    responses = iter([invalid, invalid, {"keywords": {"2": "QQ", "1": "甲乙"}}])

    def respond(**kwargs):
        clock.return_value += 2
        return reply_with(next(responses))

    model[1].chat.completions.create.side_effect = respond
    result = segment(payload("甲乙丙丁。QQ增长40%。", step=500))
    assert [s["text"] for s in result["segments"]] == ["甲乙丙丁。", "QQ增长40%。"]
    assert [s["keyword"] for s in result["segments"]] == ["甲乙", "QQ"]
    assert result["trace"]["keyword_rejected_count"] == 0
    calls = model[1].chat.completions.create.call_args_list
    assert len(calls) == 3
    assert [call.kwargs["timeout"] for call in calls] == [min(timeout, 90, budget - spent) for spent in (0, 2, 4)]
    assert model[0].call_args.kwargs["max_retries"] == 0
    assert all(call.kwargs["response_format"] == {"type": "json_object"} for call in calls)
    assert json.loads(calls[0].kwargs["messages"][1]["content"]) == [
        {"segment_id": 1, "text": "甲乙丙丁。"}, {"segment_id": 2, "text": "QQ增长40%。"},
    ]
    for previous, retry in zip(calls[:-1], calls[1:]):
        messages = retry.kwargs["messages"]
        assert messages[:2] == previous.kwargs["messages"][:2]
        assert messages[-2]["role"] == "assistant"
        assert messages[-1]["role"] == "user"
        assert "编号须为 1～2" in messages[-1]["content"]
    assert json.loads(calls[-1].kwargs["messages"][-2]["content"]) == invalid


def test_empty_model_response_retries_successfully(model, client, empty_model_response):
    """提词缺少回复字段时进入纠错，下一次合法回复仍能生成完整结果。"""
    model[1].chat.completions.create.side_effect = [empty_model_response, reply_with({"keywords": {"1": "甲乙"}})]
    response = client.post("/segmentations", json=payload("甲乙丙丁"))
    assert response.status_code == 200
    assert [(s["text"], s["keyword"]) for s in response.json()["segments"]] == [("甲乙丙丁", "甲乙")]
    calls = model[1].chat.completions.create.call_args_list
    assert len(calls) == 2
    retry = calls[1].kwargs["messages"]
    # 空回复不回填为空 assistant 消息，只追加纠错说明。
    assert [message["role"] for message in retry] == ["system", "user", "user"]
    assert "模型返回空内容。" in retry[-1]["content"]
    assert model[0].return_value.__exit__.call_count == 1


@pytest.mark.parametrize("stage,attempts,message", [
    ("keywords", 3, "模型返回空内容。"),
    ("title", 1, "模型标题提词响应格式无效。"),
])
def test_empty_model_response_returns_502(model, client, empty_model_response, stage, attempts, message):
    """持续缺少回复字段时按原尝试次数返回 502，仅标题不纠错，SDK 均关闭。"""
    model[1].chat.completions.create.side_effect = [empty_model_response] * attempts
    data = {"title": "甲乙丙丁"} if stage == "title" else payload("甲乙丙丁")
    response = client.post("/segmentations", json=data)
    assert response.status_code == 502
    assert response.json() == {"error": {"message": message}}
    assert model[1].chat.completions.create.call_count == attempts
    assert model[0].return_value.__exit__.call_count == 1


@pytest.mark.parametrize("raw, message, shape", [
    ("oops", "模型返回非法 JSON。", {}),
    ('{"keywords":[["甲乙"]]}', "模型 keywords 必须为片段编号到字符串的对象，编号须为 1～2 的十进制字符串，不能带前导零；无关键词可省略该编号。", {"keywords_type": "list", "keyword_group_count": 1}),
    ('{"keywords":["甲乙",[]]}', "模型 keywords 必须为片段编号到字符串的对象，编号须为 1～2 的十进制字符串，不能带前导零；无关键词可省略该编号。", {"keywords_type": "list", "keyword_group_count": 2}),
    ('{"keywords":[[1],[]]}', "模型 keywords 必须为片段编号到字符串的对象，编号须为 1～2 的十进制字符串，不能带前导零；无关键词可省略该编号。", {"keywords_type": "list", "keyword_group_count": 2}),
    ('{"keywords":{"3":"甲乙"}}', "模型 keywords 必须为片段编号到字符串的对象，编号须为 1～2 的十进制字符串，不能带前导零；无关键词可省略该编号。", {"keywords_type": "dict", "keyword_group_count": 1}),
    ('{"keywords":null}', "模型 keywords 必须为片段编号到字符串的对象，编号须为 1～2 的十进制字符串，不能带前导零；无关键词可省略该编号。", {"keywords_type": "NoneType", "keyword_group_count": None}),
])
@pytest.mark.parametrize("server_logs", ["INFO", "DEBUG"], indirect=True)
def test_keyword_failure_logs_completed_split_trace(model, client, server_logs, raw, message, shape):
    """提词三次失败后记录最后回复与已完成的切分诊断，不残留前次类型信息、不回显到接口。"""
    model[1].chat.completions.create.side_effect = [reply_with('{"keywords":[]}')] + [reply_with(raw)] * 2
    response = client.post("/segmentations", json=payload("甲乙丙丁。戊己庚辛。", step=500))
    assert response.status_code == 502
    assert response.json() == {"error": {"message": message}}
    assert "stage=keywords" in server_logs.text
    assert "'segment_texts': ['甲乙丙丁。', '戊己庚辛。']" in server_logs.text
    assert "model_elapsed_ms" in server_logs.text
    failure = next(record for record in server_logs.records if record.msg.startswith("切片失败"))
    logged_response = raw if raw == "oops" else json.loads(raw)
    assert failure.args[-1]["keyword_response"] == {"expected_segment_count": 2, "response": logged_response, **shape}
    assert model[1].chat.completions.create.call_count == 3
    assert "stage=keywords attempt=1" in server_logs.text
    outputs = [record for record in server_logs.records if record.msg.startswith("切片模型输出")]
    if logging.getLogger("uvicorn.error").isEnabledFor(logging.DEBUG):
        assert len(outputs) == 3
        assert all(record.levelno == logging.DEBUG for record in outputs)
        assert [record.args[-1] for record in outputs[-2:]] == [logged_response, logged_response]
    else:
        assert outputs == []
    assert raw not in response.text


@pytest.mark.parametrize("server_logs", ["INFO", "DEBUG"], indirect=True)
def test_keyword_failure_response_is_saved_in_task_error_files(model, server_logs):
    """真实提词异常经过合成日志链路落盘，完整回复与计数可查，模型返回的凭证字段仍脱敏。"""
    from server.video_composition import execution_log

    model[1].chat.completions.create.side_effect = [
        reply_with('{"keywords":[["甲乙"]],"api_key":"hidden-model-value"}')] * 3
    with pytest.raises(RuntimeError, match="模型 keywords") as caught:
        segment(payload("甲乙丙丁。戊己庚辛。", step=500))
    record = {"task_id": "keyword-failure", "stage": "segmentation", "status": "processing"}
    execution_log.write_log(record, "step_failed", {
        "step": "segmentation", **execution_log.exception_details(caught.value),
    }, lambda: [])
    folder = execution_log.LOG_ROOT / "video_composition" / record["task_id"]
    entry, = json.loads((folder / "segmentation.json").read_text())["error_log"]
    detail = (folder / entry["error"]["detail_log_path"]).read_text()
    error = json.loads(detail)["error"]
    assert "exceptions" not in entry["error"]
    assert error["exceptions"][0]["keyword_response"] == {
        "expected_segment_count": 2, "keywords_type": "list", "keyword_group_count": 1,
        "response": {"keywords": [["甲乙"]], "api_key": "[REDACTED]"},
    }
    assert "hidden-model-value" not in detail
    assert "hidden-model-value" not in server_logs.text
    assert "[REDACTED]" in server_logs.text
    assert "hidden-model-value" in model[1].chat.completions.create.call_args.kwargs["messages"][-2]["content"]


def test_original_english_punctuation_splits(model):
    """英文内容同样按原文的句读标点切开。"""
    model[1].chat.completions.create.side_effect = [reply_with({"keywords": {"1": "Hello", "2": "world"}})]
    result = segment(payload("Hello， world。", step=400))
    assert [s["text"] for s in result["segments"]] == ["Hello， ", "world。"]


def test_english_predicted_boundary_preserves_original_space(model, punctuation_model):
    """CT-Punc 预测英文词间的标点时切分，原空格仍保留在前段。"""
    punctuation_model.side_effect = None
    punctuation_model.return_value = "Hello， world。"
    result = segment(payload("Hello world", step=900))
    assert [s["text"] for s in result["segments"]] == ["Hello， ", "world。"]
    assert result["segments"][-1]["end_time"] == 9.9
    assert [s["keyword"] for s in result["segments"]] == ["He", "wo"]
    assert result["warnings"] == []


def test_retrieval_keywords_have_no_global_quota(model):
    """按 ID 填回乱序关键词，缺失的中间片段留空，后面的词不移位且允许重复对象。"""
    texts = ["散养的土鸡。", "蛋黄很饱满。", "蛋清很透亮。", "五谷杂粮喂养。", "土鸡在觅食。", "土鸡正在散步。", "到手很新鲜。"]
    keywords = ["散养的土鸡", "", "蛋清", "五谷杂粮", "土鸡", "土鸡", ""]
    model[1].chat.completions.create.side_effect = [
        reply_with({"keywords": {str(i): word for i, word in reversed(list(enumerate(keywords, 1))) if word}})]
    result = segment(payload("".join(texts), step=400))
    request = model[1].chat.completions.create.call_args.kwargs
    assert json.loads(request["messages"][1]["content"]) == [
        {"segment_id": i, "text": text} for i, text in enumerate(texts, 1)
    ]
    assert [s["text"] for s in result["segments"]] == texts
    assert [s["keyword"] for s in result["segments"]] == keywords
    assert [s["level"] for s in result["segments"]] == [2 if word else 1 for word in keywords]
    assert result["trace"]["keyword_rejected_count"] == 0


@pytest.mark.parametrize("word,expected", [("ＡＢ", "ＡＢ"), ("AB", ""), ("不存在", ""), ("", ""),
                                         (" ", ""), ("ＡＢ甲乙丙丁戊己庚辛壬癸子", "")])
def test_keyword_validation_keeps_first_valid_word(model, word, expected):
    """按 ID 返回的关键词仍校验原文、全半角、空白与长度，不合法时置空。"""
    model[1].chat.completions.create.side_effect = [reply_with({"keywords": {"1": word}})]
    result = segment({**payload("ＡＢ甲乙丙丁戊己庚辛壬癸子。", step=100), "max_segment_chars": 20})
    assert result["segments"][0]["keyword"] == expected
    assert result["trace"]["keyword_rejected_count"] == (0 if expected else 1)


@pytest.mark.parametrize("length", [12, 13])
def test_fixed_keyword_length_boundary(model, length):
    """提词阶段保留 12 字并拒绝 13 字。"""
    script = "甲乙丙丁戊己庚辛壬癸子丑寅"
    model[1].chat.completions.create.side_effect = [reply_with({"keywords": {"1": script[:length]}})]
    result = segment({**payload(script), "max_segment_chars": 20})
    assert [(s["keyword"], s["level"]) for s in result["segments"]] == [
        (script[:12], 2) if length == 12 else ("", 1)]
    assert model[1].chat.completions.create.call_count == 1
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
        "timeout": 2.5, "max_retries": 0,
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
    ("SEGMENTATION_MODEL_TIMEOUT_SECONDS", "0"), ("SEGMENTATION_BUDGET_SECONDS", "nan"),
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
    model[1].chat.completions.create.side_effect = [reply_with({"keywords": {}})]
    result = segment(grouped_payload("甲乙丙丁。戊己庚辛。壬癸子丑。", groups, step=500))
    assert [s["text"] for s in result["segments"]] == ["甲乙丙丁。", "戊己庚辛。", "壬癸子丑。"]
    assert [s["group_id"] for s in result["segments"]] == expected


@pytest.mark.parametrize("server_logs", ["INFO", "DEBUG"], indirect=True)
def test_api_response_contract(model, client, server_logs):
    """HTTP 契约不变，INFO 只有摘要，DEBUG 详情与原始回复均脱敏。"""
    model[1].chat.completions.create.side_effect = [
        reply_with({"keywords": {"1": "甲乙", "3": "戊己"}, "api_key": "hidden-model-value"})]
    response = client.post("/segmentations", json=payload("甲乙。丙丁。戊己庚辛。", step=500))
    assert response.status_code == 200
    assert model[0].return_value.__exit__.call_count == 1
    assert response.json() == {
        "segments": [
            {
                "segment_id": 1,
                "group_id": [1, 3],
                "text": "甲乙。",
                "start_time": 0.0,
                "end_time": 1.0,
                "keyword": "甲乙",
                "level": 2,
            },
            {
                "segment_id": 2,
                "group_id": [2, 3],
                "text": "丙丁。",
                "start_time": 1.5,
                "end_time": 2.5,
                "keyword": "",
                "level": 1,
            },
            {
                "segment_id": 3,
                "group_id": [3, 3],
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
            "segment_count": 3,
            "keyword_rejected_count": 0,
        },
    }
    assert "切片成功" in server_logs.text
    assert "elapsed_ms=" in server_logs.text and "segment_count=3" in server_logs.text
    assert "hidden-model-value" not in server_logs.text
    info = "\n".join(record.getMessage() for record in server_logs.records if record.levelno >= logging.INFO)
    assert "甲乙" not in info and "trace=" not in info and "content=" not in info
    if logging.getLogger("uvicorn.error").isEnabledFor(logging.DEBUG):
        assert "'punctuation_piece_count': 3" in server_logs.text
        assert "'segment_texts': ['甲乙。', '丙丁。', '戊己庚辛。']" in server_logs.text
        assert "'keyword_candidates': {'1': '甲乙', '3': '戊己'}" in server_logs.text
        assert "'model_elapsed_ms': {'punctuation':" in server_logs.text
        assert "'keywords':" in server_logs.text
        assert "[REDACTED]" in server_logs.text
    else:
        assert "切片详情" not in server_logs.text and "切片模型输出" not in server_logs.text


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
    business.assert_called_once_with({"title": None, "max_segment_chars": 8, **data}, config=None)


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
    model[1].chat.completions.create.side_effect = [reply_with({"keywords": {"1": "甲乙"}, "title_keyword": keyword})]
    response = client.post("/segmentations", json={**payload("甲乙丙丁"), "title": title})
    assert response.status_code == 200
    assert response.json()["title_keyword"] == keyword
    assert response.json()["segments"][0]["keyword"] == "甲乙"
    assert model[1].chat.completions.create.call_count == 1
    request = model[1].chat.completions.create.call_args.kwargs
    assert json.loads(request["messages"][1]["content"]) == {"title": title, "segments": [{"segment_id": 1, "text": "甲乙丙丁"}]}
    assert request["response_format"] == {"type": "json_object"}
    assert request["messages"][0]["content"].endswith(
        json.dumps({"keywords": {"1": "原文关键词"}, "title_keyword": "标题关键词"}, ensure_ascii=False))


@pytest.mark.parametrize("extra", [{}, {"title": None}])
def test_no_title_keeps_response(model, client, extra):
    """未提供标题时沿用原模型输入和响应字段，不增加模型调用。"""
    response = client.post("/segmentations", json={**payload("甲乙丙丁"), **extra})
    assert response.status_code == 200
    assert set(response.json()) == {"segments", "warnings", "trace"}
    assert model[1].chat.completions.create.call_count == 1
    request = model[1].chat.completions.create.call_args.kwargs
    assert json.loads(request["messages"][1]["content"]) == [{"segment_id": 1, "text": "甲乙丙丁"}]


@pytest.mark.parametrize("data,keyword", [
    ({"title": "散养土鸡上新"}, "土鸡"),
    ({"title": "散养土鸡上新", "asr_result": None}, "不在原标题内而且长度超过十二个字的模型结果"),
    ({"title": "散养土鸡上新", "script": "不参与切分的文案", "asr_result": None}, ""),
    ({"title": ""}, ""),
])
def test_title_only_calls_model_once(model, client, server_logs, data, keyword):
    """ASR 缺省或 null 时只提标题词，不切正文，不校验关键词的长度或原文匹配。"""
    model[1].chat.completions.create.side_effect = None
    model[1].chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"title_keyword": keyword})))])
    response = client.post("/segmentations", json=data)
    assert response.status_code == 200
    assert response.json() == {"title_keyword": keyword}
    assert "切片成功" in server_logs.text and "切片模型输出" not in server_logs.text
    assert "散养土鸡" not in server_logs.text
    model[1].chat.completions.create.assert_called_once()
    assert model[1].chat.completions.create.call_args.kwargs["messages"][1]["content"] == data["title"]
    assert model[0].call_args.kwargs["max_retries"] == 0
    assert model[1].chat.completions.create.call_args.kwargs["timeout"] == 90
    assert model[0].return_value.__exit__.call_count == 1


@pytest.mark.parametrize("raw,status,expected", [
    ('{}', 200, {"title_keyword": ""}),
    ('{"title_keyword":null}', 200, {"title_keyword": ""}),
    ('{"title_keyword":123}', 200, {"title_keyword": ""}),
    ('{"title_keyword":true}', 200, {"title_keyword": ""}),
    ('{"title_keyword":["土鸡"]}', 200, {"title_keyword": ""}),
    ('{"title_keyword":{"word":"土鸡"}}', 200, {"title_keyword": ""}),
    ('{broken', 502, {"error": {"message": "模型标题提词响应格式无效。"}}),
    ('{"title_keyword":"土鸡","api_key":"hidden-model-value"}', 200, {"title_keyword": "土鸡"}),
    ('{"api_key":"hidden-model-value",broken', 502, {"error": {"message": "模型标题提词响应格式无效。"}}),
])
@pytest.mark.parametrize("server_logs", ["INFO", "DEBUG"], indirect=True)
def test_title_only_missing_keyword_and_broken_json(model, client, server_logs, raw, status, expected):
    """标题词缺失或非字符串时保持空词；无法解析响应时明确失败且关闭客户端，不额外重试。"""
    model[1].chat.completions.create.side_effect = None
    model[1].chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=raw))])
    response = client.post("/segmentations", json={"title": "散养土鸡上新"})
    assert response.status_code == status and response.json() == expected
    model[1].chat.completions.create.assert_called_once()
    assert model[0].return_value.__exit__.call_count == 1
    assert "hidden-model-value" not in server_logs.text
    if status == 502:
        failure = next(record for record in server_logs.records if record.msg.startswith("切片失败"))
        assert failure.args[-1]["model_response"] == raw.replace('"hidden-model-value"', '[REDACTED]')
    elif not logging.getLogger("uvicorn.error").isEnabledFor(logging.DEBUG):
        assert "content=" not in server_logs.text and "trace=" not in server_logs.text
    if logging.getLogger("uvicorn.error").isEnabledFor(logging.DEBUG):
        assert "切片模型输出 stage=title_keywords" in server_logs.text
        if "hidden-model-value" in raw:
            assert "[REDACTED]" in server_logs.text


@pytest.mark.parametrize("extra", [{}, {"title_keyword": None}, {"title_keyword": []},
                                    {"title_keyword": "不存在"}, {"title_keyword": "甲乙丙丁戊己庚辛壬癸子丑寅"}])
def test_invalid_title_keyword_fails_without_fallback(model, client, extra):
    """标题关键词总共尝试三次仍不符合约束时失败，不补值。"""
    model[1].chat.completions.create.side_effect = [reply_with({"keywords": {"1": "甲乙"}, **extra})] * 3
    response = client.post("/segmentations", json={**payload("甲乙丙丁"), "title": "甲乙丙丁戊己庚辛壬癸子丑寅"})
    assert response.status_code == 502
    assert "title_keyword" in response.json()["error"]["message"]
    assert model[1].chat.completions.create.call_count == 3
    assert model[0].return_value.__exit__.call_count == 1


def test_title_keyword_retry_uses_corrected_reply(model, client):
    """标题关键词首次不在标题内时反馈错误，纠正后返回该标题词，片段关键词照常填回。"""
    default = model[1].chat.completions.create.side_effect
    invalid = json.dumps({"keywords": {"1": "甲乙"}, "title_keyword": "不存在"}, ensure_ascii=False)
    replies = iter([lambda **_: reply_with(invalid), default])
    model[1].chat.completions.create.side_effect = lambda **kwargs: next(replies)(**kwargs)
    response = client.post("/segmentations", json={**payload("甲乙丙丁"), "title": "散养土鸡上新"})
    assert response.status_code == 200
    assert response.json()["title_keyword"] == "散养"
    assert response.json()["segments"][0]["keyword"] == "甲乙"
    retry = model[1].chat.completions.create.call_args.kwargs["messages"]
    assert retry[-2] == {"role": "assistant", "content": invalid}
    assert "title_keyword" in retry[-1]["content"]
    assert model[1].chat.completions.create.call_count == 2


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
    """超长无标点文案只做一次标点恢复，不硬切，完整保留并告警。"""
    script = "甲" * 20001
    result = segment(payload(script, step=1))
    assert "".join(s["text"] for s in result["segments"]) == script
    assert result["trace"]["matched_chars"] == len(script)
    assert result["segments"][0]["start_time"] == 0
    assert result["segments"][-1]["end_time"] == 20.001
    assert len(result["segments"]) == 1
    assert [w["code"] for w in result["warnings"]] == ["segment_over_max_chars"]
    assert model[1].chat.completions.create.call_count == 1


@pytest.mark.parametrize("times", [
    [(0, 1999), (1999, 3999)],
    [(0, 500), (500, 1000)],
    [(0, 2000), (2000, 3000), (3000, 4000), (4000, 6000)],
    [(0, 1000), (5000, 7000)],
])
def test_short_punctuation_pieces_stay_separate(model, times):
    """句读标点处一律切开，不再按两秒合并短句；停顿保留在片段之间，也不请求切分。"""
    words = [{"text": char, "begin_time": begin, "end_time": end} for char, (begin, end) in zip("甲乙丙丁", times)]
    script = "".join(word["text"] + "。" for word in words)
    result = segment({"script": script, "asr_result": {"transcripts": [{"sentences": [{"words": words}]}]}})
    assert [s["text"] for s in result["segments"]] == [word["text"] + "。" for word in words]
    assert [(s["start_time"], s["end_time"]) for s in result["segments"]] == [(b / 1000, e / 1000) for b, e in times]
    assert model[1].chat.completions.create.call_count == 1


def test_original_punctuation_inside_alignment_repair_still_splits(model):
    """原标点即使位于增删修复块内也切开，使用修复后单调的字级时间。"""
    result = segment(payload("甲乙。丙丁。戊己。", "甲丙丁戊己", step=2000))
    assert [s["text"] for s in result["segments"]] == ["甲乙。", "丙丁。", "戊己。"]
    assert [(s["start_time"], s["end_time"]) for s in result["segments"]] == [(0, 2.667), (2.667, 6), (6, 10)]
    assert result["trace"]["repair_block_count"] == 1


def test_client_configuration_is_request_scoped(model, client, monkeypatch):
    """客户端完整配置只作用于本次请求，后续旧请求仍使用服务端值。"""
    config = dict(llm_base_url="https://client.test/v1", llm_api_key="client-secret", llm_model="client-model",
                  llm_timeout_seconds=8.5, llm_max_retries=3)
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


def test_server_env_sets_segmentation_timeouts_for_client_config(model, punctuation_model, client, monkeypatch):
    """单次上限与总预算只读服务端 IMV_ 配置：带客户端配置的请求也按它限时，CT-Punc 截止时间同源。"""
    monkeypatch.setenv("IMV_SEGMENTATION_MODEL_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("IMV_SEGMENTATION_BUDGET_SECONDS", "9")
    monkeypatch.setattr(segmentation, "perf_counter", MagicMock(return_value=0.0))
    config = dict(llm_base_url="https://client.test/v1", llm_api_key="client-secret", llm_model="client-model")
    response = client.post("/segmentations", json={**payload("甲乙丙丁"), "config": config})
    assert response.status_code == 200
    assert punctuation_model.call_args.kwargs["deadline"] == 9
    assert model[0].call_args.kwargs["timeout"] == 7
    assert model[1].chat.completions.create.call_args.kwargs["timeout"] == 7


@pytest.mark.parametrize("change", [
    {"llm_api_key": None}, {"llm_base_url": ""}, {"llm_model": " "},
    {"llm_max_retries": 4}, {"llm_timeout_seconds": 0}, {"allow_insecure_llm_http": True},
    {"segmentation_model_timeout_seconds": 600}, {"segmentation_budget_seconds": 600},
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


def test_translate_empty_model_response_fails_without_retry(model, empty_model_response):
    """翻译复用安全回复读取，缺少字段抛模型错误而非 AttributeError，仍只调用一次。"""
    model[1].chat.completions.create.side_effect = [empty_model_response]
    with pytest.raises(RuntimeError, match="模型返回空内容。"):
        translate(SEGMENTED)
    model[1].chat.completions.create.assert_called_once()
    assert model[0].return_value.__exit__.call_count == 1


def test_translate_allows_empty_title_translation_for_empty_title(model):
    """原标题本身为空时，英文标题为空不算错误。"""
    reply(model, {"segments": [{"text_en": "Today", "keyword_en": ""},
                               {"text_en": "chickens.", "keyword_en": ""}],
                  "title_en": "", "title_keyword_en": ""})
    result = translate({**SEGMENTED, "title_keyword": ""}, title="")
    assert result["title_en"] == "" and result["title_keyword_en"] == ""


def test_disable_thinking_is_sent_only_when_enabled(model, monkeypatch):
    """开启 IMV_LLM_DISABLE_THINKING 后提词、标题提词与翻译都附带 thinking=disabled；默认不发送该字段。"""
    segment(payload("甲乙丙丁戊己庚辛壬"))
    assert all("extra_body" not in call.kwargs for call in model[1].chat.completions.create.call_args_list)
    monkeypatch.setenv("IMV_LLM_DISABLE_THINKING", "true")
    model[1].chat.completions.create.reset_mock()
    segment(payload("甲乙丙丁戊己庚辛壬"))
    reply(model, {"title_keyword": ""})
    segment({"title": "散养土鸡上新"})
    reply(model, {"segments": [{"text_en": "Today", "keyword_en": ""}, {"text_en": "chickens.", "keyword_en": ""}]})
    translate(SEGMENTED)
    calls = model[1].chat.completions.create.call_args_list
    assert len(calls) == 3
    assert all(call.kwargs["extra_body"] == {"thinking": {"type": "disabled"}} for call in calls)
