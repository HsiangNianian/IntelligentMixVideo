"""自由组件字效的工作区：Agent 写的 `Effect.tsx` 与 `effect.json`，以及宿主据此生成的 Sprite 草稿、起始工程与验收帧。

数据流：`workspace.write/edit` 改动 `files` → `assemble` 解析清单（能安全修正的自动修正并记下说明，内容有歧义时才拒绝）→
字效由 `text_configuration` 按示例内容生成固定名称的配置参数，与清单的样式参数合并成唯一实例（`INSTANCE_ID`）的参数契约
→ `compose_source` 打包（内联 `imv` 运行时，见 `remotion/compose.mjs`）→ `SpriteDraft`，清单记在 `SpriteDraft.effect`。
编辑任务用 `restore_files` 从基线版本还原两份文件，用户之后手动调过的参数写回清单的默认值。这里不运行代码，检查与渲染在
`ToolSession` 里经隔离的校验器完成。
"""
from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops
from pydantic import ValidationError

from ..categories import EFFECT_PARAMETER, FIXED_TITLES, HEX_COLOR, PARAMETER_GROUP, parameter_problems
from ..fonts import DEFAULT_FAMILY
from .compose import compose_source, text_configuration
from .contracts import EffectManifest, EffectText, SpriteDraft
from .registry import ToolFault, argument_fault
from .schema import validate_parameters

CODE = "Effect.tsx"
MANIFEST = "effect.json"
# 自由组件 Sprite 唯一的实例：Effect.tsx 画整个效果，所有参数都提升为它的顶层参数。
INSTANCE_ID = "effect"
CANVAS = {"width": 1080, "height": 1920}
# 字效起始工程的内容区域：画面中下部、约九成宽；整体位置之后由模板的宿主变换调整。
TEXT_LAYOUT = {"x": 54.0, "y": 1180.0, "width": 972, "height": 480}
FULL_CANVAS = {"x": 0.0, "y": 0.0, "width": 1080, "height": 1920}
_COLOR = re.compile(HEX_COLOR)
_CJK = re.compile(r"[㐀-鿿豈-﫿]")
# 字效的字号与多行配置的取值范围，与 `categories` 的固定 Schema 一致。
FONT_SIZE = (24.0, 240.0)
SIZE_RATIO = (0.4, 2.0)
LINE_GAP = (-600.0, 300.0)
TRANSLATION_GAP = (0.0, 100.0)
# 宿主按固定名称提供的配置参数（任一种文字形状的）；逐行颜色 `line{i}Color` 虽有固定标题，却是 Agent 自己的样式参数。
CONFIGURATION_NAMES = frozenset(name for name in FIXED_TITLES if not re.fullmatch(r"line\d+Color", name))
# 关键帧：先试拍，再按画面变化挑。开头、结尾与每行出现后的 1.5 秒内每 3 帧试拍一次，其余时间只取少量样本。
KEYFRAMES = 8
KEYFRAME_GAP = 3
TRIAL_STEP = 3
TRIAL_WINDOW = 45
TRIAL_MIDDLE = 18
# 比较用的缩略图宽度、算作变化的最小亮度差，以及低于最大变化这一比例的步长不算运动（停留时的呼吸发光不抢入场的帧）。
THUMBNAIL_SIDE = 160
CHANGE_LEVEL = 8
CHANGE_FLOOR = 0.2
# 运动事实：位置差、透明度差与宽度比超过这些值才算变化；停留期检查中间帧之后这么多帧。
MOTION_TOLERANCE = 4.0
OPACITY_TOLERANCE = 0.05
SCALE_TOLERANCE = 0.05
TRACE_HOLD = 30
# 预览示例关键词的个数与长度上限：模板与总线的关键词每项至少要容纳 12 个字。
SAMPLE_KEYWORD_LENGTH = 12

TEXT_STARTER = """import React from "react";
import { TextRoot, LineText, KeywordRun, useTextContent, type EffectProps } from "imv";

// Starter: the text shown still in its colour. Build the requested look and motion from here.
export default function Effect(props: EffectProps) {
  const content = useTextContent(props);
  return (
    <TextRoot content={content}>
      {(line) => (
        <div style={{ color: props[`line${line.index + 1}Color`] ?? props.color }}>
          <LineText
            content={content}
            line={line}
            keyword={(run) => (
              <KeywordRun content={content} run={run} style={content.keywordEffect && props.keywordEffectColor ? { color: props.keywordEffectColor } : undefined}>
                {run.text}
              </KeywordRun>
            )}
          />
        </div>
      )}
    </TextRoot>
  );
}
"""

