# 验证字体白名单、真实 Protobuf、MySQL API 保存及 IMS 时间线；HTTP 用例通过 IMV_FONT_TEST_API 指定独立测试服务。

import json
import os
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from generated.imv.template.v1 import template_pb2 as pb
from google.protobuf.json_format import ParseDict
from pydantic import ValidationError

from server.template.schema import EffectTemplateEditor
from server.video_composition.timeline import build_timeline
from .template_wire import detail_template, saved_template

FONTS = json.loads((Path(__file__).parents[1] / "src/server/template/fonts.json").read_text())


@pytest.mark.parametrize("font", FONTS, ids=[font["label"] for font in FONTS])
def test_font_schema_and_protobuf(font):
    """十一种 IMS 字体经过模型、JSON 和真实 Protobuf 编解码后保持原值。"""
    editor = EffectTemplateEditor(title_font=font["value"], subtitle_font="Source Han Serif CN")
    message = ParseDict(editor.model_dump(mode="json", by_alias=True), pb.EffectTemplateEditor())
    restored = pb.EffectTemplateEditor.FromString(message.SerializeToString())
    assert restored.title_font == font["value"]
    assert restored.subtitle_font == "Source Han Serif CN"


@pytest.mark.parametrize("field", ["title_font", "subtitle_font"])
@pytest.mark.parametrize("value", ["", "Arial", "思源宋体", "Source Han Sans CN Normal", 42, None])
def test_font_schema_rejects_unsupported_values(field, value):
    """非法类型、未知字体和 SDK 专用标识在模型校验处拒绝。"""
    with pytest.raises(ValidationError):
        EffectTemplateEditor(**{field: value})


def test_missing_font_defaults():
    """旧记录缺少字体时继续使用普惠体。"""
    editor = EffectTemplateEditor.model_validate({})
    assert editor.title_font == editor.subtitle_font == "Alibaba PuHuiTi"


@pytest.fixture
def font_http_client():
    """直接访问独立测试服务；没有配置时跳过 HTTP 用例。"""
    address = os.environ.get("IMV_FONT_TEST_API")
    if not address:
        pytest.skip("需要 IMV_FONT_TEST_API 指向独立 MySQL 测试服务")
    with httpx.Client(base_url=address, timeout=20) as client:
        yield client


def font_payload(title_font=None, subtitle_font=None):
    """创建仅含标题和字幕的合法模板输入，未指定字体时省略对应字段。"""
    tracks = []
    for role, font in (("title", title_font), ("subtitle", subtitle_font)):
        editor = {"title": "", "subtitle": "", "bubbleText": "", role: "字体测试"}
        if font is not None:
            editor[f"{role}Font"] = font
        tracks.append({"id": role, "target": role, "start_mode": "seconds", "start": 0,
                       "duration": None, "editor": editor})
    return {"name": f"字体测试-{uuid4()}", "tracks": {"tracks": tracks}}


def post_fonts(client, payload):
    """将实际 Protobuf 字节发送到模板保存路由。"""
    message = ParseDict(payload, pb.SaveTemplateRequest())
    return client.post("/template", content=message.SerializeToString(),
                       headers={"Content-Type": "application/x-protobuf"})


@pytest.mark.parametrize("font", FONTS, ids=[font["label"] for font in FONTS])
def test_fonts_saved_read_and_used_by_ims(font_http_client, font):
    """真实 HTTP 保存、读取和更新两种字体，合成时间线保留 IMS 标识，拒绝错误更新。"""
    client = font_http_client
    payload = font_payload(font["value"], "Source Han Serif CN")
    response = post_fonts(client, payload)
    assert response.status_code == 201, response.text
    saved = saved_template(response)
    template_id = saved["template_id"]
    try:
        loaded = detail_template(client.get(f"/template/{template_id}"))
        assert loaded == saved
        assert loaded["tracks"][0]["editor"]["titleFont"] == font["value"]
        assert loaded["tracks"][1]["editor"]["subtitleFont"] == "Source Han Serif CN"
        before = deepcopy(loaded)
        timeline, _ = build_timeline(
            request={"text": "字幕", "title": "标题", "videoUrl": "https://example.com/video.mp4",
                     "audioUrl": "https://example.com/audio.wav", "styleId": template_id},
            template=loaded,
            segments=[{"segment_id": 1, "text": "字幕", "start_time": 0, "end_time": 2,
                       "keyword": "", "level": 1, "group_id": [1, 1]}],
            matches=[{"segment_id": 1, "text": "字幕", "start_time": 0, "end_time": 2,
                      "matched_candidate_url": None}],
            duration_ms=2000, width=1080, height=1920, fps=30,
        )
        text_fonts = {clip["Content"]: clip["Font"] for track in timeline["SubtitleTracks"]
                      for clip in track["SubtitleTrackClips"]}
        assert text_fonts == {"标题": font["value"], "字幕": "Source Han Serif CN"}
        assert loaded == before
        payload["template_id"] = template_id
        for field, index in (("titleFont", 0), ("subtitleFont", 1)):
            invalid = deepcopy(payload)
            invalid["tracks"]["tracks"][index]["editor"][field] = "Source Han Sans CN Normal"
            assert post_fonts(client, invalid).status_code == 422
            assert detail_template(client.get(f"/template/{template_id}")) == saved
        payload["tracks"]["tracks"][0]["editor"]["titleFont"] = "Source Han Serif CN"
        payload["tracks"]["tracks"][1]["editor"]["subtitleFont"] = font["value"]
        updated = post_fonts(client, payload)
        assert updated.status_code == 200
        loaded = detail_template(client.get(f"/template/{template_id}"))
        assert loaded["tracks"][0]["editor"]["titleFont"] == "Source Han Serif CN"
        assert loaded["tracks"][1]["editor"]["subtitleFont"] == font["value"]
    finally:
        assert client.delete(f"/template/{template_id}").status_code == 204


def test_old_request_restores_fonts_over_http(font_http_client):
    """不携带新增字段的旧客户端请求保存后返回普惠体默认值。"""
    response = post_fonts(font_http_client, font_payload())
    assert response.status_code == 201
    saved = saved_template(response)
    try:
        for track in saved["tracks"]:
            assert track["editor"]["titleFont"] == track["editor"]["subtitleFont"] == "Alibaba PuHuiTi"
    finally:
        assert font_http_client.delete(f"/template/{saved['template_id']}").status_code == 204
