"""Remotion 叠加视频与单个 Sprite 渲染：时长优先级、摆放与分段规划、任务流转、ZOS 转存与回调的离线回归。

默认不启动浏览器、ffprobe 或 ZOS：渲染器、探测与上传由替身承担，任务表 `remotion_overlay_renders` 使用代替 MySQL 的临时 SQLite（template_db）。
`real_` 用例在 Linux 上经隔离沙箱真实渲染，需 IMV_TEST_RENDERER=1（见 backend-integration 工作流）。
在 server/ 目录执行 `uv run --locked pytest tests/test_remotion_overlay_render.py -v`。
"""

import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from generated.imv.sprite.v1 import sprite_pb2 as pb
from pydantic import TypeAdapter
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import OperationalError

from server.app import app
from server.remotion_templates import overlay_render, overlay_service
from server.remotion_templates.api import app as remotion_templates_app
from server.remotion_templates.api import create_template_app
from server.remotion_templates.overlay_render import (
    OverlayError, OverlayPlacement, overlay_cpu_seconds, plan_overlay, render_overlay, resolve_total_seconds,
)
from server.remotion_templates.overlay_router import overlay_renders
from server.remotion_templates.overlay_service import OverlayRenders
from server.remotion_templates.renderer import Renderer
from server.remotion_templates.routes import runtime_for
from server.remotion_templates.runtime import Runtime
from server.remotion_templates.settings import Settings
from server.remotion_templates import sprite_render, render_resources
from server.remotion_templates.sprite_render import chunk_ranges, join_problem, plan_sprite_render
from server.remotion_templates.sprite_router import sprite_runtime
from server.remotion_templates.store import NotFound, Store
from server.remotion_templates.tables import overlay_renders as overlay_table
from server.video_composition import media
from server.video_composition.schema import MediaURL

from .conftest import published_sprite

CODE = "export default function Sprite() { return null; }"
# 自动隔离夹具会清除 IMV_*，所以在导入时读取本次运行配置的渲染器位置。
RENDERER_PATHS = {name: os.environ.get(name) for name in ("IMV_BROWSER_EXECUTABLE", "IMV_FONT_REGULAR", "IMV_FONT_BOLD")}


# 场景：浏览器已派生进程却连接失败时，worker 必须立即退出，不能只设置退出码后占住渲染名额。
@pytest.mark.skipif(os.name == "nt", reason="进程组清理回归使用 POSIX 信号")
def test_worker_exits_when_browser_launch_leaves_live_handles(tmp_path: Path) -> None:
    """执行真实 worker 的异常路径；模拟残留子进程和计时器，并清理测试进程组。"""
    node = shutil.which("node")
    if node is None:
        pytest.skip("需要 Node 执行 worker 生命周期回归")
    renderer = Path(__file__).parents[1] / "src/server/remotion"
    stub = tmp_path / "renderer-stub.mjs"
    stub.write_text('''/** 模拟启动超时后仍持有子进程与事件循环句柄的浏览器库。 */
import {spawn} from 'node:child_process';
export const bundle = async () => 'unused';
export const openBrowser = async () => {
  spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)']);
  setInterval(() => {}, 1000);
  throw new Error('browser connection failed with live handles');
};
export const selectComposition = async () => { throw new Error('must not select'); };
export const renderMedia = async () => { throw new Error('must not render'); };
''')
    source = (renderer / "overlay-worker.mjs").read_text()
    source = source.replace('"@remotion/bundler"', json.dumps(stub.as_uri()))
    source = source.replace('"@remotion/renderer"', json.dumps(stub.as_uri()))
    worker = tmp_path / "overlay-worker.mjs"
    worker.write_text(source)
    (tmp_path / "request.json").write_text(json.dumps({"composition": {}, "instances": []}))
    with (tmp_path / "failure.log").open("wb") as log:
        process = subprocess.Popen(
            [node, str(worker)], stdout=log, stderr=log, start_new_session=True,
            env={**os.environ, "IMV_WORK_ROOT": str(tmp_path), "IMV_RENDERER_ROOT": str(renderer)},
        )
        try:
            assert process.wait(timeout=5) == 1
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    assert "browser connection failed with live handles" in (tmp_path / "failure.log").read_text()
    assert not (tmp_path / "renderer.json").exists()


def sprite(sprite_id: str, *, width=1080, height=1920, fps=30, frames=60, code=CODE) -> pb.PublishedSprite:
    """构造一个已发布 Sprite：默认 2 秒、无可编辑参数。"""
    return pb.PublishedSprite(
        sprite_id=sprite_id, name=sprite_id, kind=pb.SPRITE_KIND_TEXT, tsx_code=code,
        canvas=pb.SpriteCanvas(width=width, height=height, fps=fps, preview_frames=frames),
        config_schema_json=json.dumps({"type": "object", "properties": {}}),
        default_config_json="{}",
    )


def placement(sprite_id: str, *, start=0.0, duration=None) -> OverlayPlacement:
    """构造一个摆放；duration 缺省时使用资产自身时长。"""
    return OverlayPlacement(sprite_id=sprite_id, start=start, duration=duration)


A, B = sprite("a"), sprite("b")
# c 带可编辑参数与内容区域（见 conftest.published_sprite），用于参数覆盖与宿主变换。
C = published_sprite("c")
PUBLICATIONS = {"a": A, "b": B, "c": C}
# 默认请求：a 从 0 秒、b 从 1 秒开始（各 2 秒），b 叠在 a 上；WIRE 是同一份请求的 HTTP 形式。
PLACED = [placement("a", start=0), placement("b", start=1)]
WIRE = [{"spriteId": "a", "start": 0}, {"spriteId": "b", "start": 1}]


def stored_request(**changes) -> dict:
    """任务行里保存的请求：默认的摆放列表，其余字段可覆盖。"""
    return {"placements": [item.model_dump(mode="json") for item in PLACED], "audio_url": None, "duration": 3.0,
            "callback_url": None, **changes}


# 场景：有 TTS 音频时无视请求 duration，以探测到的完整音频时长为准。
def test_audio_duration_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """音频时长优先；无音频用 duration；都没有取摆放的最晚结束；全无则报错。"""
    async def probed(url: str, timeout: float) -> float:
        """替身探测：固定返回 7.5 秒，并确认传入的是配音地址。"""
        assert url == "https://x/tts.mp3"
        return 7.5

    monkeypatch.setattr(overlay_render, "audio_duration", probed)
    placements = [placement("a", start=1), placement("b", start=4, duration=3)]

    async def total(audio, duration):
        """按给定的配音地址与 duration 解析成片总秒数。"""
        return await resolve_total_seconds(audio, duration, placements, PUBLICATIONS, 5)

    assert asyncio.run(total("https://x/tts.mp3", 99)) == 7.5
    assert asyncio.run(total(None, 12)) == 12
    assert asyncio.run(total(None, None)) == 7  # max(1+2, 4+3)
    with pytest.raises(OverlayError, match="无法确定成片时长"):
        asyncio.run(resolve_total_seconds(None, None, [], PUBLICATIONS, 5))


# 场景：摆放按请求里的先后叠放（先者在下），超出结尾截短、起点越界丢弃。
def test_plan_keeps_list_order_and_clips_placements() -> None:
    """帧范围以统一画布 fps 计算，实例顺序就是请求顺序而不是起点顺序；越界起点被丢弃，超出结尾的片段保留完整时长。"""
    placements = [
        placement("b", start=4.8),  # 2s 时长保持完整，由 6s 总长截断
        placement("a", start=0),
        placement("a", start=6),  # 起点等于结尾，丢弃
    ]
    overlay = plan_overlay(placements, PUBLICATIONS, 6, 120)
    assert (overlay.width, overlay.height, overlay.fps, overlay.frames) == (1080, 1920, 30, 180)
    assert [(i["start_frame"], i["duration_frames"]) for i in overlay.instances] == [(144, 60), (0, 60)]
    assert all(set(item) == {"sprite_id", "code", "config", "host", "start_frame", "duration_frames"} for item in overlay.instances)
    # Publications without a recorded content box pivot around the whole canvas; no placement transform yet.
    assert overlay.instances[0]["host"] == {"box": {"x": 0, "y": 0, "width": 1080, "height": 1920}, "transform": {}}
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
        [placement("a", start=1e307), placement("b", start=0, duration=1e300)], PUBLICATIONS, 6, 120,
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
        self.timeout_seconds: float | None = None

    def worker_browser_path(self) -> str:
        """返回固定浏览器路径。"""
        return "/browser"

    async def run_worker(self, directory: Path, *, worker=None, timeout_seconds=None, cpu_seconds=None) -> dict:
        """校验 worker 名称，保存请求与沙箱 CPU 上限并产出文件；排版预检 worker 视为文字都放得下。"""
        if worker == "tool-validation-worker.mjs":
            request = json.loads((directory / "request.json").read_text())
            return {"fits": [
                [{"index": item["index"], "fits": True, "size": item["values"][item["size_key"]]} for item in sprite["fit"]]
                for sprite in request["sprites"]
            ]}
        assert worker == "overlay-worker.mjs"
        self.cpu_seconds = cpu_seconds
        self.timeout_seconds = timeout_seconds
        request = json.loads((directory / "request.json").read_text())
        self.requests.append(request)
        if self.failure:
            raise self.failure
        # A chunked single Sprite render writes one file per requested frame range.
        for name in [f"chunk-{index}.webm" for index in range(len(request["ranges"]))] if "ranges" in request else ["overlay.webm"]:
            (directory / name).write_bytes(b"webm")
        return {}


@pytest.fixture
def service(tmp_path, monkeypatch, template_db):
    """带临时任务表、假渲染器、假 ZOS 的任务服务；重试间隔缩成毫秒，返回服务、渲染器和上传记录。"""
    font = tmp_path / "font.ttc"
    font.write_bytes(b"font")
    settings = Settings(_env_file=None, data_dir=tmp_path / "state", font_regular=font, font_bold=font)
    store = Store(settings.data_dir)
    store.initialize()
    renderer = FakeRenderer(settings)
    # 离线任务流转不依赖运行 pytest 主机的当前负载；真实预算行为有专门回归。
    monkeypatch.setattr(render_resources, "renderer_cpus", lambda reserve: list(range(128)))
    monkeypatch.setattr(render_resources, "memory_headroom", lambda reserve: (400 - 512 * reserve / 100) * 1024**3)

    async def join(*command, cwd, **kwargs):
        """默认假拼接器；需要检查拼接命令的用例自行覆盖。"""
        (Path(cwd) / command[-1]).write_bytes(b"webm")
        return SimpleNamespace(returncode=0, communicate=lambda: asyncio.sleep(0, (b"", b"")))

    monkeypatch.setattr(overlay_render.asyncio, "create_subprocess_exec", join)
    uploads: list[tuple] = []

    async def probed(path, overlay) -> None:
        """替身探测：视为规格一致。"""

    def upload(files, zos, timeout):
        """记录上传并返回公开地址。"""
        uploads.append((files[0][1], files[0][2], files[0][0].read_bytes()))
        return [f"https://zos.example/{files[0][1]}"]

    monkeypatch.setattr(overlay_render, "probe_overlay", probed)
    monkeypatch.setattr(overlay_service, "environment_problem", lambda settings: None)
    # 单 Sprite 渲染还要用 ffmpeg 拼接分块；CI 主机可能没有它，替身渲染器也不需要。
    monkeypatch.setattr(overlay_service, "join_problem", lambda overlay, settings: None)
    monkeypatch.setattr(overlay_service, "upload_public", upload)
    monkeypatch.setattr(overlay_service, "ZosSettings", lambda: SimpleNamespace())

    def get(sprite_id):
        """替身读取已发布 Sprite：未知 ID 与真实存储一样抛 NotFound。"""
        if sprite_id not in PUBLICATIONS:
            raise NotFound("sprite not found")
        return PUBLICATIONS[sprite_id]

    monkeypatch.setattr(overlay_service.sprites, "get", get)
    # 5/15/45 秒的通知重试和 2 秒的写库重试在用例里不等待；用例按次数而不是按时间断言。
    monkeypatch.setattr(overlay_service, "NOTIFICATION_RETRY_DELAYS", (0.001, 0.001, 0.001))
    monkeypatch.setattr(overlay_service, "PERSIST_DELAY_SECONDS", 0.001)
    return OverlayRenders(store, renderer, settings), renderer, uploads


async def finish(overlays: OverlayRenders) -> None:
    """等待全部后台任务（渲染与通知）结束；整体限时，卡住的用例不会无限等待。"""
    async def settle():
        """反复等待，直到任务集合清空（任务结束时才会从集合中移除自己）。"""
        while overlays.tasks:
            await asyncio.gather(*list(overlays.tasks))

    await asyncio.wait_for(settle(), 15)


def run_task(overlays: OverlayRenders, *, duration: float | None = 3.0, audio: str | None = None,
             callback: str | None = None, placements: list[OverlayPlacement] | None = None) -> str:
    """受理一个任务（默认的摆放）并等待它的渲染、上传和通知全部结束；返回任务 ID。"""
    async def scenario():
        """受理后等待后台协程；协程若抛出未处理异常，这里会重新抛出。"""
        task_id = await overlays.accept(placements or PLACED, audio, duration, callback)
        await finish(overlays)
        return task_id

    return asyncio.run(scenario())


def run_resume(overlays: OverlayRenders) -> None:
    """触发恢复并等待被启动的后台任务结束。"""
    async def scenario():
        """恢复后等待后台协程。"""
        overlays.resume()
        await finish(overlays)

    asyncio.run(scenario())


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

        def __init__(self, **_):
            """接受并忽略 httpx.AsyncClient 的构造参数。"""

        async def __aenter__(self):
            """作为异步上下文返回自身。"""
            return self

        async def __aexit__(self, *_):
            """退出时无需清理。"""

        async def post(self, url, json):
            """记录请求，并交给当前的 respond 决定返回、抛错或挂起。"""
            state.posted.append((url, json))
            return await state.respond()

    monkeypatch.setattr(overlay_service.httpx, "AsyncClient", Client)
    return state


def is_notified(overlays: OverlayRenders, task_id: str) -> bool:
    """任务行是否已记录终态通知处理完毕（送达或放弃）。"""
    with overlays.store.reading() as db:
        data = db.execute(select(overlay_table.c.data).where(overlay_table.c.id == task_id)).scalar_one()
    return bool(json.loads(data).get("notified"))


def task_rows(overlays: OverlayRenders) -> int:
    """任务表当前的行数。"""
    with overlays.store.reading() as db:
        return db.execute(select(func.count()).select_from(overlay_table)).scalar_one()


def insert_row(overlays: OverlayRenders, task_id: str, status: str, created_at: str, data: str) -> None:
    """直接写入一行任务记录，可以是损坏的内容。"""
    with overlays.store.transaction() as db:
        db.execute(insert(overlay_table).values(id=task_id, status=status, created_at=created_at, data=data))


# 场景：成功路径按 duration 渲染，转存 ZOS 指定键，并向回调发送四字段通知。
def test_task_renders_uploads_and_notifies(service, callbacks) -> None:
    """GET 返回 ZOS 地址；回调体为 taskId/status/videoUrl/errorMessage，源文件在任务结束后被清理。"""
    overlays, renderer, uploads = service
    task_id = run_task(overlays, duration=4.0, callback="https://bus.example/cb")
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
    assert is_notified(overlays, task_id)
    assert not (overlays.store.root / "overlays" / task_id).exists()


