"""纯素材请求及探测边界；单测隔离进程，真实媒体用例仅访问回环 HTTP。

执行 uv run --locked pytest tests/test_video_composition_media.py；真实用例需要 FFmpeg/FFprobe。
"""

import asyncio
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import shutil
import subprocess
from threading import Thread
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
    ({"streams": [{"duration": "2.5", "tags": {"DURATION": "00:00:20.000"}}], "format": {"duration": "20"}}, 0, 2.5),
    ({"streams": [{"tags": {"DURATION": "00:00:03.125000000"}}]}, 0, 3.125),
    ({"streams": [{"duration": "N/A", "start_time": "1", "tags": {"DURATION": "00:00:04.000"}}]}, 0, 3),
    ({"streams": [{"start_time": "N/A", "tags": {"DURATION": "01:02:03.5"}}]}, 0, 3723.5),
    ({"streams": [{"tags": {"DURATION-eng": "00:00:03.125000000"}}]}, 0, 3.125),
    ({"streams": [{"start_time": "1", "tags": {"dUrAtIoN-Zh": "00:00:04"}}]}, 0, 3),
    ({"streams": [{"tags": {"DURATION-eng": "00:00:20", "duration": "00:00:03"}}]}, 0, 3),
    ({"streams": [{"duration": "2.5", "tags": {"DURATION-eng": "00:00:20"}}]}, 0, 2.5),
    ({"streams": [{"tags": {"DURATION-eng": "00:00:NaN"}}]}, 0, None),
    ({"streams": [{"tags": {"DURATION-eng": "00:00:03", "DURATION": "invalid"}}]}, 0, None),
    ({"streams": [{"tags": {"DURATION-other": "00:00:03"}}]}, 0, None),
    ({"streams": [{"tags": {"DURATION": "00:60:00"}}]}, 0, None),
    ({"streams": [{"tags": {"DURATION": "00:00:NaN"}}]}, 0, None),
    ({"streams": [{"start_time": "3", "tags": {"DURATION": "00:00:03"}}]}, 0, None),
    ({"streams": [{}], "format": {"duration": "20"}}, 0, None),
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
        assert args[args.index("-show_entries") + 1] == "stream=duration,start_time:stream_tags"
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


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="需要 FFmpeg 和 FFprobe 探测真实素材")
@pytest.mark.anyio
@pytest.mark.parametrize("extension,video_codec,audio_codec,start,tag", [
    ("mp4", "mpeg4", "aac", 0, None), ("webm", "libvpx", "libopus", 0, None),
    ("webm", "libvpx-vp9", "libopus", 0, None), ("mkv", "ffv1", "pcm_s16le", 0, None),
    ("mkv", "ffv1", "pcm_s16le", 1, None), ("flv", "flv", "libmp3lame", 0, None),
    ("mkv", "ffv1", None, 0, "DURATION-eng"), ("mkv", "ffv1", None, 1, "DURATION-zh"),
])
async def test_real_video_probe_uses_video_duration(tmp_path, monkeypatch, extension, video_codec, audio_codec, start, tag):
    """真实 HTTP 素材覆盖长音轨、语言标签及偏移；FLV 无轨时长时明确失败。"""
    for key in list(os.environ):
        if key.lower().endswith("_proxy"):
            monkeypatch.delenv(key)
    path = tmp_path / f"sample.{extension}"
    # 管道输出避免 Matroska 自动补写普通 DURATION，确保样本仅依赖语言标签。
    output_args = (["-metadata:s:v:0", f"{tag}=00:00:0{3 + start}.000000000", "-f", "matroska", "pipe:1"]
                   if tag else [str(path)])
    audio_args = (["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=6", "-map", "0:v:0", "-map", "1:a:0",
                   "-c:a", audio_codec] if audio_codec else [])
    encoded = subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-itsoffset", str(start),
        "-f", "lavfi", "-i", "color=c=red:s=160x90:r=10:d=3",
        *audio_args, "-c:v", video_codec, "-threads", "1", *output_args,
    ], check=True, capture_output=True, timeout=30)
    if tag:
        path.write_bytes(encoded.stdout)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(tmp_path)))
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/{path.name}"
        if extension == "flv":
            with pytest.raises(CompositionError) as caught:
                await media.video_duration(url, 10)
            assert caught.value.error["code"] == "material_probe_failed"
            assert "视频流缺少有效 duration 或 DURATION 标签" in json.dumps(exception_details(caught.value), ensure_ascii=False)
        else:
            assert await media.video_duration(url, 10) == pytest.approx(3, abs=0.001)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


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
