"""一次 Remotion 摆放的参数覆盖与宿主变换：模板保存与叠加渲染共用的解析和校验。

覆盖值保存在模板 remotion 轨道的 `editor.remotion`（`imv.template.v1.RemotionTrackEditor`）。这里只按发布记录里的
参数契约检查它们：参数存在且可编辑、类型与默认值相同、在声明的范围内，合并后的整份配置仍满足发布时的
JSON Schema；宿主变换只能设置该分类允许的字段且不能超出范围。发布时已经渲染过各参数与宿主变换的边界样本，
所以这里不再启动沙箱；没有渲染过样本的早期发布只接受宿主变换（它在代码之外），参数只读。
"""

from __future__ import annotations

import json
from copy import deepcopy

from generated.imv.sprite.v1 import sprite_pb2 as pb

from ..template.schema import RemotionTrackEditor, RemotionTransform
from .categories import KEYWORD_SIZE_MIN, transform_fields, transform_ranges
from .fonts import require_family
from .sprites import KIND_NAMES, box_json, operator_parameters, sampled
from .tools.schema import validate_parameters


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
    declared = {item.key: item for item in operator_parameters(sprite)}
    if editor.parameters:
        config = deepcopy(config)
        for override in editor.parameters:
            parameter = declared.get(override.key)
            if parameter is None:
                raise ValueError(f"参数 {override.key} 不存在")
            if parameter.access != pb.OPERATOR_ACCESS_VISIBLE_EDITABLE:
                if not sampled(sprite):
                    raise ValueError(f"参数 {override.key} 不可修改：该资产发布于参数边界检查之前，请在字效工作区改出新版本后保存为新资产")
                raise ValueError(f"参数 {override.key} 不可修改")
            value = _checked(parameter, override.value())
            if override.key == "fontFamily":
                # The library grows after publication, so a font is checked against it live, not against the schema.
                require_family(value)
            # Overridable parameters are top-level (only sampled publications take overrides, and they bind top-level).
            definition = schema.get("properties", {}).get(override.key, {})
            if isinstance(value, float) and value.is_integer() and isinstance(definition, dict) and definition.get("type") == "integer":
                value = int(value)
            config[override.key] = value
        # JSON Schema 表达不了「0 或 12～300」，与总线 IMS 模板一致的关键词字号下限在这里补查。
        size = config.get("keywordSize")
        if sprite.kind == pb.SPRITE_KIND_TEXT and isinstance(size, (int, float)) and not isinstance(size, bool) and 0 < size < KEYWORD_SIZE_MIN:
            raise ValueError(f"参数 keywordSize 只能是 0（沿用文字字号）或 {KEYWORD_SIZE_MIN}～300")
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
