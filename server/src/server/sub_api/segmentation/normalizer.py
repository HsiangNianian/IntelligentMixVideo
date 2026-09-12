import unicodedata

PUNCTUATION_CHARS = frozenset(
    "，。！？、；：\u201c\u201d\u2018\u2019（）《》〈〉【】〔〕…—～·"
    ",.!?;:\"'()<>[]{}~`"
)


def is_alignable(char: str) -> bool:
    """判断字符是否参与时间对齐。

    作用与效果：空白与标点没有独立发音时间，不参与序列对齐。
    输入：`char` 为单个字符。
    输出：需要对齐时为 `True`。
    """
    return not char.isspace() and char not in PUNCTUATION_CHARS


def normalize_char(char: str) -> str:
    """归一化单个字符用于比较。

    作用与效果：执行 NFKC 兼容分解并折叠大小写，消除全角、半角与字母大小写差异。
    输入：`char` 为单个字符。
    输出：归一化后的字符，可能为空串或多个字符。
    """
    return unicodedata.normalize("NFKC", char).lower()


class NormalizedText:
    """原文及其归一化形式之间的下标映射。

    作用与效果：支持在归一化文本上做精确查找，并把命中位置反解回原文下标，
    用于校验关键词并生成高亮区间。
    输入：`text` 为普通文本。
    输出：保留 `normalized` 字符串和逐字符的原文下标映射。
    """

    def __init__(self, text: str) -> None:
        self.text = text
        buffer: list[str] = []
        positions: list[int] = []
        for index, char in enumerate(text):
            for normalized in normalize_char(char):
                buffer.append(normalized)
                positions.append(index)
        self.normalized = "".join(buffer)
        self.positions = positions

    def find(self, keyword: str) -> tuple[int, int] | None:
        """按首次出现位置查找关键词。

        作用与效果：对关键词做相同归一化后精确查找，命中时把区间还原到原文下标。
        输入：`keyword` 为待查找的连续文本。
        输出：原文半开区间 `[start, end)`；未命中时返回 `None`。
        """
        needle = "".join(normalize_char(char) for char in keyword)
        if not needle:
            return None
        offset = self.normalized.find(needle)
        if offset < 0:
            return None
        return self.positions[offset], self.positions[offset + len(needle) - 1] + 1
