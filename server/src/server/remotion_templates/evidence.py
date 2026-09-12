"""Seal host-generated artifacts and reject changed or missing bytes before review and publication."""

import hashlib
import json
from pathlib import Path

from .models import TemplateCandidate, TemplateSpec, ValidationReport


def digest(path: Path) -> str:
    """Hash a regular local artifact; symlinks must never redirect evidence or exports."""
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Missing or redirected evidence: {path.name}")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def seal_artifacts(
    candidate: TemplateCandidate,
    spec: TemplateSpec,
    report: ValidationReport,
    directory: Path,
) -> None:
    """Capture only actual output bytes, after the renderer has stopped all child processes."""
    names = ["Template.tsx", "candidate.json", "spec.json"]
    if any(
        check.name == "render" and check.status == "pass" for check in report.checks
    ):
        names += ["preview.mp4", *[f"frame-{frame}.png" for frame in report.frames]]
    if any(
        check.name == "parameter_render" and check.status == "pass"
        for check in report.checks
    ):
        request = json.loads((directory / "request.json").read_text())
        names += [
            "request.json",
            "renderer.json",
            "repeat.png",
            *[f"probe-{index}.png" for index in range(len(request["probes"]))],
        ]
    report.artifacts = {name: digest(directory / name) for name in names}
    verify_artifacts(candidate, spec, report, directory)


def verify_artifacts(
    candidate: TemplateCandidate,
    spec: TemplateSpec,
    report: ValidationReport,
    directory: Path,
) -> None:
    """Bracket model review and completion with current byte checks; no self-reported evidence is accepted."""
    required = {"Template.tsx", "candidate.json", "spec.json"}
    if any(
        check.name == "render" and check.status == "pass" for check in report.checks
    ):
        required |= {"preview.mp4", *[f"frame-{frame}.png" for frame in report.frames]}
        if not report.frames:
            raise ValueError("Rendered frame evidence is missing")
    if not required <= report.artifacts.keys():
        raise ValueError("Artifact evidence manifest is incomplete")
    for name, expected in report.artifacts.items():
        if Path(name).name != name or digest(directory / name) != expected:
            raise ValueError("Artifact evidence changed; validate current bytes again")
    if (directory / "Template.tsx").read_text() != candidate.tsx_code:
        raise ValueError("Validated source differs from candidate")
    if (
        TemplateCandidate.model_validate_json(
            (directory / "candidate.json").read_text()
        )
        != candidate
    ):
        raise ValueError("Validated candidate configuration changed")
    if TemplateSpec.model_validate_json((directory / "spec.json").read_text()) != spec:
        raise ValueError("Validated target changed")
