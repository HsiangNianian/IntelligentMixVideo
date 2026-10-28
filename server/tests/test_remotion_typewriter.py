"""打字机回归：明确逐字时序、复用时保留时序、真实浏览器逐帧核对显字和光标。

在 server/ 下运行 `pytest tests/test_remotion_typewriter.py`；像素用例另设 IMV_TEST_RENDERER=1 及浏览器、字体路径。
目录使用临时 SQLite，浏览器使用真实渲染沙箱，不调用模型或业务数据库。
"""

import asyncio

import pytest
from PIL import Image, ImageChops

from server.remotion_templates.renderer import Renderer
from server.remotion_templates.settings import Settings
from server.remotion_templates.tool_validation import FrameCapture, ToolValidator
from server.remotion_templates.tools.catalog import available
from server.remotion_templates.tools.compose import compose_text_source
from server.remotion_templates.tools.contracts import ComponentDefinition, RenderValidationInput, SpriteComposeInput
from server.remotion_templates.tools.registry import ToolFault

from .test_remotion_text_block import BROWSER, CHILD_TIER, COLOR, FADE_CODE, effect_preset, needs_browser, needs_bundler, preset, session

pytestmark = pytest.mark.usefixtures("template_db")


@needs_bundler
def test_glyph_timing_must_be_chosen_instead_of_turning_a_fade_into_typing(tmp_path):
    """实际失败配置：关键词指定依次出现，非关键词复用普通淡入却漏填 stagger；须明确纠错，不能偷偷默认逐字。"""
    async def run():
        current = session(tmp_path)
        body = await current.execute("preset.create", preset("text", CHILD_TIER, "color"), available())
        fade = await current.execute("preset.create", effect_preset(FADE_CODE), available())
        source = {"kind": "stored", "preset_id": fade["preset"]["preset_id"]}
        request = {"description": "关键词先打出，其余一起淡入", "kind": "text", "parameters": [], "text": {
            "layout": {"x": 0, "y": 0, "width": 180, "height": 80}, "text": "甲乙丙丁", "font_size": 40, "duration_frames": 60,
            "tiers": [{"instance_id": "body", "source": {"kind": "stored", "preset_id": body["preset"]["preset_id"]},
                       "font_family": "Noto Sans CJK SC", "font_weight": 700}],
            "effects": [
                {"instance_id": "typeIn", "source": source, "target": "keywordChars", "stagger": 1},
                {"instance_id": "plainIn", "source": source, "target": "plainChars", "seconds": 0.2, "delay": 2 / 3},
                {"instance_id": "fadeOut", "source": source, "slot": "exit"},
            ],
        }}
        with pytest.raises(ToolFault) as refused:
            await current.execute("sprite.compose", SpriteComposeInput.model_validate(request), available())
        assert refused.value.error.field == "/text/effects/1/stagger"
        assert "0" in refused.value.error.message and "1" in refused.value.error.message
        request["text"]["effects"][1]["stagger"] = 0
        result = await current.execute("sprite.compose", SpriteComposeInput.model_validate(request), available())
        values = current.sprite_drafts[result["draft_id"]].default_parameters
        assert (values["effect1Stagger"], values["effect2Stagger"]) == (1, 0)

    asyncio.run(run())


@needs_browser
def test_typewriter_example_is_checked_with_the_hosts_completed_count(tmp_path):
    """示例本身通过 effect 角色的真实 TS 检查，已完成字数必须是宿主提供的 number，不能由 Schema 假装补齐。"""
    from server.remotion_templates.agent_examples import TYPEWRITER_CODE

    example = effect_preset(TYPEWRITER_CODE, "cursorColor", {"title": "打字机", "slot": "enter", "target": "keywordChars", "stagger": 1})
    example.parameter_schema["properties"]["cursorWidth"] = {"type": "number", "minimum": 0, "maximum": 12}
    example.parameter_schema["required"].append("cursorWidth")
    example.default_parameters["cursorWidth"] = 3
    validator = ToolValidator(Renderer(Settings(_env_file=None, data_dir=tmp_path, **BROWSER)), tmp_path / "tools")
    result = asyncio.run(validator.validate_code(example))
    assert result.passed, result
    wrong = example.model_copy(update={"code": TYPEWRITER_CODE.replace("completedCount: number", "completedCount: string")})
    result = asyncio.run(validator.validate_code(wrong))
    assert not result.passed and any("completedCount" in item.message for item in result.diagnostics)


