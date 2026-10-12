"""Publish accepted Remotion versions as immutable Sprites.

A publication copies the sealed source, parameter contract and interactive player bundle out of the
chat-owned version directory, so deleting or editing the source work never changes a placed Sprite.
Publications (`imv.sprite.v1.PublishedSprite` JSON) are rows of the MySQL table in `sprite_store.py`, next
to the templates whose `remotion` tracks reference them; their player bundle copies live in the Remotion
data folder. Where a Sprite is placed is part of the template, so nothing here reads or validates IMS
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
from uuid import UUID, uuid4

from fastapi import HTTPException
from generated.imv.sprite.v1 import sprite_pb2 as pb
from google.protobuf.json_format import MessageToDict, ParseDict, ParseError
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from . import boundary, sprite_store
from .categories import HEX_COLOR, category_problems, operator_message
from .evidence import digest, verify_artifacts
from .models import TemplateVersion
from .store import Conflict, NotFound, Store

PREVIEW_BUNDLE = "interactive.js"
# 只有包含逐帧同步处理的预览包才能叠加到模板预览上；更早构建的包缺少此标记。
SYNC_MARKER = b"imv-preview-sync"
# 能读取页面注入的默认值与内容区域、接受参数覆盖和宿主变换的预览包带有此标记；更早构建的包需要重建。
HOST_MARKER = b"imv-preview-host"
# 同一来源与字段选择的发布轮流执行：重复点击等待首个完成后读取它保存的资产，不重复重建播放器包。
# 值为弱引用，没有持有者和等待者后锁自动释放，字典不会随发布次数增长。
_publishing: weakref.WeakValueDictionary[tuple, asyncio.Lock] = weakref.WeakValueDictionary()

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
    return parameter


def _parameters(schema: dict, defaults: dict, names: list[str]) -> list[pb.SpriteParameter]:
    """Expose the version's top-level parameters, text and keywords included, as editable controls.

    Each Preset instance's remaining props stay nested and internal: the code is a fixed sample that templates
    change only through these parameters.
    """
    properties = schema.get("properties", {})
    return [item for name in names if (item := _parameter(name, properties.get(name, {}), defaults.get(name))) is not None]


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


def content_box(sprite: pb.PublishedSprite) -> pb.SpriteBox:
    """The pivot box of the host transform; publications made before boxes were recorded use the whole canvas."""
    if sprite.HasField("content_box"):
        return sprite.content_box
    return pb.SpriteBox(x=0, y=0, width=sprite.canvas.width, height=sprite.canvas.height)


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
        parameters=sprite.parameters,
        source_version_id=sprite.source_version_id,
        preview_url=f"/api/sprites/{sprite.sprite_id}/preview",
        keywords_supported=bool(sprite.keywords_prop),
        content_box=content_box(sprite),
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


def preview_script(store: Store, sprite_id: str) -> tuple[str, dict]:
    """Read the sprite's own copy of the player bundle after checking it against the published hash.

    Also returns what the page injects for the player: the published defaults and the content box.
    """
    sprite = get(sprite_id)
    path = store.root / "sprites" / sprite.sprite_id / PREVIEW_BUNDLE
    try:
        if digest(path) != sprite.preview_sha256:
            raise ValueError("preview changed")
        script = path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise NotFound("sprite preview unavailable") from exc
    return script, {"config": json.loads(sprite.default_config_json or "{}"), "box": box_json(sprite)}


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
    problems = category_problems(KIND_NAMES[request.kind], version.candidate.config_schema, bound)
    if problems:
        raise _invalid(operator_message(problems))
    if request.kind == pb.SPRITE_KIND_TEXT:
        if request.text_prop != TEXT_PROP:
            raise _invalid(f"字效的 text_prop 必须是 {TEXT_PROP}")
        if request.keywords_prop not in {"", KEYWORDS_PROP} or (request.keywords_prop and KEYWORDS_PROP not in bound):
            raise _invalid(f"keywords_prop 只能为空，或在该版本声明了关键词参数时为 {KEYWORDS_PROP}")
    elif request.text_prop or request.keywords_prop:
        raise _invalid("只有字效可以声明 text_prop 与 keywords_prop")


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
        return data if _current(data) else None
    except Exception:
        logging.getLogger(__name__).exception("Sprite preview rebuild failed: %s", version.id)
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _current(bundle: bytes) -> bool:
    """Whether a player bundle can be scrubbed by the template editor and takes placement overrides."""
    return SYNC_MARKER in bundle and HOST_MARKER in bundle


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
        sprite_store.replace_data(sprite.sprite_id, MessageToDict(sprite, preserving_proto_field_name=True))
        staged.replace(target)
    finally:
        staged.unlink(missing_ok=True)


def _existing(version: TemplateVersion, request: pb.PublishSpriteRequest) -> dict | None:
    """Look up the publication JSON that this exact source and field choice already produced."""
    return sprite_store.find(str(version.id), request.kind, request.text_prop, request.keywords_prop)


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
    _validated_request(request, version)
    if (data := _existing(version, request)) is None:
        return version, None, (accepted / PREVIEW_BUNDLE).read_bytes()
    sprite = published_from(data)
    return version, sprite, (store.root / "sprites" / sprite.sprite_id / PREVIEW_BUNDLE).read_bytes()


def _bound_names(version: TemplateVersion) -> list[str]:
    """The version's top-level parameters in schema order."""
    bound = {item.name for item in version.spec.sprite.bindings}
    return [name for name in version.candidate.config_schema.get("properties", {}) if name in bound]


