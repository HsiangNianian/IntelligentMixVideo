"""Remotion 叠加视频：时长优先级、摆放规划、任务流转、ZOS 转存与回调的离线回归。

默认不启动浏览器、ffprobe 或 ZOS：渲染器、探测与上传由替身承担，任务表使用临时 SQLite。
`real_` 用例在 Linux 上经隔离沙箱真实渲染，需 IMV_TEST_RENDERER=1（见 backend-integration 工作流）。
在 server/ 目录执行 `uv run --locked pytest tests/test_remotion_overlay_render.py -v`。
"""

import asyncio
import json
import logging
import os
import sqlite3
import subprocess
import threading
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
from server.remotion_templates.api import create_template_app
from server.remotion_templates.overlay_router import overlay_renders
from server.remotion_templates.overlay_service import OverlayRenders
from server.remotion_templates.renderer import Renderer
from server.remotion_templates.routes import runtime_for
from server.remotion_templates.settings import Settings
from server.remotion_templates.store import Store
from server.video_composition import media
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


# 场景：秒转帧与模板时间轴一致：半帧向后取整（0.75 秒 @30 FPS 是第 23 帧，而不是银行家舍入的 22）。
@pytest.mark.parametrize("fps, start, length, total, expected", [
    (30, 0.75, 1.0, 4.0, (23, 30, 120)),  # 22.5 -> 23
    (20, 0.125, 0.125, 2.0, (3, 3, 40)),  # 2.5 -> 3 起点与时长各自半帧进位
    (30, 0.0, 0.25, 0.25, (0, 8, 8)),  # 成片 7.5 帧 -> 8 帧
])
def test_plan_rounds_half_frames_up(fps, start, length, total, expected) -> None:
    """起点帧、片段帧数和成片总帧数都用同一个半帧向上规则。"""
    published = {"a": sprite("a", fps=fps, frames=60)}
    overlay = plan_overlay([placement("a", start=start, duration=length)], published, total, 120)
    item = overlay.instances[0]
    assert (item["start_frame"], item["duration_frames"], overlay.frames) == expected


# 场景：与前后端共用的模板时间向量对拍：秒制起点在叠加视频里落在同一帧。
@pytest.mark.parametrize(
    "case", [
        case for case in json.loads(Path(__file__).with_name("template_timing_cases.json").read_text())
        if case["mode"] == "seconds" and case["expected_end"] > case["expected_start"]
    ],
    ids=lambda case: case["purpose"],
)
def test_plan_matches_the_shared_timing_vectors(case) -> None:
    """起点帧与模板时间轴相同，片段在成片内的结束帧也相同（叠加视频每个片段至少一帧）。"""
    fps = case["fps"]
    published = {"a": sprite("a", fps=fps, frames=60)}
    overlay = plan_overlay([placement("a", start=case["start"], duration=case["length"])], published, case["duration"], 1000)
    item = overlay.instances[0]
    assert item["start_frame"] / fps == pytest.approx(case["expected_start"])
    assert min(item["start_frame"] + item["duration_frames"], overlay.frames) / fps == pytest.approx(case["expected_end"])


# 场景：已保存的超大数值不会让规划抛出 OverflowError：超大起点被当作越界丢弃，超长片段按最长成片截短。
def test_plan_survives_huge_saved_values() -> None:
    """起点 1e307 秒的片段丢弃、其余正常；时长 1e300 秒的片段取最长成片的帧数，合成不会无限增长。"""
    overlay = plan_overlay(
        [placement("a", start=1e307, order=0), placement("b", start=0, duration=1e300, order=1)], PUBLICATIONS, 6, 120,
    )
    assert [(i["start_frame"], i["duration_frames"]) for i in overlay.instances] == [(0, 120 * 30)]
    assert overlay.span == 3600


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

    monkeypatch.setattr(media.asyncio, "create_subprocess_exec", missing)
    with caplog.at_level(logging.WARNING, logger=overlay_render.logger.name), pytest.raises(OverlayError) as caught:
        asyncio.run(overlay_render.audio_duration("https://x/tts.mp3?token=secret", 5))
    assert caught.value.code == "audio_duration_invalid"
    assert "FileNotFoundError" in caplog.text and "secret" not in caplog.text


def fake_ffprobe(monkeypatch, payload, returncode: int = 0) -> list:
    """用固定输出替换 ffprobe 子进程，返回记录实际参数的列表；不启动真实进程。"""
    calls = []

    async def communicate():
        """模拟 ffprobe 的 JSON 输出。"""
        return json.dumps(payload).encode(), b""

    async def spawn(*args, **kwargs):
        """记录参数并返回受控子进程。"""
        calls.append(args)
        return SimpleNamespace(communicate=communicate, returncode=returncode)

    monkeypatch.setattr(media.asyncio, "create_subprocess_exec", spawn)
    return calls


