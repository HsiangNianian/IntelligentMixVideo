"""动态字效参数契约：解析显式字段声明并注入业务文字、首次关键词区间、多行字效的行起点和实际帧数。

读取上游发布的正文/关键词路径，兼容已有 x-imv-role 声明，供真实字幕和容量样本共用；不修改生成或发布流程。
固定时长资产由合成宿主映射播放时钟；已声明动态时长的资产继续接收实际帧数。
"""
from copy import deepcopy
import json
import math
from ..remotion_templates.categories import BILINGUAL_PROP, LINE_STARTS_PROP, TEXT_MAX_LENGTH, bilingual as is_bilingual, line_count
from ..remotion_templates.fonts import open_font_schema
from ..remotion_templates.tools.schema import validate_parameters


class ContentError(ValueError):
    """资产尚未支持业务输入或最终参数不符合其发布契约。"""


def fields(schema: dict, prefix: str = ""):
    """遍历明确的对象属性；含点的键无法用点路径表达，不猜测数组或引用。"""
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return
    for name, definition in properties.items():
        if "." in name or not isinstance(definition, dict):
            continue
        path = prefix + name
        yield path, definition
        if definition.get("type") == "object":
            yield from fields(definition, path + ".")


def roles(schema: dict) -> dict[str, list[str]]:
    """读取显式业务角色并校验类型；关键词数组格式随后按叶子 Schema 解析。"""
    if "x-imv-dynamic-duration" in schema and type(schema["x-imv-dynamic-duration"]) is not bool:
        raise ContentError("动态时长声明必须是布尔值")
    result = {key: [] for key in ("text", "keywords", "duration")}
    for path, field in fields(schema):
        role = field.get("x-imv-role")
        if role is None:
            continue
        if not isinstance(role, str) or role not in result or field.get("type") != {"text":"string", "keywords":"array", "duration":"integer"}.get(role):
            raise ContentError("资产业务参数声明无效：" + path)
        result[role].append(path)
    return result


def dynamic_duration(schema: dict) -> bool:
    """只有明确声明的资产直接读取业务时长；上游固定资产走宿主时钟适配。"""
    mapping = roles(schema)
    return schema.get("x-imv-dynamic-duration") is not False and (
        schema.get("x-imv-dynamic-duration") is True or bool(mapping["duration"])
    )


def set_path(config: dict, path: str, value) -> None:
    """只更新已由 Schema 确认的对象路径；不修改发布默认值。"""
    parts = path.split(".")
    for part in parts[:-1]:
        if not isinstance(config.get(part), dict):
            raise ContentError("资产默认配置缺少对象：" + path)
        config = config[part]
    config[parts[-1]] = value


def first_range(text: str, keyword: str) -> list[list[int]]:
    """最终显示纯文本内仅高亮关键词首次连续命中，不恢复切分时丢弃的关键词。"""
    start = text.find(keyword) if keyword else -1
    return [[start, start + len(keyword)]] if start >= 0 else []


