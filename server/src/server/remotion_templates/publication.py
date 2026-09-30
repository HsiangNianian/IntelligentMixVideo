"""Build sealed PR76 code/Player artifacts; successful tool checks are never replaced by a model verdict."""

import json
from pathlib import Path

from .evidence import digest, seal_artifacts, verify_artifacts
from .models import Check, CompositionConfig, TemplateCandidate, TemplateSpec, ValidationReport, validation_fingerprint
from .tools.contracts import RenderValidationReport, SpriteDraft


class PresentationBuilder:
    """Publish an exact Sprite and parameter snapshot through the existing isolated worker boundary."""

    def __init__(self, renderer):
        """Keep renderer lifecycle and configured dependency paths outside generated code."""
        self.renderer = renderer

    def environment(self) -> dict[str, str]:
        """Bind public artifacts to managed source, lockfile and the fonts served by the preview route."""
        settings = self.renderer.settings
        paths = {
            "font_400": settings.font_regular,
            "font_700": settings.font_bold,
            "presentation": settings.renderer_dir / "presentation.mjs",
            "presentation_worker": settings.renderer_dir / "presentation-worker.mjs",
            "preview_host": settings.renderer_dir / "preview-host.tsx",
            "dependencies": settings.renderer_dir / "bun.lock",
        }
        return {key: digest(path) for key, path in paths.items()}

    async def build(self, sprite: SpriteDraft, defaults: dict, validation: RenderValidationReport, directory: Path):
        """Construct code and an interactive preview only after the supplied runtime checks pass."""
        required = {"default_render", "configured_render"}
        if not required <= {item.name for item in validation.checks} or not validation.passed or not validation.code_validation.passed or any(item.status != "passed" for item in [*validation.checks, *validation.tests]):
            raise ValueError("Current Sprite runtime validation has not passed")
        if validation.composition != sprite.composition:
            raise ValueError("Validation belongs to another composition")
        composition = CompositionConfig(width=1080, height=1920, fps=30, duration_in_frames=sprite.composition.duration_frames)
        spec = TemplateSpec(schema_version="2", name=sprite.description[:100], description=sprite.description, composition=composition, sprite_kind="composition", sprite=sprite)
        candidate = TemplateCandidate(tsx_code=sprite.code, config_schema=sprite.parameter_schema, default_config=defaults)
        directory.mkdir(parents=True, exist_ok=False)
        runtime = self.environment()
        (directory / "request.json").write_text(json.dumps({"code": candidate.tsx_code, "config": defaults, "composition": composition.model_dump()}, ensure_ascii=False), encoding="utf-8")
        (directory / "candidate.json").write_text(candidate.model_dump_json(), encoding="utf-8")
        (directory / "spec.json").write_text(spec.model_dump_json(), encoding="utf-8")
        (directory / "tool-validation.json").write_text(json.dumps({"component": {"code": sprite.code, "parameter_schema": sprite.parameter_schema, "default_parameters": defaults}, "report": validation.model_dump(mode="json", exclude_unset=True)}, ensure_ascii=False), encoding="utf-8")
        result = await self.renderer.run_worker(directory, worker="presentation-worker.mjs")
        received = {item["name"]: item["status"] for item in result.get("checks", [])}
        if received != {"export_source": "pass", "presentation_bundle": "pass"}:
            raise ValueError("Presentation worker did not finish its required checks")
        if runtime != self.environment():
            raise ValueError("Presentation environment changed during publication")
        report = ValidationReport(profile="pr76", fingerprint=validation_fingerprint(candidate, spec, runtime), runtime=runtime, checks=[
            Check(name="code_validation", status="pass", detail="PR76 code and parameter contract checked."),
            Check(name="runtime_validation", status="pass", detail=f"Base runtime checks and {validation.custom_tests_executed} supplied scripts passed; no visual review performed."),
            Check(name="export_source", status="pass", detail="Standalone export typechecked with omitted props."),
            Check(name="presentation_bundle", status="pass", detail="Isolated Player bundle built."),
        ])
        seal_artifacts(candidate, spec, report, directory)
        verify_artifacts(candidate, spec, report, directory)
        return candidate, spec, report, directory
