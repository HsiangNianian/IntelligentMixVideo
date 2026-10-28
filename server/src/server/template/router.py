# 模板 API：接收 Protobuf 保存请求，默认返回 JSON，兼容旧客户端明确请求的 Protobuf 响应。

from uuid import UUID
from typing import Annotated
import logging
import subprocess

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from generated.imv.template.v1 import template_pb2 as pb
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.message import DecodeError, Message
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from . import store
from .schema import Template, TemplateSave
from .preview_mask import PreviewMask, mask_video

# 静态预览资源必须放在 UUID 详情路径之前，无效果目录接口。
router = APIRouter(prefix="/template", tags=["模板管理"])
PROTOBUF_MEDIA_TYPE = "application/x-protobuf"
# 保存请求仍使用二进制，成功响应默认 JSON，并保留旧客户端的二进制格式。
PROTOBUF_CONTENT = {PROTOBUF_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}}
RESPONSE_CONTENT = {"application/json": {"schema": {"type": "object"}}, **PROTOBUF_CONTENT}


def _template_message(template: Template) -> pb.TemplateRecord:
    """将经过业务校验的模板转换为生成的 Protobuf 消息。"""
    data = template.model_dump(mode="json")
    data["tracks"] = {"tracks": data["tracks"]}
    return ParseDict(data, pb.TemplateRecord())


def _template_response(request: Request, message: Message, status_code: int = 200) -> Response:
    """旧客户端的单一 Protobuf Accept 保留二进制，其余请求默认 JSON，缓存按 Accept 区分。"""
    if request.headers.get("accept", "").strip().lower() == PROTOBUF_MEDIA_TYPE:
        return Response(
            content=message.SerializeToString(), media_type=PROTOBUF_MEDIA_TYPE,
            status_code=status_code, headers={"Vary": "Accept"},
        )
    return JSONResponse(
        content=MessageToDict(message, always_print_fields_with_no_presence=True),
        status_code=status_code, headers={"Vary": "Accept"},
    )


@router.get("", response_class=JSONResponse, responses={200: {"content": RESPONSE_CONTENT}})
def list_templates(request: Request) -> Response:
    """默认返回共享模板库的 JSON 列表，空库返回空数组。"""
    templates = store.list_templates()
    return _template_response(request, pb.ListTemplatesResponse(
        templates=[ParseDict(template.model_dump(mode="json", exclude_none=True), pb.TemplateSummary()) for template in templates],
    ))


@router.post(
    "",
    response_class=JSONResponse,
    responses={
        200: {"content": RESPONSE_CONTENT},
        201: {"content": RESPONSE_CONTENT},
    },
    openapi_extra={"requestBody": {"required": True, "content": PROTOBUF_CONTENT}},
)
async def save_template(request: Request) -> Response:
    """解析 Protobuf 保存消息；无 ID 创建，有 ID 完整更新。"""
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != PROTOBUF_MEDIA_TYPE:
        raise HTTPException(status_code=415, detail="请使用 application/x-protobuf")
    message = pb.SaveTemplateRequest()
    try:
        message.ParseFromString(await request.body())
    except DecodeError as exc:
        raise HTTPException(status_code=400, detail="Protobuf 请求内容无效") from exc
    data = MessageToDict(message, preserving_proto_field_name=True)
    if "tracks" in data:
        data["tracks"] = data["tracks"].get("tracks", [])
        for track in data["tracks"]:
            track.setdefault("duration", None)
    try:
        validated = TemplateSave.model_validate(data)
    except ValidationError as exc:
        errors = [{**error, "loc": ("body", *error["loc"])} for error in exc.errors()]
        raise RequestValidationError(errors) from exc
    result = await run_in_threadpool(store.save_template, validated)
    return _template_response(
        request, pb.SaveTemplateResponse(template=_template_message(result)),
        status_code=200 if validated.template_id else 201,
    )


@router.get("/preview-mask", response_class=Response)
def preview_mask(params: Annotated[PreviewMask, Query()]) -> Response:
    """在线程池里生成 SDK 的圆角蒙版；只接受有界几何，不访问用户视频或数据库。"""
    try:
        content = mask_video(**params.model_dump())
    except (OSError, subprocess.SubprocessError) as exc:
        logging.getLogger(__name__).exception("Preview mask encoding failed")
        raise HTTPException(status_code=503, detail="圆角预览蒙版生成失败，请检查服务端 FFmpeg 后重试") from exc
    return Response(content, media_type="video/mp4", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/{template_id}", response_class=JSONResponse, responses={200: {"content": RESPONSE_CONTENT}})
def get_template(template_id: UUID, request: Request) -> Response:
    """按 URL 中的 UUID 返回模板及服务端效果快照。"""
    template = store.get_template(template_id)
    return _template_response(request, pb.GetTemplateResponse(template=_template_message(template)))


@router.delete("/{template_id}", status_code=204)
def delete_template(template_id: UUID) -> None:
    """按 URL 中的 UUID 删除模板，成功返回空响应。"""
    store.delete_template(template_id)
