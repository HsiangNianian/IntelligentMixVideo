"""Remotion asset publication, catalog and sandboxed preview routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from generated.imv.sprite.v1 import sprite_pb2 as pb
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


@router.get("", response_class=Response, responses={200: {"content": PROTOBUF_CONTENT}}, summary="查询已发布的 Sprite 目录")
async def list_sprites(service: Service) -> Response:
    """返回不可变发布资产目录，不包含 TSX 源码。"""
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


@router.get("/{sprite_id}/preview", response_class=HTMLResponse, summary="打开已发布 Sprite 的交互预览")
async def sprite_preview(sprite_id: str, service: Service, overlay: bool = False) -> HTMLResponse:
    """返回发布时复制的隔离播放器页面，不依赖源聊天仍然存在。

    `overlay=true` 使用透明背景，普通预览保留检查棋盘格。
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
