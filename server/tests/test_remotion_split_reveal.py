"""中线分片回归：真实宿主组合、排版门禁及白字退场/橙字留下的像素结果。

在 server/ 设置 IMV_TEST_RENDERER=1、浏览器与字体路径后运行本文件；数据库使用测试夹具，不调用模型。
"""

import asyncio

import pytest
from PIL import Image

from server.remotion_templates.agent_examples import SPLIT_CODE
from server.remotion_templates.renderer import Renderer
from server.remotion_templates.settings import Settings
from server.remotion_templates.tool_validation import ToolValidator
from server.remotion_templates.tools.catalog import available
from server.remotion_templates.tools.contracts import SpriteComposeInput
from server.remotion_templates.tools.registry import ToolFault

from .test_remotion_text_block import BROWSER, CHILD_TIER, FADE_CODE, effect_preset, needs_browser, preset, session

pytestmark = pytest.mark.usefixtures("template_db")


async def split_scene(tmp_path, tier_code=CHILD_TIER, split_code=SPLIT_CODE, split_slot="cue"):
    """同一句白字先完整出现，下栏出现时白字分片离开，保持两个文字层的原始排版。"""
    current = session(tmp_path)
    tier = await current.execute("preset.create", preset("text", tier_code, "color"), available())
    fade = await current.execute("preset.create", effect_preset(FADE_CODE), available())
    split = effect_preset(split_code, usage={"title": "分片退场", "slot": "cue", "target": "line1", "seconds": 0.6})
    split.parameter_schema["properties"]["distance"] = {"type": "number", "minimum": 0, "maximum": 2}
    split.parameter_schema["required"] = ["distance"]
    split.default_parameters["distance"] = 0.8
    cut = await current.execute("preset.create", split, available())

    def source(receipt):
        """组合只引用刚存入隔离目录的 Preset。"""
        return {"kind": "stored", "preset_id": receipt["preset"]["preset_id"]}

    result = await current.execute("sprite.compose", SpriteComposeInput.model_validate({
        "description": "完整白字从中线分为上下两半，移开消失，露出橙字", "kind": "text", "parameters": [],
        "text": {
            "layout": {"x": 60, "y": 700, "width": 960, "height": 520}, "text": "千辛万苦想赚钱\n想钱生钱",
            "font_size": 132, "duration_frames": 150, "arrangement": "overlap", "line_starts": [0, 30],
            "tiers": [
                {"instance_id": "upper", "source": source(tier), "parameters": {"color": "#FFFFFF"},
                 "font_family": "Noto Sans CJK SC", "font_weight": 700, "size_ratio": 0.8},
                {"instance_id": "lower", "source": source(tier), "parameters": {"color": "#F08A2B"},
                 "font_family": "Noto Sans CJK SC", "font_weight": 700, "size_ratio": 1},
            ],
            "effects": [
                {"instance_id": "reveal", "source": source(fade), "target": "line2", "seconds": 0.3},
                {"instance_id": "split", "source": source(cut), "slot": split_slot},
                {"instance_id": "out", "source": source(fade), "slot": "exit", "target": "block", "seconds": 0.5},
            ],
        },
    }), available())
    validator = ToolValidator(Renderer(Settings(_env_file=None, data_dir=tmp_path, **BROWSER)), tmp_path / "validation")
    current.validate_render = validator.validate_render
    return current, current.sprite_drafts[result["draft_id"]]


def colored_pixels(path, orange=False):
    """排除回看灰底，读取白字或橙字的实际像素位置。"""
    with Image.open(path).convert("RGB") as image:
        return [(x, y) for y in range(image.height) for x in range(image.width)
                if (lambda r, g, b: r > 220 and (80 < g < 190 and b < 100 if orange else g > 220 and b > 220))(*image.getpixel((x, y)))]


@needs_browser
def test_split_example_passes_layout_and_reveals_the_lower_tier(tmp_path):
    """正确分片须能通过真实生成门禁，回看包括切开前、过程中、完成后和末帧；橙字留下，白字确实消失。"""
    from server.remotion_templates.agent import EXECUTOR_RULES

    assert SPLIT_CODE in EXECUTOR_RULES
    async def run():
        current, sprite = await split_scene(tmp_path)
        return await current._require_visible_video(sprite)

    shots = {frame: path for path, frame, _ in asyncio.run(run())}
    assert {29, 39, 48, 149} <= shots.keys()
    before, during = colored_pixels(shots[29]), colored_pixels(shots[39])
    assert before and during
    assert min(y for _, y in during) < min(y for _, y in before) - 4
    assert max(y for _, y in during) > max(y for _, y in before) + 4
    assert not colored_pixels(shots[29], orange=True)
    assert colored_pixels(shots[39], orange=True)
    assert not colored_pixels(shots[48]) and colored_pixels(shots[48], orange=True)
    assert not colored_pixels(shots[149]) and not colored_pixels(shots[149], orange=True)


@needs_browser
@pytest.mark.parametrize("fault", ["clip", "throw", "reversed_exit"])
def test_split_layout_check_keeps_real_clipping_and_animation_errors(tmp_path, fault):
    """文字层永久裁切、特效抛错及把 cue 反挂为 exit（正常停留时整栏消失）仍然不能保存。"""
    async def run():
        tier = CHILD_TIER.replace('width: "100%"', 'height: 5, overflow: "hidden", width: "100%"') if fault == "clip" else CHILD_TIER
        code = SPLIT_CODE.replace("const p =", 'if (props.progress > 0.5) throw new Error("split failed"); const p =') if fault == "throw" else SPLIT_CODE
        current, sprite = await split_scene(tmp_path, tier, code, "exit" if fault == "reversed_exit" else "cue")
        with pytest.raises(ToolFault) as error:
            await current._require_visible_video(sprite)
        assert error.value.error.code == "CODE_VALIDATION_FAILED"
        assert ("split failed" if fault == "throw" else "text_fit") in error.value.error.message

    asyncio.run(run())
