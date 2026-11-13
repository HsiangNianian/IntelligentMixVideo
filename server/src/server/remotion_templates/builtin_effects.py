"""Names retained only to deserialize historical Sprite metadata; generation has no built-in effect recipes."""

from typing import Literal

EffectBuiltin = Literal[
    "fade", "rise", "pop", "slideLeft", "slideRight", "slideUp", "slideDown", "blur", "glow", "breathing"
]