@pytest.mark.parametrize("concurrency", [1, 4, 8, 16])
def test_frame_concurrency_reaches_the_worker(service, concurrency) -> None:
    """服务端配置决定每块的渲染页数，任务请求不能覆盖它。"""
    overlays, renderer, _ = service
    overlays.settings.overlay_frame_concurrency = concurrency
    assert overlays.get(run_task(overlays))["status"] == "succeeded"
    assert renderer.requests[0]["concurrency"] == concurrency


@pytest.mark.parametrize("cpus, expected", [(1, 1), (3, 3), (4, 4)])
@pytest.mark.parametrize("sprite", [False, True])
def test_reduced_pages_reach_both_workers(service, monkeypatch, cpus, expected, sprite):
    """预留后只剩 1/3/4 核时，整段与单 Sprite 都传实际页数；默认四页配置本身不被修改。"""
    overlays, renderer, _ = service
    monkeypatch.setattr(render_resources, "renderer_cpus", lambda reserve: list(range(cpus)))
    task_id = run_sprite_task(overlays, render_input()) if sprite else run_task(overlays)
    assert overlays._read(task_id)[0] == "succeeded"
    assert renderer.requests[0]["concurrency"] == expected
    assert overlays.settings.overlay_frame_concurrency == 4
    assert (overlays.capacity.pages, overlays.capacity.memory_units) == (0, 0)


@pytest.mark.parametrize("sprite", [False, True])
def test_capacity_timeout_reports_resource_unavailable(service, monkeypatch, sprite):
    """容量耗尽时两种任务均返回稳定的可重试提示；不启动 worker，释放名额后新任务可继续。"""
    overlays, renderer, _ = service
    overlays.settings.overlay_resource_wait_seconds = .02
    monkeypatch.setattr(render_resources, "memory_headroom", lambda reserve: 0)
    task_id = run_sprite_task(overlays, render_input()) if sprite else run_task(overlays)
    status, data = overlays._read(task_id)
    assert status == "failed" and data["error"] == {"code": "resource_unavailable", "message": "渲染资源暂时不足，请稍后重试"}
    assert not renderer.requests and not overlays.slot.locked()
    assert (overlays.capacity.chunks, overlays.capacity.memory_units) == (0, 0)
    monkeypatch.setattr(render_resources, "memory_headroom", lambda reserve: 32 * 1024**3)
    assert overlays.get(run_task(overlays))["status"] == "succeeded"


def test_slot_wait_excludes_resource_wait(service, monkeypatch, caplog):
    """模拟两秒资源等待，名额立即可用；两个诊断字段应分别是 0 和 2000，而非重复累计。"""
    from contextlib import asynccontextmanager
    overlays, _, _ = service
    clock = [0.0]
    @asynccontextmanager
    async def claim(requested, pages):
        clock[0] += 2
        yield render_resources.RenderGrant(1, pages, 2000, 5120)
    monkeypatch.setattr(overlays.capacity, "claim", claim)
    monkeypatch.setattr(overlay_service, "perf_counter", lambda: clock[0])
    with caplog.at_level("INFO", logger=overlay_service.logger.name):
        assert overlays.get(run_task(overlays))["status"] == "succeeded"
    [entry] = [record for record in caplog.records if record.msg.startswith("Remotion performance task_id=")]
    metrics = json.loads(entry.args[-1])
    assert (metrics["slot_wait_ms"], metrics["resource_wait_ms"]) == (0, 2000)
    assert "queue_wait_ms" not in metrics


@pytest.mark.parametrize("cpu_used, threads", [(None, None), (0, 1), (4, 8)])
def test_chunk_and_vp9_settings_reach_the_worker(service, cpu_used, threads) -> None:
    """编码调优只发送已配置字段，cpu-used=0 保留；分块并发独立于浏览器页并发。"""
    overlays, renderer, _ = service
    overlays.settings.overlay_chunk_concurrency = 3
    overlays.settings.overlay_vp9_cpu_used = cpu_used
    overlays.settings.overlay_vp9_threads = threads
    assert overlays.get(run_task(overlays, duration=45))["status"] == "succeeded"
    [request] = renderer.requests
    assert request["chunk_concurrency"] == 3
    if cpu_used is None:
        assert "vp9" not in request
    else:
        assert request["vp9"] == {"cpu_used": cpu_used, "threads": threads}


def test_memory_grant_reaches_the_actual_worker(service, monkeypatch):
    """180 秒分为 12 块，但余量只够两块并行时实际 worker 必须降到 2，不能仍然跑 6。"""
    overlays, renderer, _ = service
    # 默认每块四页：两块共 8 GiB，加每视频额外 1 GiB。
    monkeypatch.setattr(render_resources, "memory_headroom", lambda reserve: 9 * 1024**3)
    assert overlays.get(run_task(overlays, duration=180))["status"] == "succeeded"
    [request] = renderer.requests
    assert request["concurrency"] == 4 and request["vp9"] == {"cpu_used": 4, "threads": 2}
    assert len(request["ranges"]) == 12 and request["chunk_concurrency"] == 2
    assert overlays.capacity.chunks == 0 and overlays.capacity.memory_units == 0


@pytest.mark.parametrize("limit", [6, 2])
def test_render_slots_bound_actual_tasks(service, monkeypatch, limit) -> None:
    """默认六个任务可同时进入 worker，自定义两个名额也生效；额外任务要等名额释放。"""
    original, renderer, _ = service
    settings = original.settings if limit == 6 else original.settings.model_copy(update={"overlay_task_concurrency": limit})
    overlays = OverlayRenders(original.store, renderer, settings)
    entered, active, peak = [], 0, 0
    run = renderer.run_worker

    async def scenario():
        """用事件控制 worker 完成时机，不启动真实浏览器或按机器速度推测并发。"""
        gate = asyncio.Event()

        async def blocked(directory, **options):
            """记录真正进入 worker 的任务数，放行后产出正常替身视频。"""
            nonlocal active, peak
            active += 1
            peak = max(active, peak)
            entered.append(directory.name)
            try:
                await gate.wait()
                return await run(directory, **options)
            finally:
                active -= 1

        monkeypatch.setattr(renderer, "run_worker", blocked)
        tasks = [await overlays.accept(PLACED, None, 3.0, None) for _ in range(limit + 1)]
        try:
            await wait_until(lambda: len(entered) == limit)
            assert sum(overlays.get(task)["status"] == "queued" for task in tasks) == 1
        finally:
            gate.set()
            await finish(overlays)
        assert all(overlays.get(task)["status"] == "succeeded" for task in tasks)

    asyncio.run(scenario())
    assert peak == limit and len(entered) == limit + 1


@pytest.mark.parametrize("failed", [False, True])
def test_stage_performance_is_private_and_preserves_failure(service, monkeypatch, caplog, failed) -> None:
    """阶段耗时与 worker 的绘制/编码计时进服务端日志；失败不补造探测和上传指标，也不公开正文。"""
    overlays, renderer, _ = service
    run = renderer.run_worker

    async def report(directory, **options):
        """模拟 worker 输出；未在指标契约里的文本不能进入性能摘要。"""
        result = await run(directory, **options)
        (directory / "renderer.json").write_text(json.dumps({"performance": {
            "bundle_ms": 1.5, "render_ms": 9, "resolved_concurrency": 1, "chunk_concurrency": 2,
            "code": "private-source", "url": "https://private.example",
            "chunks": [{"rendered_ms": 5, "encoded_ms": 4, "elapsed_ms": 9, "parallel_encoding": False,
                        "shared_memory_capture": True, "text": "private-content"}],
        }}))
        return result

    monkeypatch.setattr(renderer, "run_worker", report)
    if failed:
        renderer.failure = RuntimeError("worker stopped")
    with caplog.at_level(logging.INFO, logger=overlay_service.logger.name):
        task_id = run_task(overlays)
    entries = [record for record in caplog.records if record.msg.startswith("Remotion performance task_id=")]
    assert len(entries) == 1
    performance = json.loads(entries[0].args[-1])
    for key in ("plan_ms", "slot_wait_ms", "text_fit_ms", "prepare_ms", "worker_ms", "total_ms"):
        assert performance[key] >= 0
    if failed:
        assert "probe_ms" not in performance and "upload_ms" not in performance
        assert overlays.get(task_id)["status"] == "failed"
    else:
        assert performance["probe_ms"] >= 0 and performance["upload_ms"] >= 0
        assert performance["renderer"]["chunk_concurrency"] == 2
        assert performance["renderer"]["chunks"] == [{"rendered_ms": 5, "encoded_ms": 4, "elapsed_ms": 9,
                                                    "parallel_encoding": False, "shared_memory_capture": True}]
    assert "private-" not in entries[0].getMessage() and "https://" not in entries[0].getMessage()
    assert set(overlays.get(task_id)) == {"taskId", "status", "result", "error"}


# 场景：回调地址通过 schema 校验却无法被 httpx 解析（补零 IPv4、控制字符）时，终态也会处理完毕，不会反复待通知。
@pytest.mark.parametrize("url", ["http://192.168.001.010:8080/cb", "http://bus.example/cb\x7f"])
def test_unparseable_callback_url_is_given_up(service, caplog, url) -> None:
    """httpx.InvalidURL 不是 HTTPError：四次尝试后记为已处理，任务仍是 succeeded，日志有放弃记录。"""
    overlays, _, _ = service
    TypeAdapter(MediaURL).validate_python(url)  # 受理前的 schema 放行了这个地址
    with caplog.at_level(logging.WARNING, logger=overlay_service.logger.name):
        task_id = run_task(overlays, callback=url)
    assert overlays.get(task_id)["status"] == "succeeded" and is_notified(overlays, task_id)
    assert caplog.text.count("InvalidURL") == 4 and "重试 4 次后放弃" in caplog.text


# 场景：回调端点返回非 2xx 时按间隔重试，成功即停；全部失败则记录并放弃，日志写明状态码。
def test_callback_retries_error_statuses(service, callbacks, caplog) -> None:
    """两次 500 后第三次 200 即送达且不记放弃；四次都 500 则放弃（任务行记为已处理），日志含 HTTP 500 与放弃记录。"""
    overlays, _, _ = service
    answers = iter([500, 500, 200])

    async def flaky():
        """前两次 500，之后 200。"""
        return httpx.Response(next(answers))

    callbacks.respond = flaky
    with caplog.at_level(logging.WARNING, logger=overlay_service.logger.name):
        task_id = run_task(overlays, callback="https://bus.example/cb")
    assert len(callbacks.posted) == 3 and is_notified(overlays, task_id)
    assert caplog.text.count("收到 HTTP 500") == 2 and "放弃" not in caplog.text

    async def always_500():
        """始终 500。"""
        return httpx.Response(500)

    callbacks.respond, callbacks.posted[:] = always_500, []
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=overlay_service.logger.name):
        task_id = run_task(overlays, callback="https://bus.example/cb")
    assert len(callbacks.posted) == 4 and is_notified(overlays, task_id)
    assert caplog.text.count("收到 HTTP 500") == 4 and "重试 4 次后放弃" in caplog.text


# 场景：回调端点发出响应头后迟迟不结束时，每次尝试都有整体期限，重试照常推进。
def test_callback_attempt_has_an_overall_deadline(service, callbacks, monkeypatch, caplog) -> None:
    """超时按阶段计算的客户端挡不住滴灌响应：整体期限到点后记录 TimeoutError 并进入下一次。"""
    overlays, _, _ = service
    monkeypatch.setattr(overlay_service, "HTTP_TIMEOUT_SECONDS", 0.05)

    async def hangs():
        """永不返回，代表响应迟迟不结束。"""
        await asyncio.Event().wait()

    callbacks.respond = hangs
    with caplog.at_level(logging.WARNING, logger=overlay_service.logger.name):
        task_id = run_task(overlays, callback="https://bus.example/cb")
    assert len(callbacks.posted) == 4 and is_notified(overlays, task_id)
    assert caplog.text.count("TimeoutError") == 4


# 场景：片段超出成片结尾时，worker 请求中的合成仍覆盖最晚片段结尾，只渲染成片总帧数。
def test_request_spans_clips_cut_by_the_total(service) -> None:
    """成片 2 秒（60 帧）而片段 b 结束于第 90 帧：duration_in_frames 取 90，frames 仍为 60。"""
    overlays, renderer, _ = service
    run_task(overlays, duration=2.0)
    composition = renderer.requests[0]["composition"]
    assert (composition["duration_in_frames"], composition["frames"]) == (90, 60)


# 场景：每个隔离浏览器复用只读字体缓存，避免启动时扫描全部系统字体。
@pytest.mark.parametrize("cache_exists", [True, False])
def test_sandbox_reuses_font_cache_read_only_when_available(tmp_path, monkeypatch, cache_exists) -> None:
    """字体缓存存在时只读共享，不存在时仍能构建隔离命令，且不开放整个 /var。"""
    cache = Path("/var/cache/fontconfig")
    original_exists = Path.exists
    monkeypatch.setattr(Path, "exists", lambda path: cache_exists if path == cache else original_exists(path))
    renderer = Renderer(Settings(_env_file=None, data_dir=tmp_path))
    command = renderer.command(tmp_path, worker="overlay-worker.mjs")
    mounts = [command[index:index + 3] for index, argument in enumerate(command) if argument in {"--bind", "--ro-bind"}]
    assert (["--ro-bind", str(cache), str(cache)] in mounts) is cache_exists
    assert ["--bind", str(cache), str(cache)] not in mounts
    assert not any(mount[1] == "/var" for mount in mounts)


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


# 场景：任务把默认 30 分钟渲染期限与相应的 CPU 上限交给沙箱 worker。
def test_task_passes_the_overlay_cpu_cap_to_the_worker(service) -> None:
    """worker 收到 1800 秒超时，沙箱 CPU 上限同时随渲染期限放宽。"""
    overlays, renderer, _ = service
    run_task(overlays, duration=2.0)
    assert renderer.timeout_seconds == 1800
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
    with caplog.at_level(logging.WARNING, logger=overlay_service.logger.name):
        task_id = run_task(overlays, callback="https://bus.example/cb")
    view = overlays.get(task_id)
    assert view["status"] == "failed" and view["result"] is None
    assert view["error"] == {"code": "render_failed", "message": message}
    assert callbacks.posted == [("https://bus.example/cb", {
        "taskId": task_id, "status": "failed", "videoUrl": None, "errorMessage": message,
    })]
    assert uploads == []
    assert str(failure) in caplog.text  # 运维仍能在日志中看到完整根因


# 片段结束于 302 秒（片段 b 起点 300 秒 + 资产自身 2 秒），超过 300 秒上限。
LONG = [placement("a", start=0), placement("b", start=300)]


# 场景：时长已知时受理阶段就拒绝超限请求（显式 duration 或片段自身结尾），不写任务行。
def test_accept_enforces_the_limit_on_known_lengths(service) -> None:
    """显式 duration 超限 422；未提供时片段结尾超限同样 422；上限内的请求被受理。"""
    overlays, _, _ = service
    with pytest.raises(HTTPException) as caught:
        asyncio.run(overlays.accept(PLACED, None, 300.001, None))
    assert caught.value.status_code == 422 and "超过上限 300" in caught.value.detail
    assert task_rows(overlays) == 0
    asyncio.run(overlays.accept(PLACED, None, 300.0, None))  # 恰好等于上限
    with pytest.raises(HTTPException) as caught:
        asyncio.run(overlays.accept(LONG, None, None, None))
    assert caught.value.status_code == 422 and "超过上限 300" in caught.value.detail
    assert task_rows(overlays) == 1


