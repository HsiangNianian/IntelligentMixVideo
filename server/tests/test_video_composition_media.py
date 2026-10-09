"""纯素材请求及媒体探测边界；隔离子进程和网络，执行 uv run --locked pytest tests/test_video_composition_media.py。"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from server.video_composition import media
from server.video_composition.errors import CompositionError
from server.video_composition.execution_log import exception_details
from server.video_composition.schema import CompositionRequest, Material


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


@pytest.mark.anyio
@pytest.mark.parametrize("duration", [3, 6, 4.5])
async def test_images_cover_target_without_probing(monkeypatch, duration):
    """图片各三秒，等长完整使用、超长留给时间线裁尾，未用到的视频不访问。"""
    async def forbidden(*args):
        """图片和未使用素材均不应发起探测。"""
        pytest.fail("不应调用 FFprobe")

    monkeypatch.setattr(media, "video_duration", forbidden)
    materials = [Material(file_url="https://media.test/a.jpg", type="image") for _ in range(2)]
    materials.append(Material(file_url="https://media.test/unused.mp4", type="video"))
    assert await media.material_durations(materials, duration, 1) == ([3] if duration == 3 else [3, 3])


@pytest.mark.anyio
@pytest.mark.parametrize("payload,returncode,expected", [
    ({"streams": [{"duration": "2.5"}], "format": {"duration": "20"}}, 0, 2.5),
    ({"streams": []}, 0, None), ({"streams": [{"duration": "N/A"}]}, 0, None),
    ({"streams": [{"duration": "NaN"}]}, 0, None), ({"streams": [{"duration": "0"}]}, 0, None),
    ({"streams": [{"duration": "2"}]}, 1, None),
])
async def test_probe_uses_video_stream_and_rejects_unknown(monkeypatch, payload, returncode, expected):
    """只接受视频流有效时长，不把长音轨的容器时长、无视频或损坏输出当作可用素材。"""
    async def communicate():
        """模拟 ffprobe 的有限 JSON 输出。"""
        return json.dumps(payload).encode(), b""

    async def spawn(*args, **kwargs):
        """检查不经 shell 且限制协议，带签名地址作为单个参数保留。"""
        assert args[-1] == "https://media.test/a.mp4?token=a%2Fb&x=1"
        assert args[args.index("-select_streams") + 1] == "v:0"
        protocols = args[args.index("-protocol_whitelist") + 1].split(",")
        assert "httpproxy" in protocols and "file" not in protocols
        assert kwargs["stderr"] == asyncio.subprocess.PIPE
        return SimpleNamespace(communicate=communicate, returncode=returncode)

    monkeypatch.setattr(media.asyncio, "create_subprocess_exec", spawn)
    if expected is None:
        with pytest.raises(CompositionError, match="无法读取素材视频"):
            await media.video_duration("https://media.test/a.mp4?token=a%2Fb&x=1", 1)
    else:
        assert await media.video_duration("https://media.test/a.mp4?token=a%2Fb&x=1", 1) == expected


@pytest.mark.anyio
async def test_probe_failure_keeps_private_sanitized_diagnostics(monkeypatch):
    """探测失败保留退出码和 stderr 供后台排障，公开错误固定，日志继续去除 URL 签名。"""
    async def communicate():
        """返回代理协议失败和带签名地址，模拟 FFprobe 的真实诊断格式。"""
        return b"{}", b"Protocol 'httpproxy' not on whitelist!\nhttps://media.test/a.mp4?token=private-signature: Invalid argument\n"

    async def spawn(*args, **kwargs):
        """子进程失败，不发出真实网络请求。"""
        return SimpleNamespace(communicate=communicate, returncode=1)

    monkeypatch.setattr(media.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(CompositionError) as caught:
        await media.video_duration("https://media.test/a.mp4?token=private-signature", 1)
    assert caught.value.error == {"code": "material_probe_failed", "message": "无法读取素材视频的有效时长", "stage": "assembling"}
    details = json.dumps(exception_details(caught.value), ensure_ascii=False)
    assert "httpproxy" in details and "退出码 1" in details and "private-signature" not in details


@pytest.mark.anyio
@pytest.mark.parametrize("cancel", [False, True])
async def test_probe_timeout_and_cancellation_reap_process(monkeypatch, cancel):
    """超时或关闭任务时杀死并回收 FFprobe；取消仍向调用方传播。"""
    started = asyncio.Event()
    calls = []
    process = SimpleNamespace(returncode=None)

    async def communicate():
        """模拟阻塞网络读取。"""
        started.set()
        await asyncio.Event().wait()

    async def wait():
        """确认 kill 后确实等待进程退出。"""
        calls.append("wait")
        process.returncode = -9

    async def spawn(*args, **kwargs):
        """返回受控子进程，禁止真实外部连接。"""
        return process

    process.communicate, process.wait = communicate, wait
    process.kill = lambda: calls.append("kill")
    monkeypatch.setattr(media.asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(media.video_duration("https://media.test/a.mp4", 10 if cancel else 0.01))
    await started.wait()
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else CompositionError):
        await task
    assert calls == ["kill", "wait"]
