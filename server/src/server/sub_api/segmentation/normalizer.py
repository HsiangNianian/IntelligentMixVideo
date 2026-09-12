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
