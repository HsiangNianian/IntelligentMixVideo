"""模型证据选择回归；pytest tests/test_remotion_frame_selection.py，不减少宿主保存的事实帧。"""

from server.remotion_templates.models import (
    CompositionConfig,
    MotionSegment,
    TemplateSpec,
    TextLayer,
)
from server.remotion_templates.visual_evidence import select_frames


def test_selection_covers_hold_and_boundaries_without_mutating_evidence():
    """长时间轴选择代表帧，保留稳定展示与动效边界，原始帧清单保持完整。"""
    spec = TemplateSpec(
        name="标题",
        description="淡入淡出",
        composition=CompositionConfig(duration_in_frames=150),
        text_layers=[
            TextLayer(
                id="title",
                text="标题",
                end_frame=150,
                motion=[
                    MotionSegment(
                        phase="enter", start_frame=0, end_frame=20, description="淡入"
                    ),
                    MotionSegment(
                        phase="hold", start_frame=20, end_frame=120, description="保持"
                    ),
                    MotionSegment(
                        phase="exit", start_frame=120, end_frame=150, description="淡出"
                    ),
                ],
            )
        ],
    )
    frames = list(range(150))
    selected = select_frames(spec, frames)
    assert len(selected) <= 12
    assert {0, 19, 69, 120, 149} <= set(selected)
    assert selected == sorted(set(selected))
    assert frames == list(range(150))


def test_failure_frames_and_supplemental_requests_are_never_dropped():
    """失败帧优先于普通代表帧，补证据明确要求的帧即使超过软上限也不能静默丢失。"""
    spec = TemplateSpec(
        name="标题",
        description="静态",
        composition=CompositionConfig(duration_in_frames=150),
        text_layers=[TextLayer(id="title", text="标题", end_frame=150)],
    )
    frames = list(range(100))
    selected = select_frames(spec, frames, preferred=[42, 43], limit=6)
    assert {42, 43} <= set(selected) and len(selected) <= 6
    assert set(select_frames(spec, frames, preferred=frames[:15], limit=12)) >= set(
        frames[:15]
    )
    assert select_frames(spec, [], preferred=[5]) == []


def test_repair_selection_prioritizes_cited_neighbors():
    """多个文字层也不能挤掉失败帧附近的观察；这些帧比普通概览更适合修复。"""
    spec = TemplateSpec(
        name="多层",
        description="静态",
        composition=CompositionConfig(duration_in_frames=150),
        text_layers=[
            TextLayer(id=f"line_{i}", text="标题", start_frame=i * 10, end_frame=150)
            for i in range(5)
        ],
    )
    frames = list(range(150))
    assert {73, 74, 75} <= set(select_frames(spec, frames, preferred=[74], limit=6))
