"""关键词校验测试：模型给候选，代码验证并定位。"""

import unittest

from server.sub_api.segmentation.keywords import validate_keywords

TEXT = "它这个蛋黄打出来都是又黄又饱满，蛋清也是这种 QQ 弹弹的"


class KeywordValidatorTests(unittest.TestCase):
    def test_keyword_must_exist_verbatim(self) -> None:
        keywords, rejected = validate_keywords(TEXT, ["蛋黄", "土鸡蛋"], max_count=5, max_length=12)

        self.assertEqual([item.text for item in keywords], ["蛋黄"])
        self.assertEqual(rejected, 1)

    def test_offsets_can_be_sliced_back(self) -> None:
        keywords, _ = validate_keywords(TEXT, ["饱满", "蛋清"], max_count=5, max_length=12)

        for keyword in keywords:
            self.assertEqual(TEXT[keyword.start : keyword.end], keyword.text)
        self.assertEqual(
            [item.start for item in keywords], sorted(item.start for item in keywords)
        )

    def test_search_ignores_case_and_fullwidth(self) -> None:
        keywords, _ = validate_keywords(TEXT, ["qq"], max_count=5, max_length=12)

        self.assertEqual(len(keywords), 1)
        self.assertEqual(TEXT[keywords[0].start : keywords[0].end], "QQ")

    def test_length_and_count_limits(self) -> None:
        keywords, rejected = validate_keywords(
            "新能源汽车销量同比增长40%，适合家庭使用",
            ["的", "新能源汽车", "销量", "增长", "适合家庭使用", "家庭"],
            max_count=3,
            max_length=6,
        )

        self.assertLessEqual(len(keywords), 3)
        for item in keywords:
            self.assertLessEqual(len(item.text), 6)
        self.assertGreaterEqual(rejected, 1)

    def test_shorter_keyword_contained_in_longer_one_is_dropped(self) -> None:
        keywords, _ = validate_keywords(
            "农家散养的土鸡蛋", ["鸡蛋", "土鸡蛋"], max_count=5, max_length=12
        )

        self.assertEqual([item.text for item in keywords], ["土鸡蛋"])

    def test_duplicate_candidates_are_rejected(self) -> None:
        keywords, rejected = validate_keywords(TEXT, ["蛋黄", "蛋黄"], max_count=5, max_length=12)

        self.assertEqual([item.text for item in keywords], ["蛋黄"])
        self.assertEqual(rejected, 1)

    def test_single_character_keyword_is_kept(self) -> None:
        text = "平时不管是炒还是蒸都可以"
        keywords, rejected = validate_keywords(text, ["炒", "蒸"], max_count=5, max_length=12)

        self.assertEqual([item.text for item in keywords], ["炒", "蒸"])
        self.assertEqual(rejected, 0)
        for keyword in keywords:
            self.assertEqual(text[keyword.start : keyword.end], keyword.text)

    def test_missing_or_oversized_keyword_is_still_dropped(self) -> None:
        text = "平时不管是炒还是蒸都可以"
        keywords, rejected = validate_keywords(
            text, ["煮", "平时不管是炒还是蒸都可以吗"], max_count=5, max_length=12
        )

        self.assertEqual(keywords, [])
        self.assertEqual(rejected, 2)


if __name__ == "__main__":
    unittest.main()
