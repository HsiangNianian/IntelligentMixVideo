"""Verify immutable Sprite publication and independent style binding behavior without cloud services."""

import hashlib
import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi import FastAPI
from fastapi.testclient import TestClient
from generated.imv.sprite.v1 import sprite_pb2 as pb
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from server.sprites import store
from server.sprites import router as sprite_router
from server.sprites.render import bound_config
from server.sprites import render as sprite_render
from server.remotion_templates.models import CompositionConfig, MotionSegment, TemplateSpec, TextLayer
from .remotion_legacy import controls
from server.remotion_templates.parameters import validate_candidate
from server.remotion_templates.models import TemplateCandidate
from server.remotion_templates.keywords import literal_ranges


@pytest.fixture
def sprite_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Use one in-memory MySQL-shaped SQLAlchemy store and a verified accepted source fixture."""
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    monkeypatch.setattr(store, "get_engine", lambda: engine)
    monkeypatch.setattr(store, "get_template", lambda _id: object())
    monkeypatch.setattr(store, "load_settings", lambda: SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(store, "_ready_engine", None)
    source_id = uuid4()
    accepted = tmp_path / "accepted" / str(source_id)
    accepted.mkdir(parents=True)
    preview = b"accepted-preview"
    (accepted / "preview.mp4").write_bytes(preview)
    candidate = SimpleNamespace(
        tsx_code="export default function Sprite(){ return null; }",
        default_config={"0_text": "示例", "0_style_font_size": 40},
        config_schema={
            "type": "object", "additionalProperties": False,
            "required": ["0_text", "0_style_font_size"],
            "properties": {
                "0_text": {"type": "string", "x-imv-target": "/text_layers/0/text"},
                "0_style_font_size": {"type": "number", "minimum": 12, "maximum": 300},
            },
        },
    )
    source = SimpleNamespace(
        candidate=candidate,
        spec=SimpleNamespace(
            text_layers=[object()], sprite_kind="text", name="已验收标题",
            composition=SimpleNamespace(width=1080, height=1920, fps=30, duration_in_frames=150),
        ),
        validation=SimpleNamespace(
            render_passed=True, checks=[SimpleNamespace(status="pass")],
            artifacts={"preview.mp4": hashlib.sha256(preview).hexdigest()},
        ),
    )
    monkeypatch.setattr(store, "RemotionStore", lambda _root: SimpleNamespace(root=tmp_path, version=lambda _id: source))
    monkeypatch.setattr(store, "verify_artifacts", lambda _candidate, _spec, _report, directory: (directory / "preview.mp4").read_bytes())
    yield source_id, tmp_path
    engine.dispose()


def test_published_sprite_survives_source_removal_and_repeated_publish(sprite_store, monkeypatch: pytest.MonkeyPatch):
    """A repeat publish keeps its ID; source chat removal cannot alter copied code or preview."""
    source_id, root = sprite_store
    request = pb.PublishSpriteRequest(source_version_id=str(source_id), kind=pb.SPRITE_KIND_TEXT, text_prop="0_text")
    first = store.publish(request)
    repeated = store.publish(request)
    assert repeated.sprite_id == first.sprite_id
    monkeypatch.setattr(store, "RemotionStore", lambda _root: (_ for _ in ()).throw(AssertionError("source chat was read")))
    sprite = store.get_sprite(UUID(first.sprite_id))
    assert sprite.tsx_code.startswith("export default")
    assert store.preview_path(UUID(first.sprite_id)).read_bytes() == b"accepted-preview"
    (root / "published_sprites" / f"{first.sprite_id}.mp4").write_bytes(b"changed")
    with pytest.raises(HTTPException) as error:
        store.preview_path(UUID(first.sprite_id))
    assert error.value.status_code == 409


def test_style_binding_revision_and_kind_validation(sprite_store):
    """Save a text placement once, reject stale edits and a subtitle without keyword capability."""
    source_id, _root = sprite_store
    sprite = store.publish(pb.PublishSpriteRequest(source_version_id=str(source_id), kind=pb.SPRITE_KIND_TEXT, text_prop="0_text"))
    style_id = str(uuid4())
    title = pb.SpritePlacement(id="title-a", sprite_id=sprite.sprite_id, target=pb.SPRITE_TARGET_TITLE, start_mode="seconds")
    saved = store.save_bindings(pb.SaveStyleSpritesRequest(style_id=style_id, placements=[title], expected_revision=0))
    assert saved.revision == 1
    assert store.get_bindings(UUID(style_id)).placements[0].sprite_id == sprite.sprite_id
    with pytest.raises(HTTPException) as stale:
        store.save_bindings(pb.SaveStyleSpritesRequest(style_id=style_id, placements=[title], expected_revision=0))
    assert stale.value.status_code == 409
    title.target = pb.SPRITE_TARGET_SUBTITLE
    with pytest.raises(HTTPException) as unsupported:
        store.save_bindings(pb.SaveStyleSpritesRequest(style_id=style_id, placements=[title], expected_revision=1))
    assert unsupported.value.status_code == 422


def test_style_overrides_use_published_schema(sprite_store):
    """Reject a numeric value outside the accepted style schema before persisting a placement."""
    source_id, _root = sprite_store
    sprite = store.publish(pb.PublishSpriteRequest(source_version_id=str(source_id), kind=pb.SPRITE_KIND_TEXT, text_prop="0_text"))
    placement = pb.SpritePlacement(id="title-a", sprite_id=sprite.sprite_id, target=pb.SPRITE_TARGET_TITLE, start_mode="seconds")
    placement.overrides.add(key="0_style_font_size", value=pb.ScalarValue(number_value=500))
    with pytest.raises(HTTPException) as invalid:
        store.save_bindings(pb.SaveStyleSpritesRequest(style_id=str(uuid4()), placements=[placement]))
    assert invalid.value.status_code == 422


def test_published_enum_choices_reach_editor_and_reject_invalid_override(sprite_store):
    """Publish the schema's discrete values so the editor can offer only accepted choices."""
    source_id, _root = sprite_store
    source = store.RemotionStore(None).version(source_id)
    source.candidate.default_config["0_style_font_weight"] = 700
    source.candidate.config_schema["required"].append("0_style_font_weight")
    source.candidate.config_schema["properties"]["0_style_font_weight"] = {
        "type": "number", "enum": [400, 700],
    }
    published = store.publish(pb.PublishSpriteRequest(source_version_id=str(source_id), kind=pb.SPRITE_KIND_TEXT, text_prop="0_text"))
    control = next(item for item in published.parameters if item.key == "0_style_font_weight")
    assert [item.number_value for item in control.allowed_values] == [400, 700]
    placement = pb.SpritePlacement(id="title-a", sprite_id=published.sprite_id, target=pb.SPRITE_TARGET_TITLE, start_mode="seconds")
    placement.overrides.add(key=control.key, value=pb.ScalarValue(number_value=500))
    with pytest.raises(HTTPException) as invalid:
        store.save_bindings(pb.SaveStyleSpritesRequest(style_id=str(uuid4()), placements=[placement]))
    assert invalid.value.status_code == 422


