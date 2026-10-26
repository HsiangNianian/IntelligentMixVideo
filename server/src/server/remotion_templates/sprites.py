"""Publish accepted Remotion versions as immutable Sprites.

A publication copies the sealed source, parameter contract and interactive player bundle out of the
chat-owned version directory, so deleting or editing the source work never changes a placed Sprite.
Publications (`imv.sprite.v1.PublishedSprite` JSON) are rows of the MySQL table in `sprite_store.py`, next
to the templates whose `remotion` tracks reference them; their player bundle copies are objects in the
Remotion object store (ZOS, or the data folder without ZOS). A bundle that is missing or no longer matches its
recorded hash is rebuilt from the published source on the next preview. Where a Sprite is placed is part of the template, so nothing here reads or validates IMS
template data. HTTP framing lives in `sprite_router.py`.
"""

import asyncio
import hashlib
import json
import logging
import shutil
import weakref
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from uuid import UUID, uuid4

from fastapi import HTTPException
from generated.imv.sprite.v1 import sprite_pb2 as pb
from google.protobuf.json_format import MessageToDict, ParseDict, ParseError
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from . import boundary, sprite_store
from .categories import FONT_PARAMETERS, HEX_COLOR, LINE_STARTS_PROP, category_problems, line_count, operator_message
from .fonts import open_font_schema
from .publication import current_bundle
from .models import TemplateVersion
from .object_store import PREFIX, ObjectStoreError
from .store import Conflict, NotFound, Store

PREVIEW_BUNDLE = "interactive.js"


def bundle_key(sprite_id: str) -> str:
    """Object key of one publication's player bundle copy."""
    return f"{PREFIX}/sprites/{sprite_id}/{PREVIEW_BUNDLE}"


def _bundle_bytes(store: Store, sprite: pb.PublishedSprite) -> bytes | None:
    """The publication's verified bundle copy, or None when it is missing or no longer matches its hash.

    An unreachable object store is not a lost copy: it raises ObjectStoreError instead of starting a rebuild.
    """
    try:
        return store.cached(
            bundle_key(sprite.sprite_id), Path("sprites") / sprite.sprite_id / PREVIEW_BUNDLE, sprite.preview_sha256,
        ).read_bytes()
    except ObjectStoreError:
        raise
    except (OSError, ValueError):
        return None
# 同一来源与字段选择的发布轮流执行：重复点击等待首个完成后读取它保存的资产，不重复重建播放器包。
# 值为弱引用，没有持有者和等待者后锁自动释放，字典不会随发布次数增长。
_publishing: weakref.WeakValueDictionary[tuple, asyncio.Lock] = weakref.WeakValueDictionary()
# 同一资产的预览重建与删除轮流执行：并发预览只重建一次，删除不会被进行中的重建写回副本。
_bundles: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
# 预览包重建失败后的冷却时间（秒）：期间预览直接 404，不再为每次请求启动隔离 worker。
REBUILD_COOLDOWN = 300.0
# 最近一次重建失败的单调时间，按资产 ID；成功或删除后清除，只含已发布资产。
_rebuild_failed: dict[str, float] = {}


def bundle_lock(sprite_id: str) -> asyncio.Lock:
    """The lock that serializes rebuilding and deleting one publication's player copy."""
    lock = _bundles.get(sprite_id)
    if lock is None:
        lock = _bundles[sprite_id] = asyncio.Lock()
    return lock

# 发布类型与效果分类的对应；视频特效与转场在界面上合为第三类，但必备参数和宿主变换按各自类型检查。
KIND_NAMES = {
    pb.SPRITE_KIND_TEXT: "text",
    pb.SPRITE_KIND_FILTER_OVERLAY: "filter_overlay",
    pb.SPRITE_KIND_VIDEO_OVERLAY: "video_overlay",
    pb.SPRITE_KIND_TRANSITION_OVERLAY: "transition_overlay",
}
# 字效固定的文字与关键词参数名；关键词可选。
TEXT_PROP = "text"
KEYWORDS_PROP = "keywords"


