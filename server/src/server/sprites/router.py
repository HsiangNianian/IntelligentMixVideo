"""Protobuf routes for immutable Remotion Sprite releases and cloud-style bindings."""

from uuid import UUID
import shutil

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from generated.imv.sprite.v1 import sprite_pb2 as pb
from google.protobuf.message import DecodeError, Message
from starlette.concurrency import run_in_threadpool
from starlette.background import BackgroundTask

from . import store
from .preview import interactive_script
from .render import render
from ..remotion_templates.settings import load_settings


router = APIRouter(prefix="/api/sprites", tags=["Remotion Sprite"])
PROTOBUF_MEDIA_TYPE = "application/x-protobuf"


async def _body(request: Request, model: type[Message]) -> Message:
    """Read a bounded binary message and reject an absent or incorrect content type."""
    if request.headers.get("content-type") != PROTOBUF_MEDIA_TYPE:
        raise HTTPException(415, "请使用 application/x-protobuf")
    payload = await request.body()
    if len(payload) > 1_000_000:
        raise HTTPException(413, "Sprite 请求过大")
    message = model()
    try:
        message.ParseFromString(payload)
    except DecodeError as exc:
        raise HTTPException(400, "Protobuf 请求内容无效") from exc
    return message


def _response(message: Message, status: int = 200) -> Response:
    """Serialize a generated message while preserving binary response content type."""
    return Response(message.SerializeToString(), status_code=status, media_type=PROTOBUF_MEDIA_TYPE)


@router.get("")
async def list_sprites() -> Response:
    """List accepted, published Sprite versions without returning executable code."""
    sprites = await run_in_threadpool(store.list_sprites)
    return _response(pb.ListSpritesResponse(sprites=sprites))


@router.get("/{sprite_id}/preview.mp4")
async def preview(sprite_id: UUID) -> FileResponse:
    """Return a verified publication preview independent of its source chat's lifetime."""
    path = await run_in_threadpool(store.preview_path, sprite_id)
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/{sprite_id}/interactive")
async def interactive(sprite_id: UUID) -> HTMLResponse:
    """Serve a transparent, parent-controlled preview of an immutable published Sprite."""
    sprite = await run_in_threadpool(store.get_sprite, sprite_id)
    script = (await interactive_script(sprite)).replace("</", "<\\/")
    return HTMLResponse(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<title>Remotion Sprite 预览</title><style>html,body,#root{margin:0;width:100%;height:100%;overflow:hidden;background:transparent}</style>'
        '<body><div id="root"></div><script>' + script + '</script></body></html>',
        headers={
            "Content-Security-Policy": "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; font-src http: https:; media-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'",
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
        },
    )


@router.get("/{sprite_id}/fonts/{weight}")
async def interactive_font(sprite_id: UUID, weight: int) -> FileResponse:
    """Serve only the managed font weights for an existing published Sprite."""
    if weight not in {400, 700}:
        raise HTTPException(404, "字体字重不存在")
    await run_in_threadpool(store.get_sprite, sprite_id)
    settings = load_settings()
    font = settings.font_regular if weight == 400 else settings.font_bold
    return FileResponse(font, media_type="font/collection", headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"})


@router.post("/publish", status_code=201)
async def publish(request: Request) -> Response:
    """Publish an accepted Agent version once; repeated identical requests return its prior release."""
    source = await _body(request, pb.PublishSpriteRequest)
    sprite = await run_in_threadpool(store.publish, source)
    return _response(pb.PublishSpriteResponse(sprite=sprite), 201)


@router.post("/render")
async def render_sprite(request: Request) -> FileResponse:
    """Render one bus-supplied text interval to VP9 Alpha WebM and delete it after transfer."""
    payload = await _body(request, pb.SpriteRenderInput)
    try:
        path = await render(payload)
    except TimeoutError as exc:
        raise HTTPException(504, "Sprite 渲染超时") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (OSError, KeyError, IndexError, ZeroDivisionError) as exc:
        raise HTTPException(409, "Sprite 渲染产物不可用") from exc
    return FileResponse(
        path, media_type="video/webm", filename="sprite.webm",
        background=BackgroundTask(shutil.rmtree, path.parent, ignore_errors=True),
    )


@router.get("/styles/{style_id}")
async def get_style(style_id: UUID) -> Response:
    """Read an independent Sprite binding revision for an existing cloud template ID."""
    value = await run_in_threadpool(store.get_bindings, style_id)
    return _response(pb.GetStyleSpritesResponse(bindings=value))


@router.post("/styles/{style_id}")
async def save_style(style_id: UUID, request: Request) -> Response:
    """Replace Sprite placements only when the caller's revision still matches."""
    payload = await _body(request, pb.SaveStyleSpritesRequest)
    if payload.style_id != str(style_id):
        raise HTTPException(422, "路径与请求中的 style_id 不一致")
    value = await run_in_threadpool(store.save_bindings, payload)
    return _response(pb.SaveStyleSpritesResponse(bindings=value))