def test_new_sprite_publication_requires_30_fps(sprite_store):
    """An older accepted version remains readable but cannot become a new Sprite release."""
    source_id, _root = sprite_store
    source = store.RemotionStore(None).version(source_id)
    source.spec.composition.fps = 25
    with pytest.raises(HTTPException, match="30 FPS"):
        store.publish(pb.PublishSpriteRequest(source_version_id=str(source_id), kind=pb.SPRITE_KIND_TEXT, text_prop="0_text"))


def test_visual_sprite_keeps_kind_and_operator_style_separate(sprite_store):
    """A visual Agent release can bind to its own target while business text remains forbidden."""
    source_id, _root = sprite_store
    source = store.RemotionStore(None).version(source_id)
    source.spec.sprite_kind = "filter_overlay"
    source.spec.text_layers = []
    source.candidate.default_config = {"brightness": 0.4}
    source.candidate.config_schema = {
        "type": "object", "additionalProperties": False,
        "required": ["brightness"],
        "properties": {"brightness": {"type": "number", "minimum": 0, "maximum": 2}},
    }
    with pytest.raises(HTTPException) as wrong:
        store.publish(pb.PublishSpriteRequest(source_version_id=str(source_id), kind=pb.SPRITE_KIND_TEXT, text_prop="0_text"))
    assert wrong.value.status_code == 422
    summary = store.publish(pb.PublishSpriteRequest(source_version_id=str(source_id), kind=pb.SPRITE_KIND_FILTER_OVERLAY))
    sprite = store.get_sprite(UUID(summary.sprite_id))
    request = pb.SpriteRenderInput(sprite_id=summary.sprite_id, output=sprite.canvas)
    request.resolved_style.add(key="brightness", value=pb.ScalarValue(number_value=1.2))
    assert bound_config(sprite, request)["brightness"] == 1.2
    request.text = "不允许偷换业务文字"
    with pytest.raises(HTTPException) as invalid:
        bound_config(sprite, request)
    assert invalid.value.status_code == 422