# 场景：有 audioUrl 时 duration 被忽略，不能因它超限而拒绝；音频本身过长要到探测后才知道。
def test_audio_length_is_checked_after_probing(service, monkeypatch) -> None:
    """audioUrl 加超大 duration 被受理；探测出的音频时长超限时任务以 duration_too_long 失败。"""
    overlays, renderer, _ = service

    async def probed(url: str, timeout: float) -> float:
        """替身探测：配音有 500 秒，超过 300 秒上限。"""
        return 500.0

    monkeypatch.setattr(overlay_render, "audio_duration", probed)
    view = overlays.get(run_task(overlays, duration=9999.0, audio="https://x/tts.mp3"))
    assert view["status"] == "failed" and view["error"]["code"] == "duration_too_long"
    assert "超过上限 300" in view["error"]["message"] and renderer.requests == []


# 场景：缺少渲染所需的工具或文件时受理阶段就 503，而不是接受后才失败。
def test_accept_refuses_a_host_that_cannot_render(service, monkeypatch) -> None:
    """环境检查报告缺失项时返回 503、写服务端日志，且不创建任务。"""
    overlays, _, _ = service
    monkeypatch.setattr(overlay_service, "environment_problem", lambda settings: "ffprobe")
    with pytest.raises(HTTPException) as caught:
        asyncio.run(overlays.accept(PLACED, None, 3.0, None))
    assert caught.value.status_code == 503 and task_rows(overlays) == 0


# 场景：读不到配音时长时对调用方只给固定说明，服务端日志留下异常类型而不含带签名的地址。
def test_unreadable_audio_is_logged_without_the_url(monkeypatch, caplog) -> None:
    """ffprobe 无法启动时任务得到 audio_duration_invalid，日志只记 FileNotFoundError。"""
    async def missing(*args, **kwargs):
        """模拟系统里没有 ffprobe。"""
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
    present = {"ffprobe", "bwrap", "prlimit", "taskset", "node", "ffmpeg"}
    monkeypatch.setattr(overlay_render.shutil, "which", lambda name: f"/usr/bin/{name}" if name in present else None)
    assert overlay_render.environment_problem(settings) is None
    for tool in ("ffprobe", "bwrap", "prlimit", "taskset", "node", "ffmpeg"):
        present.discard(tool)
        assert overlay_render.environment_problem(settings) == tool
        present.add(tool)
    settings.overlay_chunked = True
    present.discard("ffmpeg")
    assert overlay_render.environment_problem(settings) == "ffmpeg"
    present.add("ffmpeg")
    assert overlay_render.environment_problem(settings) is None
    for path in files.values():
        path.unlink()
        assert overlay_render.environment_problem(settings) == str(path)
        path.write_bytes(b"x")


# 场景：重启后恢复未终态任务并完成；终态任务不会被重复渲染。
def test_resume_reruns_unfinished_tasks(service) -> None:
    """queued 任务经 resume 渲染成功；已成功任务不再产生新的渲染请求。"""
    overlays, renderer, _ = service
    overlays._insert("11111111-1111-1111-1111-111111111111", {"request": stored_request()})
    run_resume(overlays)
    overlays.resumed = False
    run_resume(overlays)
    assert overlays.get("11111111-1111-1111-1111-111111111111")["status"] == "succeeded"
    assert len(renderer.requests) == 1


# 场景：保留期清理只删除早已结束且没有待发通知的任务，未结束或仍欠回调的任务留给恢复。
def test_prune_drops_only_old_finished_tasks_with_nothing_to_deliver(service) -> None:
    """早于截止时间的成功/失败任务（无回调或已通知）删除；运行中、仍欠回调或较新的任务保留。"""
    overlays, _, _ = service
    request = stored_request()
    cases = {
        "a" * 32: ("succeeded", {}, None),
        "b" * 32: ("failed", {"notified": True}, "https://bus.example/callback"),
        "c" * 32: ("failed", {}, "https://bus.example/callback"),
        "d" * 32: ("rendering", {}, None),
    }
    for task_id, (status, changes, callback) in cases.items():
        overlays._insert(task_id, {"request": {**request, "callback_url": callback}})
        overlays._save(task_id, status, **changes)
    overlays._insert("e" * 32, {"request": request})
    overlays._save("e" * 32, "succeeded")
    with overlays.store.transaction() as db:
        db.execute(update(overlay_service.overlay_renders).where(overlay_service.overlay_renders.c.id != "e" * 32)
                   .values(created_at="2020-01-01T00:00:00+00:00"))
    overlay_service.prune_finished(overlays.store, datetime.now(UTC) - overlay_service.FINISHED_RETENTION)
    with overlays.store.reading() as db:
        kept = sorted(db.execute(select(overlay_service.overlay_renders.c.id)).scalars())
    assert kept == ["c" * 32, "d" * 32, "e" * 32]


# 场景：运行时第一次建立就恢复叠加任务，不论之后先访问哪条路由，也只恢复一次。
def test_runtime_start_recovers_overlay_tasks(tmp_path, monkeypatch, template_db) -> None:
    """runtime_for 建立运行时后调用 resume 一次；再次取用不重复恢复。"""
    settings = Settings(_env_file=None, data_dir=tmp_path / "state")
    application = create_template_app(
        settings, provider=SimpleNamespace(settings=settings), renderer=SimpleNamespace(settings=settings),
    )
    resumed = []
    monkeypatch.setattr(OverlayRenders, "resume", lambda self: resumed.append(self))

    async def scenario():
        """连续两次取用同一个应用的运行时。"""
        return await runtime_for(application.state), await runtime_for(application.state)

    first, second = asyncio.run(scenario())
    assert first is second and resumed == [first.overlays]


# 场景：工作区打开或服务重启后几个请求同时到达，运行时只建立并恢复一次。
def test_concurrent_first_requests_share_one_runtime(tmp_path, monkeypatch, template_db) -> None:
    """初始化在线程中等待时，并发的 runtime_for 等待同一次建立，不重复恢复任务或启动清理。"""
    settings = Settings(_env_file=None, data_dir=tmp_path / "state")
    application = create_template_app(
        settings, provider=SimpleNamespace(settings=settings), renderer=SimpleNamespace(settings=settings),
    )
    initialized, resumed, sweeps = [], [], []

    def slow_initialize(self) -> None:
        """模拟建表与恢复耗时，让其他请求在初始化完成前到达。"""
        time.sleep(0.2)
        initialized.append(self)

    monkeypatch.setattr(Runtime, "initialize", slow_initialize)
    monkeypatch.setattr(Runtime, "start_maintenance", lambda self: sweeps.append(self))
    monkeypatch.setattr(OverlayRenders, "resume", lambda self: resumed.append(self))

    async def scenario():
        """三个首请求同时取用运行时。"""
        return await asyncio.gather(*(runtime_for(application.state) for _ in range(3)))

    runtimes = asyncio.run(scenario())
    assert len({id(runtime) for runtime in runtimes}) == 1
    assert initialized == sweeps == [runtimes[0]] and resumed == [runtimes[0].overlays]


# 场景：路由依赖取用 Remotion 运行时里的任务服务，并在取用时触发（重试）恢复。
def test_overlay_dependency_borrows_the_runtime_service(service, monkeypatch) -> None:
    """overlay_renders() 返回运行时持有的服务对象，并调用一次 resume。"""
    overlays, _, _ = service
    resumed = []
    monkeypatch.setattr(overlays, "resume", lambda: resumed.append(True))
    monkeypatch.setattr(remotion_templates_app.state, "runtime", SimpleNamespace(overlays=overlays), raising=False)
    assert asyncio.run(overlay_renders()) is overlays and resumed == [True]


# 场景：恢复时损坏的记录被记录并跳过，不阻塞其余任务，也不让首个请求报 500。
def test_resume_skips_damaged_rows(service, caplog) -> None:
    """无法解析的 JSON、缺 request 的终态行、非对象 JSON 都跳过；正常的排队任务仍被渲染完成。"""
    overlays, renderer, _ = service
    insert_row(overlays, "bad-json", "queued", "2026-01-01T00:00:00+00:00", "{not json")
    insert_row(overlays, "no-request", "succeeded", "2026-01-01T00:00:01+00:00", '{"notified": false}')
    insert_row(overlays, "not-object", "queued", "2026-01-01T00:00:02+00:00", "[]")
    good = "33333333-3333-3333-3333-333333333333"
    insert_row(overlays, good, "queued", "2026-01-01T00:00:03+00:00", json.dumps({"request": stored_request()}))
    with caplog.at_level(logging.ERROR, logger=overlay_service.logger.name):
        run_resume(overlays)
    assert overlays.resumed and overlays.get(good)["status"] == "succeeded" and len(renderer.requests) == 1
    assert all(task_id in caplog.text for task_id in ("bad-json", "no-request", "not-object"))


# 场景：重启后只补发还没处理完的终态通知；已送达或已放弃的不会重复投递。
def test_resume_resends_only_unnotified_callbacks(service, callbacks) -> None:
    """两个已成功且带回调地址的任务，只有未标记 notified 的那个重新收到 POST。"""
    overlays, _, _ = service
    request = stored_request(callback_url="https://bus.example/cb")
    pending, done = "44444444-4444-4444-4444-444444444444", "55555555-5555-5555-5555-555555555555"
    for task_id, extra in ((pending, {}), (done, {"notified": True})):
        data = {"request": request, "video_url": f"https://zos.example/{task_id}.webm", **extra}
        insert_row(overlays, task_id, "succeeded", "2026-01-01T00:00:00+00:00", json.dumps(data))
    run_resume(overlays)
    assert [body["taskId"] for _, body in callbacks.posted] == [pending] and is_notified(overlays, pending)


# 场景：恢复按创建时间从早到晚重新启动任务，而不是按主键或存储顺序。
def test_resume_requeues_oldest_first(service, monkeypatch) -> None:
    """较早创建的任务（主键较大、较晚写入）先启动；之后各任务何时轮到渲染名额取决于各自的进度。"""
    overlays, _, _ = service
    spawned = []

    async def record(task_id: str) -> None:
        """只记录被启动的任务，不执行。"""
        spawned.append(task_id)

    monkeypatch.setattr(overlays, "_run", record)
    request = json.dumps({"request": stored_request()})
    newer, older = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
    insert_row(overlays, newer, "queued", "2026-01-02T00:00:00+00:00", request)
    insert_row(overlays, older, "queued", "2026-01-01T00:00:00+00:00", request)
    run_resume(overlays)
    assert spawned == [older, newer]


# 场景：任务表暂时读不出来时不吞掉恢复机会：不抛异常，也不标记已恢复，下次访问再试。
def test_resume_retries_when_the_table_is_unreadable(service, monkeypatch) -> None:
    """第一次读表失败时 resumed 仍为 False，第二次成功后才置为 True。"""
    overlays, _, _ = service
    real = overlays.store.reading

    def locked():
        """模拟数据库被锁，读表失败。"""
        raise OperationalError("SELECT", {}, Exception("database is locked"))

    monkeypatch.setattr(overlays.store, "reading", locked)
    overlays.resume()
    assert overlays.resumed is False
    monkeypatch.setattr(overlays.store, "reading", real)
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
            raise OperationalError("UPDATE", {}, Exception("database is locked"))
        return real(task_id, new_status, **changes)

    overlays._save = save


# 场景：数据库短暂被锁时终态写入自动重试，任务照常成功并通知。
def test_transient_database_lock_is_retried(service, callbacks) -> None:
    """成功状态前两次写入失败、第三次成功：任务 succeeded 且回调送达，不被改成失败。"""
    overlays, _, _ = service
    lock_writes(overlays, "succeeded", 2)
    task_id = run_task(overlays, callback="https://bus.example/cb")
    assert overlays.get(task_id)["status"] == "succeeded" and len(callbacks.posted) == 1


# 场景：上传成功后成功状态始终写不进去：任务不能被改成失败，也不通知；重启恢复后重新完成。
def test_unwritable_success_keeps_the_task_recoverable(service, callbacks, caplog) -> None:
    """GET 仍显示 uploading（ZOS 对象已存在）；换上可写的库后 resume 重新渲染并成功，沿用同一个对象键。"""
    overlays, renderer, uploads = service
    lock_writes(overlays, "succeeded", None)
    with caplog.at_level(logging.ERROR, logger=overlay_service.logger.name):
        task_id = run_task(overlays, callback="https://bus.example/cb")  # 后台协程不得带着未处理异常结束
    assert overlays.get(task_id)["status"] == "uploading" and len(uploads) == 1 and callbacks.posted == []
    assert "状态暂无法保存" in caplog.text
    restarted = OverlayRenders(overlays.store, renderer, overlays.settings)
    run_resume(restarted)
    assert restarted.get(task_id)["status"] == "succeeded" and len(callbacks.posted) == 1
    assert [key for key, _, _ in uploads] == [f"imv/overlay_render/{task_id}.webm"] * 2


# 场景：渲染失败后失败状态也写不进去：同样不崩溃、不通知，重启后任务仍会被处理到终态。
def test_unwritable_failure_keeps_the_task_recoverable(service, callbacks) -> None:
    """失败状态写入一直失败时任务停在 rendering；恢复后再次失败并正常记录、通知。"""
    overlays, renderer, _ = service
    renderer.failure = RuntimeError("worker crashed")
    lock_writes(overlays, "failed", None)
    task_id = run_task(overlays, callback="https://bus.example/cb")
    assert overlays.get(task_id)["status"] == "rendering" and callbacks.posted == []
    restarted = OverlayRenders(overlays.store, renderer, overlays.settings)
    run_resume(restarted)
    assert restarted.get(task_id)["status"] == "failed"
    assert [body["status"] for _, body in callbacks.posted] == ["failed"]


async def wait_until(condition, seconds: float = 5) -> None:
    """轮询等待条件成立；超时即抛 TimeoutError，避免卡住的用例无限等待。"""
    async def poll():
        """每 10 毫秒检查一次条件。"""
        while not condition():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), seconds)


# 场景：配音音频探测很慢时不占用渲染名额，后面排队的任务照常渲染并完成。
def test_slow_audio_probe_does_not_hold_the_render_slot(service, monkeypatch) -> None:
    """第一个任务卡在远程音频探测，第二个（显式 duration）仍能渲染、上传并成功；放行后第一个也完成。"""
    overlays, renderer, uploads = service
    released = []

    async def probed(url: str, timeout: float) -> float:
        """替身探测：被放行之前一直不返回。"""
        while not released:
            await asyncio.sleep(0.01)
        return 3.0

    monkeypatch.setattr(overlay_render, "audio_duration", probed)

    async def scenario():
        """先受理卡住的音频任务，再受理普通任务，并在放行前确认后者已完成。"""
        slow = await overlays.accept(PLACED, "https://x/tts.mp3", None, None)
        fast = await overlays.accept(PLACED, None, 3.0, None)
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
        """受理两个任务，确认先上传的那个卡在 ZOS 时两个渲染都已开始。"""
        tasks = [await overlays.accept(PLACED, None, 3.0, None) for _ in range(2)]
        # 两个渲染都已开始，而先上传的那个还卡在 ZOS：上传没有占着渲染名额（先后顺序由调度决定）。
        await wait_until(lambda: len(renderer.requests) == 2 and calls)
        blocked = calls[0].removeprefix("imv/overlay_render/").removesuffix(".webm")
        assert overlays.get(blocked)["status"] == "uploading"
        gate.set()
        await finish(overlays)
        return tasks

    tasks = asyncio.run(scenario())
    assert [overlays.get(task_id)["status"] for task_id in tasks] == ["succeeded", "succeeded"]


