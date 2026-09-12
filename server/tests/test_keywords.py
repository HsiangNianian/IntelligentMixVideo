"""关键词校验测试：模型给候选，代码验证并定位。

在 server/ 执行 uv run --locked pytest -v；使用离线样本与替身。
"""

from server.sub_api.segmentation.keywords import validate_keywords

TEXT = "它这个蛋黄打出来都是又黄又饱满，蛋清也是这种 QQ 弹弹的"


def test_keyword_must_exist_verbatim() -> None:
    """保留原文存在的关键词，拒绝不存在的候选。"""
    keywords, rejected = validate_keywords(
        TEXT, ["蛋黄", "土鸡蛋"], max_count=5, max_length=12
    )

    assert [item.text for item in keywords] == ["蛋黄"]
    assert rejected == 1


def test_offsets_can_be_sliced_back() -> None:
    """关键词按位置排序，输出下标能切回原文。"""
    keywords, _ = validate_keywords(TEXT, ["饱满", "蛋清"], max_count=5, max_length=12)

    for keyword in keywords:
        assert TEXT[keyword.start : keyword.end] == keyword.text
    assert [item.start for item in keywords] == sorted(
        (item.start for item in keywords)
    )


def test_search_requires_exact_case_and_width() -> None:
    """关键词区分大小写及全半角，拒绝不精确匹配的候选。"""
    text = "QQ，ＡＢ，AB"
    keywords, rejected = validate_keywords(
        text, ["qq", "ｑｑ", "QQ", "ＡＢ", "AB"], max_count=5, max_length=12
    )

    assert [item.text for item in keywords] == ["QQ", "ＡＢ", "AB"]
    assert rejected == 2
    for keyword in keywords:
        assert text[keyword.start : keyword.end] == keyword.text


def test_length_and_count_limits() -> None:
    """关键词输出遵守数量与长度限制，并统计拒绝候选。"""
    keywords, rejected = validate_keywords(
        "新能源汽车销量同比增长40%，适合家庭使用",
        ["的", "新能源汽车", "销量", "增长", "适合家庭使用", "家庭"],
        max_count=3,
        max_length=6,
    )

    assert len(keywords) <= 3
    for item in keywords:
        assert len(item.text) <= 6
    assert rejected >= 1


def test_shorter_keyword_contained_in_longer_one_is_dropped() -> None:
    """重叠候选保留较长词，去除被包含的短词。"""
    keywords, _ = validate_keywords(
        "农家散养的土鸡蛋", ["鸡蛋", "土鸡蛋"], max_count=5, max_length=12
    )

    assert [item.text for item in keywords] == ["土鸡蛋"]


def test_duplicate_candidates_are_rejected() -> None:
    """重复候选只保留一次，并计入拒绝数量。"""
    keywords, rejected = validate_keywords(
        TEXT, ["蛋黄", "蛋黄"], max_count=5, max_length=12
    )

    assert [item.text for item in keywords] == ["蛋黄"]
    assert rejected == 1


def test_single_character_keyword_is_kept() -> None:
    """单字关键词有效，按原文位置返回且不误报拒绝。"""
    text = "平时不管是炒还是蒸都可以"
    keywords, rejected = validate_keywords(
        text, ["炒", "蒸"], max_count=5, max_length=12
    )

    assert [item.text for item in keywords] == ["炒", "蒸"]
    assert rejected == 0
    for keyword in keywords:
        assert text[keyword.start : keyword.end] == keyword.text


def test_missing_or_oversized_keyword_is_still_dropped() -> None:
    """不存在或超长的候选被拒绝，返回空关键词列表。"""
    text = "平时不管是炒还是蒸都可以"
    keywords, rejected = validate_keywords(
        text, ["煮", "平时不管是炒还是蒸都可以吗"], max_count=5, max_length=12
    )

    assert keywords == []
    assert rejected == 2
