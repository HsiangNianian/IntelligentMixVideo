"""校验并定位片段关键词。

模型只提供候选词；本模块负责确认候选词确实逐字出现在片段文本中，
并给出可用于字幕高亮的字符区间。
"""

from collections.abc import Sequence

from server.sub_api.segmentation.normalizer import NormalizedText
from server.sub_api.segmentation.schemas import SegmentKeyword


def validate_keywords(
    text: str,
    candidates: Sequence[str],
    *,
    max_count: int,
    max_length: int,
) -> tuple[list[SegmentKeyword], int]:
    """校验候选关键词并生成定位区间。

    作用与效果：丢弃不在文本中、超长或重复的候选；移除被更长关键词完全包含的短词；
    按首次出现位置排序并限制数量。长度不设下限，单字关键词只要确实存在即保留。
    输入：片段文本、候选关键词、数量上限与长度上限。
    输出：通过校验的关键词列表与被丢弃的数量。
    """
    normalized = NormalizedText(text)
    accepted: list[SegmentKeyword] = []
    seen: set[str] = set()
    rejected = 0
    for candidate in candidates:
        keyword = candidate.strip()
        if not keyword or keyword in seen:
            rejected += 1
            continue
        if len(keyword) > max_length:
            rejected += 1
            continue
        span = normalized.find(keyword)
        if span is None:
            rejected += 1
            continue
        seen.add(keyword)
        accepted.append(SegmentKeyword(text=keyword, start=span[0], end=span[1]))

    accepted = [
        keyword
        for keyword in accepted
        if not any(
            keyword.text != other.text and keyword.text in other.text for other in accepted
        )
    ]
    accepted.sort(key=lambda keyword: (keyword.start, keyword.end))
    rejected += max(0, len(accepted) - max_count)
    return accepted[:max_count], rejected