FILTER_STARTER = """import React from "react";
import { AbsoluteFill } from "remotion";
import type { EffectProps } from "imv";

// Starter: a translucent tint over the whole video. Build the requested look from here; keep the video visible.
export default function Effect(props: EffectProps) {
  return <AbsoluteFill style={{ backgroundColor: props.tintColor, opacity: props.intensity * 0.5 }} />;
}
"""

MOTION_STARTER = """import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from "remotion";
import type { EffectProps } from "imv";

// Starter: a translucent bar sweeping across the canvas at the speed parameter. Build the requested effect from here.
export default function Effect(props: EffectProps) {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const progress = ((frame * props.speed) / durationInFrames) % 1;
  return (
    <AbsoluteFill>
      <div style={{ position: "absolute", left: `${progress * 100}%`, top: 0, width: 24, height: "100%", backgroundColor: props.color, opacity: 0.35 }} />
    </AbsoluteFill>
  );
}
"""


def _color_param(name: str, default: str, title: str, group: str) -> dict[str, Any]:
    """A colour style parameter entry of a starter manifest."""
    return {"name": name, "schema": {"type": "string", "pattern": HEX_COLOR}, "default": default, "title": title, "group": group}


def starter_files(kind: str | None, requested: set[str], family: str) -> dict[str, str]:
    """The workspace a new work starts from: a still example of its kind that passes every check.

    A text effect follows the new-chat switches the user turned on (双栏, 双语字幕, keyword effect), so its shape already
    matches them; ``family`` is the font library's default family. The example draws nothing the request asked for.
    """
    if kind in {"filter_overlay"}:
        manifest = {"kind": "filter_overlay", "description": "示例：半透明暖色调", "layout": FULL_CANVAS, "params": [
            {"name": "intensity", "schema": {"type": "number", "minimum": 0, "maximum": 1}, "default": 0.4, "title": "强度", "group": "滤镜"},
            _color_param("tintColor", "#FF8A3D", "色调", "滤镜"),
        ]}
        return {CODE: FILTER_STARTER, MANIFEST: json.dumps(manifest, ensure_ascii=False, indent=2)}
    if kind in {"video_overlay", "transition_overlay"}:
        manifest = {"kind": kind, "description": "示例：扫过画面的半透明光条", "layout": FULL_CANVAS, "params": [
            _color_param("color", "#FFFFFF", "颜色", "特效"),
            {"name": "speed", "schema": {"type": "number", "minimum": 0.25, "maximum": 4}, "default": 1, "title": "速度", "group": "特效"},
        ]}
        return {CODE: MOTION_STARTER, MANIFEST: json.dumps(manifest, ensure_ascii=False, indent=2)}
    bilingual, two_tier, keyword = "bilingual" in requested, "two_tier" in requested, "keyword_effect" in requested
    if bilingual:
        lines = ["示例文字一", "Sample text one", "示例文字二", "Sample text two"] if two_tier else ["示例文字", "Sample text"]
        fonts = [{"family": family, "weight": 700 if index % 2 == 0 else 400, "size_ratio": 1 if index % 2 == 0 else 0.5} for index in range(len(lines))]
    elif two_tier:
        lines = ["示例引导语", "示例主标题"]
        fonts = [{"family": family, "weight": 400, "size_ratio": 0.6}, {"family": family, "weight": 700, "size_ratio": 1}]
    else:
        lines = ["示例文字"]
        fonts = [{"family": family, "weight": 700}]
    text: dict[str, Any] = {"sample": "\n".join(lines), "keywords": ["示例"] if keyword else [], "bilingual": bilingual, "keyword_look": keyword,
                            "font_size": 96, "fonts": fonts}
    if len(lines) > 1:
        text |= {"line_starts": [0] * len(lines), "line_gap": 24}
    if len(lines) == 4:
        text["translation_gap"] = 8
    params = [_color_param("color", "#FFFFFF", "文字颜色", "文字")] if len(lines) == 1 else [
        _color_param(f"line{index}Color", "#FFFFFF", FIXED_TITLES[f"line{index}Color"], "文字") for index in range(1, len(lines) + 1)
    ]
    if keyword:
        params.append(_color_param("keywordEffectColor", "#FFD54A", "关键词颜色", "关键词字效"))
    manifest = {"kind": "text", "description": "示例：静止显示的文字", "layout": TEXT_LAYOUT, "text": text, "params": params}
    return {CODE: TEXT_STARTER, MANIFEST: json.dumps(manifest, ensure_ascii=False, indent=2)}


