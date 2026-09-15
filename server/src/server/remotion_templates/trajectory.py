"""Compare host-observed checkpoints against current evidence and feed repairs concrete evidence."""

import hashlib
from dataclasses import dataclass

from .models import (
    TemplateCandidate,
    TemplateSpec,
    ValidationReport,
    validation_fingerprint,
)


class DecisionProgress:
    """Bound decisions without new host observations; source-only changes and prose do not prove progress."""

    def __init__(self):
        """Retain compact observation identities for this run, outside the conversation window."""
        self.seen: set[str] = set()
        self.stalled_turns = 0

    def observe(self, report, *, read_current=False) -> bool:
        """Reset only for a new check outcome/diagnostic or the first read of a current candidate."""
        observations = []
        if report is not None:
            observations = [
                repr(
                    (
                        check.name,
                        check.status,
                        check.detail
                        if check.source == "host" and check.status != "pass"
                        else None,
                    )
                )
                for check in report.checks
            ]
        if read_current:
            observations.append("current_template_observed")
        fresh = set(observations) - self.seen
        self.seen.update(observations)
        self.stalled_turns = 0 if fresh else self.stalled_turns + 1
        return bool(fresh)


@dataclass(frozen=True)
class Assessment:
    """A host decision, separate from model prose, with feedback for the next action."""

    verdict: str
    completion_allowed: bool
    feedback: tuple[str, ...]


class Trajectory:
    """Track one bounded generation run; revisable estimates never substitute for current artifact evidence."""

    def __init__(self) -> None:
        """Track observations without freezing model-estimated layout values."""
        self.previous: dict[str, str] | None = None
        self.observed: set[str] = set()

    def observe(
        self, candidate: TemplateCandidate, spec: TemplateSpec, report: ValidationReport
    ) -> Assessment:
        """Detect stale evidence, regressions and exact cycles without freezing estimated plan values."""
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
            # A later submission in the same tool batch must pass its own checks.
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
            verdict = "repair"
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
