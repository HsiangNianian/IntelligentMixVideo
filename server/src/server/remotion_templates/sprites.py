"""Publish accepted Remotion versions as immutable assets for the independent Remotion library.

A publication copies the sealed source, parameter contract and interactive player bundle out of the
chat-owned version directory, so deleting or editing the source work never changes a bound Sprite.
Records live in the Remotion SQLite database as `imv.sprite.v1.PublishedSprite` JSON; reads never mutate a publication. HTTP framing lives in `sprite_router.py`.
"""

import hashlib
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException
from generated.imv.sprite.v1 import sprite_pb2 as pb
from google.protobuf.json_format import MessageToDict, ParseDict

from .evidence import digest, verify_artifacts
from .store import Conflict, NotFound, Store

PREVIEW_BUNDLE = "interactive.js"

# 版本创建时的内容类型与发布类型的对应；composition 版本不固定类型，由发布请求选择。
_VERSION_KINDS = {
    "text": pb.SPRITE_KIND_TEXT,
    "subtitle": pb.SPRITE_KIND_TEXT,
    "filter_overlay": pb.SPRITE_KIND_FILTER_OVERLAY,
    "video_overlay": pb.SPRITE_KIND_VIDEO_OVERLAY,
    "transition_overlay": pb.SPRITE_KIND_TRANSITION_OVERLAY,
}
# 发布内容类型独立于项目中的 IMS 效果目标。
_PUBLISH_KINDS = {
    pb.SPRITE_KIND_TEXT, pb.SPRITE_KIND_FILTER_OVERLAY,
    pb.SPRITE_KIND_VIDEO_OVERLAY, pb.SPRITE_KIND_TRANSITION_OVERLAY,
}


def _invalid(message: str) -> HTTPException:
    """Report a request that is well-formed protobuf but violates the Sprite contract."""
    return HTTPException(422, message)


def _scalar(kind: str, value) -> pb.ScalarValue | None:
    """Wrap a JSON value only when it has exactly the type its schema declares."""
    if kind == "string" and isinstance(value, str):
        return pb.ScalarValue(string_value=value)
    if kind in {"number", "integer"} and isinstance(value, (int, float)) and not isinstance(value, bool):
        return pb.ScalarValue(number_value=float(value))
    if kind == "boolean" and isinstance(value, bool):
        return pb.ScalarValue(bool_value=value)
    return None


def _nodes(schema: dict, prefix: str = ""):
    """Yield (dot path, definition) for every declared property, descending through nested objects.

    Property names containing a dot cannot be addressed unambiguously and are skipped.
    """
    for name, definition in schema.get("properties", {}).items():
        if "." in name or not isinstance(definition, dict):
            continue
        path = f"{prefix}{name}"
        yield path, definition
        if definition.get("type") == "object":
            yield from _nodes(definition, f"{path}.")


def _definition(schema: dict, path: str) -> dict | None:
    """Resolve one dot path to its schema definition, or None when it is not declared."""
    return next((definition for found, definition in _nodes(schema) if found == path), None)


def _lookup(values: dict, path: str):
    """Read a dot path from nested configuration; a missing step yields None."""
    for part in path.split("."):
        if not isinstance(values, dict) or part not in values:
            return None
        values = values[part]
    return values


def _parameters(schema: dict, defaults: dict, reserved: set[str]) -> list[pb.SpriteParameter]:
    """Expose scalar controls at any depth as editable parameters keyed by dot path.

    Bus-owned fields (text, keywords) and structured values are skipped.
    """
    result = []
    for key, definition in _nodes(schema):
        if key in reserved:
            continue
        kind = definition.get("type")
        default = _scalar(kind, _lookup(defaults, key))
        if default is None:
            continue
        parameter = pb.SpriteParameter(
            key=key,
            label=str(definition.get("title") or key)[:100],
            access=pb.OPERATOR_ACCESS_VISIBLE_EDITABLE,
            default_value=default,
        )
        if kind in {"number", "integer"}:
            for bound in ("minimum", "maximum"):
                if isinstance(definition.get(bound), (int, float)) and not isinstance(definition[bound], bool):
                    setattr(parameter, bound, float(definition[bound]))
        for item in definition.get("enum", []):
            allowed = _scalar(kind, item)
            if allowed is not None:
                parameter.allowed_values.append(allowed)
        result.append(parameter)
    return result


def _sprite_from(row: sqlite3.Row) -> pb.PublishedSprite:
    """Restore a stored publication and refuse source bytes that no longer match their hash."""
    sprite = ParseDict(json.loads(row["data"]), pb.PublishedSprite())
    if hashlib.sha256(sprite.tsx_code.encode()).hexdigest() != sprite.code_sha256:
        raise Conflict("Published Sprite failed integrity verification.")
    return sprite


def get(store: Store, sprite_id: str) -> pb.PublishedSprite:
    """Read one publication by ID; unknown or malformed IDs are a 404."""
    try:
        identifier = str(UUID(sprite_id))
    except ValueError:
        raise NotFound("sprite not found") from None
    with store.connection() as db:
        row = db.execute("SELECT data FROM sprites WHERE id=?", (identifier,)).fetchone()
    if row is None:
        raise NotFound("sprite not found")
    return _sprite_from(row)