# 场景：配音时长取容器的完整时长，只允许 HTTP(S) 协议；读不出有效正数时统一报 audio_duration_invalid。
@pytest.mark.parametrize("payload, returncode, expected", [
    ({"format": {"duration": "12.5"}}, 0, 12.5),
    ({"format": {}}, 0, None), ({"format": {"duration": "0"}}, 0, None), ({"format": {"duration": "NaN"}}, 0, None),
    ({"format": {"duration": "-1"}}, 0, None), ({}, 0, None), ({"format": {"duration": "3"}}, 1, None),
])
def test_audio_duration_uses_the_container_duration(monkeypatch, payload, returncode, expected) -> None:
    """读取 format.duration；缺失、非正、非有限或 ffprobe 失败都得到同一个公开错误。"""
    calls = fake_ffprobe(monkeypatch, payload, returncode)
    url = "https://x/tts.mp3?token=a%2Fb"
    if expected is None:
        with pytest.raises(OverlayError) as caught:
            asyncio.run(overlay_render.audio_duration(url, 5))
        assert caught.value.code == "audio_duration_invalid"
    else:
        assert asyncio.run(overlay_render.audio_duration(url, 5)) == expected
    args = calls[0]
    assert args[-1] == url and "-protocol_whitelist" in args and args[args.index("-show_entries") + 1] == "format=duration"


def rendered_stream(**changes) -> dict:
    """与 6 秒、1080×1920、30 FPS 叠加视频相符的 ffprobe 视频流，可覆盖个别字段。"""
    stream = {"codec_name": "vp9", "width": 1080, "height": 1920, "avg_frame_rate": "30/1",
              "nb_read_frames": "180", "tags": {"ALPHA_MODE": "1"}}
    return {"streams": [{**stream, **changes}]}


# 场景：成片探测核对编码、尺寸、帧率、帧数与透明标记，任何不符或无法读取都拒绝。
@pytest.mark.parametrize("payload, returncode, valid", [
    (rendered_stream(), 0, True),
    (rendered_stream(tags={"alpha_mode": "1"}), 0, True),  # 标签名大小写不敏感
    (rendered_stream(codec_name="vp8"), 0, False), (rendered_stream(width=720), 0, False),
    (rendered_stream(height=1280), 0, False), (rendered_stream(avg_frame_rate="25/1"), 0, False),
    (rendered_stream(avg_frame_rate="0/0"), 0, False), (rendered_stream(nb_read_frames="179"), 0, False),
    (rendered_stream(nb_read_frames="N/A"), 0, False), (rendered_stream(tags={}), 0, False),
    (rendered_stream(tags={"ALPHA_MODE": "0"}), 0, False), ({"streams": []}, 0, False), ({}, 0, False),
    (rendered_stream(), 1, False),
])
def test_probe_overlay_verifies_the_rendered_file(monkeypatch, tmp_path, payload, returncode, valid) -> None:
    """规格全部相符才放行；本地文件探测不带远程协议限制，并且只读取第一个视频流。"""
    calls = fake_ffprobe(monkeypatch, payload, returncode)
    overlay = plan_overlay([placement("a")], PUBLICATIONS, 6, 120)
    if valid:
        asyncio.run(overlay_render.probe_overlay(tmp_path / "overlay.webm", overlay))
    else:
        with pytest.raises(OverlayError) as caught:
            asyncio.run(overlay_render.probe_overlay(tmp_path / "overlay.webm", overlay))
        assert caught.value.code == "render_invalid"
    args = calls[0]
    assert args[-1] == str(tmp_path / "overlay.webm") and "-protocol_whitelist" not in args
    assert args[args.index("-select_streams") + 1] == "v:0"


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


def insert_row(overlays: OverlayRenders, task_id: str, status: str, created_at: str, data: str) -> None:
    """直接写入一行任务记录，可以是损坏的内容。"""
    with overlays.store.connection() as db:
        db.execute("INSERT INTO overlay_renders VALUES (?, ?, ?, ?)", (task_id, status, created_at, data))


# 场景：运行时第一次建立就恢复叠加任务，不论之后先访问哪条路由，也只恢复一次。
def test_runtime_start_recovers_overlay_tasks(tmp_path, monkeypatch) -> None:
    """runtime_for 建立运行时后调用 resume 一次；再次取用不重复恢复。"""
    settings = Settings(_env_file=None, data_dir=tmp_path / "state")
    application = create_template_app(
        settings, provider=SimpleNamespace(settings=settings), renderer=SimpleNamespace(settings=settings),
    )
    resumed = []
    monkeypatch.setattr(OverlayRenders, "resume", lambda self: resumed.append(self))

    async def scenario():
        return await runtime_for(application.state), await runtime_for(application.state)

    first, second = asyncio.run(scenario())
    assert first is second and resumed == [first.overlays]


