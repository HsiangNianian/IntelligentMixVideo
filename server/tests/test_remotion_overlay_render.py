"""Remotion 叠加视频：时长优先级、摆放规划、任务流转、ZOS 转存与回调的离线回归。

默认不启动浏览器、ffprobe 或 ZOS：渲染器、探测与上传由替身承担，任务表使用临时 SQLite。
`real_` 用例在 Linux 上经隔离沙箱真实渲染，需 IMV_TEST_RENDERER=1（见 backend-integration 工作流）。
在 server/ 目录执行 `uv run --locked pytest tests/test_remotion_overlay_render.py -v`。
"""

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from generated.imv.sprite.v1 import sprite_pb2 as pb
from google.protobuf.json_format import MessageToDict
from pydantic import TypeAdapter

from server.app import app
from server.remotion_templates import overlay_render, overlay_service
from server.remotion_templates.overlay_render import (
    OverlayError, overlay_cpu_seconds, plan_overlay, render_overlay, resolve_total_seconds,
)
from server.remotion_templates.overlay_router import overlay_renders
from server.remotion_templates.overlay_service import OverlayRenders
from server.remotion_templates.renderer import Renderer
from server.remotion_templates.settings import Settings
from server.remotion_templates.store import Store
from server.video_composition.schema import MediaURL

CODE = "export default function Sprite() { return null; }"
# 自动隔离夹具会清除 IMV_*，所以在导入时读取本次运行配置的渲染器位置。
RENDERER_PATHS = {name: os.environ.get(name) for name in ("IMV_BROWSER_EXECUTABLE", "IMV_FONT_REGULAR", "IMV_FONT_BOLD")}


def sprite(sprite_id: str, *, width=1080, height=1920, fps=30, frames=60, code=CODE) -> pb.PublishedSprite:
    """构造一个已发布 Sprite：默认 2 秒、无可编辑参数。"""
    return pb.PublishedSprite(
        sprite_id=sprite_id, name=sprite_id, kind=pb.SPRITE_KIND_TEXT, tsx_code=code,
        canvas=pb.SpriteCanvas(width=width, height=height, fps=fps, preview_frames=frames),
        config_schema_json=json.dumps({"type": "object", "properties": {}}),
        default_config_json="{}",
    )


def placement(sprite_id: str, *, start=0.0, duration=None, order=0) -> pb.SpritePlacement:
    """构造一个摆放；duration 缺省时使用资产自身时长。"""
    item = pb.SpritePlacement(id=f"p{order}", sprite_id=sprite_id, start_mode="seconds", start=start, order=order)
    if duration is not None:
        item.duration = duration
    return item


A, B = sprite("a"), sprite("b")
PUBLICATIONS = {"a": A, "b": B}


# 场景：有 TTS 音频时无视请求 duration，以探测到的完整音频时长为准。
def test_audio_duration_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """音频时长优先；无音频用 duration；都没有取摆放的最晚结束；全无则报错。"""
    async def probed(url: str, timeout: float) -> float:
        assert url == "https://x/tts.mp3"
        return 7.5

    monkeypatch.setattr(overlay_render, "audio_duration", probed)
    placements = [placement("a", start=1), placement("b", start=4, duration=3)]

    async def total(audio, duration):
        return await resolve_total_seconds(audio, duration, placements, PUBLICATIONS, 5)

    assert asyncio.run(total("https://x/tts.mp3", 99)) == 7.5
    assert asyncio.run(total(None, 12)) == 12
    assert asyncio.run(total(None, None)) == 7  # max(1+2, 4+3)
    with pytest.raises(OverlayError, match="无法确定成片时长"):
        asyncio.run(resolve_total_seconds(None, None, [], PUBLICATIONS, 5))


