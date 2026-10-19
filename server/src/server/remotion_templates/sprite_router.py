"""Protobuf HTTP routes for the Sprite asset catalog under /api/sprites.

The router belongs to the main app (the Remotion app is mounted at /api/templates) and borrows the
Remotion runtime for the published Sprites. Where a Sprite is placed is part of the cloud template (its
`remotion` tracks), so there is no route for that here. `/render` lets the bus render one Sprite with its
content and time as an async task that shares the overlay renders' service.
Request and response bodies are `imv.sprite.v1` messages, mirroring the template router's framing.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from generated.imv.sprite.v1 import sprite_pb2 as pb
from google.protobuf.message import DecodeError, Message
from starlette.concurrency import run_in_threadpool

from . import sprites
from .api import app as remotion_templates_app
from .fonts import FontStore
from .routes import player_page, require_sandbox, runtime_for
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
    """返回可供模板编辑添加的不可变发布条目，不包含 TSX 源码。"""
    items = await run_in_threadpool(sprites.catalog)
    return _response(pb.ListSpritesResponse(sprites=items))


@router.post(
    "/publish", response_class=Response, openapi_extra=_REQUEST_BODY,
    responses={200: {"content": PROTOBUF_CONTENT}}, summary="把成功版本保存为 Sprite 资产",
)
async def publish_sprite(
    request: Request, service: Service, device: Annotated[UUID, Header(alias="X-IMV-Device")],
) -> Response:
    """复制本设备已验收版本的源码、参数契约与预览；相同来源与字段选择重复发布返回原 Sprite。

    新发布要在隔离浏览器里渲染参数边界样本，校验环境不可用时先以 503 拒绝；其他设备的版本视为不存在。
    """
    require_sandbox(service)
    message = pb.PublishSpriteRequest()
    await _body(request, message)
    summary = await sprites.publish(service, message, device)
    return _response(pb.PublishSpriteResponse(sprite=summary))


@router.post(
    "/render", response_class=Response, openapi_extra=_REQUEST_BODY,
    responses={200: {"content": PROTOBUF_CONTENT}}, summary="受理单个 Sprite 的渲染",
)
async def render_sprite(request: Request, service: Service) -> Response:
    """按 SpriteRenderInput 的 content（标题 text 或字幕 cues）、style 覆盖与 time 异步渲染整个效果的一个透明 WebM，转存 ZOS 后回调。

    超过 30 秒的效果由服务端分块渲染再拼接，总线不分段。输入不合发布契约或时长返回 422，
    ZOS 或隔离渲染环境（较长效果还需 ffmpeg）不可用返回 503。
    """
    message = pb.SpriteRenderInput()
    await _body(request, message)
    overlays = service.overlays
    overlays.resume()
    return _response(pb.SpriteRenderAccepted(task_id=await overlays.accept_sprite(message)))


@router.get("/render/{task_id}", response_class=Response, responses={200: {"content": PROTOBUF_CONTENT}}, summary="查询单个 Sprite 的渲染任务")
async def get_sprite_render(task_id: UUID, service: Service) -> Response:
    """成功时带 ZOS 公开地址；失败仍为 200 并携带稳定错误码与脱敏说明。"""
    overlays = service.overlays
    overlays.resume()
    return _response(await run_in_threadpool(overlays.get_sprite, str(task_id)))


@router.get("/{sprite_id}/preview", response_class=HTMLResponse, summary="打开已发布 Sprite 的交互预览")
async def sprite_preview(sprite_id: str, service: Service, overlay: bool = False, measure: bool = False,
                         hide_keyword: bool = False) -> HTMLResponse:
    """返回发布时复制的隔离播放器页面，不依赖源聊天仍然存在；副本丢失时按发布源码重建。

    `overlay=true` 使用透明背景，供模板编辑把资产叠在预览画面上；再加页面参数 `sync=1`
    （新生成的资产支持）即由父页面通过消息逐帧驱动，无控制条和循环。句中原位的关键词预览时，
    `measure=true` 逐帧回报标记的关键词位置与自身文字位置，`hide_keyword=true` 隐藏正文里标记的关键词（保留占位）。
    """
    script, host = await sprites.preview_script(service, sprite_id)
    return player_page(script, overlay=overlay, host=host, fonts=FontStore.for_settings(service.settings).listing(),
                       measure=measure, hide_keyword=hide_keyword)


@router.get("/{sprite_id}/fonts/{weight}", summary="读取预览字体")
async def sprite_font(sprite_id: str, weight: int, service: Service) -> FileResponse:
    """预览页按相对路径读取受管字体；资产不存在或字重不支持返回 404。

    发布记录不保存字体指纹，这里直接提供当前配置的受管字体，只用于编辑预览。
    """
    await run_in_threadpool(sprites.get, sprite_id)
    if weight not in {400, 700}:
        raise NotFound("font not found")
    font = service.settings.font_regular if weight == 400 else service.settings.font_bold
    return FileResponse(
        font, media_type="font/collection",
        headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"},
    )


@router.post(
    "/{sprite_id}/rename", response_class=Response, openapi_extra=_REQUEST_BODY,
    responses={200: {"content": PROTOBUF_CONTENT}}, summary="重命名 Sprite 资产",
)
async def rename_sprite(sprite_id: str, request: Request) -> Response:
    """只修改目录里显示的名称；资产库共享，任何人都可改，被模板使用的资产也可以改。

    名称去除首尾空白后需为 1～100 个字符，不要求唯一，否则 422；不存在返回 404。代码、参数与预览不变。
    """
    message = pb.RenameSpriteRequest()
    await _body(request, message)
    summary = await run_in_threadpool(sprites.rename, sprite_id, message.name)
    return _response(pb.RenameSpriteResponse(sprite=summary))


@router.delete("/{sprite_id}", status_code=204, summary="删除未被模板使用的 Sprite 资产")
async def delete_sprite(sprite_id: str, service: Service) -> Response:
    """删除资产记录与预览包；资产库共享，任何人都可删除。

    仍被云端模板的 `remotion` 轨道引用时返回 409 并列出模板名称，需先在模板里移除；不存在返回 404。
    源聊天与版本不受影响，之后可以重新保存到资产。
    """
    from ..template.store import templates_using

    try:
        identifier = str(UUID(sprite_id))
    except ValueError:
        raise NotFound("sprite not found") from None
    used_by = await run_in_threadpool(templates_using, identifier)
    # A preview rebuilding the copy finishes first, so it cannot write the bundle back after the deletion.
    async with sprites.bundle_lock(identifier):
        await run_in_threadpool(sprites.remove, service.store, identifier, used_by)
    return Response(status_code=204)