# 目录里显示的资产名称长度上限，与发布时由描述截取的名称一致。
NAME_MAX_LENGTH = 100


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


def _parameter(key: str, definition: dict, default) -> pb.SpriteParameter | None:
    """Describe one top-level parameter as an editable control; None when its default does not match its type."""
    kind = definition.get("type")
    if kind == "array":
        if not isinstance(default, list) or not all(isinstance(item, str) for item in default):
            return None
        value = pb.ScalarValue(string_list_value=pb.StringList(values=default))
    elif (value := _scalar(kind, default)) is None:
        return None
    parameter = pb.SpriteParameter(
        key=key,
        label=str(definition.get("title") or key)[:100],
        access=pb.OPERATOR_ACCESS_VISIBLE_EDITABLE,
        default_value=value,
    )
    if kind in {"number", "integer"}:
        for bound in ("minimum", "maximum"):
            if isinstance(definition.get(bound), (int, float)) and not isinstance(definition[bound], bool):
                setattr(parameter, bound, float(definition[bound]))
    for item in definition.get("enum", []):
        allowed = _scalar(kind, item)
        if allowed is not None:
            parameter.allowed_values.append(allowed)
    lengths = definition.get("items", {}) if kind == "array" else definition
    for field, bound in (("min_length", "minLength"), ("max_length", "maxLength")):
        if isinstance(lengths.get(bound), int) and not isinstance(lengths[bound], bool):
            setattr(parameter, field, lengths[bound])
    if kind == "array" and isinstance(definition.get("maxItems"), int):
        parameter.max_items = definition["maxItems"]
    if definition.get("pattern") == HEX_COLOR:
        parameter.format = pb.SPRITE_PARAMETER_FORMAT_COLOR
    elif kind == "integer":
        # number_value cannot tell an integer from a number; the format keeps controls and overrides integral.
        parameter.format = pb.SPRITE_PARAMETER_FORMAT_INTEGER
    return parameter


def _parameters(schema: dict, defaults: dict, names: list[str]) -> list[pb.SpriteParameter]:
    """Expose the version's top-level parameters, text and keywords included, as editable controls.

    Each Preset instance's remaining props stay nested and internal: the code is a fixed sample that templates
    change only through these parameters. A multi-line text's `lineStartFrames` is content the host injects from
    the subtitle cues, so it is not a control either.
    """
    properties = schema.get("properties", {})
    return [
        item for name in names
        if name != LINE_STARTS_PROP and (item := _parameter(name, properties.get(name, {}), defaults.get(name))) is not None
    ]


def _content_box(version: TemplateVersion) -> pb.SpriteBox:
    """The union of the instances' layout boxes clipped to the canvas; the whole canvas when nothing is visible."""
    composition = version.spec.composition
    layouts = [item.layout for item in version.spec.sprite.instances]
    left = max(0.0, min(item.x for item in layouts))
    top = max(0.0, min(item.y for item in layouts))
    right = min(float(composition.width), max(item.x + item.width for item in layouts))
    bottom = min(float(composition.height), max(item.y + item.height for item in layouts))
    if right <= left or bottom <= top:
        return pb.SpriteBox(x=0, y=0, width=composition.width, height=composition.height)
    return pb.SpriteBox(x=left, y=top, width=right - left, height=bottom - top)


def published_from(data: dict) -> pb.PublishedSprite:
    """Restore a stored publication and refuse source bytes that no longer match their hash."""
    sprite = ParseDict(data, pb.PublishedSprite())
    if hashlib.sha256(sprite.tsx_code.encode()).hexdigest() != sprite.code_sha256:
        raise Conflict("Published Sprite failed integrity verification.")
    return sprite


def get(sprite_id: str) -> pb.PublishedSprite:
    """Read one publication by ID; unknown or malformed IDs are a 404."""
    try:
        identifier = str(UUID(sprite_id))
    except ValueError:
        raise NotFound("sprite not found") from None
    data = sprite_store.get(identifier)
    if data is None:
        raise NotFound("sprite not found")
    return published_from(data)


