"""Remotion 效果分类：字效、滤镜、转场与视频特效各自必备的顶层参数，以及顶层参数的通用约束。

分类与 SpriteKind 一一对应（视频特效与转场合为界面上的第三类）。这里只做纯数据检查：
Agent 保存 Sprite 时据此在循环内修复，发布时据此拒绝缺少必备参数的版本。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from collections.abc import Collection
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
# 字效统一支持 100 字；排版失败必须修复布局，不能降低契约上限。
TEXT_MAX_LENGTH = 100
# 关键词列表的项数和逐项长度上限，与总线的关键词约束一致。
KEYWORDS_MAX_ITEMS = 20
KEYWORD_MAX_LENGTH = 100
# 每项关键词至少要容纳的长度：总线转发切片返回的标题关键词（最多 12 字）与字幕片段关键词。
KEYWORD_MIN_LENGTH = 12
# 多行字效：`text` 用换行分隔各行，`lineStartFrames` 是每行在资产内出现的帧。它属于内容，只由宿主按字幕切片注入，
# 不列入可编辑参数；资产固定 2～MAX_LINES 行，没有这个参数的字效就是单行。
LINE_STARTS_PROP = "lineStartFrames"
MAX_LINES = 4
# 行起点的上限：资产最长按 120 秒、30 FPS 播放。
LINE_START_MAX_FRAMES = 3600
# 多行字效相邻两行之间的距离（像素）：所有行在同一个纵向容器里排布，模板可以按摆放调整。
LINE_GAP_PROP = "lineGap"
# 双栏的排布参数与取值：stack 上下排列，overlap 两栏叠在同一处、彼此居中、下栏在上层。
TIER_LAYOUT = "tierLayout"
TIER_LAYOUTS = ("stack", "overlap")
# 双语字效每组占中文、英文两行，支持一组或两组；textEn 的各行与 text 的各行配对注入。
BILINGUAL_PROP = "textEn"
# 四行双栏双语的组内中英间距；lineGap 仍控制两组之间的距离。
TRANSLATION_GAP_PROP = "translationGap"
# 普通双栏固定两行；双栏双语使用四行，更早发布的多行资产仍按封存的行数显示。
TIERS = 2
# 双栏字效每栏的字号是两栏共用的 fontSize 乘以该栏的比例，比例范围固定。
TIER_SIZE_RATIO = (0.4, 2)


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
    """文字参数：非空字符串，maxLength 必须恰好为 100。"""
    limit = definition.get("maxLength")
    return (
        definition.get("type") == "string"
        and isinstance(definition.get("minLength"), int) and definition["minLength"] >= 1
        and isinstance(limit, int) and not isinstance(limit, bool) and limit == TEXT_MAX_LENGTH
    )


def _font_family(definition: dict) -> bool:
    """字体参数：字符串枚举，取值是非空的家族名列表（是否都在字体库里由生成路径另查）。"""
    values = definition.get("enum")
    return (
        definition.get("type") == "string"
        and isinstance(values, list) and values != [] and all(isinstance(value, str) and value for value in values)
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


def _line_starts(definition: dict) -> bool:
    """多行字效的行起点：恰好 N 项（2～MAX_LINES）的整数数组，每项是 0～LINE_START_MAX_FRAMES 的帧号。"""
    items = definition.get("items")
    count = definition.get("maxItems")
    return (
        definition.get("type") == "array"
        and isinstance(items, dict) and items.get("type") == "integer"
        and items.get("minimum") == 0 and items.get("maximum") == LINE_START_MAX_FRAMES
        and isinstance(count, int) and not isinstance(count, bool) and 2 <= count <= MAX_LINES
        and definition.get("minItems") == count
    )


def line_count(schema: dict) -> int:
    """字效的固定行数：声明了合规 `lineStartFrames` 的多行字效为其项数，其余（包括所有已发布的单行字效）为 1。"""
    definition = _properties(schema).get(LINE_STARTS_PROP)
    return definition["maxItems"] if isinstance(definition, dict) and _line_starts(definition) else 1


def bilingual(schema: dict) -> bool:
    """字效是否是双语字效：声明了英文文字参数 `textEn` 的字效下栏是同一句的英文，模板与合成据此注入两种语言。"""
    return isinstance(_properties(schema).get(BILINGUAL_PROP), dict)


def content_line_count(schema: dict) -> int:
    """每个实例容纳的内容组数；双语每组占中文、英文两行，旧单组双语仍为一组。"""
    count = line_count(schema)
    return max(1, count // 2) if bilingual(schema) else count


def content_line_starts(schema: dict, starts: list[int]) -> list[int]:
    """将各组出现帧补齐到资产行数；双语每组的原文与译文使用同一个起点。"""
    groups = content_line_count(schema)
    values = (starts or [0])[:groups]
    values += [values[-1]] * (groups - len(values))
    return [frame for frame in values for _ in range(2)] if bilingual(schema) else values


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


# 字效的字体参数：只在 Agent 生成时必备（`fonts` 传入时检查），发布更早的版本不要求，所以不在 REQUIRED 里。
FONT_FAMILY = Rule(
    "fontFamily", {"type": "string", "enum": ["<family names from the font library>"]},
    "字体，字符串枚举，取值是字体库里的家族名，默认值是其中之一", _font_family,
)
# 多行字效的行起点：可选，绑定了才是多行字效，所以不在 REQUIRED 里。
LINE_STARTS = Rule(
    LINE_STARTS_PROP,
    # The example is a two-line text; minItems and maxItems are both the number of lines.
    {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": LINE_START_MAX_FRAMES}, "minItems": 2, "maxItems": 2},
    f"多行字效每行出现的帧，整数数组（minItems 与 maxItems 都等于行数，新生成的双栏为 {TIERS}），首项为 0 且不递减", _line_starts,
)
# 数组参数不合规时附给 Agent 的说明：逐栏样式是固定名称的标量参数，不能做成数组。
_ARRAY_HINT = (
    "per-tier styles are the fixed scalar parameters line1Color, line1SizeRatio, line1FontFamily and line1FontWeight "
    "(and the same for line2), not arrays"
)
# 多行字效的行间距：绑定了行起点就必须有，用户才能调整两行之间的距离。下界可为负数，让下栏向上移、叠住上栏；
# 0 仍接受，更早生成的资产照常发布。
LINE_GAP = Rule(
    LINE_GAP_PROP, {"type": "number", "minimum": -600, "maximum": 300},
    "多行字效相邻两行之间的距离（像素），数字，下界可在 -600～0 之间（负值让下栏上移叠住上栏）、上界在 200～600 之间",
    _between(-600, 600, 0, 200),
)
# 双语字效的英文文字：可选，绑定了才是双语字效（下栏是中文那一句的英文译文，由总线按切片注入）。
BILINGUAL = Rule(
    BILINGUAL_PROP, {"type": "string", "minLength": 1, "maxLength": TEXT_MAX_LENGTH},
    f"双语字效的英文文字，非空字符串且 maxLength 必须为 {TEXT_MAX_LENGTH}", _text,
)
TRANSLATION_GAP = Rule(
    TRANSLATION_GAP_PROP, {"type": "number", "minimum": 0, "maximum": 100},
    "双栏双语每组中文与英文之间的距离，0～100 像素", _exact(0, 100),
)
_COLOR = {"type": "string", "pattern": HEX_COLOR}
REQUIRED: dict[SpriteKindName, tuple[Rule, ...]] = {
    "text": (
        Rule("text", {"type": "string", "minLength": 1, "maxLength": TEXT_MAX_LENGTH}, f"文字内容，非空字符串且 maxLength 必须为 {TEXT_MAX_LENGTH}", _text),
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
# 关键词字效的开关：画了关键词字效的字效必须暴露它（默认开启），关掉后命中词只按六个关键词样式显示，不用重新生成。
KEYWORD_EFFECT_PROP = "keywordEffect"
KEYWORD_EFFECT = Rule(KEYWORD_EFFECT_PROP, {"type": "boolean"}, "关键词字效开关，布尔值，默认开启", _boolean)
# 宿主固定名称的参数在参数面板里的中文标题：组合时由宿主写入（`tools/session.py`），不随 Agent 的措辞变化。
FIXED_TITLES = {
    "text": "文字", "fontSize": "字号", "fontWeight": "字重", "fontFamily": "字体",
    "keywords": "关键词", "keywordBold": "关键词加粗", "keywordItalic": "关键词斜体", "keywordUnderline": "关键词下划线",
    "keywordStrikeout": "关键词删除线", "keywordColor": "关键词颜色", "keywordSize": "关键词字号", KEYWORD_EFFECT_PROP: "关键词字效",
    LINE_GAP_PROP: "栏间距", LINE_STARTS_PROP: "各栏出现帧", TIER_LAYOUT: "双栏排布", BILINGUAL_PROP: "英文文字", TRANSLATION_GAP_PROP: "中英间距",
    **{
        f"line{tier}{suffix}": f"{name}{label}"
        for tier, name in ((1, "上栏"), (2, "下栏"), (3, "第三行"), (4, "第四行"))
        for suffix, label in (("Color", "颜色"), ("SizeRatio", "字号比例"), ("FontFamily", "字体"), ("FontWeight", "字重"))
    },
}

# 文字块第 k 个特效（从 1 起）的配置参数：开关 effect{k}、作用位置 effect{k}Target，入场、出场与下栏出现另有时长 effect{k}Seconds
# （秒），入场与下栏出现还有推迟开始的 effect{k}Delay（秒），默认放在逐字位置的另有各字错开程度 effect{k}Stagger（0～1）。名称由宿主
# 按特效顺序生成，标题取特效的中文名称（`compose.text_configuration`）；样式参数与实例都不能用这种名字。
EFFECT_PARAMETER = re.compile(r"^effect[1-9][0-9]*(?:Target|Seconds|Delay|Stagger)?$")
# 特效可选的位置：单栏没有各栏与上下栏；逐字位置只在各栏的 text Preset 都画宿主的 children 时可选，关键词逐字另要求关键词 Preset
# 也画 children（`compose.compose_text_source`）。
EFFECT_TARGETS = {
    1: ("block", "chars", "keywordChars", "plainChars", "keyword"),
    2: ("block", "tiers", "line1", "line2", "chars", "keywordChars", "plainChars", "keyword"),
    4: ("block", "tiers", "line1", "line2", "line3", "line4", "chars", "keywordChars", "plainChars", "keyword"),
}
# 逐字位置：每个字一个单元，依次分享入场、出场进度；关键词逐字位置单列，它要求关键词 Preset 画宿主的 children。
GLYPH_TARGETS = ("chars", "keywordChars", "plainChars")
KEYWORD_GLYPH_TARGET = "keywordChars"
# 入场与下栏出现特效推迟开始的秒数范围，省略为 0。
EFFECT_DELAY = (0, 3)
# 入场、出场时长（秒）的范围与省略时的默认值（与 IMS 文字入场、出场一致）。
EFFECT_SECONDS = (0.1, 3)
DEFAULT_EFFECT_SECONDS = 0.5
# 特效类型的中文名称，写在开关参数的标题里。
EFFECT_SLOTS = {"enter": "入场", "exit": "出场", "loop": "持续", "cue": "下栏出现"}


def _tier_rules(tier: int) -> tuple[Rule, ...]:
    """双栏字效第 ``tier`` 栏（1 为上栏）自己的样式参数：颜色、相对 fontSize 的字号比例、字体与字重，名称与 Schema 固定。"""
    low, high = TIER_SIZE_RATIO
    name = FIXED_TITLES[f"line{tier}Color"][:2]
    return (
        Rule(f"line{tier}Color", _COLOR, f"{name}文字颜色，十六进制颜色", _color),
        Rule(f"line{tier}SizeRatio", {"type": "number", "minimum": low, "maximum": high},
             f"{name}字号相对 fontSize 的比例，{low}～{high} 的数字", _exact(low, high)),
        Rule(f"line{tier}FontFamily", FONT_FAMILY.example, f"{name}字体，字符串枚举，取值是字体库里的家族名，默认值是其中之一", _font_family),
        Rule(f"line{tier}FontWeight", {"type": "integer", "enum": [400, 700]}, f"{name}字重，只能是 400 和 700 两个枚举值", _weight),
    )


# 双栏字效的逐栏样式参数：只在 Agent 生成时必备（`fonts` 传入时检查），发布更早的多行版本不要求。
TIER_STYLES = tuple(rule for tier in range(1, TIERS + 1) for rule in _tier_rules(tier))
# 双栏各栏有自己的颜色、字重与字体，整段共用的这三个参数不再暴露；fontSize 仍是两栏共用的基准字号，宿主据此缩小长文字。
SHARED_STYLES = ("color", "fontWeight", FONT_FAMILY.name)
# 取值是字体库家族名的参数：保存时取消冻结的枚举，之后按实时字体库校验（`fonts.open_font_schema`、`fonts.require_family`）。
FONT_PARAMETERS = (FONT_FAMILY.name, *(f"line{tier}FontFamily" for tier in range(1, MAX_LINES + 1)))


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


# 自由组件样式参数的面板分组（字符串，同组参数在参数面板里成一张卡片）；和 title 一样只是说明，不约束取值。
PARAMETER_GROUP = "x-imv-group"
# 顶层参数允许的 Schema 关键字：发布时的边界抽样只认识这些约束，其他约束（如 multipleOf、exclusiveMaximum、
# 非颜色 pattern）会让两端取值不合 Schema 而漏抽。title、description、default 与面板分组只是说明。
_LABELS = {"type", "title", "description", "default", PARAMETER_GROUP}
_ALLOWED = {
    "number": _LABELS | {"minimum", "maximum", "enum"},
    "integer": _LABELS | {"minimum", "maximum", "enum"},
    "string": _LABELS | {"pattern", "enum", "minLength", "maxLength"},
    "boolean": _LABELS,
    "array": _LABELS | {"items", "maxItems"},
}
_ALLOWED_ITEMS = _LABELS | {"maxLength"}
# 行起点数组另有固定项数与逐项帧号范围；抽样保持它的默认值，不推两端。
_LINE_STARTS_ALLOWED = _ALLOWED["array"] | {"minItems"}
_LINE_STARTS_ITEMS = _LABELS | {"minimum", "maximum"}


def _unsupported(definition: dict, name: str) -> list[str]:
    """顶层参数里抽样无法处理的 Schema 关键字。"""
    lines = name == LINE_STARTS_PROP
    extra = set(definition) - (_LINE_STARTS_ALLOWED if lines else _ALLOWED.get(definition.get("type"), _LABELS))
    if definition.get("type") == "array" and isinstance(definition.get("items"), dict):
        extra |= {f"items.{key}" for key in set(definition["items"]) - (_LINE_STARTS_ITEMS if lines else _ALLOWED_ITEMS)}
    return sorted(extra)


def parameter_problems(definition: object, name: str) -> str | None:
    """顶层标准参数的通用约束：控件需要明确的取值范围，返回违反原因或 None。

    数字须声明上下界；字符串须是颜色、枚举或声明 maxLength；布尔不受限；数组只允许关键词形状与多行字效的行起点。
    字符串的 maxLength 不能超过模板覆盖值的上限：否则控件允许的文字保存不了，发布时的长文字样本也会无限膨胀。
    """
    if not isinstance(definition, dict):
        return "must be a JSON Schema object"
    if "$ref" in str(definition):
        return "must not use $ref"
    if extra := _unsupported(definition, name):
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
        if name == LINE_STARTS_PROP:
            return None if _line_starts(definition) else f"must be the line starts array of a two-tier text (双栏): {json.dumps(LINE_STARTS.example)}"
        return None if _keywords(definition) else f"only a keywords string array (and {LINE_STARTS_PROP}) is supported: {_ARRAY_HINT}"
    return "type must be number, integer, string, boolean or a keywords array"


def category_problems(
    kind: str | None, schema: dict, bound: set[str], defaults: dict, fonts: Collection[str] | None = None, requested: Collection[str] = (),
) -> list[Problem]:
    """列出某分类缺少、形状不符或默认值不合约定的必备参数；``bound`` 是作为顶层参数暴露的名称，``defaults`` 是默认参数。

    ``fonts`` 是字体库里的家族名：生成路径传入时，字效还必须暴露 `fontFamily` 枚举（取值都在字体库里），默认值是枚举之一；
    多行字效在生成路径为两行或四行双语，并以每行固定的颜色、字号比例、字体与字重代替整段共用的
    颜色、字重与字体；画了关键词字效的字效必须暴露默认开启的 `keywordEffect` 开关，文字颜色还必须不透明（透明由带可调
    数字参数的 `role=effect` 特效实现）。``requested`` 是新会话里用户打开的
    开关（`two_tier`、`keyword_effect`、`bilingual`），生成路径据此要求双栏、关键词字效或双语字效。发布更早的版本时都不传，
    不要求这些参数；多行字效在两条路径上都不要求整段共用的颜色与字重。
    """
    if kind not in REQUIRED:
        return [Problem("kind", None, f"kind must be one of {', '.join(KINDS)}")]
    properties = _properties(schema)
    tiered = kind == "text" and LINE_STARTS_PROP in bound
    # 双语字效：下栏是中文那一句的英文，英文以 `textEn` 单独注入，所以它必须同时是双栏。
    bilingual_sprite = kind == "text" and BILINGUAL_PROP in bound
    problems = []
    for rule in REQUIRED[kind]:
        if tiered and rule.name in SHARED_STYLES:
            continue
        if rule.name not in bound or rule.name not in properties:
            problems.append(Problem(rule.name, rule, "missing"))
        elif not rule.accepts(properties[rule.name]):
            problems.append(Problem(rule.name, rule, "has the wrong schema"))
        elif rule.off is not None and (rule.name not in defaults or not _same(defaults[rule.name], rule.off)):
            problems.append(Problem(rule.name, rule, f"{DEFAULT_REASON} {json.dumps(rule.off)} (off)"))
    if kind == "text" and fonts is not None:
        problems += _tier_problems(properties, bound, defaults, fonts) if tiered else _font_problems(FONT_FAMILY, properties, bound, defaults, fonts)
        problems += _keyword_effect_problems(properties, bound, defaults, "keyword_effect" in requested)
        problems += _opaque_colours(defaults, tuple(f"line{i}Color" for i in range(1, line_count(schema) + 1)) if tiered else ("color",))
        if {"two_tier", "bilingual"} <= set(requested) and (not bilingual_sprite or line_count(schema) != 4):
            problems.append(Problem(LINE_STARTS_PROP, LINE_STARTS, "双栏 + 双语字幕 requires two bilingual groups (four tiers: Chinese, English, Chinese, English); do not omit the second group"))
        if "two_tier" in requested and not tiered:
            problems.append(Problem(LINE_STARTS_PROP, LINE_STARTS, "missing: the user switched on 双栏 (original_request.two_tier), so make a two-tier "
                                    f"text whose default text has exactly {TIERS} lines and bind {LINE_STARTS_PROP}, {LINE_GAP_PROP} and the tier parameters"))
        if "bilingual" in requested and not bilingual_sprite:
            problems.append(Problem(BILINGUAL_PROP, BILINGUAL, "missing: the user switched on 双语字幕 (original_request.bilingual), so make a two-tier "
                                    f"text whose upper tier is the Chinese line and whose lower tier is its English translation (default text "
                                    f"\"<Chinese>\\n<English>\"), and bind {BILINGUAL_PROP} (the English line the bus injects), {LINE_STARTS_PROP}, "
                                    f"{LINE_GAP_PROP} and the tier parameters"))
        if bilingual_sprite and not tiered:
            problems.append(Problem(LINE_STARTS_PROP, LINE_STARTS, "missing: a bilingual text (双语字幕) is a two-tier text whose lower tier shows the "
                                    f"English line, so bind {LINE_STARTS_PROP} (minItems and maxItems {TIERS}), {LINE_GAP_PROP} and the tier parameters"))
    if bilingual_sprite and tiered:
        definition = properties.get(BILINGUAL_PROP)
        if not isinstance(definition, dict) or not BILINGUAL.accepts(definition):
            problems.append(Problem(BILINGUAL_PROP, BILINGUAL, "has the wrong schema"))
    if tiered:
        problems += _line_problems(properties, bound, defaults)
    elif kind == "text" and isinstance(defaults.get("text"), str) and "\n" in defaults["text"]:
        # A text that defaults to several lines is a multi-line text: without bound line starts the host would show the
        # lines as one, in one input and at one time.
        lines = len(defaults["text"].split("\n"))
        problems.append(Problem(LINE_STARTS_PROP, LINE_STARTS, f"missing: the default text has {lines} lines, so make it a two-tier text "
                                f"(双栏) with exactly {TIERS} lines and bind {LINE_STARTS_PROP} (minItems and maxItems {TIERS}), {LINE_GAP_PROP} "
                                "and the tier parameters as top-level parameters, or make the default text one line"))
    return problems


def _font_problems(rule: Rule, properties: dict, bound: set[str], defaults: dict, fonts: Collection[str]) -> list[Problem]:
    """字体参数（`fontFamily` 或某一栏的字体）：枚举取值都在字体库里，默认值是枚举之一。"""
    definition = properties.get(rule.name)
    if rule.name not in bound or definition is None:
        return [Problem(rule.name, rule, "missing")]
    if not rule.accepts(definition):
        return [Problem(rule.name, rule, "has the wrong schema")]
    if not set(definition["enum"]) <= set(fonts):
        unknown = sorted(set(definition["enum"]) - set(fonts))
        return [Problem(rule.name, rule, f"lists families that are not in the font library: {unknown}")]
    if defaults.get(rule.name) not in definition["enum"]:
        return [Problem(rule.name, rule, "must default to one of its enum values")]
    return []


def has_keyword_effect(bound: Collection[str]) -> bool:
    """字效画了关键词字效：文字组合带关键词 Preset（宿主暴露它的 keywordEffect 开关），或者绑定了以 keywordEffect 开头的外观参数。"""
    return any(name.startswith(KEYWORD_EFFECT_PROP) for name in bound)


def _keyword_effect_problems(properties: dict, bound: set[str], defaults: dict, requested: bool) -> list[Problem]:
    """关键词字效（生成路径）：用户打开了开关就必须画；画了就必须暴露默认开启的 `keywordEffect` 开关。"""
    if not has_keyword_effect(bound):
        return [Problem(KEYWORD_EFFECT_PROP, None, "missing: the user switched on the keyword effect (original_request.keyword_effect), so give the "
                        "keyword hit its own effect, bind its look as keywordEffect... parameters and bind the keywordEffect switch {\"type\":\"boolean\"}")] if requested else []
    if KEYWORD_EFFECT_PROP not in bound or KEYWORD_EFFECT_PROP not in properties:
        return [Problem(KEYWORD_EFFECT_PROP, KEYWORD_EFFECT, "missing")]
    if not KEYWORD_EFFECT.accepts(properties[KEYWORD_EFFECT_PROP]):
        return [Problem(KEYWORD_EFFECT_PROP, KEYWORD_EFFECT, "has the wrong schema")]
    if defaults.get(KEYWORD_EFFECT_PROP) is not True:
        return [Problem(KEYWORD_EFFECT_PROP, KEYWORD_EFFECT, "must default to true, so the keyword effect shows until the user switches it off")]
    return []


def _tier_problems(properties: dict, bound: set[str], defaults: dict, fonts: Collection[str]) -> list[Problem]:
    """生成的普通双栏为两行，双语为两行或四行；每行固定样式参数齐全，不暴露整段共用的颜色、字重与字体。"""
    problems = []
    definition = properties.get(LINE_STARTS_PROP)
    count = line_count({"properties": properties})
    allowed = (2, 4) if BILINGUAL_PROP in bound else (2,)
    if isinstance(definition, dict) and _line_starts(definition) and count not in allowed:
        problems.append(Problem(LINE_STARTS_PROP, LINE_STARTS, f"has {definition['maxItems']} lines: a multi-line text is a two-tier text (双栏) "
                                f"with exactly {TIERS} lines, minItems and maxItems both {TIERS}; put any further text into one of the two tiers"))
    rules = (*TIER_STYLES, *(_tier_rules(3) + _tier_rules(4) if count == 4 and BILINGUAL_PROP in bound else ()))
    for rule in rules:
        if rule.name in FONT_PARAMETERS:
            problems += _font_problems(rule, properties, bound, defaults, fonts)
        elif rule.name not in bound or rule.name not in properties:
            problems.append(Problem(rule.name, rule, "missing"))
        elif not rule.accepts(properties[rule.name]):
            problems.append(Problem(rule.name, rule, "has the wrong schema"))
    for name in SHARED_STYLES:
        if name in bound:
            problems.append(Problem(name, None, f"must not be bound for a two-tier text: each tier has its own line1{name[0].upper()}{name[1:]} "
                                    f"and line2{name[0].upper()}{name[1:]} (fontSize stays the shared base size)"))
    return problems


def _line_problems(properties: dict, bound: set[str], defaults: dict) -> list[Problem]:
    """多行字效的行起点与行间距：默认文字恰好 N 个非空行，默认行起点首项为 0 且不递减，预览才能逐行显示每一行；
    行间距必须作为顶层参数暴露。双语字效的两栏是同一句的中文与英文，默认值分别是单行的 `text` 与 `textEn`。"""
    problems = []
    if LINE_GAP_PROP not in bound or LINE_GAP_PROP not in properties:
        problems.append(Problem(LINE_GAP_PROP, LINE_GAP, "missing"))
    elif not LINE_GAP.accepts(properties[LINE_GAP_PROP]):
        problems.append(Problem(LINE_GAP_PROP, LINE_GAP, "has the wrong schema"))
    definition = properties.get(LINE_STARTS_PROP)
    if not isinstance(definition, dict) or not _line_starts(definition):
        return [Problem(LINE_STARTS_PROP, LINE_STARTS, "has the wrong schema"), *problems]
    count = definition["maxItems"]
    starts = defaults.get(LINE_STARTS_PROP)
    if not (
        isinstance(starts, list) and len(starts) == count and starts[0] == 0
        and all(isinstance(item, int) and not isinstance(item, bool) for item in starts)
        and all(left <= right for left, right in zip(starts, starts[1:]))
    ):
        problems.append(Problem(LINE_STARTS_PROP, LINE_STARTS, f"has the wrong default: {count} non-decreasing frames starting at 0"))
    if BILINGUAL_PROP in bound:
        # 原文、译文各有 count / 2 行，按相同序号配对；四行资产的组内间距单独可调。
        if count not in (2, 4):
            problems.append(Problem(LINE_STARTS_PROP, LINE_STARTS, "a bilingual text needs two or four tiers"))
        if count == 4:
            if TRANSLATION_GAP_PROP not in bound or not TRANSLATION_GAP.accepts(properties.get(TRANSLATION_GAP_PROP, {})):
                problems.append(Problem(TRANSLATION_GAP_PROP, TRANSLATION_GAP, "missing or has the wrong schema"))
            if isinstance(starts, list) and len(starts) == 4 and (starts[0] != starts[1] or starts[2] != starts[3]):
                problems.append(Problem(LINE_STARTS_PROP, LINE_STARTS, "each bilingual pair must start together, for example [0, 0, 30, 30]"))
        for name, rule in (("text", REQUIRED["text"][0]), (BILINGUAL_PROP, BILINGUAL)):
            value = defaults.get(name)
            if not isinstance(value, str) or len(parts := value.split("\n")) != count // 2 or not all(part.strip() for part in parts):
                problems.append(Problem(name, rule, f"has the wrong default: a bilingual text needs {count // 2} non-empty lines in each language, paired by line number"))
        return problems
    text = defaults.get("text")
    if not isinstance(text, str) or len(lines := text.split("\n")) != count or not all(line.strip() for line in lines):
        problems.append(Problem("text", REQUIRED["text"][0], f"has the wrong default: a multi-line text defaults to {count} non-empty lines separated by \\n"))
    return problems


def _opaque_colours(defaults: dict, names: tuple[str, ...]) -> list[Problem]:
    """生成路径的栏文字颜色必须是不透明的 #RRGGBB：半透明（#RRGGBBAA）看起来像字色，但不能像特效那样调整透明度。
    透出视频的字效应由 `role=effect` 的透明特效实现，并把透明度绑定为可调的数字样式参数；发布更早的版本不传字体库，不追溯拦截。"""
    return [
        Problem(name, None, "must be an opaque #RRGGBB colour: text that shows the video through is a transparency effect "
                "(role \"effect\") at that position, with a numeric opacity style parameter users can adjust, not a colour with alpha (#RRGGBBAA)")
        for name in names
        if isinstance(defaults.get(name), str) and re.fullmatch(r"#[0-9A-Fa-f]{8}", defaults[name])
    ]


def _same(value: object, expected: object) -> bool:
    """默认值与约定值相同；布尔与数字不互相等同（False 不等于 0），0.0 与 0 视为相同。"""
    return isinstance(value, bool) == isinstance(expected, bool) and value == expected


def keyword_default_issues(values: dict) -> list[tuple[str, object]]:
    """``values`` 里被改成非关闭值的关键词参数（名称与应有的关闭值）。

    关键词样式由云端模板按摆放设置，字效的这七个参数必须默认关闭；生成之后的参数修订也据此拒绝，
    否则会得到一个只能预览、无法保存到资产的版本。
    """
    return [(rule.name, rule.off) for rule in TEXT_KEYWORDS if rule.name in values and not _same(values[rule.name], rule.off)]


def agent_message(kind: str | None, problems: list[Problem]) -> str:
    """给 Agent 的英文说明：缺什么、应有的 Schema、如何在 sprite_compose 里绑定，以及关键词默认值要怎么修复。"""
    lines = [f"The {kind} Sprite does not expose the category's required top-level parameters:"]
    for item in problems:
        example = f"; schema {item.rule.example}" if item.rule else ""
        lines.append(f"- {item.name} {item.reason}{example}")
    lines.append(
        'Bind each one with sprite_compose "parameters": [{"name": ..., "title": <Chinese label>, '
        '"targets": [{"instance_id": ..., "parameter": <Preset prop>}]}] after giving the Preset prop that schema.'
    )
    if any(item.reason.startswith(DEFAULT_REASON) for item in problems):
        # 修复旧字效：总线每次渲染都会传入关键词与六个样式，外观留在关键词 Preset 自己的样式参数里。
        lines.append(
            "Keyword options are content the bus passes on every render, so a Sprite must not default them on. To repair an older "
            "effect, keep the keyword look in a keyword Preset's own style parameters (keywordEffect...) and give the keyword options "
            'their off defaults in this Sprite\'s default parameters: keywords [], the four switches false, keywordColor "", keywordSize 0.'
        )
    return "\n".join(lines)


def operator_message(problems: list[Problem]) -> str:
    """给运营的中文说明：缺少或形状不符的必备参数要在聊天中补充；默认值未关闭的关键词参数要在参数面板恢复并保存成新版本
    （已封存的版本不能直接修改，历史预览是只读的）。"""
    shape = [f"{item.name}（{item.rule.label}）" for item in problems if item.rule is not None and not item.reason.startswith(DEFAULT_REASON)]
    on = [f"{item.name}（应为 {json.dumps(item.rule.off, ensure_ascii=False)}）" for item in problems if item.rule is not None and item.reason.startswith(DEFAULT_REASON)]
    messages = []
    if shape:
        messages.append(f"所选分类缺少或不符合必备参数：{'；'.join(shape)}。请在聊天中让 Agent 补充后再保存到资产")
    if on:
        messages.append(f"关键词参数的默认值必须是关闭状态：{'；'.join(on)}。关键词样式由模板按摆放设置，已保存的版本不能直接修改："
                        "请在参数面板把它们恢复为关闭并点击「保存配置」生成新版本，再保存到资产（预览历史版本时参数面板只读，请先回到最新版本）；"
                        "也可以在聊天里让 Agent 重新生成一个关键词默认关闭的新版本")
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
