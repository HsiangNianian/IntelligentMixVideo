"""PR74 fixture factory for historical version, desktop bundle and Sprite compatibility tests."""

from server.remotion_templates.models import TemplateSpec


def controls(spec: TemplateSpec) -> tuple[dict, dict]:
    """Expose each scalar text/layout/style value using stable keys and local-only schema bindings."""
    properties, defaults = {}, {}

    def walk(value, parts: list[str]) -> None:
        """Flatten existing scalar leaves; structure and timing edits require code regeneration."""
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, parts + [key])
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, parts + [str(index)])
        else:
            name = "_".join(parts[1:])
            definition = {
                "type": "boolean"
                if isinstance(value, bool)
                else "string"
                if isinstance(value, str)
                else "number",
                "x-imv-target": "/" + "/".join(parts),
            }
            if parts[0] == "visual_parameters":
                definition["title"] = {
                    "brightness": "亮度",
                    "contrast": "对比度",
                    "saturation": "饱和度",
                    "opacity": "透明度",
                    "intensity": "强度",
                }.get(parts[-1], parts[-1])
                if definition["type"] == "number":
                    definition.update(
                        minimum=0
                        if parts[-1]
                        in {
                            "brightness",
                            "contrast",
                            "saturation",
                            "opacity",
                            "intensity",
                        }
                        else -1000,
                        maximum=1
                        if parts[-1] in {"opacity", "intensity"}
                        else 2
                        if parts[-1] in {"brightness", "contrast", "saturation"}
                        else 1000,
                    )
                elif definition["type"] == "string":
                    definition["maxLength"] = 100
            elif parts[-1] == "font_size":
                definition.update(minimum=1, maximum=600)
            elif parts[-1] in {"x", "y"} and "layout" in parts:
                definition.update(minimum=0, maximum=1)
            elif parts[-1] == "width" and "layout" in parts:
                definition.update(minimum=0.0001, maximum=1)
            elif parts[-1] == "line_height":
                definition.update(minimum=0.5, maximum=3)
            if parts[-1] == "font_family":
                definition["enum"] = ["Noto Sans CJK SC"]
            if parts[-1] == "font_weight":
                definition["enum"] = [400, 700]
            if parts[-1] == "align":
                definition["enum"] = ["left", "center", "right"]
            properties[name], defaults[name] = definition, value

    for index, layer in enumerate(spec.text_layers):
        for field in ("text", "layout", "style"):
            walk(layer.model_dump()[field], ["text_layers", str(index), field])
    if spec.sprite_kind not in {"text", "subtitle"}:
        walk(spec.visual_parameters, ["visual_parameters"])
    schema = {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
    return schema, defaults
