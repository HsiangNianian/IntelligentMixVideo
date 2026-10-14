"""IMS 与 Remotion 共用字幕纯文本、关键词继承与绝对区间，样式标记由各渲染器添加。"""
from ..segmentation.segmentation import SUBTITLE_PUNCTUATION


def subtitle_events(segments):
    """兼容已保存的 subtitle_parts；每个原切片仅首个合法短句继承关键词。"""
    result = []
    for item in segments:
        used = False
        for part in item.subtitle_parts or [item]:
            # 仅保留两侧紧邻数字的小数点；逗号等标点仍移除，避免把 3.14 显示成 314。
            text = "".join(char for i, char in enumerate(part.text) if char not in SUBTITLE_PUNCTUATION or (
                char in ".．" and 0 < i < len(part.text) - 1
                and part.text[i - 1].isdecimal() and part.text[i + 1].isdecimal()
            )).strip()
            keyword = item.keyword if not used and item.keyword and item.keyword in text else ""
            used = used or bool(keyword)
            if text:
                result.append((text, keyword, part.start_time, part.end_time))
    return result