@needs_bundler
@pytest.mark.parametrize("stagger", [0, 1])
def test_reused_effect_keeps_its_chosen_glyph_timing(tmp_path, stagger):
    """配方的同时/依次出现可直接复用；显式覆盖优先，改到整块或持续时不继承不适用的 stagger。"""
    async def run():
        current = session(tmp_path)
        body = await current.execute("preset.create", preset("text", CHILD_TIER, "color"), available())
        fade = await current.execute("preset.create", effect_preset(FADE_CODE, usage={
            "title": "显现", "slot": "enter", "target": "plainChars", "seconds": 0.5, "stagger": stagger,
        }), available())
        source = {"kind": "stored", "preset_id": fade["preset"]["preset_id"]}
        request = {"description": "复用时序", "kind": "text", "parameters": [], "text": {
            "layout": {"x": 0, "y": 0, "width": 180, "height": 80}, "text": "甲乙丙丁", "font_size": 40, "duration_frames": 60,
            "tiers": [{"instance_id": "body", "source": {"kind": "stored", "preset_id": body["preset"]["preset_id"]},
                       "font_family": "Noto Sans CJK SC", "font_weight": 700}],
            "effects": [{"instance_id": "enter", "source": source}, {"instance_id": "exit", "source": source, "slot": "exit", "target": "block"}],
        }}
        for changes, expected in (({}, stagger), ({"title": "覆盖", "slot": "enter", "target": "keywordChars", "seconds": 1}, stagger),
                                  ({"stagger": 1 - stagger}, 1 - stagger), ({"target": "block"}, None), ({"slot": "loop"}, None)):
            request["text"]["effects"][0] = {"instance_id": "enter", "source": source, **changes}
            if changes.get("slot") == "loop":
                request["text"]["effects"].append({"instance_id": "disabledEntrance", "source": source, "target": "block", "enabled": False})
            result = await current.execute("sprite.compose", SpriteComposeInput.model_validate(request), available())
            values = current.sprite_drafts[result["draft_id"]].default_parameters
            assert values.get("effect1Stagger") == expected
            assert "effect2Stagger" not in values
            # Keep the required entrance when testing a looping entry.
            if changes.get("slot") == "loop":
                assert "effect1Seconds" not in values

    asyncio.run(run())


def typing_scene(target="keywordChars", plain_after=False, stagger=1):
    """直接运行发给模型的示例源码；关键词在句中并有独立颜色与阴影，周围普通字仍由原栏绘制。"""
    from server.remotion_templates.agent import EXECUTOR_RULES
    from server.remotion_templates.agent_examples import TYPEWRITER_CODE

    assert TYPEWRITER_CODE in EXECUTOR_RULES

    empty = {"type": "object", "properties": {}, "additionalProperties": False}
    color = {"type": "object", "properties": {"color": COLOR}, "required": ["color"], "additionalProperties": False}
    tier = {"instance_id": "body", "preset": {"code": CHILD_TIER, "parameter_schema": color, "default_parameters": {"color": "#FFFFFF"}}, "parameters": {"color": "#FFFFFF"}}
    keyword_code = 'import React from "react"; export default function Keyword(props: {children: React.ReactNode}) { return <span style={{font: "inherit", color: "#FFD400", textShadow: "0 1px 0 #FF8800"}}>{props.children}</span>; }'
    keyword = {"instance_id": "highlight", "preset": {"code": keyword_code, "parameter_schema": empty, "default_parameters": {}}, "parameters": {}}
    typing = {"instance_id": "typing", "preset": {"code": TYPEWRITER_CODE, "parameter_schema": {
        "type": "object", "properties": {"cursorColor": COLOR, "cursorWidth": {"type": "number", "minimum": 0, "maximum": 12}},
        "required": ["cursorColor", "cursorWidth"], "additionalProperties": False,
    }, "default_parameters": {"cursorColor": "#FF00FF", "cursorWidth": 3}}, "parameters": {"cursorColor": "#FF00FF", "cursorWidth": 3}}
    block = {"layout": {"x": 0, "y": 0, "width": 180, "height": 80}, "text": "甲乙丙丁", "font_size": 40,
             "tiers": [{"font_family": "Noto Sans CJK SC", "font_weight": 700}], "keyword": {},
             "effects": [{"title": "打字", "slot": "enter", "target": target, "seconds": 2 / 3, "stagger": stagger}]}
    effects = [typing]
    if plain_after:
        block["effects"].append({"title": "淡入", "slot": "enter", "target": "plainChars", "seconds": 0.2, "delay": 2 / 3, "stagger": 0})
        effects.append({"instance_id": "plainIn", "preset": {"code": FADE_CODE, "parameter_schema": empty, "default_parameters": {}}, "parameters": {}})
    code, schema, defaults, _ = compose_text_source(block, [tier], keyword, effects, [], ["Noto Sans CJK SC"], still=True)
    return ComponentDefinition(code=code, parameter_schema=schema, default_parameters=defaults)


