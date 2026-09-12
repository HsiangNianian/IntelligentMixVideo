"""切片测试共用的样本与替身。"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from server.sub_api.segmentation.aligner import (
    AlignedChar,
    align,
    build_asr_chars,
    build_script_chars,
    project_times,
)
from server.sub_api.segmentation.builder import Clause, offsets_to_cuts, split_clauses
from server.sub_api.segmentation.normalizer import is_alignable, normalize_char
from server.sub_api.segmentation.schemas import AsrResult

FIXTURE = Path(__file__).parent / "fixtures" / "fun_asr_egg_sample.json"
KEYWORD_VOCABULARY = ("土鸡蛋", "鸡蛋", "蛋黄", "五谷杂粮", "小孩子", "农家散养", "散养")


def load_asr_payload() -> dict[str, Any]:
    """读取原始 fun-asr 响应样本。"""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def load_asr_result() -> AsrResult:
    """解析 fun-asr 响应样本。"""
    return AsrResult.model_validate(load_asr_payload())


def broken_script(script: str) -> str:
    """注入同音错字、ASR 多字与 ASR 漏字，用于反例验证。"""
    return (
        script.replace("囤一点", "屯一点")
        .replace("土腥味", "土星味")
        .replace("不吃饲料那些的", "不吃饲料")
        .replace("农家土鸡蛋", "农家散养土鸡蛋")
    )


class StubPlanner:
    """按句尾切分，并按固定词表提取关键词。"""

    def __init__(self, clause_ids: list[int] | None = None) -> None:
        self.clause_ids = clause_ids
        self.keyword_texts: list[str] = []

    def plan_boundaries(self, clauses: Sequence[Clause]) -> list[int]:
        if self.clause_ids is not None:
            return self.clause_ids
        return [item.number for item in clauses if item.text.rstrip().endswith(("。", "？", "！"))]

    def plan_keywords(self, texts: Sequence[str]) -> list[list[str]]:
        self.keyword_texts = list(texts)
        return [[word for word in KEYWORD_VOCABULARY if word in text][:5] for text in texts]


def aligned_sample(asr_result: AsrResult | None = None):
    """返回样本文案及其对齐后的字符时间轴。"""
    result = asr_result or load_asr_result()
    script = result.text or ""
    script_chars = build_script_chars(script)
    asr_chars = build_asr_chars(result)
    ops = align(script_chars, asr_chars)
    project_times(script_chars, asr_chars, ops)
    return script, script_chars


def sample_cuts(script: str, script_chars) -> list[int]:
    """以全部分句结尾作为候选切点。"""
    clauses = split_clauses(script)
    return offsets_to_cuts([clause.end_offset for clause in clauses], script_chars, len(script))


def colon_cuts(script: str, script_chars) -> list[int]:
    """只以句号级标点作为候选切点。"""
    offsets = [index + 1 for index, char in enumerate(script) if char in "。！？；"]
    return offsets_to_cuts(offsets, script_chars, len(script))


def synthetic_chars(text: str, char_ms: int = 3) -> list[AlignedChar]:
    """构造等长发音的字符时间轴，用于约束类测试。"""
    chars: list[AlignedChar] = []
    cursor = 0.0
    for index, char in enumerate(text):
        if not is_alignable(char):
            continue
        chars.append(
            AlignedChar(
                index=index,
                char=char,
                normalized=normalize_char(char),
                begin_time_ms=cursor,
                end_time_ms=cursor + char_ms,
                is_word_start=True,
            )
        )
        cursor += char_ms
    return chars
