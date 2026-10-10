"""Task-owned creation tools: immutable Presets, composed Sprites and host checks."""

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
    PresetSearchInput,
    PresetSearchOutput,
    PresetSummary,
    PresetRecord,
    RenderValidationInput,
    RenderValidationReport,
    SpriteDraft,
    SpriteRecord,
    SpriteCreateOutput,
)
from .catalog_store import CatalogStore
from .registry import ToolFault
from .schema import merge_parameters, validate_component_contract, validate_parameters
from .compose import compose_source


# 快照预算：provider 的请求体上限是 512000 字节，会话窗口自身上限 240000 字节，
# 余量留给工具描述。快照本身没有别的边界，只能在这里按需收缩。
_SNAPSHOT_BYTE_BUDGET = 200_000
# 四个搜索回执需共享 240 KB 会话交换，留出调用参数与消息封装的空间。
_SEARCH_BYTE_BUDGET = 40_000


def _snapshot_bytes(data: dict) -> int:
    """测量模型实际收到的快照体积（UTF-8 字节）。"""
    return len(json.dumps(data, ensure_ascii=False, default=str).encode())


def _bounded_snapshot(data: dict) -> dict:
    """超过预算时按顺序丢弃可由记录重建的源码，保留 ID、Schema、默认值与校验结论。

    合成后的 Sprite 源码已经包含各实例的预设源码，所以先丢实例源码；仍超限才丢
    Sprite 自身源码。正常任务体积远低于预算，模型可见信息不因此改变。
    """
    if _snapshot_bytes(data) <= _SNAPSHOT_BYTE_BUDGET:
        return data
    for record in data.get("saved_sprites", []):
        for instance in record.get("instances", []):
            preset = instance.get("preset")
            if isinstance(preset, dict):
                preset.pop("code", None)
    if _snapshot_bytes(data) <= _SNAPSHOT_BYTE_BUDGET:
        return data
    for record in [*data.get("saved_sprites", []), data.get("latest_sprite")]:
        if isinstance(record, dict):
            record.pop("code", None)
    return data


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
        self._last_component_sprite: SpriteDraft | None = None
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

    def snapshot(self) -> dict:
        """Expose task-local PR76 observations without leaking mutable catalog state."""
        latest = self.saved_sprites.get(self.latest_sprite_id or "")
        return _bounded_snapshot({
            "latest_sprite_id": self.latest_sprite_id,
            "saved_sprites": [item.model_dump(mode="json", exclude_unset=True) for item in self.saved_sprites.values()],
            "latest_sprite": latest.model_dump(mode="json", exclude_unset=True) if latest else None,
            "last_validation": {
                key: report.model_dump(mode="json") for key, report in self.validation_reports.items()
            },
            "preset_backend": self.catalog_backend,
            "feedback": list(self.feedback),
        })

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
        if record is None:
            known = [item.preset_id for item in self._recent_presets()[:10]]
            raise ToolFault("PRESET_NOT_FOUND", f"Unknown preset: {preset_id}. Use an ID returned by a tool; recent preset_ids: {known}")
        return record

    async def validate_code(self, component: ComponentDefinition) -> CodeValidationReport:
        """Run the isolated PR76 code validator and preserve real diagnostics."""
        diagnostics = validate_component_contract(component)
        if diagnostics:
            return CodeValidationReport(passed=False, diagnostics=diagnostics)
        if self.validator is None:
            raise ToolFault("VALIDATION_UNAVAILABLE", "The isolated TypeScript validator is unavailable.")
        return await self.validator.validate_code(component)

    async def validate_render(self, request: RenderValidationInput) -> RenderValidationReport:
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

    async def create_preset(self, request: PresetCreateInput) -> PresetCreateOutput:
        """Validate and store a new immutable PR76 Preset record."""
        if request.source_preset_id is not None:
            await asyncio.to_thread(self._find_preset, request.source_preset_id)
        validation = await self.validate_code(request)
        if not validation.passed:
            raise ToolFault("CODE_VALIDATION_FAILED", "Preset code validation failed.", details={"validation": validation.model_dump(mode="json")})
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
                parameter_names=list(item.parameter_schema.get("properties", {})),
            ))
            if not fits_reply():
                output.presets.pop()
                if not output.presets and full is None:
                    raise ToolFault("RESOURCE_LIMIT_EXCEEDED", "A Preset summary exceeds the tool reply size limit.")
                output.has_more = True
                break
        return output

    def modify_preset(self, request: PresetModifyInput) -> PresetModifyOutput:
        """Copy a stored Preset with whole-field replacements; unknown IDs list what exists."""
        changes = request.changes.model_dump(mode="json", exclude_unset=True)
        if not changes:
            raise ToolFault("INVALID_ARGUMENT", "changes must replace at least one field", field="/changes")
        record = self._find_preset(request.preset_id)
        base = record.model_dump(mode="json", exclude={"preset_id", "created_at"}, exclude_unset=True)
        draft = PresetDraft(**{**base, **changes, "source_preset_id": record.preset_id})
        diagnostics = validate_component_contract(draft)
        if diagnostics:
            raise ToolFault("INVALID_ARGUMENT", "The edited Preset violates the component contract.",
                            field="/changes", details={"diagnostics": [item.model_dump(mode="json", exclude_unset=True) for item in diagnostics]})
        return PresetModifyOutput(preset=draft)

    async def create_sprite(self, sprite: SpriteDraft) -> SpriteCreateOutput:
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
            # 打包器是同步子进程；放到线程里执行，避免阻塞同进程的 SSE 与其它请求。
            expected_code, expected_schema, expected_defaults = await asyncio.to_thread(compose_source, resolved)
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
            original = await self.validate_code(ComponentDefinition(code=instance.preset.code, parameter_schema=instance.preset.parameter_schema, default_parameters=instance.preset.default_parameters))
            if not original.passed:
                raise ToolFault("CODE_VALIDATION_FAILED", "A source Preset does not typecheck", details={"validation": original.model_dump(mode="json", exclude_unset=True)})
        validation = await self.validate_code(
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
        await asyncio.to_thread(self.catalog.append_sprite, record)
        self.saved_sprites[record.sprite_id] = record
        self.latest_sprite_id = record.sprite_id
        self._last_component_sprite = record
        return SpriteCreateOutput(sprite=record, validation=validation)

    async def compose_sprite(self, request):
        """Resolve immutable Presets and generate deterministic local-frame Sprite code."""
        seen: set[str] = set()
        resolved: list[dict] = []
        duration = 0
        for instance in request.instances:
            if instance.instance_id in seen or instance.instance_id in {"__proto__", "prototype", "constructor"}:
                raise ToolFault("INVALID_ARGUMENT", "instance_id must be unique and safe")
            seen.add(instance.instance_id)
            if instance.source.kind == "stored":
                source = await asyncio.to_thread(self._find_preset, instance.source.preset_id)
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
        # 打包器是同步子进程；放到线程里执行，避免阻塞同进程的 SSE 与其它请求。
        code, schema, defaults = await asyncio.to_thread(compose_source, resolved)
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
