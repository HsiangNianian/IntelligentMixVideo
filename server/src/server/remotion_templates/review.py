"""Recover visual-judge protocol failures on immutable evidence, without sending them to the code actor."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .evidence import verify_artifacts
from .models import Check, VisualReview
from .provider import ExecutionFailure, ModelContractFailure
from .visual_evidence import select_frames


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


def grounding_error(item, intent, targets, reference_count):
    """Validate factual references and structural scope; natural-language entailment remains the judge's responsibility."""
    if not all(
        isinstance(value, str) and value.strip()
        for value in (
            item.requirement_source,
            item.requirement_quote,
            item.target,
            item.observed,
            item.mismatch,
        )
    ):
        return "fail requires requirement_source, requirement_quote, target, observed and mismatch; use unknown for unresolved requirements"
    if item.target not in targets | {"canvas"}:
        return "target must identify an existing text layer or canvas"
    parts = item.requirement_source.split("/")[1:]
    if not item.requirement_source.startswith("/") or not parts:
        return "requirement_source must be a JSON pointer"
    parts = [part.replace("~1", "/").replace("~0", "~") for part in parts]
    if parts[0] == "reference_images":
        if (
            len(parts) != 2
            or not parts[1].isdigit()
            or not 0 <= int(parts[1]) < reference_count
        ):
            return "reference_images must cite an existing original reference, never inspection images or their background"
        return None
    if parts[0] not in {
        "instruction",
        "original_request",
        "clarifications",
        "parameters",
        "accepted_base",
    }:
        return "requirements must come from user_intent or original references, never candidate estimates"
    if parts[0] == "original_request" and (
        len(parts) < 2 or parts[1] not in {"description", "composition"}
    ):
        return "cite original description/composition or an original reference image"
    if (
        parts[:2]
        in (["original_request", "composition"], ["accepted_base", "composition"])
        and item.target != "canvas"
    ):
        return "a composition property must target canvas"
    if parts[0] == "parameters" and len(parts) == 2:
        try:
            index = parts[1].split("_", 1)[0]
            layer = intent["accepted_base"]["text_layers"][int(index)]
            if not index.isdigit() or item.target != layer["id"]:
                return "a layer parameter cannot be expanded to another layer or canvas"
        except (KeyError, IndexError, TypeError, ValueError):
            return "parameter target does not identify an accepted layer"
    if parts[0] == "accepted_base":
        if len(parts) < 3 or parts[1] not in {"text_layers", "composition"}:
            return "cite an accepted property, not inferred descriptions or assumptions"
        if parts[1] == "text_layers":
            try:
                layer = intent["accepted_base"]["text_layers"][int(parts[2])]
                if not parts[2].isdigit() or item.target != layer["id"]:
                    return "an accepted layer property cannot be expanded to another layer or canvas"
                if len(parts) < 4 or parts[3] not in {
                    "text",
                    "style",
                    "layout",
                    "motion",
                    "decorations",
                }:
                    return "cite a specific visible accepted property"
                if "_".join(parts[2:]) in (intent.get("parameters") or {}):
                    return "this accepted property is superseded by the latest parameters; cite the current parameter requirement"
            except (KeyError, IndexError, ValueError, TypeError):
                return "accepted layer reference does not exist"
    value = intent
    try:
        for part in parts:
            value = (
                value[int(part)]
                if isinstance(value, list) and part.isdigit()
                else value[part]
            )
    except (KeyError, IndexError, TypeError):
        return "requirement_source does not exist in user_intent"
    if value is None or isinstance(value, (dict, list)):
        return "cite a specific requirement value, not an entire object"
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if item.requirement_quote.strip() not in text:
        return "requirement_quote is not present in the cited source; do not invent acceptance requirements"
    return None