def resolve_config(sprite, text: str, keyword: str | list[str], frames: int,
                   text_prop: str = "", keywords_prop: str = "", *, config: dict | None = None,
                   line_starts: list[int] | None = None, english: str | None = None) -> dict:
    """克隆资产默认值并覆盖全部业务字段；空关键词覆盖示例，源码和样式保留。

    多行字效的 ``text`` 用换行分隔各行，``keyword`` 是各行关键词的列表，``line_starts`` 是各行出现的帧：
    资产声明了 `lineStartFrames` 时写入并补足到它的固定项数（补的项沿用最后一行，没有文字的行不会显示）。
    ``english`` 是双语字效下栏的英文：声明了 `textEn` 的资产同时写入，两种语言一起显示。
    """
    schema = json.loads(sprite.config_schema_json)
    mapping = roles(schema)
    definitions = dict(fields(schema))
    text_path = text_prop or sprite.text_prop
    keyword_path = keywords_prop or sprite.keywords_prop
    if text_path:
        mapping["text"] = list(dict.fromkeys([*mapping["text"], text_path]))
    if keyword_path:
        mapping["keywords"] = list(dict.fromkeys([*mapping["keywords"], keyword_path]))
    if not mapping["text"]:
        raise ContentError("资产尚未适配业务文字，请声明正文参数")
    config = deepcopy(json.loads(sprite.default_config_json) if config is None else config)
    for path in mapping["text"]:
        if definitions.get(path, {}).get("type") != "string":
            raise ContentError("正文参数必须是字符串：" + path)
        set_path(config, path, text)
    # 只注入在正文里命中的词，多行字效的各行关键词去重后保持先后。
    hits = [word for word in dict.fromkeys([keyword] if isinstance(keyword, str) else keyword) if first_range(text, word)]
    for path in mapping["keywords"]:
        kind = definitions.get(path, {}).get("type")
        if kind not in ("string", "array"):
            raise ContentError("关键词参数必须是字符串或数组：" + path)
        # 旧字符串字段接收实际命中的词；数组按已封存契约区分词列表与码点区间。
        value = (hits[0] if hits else "") if kind == "string" else [item for word in hits for item in first_range(text, word)]
        if kind == "array" and definitions[path].get("items", {}).get("type") == "string":
            # 上游新分类契约使用词列表；旧区间数组仍按其显式 Schema 注入。
            value = hits
        set_path(config, path, value)
    if (count := line_count(schema)) > 1:
        starts = (line_starts or [0])[:count]
        set_path(config, LINE_STARTS_PROP, starts + [starts[-1]] * (count - len(starts)))
    if english is not None:
        # 双语字效的下栏是同一句的英文：只有声明了 `textEn` 的资产收得下，其他字效不能当双语的用。
        if definitions.get(BILINGUAL_PROP, {}).get("type") != "string":
            raise ContentError("资产尚未适配双语文字：" + BILINGUAL_PROP)
        set_path(config, BILINGUAL_PROP, english)
    for path in mapping["duration"]:
        set_path(config, path, frames)
    try:
        # 摆放的字体已按实时字体库校验（`placement.resolve`），较早发布里冻结的字体枚举不再限制它。
        validate_parameters(open_font_schema(schema), config)
    except Exception as exc:
        raise ContentError("业务文字或时间不符合资产参数契约") from exc
    return config


def capacity_samples(sprite, config: dict, frames: int) -> tuple[int, list[dict]]:
    """按封存业务契约构造有界全角样本；每栏同时出现，正文/关键词各测一次，不改默认参数。

    字数按每栏计，受所有正文路径的 maxLength 限制（含换行）；关键词遵守旧字段的长度/项数约束。
    实际字幕和关键词尚未生成，这些样本只供切片容量参考，不替代最终排版预检。
    """
    schema = json.loads(sprite.config_schema_json)
    mapping = roles(schema)
    definitions = dict(fields(schema))
    text_paths = list(dict.fromkeys([*mapping["text"], *([sprite.text_prop] if sprite.text_prop else [])]))
    keyword_paths = list(dict.fromkeys([*mapping["keywords"], *([sprite.keywords_prop] if sprite.keywords_prop else [])]))
    if not text_paths:
        raise ContentError("资产尚未适配业务文字，请声明正文参数")
    # 双语字效（双语字幕）的每条切片只放一行中文，英文由 `textEn` 单独注入：容量按单行中文测量，不按两栏分行。
    lines = 1 if is_bilingual(schema) else line_count(schema)
    maximum = min(TEXT_MAX_LENGTH, *(definitions[path].get("maxLength", TEXT_MAX_LENGTH) for path in text_paths))
    minimum = max(definitions[path].get("minLength", 1) for path in text_paths)
    keyword_length, keyword_count = TEXT_MAX_LENGTH, lines
    for path in keyword_paths:
        field = definitions[path]
        keyword_count = min(keyword_count, field.get("maxItems", 1 if field.get("type") == "string" else lines))
        keyword_length = min(keyword_length, (field.get("items", {}) if field.get("type") == "array" else field).get("maxLength", TEXT_MAX_LENGTH))
    samples = []
    for chars in range(max(1, math.ceil((minimum - lines + 1) / lines)), (maximum - lines + 1) // lines + 1):
        # 各栏用不同全角字，避免旧关键词区间只高亮第一栏的同名词。
        texts = [chr(ord("测") + index) * chars for index in range(lines)]
        text = "\n".join(texts)
        plain = resolve_config(sprite, text, [], frames, config=config)
        highlighted = resolve_config(sprite, text, [part[:keyword_length] for part in texts[:keyword_count]], frames, config=config)
        samples.append({"chars": chars, "text": text, "values": [plain] if highlighted == plain else [plain, highlighted]})
    if not samples:
        raise ContentError("资产正文长度契约无法容纳全角测量样本")
    return lines, samples
