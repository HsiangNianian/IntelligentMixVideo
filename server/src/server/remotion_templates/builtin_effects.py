"""Host-provided effect components a text block names by ``builtin`` instead of a role effect Preset.

Each entry is a fixed motion the host draws itself (``compose.mjs``), so the model only names it and never
writes the Box/progress/glyph protocol for the common entrances, exits and loops. The mapping records the
default Chinese title, slot, target and entrance/exit length an entry may omit; a glyph position still needs
an explicit ``stagger`` exactly like an effect Preset.
"""

from typing import Literal

# Names the model may put in ``text.effects[].builtin``; compose.mjs has a component for each.
EffectBuiltin = Literal[
    "fade", "rise", "pop", "slideLeft", "slideRight", "slideUp", "slideDown", "blur", "glow", "breathing"
]

# name -> defaults: title (Chinese), slot, target and (for enter/exit) length in seconds; loops have none.
BUILTINS: dict[str, dict[str, object]] = {
    "fade": {"title": "淡入", "slot": "enter", "target": "block", "seconds": 0.5},
    "rise": {"title": "上浮", "slot": "enter", "target": "block", "seconds": 0.5},
    "pop": {"title": "弹出", "slot": "enter", "target": "block", "seconds": 0.5},
    "slideLeft": {"title": "左滑", "slot": "enter", "target": "block", "seconds": 0.5},
    "slideRight": {"title": "右滑", "slot": "enter", "target": "block", "seconds": 0.5},
    "slideUp": {"title": "上滑", "slot": "enter", "target": "block", "seconds": 0.5},
    "slideDown": {"title": "下滑", "slot": "enter", "target": "block", "seconds": 0.5},
    "blur": {"title": "模糊", "slot": "enter", "target": "block", "seconds": 0.5},
    "glow": {"title": "发光", "slot": "loop", "target": "block"},
    "breathing": {"title": "呼吸", "slot": "loop", "target": "block"},
}
