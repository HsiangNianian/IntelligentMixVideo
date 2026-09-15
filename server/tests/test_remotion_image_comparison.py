"""离线验证一致性容差、可见变化和诊断：uv run --locked pytest tests/test_remotion_image_comparison.py。"""

import pytest
from PIL import Image, ImageDraw, PngImagePlugin
from server.remotion_templates.image_comparison import (
    compare_images,
    consistency_checks,
)
from server.remotion_templates.models import CompositionConfig, TemplateSpec, TextLayer


def title_frame():
    """用 100 个不透明像素模拟小字效，避免空白画布稀释差异。"""
    image = Image.new("RGBA", (64, 64))
    ImageDraw.Draw(image).rectangle((20, 20, 29, 29), fill=(100, 100, 100, 255))
    return image


@pytest.mark.parametrize(
    "delta, count, expected",
    [
        (0, 0, True),
        (1, 1, True),
        (2, 1, True),
        (3, 1, False),
        (1, 2, False),
        (1, 100, False),
    ],
)
def test_noise_requires_both_small_amplitude_and_sparse_foreground(
    delta, count, expected
):
    """容差边界包含 2/255 与前景 1%；单个明显变化或大片微弱变化都必须拒绝。"""
    first = title_frame()
    second = first.copy()
    for i in range(count):
        second.putpixel((20 + i % 10, 20 + i // 10), (100 + delta, 100, 100, 255))
    difference = compare_images(first, second)
    assert difference.equivalent is expected
    assert difference.changed_pixels == count
    assert difference.visible_pixels == 100


@pytest.mark.parametrize("change", ["shift", "color", "alpha", "missing", "new_pixel"])
def test_real_changes_cannot_hide_in_transparent_canvas(change):
    """位移、颜色、透明度、内容消失及孤立亮点变化均保持失败。"""
    first = title_frame()
    second = first.copy()
    if change == "shift":
        second = Image.new("RGBA", first.size)
        second.paste(first, (1, 0))
    elif change == "color":
        ImageDraw.Draw(second).rectangle((20, 20, 29, 29), fill="red")
    elif change == "alpha":
        ImageDraw.Draw(second).rectangle((20, 20, 29, 29), fill=(100, 100, 100, 128))
    elif change == "missing":
        second = Image.new("RGBA", first.size)
    else:
        second.putpixel((60, 60), (255, 255, 255, 255))
    assert not compare_images(first, second).equivalent


def test_transparent_rgb_and_low_alpha_rounding_use_visible_composites():
    """忽略全透明 RGB；低透明度颜色差按实际合成结果计算，不按原始 RGB 放大。"""
    first = title_frame()
    second = first.copy()
    second.putpixel((0, 0), (255, 100, 40, 0))
    assert compare_images(first, second).changed_pixels == 0
    first.putpixel((20, 20), (100, 100, 100, 1))
    second.putpixel((20, 20), (121, 121, 100, 1))
    assert compare_images(first, second).equivalent
    assert compare_images(
        Image.new("RGBA", (64, 64)), Image.new("RGBA", (64, 64), (255, 255, 255, 0))
    ).equivalent


@pytest.mark.parametrize("color", ["black", "white"])
def test_both_backgrounds_expose_opacity_change(color):
    """纯黑或纯白前景的透明度变化不能被同色背景掩盖。"""
    first = Image.new("RGBA", (64, 64), color)
    second = first.copy()
    second.putalpha(128)
    assert not compare_images(first, second).equivalent


@pytest.fixture
def captures(tmp_path):
    """创建重复帧及默认导出证据，使用不同 PNG 元数据模拟字节差异。"""
    spec = TemplateSpec(
        name="test",
        description="static",
        composition=CompositionConfig(width=64, height=64, duration_in_frames=6),
        text_layers=[TextLayer(id="title", text="标题", end_frame=6)],
    )
    frame = title_frame()
    for name in ("frame-0.png", "frame-2.png", "repeat.png", "export-default.png"):
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("capture", name)
        frame.save(tmp_path / name, pnginfo=metadata)
    return tmp_path, spec


def test_capture_checks_ignore_encoding_and_supply_measurements(captures):
    """相同可见画面允许 PNG 元数据不同，验收仍记录帧号、区域与阈值。"""
    directory, spec = captures
    assert (directory / "frame-2.png").read_bytes() != (
        directory / "repeat.png"
    ).read_bytes()
    checks = consistency_checks(directory, spec, [0, 2, 5])
    assert {check.name for check in checks} == {"determinism", "export_defaults"}
    assert all(check.status == "pass" for check in checks)
    assert checks[0].frame == 2 and "bbox=None" in checks[0].detail


@pytest.mark.parametrize(
    "name, filename, guidance",
    [
        ("determinism", "repeat.png", "Remotion frame"),
        ("export_defaults", "export-default.png", "default_config"),
    ],
)
def test_failed_capture_has_measured_actionable_steer(
    captures, name, filename, guidance
):
    """真实差异附带帧号、文件、幅度、区域、阈值与对应修复方向。"""
    directory, spec = captures
    changed = title_frame()
    changed.putpixel((20, 20), (255, 255, 255, 255))
    changed.save(directory / filename)
    check = next(
        c for c in consistency_checks(directory, spec, [0, 2, 5]) if c.name == name
    )
    assert check.status == "fail"
    assert all(
        text in check.detail
        for text in [
            filename,
            "1/100",
            "155/255",
            "bbox=(20, 20, 21, 21)",
            "tolerance",
            guidance,
        ]
    )


@pytest.mark.parametrize(
    "kind", ["missing", "corrupt", "wrong_size", "both_wrong_size"]
)
def test_invalid_capture_evidence_never_passes(captures, kind):
    """缺失、损坏、单张或两张尺寸错误都失败；两个错误尺寸相同也不能绕过画布校验。"""
    directory, spec = captures
    path = directory / "repeat.png"
    if kind == "missing":
        path.unlink()
    elif kind == "corrupt":
        path.write_bytes(b"not a PNG")
    else:
        Image.new("RGBA", (32, 32)).save(path)
        if kind == "both_wrong_size":
            Image.new("RGBA", (32, 32)).save(directory / "frame-2.png")
    check = consistency_checks(directory, spec, [0, 2, 5])[0]
    assert check.status == "fail" and "missing or invalid" in check.detail


def test_empty_frames_and_dimension_mismatch_fail(captures):
    """无采样帧不能通过；直接比较不同尺寸时必须明确拒绝。"""
    directory, spec = captures
    assert consistency_checks(directory, spec, [])[0].status == "fail"
    with pytest.raises(ValueError, match="dimensions"):
        compare_images(title_frame(), Image.new("RGBA", (32, 32)))