# 场景：摆放按 order 叠放，超出结尾截短、起点越界丢弃。
def test_plan_clips_and_orders_placements() -> None:
    """帧范围以统一画布 fps 计算，越界起点被丢弃，超出结尾的片段保留完整时长（不改变资产内部时间轴）。"""
    placements = [
        placement("b", start=4.8, order=1),  # 2s 时长保持完整，由 6s 总长截断
        placement("a", start=0, order=0),
        placement("a", start=6, order=2),  # 起点等于结尾，丢弃
    ]
    overlay = plan_overlay(placements, PUBLICATIONS, 6, 120)
    assert (overlay.width, overlay.height, overlay.fps, overlay.frames) == (1080, 1920, 30, 180)
    assert [(i["start_frame"], i["duration_frames"]) for i in overlay.instances] == [(0, 60), (144, 60)]
    assert all(set(item) == {"code", "config", "start_frame", "duration_frames"} for item in overlay.instances)
    assert overlay.span == 204  # 144 + 60：合成必须覆盖最晚片段结尾，否则该片段的 Sequence 会被截短


@pytest.mark.parametrize("placements, total, code", [
    ([], 5, "no_placements"),
    ([placement("a"), placement("c")], 5, "canvas_mismatch"),
    ([placement("a")], 200, "duration_too_long"),
    ([placement("a", start=9)], 5, "no_placements"),
])
def test_plan_rejects_invalid_requests(placements, total, code) -> None:
    """空摆放、画布不一致、超长和全部越界都以稳定错误码拒绝。"""
    publications = {**PUBLICATIONS, "c": sprite("c", width=720)}
    with pytest.raises(OverlayError) as caught:
        plan_overlay(placements, publications, total, 120)
    assert caught.value.code == code


class FakeRenderer:
    """记录 worker 请求并写出占位 WebM；可设置失败。"""

    def __init__(self, settings: Settings) -> None:
        """settings 提供字体路径与超时。"""
        self.settings = settings
        self.requests: list[dict] = []
        self.failure: Exception | None = None
        self.cpu_seconds: int | None = None

    def worker_browser_path(self) -> str:
        """返回固定浏览器路径。"""
        return "/browser"

    async def run_worker(self, directory: Path, *, worker=None, timeout_seconds=None, cpu_seconds=None) -> dict:
        """校验 worker 名称，保存请求与沙箱 CPU 上限并产出文件。"""
        assert worker == "overlay-worker.mjs"
        self.cpu_seconds = cpu_seconds
        self.requests.append(json.loads((directory / "request.json").read_text()))
        if self.failure:
            raise self.failure
        (directory / "overlay.webm").write_bytes(b"webm")
        return {}


@pytest.fixture
def service(tmp_path, monkeypatch):
    """带临时任务表、假渲染器、假 ZOS 的任务服务；返回服务、渲染器和上传记录。"""
    font = tmp_path / "font.ttc"
    font.write_bytes(b"font")
    settings = Settings(_env_file=None, data_dir=tmp_path / "state", font_regular=font, font_bold=font)
    store = Store(settings.data_dir)
    store.initialize()
    renderer = FakeRenderer(settings)
    uploads: list[tuple] = []
    bindings = [MessageToDict(item, preserving_proto_field_name=True) for item in (
        placement("a", start=0, order=0), placement("b", start=1, order=1),
    )]

    async def probed(path, overlay) -> None:
        """替身探测：视为规格一致。"""

    def upload(files, zos, timeout):
        """记录上传并返回公开地址。"""
        uploads.append((files[0][1], files[0][2], files[0][0].read_bytes()))
        return [f"https://zos.example/{files[0][1]}"]

    monkeypatch.setattr(overlay_render, "probe_overlay", probed)
    monkeypatch.setattr(overlay_service, "environment_problem", lambda settings: None)
    monkeypatch.setattr(overlay_service, "upload_public", upload)
    monkeypatch.setattr(overlay_service, "ZosSettings", lambda: SimpleNamespace())
    monkeypatch.setattr(overlay_service.sprites, "get_bindings", lambda store, style_id: (1, bindings, None))
    monkeypatch.setattr(overlay_service.sprites, "get", lambda store, sprite_id: PUBLICATIONS[sprite_id])
    return OverlayRenders(store, renderer, settings), renderer, uploads


async def finish(overlays: OverlayRenders) -> None:
    """等待全部后台任务（渲染与通知）结束。"""
    while overlays.tasks:
        await asyncio.gather(*list(overlays.tasks))