@pytest.fixture
def overlay_client(service, client: TestClient):
    """让路由使用测试里的任务服务；返回测试客户端与服务，结束时还原依赖覆盖。"""
    overlays, _, _ = service

    async def provide():
        """依赖替身：返回测试创建的任务服务。"""
        return overlays

    app.dependency_overrides[overlay_renders] = provide
    yield client, overlays
    app.dependency_overrides.pop(overlay_renders, None)


# 场景：duration 与合成接口的 videoDuration 一样严格：布尔值、数字字符串和非正数都是 422。
@pytest.mark.parametrize("duration", [True, "7.5", 0, -1])
def test_duration_is_strict(overlay_client, duration) -> None:
    """布尔值、数字字符串和非正数的 duration 返回 422 且不创建任务。"""
    client, overlays = overlay_client
    response = client.post("/api/v1/overlay-renders", json={"placements": WIRE, "duration": duration})
    assert response.status_code == 422
    assert task_rows(overlays) == 0


# 场景：HTTP 契约——受理返回 taskId，GET 返回状态；未知任务 404。
def test_routes_accept_and_query(overlay_client) -> None:
    """路由使用 camelCase 请求体，整数与小数 duration 都可用，未知字段（含已移除的 styleId）拒绝，查询未知任务 404。"""
    client, _ = overlay_client
    created = client.post("/api/v1/overlay-renders", json={"placements": WIRE, "duration": 3})
    assert created.status_code == 200
    assert client.post("/api/v1/overlay-renders", json={"placements": WIRE, "duration": 7.5}).status_code == 200
    assert client.post("/api/v1/overlay-renders", json={"placements": WIRE, "bogus": 1}).status_code == 422
    assert client.post("/api/v1/overlay-renders", json={"placements": WIRE, "styleId": str(uuid4())}).status_code == 422
    assert client.get(f"/api/v1/overlay-renders/{uuid4()}").status_code == 404
    task_id = created.json()["taskId"]
    assert client.get(f"/api/v1/overlay-renders/{task_id}").json()["taskId"] == task_id


# 场景：摆放的每个字段都严格校验：缺失或类型错误、负起点、非正时长、多余字段（含已移除的 order）和超过 100 个都是 422。
@pytest.mark.parametrize("placements", [
    [{"start": 0}], [{"spriteId": "", "start": 0}], [{"spriteId": "x" * 65, "start": 0}], [{"spriteId": "a"}],
    [{"spriteId": "a", "start": -1}], [{"spriteId": "a", "start": True}], [{"spriteId": "a", "start": "1"}],
    [{"spriteId": "a", "start": 0, "duration": 0}], [{"spriteId": "a", "start": 0, "duration": -2}],
    [{"spriteId": "a", "start": 0, "duration": "3"}], [{"spriteId": "a", "start": 0, "order": 1}],
    [{"spriteId": "a", "start": 0}] * 101, "a", None,
], ids=["no-sprite", "empty-sprite", "long-sprite", "no-start", "negative-start", "bool-start", "string-start",
        "zero-duration", "negative-duration", "string-duration", "order-removed", "over-100", "not-a-list", "null"])
def test_invalid_placements_are_rejected(overlay_client, placements) -> None:
    """摆放字段不合法时返回 422 且不创建任务；缺少 placements 同样如此。"""
    client, overlays = overlay_client
    response = client.post("/api/v1/overlay-renders", json={"placements": placements, "duration": 3})
    assert response.status_code == 422 and task_rows(overlays) == 0
    assert client.post("/api/v1/overlay-renders", json={"duration": 3}).status_code == 422


# 场景：空摆放和未知 Sprite 在受理阶段就是 422 并给出原因，不创建任务。
def test_accept_rejects_empty_and_unknown_placements(overlay_client) -> None:
    """空列表得到 no_placements 的说明，引用不存在的 Sprite 得到 sprite_missing 的说明。"""
    client, overlays = overlay_client
    empty = client.post("/api/v1/overlay-renders", json={"placements": [], "duration": 3})
    assert empty.status_code == 422 and "没有 Remotion 资产摆放" in empty.json()["detail"]
    unknown = client.post("/api/v1/overlay-renders", json={"placements": [{"spriteId": "missing", "start": 0}], "duration": 3})
    assert unknown.status_code == 422 and "Sprite missing 不存在" in unknown.json()["detail"]
    assert task_rows(overlays) == 0


# 场景：受理时把摆放（含先后、起点和时长）原样存进任务行，渲染与恢复都只用这份记录。
def test_accept_stores_the_placements_with_the_task(service) -> None:
    """任务行的 request.placements 与受理时的列表一致，缺省时长保存为 null。"""
    overlays, _, _ = service
    task_id = run_task(overlays, placements=[placement("b", start=2, duration=1), placement("a")])
    _, data = overlays._read(task_id)
    assert data["request"]["placements"] == [
        {"sprite_id": "b", "start": 2.0, "duration": 1.0}, {"sprite_id": "a", "start": 0.0, "duration": None},
    ]


# 场景：恢复的任务按受理时保存的摆放渲染，先后、起点和时长原样落到 worker 请求。
def test_recovered_task_renders_its_stored_placements(service) -> None:
    """重启后 resume 渲染的是任务行里保存的摆放，而不是任何别处的当前数据。"""
    overlays, renderer, _ = service
    stored = [placement("b", start=2, duration=1), placement("a")]
    request = stored_request(placements=[item.model_dump(mode="json") for item in stored])
    overlays._insert("11111111-1111-1111-1111-111111111111", {"request": request})
    run_resume(overlays)
    assert [(i["start_frame"], i["duration_frames"]) for i in renderer.requests[0]["instances"]] == [(60, 30), (0, 60)]


# 场景：GET 只返回公开视图：三种状态的形状固定，且不泄漏请求里的音频或回调地址。
def test_query_returns_only_the_public_view(overlay_client) -> None:
    """排队、成功、失败各自返回 taskId/status/result/error，不含 request 及其中带令牌的地址。"""
    client, overlays = overlay_client
    request = stored_request(audio_url="https://x/tts.mp3?token=secret", duration=None,
                             callback_url="https://bus.example/cb?token=secret")
    error = {"code": "render_failed", "message": "叠加视频渲染失败"}
    rows = {
        "queued": ("66666666-6666-6666-6666-000000000001", {"request": request}),
        "succeeded": ("66666666-6666-6666-6666-000000000002",
                      {"request": request, "video_url": "https://zos.example/a.webm", "notified": True}),
        "failed": ("66666666-6666-6666-6666-000000000003", {"request": request, "error": error}),
    }
    for status, (task_id, data) in rows.items():
        insert_row(overlays, task_id, status, "2026-01-01T00:00:00+00:00", json.dumps(data))
    responses = {status: client.get(f"/api/v1/overlay-renders/{task_id}") for status, (task_id, _) in rows.items()}
    assert all(response.status_code == 200 for response in responses.values())
    expected = {
        "queued": {"status": "queued", "result": None, "error": None},
        "succeeded": {"status": "succeeded", "result": {"videoUrl": "https://zos.example/a.webm"}, "error": None},
        "failed": {"status": "failed", "result": None, "error": error},
    }
    for status, (task_id, _) in rows.items():
        assert responses[status].json() == {"taskId": task_id, **expected[status]}
    assert "secret" not in "".join(response.text for response in responses.values())


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


def decoded_alpha(video: Path, index: int, pixel: int = 0) -> float:
    """用 libvpx 解码一帧并返回第 pixel 个像素（按行展开，默认左上角）的 alpha；原生 vp9 解码器会丢弃透明通道。"""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-c:v", "libvpx-vp9", "-i", str(video), "-vf", f"select=eq(n\\,{index})",
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        capture_output=True, check=True,
    ).stdout
    return raw[pixel * 4 + 3] / 255


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


# 场景：总线原样转发模板轨道的 editor：参数覆盖合并进配置，宿主变换绕内容区域，二者都进入 worker 请求。
def test_plan_applies_the_placement_overrides_and_transform() -> None:
    """渲染计划里每个片段带合并后的配置与宿主层；没有 editor 的片段保持发布默认值。"""
    edited = OverlayPlacement.model_validate({"spriteId": "c", "start": 0, "editor": {"remotion": {
        "parameters": [{"key": "text", "stringValue": "新品"}], "transform": {"x": 540, "scale": 2},
    }}})
    overlay = plan_overlay([placement("c", start=1), edited], PUBLICATIONS, 3, 120)
    plain, changed = overlay.instances
    assert plain["config"]["text"] == "标题" and plain["host"]["transform"] == {}
    assert changed["config"]["text"] == "新品" and changed["config"]["fontSize"] == 64
    assert changed["host"] == {"box": {"x": 90, "y": 760, "width": 900, "height": 400}, "transform": {"x": 540, "scale": 2}}


# 场景：不符合发布参数契约的覆盖在受理阶段就是 422，不创建任务；editor 只能带 remotion。
def test_accept_rejects_overrides_outside_the_contract(overlay_client) -> None:
    """超出范围的覆盖说明是哪个 Sprite 与原因；editor 里的 IMS 字段同样拒绝。"""
    client, overlays = overlay_client
    bad = {"spriteId": "c", "start": 0, "editor": {"remotion": {"parameters": [{"key": "fontSize", "numberValue": 999}]}}}
    response = client.post("/api/v1/overlay-renders", json={"placements": [bad], "duration": 3})
    assert response.status_code == 422 and "Sprite c 的参数覆盖无效：参数 fontSize 超出允许范围" in response.json()["detail"]
    ims = {"spriteId": "c", "start": 0, "editor": {"title": "标题"}}
    assert client.post("/api/v1/overlay-renders", json={"placements": [ims], "duration": 3}).status_code == 422
    assert task_rows(overlays) == 0


# 场景：受理时 editor 随摆放存进任务行，重启恢复后按保存的覆盖渲染。
def test_stored_overrides_survive_recovery(service) -> None:
    """任务行保存 editor；恢复的任务把同样的配置和变换交给 worker。"""
    overlays, renderer, _ = service
    edited = OverlayPlacement.model_validate({"spriteId": "c", "start": 0, "editor": {"remotion": {
        "parameters": [{"key": "fontWeight", "numberValue": 700}], "transform": {"opacity": 0.5},
    }}})
    request = stored_request(placements=[edited.model_dump(mode="json")])
    overlays._insert("22222222-2222-2222-2222-222222222222", {"request": request})
    run_resume(overlays)
    [instance] = renderer.requests[0]["instances"]
    assert instance["config"]["fontWeight"] == 700 and instance["host"]["transform"] == {"opacity": 0.5}


# ---- 单个 Sprite 渲染（POST /api/sprites/render）：style + content + time，一个请求得到一个完整视频 ----
# 字效 T 带 text/keywords 内容参数（发布时固定为这两个字段名）；滤镜 F 不接收内容。
T = pb.PublishedSprite()
T.CopyFrom(published_sprite("t"))
T.text_prop, T.keywords_prop = "text", "keywords"
F = sprite("f")
F.kind = pb.SPRITE_KIND_FILTER_OVERLAY
PUBLICATIONS.update({"t": T, "f": F})


def cue(text: str, start: int, end: int, *keywords: str) -> pb.SpriteCue:
    """一条字幕切片：文字、帧区间与关键词。"""
    return pb.SpriteCue(text=text, start_frame=start, end_frame=end, keywords=list(keywords))


def render_input(**changes) -> pb.SpriteRenderInput:
    """默认请求：字效 t 的 10 秒标题，带文字与关键词。"""
    values = {"sprite_id": "t", "text": "新品上市", "keywords": ["新品"], "effect_total_frames": 300, **changes}
    return pb.SpriteRenderInput(**values)


# 场景：标题整段一条文字：Sprite 读到完整效果时长；content 写入文字与关键词，style 覆盖与宿主变换照常合并。
def test_sprite_render_shows_one_text_for_the_whole_effect() -> None:
    """一个实例从第 0 帧持续 300 帧（Sprite 读到的 durationInFrames），输出也是 300 帧。"""
    message = render_input(resolved_style=[pb.SpriteParameterOverride(key="fontWeight", value=pb.ScalarValue(number_value=700))],
                           transform=pb.SpriteHostTransform(x=540, scale=2))
    overlay = plan_sprite_render(message, T)
    assert (overlay.width, overlay.height, overlay.fps, overlay.frames) == (1080, 1920, 30, 300)
    [instance] = overlay.instances
    assert (instance["start_frame"], instance["duration_frames"]) == (0, 300)
    assert instance["config"]["text"] == "新品上市" and instance["config"]["keywords"] == ["新品"]
    assert instance["config"]["fontWeight"] == 700 and instance["host"]["transform"] == {"x": 540, "scale": 2}


# 场景：字幕按总线切分的时间逐条显示：每个切片一个实例、各带文字与关键词；切片之间透明。
# 不短于 1.5 秒（45 帧）的切片按自身长度播放，Sprite 读到的时长就是切片长度；更短的切片不播出入场，
# 按资产的预览长度（90 帧）定格在中间帧，只显示切片自己的帧数。
def test_sprite_render_shows_each_subtitle_cue_in_its_own_frames() -> None:
    """60 帧的切片按自身长度播放；18 帧与 14 帧的切片定格在 90 帧预览的第 45 帧，各显示 18、14 帧。"""
    message = render_input(text="", keywords=[], effect_total_frames=200,
                           cues=[cue("看新闻了吗", 7, 25), cue("AI把数学圈", 52, 112, "数学圈"), cue("搞炸了", 112, 126)])
    overlay = plan_sprite_render(message, T)
    assert overlay.frames == 200
    assert [(item["start_frame"], item["duration_frames"], item.get("hold"), item["config"]["text"], item["config"]["keywords"]) for item in overlay.instances] == [
        (7, 90, {"frame": 45, "frames": 18, "length": 90}, "看新闻了吗", []),
        (52, 60, None, "AI把数学圈", ["数学圈"]),
        (112, 90, {"frame": 45, "frames": 14, "length": 90}, "搞炸了", []),
    ]


# 场景：定格只针对字效：显示不足 1.5 秒的标题同样定格；滤镜等视觉 Sprite 本身就是动画，再短也按自身长度播放。
def test_only_short_text_effects_hold_still() -> None:
    """1 秒的标题定格；1 秒的滤镜按 30 帧播放；正好 1.5 秒的标题按自身长度播放。
    预览比字幕还短时，实例持续字幕的帧数，Sprite 仍读到预览长度，字幕不会提前消失。"""
    [title] = plan_sprite_render(render_input(effect_total_frames=30), T).instances
    assert (title["duration_frames"], title["hold"]) == (90, {"frame": 45, "frames": 30, "length": 90})
    brief = pb.PublishedSprite()
    brief.CopyFrom(T)
    brief.canvas.preview_frames = 9
    [held] = plan_sprite_render(render_input(effect_total_frames=36), brief).instances
    assert (held["duration_frames"], held["hold"]) == (36, {"frame": 4, "frames": 36, "length": 9})
    [tint] = plan_sprite_render(render_input(sprite_id="f", text="", keywords=[], effect_total_frames=30), F).instances
    assert (tint["duration_frames"], "hold" in tint) == (30, False)
    [long] = plan_sprite_render(render_input(effect_total_frames=45), T).instances
    assert (long["duration_frames"], "hold" in long) == (45, False)