def rename(sprite_id: str, name: str) -> pb.SpriteSummary:
    """Change the name shown in the catalog; the code, parameters and preview bundle are untouched.

    The name is trimmed and must be 1 to 100 characters; it need not be unique, like the names publishing derives.
    Unknown or malformed IDs are a 404. Assets that templates place can be renamed, since templates store only IDs.
    """
    name = name.strip()
    if not 1 <= len(name) <= NAME_MAX_LENGTH:
        raise _invalid(f"名称去除首尾空白后需为 1～{NAME_MAX_LENGTH} 个字符")
    try:
        identifier = str(UUID(sprite_id))
    except ValueError:
        raise NotFound("sprite not found") from None
    data = sprite_store.update_data(identifier, {"name": name})
    if data is None:
        raise NotFound("sprite not found")
    return summarize(published_from(data))


def content_box(sprite: pb.PublishedSprite) -> pb.SpriteBox:
    """The pivot box of the host transform; publications made before boxes were recorded use the whole canvas."""
    if sprite.HasField("content_box"):
        return sprite.content_box
    return pb.SpriteBox(x=0, y=0, width=sprite.canvas.width, height=sprite.canvas.height)


def sampled(sprite: pb.PublishedSprite) -> bool:
    """Whether the publication rendered boundary samples of its parameters.

    Every publication made with samples records a content box; earlier ones have none, and their parameters were
    never rendered away from their defaults.
    """
    return sprite.HasField("content_box")


def operator_parameters(sprite: pb.PublishedSprite) -> list[pb.SpriteParameter]:
    """The parameters operators see; an unsampled publication's editable ones become read-only.

    A text Sprite's font parameters are the exception: their values are checked against the live font library when a
    placement is saved, so changing a font does not rely on boundary samples an older publication never rendered. A
    parameter that merely uses a font-like name on another kind stays read-only.
    """
    if sampled(sprite):
        return list(sprite.parameters)
    result = []
    for parameter in sprite.parameters:
        copy = pb.SpriteParameter()
        copy.CopyFrom(parameter)
        font = sprite.kind == pb.SPRITE_KIND_TEXT and copy.key in FONT_PARAMETERS
        if copy.access == pb.OPERATOR_ACCESS_VISIBLE_EDITABLE and not font:
            copy.access = pb.OPERATOR_ACCESS_VISIBLE_READ_ONLY
        result.append(copy)
    return result


def box_json(sprite: pb.PublishedSprite) -> dict:
    """The content box as the plain object the preview page and the overlay worker read."""
    box = content_box(sprite)
    return {"x": box.x, "y": box.y, "width": box.width, "height": box.height}


def summarize(sprite: pb.PublishedSprite) -> pb.SpriteSummary:
    """Project a publication onto the public catalog entry, never including TSX."""
    return pb.SpriteSummary(
        sprite_id=sprite.sprite_id,
        name=sprite.name,
        kind=sprite.kind,
        canvas=sprite.canvas,
        parameters=operator_parameters(sprite),
        source_version_id=sprite.source_version_id,
        preview_url=f"/api/sprites/{sprite.sprite_id}/preview",
        keywords_supported=bool(sprite.keywords_prop),
        content_box=content_box(sprite),
        line_count=line_count(json.loads(sprite.config_schema_json or "{}")) if sprite.kind == pb.SPRITE_KIND_TEXT else 0,
    )


def catalog() -> list[pb.SpriteSummary]:
    """List readable publications newest first.

    A record that cannot be parsed or fails its hash check is logged and left out, so one damaged row
    never hides the whole library; reading that asset by ID still reports the exact failure.
    """
    items = []
    for sprite_id, data in sprite_store.listing():
        try:
            items.append(summarize(published_from(data)))
        except (Conflict, ValueError, ParseError, TypeError) as exc:
            logging.getLogger(__name__).warning("Skipping unreadable Sprite %s: %s", sprite_id, exc)
    return items


