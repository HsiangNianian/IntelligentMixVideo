"""Pixel-evidence regressions with synthetic transparent frames: uv run --locked pytest tests/test_remotion_template_probes.py."""

import pytest
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


def test_raster_noise_is_static_and_cannot_prove_motion_or_parameter_response(tmp_path):
    """稀疏的 1/255 栅格波动不应拒绝静态字效，也不能证明动画或参数生效。"""
    for frame in (0, 2, 5):
        rectangle(tmp_path / f"frame-{frame}.png")
    with Image.open(tmp_path / "frame-2.png") as original:
        noisy = original.convert("RGBA")
    noisy.putpixel((20, 20), (254, 255, 255, 255))
    noisy.save(tmp_path / "frame-2.png")
    noisy.save(tmp_path / "probe-0.png")
    assert verdicts(tmp_path)["motion_evidence"].status == "pass"
    assert verdicts(tmp_path, animated=True)["motion_evidence"].status == "fail"
    probe = {"key": "text", "kind": "text", "frame": 0}
    assert verdicts(tmp_path, probes=[probe])["parameter_behavior"].status == "fail"


@pytest.mark.parametrize(
    "color, expected",
    [("#FE00FE80", "pass"), ("#FF000080", "fail"), ("#FFFFFF80", "fail")],
)
def test_color_probe_allows_rounding_but_rejects_wrong_hue(tmp_path, color, expected):
    """半透明目标色允许通道取整误差，错误色相与未生效仍失败。"""
    for frame in (0, 2, 5):
        rectangle(tmp_path / f"frame-{frame}.png", color="#FFFFFF80")
    rectangle(tmp_path / "probe-0.png", color=color)
    probe = {
        "key": "color",
        "kind": "style_color",
        "previous": "#FFFFFF",
        "value": "#FF00FF",
        "frame": 2,
    }
    assert verdicts(tmp_path, probes=[probe])["parameter_behavior"].status == expected


@pytest.mark.parametrize(
    "phase, start, end, visible_frames",
    [
        ("enter", 2, 3, {2, 3, 4, 5}),
        ("exit", 2, 3, {0, 1, 2}),
        ("exit", 5, 6, {0, 1, 2, 3, 4}),
    ],
)
def test_one_frame_transition_checks_available_boundary(
    tmp_path, phase, start, end, visible_frames
):
    """一帧出现/消失使用相邻边界证据，不能要求区间内部存在两帧。"""
    spec = target()
    spec.text_layers[0].motion = [
        MotionSegment(
            phase=phase, start_frame=start, end_frame=end, description="瞬时转场"
        )
    ]
    for frame in range(6):
        if frame in visible_frames:
            rectangle(tmp_path / f"frame-{frame}.png")
        else:
            Image.new("RGBA", (64, 64)).save(tmp_path / f"frame-{frame}.png")
    checks = {c.name: c for c in pixel_checks(tmp_path, spec, list(range(6)), [])}
    assert checks["motion_evidence"].status == "pass", checks["motion_evidence"].detail
    for frame in range(6):
        rectangle(tmp_path / f"frame-{frame}.png")
    checks = {c.name: c for c in pixel_checks(tmp_path, spec, list(range(6)), [])}
    assert checks["motion_evidence"].status == "fail"


def test_single_frame_canvas_reports_missing_temporal_evidence(tmp_path):
    """整个视频只有一帧时无法核验运动，返回证据不足而非宣称动画错误或通过。"""
    spec = target()
    spec.composition.duration_in_frames = 1
    spec.text_layers[0].end_frame = 1
    spec.text_layers[0].motion = [
        MotionSegment(phase="enter", start_frame=0, end_frame=1, description="出现")
    ]
    rectangle(tmp_path / "frame-0.png")
    checks = {c.name: c for c in pixel_checks(tmp_path, spec, [0], [])}
    assert checks["motion_evidence"].status == "unknown"


@pytest.mark.parametrize("axis", ["x", "y"])
def test_edge_position_probe_moves_inward_and_verifies_actual_direction(tmp_path, axis):
    """靠边图层优先向内实验，避免探针主动裁切字效后误判整图重心方向。"""
    from server.remotion_templates.harness import controls
    from server.remotion_templates.models import TemplateCandidate
    from server.remotion_templates.probes import parameter_probes

    spec = target()
    setattr(spec.text_layers[0].layout, axis, 0.79)
    schema, defaults = controls(spec)
    candidate = TemplateCandidate(
        tsx_code="fixture", config_schema=schema, default_config=defaults
    )
    probe = next(
        p for p in parameter_probes(candidate, spec) if p["kind"] == f"layout_{axis}"
    )
    assert 0.5 < probe["value"] < probe["previous"]
    for frame in (0, 2, 5):
        image = Image.new("RGBA", (64, 64))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 30, 60), fill="white")
        draw.rectangle((38, 20, 63, 40), fill="white")
        if axis == "y":
            image = image.transpose(Image.Transpose.TRANSPOSE)
        image.save(tmp_path / f"frame-{frame}.png")
    for shift, expected in [(-6, "pass"), (0, "fail")]:
        image = Image.new("RGBA", (64, 64))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 30, 60), fill="white")
        draw.rectangle((38 + shift, 20, 63 + shift, 40), fill="white")
        if axis == "y":
            image = image.transpose(Image.Transpose.TRANSPOSE)
        image.save(tmp_path / "probe-0.png")
        probe["frame"] = 2
        assert (
            verdicts(tmp_path, probes=[probe])["parameter_behavior"].status == expected
        )


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
