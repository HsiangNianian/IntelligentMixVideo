"""IMS 与 Remotion 共用字幕纯文本、关键词继承与绝对区间，样式标记由各渲染器添加。"""
from ..segmentation.segmentation import SUBTITLE_PUNCTUATION


def _subtitle_text(text):
    """正文与展示关键词共用过滤规则：保留问号和数字内部的小数点，移除其他标点。"""
    return "".join(char for i, char in enumerate(text) if char not in SUBTITLE_PUNCTUATION or (
        char in ".．" and 0 < i < len(text) - 1
        and text[i - 1].isdecimal() and text[i + 1].isdecimal()
    )).strip()


def _sentence_events(segments):
    """逐条产出（句子序号, 字幕事件）；切片 group_id 的句内序号为 1 时开始新的一句 ASR 句子。

    兼容已保存的 subtitle_parts；每个原切片仅首个合法短句继承关键词。
    """
    sentence = 0
    for item in segments:
        if item.group_id[0] == 1 or sentence == 0:
            sentence += 1
        used = False
        for part in item.subtitle_parts or [item]:
            text = _subtitle_text(part.text)
            # 先确认原词属于此子段，避免清洗后误命中其他子段；不修改素材召回的原关键词。
            keyword = _subtitle_text(item.keyword) if not used and item.keyword and item.keyword in part.text else ""
            used = used or bool(keyword)
            if text:
                yield sentence, (text, keyword, part.start_time, part.end_time)


def subtitle_events(segments):
    """字幕事件 (text, keyword, start, end) 列表，IMS 字幕与单行 Remotion 字效逐条显示。"""
    return [event for _, event in _sentence_events(segments)]


def subtitle_blocks(segments, lines: int):
    """多行字效的字幕分组：同一句里最多 ``lines`` 个连续字幕事件为一组，换句时即使没有填满也开始新的一组。"""
    blocks, last = [], None
    for sentence, event in _sentence_events(segments):
        if sentence != last or len(blocks[-1]) == lines:
            blocks.append([])
        blocks[-1].append(event)
        last = sentence
    return blocks
