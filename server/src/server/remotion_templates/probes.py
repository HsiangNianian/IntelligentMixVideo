"""Host-selected parameter experiments and pixel checks; visual fidelity still needs independent review."""

from pathlib import Path

from PIL import Image, ImageChops

from .image_comparison import MAX_CHANNEL_DELTA, compare_images
from .models import Check, TemplateCandidate, TemplateSpec


def parameter_probes(candidate: TemplateCandidate, spec: TemplateSpec) -> list[dict]:
    """Exercise text, color, size and both coordinates on every layer at an active midpoint."""
    probes = []
    for index, layer in enumerate(spec.text_layers):
        # Prefer the hold interval; otherwise use the layer midpoint, away from invisible endpoints.
        hold = next((motion for motion in layer.motion if motion.phase == "hold"), None)
        frame = (
            (hold.start_frame + hold.end_frame - 1)
            if hold
            else (layer.start_frame + layer.end_frame - 1)
        ) // 2
        values = {
            "text": "参数验证" if layer.text != "参数验证" else "测试文字",
            "style_color": "#FF00FF"
            if layer.style.color.upper() != "#FF00FF"
            else "#00FF00",
            "style_font_size": 2
            if layer.style.font_size <= 1
            else layer.style.font_size * 0.7,
            # Prefer movement toward the center so an experiment does not introduce edge clipping.
            "layout_x": layer.layout.x + (0.1 if layer.layout.x <= 0.5 else -0.1),
            "layout_y": layer.layout.y + (0.1 if layer.layout.y <= 0.5 else -0.1),
        }
        for suffix, value in values.items():
            key = f"{index}_{suffix}"
            probes.append(
                {
                    "key": key,
                    "kind": suffix,
                    "value": value,
                    "previous": candidate.default_config[key],
                    "frame": frame,
                    "config": candidate.default_config | {key: value},
                }
            )
    return probes


def alpha_centroid(image: Image.Image, axis: int) -> float:
    """Measure the alpha-weighted center with a C-level projection, avoiding Python loops over full frames."""
    alpha = image.getchannel("A")
    size = (image.width, 1) if axis == 0 else (1, image.height)
    values = list(alpha.resize(size, Image.Resampling.BOX).get_flattened_data())
    mass = sum(values)
    return (
        sum(index * value for index, value in enumerate(values)) / mass if mass else 0
    )


def alpha_mass(image: Image.Image) -> int:
    """Measure visible alpha coverage for a controlled font-size change."""
    return sum(
        value * count for value, count in enumerate(image.getchannel("A").histogram())
    )


def color_mass(image: Image.Image, rgb: tuple[int, ...]) -> int:
    """Measure alpha-weighted target-color coverage, allowing bounded RGB rounding without hue substitution."""
    difference = ImageChops.difference(
        image.convert("RGB"), Image.new("RGB", image.size, rgb)
    )
    red, green, blue = difference.split()
    maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    mask = maximum.point(
        [255 if value <= MAX_CHANNEL_DELTA else 0 for value in range(256)]
    )
    alpha = ImageChops.multiply(mask, image.getchannel("A"))
    return sum(value * count for value, count in enumerate(alpha.histogram()))


