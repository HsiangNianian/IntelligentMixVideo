"""切片约束测试：模型管语义，代码管时长。

在 server/ 执行 uv run --locked pytest -v；使用离线样本与替身。
"""

from server.sub_api.segmentation.builder import build_spans, split_clauses
from .support import (
    aligned_sample,
    colon_cuts,
    load_asr_result,
    sample_cuts,
    synthetic_chars,
)

MIN_MS = 1200
MAX_MS = 6000


def test_split_clauses_covers_whole_script() -> None:
    """分句完整覆盖文案，编号从一开始且保留标点。"""
    script = load_asr_result().text or ""
    clauses = split_clauses(script)

    assert "".join((clause.text for clause in clauses)) == script
    assert clauses[0].number == 1
    assert clauses[0].text.endswith("，")


def test_sample_spans_respect_duration_and_continuity() -> None:
    """样本片段满足时长与文本连续性，短句确实被合并。"""
    script, script_chars = aligned_sample()
    outcome = build_spans(
        script_chars,
        len(script),
        sample_cuts(script, script_chars),
        min_duration_ms=MIN_MS,
        max_duration_ms=MAX_MS,
    )

    assert outcome.spans
    for span in outcome.spans:
        assert span.duration_ms >= MIN_MS
        assert span.duration_ms <= MAX_MS
    joined = "".join(
        script[span.start_offset : span.end_offset] for span in outcome.spans
    )
    assert joined == script

    for previous, current in zip(outcome.spans, outcome.spans[1:]):
        assert previous.end_offset == current.start_offset
        assert previous.end_time_ms <= current.start_time_ms

    # 「怎么又买一箱？」只有 0.92s，必须被合并
    assert outcome.merge_count >= 1


def test_long_sentence_is_split_under_duration_ceiling() -> None:
    """长句被拆分，生成片段不超过时长上限。"""
    script, script_chars = aligned_sample()
    outcome = build_spans(
        script_chars,
        len(script),
        colon_cuts(script, script_chars),
        min_duration_ms=MIN_MS,
        max_duration_ms=MAX_MS,
    )

    # 末句 11.12s，必须被切开
    assert outcome.split_count >= 3
    for span in outcome.spans:
        assert span.duration_ms <= MAX_MS


def test_number_and_latin_runs_are_never_split(caplog) -> None:
    """数字和拉丁字串不被拆开，无合法切点时明确记录告警。"""
    text = "销量增长40%，QQ弹弹"
    chars = synthetic_chars(text)
    builder_logger = "server.sub_api.segmentation.builder"
    with caplog.at_level("WARNING", logger=builder_logger):
        spans = build_spans(
            chars, len(text), [], min_duration_ms=1, max_duration_ms=6
        ).spans
    boundaries = {span.start_offset for span in spans}

    # 保护区间内不可切，工具会明确告警而不是静默产出碎片
    assert "segment_split_has_no_valid_cut" in caplog.records[0].message

    assert text.index("%") not in boundaries  # 不能切在 40 | %
    assert text.index("0%") not in boundaries  # 不能切在 4 | 0%
    assert text.index("Q", text.index("QQ") + 1) not in boundaries  # 不能切在 Q | Q


def test_split_prefers_boundary_outside_repair_block() -> None:
    """配置修复块后切点避开块内部，同时满足时长上限。"""
    text = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥"
    chars = synthetic_chars(text, char_ms=200)
    block = (10, 12)  # 修复块内部时间为估算值

    without = build_spans(
        chars, len(text), [], min_duration_ms=100, max_duration_ms=500
    ).spans
    with_block = build_spans(
        chars,
        len(text),
        [],
        min_duration_ms=100,
        max_duration_ms=500,
        repair_block_ranges=[block],
    ).spans
    baseline = {span.start_char for span in without} - {0}
    boundaries = {span.start_char for span in with_block} - {0}

    assert 11 in baseline  # 不传修复块时，切点会落在块内部
    assert 11 not in boundaries  # 传入修复块后，切点被推到块边缘之外
    for span in with_block:
        assert span.duration_ms <= 500
