"""`ToolSession` 的自由组件工具：workspace.read/write/edit、preview.render、validate.check、effect.save 与 image.inspect。

数据流：工作区只在任务会话里（`files`：`Effect.tsx` 与 `effect.json`），检查、预览与保存都从当前文件重新 `assemble`。检查
与保存走同一套门禁：组件的 TypeScript 与源码策略、分类必备参数，再在隔离浏览器里检查透明度、不同时长、排版与契约探针；
保存通过后记下候选 Sprite（宿主最终发布最新的一个）与它的验收联系表。预览、保存与看图得到的图片是「观察图」，附在之后的
Executor 轮次里，有新的观察图时替换旧的；`agent.py` 据此确认汇报前看过最新候选的联系表。预览直接调用校验器，不把它的
报告缓存成发布用的验收结果。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from PIL import Image

from ..categories import FONT_PARAMETERS, category_problems
from ..fonts import DEFAULT_FAMILY, FontStore, open_font_schema
from .contracts import (
    ComponentDefinition, EffectCheckInput, EffectCheckOutput, EffectProblem, EffectSaveInput, EffectSaveOutput, ImageInspectInput,
    ImageInspectOutput, PreviewRenderInput, PreviewRenderOutput, RenderValidationInput, ReviewFrame, SpriteDraft, SpriteRecord,
    SpriteReview, WorkspaceEditInput, WorkspaceFile, WorkspaceReadInput, WorkspaceWriteInput, WorkspaceWriteOutput,
)
from .registry import ToolFault
from .workspace import (
    CODE, MANIFEST, assemble, contract_probes, line_count, parse_manifest, physical_lines, preview_keywords, keyframes, motion_facts, restore_files, review_box, text_fit_parameters, trial_frames,
)

# 内容运行时 "imv" 的源码：workspace.read 只读地给出它，Agent 照着类型与实现写 Effect.tsx。
RUNTIME_FILE = Path(__file__).parents[2] / "remotion" / "imv-runtime.tsx"
# 每个任务可做的预览次数：随时能看画面，但不让模型用渲染代替思考。
PREVIEW_RENDERS = 12
# 观察图的长边像素上限：够看清字形与描边，又不让图片撑满模型上下文。
OBSERVATION_SIDE = 720
# 预览变体的挂载长度：short 是字效最短的播放长度（1.5 秒），long_duration 是总线最长的长度（120 秒）。
VARIANT_FRAMES = {"short": 45, "long_duration": 3600}
# long 变体填满的字数，与字效文字参数的 maxLength 一致。
LONG_TEXT = 100


def _diagnostic_problem(item) -> EffectProblem:
    """A code diagnostic as a problem; LSP lines refer to a formatted copy, so the checked line's text comes with it."""
    where = f"{item.file} line {item.range.start.line + 1}: " if item.file and item.range else ""
    line = item.range.start.line + 1 if item.source == "contract" and item.range else None
    return EffectProblem(check="code", message=f"{where}{item.message}"[:3000], **({"line": line} if line else {}),
                         **({"excerpt": item.excerpt} if item.excerpt else {}))


