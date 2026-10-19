"""Task-owned creation tools: immutable Presets, composed Sprites and host checks."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4
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
    SpriteComposeOutput,
    SpriteDraft,
    SpriteRecord,
    SpriteCreateOutput,
)
from ..categories import FIXED_TITLES, agent_message, category_problems, parameter_problems
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
    (lambda item: item.message.startswith("text_fit"),
     "Lay the text out from its content: wrap it onto several lines inside a bounded width, no fixed-height box with "
     "overflow hidden; use the fontSize parameter as given (the host shrinks long text at render time) and make sure text up to its maxLength fits at the smallest fontSize."),
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


def _text_block(sprite: SpriteDraft, config: dict) -> dict:
    """The accepted base's text block as sprite_compose takes it, over base_* drafts, with the current configuration
    values (``config`` is the base's defaults, user parameter revisions included)."""
    parameters = {item.instance_id: item.parameters for item in sprite.instances}
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
        "duration_frames": sprite.composition.duration_frames,
    }
    if sprite.text.keyword is not None:
        keyword = sprite.text.keyword
        block["keyword"] = {"instance_id": keyword, "source": {"kind": "draft", "draft_id": f"base_{keyword}"}, "parameters": parameters[keyword]}
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
            # The accepted base's Presets are reusable as drafts; their stored IDs are not kept in a Sprite snapshot.
            self.preset_drafts[f"base_{instance.instance_id}"] = instance.preset
        self.feedback = []
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
        """Describe the accepted base as instances over base_* Preset drafts, each source listed once."""
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
            summary["instances"] = [
                {
                    "instance_id": item.instance_id,
                    "draft_id": f"base_{item.instance_id}",
                    "parameters": item.parameters,
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
                f"(base.presets for base_* drafts, unless omitted for size). Change it with preset_modify {{\"preset_id\": \"{preset_id}\", \"changes\": ...}} "
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
    ) -> RenderValidationReport:
        """Run the isolated component behavior runner and cache the exact report.

        ``transparency`` adds the canvas-coverage measurement, ``lengths`` a mount at those render lengths (frames)
        and ``text_fit`` the longest-text layout measurement of a text effect. The options are only forwarded when
        asked for, so a validator that predates them keeps working.
        """
        if self.validator is None:
            raise ToolFault("VALIDATION_UNAVAILABLE", "The isolated behavior validator is unavailable.")
        from ..tool_validation import ValidationUnavailable

        try:
            options = {
                **({"transparency": True} if transparency else {}), **({"lengths": lengths} if lengths else {}),
                **({"text_fit": True} if text_fit else {}),
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
        """List summaries, newest first, whose description contains every whitespace-separated word of the query
        (case-insensitive) and, when asked, that play the given role in a text block.

        With ``preset_id`` the matching full record (including code) is returned as well, so
        reading a Preset never requires a write-shaped tool.
        """
        full = self._find_preset(request.preset_id) if request.preset_id is not None else None
        words = request.query.lower().split()
        output = PresetSearchOutput(**({"preset": full} if full else {}), presets=[], has_more=False)

        def fits_reply():
            """计入 ToolResult 及保存 content 字符串时的二次转义，不截断完整源码。"""
            content = json.dumps({"ok": True, "data": output.model_dump(mode="json", exclude_unset=True)}, ensure_ascii=False)
            return len(json.dumps(content, ensure_ascii=False).encode("utf-8")) <= _SEARCH_BYTE_BUDGET

        if not fits_reply():
            raise ToolFault("RESOURCE_LIMIT_EXCEEDED", "The full Preset exceeds the tool reply size limit.")
        for item in self._recent_presets():
            if not all(word in item.description.lower() for word in words) or (request.role is not None and item.role != request.role):
                continue
            if len(output.presets) >= request.limit:
                output.has_more = True
                break
            output.presets.append(PresetSummary(
                preset_id=item.preset_id,
                description=item.description,
                parameter_names=_parameter_names(item.parameter_schema),
                **({"role": item.role} if item.role else {}),
            ))
            if not fits_reply():
                output.presets.pop()
                if not output.presets and full is None:
                    raise ToolFault("RESOURCE_LIMIT_EXCEEDED", "A Preset summary exceeds the tool reply size limit.")
                output.has_more = True
                break
        return output

    def modify_preset(self, request: PresetModifyInput) -> PresetModifyOutput:
        """Copy a stored Preset or a task draft with whole-field replacements; unknown IDs list what exists.

        A task draft (``base_*`` or an earlier modify result) is what an edit naturally starts from, so its
        handle is accepted as ``preset_id``; the copy keeps that draft's catalog source, if it has one.
        """
        changes = request.changes.model_dump(mode="json", exclude_unset=True)
        if not changes:
            raise ToolFault("INVALID_ARGUMENT", "changes must replace at least one field", field="/changes")
        if request.preset_id in self.preset_drafts:
            original = self.preset_drafts[request.preset_id]
            base = original.model_dump(mode="json", exclude_unset=True)
            source = original.source_preset_id
        else:
            record = self._find_preset(request.preset_id)
            base = record.model_dump(mode="json", exclude={"preset_id", "created_at"}, exclude_unset=True)
            source = record.preset_id
        base.pop("source_preset_id", None)
        draft = PresetDraft(**{**base, **changes, **({"source_preset_id": source} if source else {})})
        diagnostics = validate_component_contract(draft)
        if diagnostics:
            raise ToolFault("INVALID_ARGUMENT", "The edited Preset violates the component contract.",
                            field="/changes", details={"diagnostics": [item.model_dump(mode="json", exclude_unset=True) for item in diagnostics]})
        draft_id = "preset_draft_" + uuid4().hex[:12]
        self.preset_drafts[draft_id] = draft
        return PresetModifyOutput(draft_id=draft_id, digest=_digest(draft), preset=PresetDraftSummary(
            description=draft.description, parameter_names=_parameter_names(draft.parameter_schema),
            **({"source_preset_id": source} if source else {}), **({"role": draft.role} if draft.role else {}),
        ))

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
            fonts={font.family for font in store.listing()} if store is not None else None, requested=self.requested,
        )
        if problems:
            raise ToolFault("CATEGORY_PARAMETERS_MISSING", agent_message(sprite.kind, problems), details={
                "missing": [{"name": item.name, "reason": item.reason, **({"schema": item.rule.example} if item.rule else {})} for item in problems],
            })
        # The Agent chose `fontFamily` from the library's enum; the stored Sprite keeps a plain string so that fonts uploaded
        # later stay selectable (the live library is the check). Everything from here on, including the host's final
        # validation, sees the stored form.
        sprite = sprite.model_copy(update={"parameter_schema": open_font_schema(sprite.parameter_schema)})
        await self._require_visible_video(sprite)
        record = SpriteRecord(
            sprite_id=uuid4().hex,
            created_at=datetime.now(UTC).isoformat(),
            **sprite.model_dump(mode="json", exclude_unset=True),
        )
        self.saved_sprites[record.sprite_id] = record
        self.latest_sprite_id = record.sprite_id
        return SpriteCreateOutput(sprite_id=record.sprite_id, created_at=record.created_at, composition=record.composition, validation=validation)

    async def _require_visible_video(self, sprite: SpriteDraft) -> None:
        """Refuse a Sprite that hides the video in most sampled frames, or throws at another render length, while the Executor can still fix it.

        Sprites are composited over the user's own video, so a full-frame opaque layer is never what a filter
        or an effect wants; the bus renders them at the final video's length, not the preview's. This is the browser mount the host needs anyway: the report is cached under the key
        the final host check reads, so completing the task does not mount the Sprite a second time.
        """
        from ..tool_validation import render_lengths

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
        )
        if report.passed:
            return
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
            preset = stored.model_dump(mode="json", exclude={"preset_id", "created_at"}, exclude_unset=True)
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
        an edit that keeps an older text effect's boxes, places its instances by their own layout.
        """
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
        Preset, and an optional keyword-role Preset for the keyword hit. The host owns the configuration parameters."""
        block = request.text
        if request.kind != "text":
            raise ToolFault("INVALID_ARGUMENT", "Only a text effect (kind text) has a text block.", field="/kind")
        if request.instances:
            raise ToolFault("INVALID_ARGUMENT", "A text effect is its text block alone: draw decorations inside the tier Presets and leave instances empty.", field="/instances")
        lines = block.text.split("\n")
        tiers = len(block.tiers)
        if len(lines) != tiers or not all(line.strip() for line in lines):
            raise ToolFault("INVALID_ARGUMENT", f"text has {len(lines)} lines for {tiers} tiers: a one-tier text is one non-empty line and a two-tier "
                            "text (双栏) is exactly \"<upper>\\n<lower>\", both non-empty.", field="/text/text")
        if "two_tier" in self.requested and tiers != 2:
            raise ToolFault("INVALID_ARGUMENT", "The user switched on 双栏 (original_request.two_tier): give the text block two tiers, upper and lower.", field="/text/tiers")
        if "keyword_effect" in self.requested and block.keyword is None:
            raise ToolFault("INVALID_ARGUMENT", "The user switched on the keyword effect (original_request.keyword_effect): give the text block a keyword-role Preset.", field="/text/keyword")
        if tiers == 2:
            for name, value in (("line_gap", block.line_gap), ("line_starts", block.line_starts)):
                if value is None:
                    raise ToolFault("INVALID_ARGUMENT", f"A two-tier text needs {name}.", field=f"/text/{name}")
            starts = block.line_starts
            if len(starts) != 2 or starts[0] != 0 or starts[1] < starts[0]:
                raise ToolFault("INVALID_ARGUMENT", "line_starts has two frames, the first 0 and the second not earlier (for example [0, 30]).", field="/text/line_starts")
            for index, tier in enumerate(block.tiers):
                if tier.size_ratio is None:
                    raise ToolFault("INVALID_ARGUMENT", "Each tier of a two-tier text needs size_ratio (0.4 to 2, a ratio of font_size).", field=f"/text/tiers/{index}/size_ratio")
        else:
            for name, value in (("line_gap", block.line_gap), ("line_starts", block.line_starts)):
                if value is not None:
                    raise ToolFault("INVALID_ARGUMENT", f"Only a two-tier text takes {name}.", field=f"/text/{name}")
            if block.tiers[0].size_ratio not in (None, 1):
                raise ToolFault("INVALID_ARGUMENT", "A one-tier text renders at font_size; leave size_ratio out.", field="/text/tiers/0/size_ratio")
        self._check_length(block.duration_frames, "/text/duration_frames")
        store = FontStore.active
        fonts = [font.family for font in store.listing()] if store is not None else sorted({tier.font_family for tier in block.tiers})
        layout = {**block.layout.model_dump(mode="json"), "z_index": 0}
        timing = {"start_frame": 0, "duration_frames": block.duration_frames}
        slots = [(tier, "text", f"/text/tiers/{index}") for index, tier in enumerate(block.tiers)]
        if block.keyword is not None:
            slots.append((block.keyword, "keyword", "/text/keyword"))
        seen: set[str] = set()
        resolved: list[dict] = []
        summaries: list[InstanceSummary] = []
        for slot, role, field in slots:
            if slot.instance_id in seen or slot.instance_id in {TEXT_BLOCK_ID, "__proto__", "prototype", "constructor"}:
                raise ToolFault("INVALID_ARGUMENT", f"instance_id must be unique, safe and not {TEXT_BLOCK_ID!r}", field=f"{field}/instance_id")
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
        code, schema, defaults, host = await asyncio.to_thread(
            compose_text_source, block.model_dump(mode="json", exclude_none=True), resolved[:tiers], resolved[tiers] if block.keyword else None, bindings, fonts,
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
                  **({"keyword": block.keyword.instance_id} if block.keyword else {})},
        ), summaries)

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
