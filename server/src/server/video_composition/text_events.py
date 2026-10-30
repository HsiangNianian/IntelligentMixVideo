"""IMS 与 Remotion 共用字幕纯文本、关键词继承与绝对区间，样式标记由各渲染器添加。

双语字效（双语字幕）另由 `bilingual_events` 同时给出每条切片的中文与英文，两种语言一起注入同一个字效。
"""
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


def _sentence_events(segments, language="zh"):
    """逐条产出（句子序号, 字幕事件）；切片 group_id 的句内序号为 1 时开始新的一句 ASR 句子。

    兼容已保存的 subtitle_parts；每个原切片仅首个合法短句继承关键词。
    """
    sentence = 0
    for item in segments:
        if item.group_id[0] == 1 or sentence == 0:
            sentence += 1
        if language == "en":
            if not item.text_en or not item.text_en.strip():
                raise ValueError("缺少英文字幕译文")
            text = _subtitle_text(item.text_en, "en")
            keyword = _subtitle_text(item.keyword_en, "en") if item.keyword_en and item.keyword_en in item.text_en else ""
            if text:
                yield sentence, (text, keyword, item.start_time, item.end_time)
            continue
        used = False
        for part in item.subtitle_parts or [item]:
            text = _subtitle_text(part.text)
            # 先确认原词属于此子段，避免清洗后误命中其他子段；不修改素材召回的原关键词。
            keyword = _subtitle_text(item.keyword) if not used and item.keyword and item.keyword in part.text else ""
            used = used or bool(keyword)
            if text:
                yield sentence, (text, keyword, part.start_time, part.end_time)


def subtitle_events(segments, language="zh"):
    """字幕事件 (text, keyword, start, end) 列表，IMS 字幕与单行 Remotion 字效逐条显示。"""
    return [event for _, event in _sentence_events(segments, language)]


def subtitle_blocks(segments, lines: int, max_length: int | None = None, max_span: float | None = None, language="zh"):
    """多行字效的字幕分组：同一句里最多 ``lines`` 个连续字幕事件为一组，换句时即使没有填满也开始新的一组。

    再加一行会让换行连接的文字超过 ``max_length`` 个码点（资产文字参数的上限），或这一行的起点比组首晚
    ``max_span`` 秒以上（行起点参数的上限）时，也开始新的一组，所以单独合规的字幕不会因为分组而失败。
    """
    blocks, last = [], None
    for sentence, event in _sentence_events(segments, language):
        block = blocks[-1] if blocks else []
        if (sentence != last or len(block) == lines
                or max_length is not None and len("\n".join(item[0] for item in block)) + 1 + len(event[0]) > max_length
                or max_span is not None and event[2] - block[0][2] > max_span):
            blocks.append([])
        blocks[-1].append(event)
        last = sentence
    return blocks


def bilingual_events(segments):
    """双语字效的字幕事件 (中文, 中文关键词, 英文, 英文关键词, 开始, 结束)：同一条切片的中英文字幕一起显示。

    一个原切片被拆成多条短句（已保存的 ``subtitle_parts``）时，每条短句配同一条切片的英文译文，英文随中文一起
    换行出现；缺少英文译文按翻译失败终止合成。
    """
    events = []
    for item in segments:
        if not item.text_en or not item.text_en.strip():
            raise ValueError("缺少英文字幕译文")
        english = _subtitle_text(item.text_en, "en")
        english_keyword = _subtitle_text(item.keyword_en, "en") if item.keyword_en and item.keyword_en in item.text_en else ""
        used = False
        for part in item.subtitle_parts or [item]:
            text = _subtitle_text(part.text)
            keyword = _subtitle_text(item.keyword) if not used and item.keyword and item.keyword in part.text else ""
            used = used or bool(keyword)
            if text:
                events.append((text, keyword, english, english_keyword, part.start_time, part.end_time))
    return events
