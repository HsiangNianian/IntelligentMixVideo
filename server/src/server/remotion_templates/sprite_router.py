"""Protobuf HTTP routes for the Sprite asset catalog and the clip layout of a style under /api/sprites.

The router belongs to the main app (the Remotion app is mounted at /api/templates) and borrows the
Remotion runtime for both the published Sprites and the clip layouts that place them. It never touches
the IMS template library, so IMS template rules and records stay out of Remotion content.
Request and response bodies are `imv.sprite.v1` messages, mirroring the template router's framing.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from generated.imv.sprite.v1 import sprite_pb2 as pb
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.message import DecodeError, Message
from starlette.concurrency import run_in_threadpool

from . import sprites
from .api import app as remotion_templates_app
from .routes import player_page, runtime_for
from .store import NotFound
from .runtime import Runtime

router = APIRouter(prefix="/api/sprites", tags=["Remotion 资产"])
PROTOBUF_MEDIA_TYPE = "application/x-protobuf"
PROTOBUF_CONTENT = {PROTOBUF_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}}
_REQUEST_BODY = {"requestBody": {"required": True, "content": PROTOBUF_CONTENT}}


async def sprite_runtime() -> Runtime:
    """Share the Remotion app's lazily started runtime, so both mounts use one data folder."""
    return await runtime_for(remotion_templates_app.state)


Service = Annotated[Runtime, Depends(sprite_runtime)]


def _response(message: Message, status_code: int = 200) -> Response:
    """Send a generated message as a binary body."""
    return Response(message.SerializeToString(), status_code, media_type=PROTOBUF_MEDIA_TYPE)


async def _body(request: Request, message: Message) -> None:
    """Decode a protobuf request body, rejecting other media types and malformed bytes."""
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != PROTOBUF_MEDIA_TYPE:
        raise HTTPException(415, "请使用 application/x-protobuf")
    try:
        message.ParseFromString(await request.body())
    except DecodeError as exc:
        raise HTTPException(400, "Protobuf 请求内容无效") from exc


def _bindings_message(
    style_id: UUID, revision: int, placements: list[dict], updated_at,
) -> pb.StyleSpriteBindings:
    """Rebuild the stored binding record as its wire message; no record is revision 0 and empty."""
    message = pb.StyleSpriteBindings(style_id=str(style_id), revision=revision)
    for item in placements:
        ParseDict(item, message.placements.add())
    if updated_at is not None:
        message.updated_at.FromDatetime(updated_at)
    return message


@router.get("", response_class=Response, responses={200: {"content": PROTOBUF_CONTENT}}, summary="查询已发布的 Sprite 目录")
async def list_sprites(service: Service) -> Response:
    """返回可供模板编辑添加的不可变发布条目，不包含 TSX 源码。"""
    items = await run_in_threadpool(sprites.catalog, service.store)
    return _response(pb.ListSpritesResponse(sprites=items))


@router.post(
    "/publish", response_class=Response, openapi_extra=_REQUEST_BODY,
    responses={200: {"content": PROTOBUF_CONTENT}}, summary="把成功版本保存为 Sprite 资产",
)
async def publish_sprite(request: Request, service: Service) -> Response:
    """复制已验收版本的源码、参数契约与预览；相同来源与字段选择重复发布返回原 Sprite。"""
    message = pb.PublishSpriteRequest()
    await _body(request, message)
    summary = await sprites.publish(service, message)
    return _response(pb.PublishSpriteResponse(sprite=summary))


@router.get(
    "/styles/{style_id}", response_class=Response,
    responses={200: {"content": PROTOBUF_CONTENT}}, summary="读取 style_id 下的 Remotion 片段",
)
async def get_style_sprites(style_id: UUID, service: Service) -> Response:
    """从未保存过片段时返回空列表和版本 0；style_id 只用于关联，不要求存在对应的 IMS 模板。"""
    revision, placements, updated_at = await run_in_threadpool(sprites.get_bindings, service.store, style_id)
    return _response(pb.GetStyleSpritesResponse(
        bindings=_bindings_message(style_id, revision, placements, updated_at),
    ))


@router.post(
    "/styles/{style_id}", response_class=Response, openapi_extra=_REQUEST_BODY,
    responses={200: {"content": PROTOBUF_CONTENT}}, summary="整体替换 style_id 下的 Remotion 片段",
)
async def save_style_sprites(style_id: UUID, request: Request, service: Service) -> Response:
    """只校验 Remotion 自己的规则（资产存在、固定片段、起点与时长）后整体替换；expected_revision 过期返回 409。"""
    message = pb.SaveStyleSpritesRequest()
    await _body(request, message)
    if message.style_id != str(style_id):
        raise HTTPException(422, "请求体 style_id 必须与路径一致")
    await run_in_threadpool(sprites.validate_placements, service.store, message.placements)
    placements = [
        MessageToDict(item, preserving_proto_field_name=True) for item in message.placements
    ]
    revision, saved, updated_at = await run_in_threadpool(
        sprites.replace_bindings, service.store, style_id, placements, message.expected_revision,
    )
    return _response(pb.SaveStyleSpritesResponse(
        bindings=_bindings_message(style_id, revision, saved, updated_at),
    ))


@router.get("/{sprite_id}/preview", response_class=HTMLResponse, summary="打开已发布 Sprite 的交互预览")
async def sprite_preview(sprite_id: str, service: Service, overlay: bool = False) -> HTMLResponse:
    """返回发布时复制的隔离播放器页面，不依赖源聊天仍然存在。

    `overlay=true` 使用透明背景，供模板编辑把资产叠在预览画面上；再加页面参数 `sync=1`
    （新生成的资产支持）即由父页面通过消息逐帧驱动，无控制条和循环。
    """
    script = await run_in_threadpool(sprites.preview_script, service.store, sprite_id)
    return player_page(script, overlay=overlay)


@router.get("/{sprite_id}/fonts/{weight}", summary="读取预览字体")
async def sprite_font(sprite_id: str, weight: int, service: Service) -> FileResponse:
    """预览页按相对路径读取受管字体；资产不存在或字重不支持返回 404。

    发布记录不保存字体指纹，这里直接提供当前配置的受管字体，只用于编辑预览。
    """
    await run_in_threadpool(sprites.get, service.store, sprite_id)
    if weight not in {400, 700}:
        raise NotFound("font not found")
    font = service.settings.font_regular if weight == 400 else service.settings.font_bold
    return FileResponse(
        font, media_type="font/collection",
        headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"},
    )
