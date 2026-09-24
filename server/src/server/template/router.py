# 模板 API：在既有 /template 路由传输 Protobuf，复用业务校验和数据库操作。

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from generated.imv.template.v1 import template_pb2 as pb
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.message import DecodeError, Message
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from . import store
from .schema import Template, TemplateSave

# 静态集合路径和 UUID 详情路径组成约定的四个接口，无效果目录接口。
router = APIRouter(prefix="/template", tags=["模板管理"])
PROTOBUF_MEDIA_TYPE = "application/x-protobuf"


def _template_message(template: Template) -> pb.TemplateRecord:
    """将经过业务校验的模板转换为生成的 Protobuf 消息。"""
    data = template.model_dump(mode="json")
    data["tracks"] = {"tracks": data["tracks"]}
    return ParseDict(data, pb.TemplateRecord())


def _protobuf_response(message: Message, status_code: int = 200) -> Response:
    """将生成的消息作为二进制响应发送，保留原有 HTTP 状态码。"""
    return Response(
        content=message.SerializeToString(),
        status_code=status_code,
        media_type=PROTOBUF_MEDIA_TYPE,
    )


@router.get("")
def list_templates() -> Response:
    """返回共享模板库的 Protobuf 列表，空库返回空消息。"""
    templates = store.list_templates()
    return _protobuf_response(pb.ListTemplatesResponse(
        templates=[_template_message(template) for template in templates],
    ))


@router.post("")
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
    return _protobuf_response(
        pb.SaveTemplateResponse(template=_template_message(result)),
        status_code=200 if validated.template_id else 201,
    )


@router.get("/{template_id}")
def get_template(template_id: UUID) -> Response:
    """按 URL 中的 UUID 返回模板及服务端效果快照。"""
    template = store.get_template(template_id)
    return _protobuf_response(pb.GetTemplateResponse(template=_template_message(template)))


@router.delete("/{template_id}", status_code=204)
def delete_template(template_id: UUID) -> None:
    """按 URL 中的 UUID 删除模板，成功返回空响应。"""
    store.delete_template(template_id)