# 场景：恢复时损坏的记录被记录并跳过，不阻塞其余任务，也不让首个请求报 500。
def test_resume_skips_damaged_rows(service, caplog) -> None:
    """无法解析的 JSON、缺 request 的终态行、非对象 JSON 都跳过；正常的排队任务仍被渲染完成。"""
    overlays, renderer, _ = service
    insert_row(overlays, "bad-json", "queued", "2026-01-01T00:00:00+00:00", "{not json")
    insert_row(overlays, "no-request", "succeeded", "2026-01-01T00:00:01+00:00", '{"notification": "pending"}')
    insert_row(overlays, "not-object", "queued", "2026-01-01T00:00:02+00:00", "[]")
    good = "33333333-3333-3333-3333-333333333333"
    request = {"style_id": str(uuid4()), "audio_url": None, "duration": 3.0, "callback_url": None}
    insert_row(overlays, good, "queued", "2026-01-01T00:00:03+00:00", json.dumps({"request": request}))

    async def scenario():
        overlays.resume()
        await finish(overlays)

    with caplog.at_level(logging.ERROR, logger=overlay_service.logger.name):
        asyncio.run(scenario())
    assert overlays.resumed and overlays.get(good)["status"] == "succeeded" and len(renderer.requests) == 1
    assert all(task_id in caplog.text for task_id in ("bad-json", "no-request", "not-object"))


# 场景：恢复按创建时间从早到晚重新启动任务，而不是按主键或存储顺序。
def test_resume_requeues_oldest_first(service, monkeypatch) -> None:
    """较早创建的任务（主键较大、较晚写入）先启动；之后各任务何时轮到渲染名额取决于各自的进度。"""
    overlays, _, _ = service
    spawned = []

    async def record(task_id: str) -> None:
        """只记录被启动的任务，不执行。"""
        spawned.append(task_id)

    monkeypatch.setattr(overlays, "_run", record)
    request = json.dumps({"request": {"style_id": str(uuid4()), "audio_url": None, "duration": 3.0, "callback_url": None}})
    newer, older = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
    insert_row(overlays, newer, "queued", "2026-01-02T00:00:00+00:00", request)
    insert_row(overlays, older, "queued", "2026-01-01T00:00:00+00:00", request)

    async def scenario():
        overlays.resume()
        await finish(overlays)

    asyncio.run(scenario())
    assert spawned == [older, newer]


# 场景：任务表暂时读不出来时不吞掉恢复机会：不抛异常，也不标记已恢复，下次访问再试。
def test_resume_retries_when_the_table_is_unreadable(service, monkeypatch) -> None:
    """第一次读表失败时 resumed 仍为 False，第二次成功后才置为 True。"""
    overlays, _, _ = service
    real = overlays.store.connection

    def locked():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(overlays.store, "connection", locked)
    overlays.resume()
    assert overlays.resumed is False
    monkeypatch.setattr(overlays.store, "connection", real)
    overlays.resume()
    assert overlays.resumed is True


def lock_writes(overlays: OverlayRenders, status: str, times: int | None) -> None:
    """让写入 `status` 的 `_save` 以“数据库被锁”失败 `times` 次；None 表示一直失败。"""
    real, left = overlays._save, {"n": times}

    def save(task_id, new_status, **changes):
        """失败次数未用完时抛 OperationalError，否则交给真实实现。"""
        if new_status == status and (left["n"] is None or left["n"] > 0):
            if left["n"] is not None:
                left["n"] -= 1
            raise sqlite3.OperationalError("database is locked")
        return real(task_id, new_status, **changes)

    overlays._save = save


@pytest.fixture
def fast_persist(monkeypatch) -> None:
    """把状态写入重试间隔缩成毫秒。"""
    monkeypatch.setattr(overlay_service, "PERSIST_DELAY_SECONDS", 0.001)


# 场景：数据库短暂被锁时终态写入自动重试，任务照常成功并通知。
def test_transient_database_lock_is_retried(service, callbacks, fast_persist) -> None:
    """成功状态前两次写入失败、第三次成功：任务 succeeded 且回调送达，不被改成失败。"""
    overlays, _, _ = service
    lock_writes(overlays, "succeeded", 2)

    async def scenario():
        task_id = await overlays.accept(uuid4(), None, 3.0, "https://bus.example/cb")
        await finish(overlays)
        return task_id

    task_id = asyncio.run(scenario())
    assert overlays.get(task_id)["status"] == "succeeded" and len(callbacks.posted) == 1