def parse_manifest(content: str) -> EffectManifest:
    """Parse effect.json, naming the JSON position or the field (as a JSON Pointer) that is wrong."""
    try:
        return EffectManifest.model_validate_json(content)
    except ValidationError as exc:
        if any(error["type"] == "json_invalid" for error in exc.errors()):
            try:
                json.loads(content)
            except json.JSONDecodeError as decode:
                raise ToolFault("INVALID_ARGUMENT", f"effect.json is not valid JSON: {decode.msg} at line {decode.lineno}, column {decode.colno}.") from exc
        # A union member's model name is not a field of the JSON the Agent wrote (/text/EffectText/fonts is /text/fonts).
        errors = [{**error, "loc": tuple(part for part in error["loc"] if not (isinstance(part, str) and part[:1].isupper()))} for error in exc.errors()]
        fault = argument_fault(ValidationError.from_exception_data(exc.title, errors))
        raise ToolFault("INVALID_ARGUMENT", f"effect.json does not match the manifest: {fault.error.message}", field=fault.error.field) from exc


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    """Keep ``value`` inside ``bounds``."""
    return min(bounds[1], max(bounds[0], value))


def _text_block(text: EffectText, fonts: list[str], requested: set[str], duration: int, layout: dict[str, Any], notes: list[str]) -> dict[str, Any]:
    """The text block `text_configuration` reads, from the manifest's sample content and configuration.

    The shape comes from the sample: one line, two for 双栏 (or one bilingual pair), four for two bilingual groups. Values
    the host can settle safely (fonts outside the library, weights, ratios, starts, gaps, keywords the sample lacks)
    are corrected and noted; a sample that does not fit any shape, or misses a shape the user switched on, is refused.
    """
    lines = text.sample.split("\n")
    count = len(lines)
    if count not in ((2, 4) if text.bilingual else (1, 2)) or not all(line.strip() for line in lines):
        raise ToolFault("INVALID_ARGUMENT", (
            f"text.sample has {count} line(s): write one non-empty line, two for 双栏 (an upper and a lower tier), or with bilingual: true "
            "two or four lines alternating Chinese and its English translation (Chinese 1, English 1, Chinese 2, English 2)."), field="/text/sample")
    if text.bilingual and any(_CJK.search(line) for line in lines[1::2]):
        raise ToolFault("INVALID_ARGUMENT", "With bilingual: true every second line of text.sample is the English translation of the line before it.", field="/text/sample")
    if "two_tier" in requested and count != (4 if text.bilingual else 2):
        raise ToolFault("INVALID_ARGUMENT", "The user switched on 双栏: write two content groups, two lines (four with bilingual: true).", field="/text/sample")
    if "bilingual" in requested and not text.bilingual:
        raise ToolFault("INVALID_ARGUMENT", "The user switched on 双语字幕: set bilingual: true and alternate Chinese lines with their English translations.", field="/text/bilingual")
    if "keyword_effect" in requested and not text.keyword_look:
        raise ToolFault("INVALID_ARGUMENT", "The user switched on the keyword effect: give keywords a look of their own and set keyword_look: true.", field="/text/keyword_look")
    library = fonts or [DEFAULT_FAMILY]
    fallback = DEFAULT_FAMILY if DEFAULT_FAMILY in library else library[0]
    chosen = list(text.fonts)
    if len(chosen) != count:
        notes.append(f"text.fonts has {len(chosen)} entries for {count} line(s); the host used one per line, repeating the last.")
        chosen = [chosen[min(index, len(chosen) - 1)] for index in range(count)]
    tiers = []
    for index, font in enumerate(chosen):
        family = font.family
        if family not in library:
            notes.append(f"Font {family!r} is not in the font library; line {index + 1} uses {fallback!r}.")
            family = fallback
        weight = font.weight if font.weight in (400, 700) else (700 if font.weight >= 550 else 400)
        if weight != font.weight:
            notes.append(f"Line {index + 1} weight {font.weight} became {weight} (fonts offer 400 and 700).")
        ratio = 1.0
        if count > 1:
            ratio = _clamp(font.size_ratio if font.size_ratio is not None else 1.0, SIZE_RATIO)
            if font.size_ratio is not None and ratio != font.size_ratio:
                notes.append(f"Line {index + 1} size_ratio was kept within {SIZE_RATIO[0]}-{SIZE_RATIO[1]}.")
        tiers.append({"font_family": family, "font_weight": weight, "size_ratio": ratio})
    font_size = _clamp(text.font_size, FONT_SIZE)
    if font_size != text.font_size:
        notes.append(f"text.font_size was kept within {FONT_SIZE[0]:g}-{FONT_SIZE[1]:g}.")
    block: dict[str, Any] = {"text": text.sample, "bilingual": text.bilingual, "font_size": font_size, "tiers": tiers,
                             "keyword": {} if text.keyword_look else None, "effects": [], "layout": layout}
    if count > 1:
        given = list(text.line_starts or [])
        starts = [given[index] if index < len(given) else (given[-1] if given else 0) for index in range(count)]
        starts[0] = 0
        for index in range(1, count):
            starts[index] = max(starts[index], starts[index - 1])
        if count == 4:
            starts[1], starts[3] = starts[0], starts[2]
        starts = [min(value, duration - 1) for value in starts]
        if text.line_starts is not None and starts != text.line_starts:
            notes.append(f"text.line_starts became {starts}: one non-decreasing frame per line from 0, a bilingual pair starting together, before the end.")
        gap = 0.0 if text.arrangement == "overlap" else _clamp(text.line_gap if text.line_gap is not None else 24.0, LINE_GAP)
        block |= {"arrangement": text.arrangement, "line_gap": gap, "line_starts": starts}
        if count == 4:
            block["translation_gap"] = _clamp(text.translation_gap if text.translation_gap is not None else 8.0, TRANSLATION_GAP)
    kept = [word for word in text.keywords if word in text.sample and len(word) <= SAMPLE_KEYWORD_LENGTH]
    if kept != text.keywords:
        notes.append(f"Sample keywords must be words of text.sample of at most {SAMPLE_KEYWORD_LENGTH} characters; kept {kept}.")
    block["preview_keywords"] = kept
    return block


