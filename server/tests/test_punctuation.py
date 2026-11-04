"""FunASR CT-Punc 加载、复用与失败恢复测试，默认隔离下载和推理；执行 uv run --locked pytest tests/test_punctuation.py。"""

import asyncio
import os
from threading import Event
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.segmentation import punctuation


# 显式开启真实官方模型测试；常规测试不导入 FunASR、不下载权重。
REAL_MODEL = os.environ.get("IMV_TEST_CT_PUNC", "") == "1"
SCRIPT = (
    "不是赢了才叫做厉害，能主动退出消耗自己的局，才是真正的强大。谁影响我，我就放弃谁，谁贬低我，我就离开谁。"
    "但凡能够做到这点的人，人生里面百分之九十九的烦恼全都迎刃而解。我们没有办法选择跟谁相遇，但是我们可以选择跟谁同行啊，"
    "不要把解释当做一种习惯，懂你的人，他不需要你掏心掏肺，不懂你的人，就算你把心肝脾肺肾，全都挖出来，人家也觉得你在演戏。"
    "成年人心照不宣的默契！就是猫有猫道，鼠有鼠道，管好自己，莫渡他人。"
)


@pytest.fixture
def engine(monkeypatch):
    """用官方 API 替身隔离磁盘缓存和联网，清理跨用例实例缓存。"""
    punctuation._model.cache_clear()
    factory = MagicMock()
    factory.return_value.generate.return_value = [{"text": "你好，世界。"}]
    monkeypatch.setitem(sys.modules, "funasr", SimpleNamespace(AutoModel=factory))
    monkeypatch.setattr(punctuation, "find_spec", lambda _: object())
    yield factory
    punctuation._model.cache_clear()


def test_official_model_is_reused(engine):
    """固定官方模型版本且重复请求只初始化一次，每次完整正文传给 generate。"""
    assert punctuation.restore("你好世界") == "你好，世界。"
    assert punctuation.restore("你好世界") == "你好，世界。"
    engine.assert_called_once_with(
        model="ct-punc", model_revision="v2.0.4", ncpu=2,
        disable_update=True, disable_pbar=True, check_latest=False,
    )
    assert engine.return_value.generate.call_count == 2
    engine.return_value.generate.assert_called_with(input="你好世界")


def test_startup_warmup_reuses_instance_for_requests(engine):
    """启动预热完成后，第一条业务请求只推理，不再次初始化或下载模型。"""
    punctuation.initialize()
    assert punctuation.restore("你好世界") == "你好，世界。"
    engine.assert_called_once()
    assert engine.return_value.generate.call_count == 2


def test_startup_without_optional_dependency_skips_warmup(engine, monkeypatch):
    """不启用标点依赖的基础服务仍可启动，且不会尝试下载。"""
    monkeypatch.setattr(punctuation, "find_spec", lambda _: None)
    punctuation.initialize()
    engine.assert_not_called()


@pytest.mark.parametrize("failure", ["load", "infer"])
def test_warmup_failure_propagates(engine, failure):
    """模型下载/加载或试推理失败必须向启动流程报告，不能假装就绪。"""
    target = engine if failure == "load" else engine.return_value.generate
    target.side_effect = RuntimeError("private model error")
    with pytest.raises(RuntimeError, match="CT-Punc 标点恢复失败"):
        punctuation.initialize()


@pytest.mark.parametrize("failure", [False, True])
def test_lifespan_waits_for_model_before_tasks_and_readiness(monkeypatch, failure):
    """阻塞预热期间不恢复任务、不宣告就绪；失败时仍关闭调度器和数据库。"""
    import server.app as app_module

    entered, release = Event(), Event()
    order = []

    def warmup():
        """通过有界事件模拟下载，允许主协程检查服务尚未就绪。"""
        order.append("warmup")
        entered.set()
        if not release.wait(5):
            raise TimeoutError("测试未释放模型预热")
        if failure:
            raise RuntimeError("model failed")

    runtime = SimpleNamespace(
        start=AsyncMock(side_effect=lambda: order.append("tasks")),
        close=AsyncMock(side_effect=lambda: order.append("close_tasks")),
    )
    monkeypatch.setattr(app_module, "Runtime", lambda: runtime)
    monkeypatch.setattr(app_module, "initialize_database", lambda: order.append("database"))
    monkeypatch.setattr(app_module, "close_database", lambda: order.append("close_database"))
    monkeypatch.setattr(app_module, "initialize_punctuation", warmup)
    monkeypatch.setattr(app_module, "remotion_templates_app", SimpleNamespace(state=SimpleNamespace()))

    async def serve():
        """lifespan 完成预热后才允许应用进入就绪阶段。"""
        async with app_module.lifespan(app_module.app):
            order.append("ready")

    async def exercise():
        """等待工作线程进入预热，并验证启动顺序及异常清理。"""
        task = asyncio.create_task(serve())
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            runtime.start.assert_not_awaited()
            assert not task.done()
        finally:
            release.set()
        if failure:
            with pytest.raises(RuntimeError, match="model failed"):
                await task
        else:
            await task

    asyncio.run(exercise())
    assert order == ["database", "warmup", *([] if failure else ["tasks", "ready"]),
                     "close_tasks", "close_database"]