@needs_browser
@pytest.mark.parametrize("target,plain_after,stagger", [("chars", False, 1), ("keywordChars", False, 1), ("keywordChars", True, 1), ("keywordChars", False, 0), ("keywordChars", False, 0.5)])
def test_real_typewriter_is_discrete_and_cursor_follows_typed_text(tmp_path, target, plain_after, stagger):
    """逐帧检查：整行/仅关键词范围、完成的字立即达到最终样式、光标随已输入文字移动；非关键词延后一起淡入。"""
    frames = [0, 1, 4, 5, 9, 10, 11, 15, 19, 20, 23, 26]
    capture = FrameCapture(frames, {"x": 0, "y": 0, "width": 180, "height": 80}, 1, tmp_path / "frames")
    validator = ToolValidator(Renderer(Settings(_env_file=None, data_dir=tmp_path, **BROWSER)), tmp_path / "tools")
    result = asyncio.run(validator.validate_render(RenderValidationInput(
        component=typing_scene(target, plain_after, stagger), duration_frames=60, parameters={"keywords": ["乙丙"]},
    ), capture=capture))
    assert result.passed, result
    images = {frame: Image.open(capture.directory / f"frame-{frame}.png").convert("RGB") for frame in frames}
    for index in range(4):
        pixels = list(images[26].crop((14 + 40 * index, 0, 47 + 40 * index, 80)).getdata())
        assert any(r > 220 and g > 180 and (b < 50 if index in (1, 2) else b > 220) for r, g, b in pixels), index
    typed_indices = [0, 1, 2, 3] if target == "chars" else [1, 2]
    for frame, picture in images.items():
        completed = min(len(typed_indices), frame * len(typed_indices) // 20) if stagger else (len(typed_indices) if frame >= 20 else 0)
        if stagger == 0.5:
            completed = 0 if frame < 15 else 1 if frame < 20 else 2
        for index in range(4):
            crop = (14 + 40 * index, 0, 47 + 40 * index, 80)  # 排除光标所在的边缘。
            actual, full = picture.crop(crop), images[26].crop(crop)
            if index in typed_indices:
                shown = typed_indices.index(index) < completed
            else:
                shown = not plain_after or frame >= 26
                if plain_after and 20 < frame < 26:
                    # 同帧两边普通字都具有相同的淡入进度，没有一边先出、一边后出。
                    white = [pixel for pixel, reference in zip(actual.getdata(), full.getdata()) if reference == (255, 255, 255)]
                    assert white and all(abs(pixel[0] - (128 + 127 * (frame - 20) / 6)) <= 2 for pixel in white)
                    continue
            if shown:
                assert ImageChops.difference(actual, full).getbbox() is None, (frame, index, "must be fully shown")
            else:
                assert actual.getextrema() == ((128, 128),) * 3, (frame, index, "must still be hidden")
        cursor_columns = [x for x in range(180) if any(r > 220 and g < 30 and b > 220 for r, g, b in [picture.getpixel((x, y)) for y in range(80)])]
        if completed == len(typed_indices):
            assert not cursor_columns, frame
        else:
            left = 10 + 40 * typed_indices[0] if completed == 0 else 47 + 40 * typed_indices[completed - 1]
            assert cursor_columns == list(range(left, left + 3)), (frame, cursor_columns)
