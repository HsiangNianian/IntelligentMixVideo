"""Publish accepted Remotion versions as immutable Sprites and save where they are placed.

A publication copies the sealed source, parameter contract and interactive player bundle out of the
chat-owned version directory, so deleting or editing the source work never changes a placed Sprite.
Publications (`imv.sprite.v1.PublishedSprite` JSON) and the clip layouts that place them both live in
the Remotion SQLite database. Nothing here reads or validates IMS template data, so IMS rules never
apply to Remotion content. HTTP framing lives in `sprite_router.py`.
"""

import asyncio
import hashlib
import json
import logging
import math
import shutil
import sqlite3
import weakref
from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException
from generated.imv.sprite.v1 import sprite_pb2 as pb
from google.protobuf.json_format import MessageToDict, ParseDict, ParseError
from starlette.concurrency import run_in_threadpool

from .evidence import digest, verify_artifacts
from .models import TemplateVersion
from .store import Conflict, NotFound, Store

MAX_PLACEMENTS = 100
PREVIEW_BUNDLE = "interactive.js"
# 只有包含逐帧同步处理的预览包才能叠加到模板预览上；更早构建的包缺少此标记。
SYNC_MARKER = b"imv-preview-sync"
# 同一来源与字段选择的发布轮流执行：重复点击等待首个完成后读取它保存的资产，不重复重建播放器包。
# 值为弱引用，没有持有者和等待者后锁自动释放，字典不会随发布次数增长。
_publishing: weakref.WeakValueDictionary[tuple, asyncio.Lock] = weakref.WeakValueDictionary()

# 版本创建时的内容类型与发布类型的对应；composition 版本不固定类型，由发布请求选择。
_VERSION_KINDS = {
    "text": pb.SPRITE_KIND_TEXT,
    "subtitle": pb.SPRITE_KIND_TEXT,
    "filter_overlay": pb.SPRITE_KIND_FILTER_OVERLAY,
    "video_overlay": pb.SPRITE_KIND_VIDEO_OVERLAY,
    "transition_overlay": pb.SPRITE_KIND_TRANSITION_OVERLAY,
}
# 发布时允许声明的内容类型；它只描述资产自身，与 IMS 模板里的对象无关。
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
    """List readable publications newest first.

    A record that cannot be parsed or fails its hash check is logged and left out, so one damaged row
    never hides the whole library; reading that asset by ID still reports the exact failure.
    """
    with store.connection() as db:
        rows = db.execute("SELECT id, data FROM sprites ORDER BY published_at DESC, id").fetchall()
    items = []
    for row in rows:
        try:
            items.append(summarize(_sprite_from(row)))
        except (Conflict, ValueError, ParseError) as exc:
            logging.getLogger(__name__).warning("Skipping unreadable Sprite %s: %s", row["id"], exc)
    return items


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


async def _rebuild_bundle(service, version) -> bytes | None:
    """Rebuild the player bundle from the sealed source with the current host code; None when unavailable.

    Failure is non-fatal: the asset is still saved with its original bundle and simply cannot be previewed
    over the template.
    """
    store = service.store
    work = store.root / "sprites" / f".build-{uuid4()}"
    try:
        work.mkdir(parents=True)
        (work / "request.json").write_text(json.dumps({
            "code": version.candidate.tsx_code,
            "config": version.candidate.default_config,
            "composition": version.spec.composition.model_dump(),
        }, ensure_ascii=False), encoding="utf-8")
        await service.harness.renderer.run_worker(work, worker="presentation-worker.mjs")
        data = (work / PREVIEW_BUNDLE).read_bytes()
        return data if SYNC_MARKER in data else None
    except Exception:
        logging.getLogger(__name__).exception("Sprite preview rebuild failed: %s", version.id)
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _store_bundle(store: Store, sprite: pb.PublishedSprite, data: bytes) -> None:
    """Replace a publication's player copy and its recorded hash; the source and parameters are untouched.

    The new bytes are staged beside the copy and the hash is recorded before they take its place, so a failed
    write or database update leaves the old copy matching its hash. A swap that never completes leaves the old
    copy, which still lacks the synchronisation marker, so publishing again retries it.
    """
    target = store.root / "sprites" / sprite.sprite_id / PREVIEW_BUNDLE
    staged = target.with_name(f"{PREVIEW_BUNDLE}.{uuid4().hex}.tmp")
    try:
        staged.write_bytes(data)
        sprite.preview_sha256 = hashlib.sha256(data).hexdigest()
        with store.connection() as db:
            db.execute(
                "UPDATE sprites SET data=? WHERE id=?",
                (json.dumps(MessageToDict(sprite, preserving_proto_field_name=True), ensure_ascii=False), sprite.sprite_id),
            )
        staged.replace(target)
    finally:
        staged.unlink(missing_ok=True)


def _existing(store: Store, version: TemplateVersion, request: pb.PublishSpriteRequest) -> sqlite3.Row | None:
    """Look up the publication that this exact source and field choice already produced."""
    with store.connection() as db:
        return db.execute(
            "SELECT data FROM sprites WHERE source_version_id=? AND kind=? AND text_prop=? AND keywords_prop=?",
            (str(version.id), request.kind, request.text_prop, request.keywords_prop),
        ).fetchone()