# 场景：上传成功后成功状态始终写不进去：任务不能被改成失败，也不通知；重启恢复后重新完成。
def test_unwritable_success_keeps_the_task_recoverable(service, callbacks, fast_persist, caplog) -> None:
    """GET 仍显示 uploading（ZOS 对象已存在）；换上可写的库后 resume 重新渲染并成功，沿用同一个对象键。"""
    overlays, renderer, uploads = service
    lock_writes(overlays, "succeeded", None)

    async def scenario():
        task_id = await overlays.accept(uuid4(), None, 3.0, "https://bus.example/cb")
        await finish(overlays)  # 后台协程不得带着未处理异常结束
        return task_id

    with caplog.at_level(logging.ERROR, logger=overlay_service.logger.name):
        task_id = asyncio.run(scenario())
    assert overlays.get(task_id)["status"] == "uploading" and len(uploads) == 1 and callbacks.posted == []
    assert "状态暂无法保存" in caplog.text

    restarted = OverlayRenders(overlays.store, renderer, overlays.settings)

    async def recover():
        restarted.resume()
        await finish(restarted)

    asyncio.run(recover())
    assert restarted.get(task_id)["status"] == "succeeded" and len(callbacks.posted) == 1
    assert [key for key, _, _ in uploads] == [f"imv/overlay_render/{task_id}.webm"] * 2


# 场景：渲染失败后失败状态也写不进去：同样不崩溃、不通知，重启后任务仍会被处理到终态。
def test_unwritable_failure_keeps_the_task_recoverable(service, callbacks, fast_persist) -> None:
    """失败状态写入一直失败时任务停在 rendering；恢复后再次失败并正常记录、通知。"""
    overlays, renderer, _ = service
    renderer.failure = RuntimeError("worker crashed")
    lock_writes(overlays, "failed", None)

    async def scenario():
        task_id = await overlays.accept(uuid4(), None, 3.0, "https://bus.example/cb")
        await finish(overlays)
        return task_id

    task_id = asyncio.run(scenario())
    assert overlays.get(task_id)["status"] == "rendering" and callbacks.posted == []
    restarted = OverlayRenders(overlays.store, renderer, overlays.settings)

    async def recover():
        restarted.resume()
        await finish(restarted)

    asyncio.run(recover())
    assert restarted.get(task_id)["status"] == "failed"
    assert [body["status"] for _, body in callbacks.posted] == ["failed"]


async def wait_until(condition, seconds: float = 5) -> None:
    """轮询等待条件成立；超时即抛 TimeoutError，避免卡住的用例无限等待。"""
    async def poll():
        while not condition():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), seconds)


# 场景：配音音频探测很慢时不占用渲染名额，后面排队的任务照常渲染并完成。
def test_slow_audio_probe_does_not_hold_the_render_slot(service, monkeypatch) -> None:
    """第一个任务卡在远程音频探测，第二个（显式 duration）仍能渲染、上传并成功；放行后第一个也完成。"""
    overlays, renderer, uploads = service
    released = []

    async def probed(url: str, timeout: float) -> float:
        while not released:
            await asyncio.sleep(0.01)
        return 3.0

    monkeypatch.setattr(overlay_render, "audio_duration", probed)

    async def scenario():
        slow = await overlays.accept(uuid4(), "https://x/tts.mp3", None, None)
        fast = await overlays.accept(uuid4(), None, 3.0, None)
        await wait_until(lambda: overlays.get(fast)["status"] == "succeeded")
        assert overlays.get(slow)["status"] == "queued"  # 仍在探测，还没有轮到渲染
        released.append(True)
        await finish(overlays)
        return slow

    slow = asyncio.run(scenario())
    assert overlays.get(slow)["status"] == "succeeded" and len(renderer.requests) == 2


# 场景：上传很慢时不占用渲染名额，下一个任务可以立刻开始渲染。
def test_slow_upload_does_not_hold_the_render_slot(service, monkeypatch) -> None:
    """第一个任务卡在 ZOS 上传时，第二个任务的渲染已经开始；放行后两者都成功。"""
    overlays, renderer, _ = service
    gate, calls = threading.Event(), []

    def upload(files, zos, timeout):
        """第一次上传阻塞到被放行，其余立即返回。"""
        calls.append(files[0][1])
        if len(calls) == 1:
            gate.wait(5)
        return [f"https://zos.example/{files[0][1]}"]

    monkeypatch.setattr(overlay_service, "upload_public", upload)

    async def scenario():
        tasks = [await overlays.accept(uuid4(), None, 3.0, None) for _ in range(2)]
        # 两个渲染都已开始，而先上传的那个还卡在 ZOS：上传没有占着渲染名额（先后顺序由调度决定）。
        await wait_until(lambda: len(renderer.requests) == 2 and calls)
        blocked = calls[0].removeprefix("imv/overlay_render/").removesuffix(".webm")
        assert overlays.get(blocked)["status"] == "uploading"
        gate.set()
        await finish(overlays)
        return tasks

    tasks = asyncio.run(scenario())
    assert [overlays.get(task_id)["status"] for task_id in tasks] == ["succeeded", "succeeded"]


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