@pytest.fixture
def callbacks(monkeypatch) -> SimpleNamespace:
    """替换回调使用的 httpx 客户端：posted 记录每次 POST 的地址与请求体，respond 决定响应（默认 200）。"""
    state = SimpleNamespace(posted=[])

    async def ok():
        """默认回应 200。"""
        return httpx.Response(200)

    state.respond = ok

    class Client:
        """记录 POST 的 httpx 替身。"""

        def __init__(self, **_): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *_): ...

        async def post(self, url, json):
            state.posted.append((url, json))
            return await state.respond()

    monkeypatch.setattr(overlay_service.httpx, "AsyncClient", Client)
    return state


@pytest.fixture
def fast_retries(monkeypatch) -> None:
    """把 5/15/45 秒的通知重试间隔缩成毫秒，用例不等待。"""
    monkeypatch.setattr(overlay_service, "NOTIFICATION_RETRY_DELAYS", (0.001, 0.001, 0.001))


def notification_of(overlays: OverlayRenders, task_id: str) -> str | None:
    """任务行里记录的回调状态（pending/sent/failed）。"""
    with overlays.store.connection() as db:
        return json.loads(db.execute("SELECT data FROM overlay_renders WHERE id=?", (task_id,)).fetchone()["data"]).get("notification")


# 场景：成功路径按 duration 渲染，转存 ZOS 指定键，并向回调发送四字段通知。
def test_task_renders_uploads_and_notifies(service, callbacks) -> None:
    """GET 返回 ZOS 地址；回调体为 taskId/status/videoUrl/errorMessage，源文件在任务结束后被清理。"""
    overlays, renderer, uploads = service

    async def scenario():
        task_id = await overlays.accept(uuid4(), None, 4.0, "https://bus.example/cb")
        await finish(overlays)
        return task_id

    task_id = asyncio.run(scenario())
    view = overlays.get(task_id)
    key = f"imv/overlay_render/{task_id}.webm"
    assert view["status"] == "succeeded" and view["result"] == {"videoUrl": f"https://zos.example/{key}"}
    assert uploads == [(key, "video/webm", b"webm")]
    assert renderer.requests[0]["composition"] == {
        "width": 1080, "height": 1920, "fps": 30, "duration_in_frames": 120, "frames": 120,
    }
    assert [i["start_frame"] for i in renderer.requests[0]["instances"]] == [0, 30]
    assert callbacks.posted == [("https://bus.example/cb", {
        "taskId": task_id, "status": "succeed", "videoUrl": f"https://zos.example/{key}", "errorMessage": None,
    })]
    assert notification_of(overlays, task_id) == "sent"
    assert not (overlays.store.root / "overlays" / task_id).exists()


# 场景：回调地址通过 schema 校验却无法被 httpx 解析（补零 IPv4、控制字符）时，终态也会记录，不卡在 pending。
@pytest.mark.parametrize("url", ["http://192.168.001.010:8080/cb", "http://bus.example/cb\x7f"])
def test_unparseable_callback_url_ends_failed(service, fast_retries, caplog, url) -> None:
    """httpx.InvalidURL 不是 HTTPError：四次尝试后记为 failed，任务仍是 succeeded，日志有放弃记录。"""
    overlays, _, _ = service
    TypeAdapter(MediaURL).validate_python(url)  # 受理前的 schema 放行了这个地址

    async def scenario():
        task_id = await overlays.accept(uuid4(), None, 3.0, url)
        await finish(overlays)  # 后台协程若抛出未处理异常，这里会重新抛出
        return task_id

    with caplog.at_level(logging.WARNING, logger=overlay_service.logger.name):
        task_id = asyncio.run(scenario())
    assert overlays.get(task_id)["status"] == "succeeded" and notification_of(overlays, task_id) == "failed"
    assert caplog.text.count("InvalidURL") == 4 and "重试 4 次后放弃" in caplog.text