def _inspect(store: Store, request: pb.PublishSpriteRequest) -> tuple[TemplateVersion, pb.PublishedSprite | None, bytes]:
    """Check the request against its re-verified source and find the player bytes involved.

    Returns the version, the publication this source and field choice already produced (or None) and the
    bundle that publication serves, or that a new one would copy. It hashes every sealed artifact, so callers
    run it in a worker thread.
    """
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
    _validated_request(request, version.candidate.config_schema, version.spec.sprite_kind)
    if (row := _existing(store, version, request)) is None:
        return version, None, (accepted / PREVIEW_BUNDLE).read_bytes()
    sprite = _sprite_from(row)
    return version, sprite, (store.root / "sprites" / sprite.sprite_id / PREVIEW_BUNDLE).read_bytes()


def _create(store: Store, request: pb.PublishSpriteRequest, version: TemplateVersion, bundle: bytes) -> pb.SpriteSummary:
    """Record a new publication with its own copy of the player bundle; losing a race to an identical one returns the winner."""
    schema = version.candidate.config_schema
    composition = version.spec.composition
    sprite_id = str(uuid4())
    published_at = datetime.now(UTC)
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
        if (row := _existing(store, version, request)) is None:
            raise
        return summarize(_sprite_from(row))
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return summarize(sprite)


async def publish(service, request: pb.PublishSpriteRequest) -> pb.SpriteSummary:
    """Copy a verified accepted version into an immutable publication; repeating returns the original.

    The player bundle is rebuilt with the current host code when the sealed one predates frame-synchronised
    previews, so older versions can still be previewed over a template after saving. Hashing, copying and
    database work run in worker threads and only the rebuild is awaited, so the event loop stays free;
    identical publications take turns, so a double click rebuilds once.
    """
    store = service.store
    lock = _publishing.setdefault(
        (request.source_version_id, request.kind, request.text_prop, request.keywords_prop), asyncio.Lock()
    )
    async with lock:
        version, sprite, bundle = await run_in_threadpool(_inspect, store, request)
        fresh = None if SYNC_MARKER in bundle else await _rebuild_bundle(service, version)
        if sprite is None:
            return await run_in_threadpool(_create, store, request, version, fresh or bundle)
        if fresh:
            await run_in_threadpool(_store_bundle, store, sprite, fresh)
        return summarize(sprite)


def validate_placements(store: Store, placements: Iterable[pb.SpritePlacement]) -> None:
    """Reject a clip list that references unknown Sprites or is not made of fixed clips.

    A clip is a published Sprite plus the second it starts at; the Sprite's own length is its duration.
    IMS template objects have no meaning here, so an object target or a style override is refused instead
    of being stored and later read as an IMS rule.
    """
    items = list(placements)
    if len(items) > MAX_PLACEMENTS:
        raise _invalid(f"最多添加 {MAX_PLACEMENTS} 个 Remotion 片段")
    if sorted(item.order for item in items) != list(range(len(items))):
        raise _invalid("order 必须从 0 开始连续且不重复")
    identifiers = [item.id for item in items]
    if any(not identifier or len(identifier) > 64 for identifier in identifiers) or len(set(identifiers)) != len(items):
        raise _invalid("片段 id 必须非空、不超过 64 个字符且互不相同")
    for item in items:
        if item.target != pb.SPRITE_TARGET_UNSPECIFIED or item.overrides:
            raise _invalid("Remotion 片段是固定内容，不支持作用对象和样式覆盖")
        if item.start_mode != "seconds":
            raise _invalid("start_mode 只能是 seconds")
        if not math.isfinite(item.start) or item.start < 0:
            raise _invalid("start 必须是不小于 0 的有限数字")
        if item.HasField("duration") and (not math.isfinite(item.duration) or item.duration <= 0):
            raise _invalid("duration 必须是大于 0 的有限数字")
    for sprite_id in {item.sprite_id for item in items}:
        try:
            get(store, sprite_id)
        except NotFound:
            raise _invalid(f"Remotion 资产 {sprite_id} 不存在") from None


def get_bindings(store: Store, style_id: UUID) -> tuple[int, list[dict], datetime | None]:
    """Read the saved clips of one style ID; no record is revision 0 with an empty list.

    The ID only associates clips with an IMS template for the editor: it is never looked up in the
    IMS template library, so it does not have to exist there.
    """
    with store.connection() as db:
        row = db.execute(
            "SELECT revision, updated_at, data FROM style_sprite_bindings WHERE style_id=?", (str(style_id),),
        ).fetchone()
    if row is None:
        return 0, [], None
    return row["revision"], json.loads(row["data"]), datetime.fromisoformat(row["updated_at"])


def replace_bindings(
    store: Store, style_id: UUID, placements: list[dict], expected_revision: int,
) -> tuple[int, list[dict], datetime]:
    """Replace the whole clip list of one style ID; a revision other than the caller's last read is a 409.

    The revision check and the write share one immediate transaction, so two editors saving from the same
    revision cannot both succeed.
    """
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT revision FROM style_sprite_bindings WHERE style_id=?", (str(style_id),)).fetchone()
        current = row["revision"] if row else 0
        if current != expected_revision:
            raise Conflict("Remotion 片段已被其他修改更新，请刷新后重试")
        now = datetime.now(UTC)
        db.execute(
            "INSERT INTO style_sprite_bindings VALUES (?, ?, ?, ?) ON CONFLICT(style_id) DO UPDATE SET "
            "revision=excluded.revision, updated_at=excluded.updated_at, data=excluded.data",
            (str(style_id), current + 1, now.isoformat(), json.dumps(placements, ensure_ascii=False)),
        )
    return current + 1, placements, now
