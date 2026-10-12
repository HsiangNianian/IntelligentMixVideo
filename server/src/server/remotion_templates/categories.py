"""Remotion 效果分类：字效、滤镜、转场与视频特效各自必备的顶层参数，以及顶层参数的通用约束。

分类与 SpriteKind 一一对应（视频特效与转场合为界面上的第三类）。这里只做纯数据检查：
Agent 保存 Sprite 时据此在循环内修复，发布时据此拒绝缺少必备参数的版本。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, get_args

from .tools.contracts import SpriteKindName

KINDS: tuple[SpriteKindName, ...] = get_args(SpriteKindName)
# 颜色参数统一使用的 JSON Schema pattern；界面和宿主按它识别颜色控件。
HEX_COLOR = "^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$"
# 文字参数的长度上限；更长的文字无法在固定画布内可靠排版。
TEXT_MAX_LENGTH = 200
# 关键词列表的项数和逐项长度上限，与总线的关键词约束一致。
KEYWORDS_MAX_ITEMS = 20
KEYWORD_MAX_LENGTH = 100


@dataclass(frozen=True)
class Rule:
    """一个标准参数：名称、给 Agent 的 Schema 示例、给运营的中文说明及检查函数。"""

    name: str
    example: dict
    label: str
    accepts: Callable[[dict], bool]


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


_COLOR = {"type": "string", "pattern": HEX_COLOR}
REQUIRED: dict[SpriteKindName, tuple[Rule, ...]] = {
    "text": (
        Rule("text", {"type": "string", "minLength": 1, "maxLength": 40}, f"文字内容，非空字符串并声明不超过 {TEXT_MAX_LENGTH} 的 maxLength", _text),
        Rule("fontSize", {"type": "number", "minimum": 24, "maximum": 240}, "字号，数字，范围在 8～400 像素之内且至少覆盖 24～160", _between(8, 400, 24, 160)),
        Rule("fontWeight", {"type": "integer", "enum": [400, 700]}, "字重，只能是 400 和 700 两个枚举值", _weight),
        Rule("color", _COLOR, "文字主色，十六进制颜色", _color),
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
# 字效可选的关键词参数：声明时必须符合约定形状，组件自行在文字里查找字面命中并高亮。
KEYWORDS = Rule(
    "keywords",
    {"type": "array", "items": {"type": "string", "maxLength": KEYWORD_MAX_LENGTH}, "maxItems": KEYWORDS_MAX_ITEMS},
    f"关键词列表，字符串数组，最多 {KEYWORDS_MAX_ITEMS} 项、每项不超过 {KEYWORD_MAX_LENGTH} 字",
    _keywords,
)


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


def parameter_problems(definition: object) -> str | None:
    """顶层标准参数的通用约束：控件需要明确的取值范围，返回违反原因或 None。

    数字须声明上下界；字符串须是颜色、枚举或声明 maxLength；布尔不受限；数组只允许关键词形状。
    """
    if not isinstance(definition, dict):
        return "must be a JSON Schema object"
    if "$ref" in str(definition):
        return "must not use $ref"
    kind = definition.get("type")
    if kind in {"number", "integer"}:
        return None if _number(definition) or isinstance(definition.get("enum"), list) else "a number needs minimum and maximum"
    if kind == "string":
        if _color(definition) or isinstance(definition.get("enum"), list) or isinstance(definition.get("maxLength"), int):
            return None
        return f"a string needs the colour pattern {HEX_COLOR}, an enum or a maxLength"
    if kind == "boolean":
        return None
    if kind == "array":
        return None if _keywords(definition) else "only a keywords string array is supported"
    return "type must be number, integer, string, boolean or a keywords array"


def category_problems(kind: str | None, schema: dict, bound: set[str]) -> list[Problem]:
    """列出某分类缺少或不合规的必备参数；``bound`` 是作为顶层参数暴露的名称。"""
    if kind not in REQUIRED:
        return [Problem("kind", None, f"kind must be one of {', '.join(KINDS)}")]
    properties = _properties(schema)
    problems = []
    rules = REQUIRED[kind] + ((KEYWORDS,) if kind == "text" and "keywords" in bound else ())
    for rule in rules:
        if rule.name not in bound or rule.name not in properties:
            problems.append(Problem(rule.name, rule, "missing"))
        elif not rule.accepts(properties[rule.name]):
            problems.append(Problem(rule.name, rule, "has the wrong schema"))
    return problems


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
