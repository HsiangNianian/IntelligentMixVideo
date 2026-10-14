"""IMS 与 Remotion 共用字幕纯文本、关键词继承与绝对区间，样式标记由各渲染器添加。"""
from ..segmentation.segmentation import SUBTITLE_PUNCTUATION


def _subtitle_text(text):
    """正文与展示关键词共用过滤规则：保留问号和数字内部的小数点，移除其他标点。"""
    return "".join(char for i, char in enumerate(text) if char not in SUBTITLE_PUNCTUATION or (
        char in ".．" and 0 < i < len(text) - 1
        and text[i - 1].isdecimal() and text[i + 1].isdecimal()
    )).strip()


def subtitle_events(segments):
    """兼容已保存的 subtitle_parts；每个原切片仅首个合法短句继承关键词。"""
    result = []
    for item in segments:
        used = False
        for part in item.subtitle_parts or [item]:
            text = _subtitle_text(part.text)
            # 先确认原词属于此子段，避免清洗后误命中其他子段；不修改素材召回的原关键词。
            keyword = _subtitle_text(item.keyword) if not used and item.keyword and item.keyword in part.text else ""
            used = used or bool(keyword)
            if text:
                result.append((text, keyword, part.start_time, part.end_time))
    return result