async def preview_script(service, sprite_id: str) -> tuple[str, dict]:
    """Read the sprite's own verified copy of the player bundle, rebuilding it from the published source when needed.

    A lost copy (missing or no longer matching its hash) is rebuilt, and the preview is a 404 if that fails. A copy
    that predates the current host protocol (`current_bundle`) cannot take the placement transform or parameter
    overrides, so the template editor would show the asset unmoved; it is rebuilt once instead of waiting for the
    Sprite to be saved again, and when that fails the old copy is served as before.
    Previews of one Sprite load together (one iframe per placement): they take turns, so only the first rebuilds,
    and after a failed rebuild of a lost copy later previews answer 404 for `REBUILD_COOLDOWN` seconds instead of
    starting another isolated worker each. An unreachable object store is a 503.

    Also returns what the page injects for the player: the published defaults and the content box.
    """
    store = service.store
    sprite = await run_in_threadpool(get, sprite_id)
    try:
        script = await run_in_threadpool(_bundle_bytes, store, sprite)
        if script is None or not current_bundle(script):
            async with bundle_lock(sprite.sprite_id):
                # Another preview may have rebuilt the copy, or a deletion removed the asset, while this one waited.
                sprite = await run_in_threadpool(get, sprite_id)
                script = await run_in_threadpool(_bundle_bytes, store, sprite)
                if script is None:
                    script = await _rebuild_once(service, sprite)
                elif not current_bundle(script):
                    fresh = await _rebuilt(service, sprite, throttle=False)
                    if fresh is not None:
                        try:
                            await run_in_threadpool(_store_bundle, store, sprite, fresh)
                            script = fresh
                        except Exception:
                            logging.getLogger(__name__).exception("Sprite preview refresh not stored: %s", sprite.sprite_id)
    except ObjectStoreError as exc:
        raise HTTPException(503, "资产预览存储暂不可用，请稍后重试") from exc
    return script.decode("utf-8"), {"config": json.loads(sprite.default_config_json or "{}"), "box": box_json(sprite)}


async def _rebuilt(service, sprite: pb.PublishedSprite, *, throttle: bool = True) -> bytes | None:
    """Rebuild a publication's player bundle from its source; None when it failed or, if throttled, failed within the cooldown.

    Only a lost copy is throttled (its preview is a 404 anyway); an outdated copy is retried by every preview, since
    the old copy keeps being served and a restored sandbox should refresh it at once.
    """
    failed = _rebuild_failed.get(sprite.sprite_id)
    if throttle and failed is not None and monotonic() - failed < REBUILD_COOLDOWN:
        return None
    canvas = sprite.canvas
    script = await _rebuild_bundle(service, sprite.tsx_code, json.loads(sprite.default_config_json or "{}"), {
        "width": canvas.width, "height": canvas.height, "fps": canvas.fps, "duration_in_frames": canvas.preview_frames,
    }, sprite.sprite_id)
    if script is None:
        if throttle:
            _rebuild_failed[sprite.sprite_id] = monotonic()
    else:
        _rebuild_failed.pop(sprite.sprite_id, None)
    return script


async def _rebuild_once(service, sprite: pb.PublishedSprite) -> bytes:
    """Rebuild and store a lost player copy under its lock; without a copy the preview is a 404."""
    script = await _rebuilt(service, sprite)
    if script is None:
        raise NotFound("sprite preview unavailable")
    await run_in_threadpool(_store_bundle, service.store, sprite, script)
    return script


def remove(store: Store, sprite_id: str, used_by: list[str]) -> None:
    """Delete an unused publication with its bundle object and cache; a template that places it blocks deletion.

    `used_by` lists the cloud templates whose `remotion` tracks reference the Sprite (409 naming them); an unknown
    ID is a 404. A record that fails its integrity check can still be deleted. The source work and its versions are
    untouched, so the version can be saved to the library again.

    The bundle objects go first and the record last: when the object store fails the record stays, the request is a
    retryable 503 and the asset stays listed, instead of a record that is gone while the client still shows it.
    """
    if sprite_store.get(sprite_id) is None:
        raise NotFound("sprite not found")
    if used_by:
        raise Conflict(f"资产正被模板使用，请先在这些模板里移除后再删除：{'、'.join(used_by)}")
    try:
        store.objects.delete_prefix(f"{PREFIX}/sprites/{sprite_id}/")
    except ObjectStoreError as exc:
        # The answer only asks for a retry; the object store's own error (code, failed keys) is kept in the server log.
        logging.getLogger(__name__).exception("Sprite files not deleted: %s", sprite_id)
        raise HTTPException(503, "资产文件暂时无法删除，请稍后重试") from exc
    shutil.rmtree(store.root / "cache" / "sprites" / sprite_id, ignore_errors=True)
    if not sprite_store.remove(sprite_id):
        raise NotFound("sprite not found")
    _rebuild_failed.pop(sprite_id, None)