# 场景：回调端点返回非 2xx 时按间隔重试，成功即停；全部失败则记录并放弃，日志写明状态码。
def test_callback_retries_error_statuses(service, callbacks, fast_retries, caplog) -> None:
    """两次 500 后第三次 200 记为 sent；四次都 500 记为 failed，且日志含 HTTP 500 与放弃记录。"""
    overlays, _, _ = service
    answers = iter([500, 500, 200])

    async def flaky():
        """前两次 500，之后 200。"""
        return httpx.Response(next(answers))

    callbacks.respond = flaky

    async def scenario():
        task_id = await overlays.accept(uuid4(), None, 3.0, "https://bus.example/cb")
        await finish(overlays)
        return task_id

    with caplog.at_level(logging.WARNING, logger=overlay_service.logger.name):
        task_id = asyncio.run(scenario())
    assert len(callbacks.posted) == 3 and notification_of(overlays, task_id) == "sent"
    assert caplog.text.count("收到 HTTP 500") == 2

    async def always_500():
        """始终 500。"""
        return httpx.Response(500)

    callbacks.respond, callbacks.posted[:] = always_500, []
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=overlay_service.logger.name):
        task_id = asyncio.run(scenario())
    assert len(callbacks.posted) == 4 and notification_of(overlays, task_id) == "failed"
    assert caplog.text.count("收到 HTTP 500") == 4 and "重试 4 次后放弃" in caplog.text


# 场景：回调端点发出响应头后迟迟不结束时，每次尝试都有整体期限，重试照常推进。
def test_callback_attempt_has_an_overall_deadline(service, callbacks, fast_retries, monkeypatch, caplog) -> None:
    """超时按阶段计算的客户端挡不住滴灌响应：整体期限到点后记录 TimeoutError 并进入下一次。"""
    overlays, _, _ = service
    monkeypatch.setattr(overlay_service, "HTTP_TIMEOUT_SECONDS", 0.05)

    async def hangs():
        """永不返回，代表响应迟迟不结束。"""
        await asyncio.Event().wait()

    callbacks.respond = hangs

    async def scenario():
        task_id = await overlays.accept(uuid4(), None, 3.0, "https://bus.example/cb")
        await finish(overlays)
        return task_id

    with caplog.at_level(logging.WARNING, logger=overlay_service.logger.name):
        task_id = asyncio.run(asyncio.wait_for(scenario(), 10))
    assert len(callbacks.posted) == 4 and notification_of(overlays, task_id) == "failed"
    assert caplog.text.count("TimeoutError") == 4


# 场景：片段超出成片结尾时，worker 请求中的合成仍覆盖最晚片段结尾，只渲染成片总帧数。
def test_request_spans_clips_cut_by_the_total(service) -> None:
    """成片 2 秒（60 帧）而片段 b 结束于第 90 帧：duration_in_frames 取 90，frames 仍为 60。"""
    overlays, renderer, _ = service

    async def scenario():
        await overlays.accept(uuid4(), None, 2.0, None)
        await finish(overlays)

    asyncio.run(scenario())
    composition = renderer.requests[0]["composition"]
    assert (composition["duration_in_frames"], composition["frames"]) == (90, 60)


# 场景：沙箱 CPU 上限默认沿用短 worker 的 300 秒，调用方可按自己的渲染期限放大。
def test_sandbox_command_applies_the_cpu_cap(tmp_path: Path) -> None:
    """prlimit 的 --cpu 取调用方给定的秒数；不给定时保持 300 秒。"""
    renderer = Renderer(Settings(_env_file=None, data_dir=tmp_path))
    assert "--cpu=300" in renderer.command(tmp_path)
    assert "--cpu=1234" in renderer.command(tmp_path, worker="overlay-worker.mjs", cpu_seconds=1234)


# 场景：叠加渲染的 CPU 上限随超时和核数放大，不会在超时前先结束健康的长渲染。
@pytest.mark.parametrize("timeout, cores, expected", [
    (3600, 8, 28800),  # 一小时期限内 8 核最多能用的 CPU 秒
    (600, None, 600),  # 核数未知时按单核
    (50, 2, 300),  # 短期限不低于短 worker 的既有上限
])
def test_overlay_cpu_cap_follows_the_deadline_and_cores(monkeypatch, timeout, cores, expected) -> None:
    """上限 = 期限 × 核数，且不低于 300 秒。"""
    monkeypatch.setattr(overlay_render.os, "cpu_count", lambda: cores)
    assert overlay_cpu_seconds(timeout) == expected