# 场景：固定 15 秒连续帧区间，尾块保留余数；短片不硬凑六块。
@pytest.mark.parametrize("fps", [24, 30, 60])
@pytest.mark.parametrize("seconds,extra,count", [(0, 1, 1), (15, -1, 1), (15, 0, 1), (15, 1, 2), (30, 0, 2), (30, 1, 3), (70, 1, 5)])
def test_fixed_chunks_cover_every_frame_once(monkeypatch, fps, seconds, extra, count) -> None:
    """15 秒边界前后均没有重叠或缺帧，除尾块外均为完整 15 秒。"""
    frames = seconds * fps + extra
    ranges = chunk_ranges(frames, fps)
    assert len(ranges) == count
    assert [frame for first, last in ranges for frame in range(first, last + 1)] == list(range(frames))
    sizes = [last - first + 1 for first, last in ranges]
    assert sizes[:-1] == [15 * fps] * (count - 1)
    assert 1 <= sizes[-1] <= 15 * fps
    monkeypatch.setattr(sprite_render.shutil, "which", lambda name: None)
    settings = Settings(_env_file=None)
    assert join_problem(plan_sprite_render(render_input(effect_total_frames=450), T), settings) is None
    assert join_problem(plan_sprite_render(render_input(effect_total_frames=451), T), settings) == "ffmpeg"


# 场景：时长 0.5～120 秒；字效带 text 或 cues 之一，切片按起点排列、互不重叠、至少一帧且在时长之内；其他分类不接收内容；内容不能伪装成样式覆盖。
@pytest.mark.parametrize(("sprite_id", "changes", "code"), [
    ("t", {"effect_total_frames": 14}, "duration_invalid"),
    ("t", {"effect_total_frames": 3601}, "duration_too_long"),
    ("t", {"text": ""}, "content_invalid"),
    ("t", {"cues": [cue("字幕", 0, 30)]}, "content_invalid"),
    ("t", {"text": "", "keywords": [], "cues": [cue("一", 0, 30), cue("二", 20, 50)]}, "content_invalid"),
    ("t", {"text": "", "keywords": [], "cues": [cue("零帧", 10, 10)]}, "content_invalid"),
    ("t", {"text": "", "keywords": [], "cues": [cue("出界", 290, 301)]}, "content_invalid"),
    ("t", {"text": "", "keywords": [], "cues": [cue("", 0, 30)]}, "content_invalid"),
    ("t", {"text": "", "cues": [cue("字幕", 0, 30)]}, "content_invalid"),
    ("f", {}, "content_invalid"),
    ("a", {"keywords": ["标题"]}, "content_invalid"),
    ("t", {"resolved_style": [pb.SpriteParameterOverride(key="text", value=pb.ScalarValue(string_value="别的"))]}, "placement_invalid"),
    ("t", {"resolved_style": [pb.SpriteParameterOverride(key="fontSize", value=pb.ScalarValue(number_value=999))]}, "placement_invalid"),
    ("t", {"text": "超" * 41}, "placement_invalid"),
    ("t", {"transform": pb.SpriteHostTransform(rotation=270)}, "placement_invalid"),
    ("t", {"output": pb.SpriteCanvas(width=1920, height=1080, fps=30)}, "canvas_mismatch"),
], ids=["too-short", "too-long", "text-effect-without-content", "text-and-cues", "overlapping-cues", "cue-without-frames",
        "cue-past-end", "empty-cue", "keywords-outside-cues", "filter-with-text", "no-keyword-parameter",
        "content-as-style", "style-out-of-range", "text-over-limit", "transform-out-of-range", "other-canvas"])
def test_sprite_render_refuses_inputs_outside_the_contract(sprite_id, changes, code) -> None:
    """每种拒绝都是带稳定错误码的 OverlayError，受理阶段转成 422。"""
    message = render_input(sprite_id=sprite_id, **changes)
    with pytest.raises(OverlayError) as caught:
        plan_sprite_render(message, PUBLICATIONS[sprite_id])
    assert caught.value.code == code, caught.value.message


# 两行字效：t 加上 lineStartFrames（两项），默认文字两行。
M = pb.PublishedSprite()
M.CopyFrom(T)
M.config_schema_json = json.dumps({**json.loads(T.config_schema_json), "properties": {
    **json.loads(T.config_schema_json)["properties"],
    "lineStartFrames": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 3600}, "minItems": 2, "maxItems": 2},
}})
M.default_config_json = json.dumps({**json.loads(T.default_config_json), "text": "一\n二", "lineStartFrames": [0, 30]}, ensure_ascii=False)


def sentence_cue(text: str, start: int, end: int, sentence: int, *keywords: str) -> pb.SpriteCue:
    """一条带句子序号的字幕切片。"""
    return pb.SpriteCue(text=text, start_frame=start, end_frame=end, sentence=sentence, keywords=list(keywords))


# 场景：多行字效把同一句里最多两个连续切片合成一组：第二行在第二个切片开始时出现，整组在最后一个切片结束时清空；
# 换句时即使没有填满也开始新组；没给句子序号时只按行数分组。
@pytest.mark.parametrize(("sentences", "expected"), [
    ((1, 1, 1, 2, 2), [(0, 120, "看新闻了吗\nAI把数学圈", [0, 60], ["新闻", "数学圈"]), (130, 60, "搞炸了", [0, 0], []),
                       (200, 90, "下一句\n收尾", [0, 60], [])]),
    ((0, 0, 0, 0, 0), [(0, 120, "看新闻了吗\nAI把数学圈", [0, 60], ["新闻", "数学圈"]), (130, 130, "搞炸了\n下一句", [0, 70], []),
                       (260, 90, "收尾", [0, 0], [])]),
])
def test_multi_line_render_groups_consecutive_cues_of_one_sentence(sentences, expected) -> None:
    """每组一个实例，文字按行连接、关键词按序去重，行起点从组的起点算起；排版预检从最后一行出现的帧开始测量。

    没给句子序号时最后只剩 30 帧的一行，不足 1.5 秒，按 90 帧预览定格。
    """
    frames = [(0, 60), (60, 120), (130, 190), (200, 260), (260, 290)]
    texts = ["看新闻了吗", "AI把数学圈", "搞炸了", "下一句", "收尾"]
    keywords = [("新闻",), ("数学圈", "新闻"), (), (), ()]
    cues = [sentence_cue(text, start, end, sentence, *words) for text, (start, end), sentence, words in zip(texts, frames, sentences, keywords)]
    overlay = plan_sprite_render(render_input(text="", keywords=[], effect_total_frames=300, cues=cues), M)
    assert [(item["start_frame"], item["duration_frames"], item["config"]["text"], item["config"]["lineStartFrames"], item["config"]["keywords"])
            for item in overlay.instances] == expected
    assert [item["reveal_frame"] for item in overlay.instances] == [entry[3][-1] for entry in expected]
    assert ["hold" in item for item in overlay.instances] == [False, False, sentences[0] == 0]


# 场景：不足 1.5 秒的一组同样定格，但所有行从第 0 帧显示，定格的中间帧不会少显示一行；整段 text 也从第 0 帧显示全部行。
def test_multi_line_short_groups_and_whole_text_show_every_line() -> None:
    """20 帧的两行组按 90 帧预览定格，行起点都是 0；两行的 text 行起点也是 0；单行字效不注入行起点。"""
    cues = [sentence_cue("看新闻", 0, 10, 1), sentence_cue("了吗", 10, 20, 1)]
    [held] = plan_sprite_render(render_input(text="", keywords=[], effect_total_frames=30, cues=cues), M).instances
    assert (held["duration_frames"], held["hold"], held["config"]["text"], held["config"]["lineStartFrames"]) == (
        90, {"frame": 45, "frames": 20, "length": 90}, "看新闻\n了吗", [0, 0])
    [whole] = plan_sprite_render(render_input(text="第一行\n第二行", effect_total_frames=300), M).instances
    assert whole["config"]["lineStartFrames"] == [0, 0] and whole["config"]["text"] == "第一行\n第二行"
    [single] = plan_sprite_render(render_input(effect_total_frames=300), T).instances
    assert "lineStartFrames" not in single["config"] and "reveal_frame" not in single


# 场景：多行字效的 text 不能超过固定行数，切片文字不能自带换行。
@pytest.mark.parametrize("changes", [
    {"text": "一\n二\n三"},
    {"text": "", "keywords": [], "cues": [sentence_cue("一\n二", 0, 60, 1)]},
], ids=["too-many-lines", "cue-with-line-break"])
def test_multi_line_render_refuses_extra_lines(changes) -> None:
    """都是 content_invalid。"""
    with pytest.raises(OverlayError) as caught:
        plan_sprite_render(render_input(**changes), M)
    assert caught.value.code == "content_invalid", caught.value.message


# 场景：同一句的两个切片单独都合规，拼起来超过资产文字上限（40 字）时分成两组，而不是整个请求被拒。
def test_multi_line_render_splits_groups_before_the_text_limit() -> None:
    """25 字加 25 字超过 40 字：两个切片各自一组。"""
    cues = [sentence_cue("甲" * 25, 0, 60, 1), sentence_cue("乙" * 25, 60, 120, 1)]
    overlay = plan_sprite_render(render_input(text="", keywords=[], effect_total_frames=300, cues=cues), M)
    assert [(item["start_frame"], item["config"]["text"]) for item in overlay.instances] == [(0, "甲" * 25), (60, "乙" * 25)]


def test_fit_frames_of_a_multi_line_text_start_at_its_last_line() -> None:
    """排版预检只在最后一行出现之后取 5 帧；没有 reveal_frame 时与原来一样从第 0 帧取。"""
    assert overlay_render._fit_frames({"start_frame": 0, "duration_frames": 60, "reveal_frame": 20}, 300) == ([20, 29, 39, 49, 59], 60)
    assert overlay_render._fit_frames({"start_frame": 0, "duration_frames": 60}, 300) == ([0, 14, 29, 44, 59], 60)


def run_sprite_task(overlays: OverlayRenders, message: pb.SpriteRenderInput) -> str:
    """受理一次单 Sprite 渲染并等待渲染、上传和通知结束；返回任务 ID。"""
    async def scenario():
        """受理后等待后台协程。"""
        task_id = await overlays.accept_sprite(message)
        await finish(overlays)
        return task_id

    return asyncio.run(scenario())


# 场景：单 Sprite 渲染与叠加渲染共用任务表与渲染名额，转存到独立的对象路径并发送同样的四字段回调；两种任务互不可查。
def test_sprite_render_task_renders_uploads_and_notifies(service, callbacks) -> None:
    """worker 收到整段效果；查询返回 ZOS 地址；叠加渲染的查询接口查不到它。"""
    overlays, renderer, uploads = service
    task_id = run_sprite_task(overlays, render_input(callback_url="https://bus.example/cb"))
    key = f"imv/sprite_render/{task_id}.webm"
    assert overlays.get_sprite(task_id) == pb.SpriteRenderTask(task_id=task_id, status="succeeded", video_url=f"https://zos.example/{key}")
    assert uploads == [(key, "video/webm", b"webm")]
    assert renderer.requests[0]["composition"] == {"width": 1080, "height": 1920, "fps": 30, "duration_in_frames": 300, "frames": 300}
    assert [(item["start_frame"], item["duration_frames"]) for item in renderer.requests[0]["instances"]] == [(0, 300)]
    assert callbacks.posted == [("https://bus.example/cb", {
        "taskId": task_id, "status": "succeed", "videoUrl": f"https://zos.example/{key}", "errorMessage": None,
    })]
    with pytest.raises(HTTPException) as missing:
        overlays.get(task_id)
    assert missing.value.status_code == 404
    overlay_task = run_task(overlays)
    with pytest.raises(HTTPException):
        overlays.get_sprite(overlay_task)


# 场景：长效果在一个任务里一次 worker 运行输出各块再拼接成一个视频：同一份合成、按块的帧区间，上传的是拼接后的文件。
@pytest.mark.parametrize("chunked", [False, True])
def test_overlay_fixed_chunking_can_be_disabled(service, monkeypatch, chunked) -> None:
    """31 秒整段视频按 15 秒分三块；可关闭分块，公开存储键与实例时间不变。"""
    overlays, renderer, uploads = service
    overlays.settings.overlay_chunked = chunked
    joined = []

    async def join(*command, cwd, **_):
        """只核对拼接顺序，不调用真实编码器。"""
        listing = Path(cwd) / command[command.index("-i") + 1]
        joined.append([line.removeprefix("file '").removesuffix("'") for line in listing.read_text().splitlines()])
        (Path(cwd) / command[-1]).write_bytes(b"joined")
        return SimpleNamespace(returncode=0, communicate=lambda: asyncio.sleep(0, (b"", b"")))

    monkeypatch.setattr(overlay_render.asyncio, "create_subprocess_exec", join)
    task_id = run_task(overlays, duration=31.0)
    [request] = renderer.requests
    assert request["composition"]["frames"] == 930
    assert [item["start_frame"] for item in request["instances"]] == [0, 30]
    if chunked:
        assert request["ranges"] == [[0, 449], [450, 899], [900, 929]]
        assert joined == [[f"chunk-{index}.webm" for index in range(3)]]
    else:
        assert "ranges" not in request and joined == []
    assert uploads == [(f"imv/overlay_render/{task_id}.webm", "video/webm", b"joined" if chunked else b"webm")]


def test_sprite_render_task_joins_its_chunks(service, monkeypatch, caplog) -> None:
    """70 秒的字幕只启动一次 worker，按五个帧区间输出，跨块的切片保持绝对起点；拼接列表按块顺序，转存的只有一个文件。"""
    overlays, renderer, uploads = service
    joined: list[list[str]] = []

    async def join(*command, cwd, **_):
        """替身 ffmpeg：记录拼接列表并写出成片。"""
        listing = Path(cwd) / command[command.index("-i") + 1]
        joined.append([line.removeprefix("file '").removesuffix("'") for line in listing.read_text().splitlines()])
        (Path(cwd) / command[-1]).write_bytes(b"joined")
        return SimpleNamespace(returncode=0, communicate=lambda: asyncio.sleep(0, (b"", b"")))

    monkeypatch.setattr(overlay_render.asyncio, "create_subprocess_exec", join)
    message = render_input(text="", keywords=[], effect_total_frames=2100, cues=[cue("开头", 0, 30), cue("跨块", 890, 920)])
    with caplog.at_level(logging.INFO, logger=overlay_service.logger.name):
        task_id = run_sprite_task(overlays, message)
    [request] = renderer.requests
    assert request["ranges"] == [[0, 449], [450, 899], [900, 1349], [1350, 1799], [1800, 2099]]
    assert request["composition"]["frames"] == 2100
    assert [(item["start_frame"], item["duration_frames"]) for item in request["instances"]] == [(0, 90), (890, 90)]
    assert joined == [[f"chunk-{index}.webm" for index in range(5)]]
    assert uploads == [(f"imv/sprite_render/{task_id}.webm", "video/webm", b"joined")]
    [entry] = [record for record in caplog.records if record.msg.startswith("Remotion performance task_id=")]
    assert json.loads(entry.args[-1])["join_ms"] >= 0