def _validated_request(request: pb.PublishSpriteRequest, version: TemplateVersion) -> None:
    """Check the chosen kind against the version's category parameters and the fixed text/keyword names."""
    if max(len(request.text_prop), len(request.keywords_prop)) > sprite_store.NAME_LENGTH:
        raise _invalid(f"text_prop 与 keywords_prop 不能超过 {sprite_store.NAME_LENGTH} 个字符")
    if request.kind not in KIND_NAMES:
        raise _invalid("kind 必须是字效、滤镜、视频特效或转场")
    sprite = version.spec.sprite
    if version.spec.schema_version != "2" or sprite is None or sprite.kind is None:
        raise _invalid("该版本由旧版生成，没有效果分类和参数契约；请在聊天中继续修改，生成新版本后再保存到资产")
    bound = {item.name for item in sprite.bindings}
    problems = category_problems(KIND_NAMES[request.kind], version.candidate.config_schema, bound, version.candidate.default_config)
    if problems:
        raise _invalid(operator_message(problems))
    if request.kind == pb.SPRITE_KIND_TEXT:
        if request.text_prop != TEXT_PROP:
            raise _invalid(f"字效的 text_prop 必须是 {TEXT_PROP}")
        # Text versions bind keywords now (category check above); "" stays for re-saves of older publications only.
        if request.keywords_prop != (KEYWORDS_PROP if KEYWORDS_PROP in bound else ""):
            raise _invalid(f"keywords_prop 在该版本声明了关键词参数时必须为 {KEYWORDS_PROP}，否则为空")
    elif request.text_prop or request.keywords_prop:
        raise _invalid("只有字效可以声明 text_prop 与 keywords_prop")


async def _rebuild_bundle(service, code: str, config: dict, composition: dict, label) -> bytes | None:
    """Rebuild a player bundle from sealed source with the current host code; None when unavailable.

    Failure is non-fatal when publishing: the asset is still saved with its original bundle and simply cannot be
    previewed over the template.
    """
    store = service.store
    work = store.root / "sprites" / f".build-{uuid4()}"
    try:
        work.mkdir(parents=True)
        (work / "request.json").write_text(json.dumps({
            "code": code, "config": config, "composition": composition,
        }, ensure_ascii=False), encoding="utf-8")
        await service.harness.renderer.run_worker(work, worker="presentation-worker.mjs")
        data = (work / PREVIEW_BUNDLE).read_bytes()
        return data if current_bundle(data) else None
    except Exception:
        logging.getLogger(__name__).exception("Sprite preview rebuild failed: %s", label)
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _store_bundle(store: Store, sprite: pb.PublishedSprite, data: bytes) -> None:
    """Replace a publication's player copy and its recorded hash; the source and parameters are untouched.

    The object is written before the hash is recorded. If the second step fails, the copy no longer matches and
    the next preview rebuilds it, so a partial update never leaves an unusable asset.
    """
    staged = store.root / "sprites" / f".bundle-{uuid4().hex}.js"
    staged.parent.mkdir(parents=True, exist_ok=True)
    try:
        staged.write_bytes(data)
        store.objects.put(bundle_key(sprite.sprite_id), staged, "text/javascript")
        sprite.preview_sha256 = hashlib.sha256(data).hexdigest()
        sprite_store.update_data(sprite.sprite_id, {"preview_sha256": sprite.preview_sha256})
    finally:
        staged.unlink(missing_ok=True)


