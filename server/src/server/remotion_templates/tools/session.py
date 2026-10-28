"""Task-owned creation tools: immutable Presets, composed Sprites and host checks."""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import logging
import re
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

import jieba

from .contracts import (
    CodeDiagnostic,
    CodeValidationReport,
    ComponentDefinition,
    InstanceSummary,
    PresetCreateInput,
    PresetCreateOutput,
    PresetDraft,
    PresetDraftSummary,
    PresetModifyInput,
    PresetModifyOutput,
    PresetSearchInput,
    PresetSearchOutput,
    PresetSummary,
    PresetRecord,
    RenderValidationInput,
    RenderValidationReport,
    ReviewFrame,
    SpriteComposeOutput,
    SpriteDraft,
    SpriteRecord,
    SpriteCreateOutput,
    SpriteReview,
)
from ..categories import EFFECT_PARAMETER, EFFECT_TARGETS, FIXED_TITLES, GLYPH_TARGETS, agent_message, category_problems, parameter_problems
from ..fonts import FontStore, open_font_schema
from .catalog_store import CatalogStore
from .registry import ToolFault
from .schema import merge_parameters, validate_component_contract, validate_parameters
from .compose import TEXT_BLOCK_ID, compose_source, compose_text_source


# 四个搜索回执需共享 240 KB 会话交换，留出调用参数与消息封装的空间。
_SEARCH_BYTE_BUDGET = 40_000
# 快照预算：provider 的请求体上限是 512000 字节，会话窗口自身上限 240000 字节，
# 余量留给规则与工具描述。快照里只有编辑基线的源码没有上限，只能在这里按需省略。
_SNAPSHOT_BYTE_BUDGET = 200_000
# 分词的词典只在第一次检索时载入，日志里不留它的启动信息。
jieba.setLogLevel(logging.WARNING)


def _usage_problem(draft: PresetDraft) -> tuple[str, str] | None:
    """Why a Preset's usage does not fit its role, as (field, message), or None: an effect Preset records its default
    title, slot, position, length and glyph timing so later works can reuse it; other roles take none, and a loop has no length or stagger."""
    if draft.role == "effect" and draft.usage is None:
        return "/usage", ('An effect Preset records how it is used: give usage {"title": "<short Chinese name such as 淡入>", "slot": "enter|exit|loop|cue", '
                          '"target": "block|tiers|line1|line2|chars|keywordChars|plainChars|keyword", "seconds": 0.5}, plus stagger (0 together, 1 sequential) at glyph targets; these are the defaults a later work reuses it with.')
    if draft.role != "effect" and draft.usage is not None:
        return "/usage", "Only an effect Preset (role effect) records a usage; leave it out."
    if draft.usage is not None and draft.usage.slot == "loop" and draft.usage.seconds is not None:
        return "/usage/seconds", "A looping effect lasts as long as its position shows: leave usage.seconds out."
    if draft.usage is not None:
        glyphs = draft.usage.target in GLYPH_TARGETS and draft.usage.slot != "loop"
        if glyphs and draft.usage.stagger is None:
            return "/usage/stagger", "Choose usage.stagger explicitly for a glyph effect: 0 shows all its glyphs together; 1 animates them one after another."
        if not glyphs and draft.usage.stagger is not None:
            return "/usage/stagger", "Only an entrance, exit or cue at chars, keywordChars or plainChars takes usage.stagger."
    return None


# 每个任务里保存后附带画面回看的次数：Executor 至多这么多次看到自己保存的 Sprite，再决定是否修改。
VISUAL_REVIEW_ROUNDS = 2
# 回看帧的最长边（像素）：看清效果即可，不必全尺寸。
_REVIEW_SIDE = 720


