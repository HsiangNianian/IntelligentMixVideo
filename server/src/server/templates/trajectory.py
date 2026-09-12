"""Compare host-observed checkpoints against a fixed goal and feed repairs concrete evidence."""

import hashlib
from dataclasses import dataclass

from .models import (
    TemplateCandidate,
    TemplateSpec,
    ValidationReport,
    validation_fingerprint,
)


@dataclass(frozen=True)
class Assessment:
    """A host decision, separate from model prose, with feedback for the next action."""

    verdict: str
    completion_allowed: bool
    feedback: tuple[str, ...]


class Trajectory:
    """Track one bounded generation run; changed goals or stale evidence never count as progress."""

    def __init__(self, spec: TemplateSpec) -> None:
        """Freeze the requested target before the actor starts writing or repairing code."""
        self.goal = spec.model_dump_json()
        self.previous: dict[str, str] | None = None
        self.observed: set[str] = set()

    def observe(
        self, candidate: TemplateCandidate, spec: TemplateSpec, report: ValidationReport
    ) -> Assessment:
        """Detect goal drift, regressions and exact cycles without treating unknown as success."""
        if spec.model_dump_json() != self.goal:
            return Assessment(
                "goal_drift",
                False,
                ("Restore the agreed TemplateSpec; do not weaken the goal to pass.",),
            )
        if report.fingerprint != validation_fingerprint(
            candidate, spec, report.runtime
        ):
            return Assessment(
                "stale_evidence",
                False,
                (
                    "Revalidate the current code and configuration; previous evidence is invalid.",
                ),
            )
        names = [check.name for check in report.checks]
        if len(names) != len(set(names)):
            return Assessment(
                "invalid_evidence",
                False,
                ("Each check must have one unambiguous current verdict.",),
            )
        current = {check.name: check.status for check in report.checks}
        feedback = tuple(
            f"{check.name}: {check.status}: {check.detail}"
            for check in report.checks
            if check.status != "pass"
        )
        if report.passed:
            # Eligibility does not stop the actor from submitting another candidate before completion.
            self.previous = current
            return Assessment("complete", True, ())
        regressed = sorted(
            name
            for name, status in (self.previous or {}).items()
            if status == "pass" and current.get(name) != "pass"
        )
        # Content identity includes runtime/font revisions, but excludes varying diagnostic wording.
        state = hashlib.sha256(
            (report.fingerprint + repr(sorted(current.items()))).encode()
        ).hexdigest()
        if state in self.observed:
            verdict = "non_progress_cycle"
            feedback += (
                "This candidate and its check outcomes were already observed; change the failing implementation.",
            )
        elif regressed:
            verdict = "regression"
            feedback += (
                f"Previously passing checks regressed: {', '.join(regressed)}. Preserve working behavior.",
            )
        elif any(
            current.get(name) == "fail"
            for name in (
                "source_policy",
                "visual_text",
                "visual_layout",
                "visual_style",
                "visual_scope",
            )
        ):
            verdict = "goal_drift"
        elif self.previous and any(
            status == "pass" and self.previous.get(name) != "pass"
            for name, status in current.items()
        ):
            verdict = "progress"
        else:
            verdict = "insufficient_evidence"
        self.observed.add(state)
        self.previous = current
        return Assessment(
            verdict,
            False,
            feedback or ("Required validation evidence is missing; run all checks.",),
        )