def _existing(version: TemplateVersion, request: pb.PublishSpriteRequest) -> dict | None:
    """Look up the publication JSON that this source and field choice already produced.

    A version composed before effect categories cannot be published again, but its earlier publications must stay
    re-savable so their player bundle can be refreshed. Their text fields were free dot paths that today's client
    no longer sends, so for such a version any publication of the same kind counts.
    """
    data = sprite_store.find(str(version.id), request.kind, request.text_prop, request.keywords_prop)
    if data is None and (version.spec.sprite is None or version.spec.sprite.kind is None):
        data = sprite_store.find_any(str(version.id), request.kind)
    return data


def _inspect(
    store: Store, request: pb.PublishSpriteRequest, device: UUID,
) -> tuple[TemplateVersion, pb.PublishedSprite | None, bytes]:
    """Check the request against its re-verified source of this device and find the player bytes involved.

    Returns the version, the publication this source and field choice already produced (or None) and the
    bundle that publication serves (empty when its copy is lost, so it is rebuilt), or that a new one would copy.
    It hashes every sealed artifact, so callers run it in a worker thread.
    """
    try:
        version_id = UUID(request.source_version_id)
    except ValueError:
        raise _invalid("source_version_id 不是有效的 UUID") from None
    version = store.version(version_id, device)
    try:
        accepted = store.accepted(version)
    except ObjectStoreError as exc:
        raise HTTPException(503, "Artifact storage is unavailable; retry later.") from exc
    except (ValueError, OSError) as exc:
        raise Conflict("accepted artifact unavailable") from exc
    if PREVIEW_BUNDLE not in version.validation.artifacts:
        raise Conflict("该版本没有可发布的交互预览")
    if max(len(request.text_prop), len(request.keywords_prop)) > sprite_store.NAME_LENGTH:
        raise _invalid(f"text_prop 与 keywords_prop 不能超过 {sprite_store.NAME_LENGTH} 个字符")
    # Re-saving an existing publication only refreshes its player; the rules for new publications do not apply to it.
    if (data := _existing(version, request)) is None:
        _validated_request(request, version)
        return version, None, (accepted / PREVIEW_BUNDLE).read_bytes()
    sprite = published_from(data)
    return version, sprite, _bundle_bytes(store, sprite) or b""


def _bound_names(version: TemplateVersion) -> list[str]:
    """The version's top-level parameters in schema order."""
    bound = {item.name for item in version.spec.sprite.bindings}
    return [name for name in version.candidate.config_schema.get("properties", {}) if name in bound]


async def _sample(service, version: TemplateVersion, kind: int) -> None:
    """Render the version's boundary samples in the isolated browser before it becomes an asset.

    Availability comes first: a sample that fails to render, or an end the parameter constraints leave unsampled,
    is only logged and the publication goes ahead, because the code still renders at its defaults and a placement
    that really breaks fails its own overlay task. A validator that cannot start is still a 503.
    """
    from .tool_validation import ToolValidator, ValidationUnavailable
    from .tools.contracts import ComponentDefinition

    candidate, composition = version.candidate, version.spec.composition
    box = _content_box(version)
    names = _bound_names(version)
    samples = boundary.samples(candidate.config_schema, candidate.default_config, names, KIND_NAMES[kind], (composition.width, composition.height))
    logger = logging.getLogger(__name__)
    if missing := boundary.unsampled(candidate.config_schema, candidate.default_config, names, samples):
        logger.warning("Sprite boundary samples skip parameters %s of %s: their ends do not fit the constraints", missing, version.id)
    work = service.store.root / "sprites" / f".samples-{uuid4()}"
    try:
        checks = await ToolValidator(service.harness.renderer, work).validate_samples(
            ComponentDefinition(code=candidate.tsx_code, parameter_schema=candidate.config_schema, default_parameters=candidate.default_config),
            {"width": composition.width, "height": composition.height, "fps": round(composition.fps), "duration_frames": composition.duration_in_frames},
            {"x": box.x, "y": box.y, "width": box.width, "height": box.height},
            [asdict(item) for item in samples],
            text=kind == pb.SPRITE_KIND_TEXT,
        )
    except ValidationUnavailable as exc:
        logger.warning("Sprite boundary samples unavailable for %s: %s", version.id, exc)
        raise HTTPException(503, "参数边界检查环境不可用，请稍后重试") from None
    finally:
        await run_in_threadpool(shutil.rmtree, work, True)
    for item in checks:
        if item.status != "passed":
            logger.warning("Sprite boundary sample %s of %s failed, published anyway: %s", item.name.removeprefix("sample:"), version.id, (item.message or "")[:300])