def _number_bounds(definition: dict[str, Any], default: Any, integer: bool) -> None:
    """Give a number without bounds a range around its default, in place."""
    value = default if isinstance(default, (int, float)) and not isinstance(default, bool) else 0
    low = definition.get("minimum", min(0, value))
    high = definition.get("maximum", max(1, value * 2, value + 1) if value >= 0 else 0)
    if integer:
        low, high = math.floor(low), math.ceil(high)
    if high <= low:
        high = low + 1
    definition["minimum"], definition["maximum"] = low, high


def _style_parameters(manifest: EffectManifest, reserved: set[str], notes: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The effect's own style parameters as top-level schemas (titled, grouped) and defaults.

    Missing bounds, a colour pattern for a colour default, a missing title and defaults outside the schema are fixed
    and noted; a configuration name the host provides is dropped with a note. What still breaks the parameter rules
    (`categories.parameter_problems`) is refused, naming the parameter.
    """
    schemas: dict[str, Any] = {}
    defaults: dict[str, Any] = {}
    for index, item in enumerate(manifest.params):
        field = f"/params/{index}"
        if item.name in reserved or EFFECT_PARAMETER.match(item.name) or item.name in schemas:
            notes.append(f"Parameter {item.name} was left out: the host provides it (or it repeats). Read the host's value from props.")
            continue
        definition = {key: value for key, value in deepcopy(item.definition).items() if key not in {"title", "description", "default", PARAMETER_GROUP}}
        default = deepcopy(item.default)
        kind = definition.get("type")
        if kind is None:
            kind = ("boolean" if isinstance(default, bool) else "integer" if isinstance(default, int) else
                    "number" if isinstance(default, float) else "string" if isinstance(default, str) else None)
            if kind is None:
                raise ToolFault("INVALID_ARGUMENT", f"Parameter {item.name} needs a schema type (number, integer, string or boolean).", field=f"{field}/schema")
            definition["type"] = kind
        if kind in {"number", "integer"} and "enum" not in definition and not {"minimum", "maximum"} <= set(definition):
            _number_bounds(definition, default, kind == "integer")
            notes.append(f"Parameter {item.name} got the range {definition['minimum']}-{definition['maximum']}; give minimum and maximum to choose it.")
        if kind == "string" and isinstance(default, str):
            if "pattern" in definition and definition["pattern"] != HEX_COLOR:
                definition.pop("pattern")
                notes.append(f"Parameter {item.name}: only the colour pattern is supported, so its pattern was removed.")
            if _COLOR.match(default) and "enum" not in definition:
                definition["pattern"] = HEX_COLOR
            elif "pattern" not in definition and "enum" not in definition and "maxLength" not in definition:
                definition["maxLength"] = 200
        if kind in {"number", "integer"} and isinstance(default, (int, float)) and not isinstance(default, bool) and "minimum" in definition:
            fixed = min(definition["maximum"], max(definition["minimum"], default))
            if fixed != default:
                notes.append(f"Parameter {item.name} default {default} was kept within its range.")
                default = int(fixed) if kind == "integer" else fixed
        if isinstance(definition.get("enum"), list) and definition["enum"] and default not in definition["enum"]:
            notes.append(f"Parameter {item.name} default {default!r} is not one of its enum values; it became {definition['enum'][0]!r}.")
            default = definition["enum"][0]
        definition["title"] = item.title or item.name
        if item.group:
            definition[PARAMETER_GROUP] = item.group
        problem = parameter_problems(definition, item.name)
        if problem:
            raise ToolFault("INVALID_ARGUMENT", f"Parameter {item.name} {problem}.", field=f"{field}/schema")
        try:
            validate_parameters({"type": "object", "properties": {item.name: definition}}, {item.name: default})
        except Exception as exc:
            raise ToolFault("INVALID_ARGUMENT", f"Parameter {item.name} default does not match its schema: {str(exc)[:300]}", field=f"{field}/default") from exc
        schemas[item.name], defaults[item.name] = definition, default
    return schemas, defaults


def _layout(manifest: EffectManifest, notes: list[str]) -> dict[str, Any]:
    """The instance box on the canvas, kept inside the 1080x1920 canvas."""
    box = manifest.layout
    width, height = min(box.width, CANVAS["width"]), min(box.height, CANVAS["height"])
    x = _clamp(box.x, (0.0, float(CANVAS["width"] - width)))
    y = _clamp(box.y, (0.0, float(CANVAS["height"] - height)))
    if (x, y, width, height) != (box.x, box.y, box.width, box.height):
        notes.append(f"layout was kept inside the canvas: x {x:g}, y {y:g}, width {width}, height {height}.")
    return {"x": x, "y": y, "width": width, "height": height, "z_index": 0}


def assemble(files: dict[str, str], *, fonts: list[str], requested: set[str], duration: int) -> tuple[SpriteDraft, list[str]]:
    """Build the Sprite draft the workspace describes, with the host's notes on what it corrected.

    A text effect's configuration parameters (content, fonts, sizes, gaps, line starts, keyword options and the keyword
    effect switch) come from `text_configuration`, exactly as for a composed text block; the effect's own style
    parameters come from the manifest. All of them are top-level parameters of the single instance, which the bundler
    places at the manifest's layout for the whole ``duration``.
    """
    notes: list[str] = []
    manifest = parse_manifest(files[MANIFEST])
    layout = _layout(manifest, notes)
    config_schemas: dict[str, Any] = {}
    config_defaults: dict[str, Any] = {}
    block = None
    if manifest.kind == "text":
        if manifest.text is None:
            raise ToolFault("INVALID_ARGUMENT", "A text effect (kind text) needs \"text\": its sample content, fonts and font size.", field="/text")
        block = _text_block(manifest.text, fonts, requested, duration, layout, notes)
        config_schemas, config_defaults = text_configuration(block, fonts or [DEFAULT_FAMILY])
    elif manifest.text is not None:
        notes.append(f"\"text\" only applies to kind text and was ignored for {manifest.kind}.")
    reserved = {*config_schemas, *CONFIGURATION_NAMES, INSTANCE_ID, "__proto__", "prototype", "constructor"}
    style_schemas, style_defaults = _style_parameters(manifest, reserved, notes)
    properties = {**config_schemas, **style_schemas}
    defaults = {**config_defaults, **style_defaults}
    instance = {
        "instance_id": INSTANCE_ID,
        "preset": {"description": manifest.description, "code": files[CODE], "default_parameters": defaults,
                   "parameter_schema": {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}},
        "parameters": defaults,
        "layout": layout,
        "timing": {"start_frame": 0, "duration_frames": duration},
    }
    bindings = [{"name": name, "title": str(definition.get("title") or name)[:40], "targets": [{"instance_id": INSTANCE_ID, "parameter": name}]}
                for name, definition in properties.items()]
    code, schema, merged = compose_source([instance], bindings)
    effect = manifest.model_dump(mode="json", by_alias=True, exclude_none=True)
    if block is not None:
        effect["text"]["keywords"] = block["preview_keywords"]
    draft = SpriteDraft.model_validate({
        "description": manifest.description, "code": code, "parameter_schema": schema, "default_parameters": merged,
        "composition": {"width": CANVAS["width"], "height": CANVAS["height"], "fps": 30, "duration_frames": duration},
        "instances": [instance], "kind": manifest.kind, "bindings": bindings, "effect": effect,
    })
    return draft, notes


def preview_keywords(sprite: SpriteDraft) -> list[str]:
    """The keywords a free-form text effect's previews show: its manifest's sample keywords, else up to two opening characters."""
    if sprite.kind != "text" or sprite.default_parameters.get("keywords"):
        return []
    kept = list(((sprite.effect or {}).get("text") or {}).get("keywords") or [])
    if kept:
        return kept
    line = next((line for line in str(sprite.default_parameters.get("text", "")).split("\n") if line.strip()), "")
    word = re.search(r"\S{1,2}", line)
    return [word.group(0)] if word else []


def trial_frames(sprite: SpriteDraft) -> list[int]:
    """The frames the host shoots to find an effect's keyframes: densely where motion usually is (the opening, the closing and
    after each later line appears), sparsely in between, and always the middle frame the bus freezes short subtitles on."""
    last = sprite.composition.duration_frames - 1
    frames = {*range(0, TRIAL_WINDOW, TRIAL_STEP), *range(max(0, last - TRIAL_WINDOW), last + 1, TRIAL_STEP), last, last // 2}
    frames |= {last * index // TRIAL_MIDDLE for index in range(TRIAL_MIDDLE)}
    for start in (sprite.default_parameters.get("lineStartFrames") or [])[1:]:
        if isinstance(start, int):
            frames |= set(range(start, start + TRIAL_WINDOW, TRIAL_STEP))
    return sorted(frame for frame in frames if 0 <= frame <= last)


def _change(first: Image.Image, second: Image.Image) -> int:
    """How much two thumbnails differ: the summed brightness difference of their pixels, ignoring raster noise, so that a block
    moving counts for more than the same block pulsing slightly."""
    histogram = ImageChops.difference(first, second).histogram()
    return sum(level * histogram[level] for level in range(CHANGE_LEVEL, len(histogram)))


def keyframes(images: dict[int, Path], sprite: SpriteDraft) -> list[tuple[int, str]]:
    """Choose what the model looks at from the trial shots (frame → path, on neutral gray): frames spaced evenly by how much the
    picture changes, not by time, so a fast entrance gets several frames and a long hold none.

    The first and last frames that show anything and the middle frame are always kept. Steps far smaller than the biggest
    one (a glow pulsing during the hold) do not count as change next to a real motion, and keyframes stay `KEYFRAME_GAP`
    frames apart. A still effect therefore yields just those few frames.
    """
    frames = sorted(images)
    if not frames:
        return []
    fps = sprite.composition.fps
    thumbnails = {}
    for frame in frames:
        with Image.open(images[frame]) as image:
            thumbnails[frame] = image.convert("L").resize((THUMBNAIL_SIDE, max(1, round(THUMBNAIL_SIDE * image.height / image.width))))
    blank = Image.new("L", thumbnails[frames[0]].size, 128)
    visible = [frame for frame in frames if _change(thumbnails[frame], blank)]
    middle = min(frames, key=lambda frame: abs(frame - (sprite.composition.duration_frames - 1) // 2))
    chosen = {middle: "middle, where the bus freezes short subtitles"}
    if visible:
        chosen.setdefault(visible[0], "first visible")
        chosen.setdefault(visible[-1], "last visible")
    steps = [(after, _change(thumbnails[before], thumbnails[after])) for before, after in zip(frames, frames[1:])]
    floor = max((change for _frame, change in steps), default=0) * CHANGE_FLOOR
    moving = [(frame, change) for frame, change in steps if change and change >= floor]
    total = sum(change for _frame, change in moving)
    slots = max(0, KEYFRAMES - len(chosen))
    passed, index = 0, 0
    for slot in range(slots):
        target = total * (slot + 0.5) / slots
        while index < len(moving) and passed + moving[index][1] < target:
            passed += moving[index][1]
            index += 1
        # Take the frame this much change was reached at, or the next one far enough from those already chosen.
        for frame, _size in moving[index:]:
            if all(abs(frame - other) >= KEYFRAME_GAP for other in chosen):
                chosen[frame] = "the picture is changing"
                break
    return [(frame, f"{chosen[frame]}, {frame / fps:.2f} s") for frame in sorted(chosen)]


def _offset(dx: float, dy: float) -> str:
    """A position relative to rest in words, in screen terms (y grows downwards)."""
    parts = []
    if abs(dx) > MOTION_TOLERANCE:
        parts.append(f"{abs(dx):.0f} px to the {'right' if dx > 0 else 'left'} of")
    if abs(dy) > MOTION_TOLERANCE:
        parts.append(f"{abs(dy):.0f} px {'below' if dy > 0 else 'above'}")
    return f"{' and '.join(parts)} its resting position" if parts else ""


def motion_facts(trace: dict[str, list[dict]], fps: int, middle: int) -> list[str]:
    """What each line and keyword hit does, in words, from the worker's measurements of the running code (`measureSubjects`).

    Its resting state is the one on the frame nearest ``middle``; each sentence gives where it first appears relative to that,
    how it gets there and how it leaves, so a direction or an order can be read off without looking at any picture. Effects a
    text box does not show, such as clipping or a filter, are not measured: those need a look at a frame.
    """
    series: dict[str, list[tuple[int, dict]]] = {}
    for frame, subjects in sorted(((int(key), value) for key, value in trace.items())):
        for subject in subjects:
            series.setdefault(subject["name"], []).append((frame, subject))
    facts = []
    at = lambda frame: f"frame {frame} ({frame / fps:.2f} s)"
    for name, points in series.items():
        shown = [(frame, item) for frame, item in points if item["revealed"] > 0]
        if not shown:
            facts.append(f"{name}: never painted.")
            continue
        rest_frame, rest = min(shown, key=lambda point: abs(point[0] - middle))
        moved = lambda item: abs(item["x"] - rest["x"]) > MOTION_TOLERANCE or abs(item["y"] - rest["y"]) > MOTION_TOLERANCE
        first_frame, first = shown[0]
        words = [f"{name} \u300c{rest['text']}\u300d: first painted at {at(first_frame)}"]
        away = _offset(first["x"] - rest["x"], first["y"] - rest["y"])
        before = [frame for frame, item in shown if frame <= rest_frame and moved(item)]
        if away and before:
            settled = next((frame for frame, _item in shown if frame > before[-1]), rest_frame)
            words.append(f"{away}, reaching rest by {at(settled)}")
        else:
            words.append("at its resting position")
        if abs(first["opacity"] - rest["opacity"]) > OPACITY_TOLERANCE:
            words.append(f"opacity {first['opacity']:.2f} rising to {rest['opacity']:.2f}")
        if first["revealed"] < 0.99 and rest["revealed"] > first["revealed"]:
            words.append(f"with {first['revealed']:.0%} of its characters painted, growing to {rest['revealed']:.0%}")
        if rest["width"] and abs(first["width"] / rest["width"] - 1) > SCALE_TOLERANCE:
            words.append(f"width {first['width'] / rest['width']:.2f}x its resting width")
        last_frame, last = shown[-1]
        gone = _offset(last["x"] - rest["x"], last["y"] - rest["y"])
        leaving = []
        if gone:
            leaving.append(f"moves to {gone}")
        if abs(last["opacity"] - rest["opacity"]) > OPACITY_TOLERANCE:
            leaving.append(f"fades to opacity {last['opacity']:.2f}")
        if leaving:
            words.append(f"then {' and '.join(leaving)} by {at(last_frame)}")
        hold = [item for frame, item in shown if rest_frame <= frame <= rest_frame + TRACE_HOLD]
        if hold and max(item["opacity"] for item in hold) - min(item["opacity"] for item in hold) > OPACITY_TOLERANCE:
            words.append("its opacity keeps changing while it rests")
        facts.append(", ".join(words) + ".")
    return facts


def restore_files(sprite: SpriteDraft, values: dict[str, Any]) -> dict[str, str]:
    """The workspace of an accepted free-form version, with ``values`` (its current defaults, including the user's
    own parameter changes) written back into the manifest so the edit starts from what the user sees."""
    manifest = deepcopy(sprite.effect or {})
    text = manifest.get("text")
    if isinstance(text, dict):
        lines = str(values.get("text", text.get("sample", ""))).split("\n")
        if "textEn" in values:
            english = str(values["textEn"]).split("\n")
            lines = [part for pair in zip(lines, english) for part in pair] if len(english) > 1 else [lines[0], english[0]]
        text["sample"] = "\n".join(lines)
        if "fontSize" in values:
            text["font_size"] = values["fontSize"]
        fonts = text.get("fonts") or []
        for index, font in enumerate(fonts):
            names = (f"line{index + 1}FontFamily", f"line{index + 1}FontWeight", f"line{index + 1}SizeRatio") if len(lines) > 1 else ("fontFamily", "fontWeight", None)
            for key, name in zip(("family", "weight", "size_ratio"), names):
                if name in values:
                    font[key] = values[name]
        for key, name in (("line_starts", "lineStartFrames"), ("line_gap", "lineGap"), ("arrangement", "tierLayout"), ("translation_gap", "translationGap")):
            if name in values:
                text[key] = values[name]
    for item in manifest.get("params", []):
        if item.get("name") in values:
            item["default"] = values[item["name"]]
    return {CODE: sprite.instances[0].preset.code, MANIFEST: json.dumps(manifest, ensure_ascii=False, indent=2)}


def line_count(content: str) -> int:
    """Lines of a workspace file, as the receipts count them."""
    return content.count("\n") + (0 if content.endswith("\n") or not content else 1)


def physical_lines(values: dict[str, Any]) -> list[str]:
    """The lines a text effect draws from its parameter values: `text` alone, or `text`/`textEn` paired as the runtime pairs them."""
    text = str(values.get("text", ""))
    count = len(values.get("lineStartFrames") or [0])
    if "textEn" not in values:
        return [text] if count == 1 else (text.split("\n") + [""] * count)[:count]
    zh, en = text.split("\n"), str(values["textEn"]).split("\n")
    return [part for index in range(2) for part in ((zh + [""])[index], (en + [""])[index])] if count == 4 else [text, str(values["textEn"])]


def _probe_content(values: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Other text in the effect's own shape, to tell drawing the injected text from drawing a sample written into the code."""
    lines = physical_lines(values)
    probes = [(f"Injected line {index + 1}" if "textEn" in values and index % 2 else f"注入的第{index + 1}行文字") for index in range(len(lines))]
    if "textEn" not in values:
        return {"text": "\n".join(probes)}, probes
    if len(lines) == 4:
        return {"text": f"{probes[0]}\n{probes[2]}", "textEn": f"{probes[1]}\n{probes[3]}"}, probes
    return {"text": probes[0], "textEn": probes[1]}, probes


