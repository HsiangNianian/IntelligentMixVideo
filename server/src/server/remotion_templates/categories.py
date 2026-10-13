"""Remotion 效果分类：字效、滤镜、转场与视频特效各自必备的顶层参数，以及顶层参数的通用约束。

分类与 SpriteKind 一一对应（视频特效与转场合为界面上的第三类）。这里只做纯数据检查：
Agent 保存 Sprite 时据此在循环内修复，发布时据此拒绝缺少必备参数的版本。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, get_args

from ..template.schema import OVERRIDE_STRING_MAX_LENGTH
from .tools.contracts import SpriteKindName

KINDS: tuple[SpriteKindName, ...] = get_args(SpriteKindName)
# 关键词字号启用时的下限，与总线 IMS 模板的 *_keyword_size（0 或 12～300）一致。
KEYWORD_SIZE_MIN = 12
# 关键词颜色允许空串（沿用文字颜色）或 #RRGGBB，与总线 IMS 模板的 *_keyword_color 一致。
KEYWORD_COLOR = "^(?:|#[0-9A-Fa-f]{6})$"
# 颜色参数统一使用的 JSON Schema pattern；界面和宿主按它识别颜色控件。
HEX_COLOR = "^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$"
# 文字参数的长度上限；更长的文字无法在固定画布内可靠排版。
TEXT_MAX_LENGTH = 200
# 关键词列表的项数和逐项长度上限，与总线的关键词约束一致。
KEYWORDS_MAX_ITEMS = 20
KEYWORD_MAX_LENGTH = 100
# 每项关键词至少要容纳的长度：总线转发切片返回的标题关键词（最多 12 字）与字幕片段关键词。
KEYWORD_MIN_LENGTH = 12


@dataclass(frozen=True)
class Rule:
    """一个标准参数：名称、给 Agent 的 Schema 示例、给运营的中文说明及检查函数。"""

    name: str
    example: dict
    label: str
    accepts: Callable[[dict], bool]
    # 必须使用的默认值（关键词参数默认不启用）；None 表示不限制默认值。
    off: object = None


def _number(definition: dict) -> bool:
    """数字参数：number 或 integer，且声明了有限的上下界。"""
    bounds = (definition.get("minimum"), definition.get("maximum"))
    return definition.get("type") in {"number", "integer"} and all(
        isinstance(value, (int, float)) and not isinstance(value, bool) for value in bounds
    ) and bounds[0] < bounds[1]


def _color(definition: dict) -> bool:
    """颜色参数：字符串且使用统一的十六进制颜色 pattern。"""
    return definition.get("type") == "string" and definition.get("pattern") == HEX_COLOR


def _text(definition: dict) -> bool:
    """文字参数：非空字符串，并声明不超过上限的最大长度。"""
    limit = definition.get("maxLength")
    return (
        definition.get("type") == "string"
        and isinstance(definition.get("minLength"), int) and definition["minLength"] >= 1
        and isinstance(limit, int) and not isinstance(limit, bool) and 1 <= limit <= TEXT_MAX_LENGTH
    )


def _between(low: float, high: float, lowest: float, highest: float) -> Callable[[dict], bool]:
    """数字参数的范围必须落在给定区间内，且下界不高于 lowest、上界不低于 highest 以保证可调。"""
    def accepts(definition: dict) -> bool:
        """检查类型与上下界。"""
        return _number(definition) and low <= definition["minimum"] <= lowest and highest <= definition["maximum"] <= high
    return accepts


def _exact(minimum: float, maximum: float) -> Callable[[dict], bool]:
    """数字参数的上下界必须恰好是约定的范围。"""
    def accepts(definition: dict) -> bool:
        """检查类型与精确的上下界。"""
        return _number(definition) and definition["minimum"] == minimum and definition["maximum"] == maximum
    return accepts


def _weight(definition: dict) -> bool:
    """字重参数：只允许受管字体提供的 400 与 700。"""
    values = definition.get("enum")
    return (
        definition.get("type") in {"integer", "number"}
        and isinstance(values, list) and len(values) == 2
        and {value for value in values if not isinstance(value, bool)} == {400, 700}
    )


def _keywords(definition: dict) -> bool:
    """关键词参数：字符串数组，限制项数与逐项长度。"""
    items = definition.get("items")
    limit = definition.get("maxItems")
    return (
        definition.get("type") == "array"
        and isinstance(items, dict) and items.get("type") == "string"
        and isinstance(items.get("maxLength"), int) and 1 <= items["maxLength"] <= KEYWORD_MAX_LENGTH
        and isinstance(limit, int) and not isinstance(limit, bool) and 1 <= limit <= KEYWORDS_MAX_ITEMS
    )


def _keyword_color(definition: dict) -> bool:
    """关键词颜色参数：字符串且使用允许空串的 pattern。"""
    return definition.get("type") == "string" and definition.get("pattern") == KEYWORD_COLOR


def _keyword_size(definition: dict) -> bool:
    """关键词字号参数：整数，范围恰好是 0（沿用文字字号）到 300。"""
    return definition.get("type") == "integer" and _exact(0, 300)(definition)


def _boolean(definition: dict) -> bool:
    """开关参数：布尔值。"""
    return definition.get("type") == "boolean"


# 字效的关键词列表：组件只在文字里高亮每个关键词的首次字面命中（与总线一致）。
KEYWORDS = Rule(
    "keywords",
    {"type": "array", "items": {"type": "string", "maxLength": KEYWORD_MAX_LENGTH}, "maxItems": KEYWORDS_MAX_ITEMS},
    f"关键词列表，字符串数组，最多 {KEYWORDS_MAX_ITEMS} 项，每项的 maxLength 在 {KEYWORD_MIN_LENGTH}～{KEYWORD_MAX_LENGTH} 之间",
    lambda definition: _keywords(definition) and definition["items"]["maxLength"] >= KEYWORD_MIN_LENGTH,
    off=[],
)
# 关键词局部样式：与总线 IMS 模板的 title/subtitle 关键词字段同名同义，由总线按摆放覆盖。
KEYWORD_STYLES = tuple(
    Rule(f"keyword{name}", {"type": "boolean"}, f"关键词{label}开关，布尔值", _boolean, off=False)
    for name, label in (("Bold", "加粗"), ("Italic", "斜体"), ("Underline", "下划线"), ("Strikeout", "删除线"))
)
KEYWORD_COLOR_RULE = Rule(
    "keywordColor", {"type": "string", "pattern": KEYWORD_COLOR},
    "关键词颜色，空字符串表示沿用文字颜色，否则是 #RRGGBB", _keyword_color, off="",
)
KEYWORD_SIZE_RULE = Rule(
    "keywordSize", {"type": "integer", "minimum": 0, "maximum": 300},
    f"关键词字号，整数，0 表示沿用文字字号，否则是 {KEYWORD_SIZE_MIN}～300 像素", _keyword_size, off=0,
)
# 字效的关键词参数：都是必备的，默认值必须是「不启用」（off），不用时保持原有样式。
TEXT_KEYWORDS = (KEYWORDS, *KEYWORD_STYLES, KEYWORD_COLOR_RULE, KEYWORD_SIZE_RULE)


_COLOR = {"type": "string", "pattern": HEX_COLOR}
REQUIRED: dict[SpriteKindName, tuple[Rule, ...]] = {
    "text": (
        Rule("text", {"type": "string", "minLength": 1, "maxLength": 40}, f"文字内容，非空字符串并声明不超过 {TEXT_MAX_LENGTH} 的 maxLength", _text),
        Rule("fontSize", {"type": "number", "minimum": 24, "maximum": 240}, "字号，数字，范围在 8～400 像素之内且至少覆盖 24～160", _between(8, 400, 24, 160)),
        Rule("fontWeight", {"type": "integer", "enum": [400, 700]}, "字重，只能是 400 和 700 两个枚举值", _weight),
        Rule("color", _COLOR, "文字主色，十六进制颜色", _color),
        *TEXT_KEYWORDS,
    ),
    "filter_overlay": (
        Rule("intensity", {"type": "number", "minimum": 0, "maximum": 1}, "强度，0～1 的数字", _exact(0, 1)),
        Rule("tintColor", _COLOR, "色调，十六进制颜色", _color),
    ),
    "video_overlay": (
        Rule("color", _COLOR, "主色，十六进制颜色", _color),
        Rule("speed", {"type": "number", "minimum": 0.25, "maximum": 4}, "速度倍率，0.25～4 的数字", _exact(0.25, 4)),
    ),
    "transition_overlay": (
        Rule("color", _COLOR, "主色，十六进制颜色", _color),
        Rule("speed", {"type": "number", "minimum": 0.25, "maximum": 4}, "速度倍率，0.25～4 的数字", _exact(0.25, 4)),
    ),
}


# 默认值未关闭的关键词参数的原因前缀；运营说明据此与缺少参数分开提示。
DEFAULT_REASON = "must default to"


@dataclass(frozen=True)
class Problem:
    """一个不合规的顶层参数及其应有的形状。"""

    name: str
    rule: Rule | None
    reason: str


def _properties(schema: dict) -> dict:
    """读取 Schema 顶层声明的属性；结构异常时视为没有属性。"""
    properties = schema.get("properties")
    return properties if isinstance(properties, dict) else {}


# 顶层参数允许的 Schema 关键字：发布时的边界抽样只认识这些约束，其他约束（如 multipleOf、exclusiveMaximum、
# 非颜色 pattern）会让两端取值不合 Schema 而漏抽。title、description 与 default 只是说明。
_LABELS = {"type", "title", "description", "default"}
_ALLOWED = {
    "number": _LABELS | {"minimum", "maximum", "enum"},
    "integer": _LABELS | {"minimum", "maximum", "enum"},
    "string": _LABELS | {"pattern", "enum", "minLength", "maxLength"},
    "boolean": _LABELS,
    "array": _LABELS | {"items", "maxItems"},
}
_ALLOWED_ITEMS = _LABELS | {"maxLength"}


def _unsupported(definition: dict) -> list[str]:
    """顶层参数里抽样无法处理的 Schema 关键字。"""
    extra = set(definition) - _ALLOWED.get(definition.get("type"), _LABELS)
    if definition.get("type") == "array" and isinstance(definition.get("items"), dict):
        extra |= {f"items.{key}" for key in set(definition["items"]) - _ALLOWED_ITEMS}
    return sorted(extra)


def parameter_problems(definition: object, name: str) -> str | None:
    """顶层标准参数的通用约束：控件需要明确的取值范围，返回违反原因或 None。

    数字须声明上下界；字符串须是颜色、枚举或声明 maxLength；布尔不受限；数组只允许关键词形状。
    字符串的 maxLength 不能超过模板覆盖值的上限：否则控件允许的文字保存不了，发布时的长文字样本也会无限膨胀。
    """
    if not isinstance(definition, dict):
        return "must be a JSON Schema object"
    if "$ref" in str(definition):
        return "must not use $ref"
    if extra := _unsupported(definition):
        return f"uses schema keywords the publish-time samples cannot cover: {', '.join(extra)}"
    kind = definition.get("type")
    if kind == "integer" and any(isinstance(definition.get(bound), float) and not definition[bound].is_integer() for bound in ("minimum", "maximum")):
        return "an integer needs integer minimum and maximum"
    if kind in {"number", "integer"}:
        return None if _number(definition) or isinstance(definition.get("enum"), list) else "a number needs minimum and maximum"
    if kind == "string":
        limit = definition.get("maxLength")
        if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= OVERRIDE_STRING_MAX_LENGTH):
            return f"a string maxLength must be between 1 and {OVERRIDE_STRING_MAX_LENGTH}"
        keyword_color = name == KEYWORD_COLOR_RULE.name and _keyword_color(definition)
        if "pattern" in definition and not (_color(definition) or keyword_color):
            return f"a string pattern must be the colour pattern {HEX_COLOR} (only keywordColor may use {KEYWORD_COLOR})"
        if isinstance(definition.get("minLength"), int) and limit is not None and definition["minLength"] > limit:
            return "a string minLength must not exceed its maxLength"
        if _color(definition) or keyword_color or isinstance(definition.get("enum"), list) or limit is not None:
            return None
        return f"a string needs the colour pattern {HEX_COLOR}, an enum or a maxLength"
    if kind == "boolean":
        return None
    if kind == "array":
        return None if _keywords(definition) else "only a keywords string array is supported"
    return "type must be number, integer, string, boolean or a keywords array"


def category_problems(kind: str | None, schema: dict, bound: set[str], defaults: dict) -> list[Problem]:
    """列出某分类缺少、形状不符或默认值不合约定的必备参数；``bound`` 是作为顶层参数暴露的名称，``defaults`` 是默认参数。"""
    if kind not in REQUIRED:
        return [Problem("kind", None, f"kind must be one of {', '.join(KINDS)}")]
    properties = _properties(schema)
    problems = []
    for rule in REQUIRED[kind]:
        if rule.name not in bound or rule.name not in properties:
            problems.append(Problem(rule.name, rule, "missing"))
        elif not rule.accepts(properties[rule.name]):
            problems.append(Problem(rule.name, rule, "has the wrong schema"))
        elif rule.off is not None and (rule.name not in defaults or not _same(defaults[rule.name], rule.off)):
            problems.append(Problem(rule.name, rule, f"{DEFAULT_REASON} {json.dumps(rule.off)} (off)"))
    return problems


def _same(value: object, expected: object) -> bool:
    """默认值与约定值相同；布尔与数字不互相等同（False 不等于 0），0.0 与 0 视为相同。"""
    return isinstance(value, bool) == isinstance(expected, bool) and value == expected


def agent_message(kind: str | None, problems: list[Problem]) -> str:
    """给 Agent 的英文说明：缺什么、应有的 Schema，以及如何在 sprite_compose 里绑定。"""
    lines = [f"The {kind} Sprite does not expose the category's required top-level parameters:"]
    for item in problems:
        example = f"; schema {item.rule.example}" if item.rule else ""
        lines.append(f"- {item.name} {item.reason}{example}")
    lines.append(
        'Bind each one with sprite_compose "parameters": [{"name": ..., "title": <Chinese label>, '
        '"targets": [{"instance_id": ..., "parameter": <Preset prop>}]}] after giving the Preset prop that schema.'
    )
    return "\n".join(lines)


def operator_message(problems: list[Problem]) -> str:
    """给运营的中文说明：缺少或形状不符的必备参数要在聊天中补充，默认值未关闭的关键词参数要在参数面板恢复。"""
    shape = [f"{item.name}（{item.rule.label}）" for item in problems if item.rule is not None and not item.reason.startswith(DEFAULT_REASON)]
    on = [f"{item.name}（应为 {json.dumps(item.rule.off, ensure_ascii=False)}）" for item in problems if item.rule is not None and item.reason.startswith(DEFAULT_REASON)]
    messages = []
    if shape:
        messages.append(f"所选分类缺少或不符合必备参数：{'；'.join(shape)}。请在聊天中让 Agent 补充后再保存到资产")
    if on:
        messages.append(f"关键词参数的默认值必须是关闭状态：{'；'.join(on)}。关键词样式由模板按摆放设置，请在参数面板把它们恢复为关闭后再保存到资产")
    return "。".join(messages)


# 宿主变换的取值范围（缩放、旋转、不透明度）；位置在 0 到画布宽高之间。
SCALE_RANGE = (0.25, 4.0)
ROTATION_RANGE = (-180.0, 180.0)
OPACITY_RANGE = (0.0, 1.0)
# 滤镜与转场覆盖整块画布，只能调整不透明度（强度）；字效与视频特效还可以移动、缩放和旋转。
_FULL_FRAME = {"filter_overlay", "transition_overlay"}


def transform_fields(kind: str | None) -> set[str]:
    """宿主变换中该分类可以设置的字段。"""
    return {"opacity"} if kind in _FULL_FRAME else {"x", "y", "scale", "rotation", "opacity"}


def transform_ranges(width: float, height: float) -> dict[str, tuple[float, float]]:
    """每个宿主变换字段在给定画布上的闭区间。"""
    return {"x": (0.0, float(width)), "y": (0.0, float(height)), "scale": SCALE_RANGE, "rotation": ROTATION_RANGE, "opacity": OPACITY_RANGE}