@pytest.mark.anyio
@pytest.mark.parametrize("interruption", ["cancel", "timeout"])
@pytest.mark.parametrize("exited_before_kill", [False, True])
async def test_join_interruption_reaps_ffmpeg(tmp_path, monkeypatch, interruption, exited_before_kill) -> None:
    """取消和超时均回收进程；kill 竞态不覆盖原异常，带引号目录也只写安全相对文件名。"""
    directory = tmp_path / "render with ' quote"
    directory.mkdir()
    for index in range(2):
        (directory / f"chunk-{index}.webm").write_bytes(b"chunk")
    started, calls = asyncio.Event(), []

    class Process:
        """模拟尚未完成的拼接以及进程自行退出与 kill 的竞态。"""
        returncode = None

        async def communicate(self):
            """持续等待，由调用方取消或整体期限打断。"""
            started.set()
            await asyncio.Future()

        def kill(self):
            """记录终止，即使进程恰好已经退出也必须继续 wait。"""
            calls.append("kill")
            if exited_before_kill:
                raise ProcessLookupError

        async def wait(self):
            """回收结束状态，供测试检查没有遗留后台进程。"""
            calls.append("wait")
            self.returncode = -9
            return self.returncode

    process = Process()

    async def spawn(*command, cwd, **_):
        """校验传给 FFmpeg 的目录与清单，不执行真实编码。"""
        assert cwd == directory
        assert command[command.index("-i") + 1] == "chunks.txt" and command[-1] == "sprite.webm"
        assert (directory / "chunks.txt").read_text() == "file 'chunk-0.webm'\nfile 'chunk-1.webm'\n"
        return process

    monkeypatch.setattr(overlay_render.asyncio, "create_subprocess_exec", spawn)
    if interruption == "timeout":
        monkeypatch.setattr(overlay_render, "JOIN_SECONDS", 0.01)
    task = asyncio.create_task(overlay_render.join_chunks(directory, 2))
    await asyncio.wait_for(started.wait(), 1)
    if interruption == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(OverlayError, match="字效视频拼接超时") as caught:
            await task
        assert caught.value.code == "render_failed"
    assert calls == ["kill", "wait"] and process.returncode is not None


# 场景：重启后恢复的单 Sprite 渲染按保存的输入渲染；渲染失败以稳定错误码与固定说明公开。
def test_sprite_render_recovers_and_reports_failures(service, callbacks) -> None:
    """未完成的任务行按保存的输入重新渲染；渲染器失败时任务失败、错误不含渲染器输出。"""
    overlays, renderer, _ = service
    stored = {"sprite_render": {"sprite_id": "t", "text": "恢复", "effect_total_frames": "60"}, "callback_url": None}
    overlays._insert("33333333-3333-3333-3333-333333333333", {"request": stored})
    run_resume(overlays)
    assert overlays.get_sprite("33333333-3333-3333-3333-333333333333").status == "succeeded"
    assert renderer.requests[0]["instances"][0]["config"]["text"] == "恢复"
    renderer.failure = RuntimeError("worker log /private/path")
    failed = overlays.get_sprite(run_sprite_task(overlays, render_input()))
    assert (failed.status, failed.error_code, failed.error_message) == ("failed", "render_failed", "叠加视频渲染失败")


@pytest.fixture
def sprite_client(service, client: TestClient):
    """让 /api/sprites 路由使用测试里的任务服务；返回测试客户端与服务，结束时还原依赖覆盖。"""
    overlays, _, _ = service

    async def provide():
        """依赖替身：只暴露渲染任务服务。"""
        return SimpleNamespace(overlays=overlays)

    app.dependency_overrides[sprite_runtime] = provide
    yield client, overlays
    app.dependency_overrides.pop(sprite_runtime, None)


def post_render(client: TestClient, message: pb.SpriteRenderInput):
    """以 Protobuf 二进制发送一次渲染请求。"""
    return client.post("/api/sprites/render", content=message.SerializeToString(), headers={"Content-Type": "application/x-protobuf"})


# 场景：HTTP 契约——Protobuf 受理返回任务 ID，查询返回任务状态；不合契约、回调地址无效或不存在的 Sprite 为 422 且不建任务；缺少拼接工具为 503；未知任务 404。
def test_sprite_render_routes_accept_and_query(sprite_client, monkeypatch) -> None:
    """受理与查询都是 imv.sprite.v1 消息；422 说明原因，不创建任务行。"""
    client, overlays = sprite_client
    created = post_render(client, render_input())
    assert created.status_code == 200 and created.headers["content-type"] == "application/x-protobuf"
    task_id = pb.SpriteRenderAccepted.FromString(created.content).task_id
    queried = client.get(f"/api/sprites/render/{task_id}")
    assert pb.SpriteRenderTask.FromString(queried.content).task_id == task_id
    rows = task_rows(overlays)
    for message, detail in (
        (render_input(effect_total_frames=3601), "效果时长不能超过 120 秒"),
        (render_input(callback_url="ftp://bus.example/cb"), "callback_url"),
        (render_input(sprite_id="missing"), "Sprite missing 不存在"),
    ):
        response = post_render(client, message)
        assert response.status_code == 422 and detail in response.json()["detail"]
    monkeypatch.setattr(overlay_service, "join_problem", lambda overlay, settings: "ffmpeg")
    assert post_render(client, render_input()).status_code == 503
    assert task_rows(overlays) == rows
    assert client.get(f"/api/sprites/render/{uuid4()}").status_code == 404
    assert client.post("/api/sprites/render", json={"spriteId": "t"}).status_code == 415


# 一个整段淡入、末 10 帧淡出的组件：淡出终点取自 durationInFrames。
EDGE_FADE = """import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate} from 'remotion';
export default function Sprite() {
  const frame = useCurrentFrame();
  const {durationInFrames: d} = useVideoConfig();
  const opacity = interpolate(frame, [0, 10, d - 10, d], [0, 1, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return <AbsoluteFill style={{background: '#ff0000', opacity}} />;
}
"""


# 场景：真实沙箱里把 4 秒效果按 2 秒切块渲染再拼接：第二块开头仍是全亮（不重新淡入），末尾按完整时长淡出，拼接后保留透明通道。
@pytest.mark.skipif(os.environ.get("IMV_TEST_RENDERER") != "1", reason="set IMV_TEST_RENDERER=1 for Linux overlay integration")
@pytest.mark.parametrize("chunk_concurrency", [1, 2])
def test_real_sprite_render_joins_chunks_into_one_continuous_video(tmp_path: Path, monkeypatch, chunk_concurrency) -> None:
    """两块串行或并行都保持 120 帧时钟：第 0/60/115/119 帧的 alpha 为 0/1/0.5/0.1。"""
    monkeypatch.setattr(overlay_render, "CHUNK_SECONDS", 2)
    configured = {
        name: Path(value)
        for name, value in (
            ("browser_executable", RENDERER_PATHS["IMV_BROWSER_EXECUTABLE"]),
            ("font_regular", RENDERER_PATHS["IMV_FONT_REGULAR"]),
            ("font_bold", RENDERER_PATHS["IMV_FONT_BOLD"]),
        )
        if value
    }
    settings = Settings(_env_file=None, data_dir=tmp_path / "state", overlay_chunk_concurrency=chunk_concurrency, **configured)
    edge = sprite("edge", width=180, height=320, frames=30, code=EDGE_FADE)
    edge.kind = pb.SPRITE_KIND_FILTER_OVERLAY
    overlay = plan_sprite_render(pb.SpriteRenderInput(sprite_id="edge", effect_total_frames=120), edge)
    video = asyncio.run(sprite_render.render_sprite(Renderer(settings), overlay, tmp_path / "job"))
    assert video.name == "sprite.webm"
    for frame, expected in ((0, 0.0), (60, 1.0), (115, 0.5), (119, 0.1)):
        assert decoded_alpha(video, frame) == pytest.approx(expected, abs=0.05)


def still_title(code: str) -> pb.PublishedSprite:
    """一个 30 帧预览的字效：只声明可编辑的 text，内容区域为整块 180×320 画布。"""
    return pb.PublishedSprite(
        sprite_id="still", name="still", kind=pb.SPRITE_KIND_TEXT, tsx_code=code, text_prop="text",
        canvas=pb.SpriteCanvas(width=180, height=320, fps=30, preview_frames=30),
        content_box=pb.SpriteBox(x=0, y=0, width=180, height=320),
        config_schema_json=json.dumps({"type": "object", "properties": {"text": {"type": "string", "minLength": 1}}}),
        default_config_json=json.dumps({"text": "字"}),
        parameters=[pb.SpriteParameter(key="text", access=pb.OPERATOR_ACCESS_VISIBLE_EDITABLE, default_value=pb.ScalarValue(string_value="字"))],
    )


# 场景：真实沙箱里，0.4 秒的短字幕定格在预览中间帧、整段全亮且结束后透明，不淡入淡出；2 秒的字幕照常按自身长度淡入；
# 比 1 秒预览更长的 1.33 秒短字幕也显示满自己的帧数，不会在预览长度处消失。
@pytest.mark.skipif(os.environ.get("IMV_TEST_RENDERER") != "1", reason="set IMV_TEST_RENDERER=1 for Linux overlay integration")
def test_real_short_subtitles_hold_still(tmp_path: Path) -> None:
    """切片 0～12 帧：第 0/11 帧 alpha 1、第 12 帧 0；切片 30～90 帧：第 30 帧 alpha 0（淡入开始）、第 60 帧 1；
    切片 100～140 帧：第 135 帧 alpha 1、第 140 帧 0。"""
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
    message = pb.SpriteRenderInput(sprite_id="still", effect_total_frames=150, cues=[cue("短句", 0, 12), cue("长句", 30, 90), cue("长于预览", 100, 140)])
    video = asyncio.run(sprite_render.render_sprite(Renderer(settings), plan_sprite_render(message, still_title(EDGE_FADE)), tmp_path / "job"))
    for frame, expected in ((0, 1.0), (11, 1.0), (12, 0.0), (30, 0.0), (60, 1.0), (135, 1.0), (140, 0.0)):
        assert decoded_alpha(video, frame) == pytest.approx(expected, abs=0.05), frame


# 两行字效：第 i 行到 lineStartFrames[i] 才出现，第一行涂满画布上半部、第二行涂满下半部。
LINE_BLOCKS = """import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
export default function Sprite(props: {text: string; lineStartFrames: number[]}) {
  const frame = useCurrentFrame();
  return <AbsoluteFill>{props.text.split('\\n').map((line, index) => frame >= (props.lineStartFrames[index] ?? Infinity)
    ? <div key={index} style={{position: 'absolute', left: 0, right: 0, top: index * 160, height: 160, background: '#ff0000'}} /> : null)}</AbsoluteFill>;
}
"""


# 场景：真实沙箱里，两行字效把同一句的两个切片放进一个画面：第一行先出现，第二个切片开始时第二行出现，
# 整组在第二个切片结束时一起清空；下一句的切片从第一行重新开始。
@pytest.mark.skipif(os.environ.get("IMV_TEST_RENDERER") != "1", reason="set IMV_TEST_RENDERER=1 for Linux overlay integration")
def test_real_multi_line_text_reveals_lines_and_clears_together(tmp_path: Path) -> None:
    """切片 0～60、60～120 属于第 1 句，130～190 属于第 2 句：上半部（第一行）与下半部（第二行）在第 10/70/125/140 帧的 alpha。"""
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
    lines = still_title(LINE_BLOCKS)
    lines.config_schema_json = json.dumps({"type": "object", "properties": {
        "text": {"type": "string", "minLength": 1},
        "lineStartFrames": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 3600}, "minItems": 2, "maxItems": 2},
    }})
    lines.default_config_json = json.dumps({"text": "一\n二", "lineStartFrames": [0, 15]})
    message = pb.SpriteRenderInput(sprite_id="still", effect_total_frames=200, cues=[
        sentence_cue("一", 0, 60, 1), sentence_cue("二", 60, 120, 1), sentence_cue("三", 130, 190, 2)])
    video = asyncio.run(sprite_render.render_sprite(Renderer(settings), plan_sprite_render(message, lines), tmp_path / "job"))
    bottom = 200 * 180
    for frame, top_alpha, bottom_alpha in ((10, 1.0, 0.0), (70, 1.0, 1.0), (125, 0.0, 0.0), (140, 1.0, 0.0)):
        assert decoded_alpha(video, frame) == pytest.approx(top_alpha, abs=0.05), frame
        assert decoded_alpha(video, frame, bottom) == pytest.approx(bottom_alpha, abs=0.05), frame


class FitRenderer:
    """排版预检 worker 的替身：只有不超过 `capacity` 的字号才放得下，并记录每次请求。"""

    def __init__(self, capacity: int | None, shift: dict | None = None, shift_text: str | None = None) -> None:
        """capacity 为能完整显示文字的最大字号；None 表示连最小字号也放不下；shift 是答案里附带的平移，给出 shift_text 时只附在该文字的条目上。"""
        self.capacity = capacity
        self.shift = shift
        self.shift_text = shift_text
        self.settings = SimpleNamespace(render_timeout_seconds=180)
        self.requests: list[dict] = []
        self.limits: list[tuple] = []

    def worker_browser_path(self) -> str:
        """返回固定浏览器路径。"""
        return "/browser"

    async def run_worker(self, directory: Path, *, worker=None, timeout_seconds=None, cpu_seconds=None) -> dict:
        """按 capacity 为每个条目给出与真实 worker 相同形状的答案，并记录墙钟期限与 CPU 上限。"""
        assert worker == "tool-validation-worker.mjs"
        request = json.loads((directory / "request.json").read_text())
        self.requests.append(request)
        self.limits.append((timeout_seconds, cpu_seconds))
        fits = []
        for sprite in request["sprites"]:
            answers = []
            for item in sprite["fit"]:
                wanted = item["values"][item["size_key"]]
                if self.capacity is None:
                    answers.append({"index": item["index"], "fits": False, "size": item["min"]})
                else:
                    moved = self.shift and self.shift_text in (None, item["text"])
                    answers.append({"index": item["index"], "fits": True, "size": min(wanted, self.capacity), **({"shift": self.shift} if moved else {})})
            fits.append(answers)
        return {"fits": fits}


def title_overlay(text: str, font_size: int = 128):
    """一个 text/fontSize 可编辑的字效摆放，并按总线的方式覆盖文字与字号后规划出渲染计划。"""
    editor = {"remotion": {"parameters": [{"key": "text", "stringValue": text}, {"key": "fontSize", "numberValue": font_size}]}}
    placed = OverlayPlacement.model_validate({"spriteId": "c", "start": 0, "editor": editor})
    return plan_overlay([placed], PUBLICATIONS, 3.0, 120)