def test_subtitle_publication_keeps_keyword_and_short_interval_fallback(sprite_store):
    """A checked repeated-keyword source records a visible static frame for short captions."""
    source_id, root = sprite_store
    source = store.RemotionStore(None).version(source_id)
    source.spec.sprite_kind = "subtitle"
    source.spec.text_layers = [SimpleNamespace(motion=[SimpleNamespace(phase="enter", start_frame=0, end_frame=8)])]
    source.validation.frames = [0, 8]
    source.validation.checks = [SimpleNamespace(name="parameter_behavior", status="pass")]
    for frame, alpha in ((0, 0), (8, 255)):
        Image.new("RGBA", (8, 8), (255, 255, 255, alpha)).save(root / "accepted" / str(source_id) / f"frame-{frame}.png")
    published = store.publish(pb.PublishSpriteRequest(
        source_version_id=str(source_id), kind=pb.SPRITE_KIND_TEXT,
        text_prop="0_text", keywords_prop="highlightRanges",
    ))
    sprite = store.get_sprite(UUID(published.sprite_id))
    assert sprite.animation_frames == 8
    assert sprite.static_frame == 8
    assert published.keywords_supported
    placement = pb.SpritePlacement(id="subtitle", sprite_id=published.sprite_id, target=pb.SPRITE_TARGET_SUBTITLE, start_mode="seconds")
    assert store.save_bindings(pb.SaveStyleSpritesRequest(style_id=str(uuid4()), placements=[placement])).revision == 1
    request = pb.SpriteRenderInput(sprite_id=published.sprite_id, text="花开花落", keywords=["花"])
    assert bound_config(sprite, request) == {"0_text": "花开花落", "0_style_font_size": 40, "highlightRanges": [[0, 1], [2, 3]]}


def test_literal_keyword_ranges_cover_repeats_overlaps_and_unicode():
    """Business matching is host-owned and uses code-point indices understood by Array.from(text)."""
    assert literal_ranges("花开花落", ["花"]) == [[0, 1], [2, 3]]
    assert literal_ranges("aaaa", ["aa"]) == [[0, 4]]
    assert literal_ranges("😀花😀花", ["花"]) == [[1, 2], [3, 4]]