def _long_text(values: dict) -> dict:
    """The default lines repeated until the text is as long as templates may make it, in the effect's own shape."""
    lines = physical_lines(values)
    share = max(1, LONG_TEXT // max(1, len(lines)))
    filled = [(line * (share // max(1, len(line)) + 1))[:share] if line else line for line in lines]
    if "textEn" not in values:
        return {"text": "\n".join(filled)}
    if len(filled) == 4:
        return {"text": f"{filled[0]}\n{filled[2]}", "textEn": f"{filled[1]}\n{filled[3]}"}
    return {"text": filled[0], "textEn": filled[1]}


class EffectTools:
    """Workspace, preview, check, save and image tools of a free-form effect; mixed into `ToolSession`.

    The session supplies editable ``files`` for new and historical works, ``images``, ``directory``,
    ``requested``, ``validator`` and the Sprite bookkeeping (`saved_sprites`, `latest_sprite_id`, `validate_code`,
    `validate_render`).
    """

    files: dict[str, str]
    # This work's versions by number, as (spec, candidate), for restoring any earlier version on demand.
    versions: dict[int, tuple]
    # Images the latest image-producing tool returned, attached to the next Executor turns as (path, label).
    observations: list[tuple[Path, str]]
    # Whether they have yet to reach the Executor's window, and the accepted version's frames, also shown to the Plan.
    observations_fresh: bool
    base_frames: list[tuple[Path, str]]
    # What the accepted version does, measured from its running code, for an edit.
    base_motion: list[str]
    # The saved candidate whose acceptance sheet is attached and not yet shown, and the one the model has seen.
    pending_sheet: str | None
    sheet_seen: str | None
    # Workspace digests at the latest save and the latest passing check.
    saved_hash: str | None
    checked_hash: str | None
    renders_left: int

    def _workspace(self) -> dict[str, str]:
        """The same two editable files for new effects and all accepted historical versions."""
        return self.files

    def workspace_hash(self) -> str | None:
        """A digest of both files, telling whether they changed since a check or save."""
        return hashlib.sha256("\0".join(self.files[name] for name in (CODE, MANIFEST)).encode()).hexdigest()

    def read_file(self, request: WorkspaceReadInput) -> WorkspaceFile:
        """Read a bounded page of current, historical or runtime source; offsets count Unicode characters, not bytes."""
        if request.version is not None:
            if request.version not in self.versions or request.path == RUNTIME_FILE.name:
                raise ToolFault("INVALID_ARGUMENT", f"Earlier versions hold {CODE} and {MANIFEST}; the versions of this work are {sorted(self.versions)}.", field="/version")
            from .workspace import restore_version_files
            content = restore_version_files(*self.versions[request.version])[request.path]
        elif request.path == RUNTIME_FILE.name:
            content = RUNTIME_FILE.read_text(encoding="utf-8")
        else:
            content = self._workspace()[request.path]
        if request.offset > len(content):
            raise ToolFault("INVALID_ARGUMENT", f"offset exceeds the file's {len(content)} characters.", field="/offset")
        page = content[request.offset:request.offset + 16_000]
        # Bound encoded text, including escapes, so even several read replies fit one atomic tool exchange.
        while len(json.dumps(page, ensure_ascii=False).encode()) > 16_000:
            page = page[:len(page) // 2]
        end = request.offset + len(page)
        return WorkspaceFile(path=request.path, content=page, lines=line_count(content), offset=request.offset,
                             **({"next_offset": end} if end < len(content) else {}))

    def _store(self, path: str, content: str) -> WorkspaceWriteOutput:
        """Replace a file after checking that a manifest still parses; report the lines removed and added."""
        files = self._workspace()
        if path == MANIFEST:
            parse_manifest(content)
        from .session import _line_changes

        removed, added = _line_changes(files[path], content)
        files[path] = content
        return WorkspaceWriteOutput(path=path, lines=line_count(content), lines_removed=removed, lines_added=added)

    def write_file(self, request: WorkspaceWriteInput) -> WorkspaceWriteOutput:
        """Write a whole file."""
        return self._store(request.path, request.content)

    def edit_file(self, request: WorkspaceEditInput) -> WorkspaceWriteOutput:
        """Apply exact replacements to a file, in order; nothing changes when one of them does not match exactly once."""
        from .session import _apply_edits

        content = _apply_edits(self._workspace()[request.path], [edit.model_dump() for edit in request.edits])
        return self._store(request.path, content)

    def _fonts(self) -> list[str]:
        """The font library's family names, the enum text effects choose fonts from."""
        store = FontStore.active
        return store.families() if store is not None else [DEFAULT_FAMILY]

    def _duration(self) -> int:
        """The preview length: the user's choice for a new work, the accepted base's for an edit."""
        if self.target_frames:
            return self.target_frames
        if self.spec is None:
            return 150
        return self.spec.sprite.composition.duration_frames if self.spec.sprite is not None else self.spec.composition.duration_in_frames

    async def _assemble(self, duration: int | None = None) -> tuple[SpriteDraft, list[str]]:
        """Build the draft the workspace describes (the bundler is a subprocess, so it runs off the event loop)."""
        files = dict(self._workspace())
        manifest = parse_manifest(files[MANIFEST])
        if manifest.contract is not None:
            # Only an accepted version may supply cue-off measurement flags. Reading an earlier version also
            # permits restoring its original check, but editable workspace data cannot invent bypass switches.
            accepted = [self.spec, *(spec for spec, _candidate in self.versions.values())]
            trusted = [None, *(text_fit_parameters(spec.sprite) for spec in accepted if spec is not None)]
            if manifest.contract.text_fit_parameters not in trusted:
                raise ToolFault("INVALID_ARGUMENT", "contract.text_fit_parameters is host-owned: preserve the accepted version's layout check.", field="/contract/text_fit_parameters")
        return await asyncio.to_thread(assemble, files, fonts=self._fonts(), requested=set(self.requested), duration=duration or self._duration())

    async def _gate(self, draft: SpriteDraft, capture=None) -> tuple[list[EffectProblem], SpriteDraft | None]:
        """Run every check a saved effect must pass; returns the problems and, when there are none, the Sprite as stored."""
        from ..tool_validation import render_lengths

        component = draft.instances[0].preset
        code = await self.validate_code(ComponentDefinition(code=component.code, parameter_schema=component.parameter_schema,
                                                            default_parameters=component.default_parameters))
        if not code.passed:
            problems = [_diagnostic_problem(item) for item in code.diagnostics if item.severity == "error"]
            return list({item.message: item for item in problems}.values())[:8] or [EffectProblem(check="code", message="Effect.tsx does not compile.")], None
        schema, fonts = draft.parameter_schema, self._fonts()
        if (draft.effect or {}).get("contract"):
            # Stored font controls are open strings so later uploads work. Check their defaults against today's
            # library without writing a frozen enum back into the accepted schema or skipping other category rules.
            properties = schema.get("properties", {})
            schema = {**schema, "properties": {**properties, **{
                name: {**definition, "enum": fonts} for name in FONT_PARAMETERS
                if isinstance(definition := properties.get(name), dict) and definition.get("type") == "string" and "enum" not in definition
            }}}
        missing = category_problems(draft.kind, schema, {item.name for item in draft.bindings}, draft.default_parameters,
                                    fonts=set(fonts), requested=set(self.requested), allow_disabled_keyword_effect=True)
        if missing:
            hint = ("Edit contract.parameter_schema and contract.default_parameters in effect.json, preserving the existing controls."
                    if (draft.effect or {}).get("contract") else
                    'Declare style parameters in effect.json params; text, fonts, sizes, gaps and keywords come from its "text" section.')
            return [EffectProblem(check="parameters", message=(
                f"{item.name} {item.reason}{f'; schema {item.rule.example}' if item.rule else ''}. {hint}")) for item in missing], None
        sprite = draft.model_copy(update={"parameter_schema": open_font_schema(draft.parameter_schema)})
        text = sprite.kind == "text"
        report = await self.validate_render(
            RenderValidationInput(component=ComponentDefinition(code=sprite.code, parameter_schema=sprite.parameter_schema, default_parameters=sprite.default_parameters),
                                  duration_frames=sprite.composition.duration_frames),
            transparency=True, lengths=render_lengths(sprite.composition.fps, text=text), text_fit=text,
            # Retain historical cue/lockstep checks; new effects allow independently timed lines.
            text_fit_parameters=text_fit_parameters(sprite) if text else None, effect_contract=contract_probes(sprite), capture=capture,
        )
        if report.passed:
            return [], sprite
        problems = [_diagnostic_problem(item) for item in report.code_validation.diagnostics if item.severity == "error"]
        problems += [EffectProblem(check=item.name, message=item.message or f"{item.name} failed") for item in report.checks if item.status != "passed" and item.name != "code_contract"]
        return problems or [EffectProblem(check="render", message="The effect did not pass the render checks.")], None

    async def check_effect(self, request: EffectCheckInput) -> EffectCheckOutput:
        """Run the save gate on the workspace as it stands, without saving; a manifest problem is a problem, not a tool error."""
        try:
            draft, notes = await self._assemble()
        except ToolFault as exc:
            # A manifest the host cannot read, or code the bundler cannot parse, is what this check reports.
            if exc.error.code not in {"INVALID_ARGUMENT", "COMPOSITION_FAILED"}:
                raise
            check = "manifest" if exc.error.code == "INVALID_ARGUMENT" else "code"
            return EffectCheckOutput(passed=False, problems=[EffectProblem(check=check, message=f"{exc.error.message}{f' (at {exc.error.field})' if exc.error.field else ''}"[:3000])])
        problems, _sprite = await self._gate(draft)
        if not problems:
            self.checked_hash = self.workspace_hash()
        return EffectCheckOutput(passed=not problems, problems=problems, notes=notes)

    def _frames_out(self, shots: list[tuple[Path, int, str]], prefix: str) -> list[ReviewFrame]:
        """Make captured frames the current observation images and number them within that message.

        They replace whatever was attached before, so a saved candidate's sheet that has not been shown yet no longer counts
        as pending (`save_effect` marks its own sheet pending afterwards).
        """
        self.observations = [(path, f"{prefix} frame {frame} ({label})") for path, frame, label in shots]
        self.observations_fresh = True
        self.pending_sheet = None
        return [ReviewFrame(image=index, frame=frame, label=label) for index, (_path, frame, label) in enumerate(shots, start=1)]

    def _keyframes(self, capture, sprite: SpriteDraft) -> list[tuple[Path, int, str]]:
        """The keyframes among a capture's trial shots, as (path, frame, label)."""
        images = {frame: capture.directory / f"frame-{frame}.png" for frame in capture.frames if (capture.directory / f"frame-{frame}.png").is_file()}
        return [(images[frame], frame, label) for frame, label in keyframes(images, sprite)]

    def _middle_shot(self, capture, sprite: SpriteDraft) -> list[tuple[Path, int, str]]:
        """The bus hold frame (duration // 2), for what cannot be measured: colours, fills and ghosted text."""
        frame = sprite.composition.duration_frames // 2
        path = capture.directory / f"frame-{frame}.png"
        return [(path, frame, "middle, where the bus freezes short subtitles")] if path.is_file() else []

    def _motion(self, capture, sprite: SpriteDraft) -> list[str]:
        """The motion facts of a capture's measurements, or none when the worker wrote none."""
        path = capture.directory / "motion.json"
        if not path.is_file():
            return []
        return motion_facts(json.loads(path.read_text(encoding="utf-8")), sprite.composition.fps, sprite.composition.duration_frames // 2)

    def _capture(self, sprite: SpriteDraft, frames: list[int], keywords: list[str], extra: dict | None = None, *, trace: list[int] | None = None):
        """A frame capture of ``frames`` over the effect's region, scaled for the model, with sample keywords mounted; ``trace`` frames are measured."""
        from ..tool_validation import FrameCapture

        box = review_box(sprite)
        parameters = {**({"keywords": keywords} if keywords else {}), **(extra or {})}
        return FrameCapture(frames, box, min(1, OBSERVATION_SIDE / max(box["width"], box["height"])),
                            self.directory / "frames" / uuid4().hex, parameters or None, trace)

    async def save_effect(self, request: EffectSaveInput) -> EffectSaveOutput:
        """Check the workspace and save it as this task's latest candidate, returning its acceptance sheet."""
        draft, notes = await self._assemble()
        keywords = preview_keywords(draft)
        capture = self._capture(draft, [draft.composition.duration_frames // 2], keywords, trace=trial_frames(draft))
        problems, sprite = await self._gate(draft, capture)
        if sprite is None:
            listed = "\n".join(f"- [{item.check}] {item.message}" + (f" (line {item.line}: {item.excerpt})" if item.line and item.excerpt else "") for item in problems)
            raise ToolFault("CODE_VALIDATION_FAILED", f"The effect was not saved; fix these and save again:\n{listed}"[:6000],
                            details={"problems": [item.model_dump(mode="json", exclude_none=True) for item in problems]})
        record = SpriteRecord(sprite_id=uuid4().hex, created_at=datetime.now(UTC).isoformat(), **sprite.model_dump(mode="json", exclude_unset=True))
        self.saved_sprites[record.sprite_id] = record
        self.latest_sprite_id = record.sprite_id
        self.saved_hash = self.checked_hash = self.workspace_hash()
        frames = self._frames_out(self._middle_shot(capture, sprite), "saved candidate")
        self.pending_sheet = record.sprite_id
        return EffectSaveOutput(sprite_id=record.sprite_id, created_at=record.created_at, composition=record.composition, motion=self._motion(capture, sprite), notes=notes,
                                review=SpriteReview(frames=frames, rounds_left=self.renders_left, **({"keywords": keywords} if keywords else {})))

    async def render_preview(self, request: PreviewRenderInput) -> PreviewRenderOutput:
        """Render chosen frames of the workspace as it stands (a content variant if asked) without checking or saving it."""
        if self.renders_left <= 0:
            raise ToolFault("RESOURCE_LIMIT_EXCEEDED", f"This task has used its {PREVIEW_RENDERS} previews: check and save the effect instead.")
        from ..tool_validation import ValidationUnavailable

        draft, notes = await self._assemble(VARIANT_FRAMES.get(request.variant))
        last = draft.composition.duration_frames - 1
        wanted = list(dict.fromkeys(min(frame, last) for frame in request.frames)) if request.frames else trial_frames(draft)
        keywords = list(request.keywords) if request.keywords is not None else preview_keywords(draft)
        extra = _long_text(draft.default_parameters) if request.variant == "long" and draft.kind == "text" else None
        capture = self._capture(draft, wanted, keywords, extra, trace=trial_frames(draft))
        self.renders_left -= 1
        try:
            report = await self.validator.validate_render(RenderValidationInput(
                component=ComponentDefinition(code=draft.code, parameter_schema=draft.parameter_schema, default_parameters=draft.default_parameters),
                duration_frames=draft.composition.duration_frames), capture=capture)
        except ValidationUnavailable as exc:
            raise ToolFault("VALIDATION_UNAVAILABLE", f"The isolated renderer is unavailable: {exc}"[:3000]) from exc
        if not report.passed:
            problems = [_diagnostic_problem(item).message for item in report.code_validation.diagnostics if item.severity == "error"]
            problems += [f"[{item.name}] {item.message}" for item in report.checks if item.status != "passed" and item.message]
            raise ToolFault("CODE_VALIDATION_FAILED", "The effect does not render: " + "\n".join(problems[:6])[:4000])
        shots = ([(capture.directory / f"frame-{frame}.png", frame, "requested") for frame in wanted if (capture.directory / f"frame-{frame}.png").is_file()]
                 if request.frames else self._keyframes(capture, draft))
        frames = self._frames_out(shots, f"preview ({request.variant})")
        return PreviewRenderOutput(duration_frames=draft.composition.duration_frames, frames=frames, motion=self._motion(capture, draft), notes=notes, **({"keywords": keywords} if keywords else {}))

    async def base_preview(self) -> None:
        """Render the accepted version's acceptance sheet once for an edit, as `base_frames` and the first observation images, without using a preview.

        The Plan and the Executor share the render. A failure leaves the edit without them (the Executor can still look with
        preview_render) and is recorded in the private audit.
        """
        if self.base_frames:
            self.observations, self.observations_fresh = list(self.base_frames), True
            return
        try:
            draft, _notes = await self._assemble()
            capture = self._capture(draft, [draft.composition.duration_frames // 2], preview_keywords(draft), trace=trial_frames(draft))
            report = await self.validator.validate_render(RenderValidationInput(
                component=ComponentDefinition(code=draft.code, parameter_schema=draft.parameter_schema, default_parameters=draft.default_parameters),
                duration_frames=draft.composition.duration_frames), capture=capture)
        except Exception as exc:
            self.budget.record("base_preview_failed", error=str(exc)[:1000])
            return
        if report.passed:
            self._frames_out(self._middle_shot(capture, draft), "accepted version")
            self.base_frames = list(self.observations)
            self.base_motion = self._motion(capture, draft)

    def inspect_image(self, request: ImageInspectInput) -> ImageInspectOutput:
        """Crop a region of a reference image and enlarge it, as the next observation image."""
        if request.image > len(self.images):
            raise ToolFault("INVALID_ARGUMENT", f"There are {len(self.images)} reference image(s); image counts from 1.", field="/image")
        with Image.open(self.images[request.image - 1]) as source:
            width, height = source.size
            left, top = round(request.x * width), round(request.y * height)
            right, bottom = min(width, round((request.x + request.width) * width)), min(height, round((request.y + request.height) * height))
            if right - left < 4 or bottom - top < 4:
                raise ToolFault("INVALID_ARGUMENT", "The region is outside the image or too small; x + width and y + height stay within 1.", field="/width")
            region = source.convert("RGBA").crop((left, top, right, bottom))
        scale = min(4.0, 1024 / max(region.size))
        if scale > 1:
            region = region.resize((round(region.width * scale), round(region.height * scale)), Image.Resampling.LANCZOS)
        path = self.directory / "inspect" / f"{uuid4().hex}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        region.save(path)
        self.observations = [(path, f"reference image {request.image}, region x {request.x:g} y {request.y:g} width {request.width:g} height {request.height:g}")]
        self.observations_fresh = True
        self.pending_sheet = None
        return ImageInspectOutput(image=1, width=region.width, height=region.height, source_width=width, source_height=height)