def review_frames(sprite: SpriteDraft) -> list[tuple[int, str]]:
    """Return at most six observations, protecting a transition's before/after and the exit from entrance crowding.

    Labels describe sampling times, not a verdict that text is complete. Effect windows use the target tier's clock
    and the same positive half-up rounding and length clamp as compose.mjs. Other Sprites keep their three samples.
    """
    last, fps = sprite.composition.duration_frames - 1, sprite.composition.fps
    if sprite.text is None:
        picks = [(last // 2, "half way"), (last // 4, "a quarter of the way"), (last * 3 // 4, "three quarters of the way")]
    else:
        config = sprite.default_parameters
        exit_length = 0
        cues: list[tuple[int, int]] = []
        entrances: dict[int, str] = {}
        starts = config.get("lineStartFrames") or [0]
        cue = starts[1] if len(starts) > 1 else None
        if "text" in config:
            lines = str(config["text"]).split("\n")
            if len(lines) < 2 or not lines[1]:
                cue = None
        for index, effect in enumerate(sprite.text.effects, start=1):
            if effect.slot == "loop" or config.get(f"effect{index}") is False:
                continue
            target = config.get(f"effect{index}Target", "block")
            origins = [0] if target == "block" else starts[:1] if target == "line1" else starts[1:2] if target == "line2" else starts
            for origin in origins:
                if origin > last:
                    continue
                half = (last - origin) // 2
                length = max(1, min(int((config.get(f"effect{index}Seconds") or 0.5) * fps + 0.5), half))
                delay = min(int((config.get(f"effect{index}Delay") or 0) * fps + 0.5), max(0, half - length))
                if effect.slot == "enter":
                    entrances.setdefault(origin + delay + length // 2, f"half way into the entrance {effect.title}")
                elif effect.slot == "cue" and cue is not None:
                    cues.append((cue + delay + length // 2, cue + delay + length))
                elif effect.slot == "exit":
                    exit_length = max(exit_length, length)
        picks = [(last // 2, "middle of the preview"), (last, "last frame of the preview")]
        if exit_length:
            picks.append((last - exit_length // 2, "half way out of the exit"))
        if cue:
            picks.append((cue - 1, "just before the lower tier appears"))
        if cues:
            middle, end = max(cues, key=lambda window: window[1])
            picks.extend([(middle, "half way into the effects played when the lower tier appears"),
                          (end, "end of the effects played when the lower tier appears")])
        picks.extend(sorted(entrances.items()))
    chosen: dict[int, str] = {}
    for frame, label in picks:
        if len(chosen) < 6 and 0 <= frame <= last:
            chosen.setdefault(frame, label)
    return sorted(chosen.items())


def _review_box(sprite: SpriteDraft) -> dict[str, float]:
    """The canvas region a review frame shows: a text block with room around it for effects that move it, else the canvas."""
    width, height = sprite.composition.width, sprite.composition.height
    if sprite.text is None:
        return {"x": 0, "y": 0, "width": width, "height": height}
    layout = sprite.text.layout
    margin = max(120, layout.height / 2)
    left, top = max(0, layout.x - margin), max(0, layout.y - margin)
    right, bottom = min(width, layout.x + layout.width + margin), min(height, layout.y + layout.height + margin)
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def sample_keywords(sprite: SpriteDraft) -> list[str]:
    """The keywords a text effect's review frames show when its defaults have none, as the workspace preview shows them:
    the text block's `preview_keywords` (the words the Agent marks as keywords of its sample text), else up to two
    characters opening the first non-empty line of the default text (the `text_fit` keyword mount's sample); none for
    other Sprites or when keywords are set. Audit (work 06eb0788): frames keyed on the first two characters showed them
    typing first, so the Executor "fixed" a right keyword-first order into the wrong one."""
    values = sprite.default_parameters
    if sprite.kind != "text" or "keywords" not in sprite.parameter_schema.get("properties", {}) or values.get("keywords"):
        return []
    if sprite.text is not None and sprite.text.preview_keywords:
        return list(sprite.text.preview_keywords)
    line = next((line for line in str(values.get("text", "")).split("\n") if line.strip()), "")
    word = re.search(r"\S{1,2}", line)
    return [word.group(0)] if word else []


def _terms(text: str) -> set[str]:
    """The search terms of ``text``: its words (lower case, jieba's search segmentation) of two or more characters, or
    its single characters when it has no longer word, so a Chinese query needs no spaces between its words."""
    words = {word.strip().lower() for word in jieba.cut_for_search(text)}
    words = {word for word in words if any(char.isalnum() for char in word)}
    return {word for word in words if len(word) > 1} or words


_OMITTED = (
    "{size} bytes of source omitted to keep the snapshot within the request limit. Compose it unchanged by its "
    "draft_id, or write a replacement with preset_create."
)


def _snapshot_bytes(data: dict) -> int:
    """测量模型实际收到的快照体积（UTF-8 字节）。"""
    return len(json.dumps(data, ensure_ascii=False, default=str).encode())


def _bounded_snapshot(data: dict) -> dict:
    """超过预算时从最大的开始省略基线源码，保留草稿句柄、Schema、默认值与编排。

    正常编辑的基线远低于预算，模型可见信息不变；省略的预设仍可按 draft_id 原样组合。
    """
    base = data.get("base") or {}
    if _snapshot_bytes(data) <= _SNAPSHOT_BYTE_BUDGET:
        return data
    if "legacy_code" in base:
        base["legacy_code_omitted"] = _OMITTED.format(size=len(base.pop("legacy_code").encode()))
    presets = base.get("presets", {})
    for draft_id in sorted((key for key, value in presets.items() if "code" in value), key=lambda key: -len(presets[key]["code"])):
        if _snapshot_bytes(data) <= _SNAPSHOT_BYTE_BUDGET:
            break
        presets[draft_id]["code_omitted"] = _OMITTED.format(size=len(presets[draft_id].pop("code").encode()))
    return data


def _ts_code(item: CodeDiagnostic) -> str:
    """TypeScript error number without the optional TS prefix; the isolated worker reports bare numbers."""
    return (item.code or "").removeprefix("TS")


# How the validation worker's `text_fit` names a keyword hit drawn without its tier's look (`keywordLookProblem`).
_KEYWORD_LOOK_LOST = "is drawn without its tier's look"
# Fixed host advice for errors a weak model rarely fixes from compiler text alone; never model-written.
_HINTS = (
    (lambda item: _ts_code(item) in {"2322", "2339", "2345", "2353", "2741"},
     "Props must match parameter_schema: declare every schema property in the component's props type with the same "
     "name and type, read only declared props, and keep default_parameters complete."),
    (lambda item: _ts_code(item) in {"2305", "2307", "2614"} or item.message.startswith(("Import not permitted", "Only React/Remotion imports")),
     "Import only from react and remotion, with named imports such as {AbsoluteFill, useCurrentFrame, interpolate}; "
     "no other module exists in the sandbox."),
    (lambda item: item.message.startswith(("Unsupported capability", "Dynamic imports", "Re-exports")),
     "The sandbox runs plain React/Remotion code: no network, eval, Function, process, globalThis, dynamic import or re-export."),
    (lambda item: item.message.startswith("transparency"),
     "Keep the canvas see-through over the user's video: no opaque full-frame background; build tints from translucent "
     "layers, gradients, vignettes or grain."),
    # A keyword hit drawn without its tier's look is not a layout problem; its message says where the look belongs.
    (lambda item: item.message.startswith("text_fit") and _KEYWORD_LOOK_LOST not in item.message,
     "Lay the text out from its content: wrap it onto several lines inside a bounded width, no fixed-height box with "
     "overflow hidden; use the fontSize parameter as given (the host shrinks long text at render time) and make sure text up to its maxLength fits at the smallest fontSize. "
     "If a whole tier is missing, check effect endpoints: enter and exit must show it at progress 1. An effect triggered by another tier appearing uses cue, whose progress rises from 0 to 1."),
    (lambda item: item.source == "contract" and item.field is not None,
     "parameter_schema must be a self-contained object schema with additionalProperties false, and default_parameters "
     "must satisfy it."),
)
# Errors spelled out in the refusal text; the complete report stays in details.validation.
_LISTED_ERRORS = 5


def _validation_fault(summary: str, report: CodeValidationReport) -> ToolFault:
    """Refuse with the first errors located and quoted inline, plus the fixed hints that apply to them."""
    errors = [item for item in report.diagnostics if item.severity == "error"] or report.diagnostics
    lines = [summary]
    for item in errors[:_LISTED_ERRORS]:
        position = f"{item.range.start.line + 1}:{item.range.start.character + 1}" if item.range else None
        where = " ".join(part for part in (item.file, position, item.code) if part)
        lines.append(f"- {where}: {item.message}" if where else f"- {item.message}")
        if item.excerpt:
            lines.append(f"  > {item.excerpt}")
    if len(errors) > _LISTED_ERRORS:
        lines.append(f"- and {len(errors) - _LISTED_ERRORS} more in details.validation")
    details = {"validation": report.model_dump(mode="json", exclude_unset=True)}
    hints = [text for matches, text in _HINTS if any(matches(item) for item in errors)]
    if hints:
        details["hints"] = hints
    return ToolFault("CODE_VALIDATION_FAILED", "\n".join(lines), details=details)


def _titled(parameters) -> list[dict]:
    """The Agent's bindings as data; fixed-name parameters carry the host's Chinese titles, not the Agent's wording."""
    return [
        item.model_dump(mode="json") | ({"title": FIXED_TITLES[item.name]} if item.name in FIXED_TITLES else {})
        for item in parameters
    ]


def _instance_parameters(sprite: SpriteDraft, config: dict) -> dict[str, dict]:
    """Return each instance's parameters with the current user configuration applied.

    The Sprite snapshot keeps the generation-time values, while manual parameter revisions live only in the accepted
    candidate's ``default_config``. Overlay bound top-level values and nested instance objects before handing the base
    back to the Agent, so a later edit does not silently restore stale appearance values.
    """
    parameters = {item.instance_id: deepcopy(item.parameters) for item in sprite.instances}
    for binding in sprite.bindings:
        if binding.name not in config:
            continue
        for target in binding.targets:
            values = parameters.get(target.instance_id)
            if values is not None:
                values[target.parameter] = deepcopy(config[binding.name])
    for instance_id, values in parameters.items():
        nested = config.get(instance_id)
        if isinstance(nested, dict):
            values.update(deepcopy(nested))
    return parameters


def _text_block(sprite: SpriteDraft, config: dict) -> dict:
    """The accepted base's text block as sprite_compose takes it, over base_* drafts, with the current configuration
    values (``config`` is the base's defaults, user parameter revisions included)."""
    parameters = _instance_parameters(sprite, config)
    two = len(sprite.text.tiers) == 2

    def tier(index: int, instance_id: str) -> dict:
        """One tier with its fonts read from the configuration."""
        prefix = f"line{index + 1}" if two else ""
        name = lambda key: f"{prefix}{key}" if two else key[0].lower() + key[1:]
        return {
            "instance_id": instance_id, "source": {"kind": "draft", "draft_id": f"base_{instance_id}"}, "parameters": parameters[instance_id],
            "font_family": config.get(name("FontFamily")), "font_weight": config.get(name("FontWeight")),
            **({"size_ratio": config.get(f"{prefix}SizeRatio")} if two else {}),
        }

    block = {
        "layout": sprite.text.layout.model_dump(mode="json"), "text": config.get("text"), "font_size": config.get("fontSize"),
        "tiers": [tier(index, instance_id) for index, instance_id in enumerate(sprite.text.tiers)],
        **({"line_gap": config.get("lineGap"), "line_starts": config.get("lineStartFrames")} if two else {}),
        **({"arrangement": config["tierLayout"]} if two and "tierLayout" in config else {}),
        "duration_frames": sprite.composition.duration_frames,
    }
    if sprite.text.preview_keywords:
        block["preview_keywords"] = list(sprite.text.preview_keywords)
    if sprite.text.keyword is not None:
        keyword = sprite.text.keyword
        block["keyword"] = {"instance_id": keyword, "source": {"kind": "draft", "draft_id": f"base_{keyword}"}, "parameters": parameters[keyword]}
    if sprite.text.effects:
        # Effect k's position, length and switch are its configuration parameters, as the user last saved them.
        block["effects"] = [
            {
                "instance_id": item.instance_id, "source": {"kind": "draft", "draft_id": f"base_{item.instance_id}"}, "parameters": parameters[item.instance_id],
                "title": item.title, "slot": item.slot, "target": config.get(f"effect{index}Target"),
                **({"seconds": config.get(f"effect{index}Seconds")} if item.slot != "loop" else {}),
                **({"delay": config[f"effect{index}Delay"]} if f"effect{index}Delay" in config else {}),
                **({"stagger": config[f"effect{index}Stagger"]} if f"effect{index}Stagger" in config else {}), "enabled": config.get(f"effect{index}") is not False,
            }
            for index, item in enumerate(sprite.text.effects, start=1)
        ]
    return block


def _parameter_names(schema: dict) -> list[str]:
    """List the top-level parameters a parameter schema declares, for code-free summaries."""
    return list(schema.get("properties", {}))


def _digest(draft: PresetDraft | SpriteDraft) -> str:
    """Short stable hash of a draft's whole content; the random draft_id never enters it.

    The no-progress guard compares receipts: the same request yields a new draft_id every time, so this is
    what tells a repeated draft apart from one whose source, schema, defaults or instances changed.
    """
    content = json.dumps(draft.model_dump(mode="json", exclude_unset=True), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _apply_edits(code: str, edits: list[dict]) -> str:
    """Apply preset_modify's exact replacements in order, each to the code as the earlier ones left it.

    Every old_str must occur exactly once, so a replacement never lands somewhere the model did not look. A miss
    names the edit, and where a trimmed first line of old_str does occur, so the next call can copy it exactly.
    """
    for index, edit in enumerate(edits):
        old, field = edit["old_str"], f"/changes/edits/{index}/old_str"
        starts, at = [], code.find(old)
        while at != -1:
            starts.append(at)
            at = code.find(old, at + 1)
        if len(starts) > 1:
            lines = ", ".join(str(code.count("\n", 0, start) + 1) for start in starts)
            raise ToolFault("INVALID_ARGUMENT", f"edits[{index}].old_str occurs {len(starts)} times (lines {lines}): "
                            "include a neighbouring line so it matches exactly once.", field=field)
        if not starts:
            first = next((line.strip() for line in old.splitlines() if line.strip()), "")
            near = [number for number, line in enumerate(code.splitlines(), 1) if first and line.strip() == first]
            where = f" Its first line matches line {', '.join(map(str, near))} once spaces are trimmed." if near else ""
            raise ToolFault("INVALID_ARGUMENT", f"edits[{index}].old_str does not occur in the code{' after the earlier edits' if index else ''}: "
                            f"copy it exactly from the source, indentation and line breaks included.{where}", field=field)
        code = code[:starts[0]] + edit["new_str"] + code[starts[0] + len(old):]
    return code


def _line_changes(old: str, new: str) -> tuple[int, int]:
    """Count the lines a copy removed from and added to its source's code, as a line diff shows them."""
    removed = added = 0
    matcher = difflib.SequenceMatcher(None, old.splitlines(), new.splitlines(), autojunk=False)
    for tag, start, end, new_start, new_end in matcher.get_opcodes():
        if tag != "equal":
            removed += end - start
            added += new_end - new_start
    return removed, added


class ToolSession:
    """Keep renderer candidates task-local while PR76 Preset/Sprite records remain immutable."""

    def __init__(
        self, harness, spec, base, budget, directory, images, on_stage, intent
    ):
        """Bind host services without creating another model loop or search role."""
        self.harness, self.budget = harness, budget
        self.spec = spec
        self.directory, self.images = directory, images
        self.on_stage, self.intent = on_stage, intent or {}
        request = self.intent.get("original_request") or {}
        # A new work must last exactly as long as the user chose; an edit starts from an accepted version
        # and keeps deriving its length from the instances.
        self.target_frames = None if spec else (request.get("composition") or {}).get("duration_in_frames")
        # The new-chat switches the user turned on (双栏, keyword effect) bind a new work; edits follow the accepted base.
        self.requested = set() if spec else {name for name in ("two_tier", "keyword_effect") if request.get(name) is True}
        self.kind = spec.sprite_kind if spec else request.get("sprite_kind")
        self.catalog = CatalogStore()
        self.latest_sprite_id: str | None = None
        self.saved_sprites: dict[str, SpriteRecord] = {}
        self.validation_reports: dict[str, RenderValidationReport] = {}
        # Drafts stay host-side and are referenced by handle, so the model never re-sends their source.
        self.preset_drafts: dict[str, PresetDraft] = {}
        self.sprite_drafts: dict[str, SpriteDraft] = {}
        # For an edit, ``spec`` is the accepted version's spec and ``base`` its TemplateCandidate.
        self.base = base
        base_sprite = spec.sprite if spec is not None and base is not None else None
        for instance in base_sprite.instances if base_sprite else []:
            # The accepted base's Presets are reusable as drafts; a snapshot keeps the stored Preset it came from as its source.
            self.preset_drafts[f"base_{instance.instance_id}"] = instance.preset
        # The host holds tier Presets still and effect Presets draw the block's motion, except in edits of a text effect made
        # before configurable effects, whose tier Presets animate themselves (a text block without `still`, or boxes).
        self.still_text = base_sprite is None or (base_sprite.text is not None and base_sprite.text.still is True)
        self.feedback = []
        # Frames of the latest saved Sprite the Executor looks at (path, frame, label), and how many reviews this task had.
        self.review_frames: list[tuple] = []
        self.review_rounds = 0
        self.catalog_backend = "unset"
        self.operation = ""
        self.validator = None
        try:
            from ..tool_validation import ToolValidator
            self.validator = ToolValidator(harness.renderer, self.directory / "tools")
        except ImportError:
            # The validation service is loaded lazily by deployments. Contract-only
            # diagnostics remain available for catalog inspection and compose.
            self.validator = None

    def snapshot(self, *, with_code: bool = False) -> dict:
        """Expose task-local observations as IDs and summaries; source is only read on purpose.

        ``with_code`` adds the accepted base's Preset source for the layer that edits code, largest
        sources first omitted when they would exceed the snapshot budget. Saved Sprites and drafts are
        never repeated here: their handles are what the tools take.
        """
        return _bounded_snapshot({
            "latest_sprite_id": self.latest_sprite_id,
            **({"target_duration_frames": self.target_frames} if self.target_frames else {}),
            "saved_sprites": [
                {
                    "sprite_id": item.sprite_id,
                    "description": item.description,
                    "composition": item.composition.model_dump(mode="json"),
                    "instance_ids": [instance.instance_id for instance in item.instances],
                }
                for item in self.saved_sprites.values()
            ],
            "drafts": {"presets": list(self.preset_drafts), "sprites": list(self.sprite_drafts)},
            "base": self._base_summary(with_code),
            "last_validation": {
                key: report.model_dump(mode="json") for key, report in self.validation_reports.items()
            },
            "preset_backend": self.catalog_backend,
            "feedback": list(self.feedback),
        })

    def _base_summary(self, with_code: bool) -> dict | None:
        """Describe the accepted base as instances over base_* Preset drafts, each source listed once.

        ``text`` and ``instances`` take the shape sprite_compose takes, so an edit re-composes the base by swapping in
        its new draft_id and values instead of rebuilding each entry.
        """
        if self.base is None:
            return None
        sprite = self.spec.sprite if self.spec is not None else None
        if sprite is None:
            # A pre-Sprite version has one component and no Presets to reuse.
            return {"legacy_code": self.base.tsx_code} if with_code else {"legacy": True}
        summary = {
            "description": sprite.description,
            "composition": sprite.composition.model_dump(mode="json"),
            # Re-composing an edit must keep the category and the operator parameters the accepted base exposed;
            # the configuration parameters of a text block are the host's and come back with the block itself.
            **({"kind": sprite.kind} if sprite.kind is not None else {}),
            "parameters": [
                item.model_dump(mode="json") for item in sprite.bindings
                if not any(target.instance_id == TEXT_BLOCK_ID for target in item.targets)
            ],
        }
        if sprite.text is not None:
            summary["text"] = _text_block(sprite, self.base.default_config)
        else:
            parameters = _instance_parameters(sprite, self.base.default_config)
            summary["instances"] = [
                {
                    "instance_id": item.instance_id,
                    "source": {"kind": "draft", "draft_id": f"base_{item.instance_id}"},
                    "parameters": parameters[item.instance_id],
                    "layout": item.layout.model_dump(mode="json"),
                    "timing": item.timing.model_dump(mode="json"),
                }
                for item in sprite.instances
            ]
        if with_code:
            sources: dict[str, str] = {}
            presets = {}
            for item in sprite.instances:
                draft_id = f"base_{item.instance_id}"
                same = sources.get(item.preset.code)
                presets[draft_id] = {"same_code_as": same} if same else item.preset.model_dump(mode="json", exclude_unset=True)
                sources.setdefault(item.preset.code, draft_id)
            summary["presets"] = presets
        return summary

    def _draft(self, drafts: dict, draft_id: str, field: str):
        """Resolve a task-local draft handle; an unknown one lists the handles that exist."""
        try:
            return drafts[draft_id]
        except KeyError:
            raise ToolFault("INVALID_ARGUMENT", f"Unknown draft_id {draft_id}; drafts in this task: {list(drafts) or 'none'}", field=field) from None

    def sprite_draft(self, draft_id: str) -> SpriteDraft:
        """Return the composed Sprite a sprite.create handle refers to."""
        return self._draft(self.sprite_drafts, draft_id, "/draft_id")

    def saved_sprite(self, identifier: str) -> SpriteRecord:
        """Resolve only a Sprite created by this task, preserving publication provenance."""
        try:
            return self.saved_sprites[identifier]
        except KeyError as exc:
            raise ToolFault("SPRITE_NOT_FOUND", f"Unknown task Sprite: {identifier}") from exc

    def validation_for(self, sprite: SpriteRecord | SpriteDraft, *, parameters=None) -> RenderValidationReport | None:
        """Return the exact latest render report for a Sprite and parameter snapshot."""
        values = merge_parameters(sprite.default_parameters, parameters)
        key = self._validation_key(sprite, values)
        report = self.validation_reports.get(key)
        if report is not None:
            return report
        component = ComponentDefinition(
            code=sprite.code,
            parameter_schema=sprite.parameter_schema,
            default_parameters=sprite.default_parameters,
        )
        return self.validation_reports.get(self._component_validation_key(component, sprite.composition.duration_frames, values))

    @staticmethod
    def _component_validation_key(component: ComponentDefinition, duration_frames: int, parameters: dict) -> str:
        """Canonical validation key for a standalone component execution."""
        payload = {
            "code": component.code,
            "schema": component.parameter_schema,
            "defaults": component.default_parameters,
            "duration_frames": duration_frames,
            "parameters": parameters,
        }
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def _validation_key(sprite: SpriteDraft, parameters: dict) -> str:
        """Canonical key binds code, schema, defaults, parameters and duration."""
        payload = {
            "code": sprite.code,
            "schema": sprite.parameter_schema,
            "defaults": sprite.default_parameters,
            "composition": sprite.composition.model_dump(mode="json"),
            "parameters": parameters,
        }
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _recent_presets(self) -> list[PresetRecord]:
        """Order the merged catalog by creation time for listing and missing-ID hints."""
        def created_at(record):
            """旧无时区值按 UTC 处理；不可解析值放末尾，不使整个目录不可读。"""
            try:
                value = datetime.fromisoformat(record.created_at)
                return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
            except (ValueError, OverflowError):
                return datetime.min.replace(tzinfo=UTC)

        return sorted(
            self.catalog.read_presets(),
            key=lambda item: (created_at(item), item.preset_id),
            reverse=True,
        )

    def _find_preset(self, preset_id: str) -> PresetRecord:
        """Resolve a Preset ID from the immutable catalog; unknown IDs fail loudly."""
        record = self.catalog.find_preset(preset_id)
        if record is None and preset_id in self.preset_drafts:
            # A weak model reads or composes a draft handle like a stored ID; say what it is and how to use it.
            raise ToolFault("PRESET_NOT_FOUND", (
                f"{preset_id} is a draft of this task, not a stored Preset: its source is in the snapshot "
                f"(base.presets for base_* drafts, unless omitted for size). Change it with preset_modify {{\"preset_id\": \"{preset_id}\", \"changes\": {{\"edits\": [{{\"old_str\": ..., \"new_str\": ...}}]}}}} "
                f"or compose it with source {{\"kind\": \"draft\", \"draft_id\": \"{preset_id}\"}}."
            ))
        if record is None:
            known = [item.preset_id for item in self._recent_presets()[:10]]
            drafts = f"; draft_ids in this task: {list(self.preset_drafts)}" if self.preset_drafts else ""
            raise ToolFault("PRESET_NOT_FOUND", f"Unknown preset: {preset_id}. Use an ID returned by a tool; recent preset_ids: {known}{drafts}")
        return record

    async def validate_code(self, component: ComponentDefinition) -> CodeValidationReport:
        """Run the isolated PR76 code validator and preserve real diagnostics."""
        diagnostics = validate_component_contract(component)
        if diagnostics:
            return CodeValidationReport(passed=False, diagnostics=diagnostics)
        if self.validator is None:
            raise ToolFault("VALIDATION_UNAVAILABLE", "The isolated TypeScript validator is unavailable.")
        # Imported here: a deployment may drop the validation module, which leaves self.validator None.
        from ..tool_validation import ValidationUnavailable

        try:
            return await self.validator.validate_code(component)
        except ValidationUnavailable as exc:
            raise ToolFault("VALIDATION_UNAVAILABLE", f"The isolated TypeScript validator is unavailable: {exc}"[:3000]) from exc

    async def validate_render(
        self, request: RenderValidationInput, *, transparency: bool = False, lengths: list[int] | None = None, text_fit: bool = False,
        capture=None, text_fit_parameters: dict[str, bool] | None = None,
    ) -> RenderValidationReport:
        """Run the isolated component behavior runner and cache the exact report.

        ``transparency`` adds the canvas-coverage measurement, ``lengths`` a mount at those render lengths (frames),
        ``text_fit`` measures the text layout, with the host's optional ``text_fit_parameters`` patch; ``capture``
        supplies actual animated frames for the Agent. Only requested options are forwarded.
        """
        if self.validator is None:
            raise ToolFault("VALIDATION_UNAVAILABLE", "The isolated behavior validator is unavailable.")
        from ..tool_validation import ValidationUnavailable

        try:
            options = {
                **({"transparency": True} if transparency else {}), **({"lengths": lengths} if lengths else {}),
                **({"text_fit": True} if text_fit else {}), **({"capture": capture} if capture else {}),
                **({"text_fit_parameters": text_fit_parameters} if text_fit_parameters is not None else {}),
            }
            report = await self.validator.validate_render(request, **options)
        except ValidationUnavailable as exc:
            raise ToolFault("VALIDATION_UNAVAILABLE", f"The isolated behavior validator is unavailable: {exc}"[:3000]) from exc
        parameters = merge_parameters(request.component.default_parameters, request.parameters)
        try:
            validate_parameters(request.component.parameter_schema, parameters)
        except Exception:
            # Validator reports this as a failed parameters check; preserve its report.
            pass
        self.validation_reports[self._component_validation_key(request.component, request.duration_frames, parameters)] = report
        return report

    async def create_preset(self, request: PresetCreateInput) -> PresetCreateOutput:
        """Validate and store a new immutable PR76 Preset record."""
        if request.source_preset_id is not None:
            await asyncio.to_thread(self._find_preset, request.source_preset_id)
        if problem := _usage_problem(request):
            raise ToolFault("INVALID_ARGUMENT", problem[1], field=problem[0])
        validation = await self.validate_code(request)
        if not validation.passed:
            raise _validation_fault("Preset code validation failed.", validation)
        record = PresetRecord(
            preset_id=uuid4().hex,
            created_at=datetime.now(UTC).isoformat(),
            **request.model_dump(mode="json", exclude_unset=True),
        )
        # Presets are shared by every device in MySQL; the record is immutable once saved.
        self.catalog_backend = await asyncio.to_thread(self.catalog.append_preset, record)
        return PresetCreateOutput(preset=record, validation=validation)

    def search_presets(self, request: PresetSearchInput) -> PresetSearchOutput:
        """List summaries of the Presets matching the query, best first, and, when asked, only those of one role or
        effect slot. A Preset's score is the number of the query's terms (`_terms`) its description or usage title
        contains, and one without any is left out; among equal scores the ones accepted versions used and the asset
        library published come first (`CatalogStore.use_counts`), then the newest. An empty query lists everything in
        that order.

        With ``preset_id`` the matching full record (including code) is returned as well, so
        reading a Preset never requires a write-shaped tool.
        """
        full = self._find_preset(request.preset_id) if request.preset_id is not None else None
        terms = _terms(request.query) if request.query.strip() else set()
        output = PresetSearchOutput(**({"preset": full} if full else {}), presets=[], has_more=False)

        def fits_reply():
            """计入 ToolResult 及保存 content 字符串时的二次转义，不截断完整源码。"""
            content = json.dumps({"ok": True, "data": output.model_dump(mode="json", exclude_unset=True)}, ensure_ascii=False)
            return len(json.dumps(content, ensure_ascii=False).encode("utf-8")) <= _SEARCH_BYTE_BUDGET

        if not fits_reply():
            raise ToolFault("RESOURCE_LIMIT_EXCEEDED", "The full Preset exceeds the tool reply size limit.")
        presets = self._recent_presets()
        counts = self.catalog.use_counts() if presets else {}
        ranked = []
        for item in presets:
            if (request.role is not None and item.role != request.role) or (request.slot is not None and (item.usage is None or item.usage.slot != request.slot)):
                continue
            text = f"{item.description} {item.usage.title if item.usage else ''}".lower()
            score = sum(term in text for term in terms)
            if terms and not score:
                continue
            accepted, published = counts.get(item.preset_id, (0, 0))
            ranked.append((score, published, accepted, item))
        # A stable sort keeps the newest first among equals.
        ranked.sort(key=lambda entry: (-entry[0], -entry[1], -entry[2]))
        for _score, published, accepted, item in ranked:
            if len(output.presets) >= request.limit:
                output.has_more = True
                break
            output.presets.append(PresetSummary(
                preset_id=item.preset_id,
                description=item.description,
                parameter_names=_parameter_names(item.parameter_schema),
                **({"role": item.role} if item.role else {}), **({"usage": item.usage} if item.usage else {}),
                accepted_uses=accepted, published_uses=published,
            ))
            if not fits_reply():
                output.presets.pop()
                if not output.presets and full is None:
                    raise ToolFault("RESOURCE_LIMIT_EXCEEDED", "A Preset summary exceeds the tool reply size limit.")
                output.has_more = True
                break
        return output

    def modify_preset(self, request: PresetModifyInput) -> PresetModifyOutput:
        """Copy a stored Preset or a task draft with exact code edits or whole-field replacements; unknown IDs list what exists.

        A task draft (``base_*`` or an earlier modify result) is what an edit naturally starts from, so its
        handle is accepted as ``preset_id``; the copy keeps that draft's catalog source, if it has one. The receipt
        counts the code lines the copy changed, and notes when an edit copied a stored Preset instead of the
        accepted version's ``base_*`` draft (seen in a replay: the copy dropped what earlier versions had changed).
        """
        changes = request.changes.model_dump(mode="json", exclude_unset=True)
        if not changes:
            raise ToolFault("INVALID_ARGUMENT", "changes must replace at least one field", field="/changes")
        if "code" in changes and "edits" in changes:
            raise ToolFault("INVALID_ARGUMENT", "Give either edits (exact replacements in the copied code) or code (the whole new source), not both.",
                            field="/changes/edits")
        notice = None
        if request.preset_id in self.preset_drafts:
            original = self.preset_drafts[request.preset_id]
            base = original.model_dump(mode="json", exclude_unset=True)
            source = original.source_preset_id
        else:
            record = self._find_preset(request.preset_id)
            base = record.model_dump(mode="json", exclude={"preset_id", "created_at"}, exclude_unset=True)
            source = record.preset_id
            accepted = {key: draft for key, draft in self.preset_drafts.items() if key.startswith("base_")}
            # A Sprite snapshot keeps no stored preset_id, so the Preset a base draft came from is known by its content.
            drawn = lambda item: (item.code, item.parameter_schema, item.default_parameters)
            if accepted and all(drawn(draft) != drawn(record) for draft in accepted.values()):
                notice = (f"{request.preset_id} is a stored Preset, not one the accepted version is built from ({', '.join(accepted)}), "
                          "so changes made in earlier versions are not in this copy. To change an element of the accepted version, copy its "
                          "base_* draft instead; an element the base does not have may start from a stored Preset.")
        base.pop("source_preset_id", None)
        if "edits" in changes:
            changes["code"] = _apply_edits(base["code"], changes.pop("edits"))
        draft = PresetDraft(**{**base, **changes, **({"source_preset_id": source} if source else {})})
        if problem := _usage_problem(draft):
            raise ToolFault("INVALID_ARGUMENT", problem[1], field="/changes" + problem[0])
        diagnostics = validate_component_contract(draft)
        if diagnostics:
            raise ToolFault("INVALID_ARGUMENT", "The edited Preset violates the component contract.",
                            field="/changes", details={"diagnostics": [item.model_dump(mode="json", exclude_unset=True) for item in diagnostics]})
        draft_id = "preset_draft_" + uuid4().hex[:12]
        self.preset_drafts[draft_id] = draft
        removed, added = _line_changes(base["code"], draft.code)
        return PresetModifyOutput(draft_id=draft_id, digest=_digest(draft), preset=PresetDraftSummary(
            description=draft.description, parameter_names=_parameter_names(draft.parameter_schema),
            **({"source_preset_id": source} if source else {}), **({"role": draft.role} if draft.role else {}),
        ), code_lines_removed=removed, code_lines_added=added, **({"notice": notice} if notice else {}))

    async def create_sprite(self, draft_id: str) -> SpriteCreateOutput:
        """Validate and store the composed Sprite a sprite.compose handle refers to.

        Only compose_sprite writes Sprite drafts, from resolved Presets, so their code, schema, defaults
        and timing agree by construction; the draft is not bundled a second time just to compare.
        """
        sprite = self.sprite_draft(draft_id)
        # Bundling erases TypeScript annotations; validate each distinct original module before its generated
        # adapter. Instances of one Preset share a module, so its check runs once.
        # A text-block Preset is checked with its role, so the call site passes the props the host gives it.
        checked: set[str] = set()
        for instance in sprite.instances:
            component = instance.preset
            key = component.model_dump_json(include={"code", "parameter_schema", "default_parameters", "role"})
            if key in checked:
                continue
            checked.add(key)
            original = await self.validate_code(component)
            if not original.passed:
                raise _validation_fault(f"Source Preset of instance {instance.instance_id} does not typecheck.", original)
        validation = await self.validate_code(
            ComponentDefinition(
                code=sprite.code,
                parameter_schema=sprite.parameter_schema,
                default_parameters=sprite.default_parameters,
            )
        )
        if not validation.passed:
            raise _validation_fault("Sprite code validation failed.", validation)
        store = FontStore.active
        problems = category_problems(
            sprite.kind, sprite.parameter_schema, {item.name for item in sprite.bindings}, sprite.default_parameters,
            fonts=set(store.families()) if store is not None else None, requested=self.requested,
        )
        if problems:
            raise ToolFault("CATEGORY_PARAMETERS_MISSING", agent_message(sprite.kind, problems), details={
                "missing": [{"name": item.name, "reason": item.reason, **({"schema": item.rule.example} if item.rule else {})} for item in problems],
            })
        # The Agent chose `fontFamily` from the library's enum; the stored Sprite keeps a plain string so that fonts uploaded
        # later stay selectable (the live library is the check). Everything from here on, including the host's final
        # validation, sees the stored form.
        sprite = sprite.model_copy(update={"parameter_schema": open_font_schema(sprite.parameter_schema)})
        frames = await self._require_visible_video(sprite)
        record = SpriteRecord(
            sprite_id=uuid4().hex,
            created_at=datetime.now(UTC).isoformat(),
            **sprite.model_dump(mode="json", exclude_unset=True),
        )
        self.saved_sprites[record.sprite_id] = record
        self.latest_sprite_id = record.sprite_id
        # Frames of an earlier Sprite would mislead, so they always make way for this one's (or none).
        self.review_frames = frames
        review = {}
        if frames:
            self.review_rounds += 1
            keywords = sample_keywords(sprite)
            review["review"] = SpriteReview(frames=[ReviewFrame(image=len(self.images) + index, frame=frame, label=label)
                                                    for index, (_path, frame, label) in enumerate(frames, start=1)],
                                            rounds_left=VISUAL_REVIEW_ROUNDS - self.review_rounds, **({"keywords": keywords} if keywords else {}))
        return SpriteCreateOutput(sprite_id=record.sprite_id, created_at=record.created_at, composition=record.composition, validation=validation, **review)

    async def _require_visible_video(self, sprite: SpriteDraft) -> list[tuple]:
        """Refuse a Sprite that hides the video in most sampled frames, or throws at another render length, while the Executor can still fix it.

        Sprites are composited over the user's own video, so a full-frame opaque layer is never what a filter
        or an effect wants; the bus renders them at the final video's length, not the preview's. This is the browser mount the host needs anyway: the report is cached under the key
        the final host check reads, so completing the task does not mount the Sprite a second time. While the task has
        reviews left the same mount captures `review_frames`, returned as (path, frame, label) for those it captured.
        """
        from ..tool_validation import FrameCapture, render_lengths

        capture = None
        if self.review_rounds < VISUAL_REVIEW_ROUNDS:
            picks, box = review_frames(sprite), _review_box(sprite)
            keywords = sample_keywords(sprite)
            capture = FrameCapture([frame for frame, _ in picks], box, min(1, _REVIEW_SIDE / max(box["width"], box["height"])),
                                   self.directory / "review" / uuid4().hex, {"keywords": keywords} if keywords else None)

        report = await self.validate_render(
            RenderValidationInput(
                component=ComponentDefinition(
                    code=sprite.code,
                    parameter_schema=sprite.parameter_schema,
                    default_parameters=sprite.default_parameters,
                ),
                duration_frames=sprite.composition.duration_frames,
            ),
            transparency=True,
            # The bus renders a Sprite at the final video's length; a text effect shorter than the static threshold is
            # frozen at its preview length instead, so its shortest played length is that threshold.
            lengths=render_lengths(sprite.composition.fps, text=sprite.kind == "text"),
            text_fit=sprite.kind == "text",
            # A cue can intentionally remove one tier as another arrives. Measure layout without that cue, but
            # keep enter/exit wrappers: they must still show all text at progress 1, rejecting reversed transitions.
            # Presence of this host-only patch also permits independent tier timing in a host-composed still block.
            **({"text_fit_parameters": {f"effect{index}": False for index, effect in enumerate(sprite.text.effects, start=1)
                                        if effect.slot == "cue"}} if sprite.text is not None and sprite.text.still else {}),
            **({"capture": capture} if capture else {}),
        )
        if report.passed:
            shots = [(capture.directory / f"frame-{frame}.png", frame, label) for frame, label in picks] if capture else []
            return [shot for shot in shots if shot[0].is_file()]
        diagnostics = [
            CodeDiagnostic(source="contract", severity="error", message=f"{item.name}: {item.message}" if item.message else item.name)
            for item in report.checks
            if item.status != "passed"
        ] or [CodeDiagnostic(source="contract", severity="error", message="Sprite render validation failed.")]
        raise _validation_fault("Sprite render validation failed.", CodeValidationReport(passed=False, diagnostics=diagnostics))

    async def _resolve(self, instance_id: str, source, parameters: dict | None, field: str) -> tuple[dict, dict, dict]:
        """Resolve one composed Preset: its snapshot, where it came from and its effective parameters (``field`` points at the instance)."""
        if source.kind == "stored":
            stored = await asyncio.to_thread(self._find_preset, source.preset_id)
            # The snapshot names the stored Preset it was taken from, so the version records which Presets it used.
            preset = {**stored.model_dump(mode="json", exclude={"preset_id", "created_at"}, exclude_unset=True), "source_preset_id": stored.preset_id}
            origin = {"preset_id": stored.preset_id}
        else:
            draft = self._draft(self.preset_drafts, source.draft_id, f"{field}/source/draft_id")
            preset = draft.model_dump(mode="json", exclude_unset=True)
            origin = {"draft_id": source.draft_id}
        try:
            diagnostics = validate_component_contract(ComponentDefinition(
                code=preset["code"], parameter_schema=preset["parameter_schema"], default_parameters=preset["default_parameters"]
            ))
            if diagnostics:
                raise ValueError(diagnostics[0].message)
            effective = merge_parameters(preset["default_parameters"], parameters)
            validate_parameters(preset["parameter_schema"], effective)
        except Exception as exc:
            raise ToolFault("INVALID_ARGUMENT", f"Invalid parameters for {instance_id}: {str(exc)[:300]}", field=f"{field}/parameters") from exc
        return preset, origin, effective

    def _check_length(self, duration: int, field: str) -> None:
        """A new work lasts exactly the length the user chose: the last instance's end, or the text block's duration."""
        if self.target_frames and duration != self.target_frames:
            fix = (f"the text block lasts {duration} frames: set its duration_frames to exactly {self.target_frames}." if field.startswith("/text")
                   else f"the last instance ends at frame {duration}: retime the instances so the latest start_frame + duration_frames is exactly {self.target_frames}.")
            raise ToolFault("INVALID_ARGUMENT", f"The user chose {self.target_frames} frames ({self.target_frames / 30:g} s) but {fix}", field=field)

    async def compose_sprite(self, request) -> SpriteComposeOutput:
        """Resolve immutable Presets, generate deterministic local-frame Sprite code and keep it as a draft.

        A text effect is composed from a text block (`request.text`), which the host lays out; every other Sprite, and
        an edit that keeps an older text effect's boxes, places its instances by their own layout. The review frames of
        the last saved Sprite go: they no longer show what is being composed, and only `sprite.create` renders it.
        """
        self.review_frames = []
        if request.text is not None:
            return await self._compose_text(request)
        if request.kind == "text" and self.spec is None:
            raise ToolFault("INVALID_ARGUMENT", "Compose a text effect from a text block: pass \"text\" {layout, text, font_size, tiers, ...} "
                            "with text-role Presets for the tiers (and a keyword-role Preset for a keyword effect) instead of instances.", field="/text")
        if not request.instances:
            raise ToolFault("INVALID_ARGUMENT", "Give at least one instance.", field="/instances")
        seen: set[str] = set()
        resolved: list[dict] = []
        summaries: list[InstanceSummary] = []
        duration = 0
        for index, instance in enumerate(request.instances):
            if instance.instance_id in seen or instance.instance_id in {"__proto__", "prototype", "constructor"}:
                raise ToolFault("INVALID_ARGUMENT", "instance_id must be unique and safe", field=f"/instances/{index}/instance_id")
            seen.add(instance.instance_id)
            preset, origin, parameters = await self._resolve(instance.instance_id, instance.source, instance.parameters, f"/instances/{index}")
            end = instance.timing.start_frame + instance.timing.duration_frames
            duration = max(duration, end)
            resolved.append({
                "instance_id": instance.instance_id,
                "preset": preset,
                "parameters": parameters,
                "layout": instance.layout.model_dump(mode="json"),
                "timing": instance.timing.model_dump(mode="json"),
            })
            summaries.append(InstanceSummary(
                instance_id=instance.instance_id,
                **origin,
                parameter_names=_parameter_names(preset["parameter_schema"]),
                layout=instance.layout,
                timing=instance.timing,
            ))
        self._check_length(duration, "/instances")
        bindings = _titled(request.parameters)
        # 打包器是同步子进程；放到线程里执行，避免阻塞同进程的 SSE 与其它请求。
        code, schema, defaults = await asyncio.to_thread(compose_source, resolved, bindings)
        self._check_bindings(request.parameters, schema)
        return self._keep(SpriteDraft(
            description=request.description,
            code=code,
            parameter_schema=schema,
            default_parameters=defaults,
            composition={"width": 1080, "height": 1920, "fps": 30, "duration_frames": duration},
            instances=resolved,
            kind=request.kind,
            bindings=bindings,
        ), summaries)

    async def _compose_text(self, request) -> SpriteComposeOutput:
        """Compose a text effect from its text block: one tier or an upper and a lower tier, each drawn by a text-role
        Preset, an optional keyword-role Preset for the keyword hit and effect-role Presets wrapping positions of the
        block. The host owns the configuration parameters."""
        block = request.text
        if request.kind != "text":
            raise ToolFault("INVALID_ARGUMENT", "Only a text effect (kind text) has a text block.", field="/kind")
        block = block.model_copy(update={"effects": [await self._with_usage(effect, index) for index, effect in enumerate(block.effects)]})
        if request.instances:
            raise ToolFault("INVALID_ARGUMENT", "A text effect is its text block alone: draw decorations inside the tier Presets or as effects of the block and leave instances empty.", field="/instances")
        lines = block.text.split("\n")
        tiers = len(block.tiers)
        if len(lines) != tiers or not all(line.strip() for line in lines):
            raise ToolFault("INVALID_ARGUMENT", f"text has {len(lines)} lines for {tiers} tiers: a one-tier text is one non-empty line and a two-tier "
                            "text (双栏) is exactly \"<upper>\\n<lower>\", both non-empty.", field="/text/text")
        for index, word in enumerate(block.preview_keywords or []):
            if word not in block.text:
                raise ToolFault("INVALID_ARGUMENT", f"preview_keywords names words of the default text; {word!r} is not in it.", field=f"/text/preview_keywords/{index}")
        if "two_tier" in self.requested and tiers != 2:
            raise ToolFault("INVALID_ARGUMENT", "The user switched on 双栏 (original_request.two_tier): give the text block two tiers, upper and lower.", field="/text/tiers")
        if "keyword_effect" in self.requested and block.keyword is None:
            raise ToolFault("INVALID_ARGUMENT", "The user switched on the keyword effect (original_request.keyword_effect): give the text block a keyword-role Preset.", field="/text/keyword")
        if tiers == 2:
            # Overlapping tiers keep no gap between them.
            for name, value in (("line_gap", 0 if block.arrangement == "overlap" else block.line_gap), ("line_starts", block.line_starts)):
                if value is None:
                    raise ToolFault("INVALID_ARGUMENT", f"A two-tier text needs {name}.", field=f"/text/{name}")
            starts = block.line_starts
            if len(starts) != 2 or starts[0] != 0 or starts[1] < starts[0]:
                raise ToolFault("INVALID_ARGUMENT", "line_starts has two frames, the first 0 and the second not earlier (for example [0, 30]).", field="/text/line_starts")
            # The preview plays the default starts; a lower tier starting at the end would never be seen there.
            if starts[1] >= block.duration_frames:
                raise ToolFault("INVALID_ARGUMENT", f"The lower tier starts at frame {starts[1]} but the block lasts {block.duration_frames} frames: "
                                "start it before the end so it shows.", field="/text/line_starts")
            for index, tier in enumerate(block.tiers):
                if tier.size_ratio is None:
                    raise ToolFault("INVALID_ARGUMENT", "Each tier of a two-tier text needs size_ratio (0.4 to 2, a ratio of font_size).", field=f"/text/tiers/{index}/size_ratio")
        else:
            for name, value in (("arrangement", block.arrangement), ("line_gap", block.line_gap), ("line_starts", block.line_starts)):
                if value is not None:
                    raise ToolFault("INVALID_ARGUMENT", f"Only a two-tier text takes {name}.", field=f"/text/{name}")
            if block.tiers[0].size_ratio not in (None, 1):
                raise ToolFault("INVALID_ARGUMENT", "A one-tier text renders at font_size; leave size_ratio out.", field="/text/tiers/0/size_ratio")
        for index, effect in enumerate(block.effects):
            if effect.target not in EFFECT_TARGETS[tiers]:
                raise ToolFault("INVALID_ARGUMENT", f"A one-tier text has no {effect.target} position: an effect wraps {' or '.join(EFFECT_TARGETS[1])} "
                                "(tiers, line1 and line2 are positions of a two-tier text).", field=f"/text/effects/{index}/target")
            if effect.slot == "cue" and tiers != 2:
                raise ToolFault("INVALID_ARGUMENT", "A cue effect plays when the lower tier appears, so only a two-tier text has one.", field=f"/text/effects/{index}/slot")
            if effect.stagger is not None and (effect.slot == "loop" or effect.target not in GLYPH_TARGETS):
                raise ToolFault("INVALID_ARGUMENT", "stagger spreads an entrance, exit or cue over the glyphs, so only such an effect at chars, keywordChars "
                                "or plainChars takes it.", field=f"/text/effects/{index}/stagger")
            if effect.slot != "loop" and effect.target in GLYPH_TARGETS and effect.stagger is None:
                raise ToolFault("INVALID_ARGUMENT", "Choose stagger explicitly here or in the Preset's usage: 0 shows all glyphs at this position together; "
                                "1 animates them one after another. A plainChars fade for the whole non-keyword part needs 0, not 1.",
                                field=f"/text/effects/{index}/stagger")
            if effect.delay is not None and effect.slot not in {"enter", "cue"}:
                raise ToolFault("INVALID_ARGUMENT", "Only an entrance or a cue effect waits: leave delay out of an exit or a looping effect.",
                                field=f"/text/effects/{index}/delay")
            if effect.slot == "loop" and effect.seconds is not None:
                raise ToolFault("INVALID_ARGUMENT", "A looping effect lasts as long as its position shows: leave seconds out (only enter and exit "
                                "effects have a length).", field=f"/text/effects/{index}/seconds")
        # Held tiers only move through effects, so every such text has an entrance and an exit users can switch off.
        missing = [slot for slot in ("enter", "exit") if self.still_text and not any(effect.slot == slot for effect in block.effects)]
        if missing:
            raise ToolFault("INVALID_ARGUMENT", f"The text block has no {' and no '.join(missing)} effect: the host holds its tiers still, so give it at least one enter "
                            "and one exit effect Preset (role effect), such as a fade with opacity = progress listed as both, or the motion the request or "
                            "reference shows. For text the user wants still, keep them with enabled false so users can switch them on.", field="/text/effects")
        self._check_length(block.duration_frames, "/text/duration_frames")
        store = FontStore.active
        fonts = store.families() if store is not None else sorted({tier.font_family for tier in block.tiers})
        layout = {**block.layout.model_dump(mode="json"), "z_index": 0}
        timing = {"start_frame": 0, "duration_frames": block.duration_frames}
        slots = [(tier, "text", f"/text/tiers/{index}") for index, tier in enumerate(block.tiers)]
        if block.keyword is not None:
            slots.append((block.keyword, "keyword", "/text/keyword"))
        slots += [(effect, "effect", f"/text/effects/{index}") for index, effect in enumerate(block.effects)]
        # Each instance's Preset props sit under its instance_id next to the host's configuration parameters, so an id
        # such as fontSize, keywordEffect or effect1 would replace that parameter.
        reserved = {TEXT_BLOCK_ID, *FIXED_TITLES, "__proto__", "prototype", "constructor"}
        seen: set[str] = set()
        resolved: list[dict] = []
        summaries: list[InstanceSummary] = []
        for slot, role, field in slots:
            if slot.instance_id in seen or slot.instance_id in reserved or EFFECT_PARAMETER.match(slot.instance_id):
                raise ToolFault("INVALID_ARGUMENT", f"instance_id {slot.instance_id!r} must be unique, safe and not a parameter name of the text block "
                                "(text, fontSize, keywords, keywordEffect, effect1, ...): name the tiers upper and lower, the keyword Preset keyword "
                                "and each effect by what it does (fadeIn, glow), say", field=f"{field}/instance_id")
            seen.add(slot.instance_id)
            if role == "text" and slot.font_family not in fonts:
                raise ToolFault("INVALID_ARGUMENT", f"font_family {slot.font_family!r} is not in the font library: {fonts}", field=f"{field}/font_family")
            preset, origin, parameters = await self._resolve(slot.instance_id, slot.source, slot.parameters, field)
            if preset.get("role") != role:
                raise ToolFault("INVALID_ARGUMENT", f"{slot.instance_id} needs a Preset with role {role!r} (written for the text block), "
                                f"not role {preset.get('role')!r}: create one with preset_create role {role!r}.", field=f"{field}/source")
            resolved.append({"instance_id": slot.instance_id, "preset": preset, "parameters": parameters, "layout": layout, "timing": timing})
            summaries.append(InstanceSummary(instance_id=slot.instance_id, **origin, parameter_names=_parameter_names(preset["parameter_schema"]),
                                             layout=layout, timing=timing))
        bindings = _titled(request.parameters)
        drawn = tiers + (1 if block.keyword else 0)
        code, schema, defaults, host = await asyncio.to_thread(
            compose_text_source, block.model_dump(mode="json", exclude_none=True), resolved[:tiers], resolved[tiers] if block.keyword else None,
            resolved[drawn:], bindings, fonts, still=self.still_text,
        )
        self._check_bindings(request.parameters, schema)
        return self._keep(SpriteDraft(
            description=request.description,
            code=code,
            parameter_schema=schema,
            default_parameters=defaults,
            composition={"width": 1080, "height": 1920, "fps": 30, "duration_frames": block.duration_frames},
            instances=resolved,
            kind="text",
            bindings=[*host, *bindings],
            text={"layout": block.layout.model_dump(mode="json"), "tiers": [tier.instance_id for tier in block.tiers],
                  **({"keyword": block.keyword.instance_id} if block.keyword else {}),
                  "effects": [{"instance_id": item.instance_id, "title": item.title, "slot": item.slot} for item in block.effects],
                  **({"preview_keywords": block.preview_keywords} if block.preview_keywords else {}),
                  **({"still": True} if self.still_text else {})},
        ), summaries)

    async def _with_usage(self, effect, index: int):
        """Fill omitted usage fields, including glyph timing only where the effective slot and target support it."""
        missing = [name for name in ("title", "slot", "target") if getattr(effect, name) is None]
        needs_stagger = effect.slot != "loop" and effect.target in GLYPH_TARGETS and effect.stagger is None
        if not missing and not needs_stagger and (effect.seconds is not None or effect.slot == "loop"):
            return effect
        field = f"/text/effects/{index}"
        source = effect.source
        preset = (await asyncio.to_thread(self._find_preset, source.preset_id) if source.kind == "stored"
                  else self._draft(self.preset_drafts, source.draft_id, f"{field}/source/draft_id"))
        usage = preset.usage
        if usage is None:
            if missing:
                raise ToolFault("INVALID_ARGUMENT", f"{effect.instance_id} leaves out {', '.join(missing)} and its Preset records no usage to take them from: give them.",
                                field=f"{field}/{missing[0]}")
            return effect
        filled = {name: getattr(usage, name) for name in missing}
        if effect.seconds is None and (effect.slot or usage.slot) != "loop" and usage.seconds is not None:
            filled["seconds"] = usage.seconds
        if effect.stagger is None and (effect.slot or usage.slot) != "loop" and (effect.target or usage.target) in GLYPH_TARGETS and usage.stagger is not None:
            filled["stagger"] = usage.stagger
        return effect.model_copy(update=filled)

    def _check_bindings(self, parameters, schema: dict) -> None:
        """Every bound style parameter needs the bounded schema the publish check can sample."""
        for index, binding in enumerate(parameters):
            reason = parameter_problems(schema["properties"][binding.name], binding.name)
            if reason:
                raise ToolFault("INVALID_ARGUMENT", f"Parameter {binding.name} {reason}: give the bound Preset prop a bounded schema", field=f"/parameters/{index}")

    def _keep(self, sprite: SpriteDraft, summaries: list[InstanceSummary]) -> SpriteComposeOutput:
        """Keep a composed draft under a new handle and summarize it without source."""
        draft_id = "sprite_draft_" + uuid4().hex[:12]
        self.sprite_drafts[draft_id] = sprite
        return SpriteComposeOutput(
            draft_id=draft_id,
            digest=_digest(sprite),
            description=sprite.description,
            composition=sprite.composition,
            kind=sprite.kind,
            instances=summaries,
            parameter_names=[item.name for item in sprite.bindings],
            default_parameters=sprite.default_parameters,
            code_bytes=len(sprite.code.encode()),
        )

    async def execute(self, name, args, catalog):
        """Dispatch one exact registered PR76 handler; old find/search/combine aliases do not exist."""
        await asyncio.sleep(0)
        self.operation = name
        from .registry import get_tool

        result = await get_tool(name).invoke(self, args)
        if result["ok"]:
            return result["data"]
        error = result["error"]
        raise ToolFault(error["code"], error["message"], field=error.get("field"), details=error.get("details"))