# 场景：最小字号下文字仍出画布时 worker 答案带平移：渲染计划里这个摆放的宿主变换整体移动，已设置的坐标加上平移、
# 未设置的从内容区域中心算起；字号照常缩小，保存的摆放设置不变；答案里的平移不是一对有限数字时按预检不可用（render_failed）处理。
def test_fit_applies_the_workers_shift_to_this_renders_host_only(tmp_path) -> None:
    """x 已设为 300、y 未设（内容区域中心 960）：平移 (0, 40) 后变换为 x=300、y=1000；字号缩到 48。"""
    editor = {"remotion": {"parameters": [{"key": "text", "stringValue": "测" * 20}, {"key": "fontSize", "numberValue": 128}], "transform": {"x": 300}}}
    placement = OverlayPlacement.model_validate({"spriteId": "c", "start": 0, "editor": editor})
    overlay = plan_overlay([placement], PUBLICATIONS, 3.0, 120)
    box = overlay.instances[0]["host"]["box"]
    asyncio.run(overlay_render.fit_texts(FitRenderer(capacity=48, shift={"x": 0, "y": 40}), overlay, PUBLICATIONS, tmp_path / "fit"))
    assert overlay.instances[0]["host"]["transform"] == {"x": 300, "y": box["y"] + box["height"] / 2 + 40}
    assert overlay.instances[0]["config"]["fontSize"] == 48
    assert placement.editor.remotion.transform.model_dump(exclude_none=True) == {"x": 300}
    # A video composition gives all cues of a placement one shared host: only the cue that needs the shift moves.
    shared = {"box": box, "transform": {"y": 500}}
    cues = [{**overlay.instances[0], "text": text, "host": shared, "config": {"text": text, "fontSize": 128}} for text in ("短句", "测" * 20, "测" * 20)]
    overlay = replace(overlay, instances=cues)
    renderer = FitRenderer(capacity=48, shift={"x": 0, "y": 40}, shift_text="测" * 20)
    asyncio.run(overlay_render.fit_texts(renderer, overlay, PUBLICATIONS, tmp_path / "shared"))
    assert [item["host"]["transform"] for item in overlay.instances] == [{"y": 500}, {"y": 540}, {"y": 540}]
    assert shared == {"box": box, "transform": {"y": 500}}
    with pytest.raises(OverlayError) as failure:
        asyncio.run(overlay_render.fit_texts(FitRenderer(capacity=48, shift={"x": "1", "y": 0}), title_overlay("标题"), PUBLICATIONS, tmp_path / "bad"))
    assert failure.value.code == "render_failed"



# 场景（issue #119）：总线传来的长标题在默认字号下放不下，宿主按这次真正的文字把字号缩到放得下的最大值，文字本身不变。
def test_text_that_does_not_fit_gets_a_smaller_font_size(tmp_path) -> None:
    """放得下的最大字号是 72：128 的请求缩成 72，其余配置不动；预检请求带着真实文字、字号参数名与发布的最小字号。"""
    renderer = FitRenderer(capacity=72)
    overlay = title_overlay("测" * 40)
    asyncio.run(overlay_render.fit_texts(renderer, overlay, PUBLICATIONS, tmp_path / "fit"))
    assert overlay.instances[0]["config"]["fontSize"] == 72
    assert overlay.instances[0]["config"]["text"] == "测" * 40
    [request] = renderer.requests
    [sprite] = request["sprites"]
    [item] = sprite["fit"]
    assert (item["text"], item["size_key"], item["min"]) == ("测" * 40, "fontSize", 24)
    assert request["composition"]["duration_frames"] == 90


# 场景：文字本来就放得下时保持用户设置的字号。
def test_text_that_fits_keeps_its_requested_size(tmp_path) -> None:
    """容量足够时字号不变。"""
    overlay = title_overlay("标题", 96)
    asyncio.run(overlay_render.fit_texts(FitRenderer(capacity=240), overlay, PUBLICATIONS, tmp_path / "fit"))
    assert overlay.instances[0]["config"]["fontSize"] == 96


# 场景：缩到最小字号也放不下时不渲染被裁切的标题，任务以稳定错误码失败，总线据此提示。
def test_text_that_cannot_fit_at_the_smallest_size_fails_with_a_stable_code(tmp_path) -> None:
    """text_too_long 与固定说明，不含本地路径。"""
    overlay = title_overlay("测" * 40)
    with pytest.raises(OverlayError) as failure:
        asyncio.run(overlay_render.fit_texts(FitRenderer(capacity=None), overlay, PUBLICATIONS, tmp_path / "fit"))
    assert failure.value.code == "text_too_long" and "缩短文字" in failure.value.message


# 场景：不是字效、或没有文字与字号参数的 Sprite 不参与预检，也不启动浏览器。
def test_sprites_without_text_are_not_measured(tmp_path) -> None:
    """a 没有任何参数，预检不向 worker 发请求。"""
    renderer = FitRenderer(capacity=1)
    overlay = plan_overlay([placement("a")], PUBLICATIONS, 3.0, 120)
    asyncio.run(overlay_render.fit_texts(renderer, overlay, PUBLICATIONS, tmp_path / "fit"))
    assert renderer.requests == []


# 场景：预检环境不可用时任务以渲染失败收尾，而不是带着被裁切的文字继续。
def test_unavailable_fit_check_fails_the_render(tmp_path) -> None:
    """worker 抛出沙箱错误时是 render_failed，原因只保留在 __cause__。"""
    class Broken(FitRenderer):
        """Fail the sandbox run."""

        async def run_worker(self, directory: Path, **options) -> dict:
            """Simulate a sandbox that cannot start."""
            raise RuntimeError("bwrap: no permission")

    with pytest.raises(OverlayError) as failure:
        asyncio.run(overlay_render.fit_texts(Broken(1), title_overlay("标题"), PUBLICATIONS, tmp_path / "fit"))
    assert failure.value.code == "render_failed" and "bwrap" not in failure.value.message and failure.value.__cause__ is not None


# 场景：任务流转中，预检判定放不下时任务以 text_too_long 失败，公开错误只有稳定错误码与固定说明，不渲染也不上传。
def test_task_fails_with_text_too_long_when_the_title_cannot_fit(service, monkeypatch) -> None:
    """失败任务没有视频与上传，渲染器没有被调用。"""
    overlays, renderer, uploads = service

    async def refuse(*args) -> None:
        """Report the text as impossible to fit."""
        raise OverlayError("text_too_long", "文字过长：缩到最小字号仍无法完整显示，请缩短文字")

    monkeypatch.setattr(overlay_service, "fit_texts", refuse)
    task_id = run_task(overlays)
    task = overlays.get(task_id)
    assert task["status"] == "failed" and task["error"]["code"] == "text_too_long"
    assert renderer.requests == [] and uploads == []


# issue #119 的组件写法：972x360 的固定框 + overflow hidden，换行或不换行。
FIXED_BOX = """import React from "react";
import { AbsoluteFill } from "remotion";
export default function Title(props: { text: string; fontSize: number }) {
  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div style={{ width: 972, height: 360, overflow: "hidden", textAlign: "center", fontSize: props.fontSize, lineHeight: 1.2, wordBreak: "break-all", whiteSpace: WRAP, color: "#fff" }}>{props.text}</div>
    </AbsoluteFill>
  );
}
"""


def real_fit(tmp_path: Path, whitespace: str, text: str):
    """用真实沙箱和浏览器对一个固定框字效做排版预检，返回预检后的渲染计划。"""
    return fit_for_real(tmp_path, FIXED_BOX.replace("WRAP", json.dumps(whitespace)), [titled(text)], 1.0)


def titled(text: str, *, sprite_id: str = "t", duration: float | None = None, font_size: int | None = None, **transform) -> OverlayPlacement:
    """字效 sprite_id（默认 t）从第 0 秒开始的一个摆放：覆盖文字，可选固定时长、字号与宿主变换。"""
    parameters = [{"key": "text", "stringValue": text}, *([{"key": "fontSize", "numberValue": font_size}] if font_size else [])]
    remotion = {"parameters": parameters, **({"transform": transform} if transform else {})}
    return OverlayPlacement.model_validate({"spriteId": sprite_id, "start": 0, **({"duration": duration} if duration else {}), "editor": {"remotion": remotion}})


