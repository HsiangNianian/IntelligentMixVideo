# 使用生成的消息类收发真实模板 HTTP 请求，供服务端用例核对 Protobuf 响应。

from generated.imv.template.v1 import template_pb2 as pb
from google.protobuf.json_format import MessageToDict, ParseDict
from httpx import Client, Response

from server.template.schema import Template


def post_template(client: Client, payload: dict) -> Response:
    """按原有 POST 地址编码并发送模板保存消息。"""
    data = {**payload, "tracks": {"tracks": payload["tracks"]}} if "tracks" in payload else payload
    message = ParseDict(data, pb.SaveTemplateRequest())
    return client.post(
        "/template",
        content=message.SerializeToString(),
        headers={"Content-Type": "application/x-protobuf"},
    )


def template_data(message: pb.TemplateRecord) -> dict:
    """将生成的模板消息转换为业务模型字段，便于检查服务端默认值。"""
    data = MessageToDict(message, preserving_proto_field_name=True)
    data["tracks"] = data["tracks"].get("tracks", [])
    for track in data["tracks"]:
        track.setdefault("duration", None)
    return Template.model_validate(data).model_dump(mode="json", by_alias=True)


def saved_template(response: Response) -> dict:
    """解码保存响应中的模板记录。"""
    return template_data(pb.SaveTemplateResponse.FromString(response.content).template)


def detail_template(response: Response) -> dict:
    """解码详情响应中的模板记录。"""
    return template_data(pb.GetTemplateResponse.FromString(response.content).template)


def listed_templates(response: Response) -> list[dict]:
    """解码列表响应中的全部模板记录。"""
    return [template_data(item) for item in pb.ListTemplatesResponse.FromString(response.content).templates]