# 场景：任务把按渲染期限算出的 CPU 上限交给沙箱 worker。
def test_task_passes_the_overlay_cpu_cap_to_the_worker(service) -> None:
    """worker 收到 overlay_cpu_seconds(渲染超时)，而不是短 worker 的固定 300 秒。"""
    overlays, renderer, _ = service

    async def scenario():
        await overlays.accept(uuid4(), None, 2.0, None)
        await finish(overlays)

    asyncio.run(scenario())
    assert renderer.cpu_seconds == overlay_cpu_seconds(overlays.settings.overlay_render_timeout_seconds)


# 场景：渲染失败时任务为 failed，GET 和回调只含固定说明；渲染器输出只进服务端日志，不上传。
@pytest.mark.parametrize("failure, message", [
    (RuntimeError("Traceback at /home/secret/path token=abc123"), "叠加视频渲染失败"),
    (TimeoutError("isolated renderer worker timed out after 3600s"), "叠加视频渲染超时"),
])
def test_render_failure_is_sanitized(service, callbacks, caplog, failure, message) -> None:
    """错误码为 render_failed，说明是固定文案，不含本地路径或密钥；根因保留在服务端日志。"""
    overlays, renderer, uploads = service
    renderer.failure = failure

    async def scenario():
        task_id = await overlays.accept(uuid4(), None, 3.0, "https://bus.example/cb")
        await finish(overlays)
        return task_id

    with caplog.at_level(logging.WARNING, logger=overlay_service.logger.name):
        task_id = asyncio.run(scenario())
    view = overlays.get(task_id)
    assert view["status"] == "failed" and view["result"] is None
    assert view["error"] == {"code": "render_failed", "message": message}
    assert callbacks.posted == [("https://bus.example/cb", {
        "taskId": task_id, "status": "failed", "videoUrl": None, "errorMessage": message,
    })]
    assert uploads == []
    assert str(failure) in caplog.text  # 运维仍能在日志中看到完整根因


def task_rows(overlays: OverlayRenders) -> int:
    """任务表当前的行数。"""
    with overlays.store.connection() as db:
        return db.execute("SELECT COUNT(*) FROM overlay_renders").fetchone()[0]


def long_bindings(monkeypatch) -> None:
    """让样式的片段结束于 150 秒（片段 b 起点 148 秒 + 资产自身 2 秒），超过 120 秒上限。"""
    stored = [MessageToDict(item, preserving_proto_field_name=True) for item in (
        placement("a", start=0, order=0), placement("b", start=148, order=1),
    )]
    monkeypatch.setattr(overlay_service.sprites, "get_bindings", lambda store, style_id: (1, stored, None))


# 场景：时长已知时受理阶段就拒绝超限请求（显式 duration 或片段自身结尾），不写任务行。
def test_accept_enforces_the_limit_on_known_lengths(service, monkeypatch) -> None:
    """显式 duration 超限 422；未提供时片段结尾超限同样 422；上限内的请求被受理。"""
    overlays, _, _ = service
    with pytest.raises(HTTPException) as caught:
        asyncio.run(overlays.accept(uuid4(), None, 9999.0, None))
    assert caught.value.status_code == 422 and "超过上限 120" in caught.value.detail
    assert task_rows(overlays) == 0
    asyncio.run(overlays.accept(uuid4(), None, 120.0, None))  # 恰好等于上限
    long_bindings(monkeypatch)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(overlays.accept(uuid4(), None, None, None))
    assert caught.value.status_code == 422 and "超过上限 120" in caught.value.detail
    assert task_rows(overlays) == 1


# 场景：有 audioUrl 时 duration 被忽略，不能因它超限而拒绝；音频本身过长要到探测后才知道。
def test_audio_length_is_checked_after_probing(service, monkeypatch) -> None:
    """audioUrl 加超大 duration 被受理；探测出的音频时长超限时任务以 duration_too_long 失败。"""
    overlays, renderer, _ = service

    async def probed(url: str, timeout: float) -> float:
        return 500.0

    monkeypatch.setattr(overlay_render, "audio_duration", probed)

    async def scenario():
        task_id = await overlays.accept(uuid4(), "https://x/tts.mp3", 9999.0, None)
        await finish(overlays)
        return task_id

    view = overlays.get(asyncio.run(scenario()))
    assert view["status"] == "failed" and view["error"]["code"] == "duration_too_long"
    assert "超过上限 120" in view["error"]["message"] and renderer.requests == []