def test_render_rejects_chunk_outside_logical_effect(sprite_store, monkeypatch):
    """A bus chunk cannot seek beyond the declared logical effect before launching a renderer."""
    source_id, _root = sprite_store
    summary = store.publish(pb.PublishSpriteRequest(source_version_id=str(source_id), kind=pb.SPRITE_KIND_TEXT, text_prop="0_text"))
    sprite = store.get_sprite(UUID(summary.sprite_id))
    monkeypatch.setattr(sprite_render, "get_sprite", lambda _id: sprite)
    request = pb.SpriteRenderInput(
        sprite_id=summary.sprite_id, output=sprite.canvas,
        effect_total_frames=20, effect_offset_frames=10, render_frame_count=30,
        text="标题",
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(sprite_render.render(request))
    assert error.value.status_code == 422


def test_render_windows_are_effect_relative_and_contiguous():
    """Chunk offsets preserve one logical animation without carrying an IMS timeline frame."""
    canvas = pb.SpriteCanvas(width=320, height=180, fps=30)
    single = pb.SpriteRenderInput(output=canvas, effect_total_frames=24, render_frame_count=24)
    assert sprite_render._render_window(single) == (24, 0, 24)
    first = pb.SpriteRenderInput(output=canvas, effect_total_frames=1200, render_frame_count=900)
    second = pb.SpriteRenderInput(output=canvas, effect_total_frames=1200, effect_offset_frames=900, render_frame_count=300)
    assert sprite_render._render_window(first) == (1200, 0, 900)
    assert sprite_render._render_window(second) == (1200, 900, 300)
    fields = pb.SpriteRenderInput.DESCRIPTOR.fields_by_name
    assert fields["effect_offset_frames"].number == 4
    assert fields["render_frame_count"].number == 5
    assert fields["effect_total_frames"].number == 9
    assert "timeline_in_frame" not in fields


def test_agent_spec_requires_effect_controls_and_keyword_examples():
    """Agent plan validation keeps filter controls, motion, and repeated-keyword evidence explicit."""
    canvas = CompositionConfig(width=320, height=180, fps=30, duration_in_frames=30)
    with pytest.raises(ValueError, match="brightness"):
        TemplateSpec(name="滤镜", description="暖色", composition=canvas, sprite_kind="filter_overlay", visual_parameters={"opacity": 0.5})
    filter_spec = TemplateSpec(name="滤镜", description="暖色", composition=canvas, sprite_kind="filter_overlay", visual_parameters={"brightness": 1.0, "contrast": 1.0, "saturation": 1.0})
    schema, defaults = controls(filter_spec)
    validate_candidate(TemplateCandidate(tsx_code="export default () => null", config_schema=schema, default_config=defaults), filter_spec)
    with pytest.raises(ValueError, match="motion"):
        TemplateSpec(name="动效", description="闪白", composition=canvas, sprite_kind="transition_overlay", visual_parameters={"intensity": 0.5})
    TemplateSpec(name="动效", description="闪白", composition=canvas, sprite_kind="transition_overlay", visual_parameters={"intensity": 0.5}, visual_motion=[MotionSegment(phase="enter", start_frame=0, end_frame=15, description="闪入")])
    with pytest.raises(ValueError, match="至少出现两次"):
        TemplateSpec(name="字幕", description="高亮花", composition=canvas, sprite_kind="subtitle", text_layers=[TextLayer(id="line", text="花开了", end_frame=30)], keyword_examples=["花"])


def test_subtitle_keyword_error_distinguishes_keywords_from_sample_sentences():
    """A rejected Agent plan explains how to replace full sample sentences with literal keywords."""
    canvas = CompositionConfig(width=320, height=180, fps=30, duration_in_frames=30)
    plan = {
        "name": "关键词字幕",
        "description": "重复词高亮",
        "composition": canvas,
        "sprite_kind": "subtitle",
        "text_layers": [TextLayer(id="line", text="这个方法简单，操作也很简单", end_frame=30)],
        "keyword_examples": ["这个方法简单，操作也很简单"],
    }
    with pytest.raises(ValueError, match="关键词本身.*不要填示例句子.*简单"):
        TemplateSpec(**plan)
    plan["keyword_examples"] = ["简单"]
    assert TemplateSpec(**plan).keyword_examples == ["简单"]


def test_interactive_preview_is_transparent_and_escapes_embedded_script(monkeypatch):
    """The browser receives a sandboxed transparent player without exposing raw source routes."""
    sprite_id = uuid4()
    monkeypatch.setattr(sprite_router.store, "get_sprite", lambda _id: pb.PublishedSprite(sprite_id=str(sprite_id)))

    async def script(_sprite):
        """Stand in for the separately tested sandbox bundle step."""
        return "console.log('</script>')"

    monkeypatch.setattr(sprite_router, "interactive_script", script)
    app = FastAPI()
    app.include_router(sprite_router.router)
    response = TestClient(app).get(f"/api/sprites/{sprite_id}/interactive")
    assert response.status_code == 200
    assert "background:transparent" in response.text
    assert "<\\/script>" in response.text
    assert "sandbox allow-scripts" in response.headers["content-security-policy"]