def test_missing_optional_dependency_has_installation_hint(monkeypatch):
    """未安装服务器标点依赖时给出安装命令，不暴露内部导入信息，也不做无意义的重试。"""
    punctuation._model.cache_clear()
    monkeypatch.setitem(sys.modules, "funasr", None)
    with pytest.raises(RuntimeError, match="--extra ct-punc"):
        punctuation.restore("你好世界")
    assert punctuation._model.cache_info().currsize == 0
    assert punctuation._model.cache_info().misses == 1


@pytest.mark.parametrize("result", [None, [], {}, [{"text": ""}], [{"text": 1}], [{}], ["你好"],
                                        [{"text": "甲"}, {"text": "乙"}]])
def test_invalid_official_response_is_rejected(engine, result):
    """缺失、错误类型或多条结果不能悄悄截断正文。"""
    engine.return_value.generate.return_value = result
    with pytest.raises(RuntimeError, match="CT-Punc 标点恢复失败"):
        punctuation.restore("你好世界")


@pytest.mark.parametrize("failure", ["load", "infer"])
def test_engine_failure_is_safe_and_next_request_can_recover(engine, failure):
    """持续的下载/加载或推理错误重试耗尽后不泄露内部信息；失败加载不缓存，下一次请求可重试。"""
    target = engine if failure == "load" else engine.return_value.generate
    target.side_effect = RuntimeError("private path and implementation detail")
    with pytest.raises(RuntimeError, match="CT-Punc 标点恢复失败") as caught:
        punctuation.restore("你好世界")
    assert "private" not in str(caught.value)
    assert "private" in str(caught.value.__cause__)
    assert target.call_count == punctuation.MAX_ATTEMPTS
    target.side_effect = None
    assert punctuation.restore("你好世界") == "你好，世界。"
    assert engine.call_count == (punctuation.MAX_ATTEMPTS + 1 if failure == "load" else 1)


@pytest.mark.parametrize("deadline", [None, 120])
@pytest.mark.parametrize("failure", ["load", "infer", "format"])
def test_transient_failure_retries_within_same_call(engine, monkeypatch, failure, deadline):
    """偶发的加载、推理异常或无效回复只出现一次时，同一次调用内重试成功，不向切片报错。"""
    monkeypatch.setattr(punctuation, "perf_counter", lambda: 0)
    if failure == "load":
        engine.side_effect = [RuntimeError("transient"), engine.return_value]
    elif failure == "infer":
        engine.return_value.generate.side_effect = [RuntimeError("transient"), [{"text": "你好，世界。"}]]
    else:
        engine.return_value.generate.side_effect = [[], [{"text": "你好，世界。"}]]
    assert punctuation.restore("你好世界", deadline=deadline) == "你好，世界。"
    assert engine.call_count == (2 if failure == "load" else 1)
    assert engine.return_value.generate.call_count == (1 if failure == "load" else 2)


@pytest.mark.parametrize("acquired", [False, True])
def test_lock_wait_expiry_skips_model(engine, monkeypatch, acquired):
    """等锁到期或刚拿锁时已到期都不加载模型；只释放实际拿到的锁。"""
    clock = MagicMock(return_value=5.0)
    monkeypatch.setattr(punctuation, "perf_counter", clock)
    lock = MagicMock()

    def acquire(*, timeout):
        """模拟有限等锁后到达截止时间，包括刚好拿到锁的竞态。"""
        assert timeout == 115
        clock.return_value = 120
        return acquired

    lock.acquire.side_effect = acquire
    monkeypatch.setattr(punctuation, "_lock", lock)
    with pytest.raises(TimeoutError, match="切片处理超时"):
        punctuation.restore("你好世界", deadline=120)
    engine.assert_not_called()
    assert lock.release.call_count == int(acquired)