# 场景：缺少渲染所需的工具或文件时受理阶段就 503，而不是接受后才失败。
def test_accept_refuses_a_host_that_cannot_render(service, monkeypatch) -> None:
    """环境检查报告缺失项时返回 503、写服务端日志，且不创建任务。"""
    overlays, _, _ = service
    monkeypatch.setattr(overlay_service, "environment_problem", lambda settings: "ffprobe")
    with pytest.raises(HTTPException) as caught:
        asyncio.run(overlays.accept(uuid4(), None, 3.0, None))
    assert caught.value.status_code == 503 and task_rows(overlays) == 0


# 场景：读不到配音时长时对调用方只给固定说明，服务端日志留下异常类型而不含带签名的地址。
def test_unreadable_audio_is_logged_without_the_url(monkeypatch, caplog) -> None:
    """ffprobe 无法启动时任务得到 audio_duration_invalid，日志只记 FileNotFoundError。"""
    async def missing(*args, **kwargs):
        raise FileNotFoundError("[Errno 2] No such file or directory: 'ffprobe'")

    monkeypatch.setattr(overlay_render.asyncio, "create_subprocess_exec", missing)
    with caplog.at_level(logging.WARNING, logger=overlay_render.logger.name), pytest.raises(OverlayError) as caught:
        asyncio.run(overlay_render.audio_duration("https://x/tts.mp3?token=secret", 5))
    assert caught.value.code == "audio_duration_invalid"
    assert "FileNotFoundError" in caplog.text and "secret" not in caplog.text


# 场景：环境检查逐项指出缺少的工具或文件；全部具备时返回 None。
def test_environment_problem_names_the_missing_piece(tmp_path, monkeypatch) -> None:
    """依次缺 ffprobe、bwrap、prlimit、node，再缺浏览器和两个字体文件。"""
    files = {name: tmp_path / name for name in ("browser", "regular.ttc", "bold.ttc")}
    for path in files.values():
        path.write_bytes(b"x")
    settings = Settings(
        _env_file=None, data_dir=tmp_path, browser_executable=files["browser"],
        font_regular=files["regular.ttc"], font_bold=files["bold.ttc"],
    )
    present = {"ffprobe", "bwrap", "prlimit", "node"}
    monkeypatch.setattr(overlay_render.shutil, "which", lambda name: f"/usr/bin/{name}" if name in present else None)
    assert overlay_render.environment_problem(settings) is None
    for tool in ("ffprobe", "bwrap", "prlimit", "node"):
        present.discard(tool)
        assert overlay_render.environment_problem(settings) == tool
        present.add(tool)
    for path in files.values():
        path.unlink()
        assert overlay_render.environment_problem(settings) == str(path)
        path.write_bytes(b"x")


# 场景：重启后恢复未终态任务并完成；终态任务不会被重复渲染。
def test_resume_reruns_unfinished_tasks(service) -> None:
    """queued 任务经 resume 渲染成功；已成功任务不再产生新的渲染请求。"""
    overlays, renderer, _ = service
    request = {"style_id": str(uuid4()), "audio_url": None, "duration": 3.0, "callback_url": None}
    overlays._insert("11111111-1111-1111-1111-111111111111", {"request": request})

    async def scenario():
        overlays.resume()
        await finish(overlays)
        overlays.resumed = False
        overlays.resume()
        await finish(overlays)

    asyncio.run(scenario())
    assert overlays.get("11111111-1111-1111-1111-111111111111")["status"] == "succeeded"
    assert len(renderer.requests) == 1


# 场景：duration 与合成接口的 videoDuration 一样严格：布尔值、数字字符串和非正数都是 422。
@pytest.mark.parametrize("duration", [True, "7.5", 0, -1])
def test_duration_is_strict(service, client: TestClient, duration) -> None:
    """布尔值、数字字符串和非正数的 duration 返回 422 且不创建任务。"""
    overlays, _, _ = service

    async def provide():
        return overlays

    app.dependency_overrides[overlay_renders] = provide
    try:
        response = client.post("/api/v1/overlay-renders", json={"styleId": str(uuid4()), "duration": duration})
        assert response.status_code == 422
        assert task_rows(overlays) == 0
    finally:
        app.dependency_overrides.pop(overlay_renders, None)


