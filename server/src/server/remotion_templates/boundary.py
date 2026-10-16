"""发布时的边界抽样：用默认值、各参数的两端取值、中间枚举值和宿主变换的两端，在隔离浏览器里各渲染几帧。

通过后，模板里的修改只做即时的 Schema 校验，不再启动沙箱。抽样只覆盖边界，不是穷举证明：
组合样本把所有参数和宿主变换同时推到一端；它不满足 Schema 时（参数之间互相约束），改为逐步加入仍合法的一端取值
得到的组合，以及每个参数单独推到一端的样本，它们都带同一端的宿主变换。字效另渲染 ``keywords:styled``：
低端（最长文字、关键词全部命中）再把关键词样式全部推到高端，覆盖「有命中且样式开启」的路径。仍在实际渲染中出错的摆放，
由叠加渲染任务失败并报告。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .categories import HEX_COLOR, KEYWORD_COLOR, TEXT_KEYWORDS, transform_fields, transform_ranges
from .tools.schema import validate_parameters

# 文字填充字符：CJK 字形最宽、最容易溢出，最能暴露排版问题。
FILLER = "测"
# 退回逐个参数时最多生成的样本数，避免发布时间随参数数量线性增长。
MAX_SINGLE_SAMPLES = 12
# 枚举最多再抽的中间取值个数。
MAX_ENUM_SAMPLES = 4

# 关键词样式参数（加粗等开关、颜色、字号），不含关键词列表本身。
KEYWORD_STYLE_NAMES = {rule.name for rule in TEXT_KEYWORDS} - {"keywords"}


@dataclass(frozen=True)
class Sample:
    """一组要渲染的完整参数与宿主变换。"""

    name: str
    values: dict
    transform: dict = field(default_factory=dict)


def _extreme(definition: dict, default, high: bool):
    """一个顶层参数在一端的取值；无法推出极端值时保持默认值。"""
    kind = definition.get("type")
    choices = definition.get("enum")
    if isinstance(choices, list) and choices:
        return choices[-1] if high else choices[0]
    if kind in {"number", "integer"} and "minimum" in definition and "maximum" in definition:
        return definition["maximum"] if high else definition["minimum"]
    if kind == "boolean":
        return high
    if kind == "string":
        if "pattern" in definition:
            if definition["pattern"] == KEYWORD_COLOR:
                return "#FFFFFF" if high else ""
            return ("#FFFFFF" if high else "#000000") if definition["pattern"] == HEX_COLOR else default
        if isinstance(definition.get("maxLength"), int):
            return FILLER * (max(1, definition.get("minLength", 1)) if high else definition["maxLength"])
        return default
    if kind == "array":
        # A multi-line text's line starts are content the host injects, not a style: keep the asset's own schedule.
        if (definition.get("items") or {}).get("type") == "integer":
            return default
        # Keywords: every slot filled with a word that hits the filler text everywhere, or none at all.
        if high or not isinstance(definition.get("maxItems"), int):
            return []
        return [FILLER] * definition["maxItems"]
    return default


def _valid(schema: dict, values: dict) -> bool:
    """样本满足版本的参数 Schema。"""
    try:
        validate_parameters(schema, values)
        return True
    except Exception:
        return False


def _transform(kind: str | None, canvas: tuple[int, int], high: bool) -> dict:
    """该分类允许的宿主变换字段全部取一端。"""
    ranges = transform_ranges(*canvas)
    return {name: ranges[name][1 if high else 0] for name in sorted(transform_fields(kind))}


def unsampled(schema: dict, defaults: dict, names: list[str], drawn: list[Sample]) -> list[str]:
    """Top-level parameters whose low or high end or one of whose enum values no sample renders.

    Bound parameters only use constraints the extremes respect, so this only happens when root-level constraints
    tie parameters together so tightly that even one parameter's extreme does not fit with the defaults.
    """
    properties = schema.get("properties", {})
    missing = []
    for name in names:
        definition = properties.get(name, {})
        wanted = [_extreme(definition, defaults.get(name), high) for high in (False, True)]
        wanted += definition.get("enum") or []
        if any(not any(item.values.get(name) == value for item in drawn) for value in wanted):
            missing.append(name)
    return missing


def samples(schema: dict, defaults: dict, names: list[str], kind: str | None, canvas: tuple[int, int]) -> list[Sample]:
    """为版本的顶层参数 ``names`` 生成边界样本；第一个总是默认值。"""
    properties = schema.get("properties", {})
    result = [Sample("defaults", defaults)]
    for label, high in (("low", False), ("high", True)):
        changes = {name: _extreme(properties.get(name, {}), defaults.get(name), high) for name in names}
        combined = {**defaults, **changes}
        if _valid(schema, combined):
            result.append(Sample(label, combined, _transform(kind, canvas, high)))
            continue
        # Parameters constrain each other: keep every extreme that still fits, then each extreme on its own. All of
        # them carry the same end of the host transform, so no parameter extreme is only rendered untransformed.
        transform = _transform(kind, canvas, high)
        greedy = dict(defaults)
        for name, value in changes.items():
            if _valid(schema, trial := {**greedy, name: value}):
                greedy = trial
        result.append(Sample(f"{label}:combined", greedy, transform))
        singles = 0
        for name, value in changes.items():
            single = {**defaults, name: value}
            if value != defaults.get(name) and _valid(schema, single) and singles < MAX_SINGLE_SAMPLES:
                result.append(Sample(f"{label}:{name}", single, transform))
                singles += 1
    longest = max((len(properties.get(name, {}).get("enum") or []) for name in names), default=0)
    for index in range(1, min(longest - 1, MAX_ENUM_SAMPLES + 1)):
        changes = {
            name: choices[min(index, len(choices) - 1)]
            for name in names
            if isinstance(choices := properties.get(name, {}).get("enum"), list) and choices
        }
        values = {**defaults, **changes}
        if _valid(schema, values):
            result.append(Sample(f"enum:{index}", values))
    # "low" fills every keyword slot with the styles off and "high" turns the styles on without keywords, so neither
    # renders a hit with its styling: render the low end again with every keyword style at its high end.
    styled = [name for name in names if name in KEYWORD_STYLE_NAMES]
    if "keywords" in names and styled:
        values = {
            **defaults,
            **{name: _extreme(properties.get(name, {}), defaults.get(name), False) for name in names},
            **{name: _extreme(properties.get(name, {}), defaults.get(name), True) for name in styled},
        }
        if _valid(schema, values):
            result.append(Sample("keywords:styled", values, _transform(kind, canvas, False)))
    unique = {}
    for item in result:
        unique.setdefault(json.dumps([item.values, item.transform], sort_keys=True, ensure_ascii=False), item)
    return list(unique.values())