def fit_for_real(tmp_path: Path, codes: str | dict[str, str], placements: list[OverlayPlacement], seconds: float, *, minimum: int = 24):
    """用真实沙箱和浏览器对这些摆放做排版预检，返回预检后的渲染计划。

    codes 是各字效的源码（Sprite ID 到源码；只给一份源码时就是字效 t），内容区域都是整块画布。
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
    schema = {"type": "object", "properties": {
        "text": {"type": "string", "minLength": 1, "maxLength": 100},
        "fontSize": {"type": "number", "minimum": minimum, "maximum": 240},
        "keywordSize": {"type": "integer", "minimum": 0, "maximum": 300},
    }, "required": ["text", "fontSize"], "additionalProperties": False}
    published = {sprite_id: pb.PublishedSprite(
        sprite_id=sprite_id, name=sprite_id, kind=pb.SPRITE_KIND_TEXT, tsx_code=code, text_prop="text",
        canvas=pb.SpriteCanvas(width=1080, height=1920, fps=30, preview_frames=30),
        config_schema_json=json.dumps(schema), default_config_json=json.dumps({"text": "标题", "fontSize": 128, "keywordSize": 0}, ensure_ascii=False),
        content_box=pb.SpriteBox(x=0, y=0, width=1080, height=1920),
        parameters=[
            pb.SpriteParameter(key="text", label="文字", access=pb.OPERATOR_ACCESS_VISIBLE_EDITABLE, default_value=pb.ScalarValue(string_value="标题"), min_length=1, max_length=100),
            pb.SpriteParameter(key="fontSize", label="字号", access=pb.OPERATOR_ACCESS_VISIBLE_EDITABLE, default_value=pb.ScalarValue(number_value=128), minimum=minimum, maximum=240),
            pb.SpriteParameter(key="keywordSize", label="关键词字号", access=pb.OPERATOR_ACCESS_VISIBLE_EDITABLE, default_value=pb.ScalarValue(number_value=0), minimum=0, maximum=300, format=pb.SPRITE_PARAMETER_FORMAT_INTEGER),
        ],
    ) for sprite_id, code in ({"t": codes} if isinstance(codes, str) else codes).items()}
    overlay = plan_overlay(placements, published, seconds, 120)
    asyncio.run(overlay_render.fit_texts(Renderer(settings), overlay, published, tmp_path / "fit"))
    return overlay


# 场景（issue #119）：真实浏览器里，100 字标题放进 972x360 的固定框时，宿主把 128 的字号缩到放得下的最大值；短标题不动；
# 一个不换行的组件缩到最小字号也放不下，任务以 text_too_long 失败。
@pytest.mark.skipif(os.environ.get("IMV_TEST_RENDERER") != "1", reason="set IMV_TEST_RENDERER=1 for Linux overlay integration")
def test_real_browser_fits_a_long_title_into_the_fixed_box(tmp_path: Path) -> None:
    """换行的固定框：长标题字号落在 24～52（每行 18～20 字、5 行以内），短标题保持 128；不换行的组件失败。"""
    long_title = real_fit(tmp_path / "long", "normal", "测" * 100)
    assert 24 <= long_title.instances[0]["config"]["fontSize"] <= 52
    short_title = real_fit(tmp_path / "short", "normal", "标题")
    assert short_title.instances[0]["config"]["fontSize"] == 128
    with pytest.raises(OverlayError) as failure:
        real_fit(tmp_path / "nowrap", "nowrap", "测" * 100)
    assert failure.value.code == "text_too_long"


# 场景：单 Sprite 渲染的字幕：相同内容只测一次并共用结果；定格的切片只在定格帧上量，按自身长度播放的切片量首、尾和中间帧；
# 一个 Sprite 只启动一次浏览器运行，缩小结果写回所有相同内容的实例。
def test_sprite_render_fits_each_distinct_cue_in_one_browser_run(tmp_path) -> None:
    """四个切片里两个文字相同、一个定格：worker 只收到 3 个条目；缩小结果写回所有相同内容的实例。"""
    message = render_input(text="", keywords=[], effect_total_frames=300, cues=[
        cue("短句", 0, 20), cue("短句", 30, 50), cue("测" * 30, 60, 160), cue("够短", 170, 290)])
    overlay = plan_sprite_render(message, T)
    renderer = FitRenderer(capacity=40)
    asyncio.run(overlay_render.fit_texts(renderer, overlay, {"t": T}, tmp_path / "fit"))
    [request] = renderer.requests
    [sprite] = request["sprites"]
    # Each item is mounted at the length its Sprite reads: the previewed 90 frames when held, otherwise its own.
    assert [(item["text"], item["frames"], item["length"]) for item in sprite["fit"]] == [
        ("短句", [45], 90), ("测" * 30, [0, 24, 49, 74, 99], 100), ("够短", [0, 29, 59, 89, 119], 120)]
    assert request["composition"]["duration_frames"] == 120
    assert [item["config"]["fontSize"] for item in overlay.instances] == [40, 40, 40, 40]


def clip(text: str, duration: float | None = None, **transform) -> OverlayPlacement:
    """字效 c 从第 0 秒开始的一个摆放：覆盖文字，可选固定时长与宿主变换。"""
    remotion = {"parameters": [{"key": "text", "stringValue": text}], **({"transform": transform} if transform else {})}
    return OverlayPlacement.model_validate({"spriteId": "c", "start": 0, **({"duration": duration} if duration else {}), "editor": {"remotion": remotion}})


# 场景：同一 Sprite 的两个摆放长度不同（1 秒与 3 秒）时，每份内容按自己播放的长度挂载（Sprite 读到的 durationInFrames），
# 不能都按最长的 3 秒测量。
def test_fit_mounts_each_content_at_the_length_it_plays(tmp_path) -> None:
    """worker 收到的每个条目带自己的长度：30 帧与 90 帧。"""
    renderer = FitRenderer(capacity=240)
    overlay = plan_overlay([clip("短标题", 1.0), clip("长一点的标题", 3.0)], PUBLICATIONS, 3.0, 120)
    asyncio.run(overlay_render.fit_texts(renderer, overlay, PUBLICATIONS, tmp_path / "fit"))
    [request] = renderer.requests
    [sprite] = request["sprites"]
    assert [(item["text"], item["length"]) for item in sprite["fit"]] == [("短标题", 30), ("长一点的标题", 90)]


# 场景：相同文字的摆放只差宿主变换（放大、移到边缘并旋转）时分别测量，每份带自己的变换和 Sprite 的内容区域；
# 只差不透明度的摆放不改变文字的位置与大小，仍与未变换的摆放共用一次测量。
def test_fit_measures_each_placement_where_its_transform_puts_it(tmp_path) -> None:
    """四个摆放得到三个条目：未变换（含只改不透明度的）、放大 2 倍、移到 x=100 并旋转 90°。"""
    renderer = FitRenderer(capacity=240)
    placements = [clip("标题"), clip("标题", scale=2), clip("标题", opacity=0.5), clip("标题", x=100, rotation=90)]
    overlay = plan_overlay(placements, PUBLICATIONS, 3.0, 120)
    asyncio.run(overlay_render.fit_texts(renderer, overlay, PUBLICATIONS, tmp_path / "fit"))
    [request] = renderer.requests
    [sprite] = request["sprites"]
    assert [(item["index"], item["transform"]) for item in sprite["fit"]] == [(0, {}), (1, {"scale": 2}), (3, {"x": 100, "rotation": 90})]
    assert sprite["box"] == {"x": 90, "y": 760, "width": 900, "height": 400}


# 场景：大量不同字幕切片在一次浏览器运行里测量时，墙钟期限按切片数放宽，沙箱 CPU 上限随期限一起放宽，
# 不会在期限之前先耗尽默认的 300 CPU 秒而以 render_failed 结束。
def test_fit_cpu_cap_grows_with_its_deadline(tmp_path) -> None:
    """100 个不同的定格切片：期限 510 秒（每条 5 秒、每个 Sprite 10 秒），CPU 上限与叠加渲染同样按期限 × 核数计算。"""
    cues = [cue(f"第{index}句", index * 36, index * 36 + 30) for index in range(100)]
    overlay = plan_sprite_render(render_input(text="", keywords=[], effect_total_frames=3600, cues=cues), T)
    renderer = FitRenderer(capacity=240)
    asyncio.run(overlay_render.fit_texts(renderer, overlay, {"t": T}, tmp_path / "fit"))
    assert renderer.limits == [(510, overlay_cpu_seconds(510))]
    assert overlay_cpu_seconds(510) >= 510 > overlay_render.CPU_SECONDS


# 字幕框的宽度随 Sprite 读到的长度变化：超过 60 帧时是 300px 宽的 overflow hidden 窄框，否则是 972px 宽的框，都不换行。
LENGTH_DEPENDENT_BOX = """import React from "react";
import { AbsoluteFill, useVideoConfig } from "remotion";
export default function Title(props: { text: string; fontSize: number }) {
  const { durationInFrames } = useVideoConfig();
  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div style={{ width: durationInFrames > 60 ? 300 : 972, overflow: "hidden", whiteSpace: "nowrap", textAlign: "center", fontSize: props.fontSize, color: "#fff" }}>{props.text}</div>
    </AbsoluteFill>
  );
}
"""
# 972px 宽、自动换行的居中标题，字号就是 fontSize 参数，本身不缩小。
WRAPPED_TITLE = """import React from "react";
import { AbsoluteFill } from "remotion";
export default function Title(props: { text: string; fontSize: number }) {
  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div style={{ width: 972, textAlign: "center", fontSize: props.fontSize, lineHeight: 1.2, whiteSpace: "normal", wordBreak: "break-all", color: "#fff" }}>{props.text}</div>
    </AbsoluteFill>
  );
}
"""


# 场景：真实浏览器里，1 秒的摆放读到 30 帧、在 972px 的框里放得下 8 个 100px 的字；同一 Sprite 另有一个 3 秒的摆放，
# 若都按最长的 90 帧挂载，1 秒的那个会被量进 300px 的窄框而被错误缩小。
@pytest.mark.skipif(os.environ.get("IMV_TEST_RENDERER") != "1", reason="set IMV_TEST_RENDERER=1 for Linux overlay integration")
def test_real_browser_fits_each_clip_at_its_own_length(tmp_path: Path) -> None:
    """两个摆放都保持 100 的字号。"""
    overlay = fit_for_real(tmp_path, LENGTH_DEPENDENT_BOX, [titled("测" * 8, duration=1.0, font_size=100), titled("短", duration=3.0, font_size=100)], 3.0)
    assert [item["config"]["fontSize"] for item in overlay.instances] == [100, 100]


# 场景：真实浏览器里，8 个 100px 的字（800px 宽）在原位放得下；放大 2 倍后超出 1080px 宽的画布，放大 2.5 倍并旋转 90° 后
# 超出 1920px 高的画布，中心移到 x=100 后左半边出了画布；宿主按各自变换后的位置把字号缩到放得下的最大值。
@pytest.mark.skipif(os.environ.get("IMV_TEST_RENDERER") != "1", reason="set IMV_TEST_RENDERER=1 for Linux overlay integration")
def test_real_browser_fits_text_where_the_host_transform_puts_it(tmp_path: Path) -> None:
    """原位 100；放大 2 倍不超过 67（8×67×2≤1080）；旋转后按高度不超过 96（8×96×2.5≤1920）；移到 x=100 不超过 26。"""
    placements = [
        titled("测" * 8, font_size=100), titled("测" * 8, font_size=100, scale=2),
        titled("测" * 8, font_size=100, scale=2.5, rotation=90), titled("测" * 8, font_size=100, x=100),
    ]
    sizes = [item["config"]["fontSize"] for item in fit_for_real(tmp_path, WRAPPED_TITLE, placements, 1.0).instances]
    assert sizes[0] == 100
    assert 60 <= sizes[1] <= 67
    assert 85 <= sizes[2] <= 96
    assert 24 <= sizes[3] <= 26


# 一行标题放在恰好一行高（lineHeight 1）的 overflow hidden 遮罩里，常见于揭示动画的字效。
LINE_MASK_TITLE = """import React from "react";
import { AbsoluteFill } from "remotion";
export default function Title(props: { text: string; fontSize: number }) {
  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div style={{ width: 972, height: props.fontSize, overflow: "hidden", textAlign: "center" }}>
        <div style={{ fontSize: props.fontSize, lineHeight: 1, whiteSpace: "nowrap", color: "#fff" }}>{props.text}</div>
      </div>
    </AbsoluteFill>
  );
}
"""


# 场景：真实浏览器里，遮罩只裁掉字体上下伸部的空白、不裁字形，短标题保持原字号；若按字体的上下伸部度量，
# 任何字号都会被判为裁切，连两个字的标题也以 text_too_long 失败。
@pytest.mark.skipif(os.environ.get("IMV_TEST_RENDERER") != "1", reason="set IMV_TEST_RENDERER=1 for Linux overlay integration")
def test_real_browser_keeps_a_short_title_in_a_line_mask(tmp_path: Path) -> None:
    """两个字的标题保持 128 的字号。"""
    overlay = fit_for_real(tmp_path, LINE_MASK_TITLE, [titled("标题")], 1.0)
    assert overlay.instances[0]["config"]["fontSize"] == 128


# 场景：一个模板里有两个不同的字效时，所有文字在一次浏览器运行里、一个期限内测量，不再每个 Sprite 启动一次浏览器；
# 每个 Sprite 带自己的源码、内容区域与条目，结果写回各自的实例。
def test_fit_measures_every_text_sprite_in_one_browser_run(tmp_path) -> None:
    """字效 c 与 t 各一个摆放：worker 只运行一次，收到两个 Sprite，期限仍是默认的 180 秒；两个实例都缩到 48。"""
    renderer = FitRenderer(capacity=48)
    second = OverlayPlacement.model_validate({"spriteId": "t", "start": 0, "editor": {"remotion": {"parameters": [{"key": "text", "stringValue": "第二个标题"}]}}})
    overlay = plan_overlay([clip("第一个标题"), second], PUBLICATIONS, 3.0, 120)
    asyncio.run(overlay_render.fit_texts(renderer, overlay, PUBLICATIONS, tmp_path / "fit"))
    [request] = renderer.requests
    assert [[item["text"] for item in sprite["fit"]] for sprite in request["sprites"]] == [["第一个标题"], ["第二个标题"]]
    assert renderer.limits == [(180, overlay_cpu_seconds(180))]
    assert [item["config"]["fontSize"] for item in overlay.instances] == [48, 48]


# 场景：真实浏览器里，一次运行先后加载两个不同的字效：t 是自动换行的标题，放大 2 倍后要缩小；u 是一行高的遮罩，短标题不变。
# 每个摆放都按自己 Sprite 的组件测量。
@pytest.mark.skipif(os.environ.get("IMV_TEST_RENDERER") != "1", reason="set IMV_TEST_RENDERER=1 for Linux overlay integration")
def test_real_browser_fits_two_sprites_in_one_run(tmp_path: Path) -> None:
    """t 不超过 67；u 保持 128。"""
    placements = [titled("测" * 8, font_size=100, scale=2), titled("标题", sprite_id="u")]
    sizes = [item["config"]["fontSize"] for item in fit_for_real(tmp_path, {"t": WRAPPED_TITLE, "u": LINE_MASK_TITLE}, placements, 1.0).instances]
    assert 60 <= sizes[0] <= 67
    assert sizes[1] == 128


# 场景：3 秒的摆放放进 1 秒的成片时被总长截断，Sprite 仍读到 90 帧，但只量成片里出现的前 30 帧；
# 截断之后的帧不会出现在视频里，不能让它们替被裁切的文字通过预检。
def test_fit_measures_only_the_frames_that_are_rendered(tmp_path) -> None:
    """条目长度 90 帧，采样帧落在 0～29。"""
    renderer = FitRenderer(capacity=240)
    overlay = plan_overlay([clip("标题", 3.0)], PUBLICATIONS, 1.0, 120)
    asyncio.run(overlay_render.fit_texts(renderer, overlay, PUBLICATIONS, tmp_path / "fit"))
    [request] = renderer.requests
    [sprite] = request["sprites"]
    assert [(item["frames"], item["length"]) for item in sprite["fit"]] == [([0, 7, 14, 21, 29], 90)]


# 场景：标题摆在紧贴画布顶边的位置（内容中心 y=20）、字号 200，总线可能传入 8～100 个字。8 个字缩小字号即可放进画布，位置不动；
# 100 个字缩到最小字号 24 仍有一部分在画布外，宿主把它向画布内平移最小距离而不是以 text_too_long 失败，水平位置与摆放里保存的设置不变。
@pytest.mark.skipif(os.environ.get("IMV_TEST_RENDERER") != "1", reason="set IMV_TEST_RENDERER=1 for Linux overlay integration")
def test_real_browser_nudges_a_long_title_at_the_top_edge_into_the_canvas(tmp_path: Path) -> None:
    """8 字缩到 40 以内且不平移；100 字字号 24、y 从 20 下移到约 43（三行 86px 的一半），x 不变。"""
    short = fit_for_real(tmp_path / "short", WRAPPED_TITLE, [titled("测" * 8, font_size=200, x=540, y=20)], 1.0).instances[0]
    assert short["config"]["fontSize"] <= 40
    assert short["host"]["transform"] == {"x": 540, "y": 20}
    long = fit_for_real(tmp_path / "long", WRAPPED_TITLE, [titled("测" * 100, font_size=200, x=540, y=20)], 1.0).instances[0]
    assert long["config"]["fontSize"] == 24
    assert long["host"]["transform"]["x"] == 540
    assert 40 <= long["host"]["transform"]["y"] <= 48


# 场景：标题的内容中心紧贴画布左边（x=60），8 个字在最小字号 24 下宽 192，左端仍在画布外；宿主只向右平移，
# 不写入没有移动的 y 坐标，字号缩到最小。
@pytest.mark.skipif(os.environ.get("IMV_TEST_RENDERER") != "1", reason="set IMV_TEST_RENDERER=1 for Linux overlay integration")
def test_real_browser_nudges_a_title_at_the_left_edge_sideways_only(tmp_path: Path) -> None:
    """字号 24；x 从 60 右移到约 96（192px 宽的一半），变换里仍没有 y。"""
    instance = fit_for_real(tmp_path, WRAPPED_TITLE, [titled("测" * 8, font_size=100, x=60)], 1.0).instances[0]
    assert instance["config"]["fontSize"] == 24
    assert set(instance["host"]["transform"]) == {"x"}
    assert 94 <= instance["host"]["transform"]["x"] <= 98


# 场景：摆放旋转 90°：100 个字在 972 宽的框里排成约 960px 的行，旋转后竖直方向占 960px，中心 y=100 时上端在画布外约 380px。
# 平移按旋转后的外包矩形计算，只向下移动，x 保持摆放里的值。
@pytest.mark.skipif(os.environ.get("IMV_TEST_RENDERER") != "1", reason="set IMV_TEST_RENDERER=1 for Linux overlay integration")
def test_real_browser_nudges_a_rotated_title_by_its_rotated_extent(tmp_path: Path) -> None:
    """字号 24；y 从 100 下移约 380，x 保持 540，旋转不变。"""
    instance = fit_for_real(tmp_path, WRAPPED_TITLE, [titled("测" * 100, font_size=100, x=540, y=100, rotation=90)], 1.0).instances[0]
    transform = instance["host"]["transform"]
    assert instance["config"]["fontSize"] == 24
    assert (transform["x"], transform["rotation"]) == (540, 90)
    assert 470 <= transform["y"] <= 490


KEYWORD_TITLE = """import React from "react";
import { AbsoluteFill } from "remotion";
export default function Title(props: { text: string; fontSize: number; keywordSize: number }) {
  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div style={{ width: 972, textAlign: "center", fontSize: props.fontSize, lineHeight: 1.2, whiteSpace: "normal", wordBreak: "break-all", color: "#fff" }}>
        <span style={{ fontSize: props.keywordSize || props.fontSize, color: "#f00" }}>{props.text.slice(0, 2)}</span>{props.text.slice(2)}
      </div>
    </AbsoluteFill>
  );
}
"""


def keyworded(text: str, font_size: int, keyword_size: int) -> OverlayPlacement:
    """字效 t 的一个摆放：覆盖文字、字号与关键词字号（关键词是文字的前两个字）。"""
    parameters = [{"key": "text", "stringValue": text}, {"key": "fontSize", "numberValue": font_size}, {"key": "keywordSize", "numberValue": keyword_size}]
    return OverlayPlacement.model_validate({"spriteId": "t", "start": 0, "editor": {"remotion": {"parameters": parameters}}})


# 场景：模板里关键词字号设成文字的 1.5 倍（字号 160、关键词 240）。总线传来的长标题把字号缩小时，绝对字号的关键词若不跟着缩，
# 会压住后面的字并逼着正文缩得更小；宿主让关键词字号按同一比例缩小，保持 1.5 倍，字号没有缩小时关键词字号不动。
@pytest.mark.skipif(os.environ.get("IMV_TEST_RENDERER") != "1", reason="set IMV_TEST_RENDERER=1 for Linux overlay integration")
def test_real_browser_shrinks_the_keyword_size_with_the_text(tmp_path: Path) -> None:
    """长标题：字号缩到 160 以下，关键词字号约为它的 1.5 倍而不是 240；短标题：两个字号都不变。"""
    long = fit_for_real(tmp_path / "long", KEYWORD_TITLE, [keyworded("测" * 100, 160, 240)], 1.0).instances[0]["config"]
    assert 24 <= long["fontSize"] < 160
    assert abs(long["keywordSize"] - 1.5 * long["fontSize"]) <= 1
    short = fit_for_real(tmp_path / "short", KEYWORD_TITLE, [keyworded("标题", 100, 150)], 1.0).instances[0]["config"]
    assert (short["fontSize"], short["keywordSize"]) == (100, 150)


# 场景：资产声明的 fontSize 最小值是 8，100 个字在字号 200 下放不下。宿主的下限取 24（再小的行高算不可读，“越小越放得下”的二分假设不成立），
# 在 24～200 之间二分得到放得下的字号；若下限是 8，先试 8 因“太小”判为放不下，整个任务就会以 text_too_long 失败。
@pytest.mark.skipif(os.environ.get("IMV_TEST_RENDERER") != "1", reason="set IMV_TEST_RENDERER=1 for Linux overlay integration")
def test_real_browser_never_shrinks_text_below_the_readable_size(tmp_path: Path) -> None:
    """最小值 8、字号 200、100 字：缩到 24 以上且小于 200；字号本来就是 20 的短标题不被放大。"""
    instance = fit_for_real(tmp_path / "long", WRAPPED_TITLE, [titled("测" * 100, font_size=200)], 1.0, minimum=8).instances[0]
    assert 24 <= instance["config"]["fontSize"] < 200
    small = fit_for_real(tmp_path / "small", WRAPPED_TITLE, [titled("标题", font_size=20)], 1.0, minimum=8).instances[0]
    assert small["config"]["fontSize"] == 20