def _extremes(definition: dict[str, Any]) -> list[Any] | None:
    """Two values at the ends of a style parameter's range, or None when it has no comparable ends."""
    kind = definition.get("type")
    if isinstance(definition.get("enum"), list):
        values = definition["enum"]
        return [values[0], values[-1]] if len(values) > 1 else None
    if kind == "boolean":
        return [False, True]
    if kind in {"number", "integer"} and {"minimum", "maximum"} <= set(definition):
        return [definition["minimum"], definition["maximum"]]
    if kind == "string" and definition.get("pattern") == HEX_COLOR:
        return ["#000000", "#FFFFFF"]
    return None


def contract_probes(sprite: SpriteDraft) -> dict[str, Any]:
    """The `effect_contract` request of the validation worker for a free-form effect (see `effectContractCheck`).

    Every style parameter of the manifest (and a text effect's keyword effect switch) is probed at its two ends over
    the defaults with every line shown and the sample keywords on; a text effect also gets other text, a keyword with
    its options on, its default lines for the middle frame and its default and smallest font size.
    """
    values = sprite.default_parameters
    properties = sprite.parameter_schema.get("properties", {})
    style = [item.get("name") for item in (sprite.effect or {}).get("params", [])]
    names = [name for name in style if name in properties] + (["keywordEffect"] if "keywordEffect" in properties else [])
    starts = [0] * len(values["lineStartFrames"]) if isinstance(values.get("lineStartFrames"), list) else None
    keywords = preview_keywords(sprite)
    base = {**values, **({"keywords": keywords} if keywords else {}), **({"lineStartFrames": starts} if starts else {})}
    wiring = [{"name": name, "values": ends} for name in names if (ends := _extremes(properties[name])) is not None]
    spec: dict[str, Any] = {"base": base, "wiring": wiring}
    if sprite.kind == "text":
        probe, probe_lines = _probe_content(values)
        ratios = [value for key, value in values.items() if re.fullmatch(r"line\d+SizeRatio", key) and isinstance(value, (int, float)) and value > 0]
        smallest = max(float(properties.get("fontSize", {}).get("minimum", FONT_SIZE[0])), math.ceil(FONT_SIZE[0] / min([1.0, *ratios])))
        spec |= {
            "sample": {key: values[key] for key in ("text", "textEn") if key in values},
            "probe": probe, "probe_lines": probe_lines, "lines": physical_lines(values), "starts": starts,
            "keyword": keywords[0] if keywords else None,
            "font_size": {"default": values.get("fontSize"), "min": smallest} if isinstance(values.get("fontSize"), (int, float)) else None,
        }
    return spec


def review_box(sprite: SpriteDraft) -> dict[str, float]:
    """The canvas region previews show: the effect's box with room around it for motion, or the whole canvas for a full-canvas effect."""
    layout = sprite.instances[0].layout
    margin = max(120.0, layout.height / 2)
    left, top = max(0.0, layout.x - margin), max(0.0, layout.y - margin)
    right = min(float(CANVAS["width"]), layout.x + layout.width + margin)
    bottom = min(float(CANVAS["height"]), layout.y + layout.height + margin)
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}
