"""Host-provided effect components a text block names by ``builtin`` instead of a role effect Preset.

Each entry is a fixed motion the host draws itself (``compose.mjs``), so the model only names it and never
writes the Box/progress/glyph protocol for the common entrances, exits and loops. ``title``/``slot``/``target``
and (for enter/exit) ``seconds`` are the defaults an entry may omit; ``schema``/``defaults`` are the effect's own
tunable style props, which the model binds as top-level parameters like a Preset's. A glyph position still needs
an explicit ``stagger``.
"""

from typing import Literal

# Names the model may put in ``text.effects[].builtin``; compose.mjs has a component for each.
EffectBuiltin = Literal[
    "fade", "rise", "pop", "slideLeft", "slideRight", "slideUp", "slideDown", "blur", "glow", "breathing"
]

_COLOR = {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$", "title": "颜色"}
_DISTANCE = {"type": "number", "minimum": 0, "maximum": 200, "title": "距离"}

# name -> defaults: title (Chinese), slot, target, (enter/exit) length in seconds, and the tunable style props.
BUILTINS: dict[str, dict[str, object]] = {
    "fade": {"title": "淡入", "slot": "enter", "target": "block", "seconds": 0.5, "schema": {}, "defaults": {}},
    "rise": {"title": "上浮", "slot": "enter", "target": "block", "seconds": 0.5, "schema": {"distance": dict(_DISTANCE)}, "defaults": {"distance": 40}},
    "pop": {"title": "弹出", "slot": "enter", "target": "block", "seconds": 0.5,
            "schema": {"scale": {"type": "number", "minimum": 0, "maximum": 1, "title": "起始缩放"}}, "defaults": {"scale": 0.6}},
    "slideLeft": {"title": "左滑", "slot": "enter", "target": "block", "seconds": 0.5, "schema": {"distance": dict(_DISTANCE)}, "defaults": {"distance": 60}},
    "slideRight": {"title": "右滑", "slot": "enter", "target": "block", "seconds": 0.5, "schema": {"distance": dict(_DISTANCE)}, "defaults": {"distance": 60}},
    "slideUp": {"title": "上滑", "slot": "enter", "target": "block", "seconds": 0.5, "schema": {"distance": dict(_DISTANCE)}, "defaults": {"distance": 60}},
    "slideDown": {"title": "下滑", "slot": "enter", "target": "block", "seconds": 0.5, "schema": {"distance": dict(_DISTANCE)}, "defaults": {"distance": 60}},
    "blur": {"title": "模糊", "slot": "enter", "target": "block", "seconds": 0.5,
             "schema": {"amount": {"type": "number", "minimum": 0, "maximum": 30, "title": "模糊"}}, "defaults": {"amount": 8}},
    "glow": {"title": "发光", "slot": "loop", "target": "block",
             "schema": {"color": dict(_COLOR), "intensity": {"type": "number", "minimum": 0, "maximum": 30, "title": "强度"}},
             "defaults": {"color": "#FFFFFF", "intensity": 10}},
    "breathing": {"title": "呼吸", "slot": "loop", "target": "block",
                  "schema": {"amplitude": {"type": "number", "minimum": 0, "maximum": 0.2, "title": "幅度"}}, "defaults": {"amplitude": 0.04}},
}