def summarize(sprite: pb.PublishedSprite) -> pb.SpriteSummary:
    """Project a publication onto the public catalog entry, never including TSX."""
    return pb.SpriteSummary(
        sprite_id=sprite.sprite_id,
        name=sprite.name,
        kind=sprite.kind,
        canvas=sprite.canvas,
        parameters=sprite.parameters,
        source_version_id=sprite.source_version_id,
        preview_url=f"/api/sprites/{sprite.sprite_id}/preview",
        keywords_supported=bool(sprite.keywords_prop),
    )


def catalog(store: Store) -> list[pb.SpriteSummary]:
    """List publications newest first."""
    with store.connection() as db:
        rows = db.execute("SELECT data FROM sprites ORDER BY published_at DESC, id").fetchall()
    return [summarize(_sprite_from(row)) for row in rows]


def preview_script(store: Store, sprite_id: str) -> str:
    """Read the sprite's own copy of the player bundle after checking it against the published hash."""
    sprite = get(store, sprite_id)
    path = store.root / "sprites" / sprite.sprite_id / PREVIEW_BUNDLE
    try:
        if digest(path) != sprite.preview_sha256:
            raise ValueError("preview changed")
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise NotFound("sprite preview unavailable") from exc


def _validated_request(request: pb.PublishSpriteRequest, schema: dict, version_kind: str) -> None:
    """Check the declared kind and the text/keyword field names against the sealed schema."""
    if request.kind not in _PUBLISH_KINDS:
        raise _invalid("kind 必须是文字、滤镜叠加、视频叠加或转场叠加")
    expected = _VERSION_KINDS.get(version_kind)
    if expected is not None and expected != request.kind:
        raise _invalid("kind 与该版本创建时的类型不一致")
    if request.kind == pb.SPRITE_KIND_TEXT:
        text = _definition(schema, request.text_prop)
        if text is None or text.get("type") != "string":
            raise _invalid("text_prop 必须是该版本参数中的字符串字段（嵌套字段用点号，如 title_main.title）")
        if request.keywords_prop and (
            _definition(schema, request.keywords_prop) is None
            or request.keywords_prop == request.text_prop
            or _definition(schema, request.keywords_prop).get("type") == "object"
        ):
            raise _invalid("keywords_prop 必须是该版本参数中不同于 text_prop 的非对象字段")
    elif request.text_prop or request.keywords_prop:
        raise _invalid("只有文字 Sprite 可以声明 text_prop 与 keywords_prop")


async def publish(service, request: pb.PublishSpriteRequest) -> pb.SpriteSummary:
    """Copy a verified accepted version and its sealed player; repeating returns the original asset."""
    store = service.store
    try:
        version_id = UUID(request.source_version_id)
    except ValueError:
        raise _invalid("source_version_id 不是有效的 UUID") from None
    version = store.version(version_id)
    accepted = store.root / "accepted" / str(version.id)
    try:
        verify_artifacts(version.candidate, version.spec, version.validation, accepted)
    except (ValueError, OSError) as exc:
        raise Conflict("accepted artifact unavailable") from exc
    if PREVIEW_BUNDLE not in version.validation.artifacts:
        raise Conflict("该版本没有可发布的交互预览")
    schema = version.candidate.config_schema
    _validated_request(request, schema, version.spec.sprite_kind)

    def existing() -> sqlite3.Row | None:
        """Look up the publication that this exact source and field choice already produced."""
        with store.connection() as db:
            return db.execute(
                "SELECT data FROM sprites WHERE source_version_id=? AND kind=? AND text_prop=? AND keywords_prop=?",
                (str(version.id), request.kind, request.text_prop, request.keywords_prop),
            ).fetchone()

    if (row := existing()) is not None:
        return summarize(_sprite_from(row))

    composition = version.spec.composition
    sprite_id = str(uuid4())
    published_at = datetime.now(UTC)
    bundle = (accepted / PREVIEW_BUNDLE).read_bytes()
    directory = store.root / "sprites" / sprite_id
    directory.mkdir(parents=True)
    try:
        (directory / PREVIEW_BUNDLE).write_bytes(bundle)
        sprite = pb.PublishedSprite(
            sprite_id=sprite_id,
            source_version_id=str(version.id),
            name=version.spec.name,
            kind=request.kind,
            canvas=pb.SpriteCanvas(
                width=composition.width,
                height=composition.height,
                fps=round(composition.fps),
                preview_frames=composition.duration_in_frames,
            ),
            tsx_code=version.candidate.tsx_code,
            code_sha256=hashlib.sha256(version.candidate.tsx_code.encode()).hexdigest(),
            parameters=_parameters(schema, version.candidate.default_config, {request.text_prop, request.keywords_prop}),
            text_prop=request.text_prop,
            keywords_prop=request.keywords_prop,
            preview_sha256=hashlib.sha256(bundle).hexdigest(),
            config_schema_json=json.dumps(schema, ensure_ascii=False),
            default_config_json=json.dumps(version.candidate.default_config, ensure_ascii=False),
        )
        sprite.published_at.FromDatetime(published_at)
        with store.connection() as db:
            db.execute(
                "INSERT INTO sprites VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    sprite_id, str(version.id), request.kind, request.text_prop, request.keywords_prop,
                    published_at.isoformat(),
                    json.dumps(MessageToDict(sprite, preserving_proto_field_name=True), ensure_ascii=False),
                ),
            )
    except sqlite3.IntegrityError:
        shutil.rmtree(directory, ignore_errors=True)
        if (row := existing()) is None:
            raise
        return summarize(_sprite_from(row))
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return summarize(sprite)
