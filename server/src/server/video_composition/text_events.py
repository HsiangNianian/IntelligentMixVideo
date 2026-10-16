"""IMS 与 Remotion 共用字幕纯文本、关键词继承与绝对区间，样式标记由各渲染器添加。"""
from ..segmentation.segmentation import SUBTITLE_PUNCTUATION


def _subtitle_text(text, language="zh"):
    """正文与展示关键词共用过滤规则：保留问号和数字内部的小数点；英文额外保留词内撇号。"""
    return "".join(char for i, char in enumerate(text) if char not in SUBTITLE_PUNCTUATION or (
        char in ".．" and 0 < i < len(text) - 1
        and text[i - 1].isdecimal() and text[i + 1].isdecimal()
    ) or (
        language == "en" and char in "'’" and 0 < i < len(text) - 1
        and text[i - 1].isalnum() and text[i + 1].isalnum()
    )).strip()


def subtitle_events(segments, language="zh"):
    """兼容已保存的 subtitle_parts；每个原切片仅首个合法短句继承关键词。"""
    result = []
    for item in segments:
        if language == "en":
            if not item.text_en or not item.text_en.strip():
                raise ValueError("缺少英文字幕译文")
            text = _subtitle_text(item.text_en, "en")
            keyword = _subtitle_text(item.keyword_en, "en") if item.keyword_en and item.keyword_en in item.text_en else ""
            if text:
                result.append((text, keyword, item.start_time, item.end_time))
            continue
        used = False
        for part in item.subtitle_parts or [item]:
            text = _subtitle_text(part.text)
            # 先确认原词属于此子段，避免清洗后误命中其他子段；不修改素材召回的原关键词。
            keyword = _subtitle_text(item.keyword) if not used and item.keyword and item.keyword in part.text else ""
            used = used or bool(keyword)
            if text:
                result.append((text, keyword, part.start_time, part.end_time))
    return result
