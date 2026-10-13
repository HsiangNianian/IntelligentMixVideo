"""一次 Remotion 摆放的参数覆盖与宿主变换：模板保存与叠加渲染共用的解析和校验。

覆盖值保存在模板 remotion 轨道的 `editor.remotion`（`imv.template.v1.RemotionTrackEditor`）。这里只按发布记录里的
参数契约检查它们：参数存在且可编辑、类型与默认值相同、在声明的范围内，合并后的整份配置仍满足发布时的
JSON Schema；宿主变换只能设置该分类允许的字段且不能超出范围。发布时的边界抽样已经覆盖这些取值，
所以这里不再启动沙箱。
"""

from __future__ import annotations

import json
from copy import deepcopy

from generated.imv.sprite.v1 import sprite_pb2 as pb

from ..template.schema import RemotionTrackEditor, RemotionTransform
from .categories import transform_fields, transform_ranges
from .sprites import KIND_NAMES, box_json
from .tools.schema import validate_parameters


def _definition(schema: dict, path: list[str]) -> dict:
    """按点路径读取配置 Schema 中的属性定义；未声明时为空。"""
    for part in path:
        properties = schema.get("properties") if isinstance(schema, dict) else None
        schema = properties.get(part, {}) if isinstance(properties, dict) else {}
    return schema if isinstance(schema, dict) else {}


def _assign(config: dict, path: list[str], value) -> None:
    """按点路径写入配置；中间对象缺失时报错，而不是凭空创建。"""
    target = config
    for part in path[:-1]:
        target = target.get(part) if isinstance(target, dict) else None
        if not isinstance(target, dict):
            raise ValueError(f"参数 {'.'.join(path)} 在默认配置中不存在")
    target[path[-1]] = value


def _checked(declared: pb.SpriteParameter, value):
    """按发布时的参数声明检查一个覆盖值，返回可写入配置的值。"""
    key = declared.key
    expected = declared.default_value.WhichOneof("value")
    actual = (
        "string_list_value" if isinstance(value, list)
        else "bool_value" if isinstance(value, bool)
        else "number_value" if isinstance(value, float)
        else "string_value"
    )
    if expected != actual:
        raise ValueError(f"参数 {key} 的值类型与默认值不同")
    if actual == "number_value":
        if declared.format == pb.SPRITE_PARAMETER_FORMAT_INTEGER and not value.is_integer():
            raise ValueError(f"参数 {key} 必须是整数")
        if declared.HasField("minimum") and value < declared.minimum or declared.HasField("maximum") and value > declared.maximum:
            raise ValueError(f"参数 {key} 超出允许范围")
    items = value if actual == "string_list_value" else [value] if actual == "string_value" else []
    for item in items:
        if declared.HasField("min_length") and len(item) < declared.min_length or declared.HasField("max_length") and len(item) > declared.max_length:
            raise ValueError(f"参数 {key} 的文字长度超出允许范围")
    if actual == "string_list_value" and declared.HasField("max_items") and len(value) > declared.max_items:
        raise ValueError(f"参数 {key} 最多 {declared.max_items} 项")
    allowed = [getattr(item, item.WhichOneof("value")) for item in declared.allowed_values if item.WhichOneof("value") == actual]
    if declared.allowed_values and value not in allowed:
        raise ValueError(f"参数 {key} 不是允许的取值")
    return value


def resolve(sprite: pb.PublishedSprite, editor: RemotionTrackEditor | None) -> tuple[dict, dict]:
    """把一次摆放的覆盖合并到发布默认配置，并给出宿主层的内容区域与变换；不合契约时抛出中文 ValueError。"""
    config = json.loads(sprite.default_config_json or "{}")
    schema = json.loads(sprite.config_schema_json or "{}")
    editor = editor or RemotionTrackEditor()
    declared = {item.key: item for item in sprite.parameters}
    if editor.parameters:
        config = deepcopy(config)
        for override in editor.parameters:
            parameter = declared.get(override.key)
            if parameter is None:
                raise ValueError(f"参数 {override.key} 不存在")
            if parameter.access != pb.OPERATOR_ACCESS_VISIBLE_EDITABLE:
                raise ValueError(f"参数 {override.key} 不可修改")
            value = _checked(parameter, override.value())
            path = override.key.split(".")
            if isinstance(value, float) and value.is_integer() and _definition(schema, path).get("type") == "integer":
                value = int(value)
            _assign(config, path, value)
        try:
            validate_parameters(schema, config)
        except Exception as exc:
            raise ValueError(f"参数覆盖不符合发布契约：{str(exc)[:200]}") from None
    transform = (editor.transform or RemotionTransform()).model_dump(exclude_none=True)
    # Historical publications without a kind move like a visual effect.
    allowed = transform_fields(KIND_NAMES.get(sprite.kind))
    if extra := sorted(set(transform) - allowed):
        raise ValueError(f"该类型的资产不能设置 {'、'.join(extra)}")
    ranges = transform_ranges(sprite.canvas.width, sprite.canvas.height)
    for name, value in transform.items():
        low, high = ranges[name]
        if not low <= value <= high:
            raise ValueError(f"{name} 超出允许范围 {low:g}～{high:g}")
    return config, {"box": box_json(sprite), "transform": transform}
