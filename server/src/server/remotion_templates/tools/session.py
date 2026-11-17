"""Task-local effect workspace, isolated validation."""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import json

from .contracts import (
    CodeValidationReport, ComponentDefinition,
    RenderValidationInput, RenderValidationReport, SpriteDraft, SpriteRecord,
)
from ..fonts import DEFAULT_FAMILY, FontStore
from .registry import ToolFault
from .schema import merge_parameters, validate_component_contract, validate_parameters
from .effect_tools import PREVIEW_RENDERS, EffectTools
from .workspace import restore_version_files, starter_files


def _apply_edits(code: str, edits: list[dict]) -> str:
    """Apply workspace edits in order, each to the file as the earlier ones left it.

    Every old_str must occur exactly once, so a replacement never lands somewhere the model did not look. A miss
    names the edit, and where a trimmed first line of old_str does occur, so the next call can copy it exactly.
    """
    for index, edit in enumerate(edits):
        old, field = edit["old_str"], f"/edits/{index}/old_str"
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


class ToolSession(EffectTools):
    """Keep effect workspaces and validated candidates task-local; stored versions remain immutable."""

    def __init__(
        self, harness, spec, base, budget, directory, images, on_stage, intent, *, versions: list | None = None
    ):
        """Restore any accepted version into the same task-local effect workspace; history stays immutable."""
        self.harness, self.budget = harness, budget
        self.spec = spec
        self.directory, self.images = directory, images
        self.on_stage, self.intent = on_stage, intent or {}
        request = self.intent.get("original_request") or {}
        # A new work must last exactly as long as the user chose; an edit starts from an accepted version
        # and keeps deriving its length from the instances.
        self.target_frames = None if spec else (request.get("composition") or {}).get("duration_in_frames")
        # The new-chat switches the user turned on (双栏, keyword effect, 双语字幕) bind a new work; edits follow the accepted base.
        self.requested = set() if spec else {name for name in ("two_tier", "keyword_effect", "bilingual") if request.get(name) is True}
        self.kind = spec.sprite_kind if spec else request.get("sprite_kind")
        self.latest_sprite_id: str | None = None
        self.saved_sprites: dict[str, SpriteRecord] = {}
        self.validation_reports: dict[str, RenderValidationReport] = {}
        self.base = base
        self.feedback = []
        self.files = (restore_version_files(spec, base) if spec is not None and base is not None
                      else starter_files(self.kind, self.requested, (FontStore.active.families() if FontStore.active is not None else [DEFAULT_FAMILY])[0]))
        self.versions = {item.number: (item.spec, item.candidate) for item in versions or []}
        self.observations: list[tuple] = []
        # Whether the observations have yet to reach the Executor's window, and the accepted version's frames of an edit.
        self.observations_fresh = False
        self.base_frames: list[tuple] = []
        self.base_motion: list[str] = []
        self.pending_sheet = self.sheet_seen = self.saved_hash = self.checked_hash = None
        self.renders_left = PREVIEW_RENDERS
        self.validator = None
        try:
            from ..tool_validation import ToolValidator
            self.validator = ToolValidator(harness.renderer, self.directory / "tools")
        except ImportError:
            # The validation service is loaded lazily by deployments. Contract-only
            # diagnostics remain available for library inspection and workspace edits.
            self.validator = None

    def snapshot(self) -> dict:
        """Outer and Plan see the workspace's facts and current state."""
        return self._workspace_snapshot()

    def task_facts(self) -> dict:
        """The pinned handoff describes the accepted base; files are read through workspace.read."""
        return self._workspace_facts()

    def turn_state(self) -> dict:
        """Only changing workspace state follows the append-only Executor window."""
        return self._workspace_turn_state()

    def _workspace_snapshot(self) -> dict:
        """A free-form task's facts and its current state, for the layers that see the whole snapshot on each turn."""
        return {**self._workspace_turn_state(), **self._workspace_facts()}

    def _workspace_turn_state(self) -> dict:
        """What changes from turn to turn in a free-form task."""
        digest = self.workspace_hash()
        return {
            "workspace": {name: {"lines": len(content.splitlines())} for name, content in self.files.items()},
            "checked": digest == self.checked_hash,
            "latest_sprite_id": self.latest_sprite_id,
            "saved_unchanged": self.latest_sprite_id is not None and digest == self.saved_hash,
            "previews_left": self.renders_left,
            "feedback": list(self.feedback),
        }

    def _workspace_facts(self) -> dict:
        """What stays the same for the whole free-form task."""
        sprite = self.spec.sprite if self.spec is not None else None
        base = ({"description": sprite.description if sprite is not None else self.spec.description,
                 "kind": json.loads(self.files["effect.json"])["kind"], "duration_frames": self._duration(),
                 **({"motion": self.base_motion} if self.base_motion else {})} if self.base is not None else None)
        return {
            # The newest versions an edit can go back to with workspace_read {version}.
            **({"versions": [{"number": number, "description": (self.versions[number][0].sprite.description if self.versions[number][0].sprite else "Earlier effect")[:200]} for number in sorted(self.versions)[-10:]]} if self.versions else {}),
            **({"target_duration_frames": self.target_frames} if self.target_frames else {}),
            **({"base": base} if base is not None else {}),
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
        capture=None, text_fit_parameters: dict[str, bool] | None = None, effect_contract: dict | None = None,
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
                **({"text_fit_parameters": text_fit_parameters} if text_fit and text_fit_parameters is not None else {}),
                **({"effect_contract": effect_contract} if effect_contract is not None else {}),
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


    async def execute(self, name, args, catalog):
        """Dispatch one exact registered PR76 handler; old find/search/combine aliases do not exist."""
        await asyncio.sleep(0)
        from .registry import get_tool

        result = await get_tool(name, catalog).invoke(self, args)
        if result["ok"]:
            return result["data"]
        error = result["error"]
        raise ToolFault(error["code"], error["message"], field=error.get("field"), details=error.get("details"))
