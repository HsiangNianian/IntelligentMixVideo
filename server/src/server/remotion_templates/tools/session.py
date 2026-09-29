"""Task-owned PR76 tools: deterministic image, Preset, Sprite and validation operations."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from .contracts import (
    CodeValidationReport,
    ComponentDefinition,
    PresetCreateInput,
    PresetCreateOutput,
    PresetDraft,
    PresetModifyInput,
    PresetModifyOutput,
    PresetRecord,
    PresetSearchInput,
    PresetSearchOutput,
    PresetSearchMatch,
    RenderValidationInput,
    RenderValidationReport,
    SpriteDraft,
    SpriteRecord,
    SpriteCreateOutput,
)
from .registry import ToolFault
from .schema import merge_parameters, validate_component_contract, validate_parameters
from .semantic import PresetSemanticIndex, SemanticIndexError
from .compose import compose_source


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
        self.pr76_root = root / "pr76_catalog"
        self.pr76_root.mkdir(parents=True, exist_ok=True)
        self.latest_sprite_id: str | None = None
        self.saved_sprites: dict[str, SpriteRecord] = {}
        self.validation_reports: dict[str, RenderValidationReport] = {}
        self._last_component_sprite: SpriteDraft | None = None
        self.feedback = []
        self.operation = ""
        self.validator = None
        try:
            from ..tool_validation import ToolValidator
            self.validator = ToolValidator(harness.renderer, self.directory / "tools")
        except ImportError:
            # The validation service is loaded lazily by deployments. Contract-only
            # diagnostics remain available for catalog inspection and compose.
            self.validator = None
        self.semantic_index = PresetSemanticIndex(self.pr76_root)

    def snapshot(self) -> dict:
        """Expose task-local PR76 observations without leaking mutable catalog state."""
        latest = self.saved_sprites.get(self.latest_sprite_id or "")
        return {
            "latest_sprite_id": self.latest_sprite_id,
            "saved_sprites": [item.model_dump(mode="json", exclude_unset=True) for item in self.saved_sprites.values()],
            "latest_sprite": latest.model_dump(mode="json", exclude_unset=True) if latest else None,
            "last_validation": {
                key: report.model_dump(mode="json") for key, report in self.validation_reports.items()
            },
            "feedback": list(self.feedback),
        }

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

    def _json_path(self, category: str) -> Path:
        """Return the private PR76 catalog file for one immutable record category."""
        return self.pr76_root / f"{category}.json"

    def _read_records(self, category: str, model):
        """Read and validate the private catalog; malformed records stop the operation."""
        path = self._json_path(category)
        if not path.exists():
            return []
        values = json.loads(path.read_text(encoding="utf-8"))
        return [model.model_validate(item) for item in values]

    def _write_records(self, category: str, records) -> None:
        """Atomically persist PR76 records without exposing a partial catalog."""
        path = self._json_path(category)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps([item.model_dump(mode="json", exclude_unset=True) for item in records], ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    def _find_preset(self, preset_id: str) -> PresetDraft:
        """Resolve a Preset ID only from the immutable PR76 catalog."""
        for record in self._read_records("presets", PresetRecord):
            if record.preset_id == preset_id:
                return record
        raise ToolFault("PRESET_NOT_FOUND", f"Unknown preset: {preset_id}")

    async def search_pr76(self, request: PresetSearchInput) -> PresetSearchOutput:
        """Query ChromaDB by natural-language meaning and return complete records."""
        records = {record.preset_id: record for record in self._read_records("presets", PresetRecord)}
        try:
            identifiers = await asyncio.wait_for(
                asyncio.to_thread(self.semantic_index.query, request.query, request.limit), timeout=30
            )
        except SemanticIndexError as exc:
            raise ToolFault("SEARCH_UNAVAILABLE", str(exc)) from exc
        matches = [
            PresetSearchMatch(rank=index, preset=records[preset_id])
            for index, preset_id in enumerate(identifiers, 1)
            if preset_id in records
        ]
        return PresetSearchOutput(matches=matches)

    async def validate_pr76_code(self, component: ComponentDefinition) -> CodeValidationReport:
        """Run the isolated PR76 code validator and preserve real diagnostics."""
        diagnostics = validate_component_contract(component)
        if diagnostics:
            return CodeValidationReport(passed=False, diagnostics=diagnostics)
        if self.validator is None:
            raise ToolFault("VALIDATION_UNAVAILABLE", "The isolated TypeScript validator is unavailable.")
        return await self.validator.validate_code(component)

    async def validate_pr76_render(self, request: RenderValidationInput) -> RenderValidationReport:
        """Run the isolated component behavior runner and cache the exact report."""
        if self.validator is None:
            raise ToolFault("VALIDATION_UNAVAILABLE", "The isolated behavior validator is unavailable.")
        report = await self.validator.validate_render(request)
        parameters = merge_parameters(request.component.default_parameters, request.parameters)
        try:
            validate_parameters(request.component.parameter_schema, parameters)
        except Exception:
            # Validator reports this as a failed parameters check; preserve its report.
            pass
        self.validation_reports[self._component_validation_key(request.component, request.duration_frames, parameters)] = report
        return report

    async def create_pr76_preset(self, request: PresetCreateInput) -> PresetCreateOutput:
        """Validate and store a new immutable PR76 Preset record."""
        if request.source_preset_id is not None:
            self._find_preset(request.source_preset_id)
        validation = await self.validate_pr76_code(request)
        if not validation.passed:
            raise ToolFault("CODE_VALIDATION_FAILED", "Preset code validation failed.", details={"validation": validation.model_dump(mode="json")})
        record = PresetRecord(
            preset_id=uuid4().hex,
            created_at=datetime.now(UTC).isoformat(),
            **request.model_dump(mode="json", exclude_unset=True),
        )
        records = self._read_records("presets", PresetRecord)
        records.append(record)
        self._write_records("presets", records)
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self.semantic_index.upsert, record.preset_id, record.description), timeout=30
            )
        except SemanticIndexError as exc:
            self._write_records("presets", [item for item in records if item.preset_id != record.preset_id])
            raise ToolFault("SEARCH_UNAVAILABLE", str(exc)) from exc
        except Exception as exc:
            self._write_records("presets", [item for item in records if item.preset_id != record.preset_id])
            try:
                await asyncio.wait_for(asyncio.to_thread(self.semantic_index.delete, record.preset_id), timeout=10)
            except Exception:
                pass
            raise ToolFault("SEARCH_UNAVAILABLE", f"Preset index write failed: {exc}") from exc
        return PresetCreateOutput(preset=record, validation=validation)

    async def modify_pr76_preset(self, request: PresetModifyInput) -> PresetModifyOutput:
        """Create a full immutable draft from one exact Preset and explicit field replacements."""
        previous = self._find_preset(request.preset_id)
        changes = request.changes.model_dump(mode="json", exclude_unset=True)
        if not changes:
            raise ToolFault("INVALID_ARGUMENT", "changes must contain at least one field")
        changes.pop("source_preset_id", None)
        values = previous.model_dump(mode="json", exclude_unset=True)
        values.pop("preset_id", None)
        values.pop("created_at", None)
        values.update(changes)
        values["source_preset_id"] = request.preset_id
        draft = PresetDraft(**values)
        diagnostics = validate_component_contract(ComponentDefinition(
            code=draft.code,
            parameter_schema=draft.parameter_schema,
            default_parameters=draft.default_parameters,
        ))
        if diagnostics:
            raise ToolFault("INVALID_ARGUMENT", diagnostics[0].message, details={"validation": {"passed": False, "diagnostics": [item.model_dump(mode="json") for item in diagnostics]}})
        return PresetModifyOutput(preset=draft)

    async def create_pr76_sprite(self, sprite: SpriteDraft) -> SpriteCreateOutput:
        """Validate source consistency and store one immutable composed Sprite."""
        if sprite.composition.width != 1080 or sprite.composition.height != 1920 or sprite.composition.fps != 30:
            raise ToolFault("INVALID_ARGUMENT", "Sprite composition must be 1080x1920 at 30 FPS")
        # Rebuild source, schema and defaults from the immutable instance snapshot;
        # arbitrary code/schema edits must never be accepted as a compose result.
        resolved = [item.model_dump(mode="json", exclude_unset=True) for item in sprite.instances]
        expected_duration = max(
            item["timing"]["start_frame"] + item["timing"]["duration_frames"] for item in resolved
        )
        if expected_duration != sprite.composition.duration_frames:
            raise ToolFault("COMPOSITION_FAILED", "Sprite duration does not match instance timings.")
        try:
            expected_code, expected_schema, expected_defaults = compose_source(resolved)
        except ToolFault:
            raise
        expected_payload = {
            "code": expected_code,
            "parameter_schema": expected_schema,
            "default_parameters": expected_defaults,
            "composition": sprite.composition.model_dump(mode="json"),
            "instances": resolved,
        }
        actual_payload = {
            "code": sprite.code,
            "parameter_schema": sprite.parameter_schema,
            "default_parameters": sprite.default_parameters,
            "composition": sprite.composition.model_dump(mode="json"),
            "instances": resolved,
        }
        if expected_payload != actual_payload:
            raise ToolFault("COMPOSITION_FAILED", "Sprite code, schema or defaults do not match its instance definition.")
        # Bundling erases TypeScript annotations; validate each original module before its generated adapter.
        for instance in sprite.instances:
            original = await self.validate_pr76_code(ComponentDefinition(code=instance.preset.code, parameter_schema=instance.preset.parameter_schema, default_parameters=instance.preset.default_parameters))
            if not original.passed:
                raise ToolFault("CODE_VALIDATION_FAILED", "A source Preset does not typecheck", details={"validation": original.model_dump(mode="json", exclude_unset=True)})
        validation = await self.validate_pr76_code(
            ComponentDefinition(
                code=sprite.code,
                parameter_schema=sprite.parameter_schema,
                default_parameters=sprite.default_parameters,
            )
        )
        if not validation.passed:
            raise ToolFault("CODE_VALIDATION_FAILED", "Sprite code validation failed.", details={"validation": validation.model_dump(mode="json")})
        record = SpriteRecord(
            sprite_id=uuid4().hex,
            created_at=datetime.now(UTC).isoformat(),
            **sprite.model_dump(mode="json", exclude_unset=True),
        )
        records = self._read_records("sprites", SpriteRecord)
        records.append(record)
        self._write_records("sprites", records)
        self.saved_sprites[record.sprite_id] = record
        self.latest_sprite_id = record.sprite_id
        self._last_component_sprite = record
        return SpriteCreateOutput(sprite=record, validation=validation)

    def compose_pr76(self, request):
        """Resolve immutable Presets and generate deterministic local-frame Sprite code."""
        seen: set[str] = set()
        resolved: list[dict] = []
        duration = 0
        for instance in request.instances:
            if instance.instance_id in seen or instance.instance_id in {"__proto__", "prototype", "constructor"}:
                raise ToolFault("INVALID_ARGUMENT", "instance_id must be unique and safe")
            seen.add(instance.instance_id)
            if instance.source.kind == "stored":
                source = self._find_preset(instance.source.preset_id)
                preset = source.model_dump(mode="json", exclude={"preset_id", "created_at"}, exclude_unset=True)
            else:
                preset = instance.source.preset.model_dump(mode="json", exclude_unset=True)
            try:
                diagnostics = validate_component_contract(ComponentDefinition(
                    code=preset["code"], parameter_schema=preset["parameter_schema"], default_parameters=preset["default_parameters"]
                ))
                if diagnostics:
                    raise ValueError(diagnostics[0].message)
                parameters = merge_parameters(preset["default_parameters"], instance.parameters)
                validate_parameters(preset["parameter_schema"], parameters)
            except Exception as exc:
                raise ToolFault("INVALID_ARGUMENT", f"Invalid parameters for {instance.instance_id}") from exc
            end = instance.timing.start_frame + instance.timing.duration_frames
            duration = max(duration, end)
            resolved.append({
                "instance_id": instance.instance_id,
                "preset": preset,
                "parameters": parameters,
                "layout": instance.layout.model_dump(mode="json"),
                "timing": instance.timing.model_dump(mode="json"),
            })
        code, schema, defaults = compose_source(resolved)
        sprite = SpriteDraft(
            description=request.description,
            code=code,
            parameter_schema=schema,
            default_parameters=defaults,
            composition={"width": 1080, "height": 1920, "fps": 30, "duration_frames": duration},
            instances=resolved,
        )
        return {"sprite": sprite.model_dump(mode="json", exclude_unset=True)}

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
