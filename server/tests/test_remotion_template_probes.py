"""Pixel-evidence regressions with synthetic transparent frames: uv run --locked pytest tests/test_remotion_template_probes.py."""

from PIL import Image, ImageDraw
from server.remotion_templates.models import (
    CompositionConfig,
    MotionSegment,
    TemplateSpec,
    TextLayer,
)
from server.remotion_templates.probes import pixel_checks


def rectangle(path, *, box=(20, 20, 40, 40), color="#FFFFFF", background=(0, 0, 0, 0)):
    """Write known pixels so verdicts have an independent expected geometry and color."""
    image = Image.new("RGBA", (64, 64), background)
    ImageDraw.Draw(image).rectangle(box, fill=color)
    image.save(path)


def target(*, animated=False):
    """Use a full-duration layer; optional enter motion requires observed frame changes."""
    return TemplateSpec(
        name="fixture",
        description="fixture",
        composition=CompositionConfig(width=64, height=64, duration_in_frames=6),
        text_layers=[
            TextLayer(
                id="title",
                text="标题",
                end_frame=6,
                motion=[
                    MotionSegment(
                        phase="enter", start_frame=0, end_frame=3, description="淡入"
                    )
                ]
                if animated
                else [],
            )
        ],
    )


def verdicts(directory, *, animated=False, probes=None):
    """Read actual PNGs through the production verifier and index checks for assertions."""
    return {
        check.name: check
        for check in pixel_checks(
            directory, target(animated=animated), [0, 2, 5], probes or []
        )
    }


def test_declared_motion_needs_real_changes_and_static_needs_stability(tmp_path):
    """An enter-animation promise fails on identical frames; a static promise fails on changing frames."""
    for frame in (0, 2, 5):
        rectangle(tmp_path / f"frame-{frame}.png")
    assert verdicts(tmp_path)["motion_evidence"].status == "pass"
    assert verdicts(tmp_path, animated=True)["motion_evidence"].status == "fail"
    rectangle(tmp_path / "frame-0.png", color="#FFFFFF20")
    assert verdicts(tmp_path, animated=True)["motion_evidence"].status == "pass"
    assert verdicts(tmp_path)["motion_evidence"].status == "fail"


def test_opaque_and_blank_templates_do_not_pass_transparency(tmp_path):
    """An opaque background or all-transparent content cannot count as a usable transparent template."""
    for frame in (0, 2, 5):
        rectangle(tmp_path / f"frame-{frame}.png", background="black")
    assert verdicts(tmp_path)["transparency"].status == "fail"
    for frame in (0, 2, 5):
        Image.new("RGBA", (64, 64)).save(tmp_path / f"frame-{frame}.png")
    assert verdicts(tmp_path)["transparency"].status == "fail"


def test_parameter_probes_check_direction_size_and_requested_color(tmp_path):
    """Arbitrary pixel changes cannot satisfy incorrect coordinate, size or color behavior."""
    for frame in (0, 2, 5):
        rectangle(tmp_path / f"frame-{frame}.png")
    probes = [
        {"key": "x", "kind": "layout_x", "previous": 0.5, "value": 0.6, "frame": 2},
        {
            "key": "size",
            "kind": "style_font_size",
            "previous": 20,
            "value": 10,
            "frame": 2,
        },
        {
            "key": "color",
            "kind": "style_color",
            "previous": "#FFFFFF",
            "value": "#FF00FF",
            "frame": 2,
        },
    ]
    rectangle(tmp_path / "probe-0.png", box=(10, 20, 30, 40))
    rectangle(tmp_path / "probe-1.png", box=(10, 10, 50, 50))
    rectangle(tmp_path / "probe-2.png", color="#FF0000")
    bad = verdicts(tmp_path, probes=probes)["parameter_behavior"]
    assert bad.status == "fail"
    assert all(name in bad.detail for name in ("x:", "size:", "color:"))
    rectangle(tmp_path / "probe-0.png", box=(30, 20, 50, 40))
    rectangle(tmp_path / "probe-1.png", box=(25, 25, 35, 35))
    rectangle(tmp_path / "probe-2.png", color="#FF00FF")
    assert verdicts(tmp_path, probes=probes)["parameter_behavior"].status == "pass"


def test_missing_or_wrong_size_frames_never_create_passing_evidence(tmp_path):
    """Missing artifacts and wrong dimensions yield a failed evidence check, not a successful empty report."""
    assert verdicts(tmp_path)["pixel_evidence"].status == "fail"
    Image.new("RGBA", (32, 32)).save(tmp_path / "frame-0.png")
    assert "dimensions" in verdicts(tmp_path)["pixel_evidence"].detail


def test_full_duration_hold_requires_static_frames(tmp_path):
    """A hold-only static promise must reject actual movement just like an empty motion list."""
    spec = target()
    spec.text_layers[0].motion = [
        MotionSegment(
            phase="hold", start_frame=0, end_frame=6, description="全程静态显示"
        )
    ]
    for frame in (0, 2, 5):
        rectangle(tmp_path / f"frame-{frame}.png", box=(10 + frame, 20, 30 + frame, 40))
    checks = {
        check.name: check for check in pixel_checks(tmp_path, spec, [0, 2, 5], [])
    }
    assert checks["motion_evidence"].status == "fail"


def test_font_size_probe_changes_boundary_values_with_matching_direction(tmp_path):
    """Legal small font sizes still get distinct experiments, and larger output must count as growth."""
    from server.remotion_templates.models import TemplateCandidate
    from server.remotion_templates.probes import parameter_probes

    for size in (0.5, 1, 20, 600):
        spec = target()
        spec.text_layers[0].style.font_size = size
        candidate = TemplateCandidate(
            tsx_code="fixture",
            config_schema={},
            default_config={
                "0_text": "标题",
                "0_style_color": "#FFFFFF",
                "0_style_font_size": size,
                "0_layout_x": 0.5,
                "0_layout_y": 0.5,
            },
        )
        probe = next(
            item
            for item in parameter_probes(candidate, spec)
            if item["kind"] == "style_font_size"
        )
        assert probe["value"] != size and 0 < probe["value"] <= 600
        for frame in (0, 2, 5):
            rectangle(tmp_path / f"frame-{frame}.png")
        box = (10, 10, 50, 50) if probe["value"] > size else (25, 25, 35, 35)
        rectangle(tmp_path / "probe-0.png", box=box)
        result = {
            check.name: check
            for check in pixel_checks(tmp_path, spec, [0, 2, 5], [probe])
        }
        assert result["parameter_behavior"].status == "pass", result