def _create(store: Store, request: pb.PublishSpriteRequest, version: TemplateVersion, bundle: bytes) -> pb.SpriteSummary:
    """Record a new publication with its own copy of the player bundle; losing a race to an identical one returns the winner."""
    # Published font parameters are open strings: their enum can only name fonts known at generation time.
    schema = open_font_schema(version.candidate.config_schema)
    composition = version.spec.composition
    sprite_id = str(uuid4())
    published_at = datetime.now(UTC)
    staged = store.root / "sprites" / f".bundle-{uuid4().hex}.js"
    staged.parent.mkdir(parents=True, exist_ok=True)
    try:
        staged.write_bytes(bundle)
        store.objects.put(bundle_key(sprite_id), staged, "text/javascript")
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
            parameters=_parameters(schema, version.candidate.default_config, _bound_names(version)),
            text_prop=request.text_prop,
            keywords_prop=request.keywords_prop,
            preview_sha256=hashlib.sha256(bundle).hexdigest(),
            config_schema_json=json.dumps(schema, ensure_ascii=False),
            default_config_json=json.dumps(version.candidate.default_config, ensure_ascii=False),
            content_box=_content_box(version),
        )
        sprite.published_at.FromDatetime(published_at)
        sprite_store.insert(
            sprite_id=sprite_id, source_version_id=str(version.id), kind=request.kind,
            text_prop=request.text_prop, keywords_prop=request.keywords_prop, published_at=published_at,
            data=MessageToDict(sprite, preserving_proto_field_name=True),
        )
    except IntegrityError as exc:
        store.objects.delete_prefix(f"{PREFIX}/sprites/{sprite_id}/")
        # Only a duplicate key means an identical publication won the race; other integrity errors are real.
        if exc.orig.args[0] != 1062 or (data := _existing(version, request)) is None:
            raise
        return summarize(published_from(data))
    except BaseException:
        store.objects.delete_prefix(f"{PREFIX}/sprites/{sprite_id}/")
        raise
    finally:
        staged.unlink(missing_ok=True)
    return summarize(sprite)


async def publish(service, request: pb.PublishSpriteRequest, device: UUID) -> pb.SpriteSummary:
    """Copy a verified accepted version into an immutable publication; repeating returns the original.

    A new publication first renders its boundary samples (`boundary.py`), so later template overrides inside its
    parameter contract need no sandbox. The player bundle is rebuilt with the current host code when the sealed
    one predates frame-synchronised previews or placement overrides, so older versions can still be previewed
    over a template after saving. Hashing, copying and database work run in worker threads and only the browser
    work is awaited, so the event loop stays free; identical publications take turns, so a double click samples
    and rebuilds once.
    """
    store = service.store
    lock = _publishing.setdefault(
        (request.source_version_id, request.kind, request.text_prop, request.keywords_prop), asyncio.Lock()
    )
    async with lock:
        version, sprite, bundle = await run_in_threadpool(_inspect, store, request, device)
        if sprite is None:
            await _sample(service, version, request.kind)
        fresh = None if current_bundle(bundle) else await _rebuild_bundle(
            service, version.candidate.tsx_code, version.candidate.default_config,
            version.spec.composition.model_dump(), version.id,
        )
        if sprite is None:
            return await run_in_threadpool(_create, store, request, version, fresh or bundle)
        if fresh:
            await run_in_threadpool(_store_bundle, store, sprite, fresh)
        return summarize(sprite)
