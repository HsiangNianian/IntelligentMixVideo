"""Persist immutable Sprite releases and independently versioned style bindings in MySQL."""

import hashlib
import json
from math import isfinite
from pathlib import Path
import shutil
from datetime import UTC, datetime
from threading import Lock
from uuid import UUID, uuid4

from fastapi import HTTPException
from generated.imv.sprite.v1 import sprite_pb2 as pb
from google.protobuf.json_format import MessageToDict, ParseDict
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from PIL import Image
from sqlalchemy import JSON, Column, DateTime, Integer, MetaData, String, Table, select
from sqlalchemy.exc import IntegrityError

from ..database import get_engine
from ..template.store import get_template
from ..remotion_templates.evidence import verify_artifacts
from ..remotion_templates.evidence import digest
from ..remotion_templates.settings import load_settings
from ..remotion_templates.store import Store as RemotionStore


metadata = MetaData()
sprites = Table(
    "published_sprites", metadata,
    Column("sprite_id", String(36), primary_key=True),
    Column("publication_key", String(64), nullable=False, unique=True),
    Column("kind", Integer, nullable=False),
    Column("data", JSON, nullable=False),
    Column("created_at", DateTime, nullable=False),
)
bindings = Table(
    "style_sprite_bindings", metadata,
    Column("style_id", String(36), primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("data", JSON, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
_schema_lock = Lock()
_ready_engine = None


def initialize_schema():
    """Create only the two Sprite tables on first use of this module."""
    global _ready_engine
    engine = get_engine()
    with _schema_lock:
        if _ready_engine is not engine:
            metadata.create_all(engine)
            _ready_engine = engine
    return engine


def _message(value: dict, model):
    """Restore a validated protobuf record from its JSON-compatible database snapshot."""
    return ParseDict(value, model())


def summary(sprite: pb.PublishedSprite) -> pb.SpriteSummary:
    """Return authoring metadata without copying executable source into catalog responses."""
    return pb.SpriteSummary(
        sprite_id=sprite.sprite_id, source_version_id=sprite.source_version_id,
        name=sprite.name, kind=sprite.kind, canvas=sprite.canvas,
        parameters=sprite.parameters,
        preview_url=f"/api/sprites/{sprite.sprite_id}/preview.mp4",
        keywords_supported=bool(sprite.keywords_prop),
    )


def _parameter(key: str, definition: dict, value) -> pb.SpriteParameter:
    """Expose an accepted scalar control's type, bounds and discrete choices."""
    def scalar_value(item):
        """Encode a schema scalar without collapsing bool into a number."""
        scalar = pb.ScalarValue()
        if isinstance(item, bool):
            scalar.bool_value = item
        elif isinstance(item, str):
            scalar.string_value = item
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            scalar.number_value = item
        else:
            raise ValueError("Sprite 参数只能是标量")
        return scalar

    parameter = pb.SpriteParameter(
        key=key, label=definition.get("title") or key,
        access=pb.OPERATOR_ACCESS_VISIBLE_READ_ONLY
        if key.endswith("font_family") else pb.OPERATOR_ACCESS_VISIBLE_EDITABLE,
        default_value=scalar_value(value),
    )
    parameter.allowed_values.extend(scalar_value(item) for item in definition.get("enum", []))
    if "minimum" in definition:
        parameter.minimum = definition["minimum"]
    if "maximum" in definition:
        parameter.maximum = definition["maximum"]
    return parameter


def publish(request: pb.PublishSpriteRequest) -> pb.SpriteSummary:
    """Copy one accepted, integrity-checked Agent version into an immutable cloud release."""
    try:
        version_id = UUID(request.source_version_id)
    except ValueError as exc:
        raise HTTPException(422, "源版本 ID 无效") from exc
    source_store = RemotionStore(load_settings().data_dir)
    source = source_store.version(version_id)
    directory = source_store.root / "accepted" / str(version_id)
    if (
        not source.validation.render_passed
        or any(check.status != "pass" for check in source.validation.checks)
        or "preview.mp4" not in source.validation.artifacts
    ):
        raise HTTPException(409, "源版本没有完整的通过验收证据")
    try:
        verify_artifacts(source.candidate, source.spec, source.validation, directory)
    except (OSError, ValueError) as exc:
        raise HTTPException(409, "源版本产物不可用或已变化") from exc
    controls = source.candidate.config_schema.get("properties", {})
    kinds = {
        "text": pb.SPRITE_KIND_TEXT,
        "subtitle": pb.SPRITE_KIND_TEXT,
        "filter_overlay": pb.SPRITE_KIND_FILTER_OVERLAY,
        "video_overlay": pb.SPRITE_KIND_VIDEO_OVERLAY,
        "transition_overlay": pb.SPRITE_KIND_TRANSITION_OVERLAY,
    }
    if request.kind == pb.SPRITE_KIND_UNSPECIFIED or request.kind != kinds.get(source.spec.sprite_kind):
        raise HTTPException(422, "发布类型必须与 Agent 验收类型一致")
    text_prop = request.text_prop
    if request.kind == pb.SPRITE_KIND_TEXT:
        if len(source.spec.text_layers) != 1 or text_prop not in controls or controls[text_prop].get("x-imv-target") != "/text_layers/0/text":
            raise HTTPException(422, "文字 Sprite 必须指定唯一业务文字参数")
        if source.spec.sprite_kind == "subtitle":
            if request.keywords_prop != "highlightRanges" or not any(check.name == "parameter_behavior" and check.status == "pass" for check in source.validation.checks):
                raise HTTPException(422, "字幕 Sprite 缺少关键词参数验收")
        elif request.keywords_prop:
            raise HTTPException(422, "标题 Sprite 不接收关键词")
    elif text_prop or request.keywords_prop:
        raise HTTPException(422, "视觉 Sprite 不接收业务文字或关键词参数")
    parameters = [
        _parameter(key, definition, source.candidate.default_config[key])
        for key, definition in controls.items() if key != text_prop
    ]
    composition = source.spec.composition
    if composition.fps != 30:
        raise HTTPException(422, "新发布的 Sprite 帧率须为 30 FPS")
    animation_frames = 0
    static_frame = 0
    if source.spec.sprite_kind == "subtitle":
        animation_frames = sum(
            motion.end_frame - motion.start_frame
            for motion in source.spec.text_layers[0].motion if motion.phase != "hold"
        )
        samples = []
        for frame in source.validation.frames:
            with Image.open(directory / f"frame-{frame}.png") as image:
                samples.append((sum(value * count for value, count in enumerate(image.getchannel("A").histogram())), frame))
        if not samples or max(samples)[0] == 0:
            raise HTTPException(422, "字幕 Sprite 没有可冻结的可见帧")
        static_frame = max(samples)[1]
    code = source.candidate.tsx_code
    publication_key = hashlib.sha256(
        f"{version_id}:{request.kind}:{text_prop}:{request.keywords_prop}:{hashlib.sha256(code.encode()).hexdigest()}".encode()
    ).hexdigest()
    now = datetime.now(UTC)
    sprite = pb.PublishedSprite(
        sprite_id=str(uuid4()), source_version_id=str(version_id),
        name=source.spec.name, kind=request.kind,
        canvas=pb.SpriteCanvas(
            width=composition.width, height=composition.height,
            fps=int(composition.fps), preview_frames=composition.duration_in_frames,
        ),
        tsx_code=code, code_sha256=hashlib.sha256(code.encode()).hexdigest(),
        parameters=parameters, text_prop=text_prop, keywords_prop=request.keywords_prop,
        preview_sha256=source.validation.artifacts["preview.mp4"],
        config_schema_json=json.dumps(source.candidate.config_schema, ensure_ascii=False),
        default_config_json=json.dumps(source.candidate.default_config, ensure_ascii=False),
        animation_frames=animation_frames, static_frame=static_frame,
    )
    sprite.published_at.FromDatetime(now)
    data = MessageToDict(sprite, preserving_proto_field_name=True)
    preview = source_store.root / "published_sprites" / f"{sprite.sprite_id}.mp4"
    preview.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(directory / "preview.mp4", preview)
    if digest(preview) != sprite.preview_sha256:
        preview.unlink(missing_ok=True)
        raise HTTPException(409, "Sprite 预览复制后校验失败")
    try:
        with initialize_schema().begin() as connection:
            row = connection.execute(select(sprites.c.data).where(sprites.c.publication_key == publication_key)).first()
            if row is not None:
                preview.unlink(missing_ok=True)
                return summary(_message(row.data, pb.PublishedSprite))
            connection.execute(sprites.insert().values(
                sprite_id=sprite.sprite_id, publication_key=publication_key,
                kind=sprite.kind, data=data, created_at=now.replace(tzinfo=None),
            ))
    except IntegrityError:
        preview.unlink(missing_ok=True)
        with initialize_schema().connect() as connection:
            row = connection.execute(select(sprites.c.data).where(sprites.c.publication_key == publication_key)).one()
            return summary(_message(row.data, pb.PublishedSprite))
    except BaseException:
        preview.unlink(missing_ok=True)
        raise
    return summary(sprite)


def preview_path(sprite_id: UUID) -> Path:
    """Serve only the retained preview whose bytes still match the immutable release."""
    sprite = get_sprite(sprite_id)
    path = load_settings().data_dir / "published_sprites" / f"{sprite_id}.mp4"
    try:
        if digest(path) != sprite.preview_sha256:
            raise ValueError("preview digest differs")
    except (OSError, ValueError) as exc:
        raise HTTPException(409, "Sprite 预览不可用或已变化") from exc
    return path


def get_sprite(sprite_id: UUID) -> pb.PublishedSprite:
    """Resolve published source for server-side rendering without reading mutable Agent works."""
    with initialize_schema().connect() as connection:
        row = connection.execute(select(sprites.c.data).where(sprites.c.sprite_id == str(sprite_id))).first()
    if row is None:
        raise HTTPException(404, "Sprite 不存在")
    sprite = _message(row.data, pb.PublishedSprite)
    if hashlib.sha256(sprite.tsx_code.encode()).hexdigest() != sprite.code_sha256:
        raise HTTPException(409, "Sprite 代码完整性校验失败")
    return sprite


def list_sprites() -> list[pb.SpriteSummary]:
    """List immutable releases in publication order without exposing TSX source."""
    with initialize_schema().connect() as connection:
        rows = connection.execute(select(sprites.c.data).order_by(sprites.c.created_at, sprites.c.sprite_id)).all()
    return [summary(_message(row.data, pb.PublishedSprite)) for row in rows]


def get_bindings(style_id: UUID) -> pb.StyleSpriteBindings:
    """Return an empty revision-zero set for a cloud template without Sprite placements."""
    get_template(style_id)
    with initialize_schema().connect() as connection:
        row = connection.execute(select(bindings.c.data).where(bindings.c.style_id == str(style_id))).first()
    return _message(row.data, pb.StyleSpriteBindings) if row else pb.StyleSpriteBindings(style_id=str(style_id))


def _validate_placement(item: pb.SpritePlacement, known: dict[str, pb.PublishedSprite]) -> None:
    """Check a placement against its immutable Sprite kind and editable scalar contract."""
    try:
        sprite_id = UUID(item.sprite_id)
    except ValueError as exc:
        raise HTTPException(422, "Sprite 引用 ID 无效") from exc
    if str(sprite_id) not in known:
        known[str(sprite_id)] = get_sprite(sprite_id)
    sprite = known[str(sprite_id)]
    allowed = {
        pb.SPRITE_KIND_TEXT: {pb.SPRITE_TARGET_TITLE, pb.SPRITE_TARGET_SUBTITLE},
        pb.SPRITE_KIND_FILTER_OVERLAY: {pb.SPRITE_TARGET_FILTER},
        pb.SPRITE_KIND_VIDEO_OVERLAY: {pb.SPRITE_TARGET_VIDEO_EFFECT, pb.SPRITE_TARGET_VIDEO_ENTER, pb.SPRITE_TARGET_VIDEO_EXIT},
        pb.SPRITE_KIND_TRANSITION_OVERLAY: {pb.SPRITE_TARGET_TRANSITION},
    }
    if item.target not in allowed.get(sprite.kind, set()):
        raise HTTPException(422, "Sprite 类型与作用对象不匹配")
    if item.target == pb.SPRITE_TARGET_SUBTITLE and not sprite.keywords_prop:
        raise HTTPException(422, "字幕 Sprite 尚未通过关键词高亮验收")
    boundary_targets = {
        pb.SPRITE_TARGET_TRANSITION,
        pb.SPRITE_TARGET_VIDEO_ENTER,
        pb.SPRITE_TARGET_VIDEO_EXIT,
    }
    if item.target in boundary_targets and not item.HasField("duration"):
        raise HTTPException(422, "素材边界 Sprite 必须指定持续时间")
    if item.start_mode not in {"seconds", "percent"} or not 0 <= item.start < (100 if item.start_mode == "percent" else float("inf")):
        raise HTTPException(422, "Sprite 开始时间无效")
    if item.HasField("duration") and not 0 < item.duration <= 3600:
        raise HTTPException(422, "Sprite 持续时间无效")
    controls = {control.key: control for control in sprite.parameters}
    resolved = json.loads(sprite.default_config_json)
    used = set()
    for override in item.overrides:
        control = controls.get(override.key)
        if override.key in used or control is None or control.access != pb.OPERATOR_ACCESS_VISIBLE_EDITABLE or not override.HasField("value"):
            raise HTTPException(422, "Sprite 参数覆盖字段无效")
        used.add(override.key)
        value_type = override.value.WhichOneof("value")
        if value_type != control.default_value.WhichOneof("value"):
            raise HTTPException(422, "Sprite 参数类型与发布版本不一致")
        if value_type == "number_value" and (
            not isfinite(override.value.number_value)
            or (control.HasField("minimum") and override.value.number_value < control.minimum)
            or (control.HasField("maximum") and override.value.number_value > control.maximum)
        ):
            raise HTTPException(422, "Sprite 参数超出允许范围")
        resolved[override.key] = getattr(override.value, value_type)
    try:
        Draft202012Validator(json.loads(sprite.config_schema_json)).validate(resolved)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(422, "Sprite 参数不满足发布版本的配置约束") from exc


def save_bindings(request: pb.SaveStyleSpritesRequest) -> pb.StyleSpriteBindings:
    """Replace one style's Sprite placements with optimistic revision checking."""
    try:
        style_id = UUID(request.style_id)
    except ValueError as exc:
        raise HTTPException(422, "style_id 无效") from exc
    get_template(style_id)
    if len(request.placements) > 100:
        raise HTTPException(422, "Sprite 轨道不能超过 100 条")
    ids = [item.id for item in request.placements]
    if any(not item.id or len(item.id) > 100 for item in request.placements) or len(ids) != len(set(ids)):
        raise HTTPException(422, "Sprite 轨道 ID 缺失或重复")
    if [item.order for item in request.placements] != list(range(len(request.placements))):
        raise HTTPException(422, "Sprite 轨道层级顺序无效")
    known = {}
    for item in request.placements:
        _validate_placement(item, known)
    now = datetime.now(UTC)
    try:
        with initialize_schema().begin() as connection:
            row = connection.execute(select(bindings).where(bindings.c.style_id == str(style_id)).with_for_update()).first()
            revision = row.revision if row else 0
            if revision != request.expected_revision:
                raise HTTPException(409, "Sprite 绑定已被其他编辑更新，请重新读取")
            result = pb.StyleSpriteBindings(style_id=str(style_id), placements=request.placements, revision=revision + 1)
            result.updated_at.FromDatetime(now)
            data = MessageToDict(result, preserving_proto_field_name=True)
            if row:
                connection.execute(bindings.update().where(bindings.c.style_id == str(style_id)).values(
                    revision=result.revision, data=data, updated_at=now.replace(tzinfo=None),
                ))
            else:
                connection.execute(bindings.insert().values(
                    style_id=str(style_id), revision=result.revision, data=data,
                    updated_at=now.replace(tzinfo=None),
                ))
    except IntegrityError as exc:
        raise HTTPException(409, "Sprite 绑定已被其他编辑更新，请重新读取") from exc
    return result
