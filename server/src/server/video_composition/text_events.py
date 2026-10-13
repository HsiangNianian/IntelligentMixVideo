"""IMS 与 Remotion 共用字幕纯文本、关键词继承与绝对区间，样式标记由各渲染器添加。"""
from ..segmentation.segmentation import SUBTITLE_PUNCTUATION


def subtitle_events(segments):
    """兼容已保存的 subtitle_parts；每个原切片仅首个合法短句继承关键词。"""
    result = []
    for item in segments:
        used = False
        for part in item.subtitle_parts or [item]:
            text = "".join(char for char in part.text if char not in SUBTITLE_PUNCTUATION).strip()
            keyword = item.keyword if not used and item.keyword and item.keyword in text else ""
            used = used or bool(keyword)
            if text:
                result.append((text, keyword, part.start_time, part.end_time))
    return result
