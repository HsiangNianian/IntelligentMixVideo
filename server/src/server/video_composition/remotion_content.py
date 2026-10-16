"""动态字效参数契约：解析显式字段声明并注入业务文字、首次关键词区间、多行字效的行起点和实际帧数。

读取上游发布的正文/关键词路径，兼容已有 x-imv-role 声明，不修改生成或发布流程。
固定时长资产由合成宿主映射播放时钟；已声明动态时长的资产继续接收实际帧数。
"""
from copy import deepcopy
import json
from ..remotion_templates.categories import LINE_STARTS_PROP, line_count
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
                   line_starts: list[int] | None = None) -> dict:
    """克隆资产默认值并覆盖全部业务字段；空关键词覆盖示例，源码和样式保留。

    多行字效的 ``text`` 用换行分隔各行，``keyword`` 是各行关键词的列表，``line_starts`` 是各行出现的帧：
    资产声明了 `lineStartFrames` 时写入并补足到它的固定项数（补的项沿用最后一行，没有文字的行不会显示）。
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
    for path in mapping["duration"]:
        set_path(config, path, frames)
    try:
        validate_parameters(schema, config)
    except Exception as exc:
        raise ContentError("业务文字或时间不符合资产参数契约") from exc
    return config