def review_errors(
    review, frames, duration, *, intent=None, targets=None, reference_count=0
) -> list[str]:
    """Check citations and explicit contradictory status declarations; free-form reasoning is not proven by this guard."""
    errors = []
    for item in review.checks:
        if item.status == "fail":
            error = grounding_error(
                item, intent or {}, targets or set(), reference_count
            )
            if error:
                errors.append(f"{item.name}: {error}")
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
        if set(item.requested_frames) & set(frames):
            errors.append(
                f"{item.name}: requested_frames must supply new evidence, not already displayed frames"
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
    required_frames=(),
):
    """Retry malformed/citation/conflicting output within the judge budget; valid failures return immediately."""
    static = static_motion_verified(spec, report)
    frames = select_frames(
        spec,
        report.frames,
        preferred=required_frames,
        limit=max(3, min(12, budget.remaining(settings) // 6000)),
    )
    targets = {layer.id for layer in spec.text_layers}
    accepted = (intent or {}).get("accepted_base") or {}
    targets.update(layer["id"] for layer in accepted.get("text_layers", []))
    payload = {
        "user_intent": intent,
        "candidate_plan": spec.model_dump(),
        "frames": frames,
        "available_frames": report.frames,
        "reference_count": len(images),
        "frame_images": [
            {"image_position": len(images) + index + 1, "frame": frame}
            for index, frame in enumerate(frames)
        ],
        "host_motion": "verified_static" if static else "semantic_review_required",
    }
    instruction = (
        scope
        + """
Independently inspect actual frames against the original user_intent and reference images. candidate_plan describes the implementation, NOT acceptance requirements: never reject corrected inferred coordinates, size, line-height or timing just because they differ from a previous estimate. For edits, preserve unrelated accepted_base properties and honor the latest instruction/parameters. Block only clear user-visible errors: wrong/missing wording, unreadable overlap or clipping, clearly wrong requested style, missing requested animation or unauthorized content. Allow reasonable font approximation and small aesthetic/layout differences; do not demand pixel-exact matching or personal aesthetic preferences. Explicit user constraints still apply.
Return exactly five checks: text, layout, style, motion, scope. Cite actual Remotion frame numbers from frames, not image positions; use null for judgments spanning frames. frame_images maps one-based positions in the complete image list to actual frames. Reference images have no frame number.
Only frames lists images supplied in this review. available_frames lists all host samples, not all visible observations; request missing frames via requested_frames instead of assuming their contents.
Check readable wording, clipping, placement, styling, requested temporal behavior and typography-only scope. Do not demand background reconstruction. host_motion=verified_static reports observed static output only; it does NOT authorize ignoring a user request for animation. Check requested behavior against user_intent even if candidate_plan claims motion: []. When static behavior is requested, trust host noise-tolerant temporal checks; do not require explicit hold or invent animation requirements.
Unknown evidence stays unknown. A valid failure must identify an actual mismatch against an existing requirement. Status and detail must agree. Correction requests concern the assessment, not permission to relax the target or accept an artifact.
For every fail, provide requirement_source (JSON pointer into user_intent or /reference_images/N, zero-based), requirement_quote (verbatim source excerpt, or a specific original-reference observation), target (text layer id or canvas), observed and mismatch. Cite a specific property of accepted_base, never its inferred description/assumptions. User intent takes precedence: latest instruction/parameters override the corresponding original request and accepted properties, while unrelated accepted behavior remains protected. Candidate descriptions and assumptions explain implementation but cannot create acceptance requirements. Bind each requirement to its actual object: a local text/background/layout request must not expand to the whole composition. If scope is ambiguous, use unknown and describe the ambiguity instead of inventing a failure. Inspection backgrounds and other presentation aids are not authored template content.
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
                images=images + [directory / f"frame-{frame}.png" for frame in frames],
                vision=True,
                phase="judge",
            )
            errors = review_errors(
                result,
                frames,
                spec.composition.duration_in_frames,
                intent=intent,
                targets=targets,
                reference_count=len(images),
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
                        detail=(
                            f"Requirement {item.requirement_source}: {item.requirement_quote}; target={item.target}; observed={item.observed}; mismatch={item.mismatch}. {item.detail}"
                            if item.status == "fail"
                            else item.detail
                        ),
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