def test_expired_request_does_not_acquire_lock(engine, monkeypatch):
    """进入恢复前预算已耗尽时不等锁，也不加载模型。"""
    monkeypatch.setattr(punctuation, "perf_counter", lambda: 120)
    lock = MagicMock()
    monkeypatch.setattr(punctuation, "_lock", lock)
    with pytest.raises(TimeoutError, match="切片处理超时"):
        punctuation.restore("你好世界", deadline=120)
    lock.acquire.assert_not_called()
    lock.release.assert_not_called()
    engine.assert_not_called()


@pytest.mark.parametrize("stage", ["load", "infer"])
@pytest.mark.parametrize("failed", [False, True])
def test_model_work_exhausting_budget_stops_and_releases_lock(engine, monkeypatch, stage, failed):
    """加载或推理完成/失败时预算耗尽：不开始下次推理、不包装超时，并始终释放锁。"""
    clock = MagicMock(return_value=0.0)
    monkeypatch.setattr(punctuation, "perf_counter", clock)

    def slow_work(**kwargs):
        """以假时钟模拟同步模型工作耗尽预算。"""
        clock.return_value = 120
        if failed:
            raise RuntimeError("private model failure")
        return engine.return_value if stage == "load" else [{"text": "你好，世界。"}]

    target = engine if stage == "load" else engine.return_value.generate
    target.side_effect = slow_work
    with pytest.raises(TimeoutError, match="切片处理超时"):
        punctuation.restore("你好世界", deadline=120)
    engine.assert_called_once()
    assert engine.return_value.generate.call_count == int(stage == "infer")
    assert not punctuation._lock.locked()
    # 新请求有新的预算，前次超时不能把进程内锁留住。
    target.side_effect = None
    assert punctuation.restore("你好世界", deadline=240) == "你好，世界。"


@pytest.mark.skipif(not REAL_MODEL, reason="设置 IMV_TEST_CT_PUNC=1 才下载并运行真实官方模型")
def test_real_ct_punc_pipeline_preserves_script_and_timeline(monkeypatch):
    """用户完整文案跑真实 CT-Punc、合并与对齐；仅关键词接口替身，ASR 时间为合成测试数据。"""
    from server.segmentation import segmentation
    from server.segmentation.settings import ClientSettings

    factory = MagicMock()
    factory.return_value.__enter__.return_value.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"keywords":{}}'))],
    )
    monkeypatch.setattr(segmentation, "OpenAI", factory)
    body = segmentation._core(SCRIPT)
    data = {"script": SCRIPT, "asr_result": {"transcripts": [{"sentences": [{"words": [
        {"text": c, "begin_time": i * 200, "end_time": (i + 1) * 200} for i, c in enumerate(body)
    ]}]}]}}
    config = ClientSettings(llm_base_url="https://example.test/v1", llm_api_key="test", llm_model="test")
    punctuation._model.cache_clear()
    try:
        punctuation.initialize()
        assert punctuation._model.cache_info().misses == 1
        result = segmentation.segment(data, config=config)
        assert segmentation.segment(data, config=config) == result
        assert punctuation._model.cache_info().misses == 1
        merged = "".join(s["text"] for s in result["segments"])
        assert segmentation._core(merged) == body
        # 新文本仅可插入标点，原文全部字符（含原标点）按顺序保留。
        cursor = iter(merged)
        assert all(any(candidate == char for candidate in cursor) for char in SCRIPT)
        assert len(result["segments"]) > 1
        assert result["segments"][0]["start_time"] == 0
        assert result["segments"][-1]["end_time"] == len(body) / 5
        assert result["trace"]["edit_cost"] == 0
        assert [w["code"] for w in result["warnings"]] == ["segment_over_max_chars"]
        assert factory.return_value.__enter__.return_value.chat.completions.create.call_count == 2
    finally:
        punctuation._model.cache_clear()