def pixel_checks(
    directory: Path, spec: TemplateSpec, frames: list[int], probes: list[dict]
) -> list[Check]:
    """Check alpha, declared temporal change, and actual responses to controlled parameter patches."""
    checks = []
    originals = {}
    try:
        if not frames:
            raise ValueError("No sampled frames to verify")
        for frame in frames:
            with Image.open(directory / f"frame-{frame}.png") as image:
                if image.size != (spec.composition.width, spec.composition.height):
                    raise ValueError("PNG dimensions differ from requested canvas")
                originals[frame] = image.convert("RGBA")
        transparent = all(
            image.getchannel("A").getextrema()[0] == 0 for image in originals.values()
        )
        visible = any(image.getchannel("A").getbbox() for image in originals.values())
        checks.append(
            Check(
                name="transparency",
                status="pass" if transparent and visible else "fail",
                detail="Frames must retain transparent pixels and show visible content in at least one sample.",
            )
        )
        changing = [
            motion
            for layer in spec.text_layers
            for motion in layer.motion
            if motion.phase != "hold"
        ]
        missing = []
        insufficient = []
        for motion in changing:
            sample_start, sample_end = motion.start_frame, motion.end_frame
            if sample_end - sample_start == 1:
                # A one-frame cut has no internal pair; inspect its available adjacent boundaries.
                sample_start -= 1
                sample_end += 1
            samples = [
                image
                for frame, image in originals.items()
                if sample_start <= frame < sample_end
            ]
            if len(samples) < 2:
                insufficient.append(
                    f"{motion.phase}:{motion.start_frame}-{motion.end_frame}: "
                    "fewer than two available frames; temporal change cannot be verified. "
                    "Obtain boundary samples or clarify the timing; do not claim success from one still"
                )
            elif all(
                compare_images(samples[0], sample).equivalent for sample in samples[1:]
            ):
                missing.append(
                    f"{motion.phase}:{motion.start_frame}-{motion.end_frame}: "
                    "no visible change beyond raster tolerance; implement the declared frame-driven motion"
                )
        static = not changing and all(
            layer.start_frame == 0
            and layer.end_frame == spec.composition.duration_in_frames
            for layer in spec.text_layers
        )
        if static:
            first_frame = frames[0]
            for frame in frames[1:]:
                difference = compare_images(originals[first_frame], originals[frame])
                if not difference.equivalent:
                    missing.append(
                        f"static target changed across frames {first_frame}->{frame}: "
                        f"{difference.detail} Keep this full-duration static target unchanged; "
                        "remove unrequested frame/time-dependent changes, preserving its styling"
                    )
        checks.append(
            Check(
                name="motion_evidence",
                status="fail" if missing else "unknown" if insufficient else "pass",
                detail="Motion evidence mismatch: " + "; ".join(missing + insufficient)
                if missing or insufficient
                else "Sampled frames agree with declared static/change requirements; semantic timing is reviewed separately.",
            )
        )
        failures = []
        for index, probe in enumerate(probes):
            with Image.open(directory / f"probe-{index}.png") as image:
                changed = image.convert("RGBA")
            baseline = originals[probe["frame"]]
            difference = compare_images(baseline, changed)
            if difference.equivalent:
                failures.append(
                    f"{probe['key']}: no visible response beyond raster tolerance. "
                    f"{difference.detail} Connect this prop to the rendered content/style."
                )
            elif probe["kind"] in {"layout_x", "layout_y"}:
                axis = 0 if probe["kind"] == "layout_x" else 1
                observed = alpha_centroid(changed, axis) - alpha_centroid(
                    baseline, axis
                )
                if observed * (probe["value"] - probe["previous"]) <= 0:
                    failures.append(
                        f"{probe['key']}: visible content did not move in the requested direction"
                    )
            elif probe["kind"] == "style_font_size":
                coverage_delta = alpha_mass(changed) - alpha_mass(baseline)
                if coverage_delta * (probe["value"] - probe["previous"]) <= 0:
                    failures.append(
                        f"{probe['key']}: font size did not change visible coverage in the requested direction"
                    )
            elif probe["kind"] == "style_color":
                rgb = tuple(bytes.fromhex(probe["value"][1:]))
                before = color_mass(baseline, rgb)
                after = color_mass(changed, rgb)
                if after <= before:
                    failures.append(
                        f"{probe['key']}: requested color {probe['value']} coverage did not increase "
                        f"(alpha-weighted mass {before}->{after}, RGB tolerance {MAX_CHANNEL_DELTA}/255). "
                        "Check color prop forwarding; occlusion or color blending can make this experiment inconclusive."
                    )
        checks.append(
            Check(
                name="parameter_behavior",
                status="fail" if failures else "pass",
                detail="; ".join(failures)[:6000]
                if failures
                else f"Executed {len(probes)} controlled text/color/size/position render experiments.",
            )
        )
    except (OSError, ValueError, KeyError) as exc:
        checks.append(
            Check(name="pixel_evidence", status="fail", detail=str(exc)[:1000])
        )
    return checks
