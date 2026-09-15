"""Compare visible PNG evidence with bounded raster tolerance; feed measured differences to steer."""

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops

from .models import Check, TemplateSpec

# Allow sparse rounding noise, never a large change diluted by a transparent canvas.
MAX_CHANNEL_DELTA = 2
MAX_CHANGED_FRACTION = 0.01


@dataclass(frozen=True)
class ImageDifference:
    """Measure visible change against the union of nontransparent content, not canvas area."""

    changed_pixels: int
    visible_pixels: int
    max_delta: int
    bounds: tuple[int, int, int, int] | None

    @property
    def equivalent(self) -> bool:
        """Require both low amplitude and sparse coverage to classify raster noise as equivalent."""
        return (
            self.max_delta <= MAX_CHANNEL_DELTA
            and self.changed_pixels <= self.visible_pixels * MAX_CHANGED_FRACTION
        )

    @property
    def detail(self) -> str:
        """Expose measurements and fixed host thresholds for review and actionable tool feedback."""
        fraction = (
            self.changed_pixels / self.visible_pixels if self.visible_pixels else 0
        )
        return (
            f"Visible difference: {self.changed_pixels}/{self.visible_pixels} pixels "
            f"({fraction:.4%} of foreground), max channel delta={self.max_delta}/255, "
            f"bbox={self.bounds}; tolerance requires delta<={MAX_CHANNEL_DELTA}/255 "
            f"AND changed foreground<={MAX_CHANGED_FRACTION:.0%}."
        )


def compare_images(first: Image.Image, second: Image.Image) -> ImageDifference:
    """Compare on black and white to ignore invisible RGB while preserving color and alpha changes."""
    if first.size != second.size:
        raise ValueError(f"PNG dimensions differ: {first.size} vs {second.size}")
    first, second = first.convert("RGBA"), second.convert("RGBA")
    visible = ImageChops.lighter(first.getchannel("A"), second.getchannel("A"))
    difference = Image.new("L", first.size)
    # Opposite backgrounds expose both light/dark text and opacity differences.
    for color in ("black", "white"):
        background = Image.new("RGBA", first.size, color)
        a = Image.alpha_composite(background, first).convert("RGB")
        b = Image.alpha_composite(background, second).convert("RGB")
        for channel in ImageChops.difference(a, b).split():
            difference = ImageChops.lighter(difference, channel)
    histogram = difference.histogram()
    return ImageDifference(
        changed_pixels=sum(histogram[1:]),
        visible_pixels=sum(visible.histogram()[1:]),
        max_delta=difference.getextrema()[1],
        bounds=difference.getbbox(),
    )


def consistency_checks(
    directory: Path, spec: TemplateSpec, frames: list[int]
) -> list[Check]:
    """Verify captured repeat/export frames on the host; absent or corrupt evidence fails closed."""
    checks = []
    if not frames:
        return [
            Check(
                name="determinism",
                status="fail",
                detail="No sampled frames to compare.",
            )
        ]
    for name, frame, filename, guidance in (
        (
            "determinism",
            frames[len(frames) // 2],
            "repeat.png",
            (
                "Keep the same props and frame deterministic: inspect mutable module data, "
                "prop mutation and CSS/wall-clock animation. Derive animation only from the "
                "Remotion frame; preserve requested styling and motion. If already pure, "
                "report a renderer limitation rather than blindly rewriting the design."
            ),
        ),
        (
            "export_defaults",
            frames[0],
            "export-default.png",
            (
                "Check exported default props and prop forwarding against default_config; "
                "preserve the agreed design. A pure matching export may need renderer investigation."
            ),
        ),
    ):
        original = f"frame-{frame}.png"
        try:
            with (
                Image.open(directory / original) as first,
                Image.open(directory / filename) as second,
            ):
                expected = (spec.composition.width, spec.composition.height)
                if first.size != expected or second.size != expected:
                    raise ValueError(
                        f"PNG dimensions must match requested canvas {expected}"
                    )
                difference = compare_images(first, second)
            detail = f"Frame {frame}, {original} vs {filename}. {difference.detail}"
            if not difference.equivalent:
                detail += " " + guidance
            checks.append(
                Check(
                    name=name,
                    frame=frame,
                    status="pass" if difference.equivalent else "fail",
                    detail=detail,
                )
            )
        except (OSError, ValueError) as exc:
            checks.append(
                Check(
                    name=name,
                    frame=frame,
                    status="fail",
                    detail=f"Cannot compare {original} vs {filename}: {exc}. "
                    "Rendering evidence is missing or invalid; rerun validation before completion.",
                )
            )
    return checks
