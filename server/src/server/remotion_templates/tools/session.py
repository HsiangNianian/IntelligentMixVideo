"""Task-owned creation tools: immutable Presets, composed Sprites and host checks."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
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
from ..categories import agent_message, category_problems, parameter_problems
from .catalog_store import CatalogStore
from .registry import ToolFault
from .schema import merge_parameters, validate_component_contract, validate_parameters
from .compose import compose_source


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
        self.composition = spec.composition.model_dump() if spec else request.get("composition")
        self.kind = spec.sprite_kind if spec else request.get("sprite_kind")
        root = harness.settings.data_dir
        if not root.is_absolute():
            root = Path(__file__).parent.parent / root
        self.catalog_root = root / "catalog"
        self.catalog_root.mkdir(parents=True, exist_ok=True)
        self.catalog = CatalogStore(self.catalog_root)
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
            # Re-composing an edit must keep the category and the operator parameters the accepted base exposed.
            **({"kind": sprite.kind} if sprite.kind is not None else {}),
            "parameters": [item.model_dump(mode="json") for item in sprite.bindings],
            "instances": [
                {
                    "instance_id": item.instance_id,
                    "draft_id": f"base_{item.instance_id}",
                    "parameters": item.parameters,
                    "layout": item.layout.model_dump(mode="json"),
                    "timing": item.timing.model_dump(mode="json"),
                }
                for item in sprite.instances
            ],
        }
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

    async def validate_render(self, request: RenderValidationInput, *, transparency: bool = False) -> RenderValidationReport:
        """Run the isolated component behavior runner and cache the exact report.

        ``transparency`` adds the canvas-coverage measurement. The option is only forwarded when asked for,
        so a validator that predates it keeps working.
        """
        if self.validator is None:
            raise ToolFault("VALIDATION_UNAVAILABLE", "The isolated behavior validator is unavailable.")
        from ..tool_validation import ValidationUnavailable

        try:
            report = await self.validator.validate_render(request, **({"transparency": True} if transparency else {}))
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
        # Presets persist to MySQL with a local fallback; the record is immutable once saved.
        self.catalog_backend = await asyncio.to_thread(self.catalog.append_preset, record)
        return PresetCreateOutput(preset=record, validation=validation)

    def search_presets(self, request: PresetSearchInput) -> PresetSearchOutput:
        """List summaries, newest first, filtered by a case-insensitive description substring.

        With ``preset_id`` the matching full record (including code) is returned as well, so
        reading a Preset never requires a write-shaped tool.
        """
        full = self._find_preset(request.preset_id) if request.preset_id is not None else None
        needle = request.query.strip().lower()
        output = PresetSearchOutput(**({"preset": full} if full else {}), presets=[], has_more=False)

        def fits_reply():
            """计入 ToolResult 及保存 content 字符串时的二次转义，不截断完整源码。"""
            content = json.dumps({"ok": True, "data": output.model_dump(mode="json", exclude_unset=True)}, ensure_ascii=False)
            return len(json.dumps(content, ensure_ascii=False).encode("utf-8")) <= _SEARCH_BYTE_BUDGET

        if not fits_reply():
            raise ToolFault("RESOURCE_LIMIT_EXCEEDED", "The full Preset exceeds the tool reply size limit.")
        for item in self._recent_presets():
            if needle not in item.description.lower():
                continue
            if len(output.presets) >= request.limit:
                output.has_more = True
                break
            output.presets.append(PresetSummary(
                preset_id=item.preset_id,
                description=item.description,
                parameter_names=_parameter_names(item.parameter_schema),
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
            **({"source_preset_id": source} if source else {}),
        ))

    async def create_sprite(self, draft_id: str) -> SpriteCreateOutput:
        """Validate and store the composed Sprite a sprite.compose handle refers to.

        Only compose_sprite writes Sprite drafts, from resolved Presets, so their code, schema, defaults
        and timing agree by construction; the draft is not bundled a second time just to compare.
        """
        sprite = self.sprite_draft(draft_id)
        # Bundling erases TypeScript annotations; validate each distinct original module before its generated
        # adapter. Instances of one Preset share a module, so its check runs once.
        checked: set[str] = set()
        for instance in sprite.instances:
            component = ComponentDefinition(code=instance.preset.code, parameter_schema=instance.preset.parameter_schema, default_parameters=instance.preset.default_parameters)
            key = component.model_dump_json()
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
        problems = category_problems(sprite.kind, sprite.parameter_schema, {item.name for item in sprite.bindings}, sprite.default_parameters)
        if problems:
            raise ToolFault("CATEGORY_PARAMETERS_MISSING", agent_message(sprite.kind, problems), details={
                "missing": [{"name": item.name, "reason": item.reason, **({"schema": item.rule.example} if item.rule else {})} for item in problems],
            })
        await self._require_visible_video(sprite)
        record = SpriteRecord(
            sprite_id=uuid4().hex,
            created_at=datetime.now(UTC).isoformat(),
            **sprite.model_dump(mode="json", exclude_unset=True),
        )
        await asyncio.to_thread(self.catalog.append_sprite, record)
        self.saved_sprites[record.sprite_id] = record
        self.latest_sprite_id = record.sprite_id
        return SpriteCreateOutput(sprite_id=record.sprite_id, created_at=record.created_at, composition=record.composition, validation=validation)

    async def _require_visible_video(self, sprite: SpriteDraft) -> None:
        """Refuse a Sprite that hides the video in most sampled frames while the Executor can still fix it.

        Sprites are composited over the user's own video, so a full-frame opaque layer is never what a filter
        or an effect wants. This is the browser mount the host needs anyway: the report is cached under the key
        the final host check reads, so completing the task does not mount the Sprite a second time.
        """
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
        )
        if report.passed:
            return
        diagnostics = [
            CodeDiagnostic(source="contract", severity="error", message=f"{item.name}: {item.message}" if item.message else item.name)
            for item in report.checks
            if item.status != "passed"
        ] or [CodeDiagnostic(source="contract", severity="error", message="Sprite render validation failed.")]
        raise _validation_fault("Sprite render validation failed.", CodeValidationReport(passed=False, diagnostics=diagnostics))

    async def compose_sprite(self, request) -> SpriteComposeOutput:
        """Resolve immutable Presets, generate deterministic local-frame Sprite code and keep it as a draft."""
        seen: set[str] = set()
        resolved: list[dict] = []
        summaries: list[InstanceSummary] = []
        duration = 0
        for index, instance in enumerate(request.instances):
            if instance.instance_id in seen or instance.instance_id in {"__proto__", "prototype", "constructor"}:
                raise ToolFault("INVALID_ARGUMENT", "instance_id must be unique and safe", field=f"/instances/{index}/instance_id")
            seen.add(instance.instance_id)
            if instance.source.kind == "stored":
                source = await asyncio.to_thread(self._find_preset, instance.source.preset_id)
                preset = source.model_dump(mode="json", exclude={"preset_id", "created_at"}, exclude_unset=True)
                origin = {"preset_id": source.preset_id}
            else:
                draft = self._draft(self.preset_drafts, instance.source.draft_id, f"/instances/{index}/source/draft_id")
                preset = draft.model_dump(mode="json", exclude_unset=True)
                origin = {"draft_id": instance.source.draft_id}
            try:
                diagnostics = validate_component_contract(ComponentDefinition(
                    code=preset["code"], parameter_schema=preset["parameter_schema"], default_parameters=preset["default_parameters"]
                ))
                if diagnostics:
                    raise ValueError(diagnostics[0].message)
                parameters = merge_parameters(preset["default_parameters"], instance.parameters)
                validate_parameters(preset["parameter_schema"], parameters)
            except Exception as exc:
                raise ToolFault(
                    "INVALID_ARGUMENT", f"Invalid parameters for {instance.instance_id}: {str(exc)[:300]}",
                    field=f"/instances/{index}/parameters",
                ) from exc
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
        bindings = [item.model_dump(mode="json") for item in request.parameters]
        # 打包器是同步子进程；放到线程里执行，避免阻塞同进程的 SSE 与其它请求。
        code, schema, defaults = await asyncio.to_thread(compose_source, resolved, bindings)
        for index, binding in enumerate(request.parameters):
            reason = parameter_problems(schema["properties"][binding.name], binding.name)
            if reason:
                raise ToolFault("INVALID_ARGUMENT", f"Parameter {binding.name} {reason}: give the bound Preset prop a bounded schema", field=f"/parameters/{index}")
        sprite = SpriteDraft(
            description=request.description,
            code=code,
            parameter_schema=schema,
            default_parameters=defaults,
            composition={"width": 1080, "height": 1920, "fps": 30, "duration_frames": duration},
            instances=resolved,
            kind=request.kind,
            bindings=bindings,
        )
        draft_id = "sprite_draft_" + uuid4().hex[:12]
        self.sprite_drafts[draft_id] = sprite
        return SpriteComposeOutput(
            draft_id=draft_id,
            digest=_digest(sprite),
            description=sprite.description,
            composition=sprite.composition,
            kind=request.kind,
            instances=summaries,
            parameter_names=[item.name for item in request.parameters],
            default_parameters=defaults,
            code_bytes=len(code.encode()),
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
