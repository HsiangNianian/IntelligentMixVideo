"""纯素材请求边界测试：验证模式推导、文案回退和字段校验，不访问外部服务。"""

import pytest

from server.video_composition.schema import CompositionRequest


@pytest.mark.parametrize("text,copy,expected", [
    ("优先正文", "备用文案", "优先正文"), (None, "备用文案", "备用文案"),
    ("", "备用文案", "备用文案"), (" \n", "备用文案", "备用文案"), (None, None, None), (" ", " ", None),
])
def test_text_copy_precedence(composition_case, text, copy, expected):
    """空值回退 copy，非空 text 优先；业务正文统一存 text，不保留兼容字段。"""
    request = CompositionRequest.model_validate({**composition_case["request"], "videoUrl": None,
        "materials": [{"fileUrl": "https://media.test/a.jpg", "type": "image"}], "text": text, "copy": copy})
    assert request.text == expected and "copy" not in request.model_dump(by_alias=True)



@pytest.mark.parametrize("patch", [
    {"materials": []}, {"materials": None},
    {"processRules": {}}, {"processRules": {"videoDuration": 0}},
    {"processRules": {"videoDuration": float("inf")}}, {"processRules": {"videoDuration": -1}},
    {"packRules": {"backgroundMusic": {"audioSwitch": True, "audioUrl": ""}}},
    {"audioUrl": "http://media.test/voice.wav"},
])
def test_material_mode_invalid_contract_rejected(composition_case, patch):
    """纯素材仍校验非空素材、目标时长和开启的音乐，不放松有声配音要求。"""
    with pytest.raises(ValueError):
        CompositionRequest.model_validate({
            "styleId": composition_case["request"]["styleId"], "processRules": {"videoDuration": 10.5},
            "materials": [{"fileUrl": "https://media.test/a.jpg", "type": "image"}], **patch})



@pytest.mark.parametrize("urls,expected", [
    ({"videoUrl": "https://media.test/avatar.mp4", "audioUrl": "https://media.test/voice.wav"}, "standard"),
    ({"audioUrl": "https://media.test/voice.wav"}, "materials_voice"),
    ({"videoUrl": None, "audioUrl": "https://media.test/voice.wav"}, "materials_voice"),
    ({}, "materials_silent"), ({"videoUrl": None, "audioUrl": None}, "materials_silent"),
])
def test_mode_is_internal_and_inferred_from_media_urls(composition_case, urls, expected):
    """缺省和 null 均视为无地址；背景音乐、文案、时长和外传模式不能覆盖分流，快照恢复一致。"""
    body = {"styleId": composition_case["request"]["styleId"], "text": "测试正文",
            "materials": [{"fileUrl": "https://media.test/a.jpg", "type": "image"}],
            "processRules": {"videoDuration": 10.5},
            "packRules": {"backgroundMusic": {"audioSwitch": True, "audioUrl": "https://media.test/bgm.mp3"}},
            **urls}
    request = CompositionRequest.model_validate(body)
    assert request.composition_mode == expected
    for field in ("compositionMode", "composition_mode"):
        overridden = CompositionRequest.model_validate({**body, field: "unknown"})
        assert overridden.composition_mode == expected
    saved = request.model_dump(mode="json", by_alias=True)
    assert "compositionMode" not in saved and "composition_mode" not in saved
    assert CompositionRequest.model_validate(saved).composition_mode == expected
