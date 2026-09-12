"""Segment Planner：让模型判断语义切点与关键词。

模型只输出分句编号与关键词候选，不产出任何时间数字；
所有时间、偏移与合规性由确定性代码负责。
"""

import json
import logging
from collections.abc import Sequence
from typing import Any

from server.core.errors import LLMError, LLMOutputInvalidError
from server.llm.client import LLMClient
from server.sub_api.segmentation.builder import Clause

logger = logging.getLogger(__name__)

BOUNDARY_PROMPT = """你是短视频口播文案的语义分片器。

你会收到一份按编号排列的文案分句，每个分句以标点结尾。请判断哪些编号之后应该结束当前画面片段。

判断原则：
1. 一个片段尽量对应一个主要画面：一个完整的人物、地点、动作、事件或结论。
2. 话题归纳、转折、结论切换处适合作为边界。
3. 并列的同类内容尽量保留在同一个片段内。
4. 每个片段建议 8~35 个字，但这只是参考，不要为凑长度破坏语义。
5. 只依据给定文案判断，不要引入文案之外的信息。

只输出 JSON，不要解释：
{"boundaries_after": [3, 7]}
数组元素表示"该编号分句之后切一刀"，按升序排列；最后一个分句之后不需要输出。"""

KEYWORD_PROMPT = """你是短视频素材检索的关键词提取器。

你会收到若干编号片段文本。请为每个片段提取 0~5 个关键词，用于素材库召回与字幕高亮。

要求：
1. 关键词必须逐字出现在对应片段文本中，不能改写、不能翻译、不能新增字词。
2. 优先提取人物、物体、动作、地点、数字与核心概念。
3. 不要提取"的、了、是、这个、那个、都、还"等无信息量的虚词。
4. 不要求每段都有关键词；没有合适的就返回空数组。
5. 每个关键词不超过 12 个字，可以是一个字。

只输出 JSON，不要解释：
{"keywords": [["新能源汽车", "增长40%"], []]}
数组长度必须与片段数量一致，按下标顺序对应每个片段。"""



class SegmentPlanner:
    """把语义判断委托给模型，并校验其输出结构。"""

    def __init__(self, client: LLMClient, *, max_provider_retries: int = 1) -> None:
        """初始化分片规划器。

        作用与效果：绑定模型客户端与重试次数，供切点与关键词两次调用复用。
        输入：模型客户端与可重试次数。
        输出：无。
        """
        self.client = client
        self.max_provider_retries = max_provider_retries

    def plan_boundaries(self, clauses: Sequence[Clause]) -> list[int]:
        """请求模型给出分片切点。

        作用与效果：把编号分句交给模型，只保留合法且升序的编号，越界编号直接丢弃。
        输入：分句序列。
        输出：合法的分句编号升序列表。
        """
        listing = "\n".join(f"{item.number}. {item.text}" for item in clauses)
        payload = {
            "clause_count": len(clauses),
            "clauses": listing,
            "reminder": "只返回 boundaries_after 数组，元素范围 1 到 clause_count-1。",
        }
        content = self._complete_json(BOUNDARY_PROMPT, payload, stage="segment_boundary")
        raw = content.get("boundaries_after")
        if not isinstance(raw, list):
            raise LLMOutputInvalidError("模型未返回 boundaries_after 数组。")
        ids = [
            item
            for item in raw
            if isinstance(item, int) and not isinstance(item, bool) and 1 <= item < len(clauses)
        ]
        return sorted(set(ids))

    def plan_keywords(self, texts: Sequence[str]) -> list[list[str]]:
        """请求模型给出每个片段的关键词候选。

        作用与效果：数量不一致时截断或补空，交由校验层按"逐字存在于文本"兜底。
        输入：片段文本序列。
        输出：与片段一一对应的候选关键词。
        """
        payload = {
            "segment_count": len(texts),
            "segments": [{"index": index, "text": text} for index, text in enumerate(texts)],
        }
        content = self._complete_json(KEYWORD_PROMPT, payload, stage="segment_keyword")
        raw = content.get("keywords")
        if not isinstance(raw, list):
            raise LLMOutputInvalidError("模型未返回 keywords 数组。")
        groups: list[list[str]] = []
        for group in raw[: len(texts)]:
            if not isinstance(group, list):
                groups.append([])
                continue
            groups.append([item for item in group if isinstance(item, str) and item.strip()])
        groups.extend([] for _ in range(len(texts) - len(groups)))
        return groups

    def _complete_json(
        self,
        system_prompt: str,
        payload: dict[str, Any],
        *,
        stage: str,
    ) -> dict[str, Any]:
        """调用模型 JSON 接口并对可重试错误退避。

        作用与效果：记录安全诊断，达到重试上限或遇不可重试错误时向上传播。
        输入：系统提示、用户载荷与阶段名。
        输出：解析后的 JSON 对象。
        """
        retry_count = 0
        while True:
            try:
                content = self.client.complete_json(
                    system_prompt=system_prompt,
                    user_prompt=json.dumps(payload, ensure_ascii=False),
                )
                return _extract_json(content)
            except LLMError as exc:
                logger.warning(
                    "segment_plan_llm_attempt_failed",
                    extra={
                        "planner": "segment",
                        "planner_stage": stage,
                        "attempt": retry_count + 1,
                        "error_code": exc.code,
                        "retryable": exc.retryable,
                    },
                )
                if not exc.retryable or retry_count >= self.max_provider_retries:
                    raise
                retry_count += 1


def _extract_json(content: str) -> dict[str, Any]:
    """从模型正文中解析 JSON 对象。

    作用与效果：兼容裸 JSON 与 ```json 代码块两种输出。
    输入：模型返回的正文文本。
    输出：解析后的字典；失败时抛出 `LLMOutputInvalidError`。
    """
    text = content.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        text = text.removeprefix("json").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMOutputInvalidError("模型输出不是合法 JSON。") from exc
    if not isinstance(parsed, dict):
        raise LLMOutputInvalidError("模型输出不是 JSON 对象。")
    return parsed
