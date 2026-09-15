"""Recover visual-judge protocol failures on immutable evidence, without sending them to the code actor."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .evidence import verify_artifacts
from .models import Check, VisualReview
from .provider import ExecutionFailure, ModelContractFailure


class ReviewUnavailable(ExecutionFailure):
    """A bounded judge recovery ended without a usable assessment; no code repair is justified."""

    def __init__(self, message):
        """Publish the recovery reason without exposing model evidence to the public conversation."""
        super().__init__("review_unavailable", message)


def static_motion_verified(spec, report) -> bool:
    """Describe observed static output; this never decides whether the user requested static behavior."""
    return all(
        layer.start_frame == 0
        and layer.end_frame == spec.composition.duration_in_frames
        and all(motion.phase == "hold" for motion in layer.motion)
        for layer in spec.text_layers
    ) and {"determinism", "motion_evidence"} <= {
        check.name
        for check in report.checks
        if check.source == "host" and check.status == "pass"
    }


@dataclass
class ReviewResult:
    """Keep judge protocol failure distinct from missing evidence and valid artifact failures."""

    checks: list[Check]
    requested_frames: list[int] = field(default_factory=list)
    protocol_failed: bool = False


def review_errors(review, frames, duration) -> list[str]:
    """Check citations and explicit contradictory status declarations; free-form reasoning is not proven by this guard."""
    errors = []
    for item in review.checks:
        if item.frame is not None and item.frame not in frames:
            errors.append(
                f"{item.name}: frame {item.frame} is not in supplied frames {frames}; use frame_images, not image ordinals"
            )
        if item.status in {"pass", "fail"} and (
            item.missing_evidence or item.requested_frames
        ):
            errors.append(
                f"{item.name}: a known judgment cannot also require missing evidence"
            )
        if any(
            type(frame) is not int or not 0 <= frame < duration
            for frame in item.requested_frames
        ):
            errors.append(
                f"{item.name}: requested_frames must be actual frame numbers within the composition"
            )
        # A narrow protocol check for the observed failure, not a general semantic classifier.
        declarations = re.findall(
            r"\bstatus\s*(?:is|:|=)\s*[`'\"]?(pass|fail|unknown|conflict)\b",
            item.detail,
            re.IGNORECASE,
        )
        if any(value.lower() != item.status for value in declarations):
            errors.append(
                f"{item.name}: status={item.status} contradicts the explicit status in detail; re-evaluate the cited evidence, never flip a status just to pass"
            )
    return errors


async def review_candidate(
    ask,
    settings,
    scope,
    candidate,
    spec,
    report,
    directory: Path,
    images,
    budget,
    intent,
):
    """Retry malformed/citation/conflicting output within the judge budget; valid failures return immediately."""
    static = static_motion_verified(spec, report)
    payload = {
        "user_intent": intent,
        "candidate_plan": spec.model_dump(include={"composition", "text_layers"}),
        "frames": report.frames,
        "reference_count": len(images),
        "frame_images": [
            {"image_position": len(images) + index + 1, "frame": frame}
            for index, frame in enumerate(report.frames)
        ],
        "host_motion": "verified_static" if static else "semantic_review_required",
    }
    instruction = (
        scope
        + """
Independently inspect actual frames against the original user_intent and reference images. candidate_plan describes the implementation, NOT acceptance requirements: never reject corrected inferred coordinates, size, line-height or timing just because they differ from a previous estimate. For edits, preserve unrelated accepted_base properties and honor the latest instruction/parameters. Block only clear user-visible errors: wrong/missing wording, unreadable overlap or clipping, clearly wrong requested style, missing requested animation or unauthorized content. Allow reasonable font approximation and small aesthetic/layout differences; do not demand pixel-exact matching or personal aesthetic preferences. Explicit user constraints still apply.
Return exactly five checks: text, layout, style, motion, scope. Cite actual Remotion frame numbers from frames, not image positions; use null for judgments spanning frames. frame_images maps one-based positions in the complete image list to actual frames. Reference images have no frame number.
Check readable wording, clipping, placement, styling, requested temporal behavior and typography-only scope. Do not demand background reconstruction. host_motion=verified_static reports observed static output only; it does NOT authorize ignoring a user request for animation. Check requested behavior against user_intent even if candidate_plan claims motion: []. When static behavior is requested, trust host noise-tolerant temporal checks; do not require explicit hold or invent animation requirements.
Unknown evidence stays unknown. A valid failure must identify an actual mismatch against an existing requirement. Status and detail must agree. Correction requests concern the assessment, not permission to relax the target or accept an artifact.
Use conflict for inconsistent evidence and unknown for missing evidence. State missing_evidence explicitly; requested_frames can ask for up to eight valid additional frame numbers. A known pass/fail cannot require missing evidence. The host may capture requested frames within its budget; do not invent their contents.
"""
    )
    retained = {}
    for attempt in range(settings.max_review_retries + 1):
        verify_artifacts(candidate, spec, report, directory)
        result = None
        try:
            result = await ask(
                VisualReview,
                instruction,
                json.dumps(payload, ensure_ascii=False),
                budget,
                images=images
                + [directory / f"frame-{frame}.png" for frame in report.frames],
                vision=True,
                phase="judge",
            )
            errors = review_errors(
                result, report.frames, spec.composition.duration_in_frames
            )
            # Correct only invalid dimensions; a valid negative conclusion is never retried into a pass.
            for item in result.checks:
                if item.name not in retained and not any(
                    error.startswith(item.name + ":") for error in errors
                ):
                    retained[item.name] = item
        except ModelContractFailure as exc:
            errors = [str(exc)]
        verify_artifacts(candidate, spec, report, directory)
        with (directory / "reviews.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "attempt": attempt + 1,
                        "fingerprint": report.fingerprint,
                        "assessment": result.model_dump() if result else None,
                        "errors": errors,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        if not errors:
            findings = [retained[item.name] for item in result.checks]
            return ReviewResult(
                [
                    Check(
                        name="visual_" + item.name,
                        source="visual_model",
                        status="unknown" if item.status == "conflict" else item.status,
                        detail=item.detail,
                        frame=item.frame,
                    )
                    for item in findings
                ],
                sorted({frame for item in findings for frame in item.requested_frames}),
            )
        payload["correction"] = {
            "errors": errors,
            "previous_assessment": result.model_dump() if result else None,
            "instruction": "Correct the assessment against the same immutable evidence. Valid negative conclusions must remain negative.",
        }
    return ReviewResult(
        [
            Check(
                name="visual_" + name,
                source="visual_model",
                status="unknown",
                detail="Visual review protocol remained invalid after bounded correction: "
                + "; ".join(errors)[:1000],
            )
            for name in ("text", "layout", "style", "motion", "scope")
        ],
        protocol_failed=True,
    )