async def _sample(service, version: TemplateVersion, kind: int) -> None:
    """Render the version's boundary samples in the isolated browser before it becomes an asset.

    After this passes, any template value inside the published parameter contract only needs the instant schema
    check. A sample that fails is a 422 naming it; a validator that cannot start is a 503.
    """
    from .tool_validation import ToolValidator, ValidationUnavailable
    from .tools.contracts import ComponentDefinition

    candidate, composition = version.candidate, version.spec.composition
    box = _content_box(version)
    samples = boundary.samples(
        candidate.config_schema, candidate.default_config, _bound_names(version), KIND_NAMES[kind],
        (composition.width, composition.height),
    )
    work = service.store.root / "sprites" / f".samples-{uuid4()}"
    try:
        checks = await ToolValidator(service.harness.renderer, work).validate_samples(
            ComponentDefinition(code=candidate.tsx_code, parameter_schema=candidate.config_schema, default_parameters=candidate.default_config),
            {"width": composition.width, "height": composition.height, "fps": round(composition.fps), "duration_frames": composition.duration_in_frames},
            {"x": box.x, "y": box.y, "width": box.width, "height": box.height},
            [asdict(item) for item in samples],
        )
    except ValidationUnavailable as exc:
        logging.getLogger(__name__).warning("Sprite boundary samples unavailable for %s: %s", version.id, exc)
        raise HTTPException(503, "参数边界检查环境不可用，请稍后重试") from None
    finally:
        await run_in_threadpool(shutil.rmtree, work, True)
    failed = next((item for item in checks if item.status != "passed"), None)
    if failed is not None:
        raise _invalid(
            f"参数边界检查未通过（{failed.name.removeprefix('sample:')}）：{(failed.message or '')[:300]}。"
            "请在聊天中让 Agent 修复后再保存到资产"
        )


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
        shutil.rmtree(directory, ignore_errors=True)
        # Only a duplicate key means an identical publication won the race; other integrity errors are real.
        if exc.orig.args[0] != 1062 or (data := _existing(version, request)) is None:
            raise
        return summarize(published_from(data))
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return summarize(sprite)


async def publish(service, request: pb.PublishSpriteRequest) -> pb.SpriteSummary:
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
        version, sprite, bundle = await run_in_threadpool(_inspect, store, request)
        if sprite is None:
            await _sample(service, version, request.kind)
        fresh = None if _current(bundle) else await _rebuild_bundle(service, version)
        if sprite is None:
            return await run_in_threadpool(_create, store, request, version, fresh or bundle)
        if fresh:
            await run_in_threadpool(_store_bundle, store, sprite, fresh)
        return summarize(sprite)