# 场景：HTTP 契约——受理返回 taskId，GET 返回状态；未知任务 404。
def test_routes_accept_and_query(service, client: TestClient) -> None:
    """路由使用 camelCase 请求体，未知字段拒绝，查询未知任务 404。"""
    overlays, _, _ = service

    async def provide():
        return overlays

    app.dependency_overrides[overlay_renders] = provide
    try:
        created = client.post("/api/v1/overlay-renders", json={"styleId": str(uuid4()), "duration": 3})
        assert created.status_code == 200
        assert client.post("/api/v1/overlay-renders", json={"styleId": str(uuid4()), "duration": 7.5}).status_code == 200
        assert client.post("/api/v1/overlay-renders", json={"styleId": str(uuid4()), "bogus": 1}).status_code == 422
        assert client.get(f"/api/v1/overlay-renders/{uuid4()}").status_code == 404
        task_id = created.json()["taskId"]
        assert client.get(f"/api/v1/overlay-renders/{task_id}").json()["taskId"] == task_id
    finally:
        app.dependency_overrides.pop(overlay_renders, None)


# 组合型 Sprite：标题 Preset 位于嵌套的 Sequence(from=30, 60 帧)，淡出区间取自自身的 durationInFrames。
NESTED_FADE = """import React from 'react';
import {AbsoluteFill, Sequence, useCurrentFrame, useVideoConfig, interpolate} from 'remotion';
function Title() {
  const frame = useCurrentFrame();
  const {durationInFrames: d} = useVideoConfig();
  const opacity = interpolate(frame, [0, 10, d - 10, d], [0, 1, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return <AbsoluteFill style={{background: '#ff0000', opacity}} />;
}
export default function Sprite() {
  return <AbsoluteFill><Sequence from={30} durationInFrames={60}><Title /></Sequence></AbsoluteFill>;
}
"""


def decoded_alpha(video: Path, index: int) -> float:
    """用 libvpx 解码一帧并返回左上角像素的 alpha；原生 vp9 解码器会丢弃透明通道。"""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-c:v", "libvpx-vp9", "-i", str(video), "-vf", f"select=eq(n\\,{index})",
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        capture_output=True, check=True,
    ).stdout
    return raw[3] / 255


# 场景：真实沙箱渲染中，嵌套 Sequence 内的 Preset 读到自己的时长，且被成片总长截断时也不受影响。
@pytest.mark.skipif(os.environ.get("IMV_TEST_RENDERER") != "1", reason="set IMV_TEST_RENDERER=1 for Linux overlay integration")
def test_real_overlay_keeps_nested_sequence_timing(tmp_path: Path) -> None:
    """片段起点第 30 帧、成片 4 秒（120 帧，截断片段）：标题在局部第 52/55/59 帧的 alpha 为 0.8/0.5/0.1。

    标题自己的 Sequence 为 60 帧，所以淡出以 60 为终点；若 Preset 读到整个 Sprite 的 150 帧，alpha 会一直是 1。
    同时 render_overlay 内的 ffprobe 核对编码、尺寸、帧率、帧数与透明标记。
    """
    configured = {
        name: Path(value)
        for name, value in (
            ("browser_executable", RENDERER_PATHS["IMV_BROWSER_EXECUTABLE"]),
            ("font_regular", RENDERER_PATHS["IMV_FONT_REGULAR"]),
            ("font_bold", RENDERER_PATHS["IMV_FONT_BOLD"]),
        )
        if value
    }
    settings = Settings(_env_file=None, data_dir=tmp_path / "state", **configured)
    published = {"nested": sprite("nested", width=180, height=320, frames=150, code=NESTED_FADE)}
    overlay = plan_overlay([placement("nested", start=1.0)], published, 4.0, 120)
    video = asyncio.run(render_overlay(Renderer(settings), overlay, tmp_path / "job"))
    for frame, expected in ((112, 0.8), (115, 0.5), (119, 0.1)):  # 标题从第 60 帧开始
        assert decoded_alpha(video, frame) == pytest.approx(expected, abs=0.05)
