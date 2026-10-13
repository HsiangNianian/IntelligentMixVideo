"""Build sealed PR76 code/Player artifacts; successful tool checks are never replaced by a model verdict."""

import json
import shutil
import subprocess
from pathlib import Path

from .evidence import digest, seal_artifacts, verify_artifacts
from .models import (
    Check, CompositionConfig, TemplateCandidate, TemplateSpec, TemplateVersion, ValidationReport, validation_fingerprint,
)
from .parameters import patch_parameters
from .tools.contracts import RenderValidationReport, SpriteDraft

# 预览包首行声明的宿主协议版本（逐帧同步、读取页面注入的默认值与内容区域、接受参数覆盖和宿主变换），与
# presentation.mjs 的 PREVIEW_PROTOCOL 一致；更早构建的包没有这一行，需要重建。只认首行：生成代码与默认参数
# 也会打进包里，包内任意位置出现的协议字样都不可信。
PREVIEW_PROTOCOL = b"/* imv-preview-protocol 1 */"


def current_bundle(bundle: bytes) -> bool:
    """Whether a player bundle declares the current host protocol on its first line, which generated code cannot write."""
    return bundle.startswith(PREVIEW_PROTOCOL + b"\n")


def revisable(accepted: Path) -> bool:
    """Whether an accepted version's parameters can be revised instantly by reusing its player bundle.

    Only a bundle that reads the defaults the page injects shows a revision's new defaults; an older one would keep
    showing the defaults compiled into it, so such a version is revised by rebuilding in the sandbox instead.
    """
    try:
        return current_bundle((accepted / "interactive.js").read_bytes())
    except OSError:
        return False


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
            "host_layer": settings.renderer_dir / "host-layer.tsx",
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
        report = ValidationReport(profile="v2", fingerprint=validation_fingerprint(candidate, spec, runtime), runtime=runtime, checks=[
            Check(name="code_validation", status="pass", detail="PR76 code and parameter contract checked."),
            Check(name="runtime_validation", status="pass", detail=f"Base runtime checks and {validation.custom_tests_executed} supplied scripts passed; no visual review performed."),
            Check(name="export_source", status="pass", detail="Standalone export typechecked with omitted props."),
            Check(name="presentation_bundle", status="pass", detail="Isolated Player bundle built."),
        ])
        seal_artifacts(candidate, spec, report, directory)
        verify_artifacts(candidate, spec, report, directory)
        return candidate, spec, report, directory

    def revise(self, base: TemplateVersion, accepted: Path, patch: dict, directory: Path):
        """Seal a parameter revision of an accepted PR76 version at once, without the sandbox or a new bundle.

        The code is unchanged and the new defaults satisfy the parameter schema, so the player bundle and the
        runtime evidence are reused: the player reads its defaults from the page, and the publish-time boundary
        samples render any value inside the schema. Only the standalone export, which embeds the defaults, is
        regenerated, by parsing and formatting the sealed source on the host.
        """
        verify_artifacts(base.candidate, base.spec, base.validation, accepted)
        if not revisable(accepted):
            raise ValueError("The base player bundle cannot show new defaults; revise it in the sandbox")
        candidate, spec = patch_parameters(base.candidate, base.spec, patch)
        directory.mkdir(parents=True, exist_ok=False)
        for name in ("Template.tsx", "interactive.js"):
            shutil.copyfile(accepted / name, directory / name)
        script = self.renderer.settings.renderer_dir / "export.mjs"
        try:
            process = subprocess.run(
                ["node", str(script)],
                input=json.dumps({"code": candidate.tsx_code, "config": candidate.default_config, "composition": spec.composition.model_dump()}, ensure_ascii=False),
                text=True, capture_output=True, timeout=60, check=True, cwd=script.parent,
            )
            exported = json.loads(process.stdout)["code"]
        except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
            raise ValueError("Standalone export could not be regenerated") from exc
        (directory / "Export.tsx").write_text(exported, encoding="utf-8")
        (directory / "candidate.json").write_text(candidate.model_dump_json(), encoding="utf-8")
        (directory / "spec.json").write_text(spec.model_dump_json(), encoding="utf-8")
        inherited = json.loads((accepted / "tool-validation.json").read_text())
        (directory / "tool-validation.json").write_text(json.dumps({
            "component": {"code": candidate.tsx_code, "parameter_schema": candidate.config_schema, "default_parameters": candidate.default_config},
            "report": inherited["report"],
            "inherited_from": str(base.id),
        }, ensure_ascii=False), encoding="utf-8")
        runtime = base.validation.runtime
        report = ValidationReport(profile="v2", fingerprint=validation_fingerprint(candidate, spec, runtime), runtime=runtime, checks=[
            Check(name="code_validation", status="pass", detail=f"Code unchanged from version {base.number}; new defaults checked against the parameter schema."),
            Check(name="runtime_validation", status="pass", detail="Runtime checks inherited from the base version; publishing renders boundary samples of the parameters."),
            Check(name="export_source", status="pass", detail="Standalone export regenerated with the new defaults."),
            Check(name="presentation_bundle", status="pass", detail="Player bundle reused; the preview page supplies the new defaults."),
        ])
        seal_artifacts(candidate, spec, report, directory)
        return candidate, spec, report, directory
