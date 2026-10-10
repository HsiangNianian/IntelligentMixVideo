"""Remotion 叠加视频：时长优先级、摆放规划、任务流转、ZOS 转存与回调的离线回归。

不启动浏览器、ffprobe 或 ZOS：渲染器、探测与上传由替身承担，任务表使用临时 SQLite。
在 server/ 目录执行 `uv run --locked pytest tests/test_remotion_overlay_render.py -v`。
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from generated.imv.sprite.v1 import sprite_pb2 as pb
from google.protobuf.json_format import MessageToDict

from server.app import app
from server.remotion_templates import overlay_render, overlay_service
from server.remotion_templates.overlay_render import OverlayError, plan_overlay, resolve_total_seconds
from server.remotion_templates.overlay_router import overlay_renders
from server.remotion_templates.overlay_service import OverlayRenders
from server.remotion_templates.settings import Settings
from server.remotion_templates.store import Store

CODE = "export default function Sprite() { return null; }"


def sprite(sprite_id: str, *, width=1080, height=1920, fps=30, frames=60) -> pb.PublishedSprite:
    """构造一个已发布 Sprite：默认 2 秒、无可编辑参数。"""
    return pb.PublishedSprite(
        sprite_id=sprite_id, name=sprite_id, kind=pb.SPRITE_KIND_TEXT, tsx_code=CODE,
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

    def worker_browser_path(self) -> str:
        """返回固定浏览器路径。"""
        return "/browser"

    async def run_worker(self, directory: Path, *, worker=None, timeout_seconds=None) -> dict:
        """校验 worker 名称，保存请求并产出文件。"""
        assert worker == "overlay-worker.mjs"
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
    monkeypatch.setattr(overlay_service, "upload_public", upload)
    monkeypatch.setattr(overlay_service, "ZosSettings", lambda: SimpleNamespace())
    monkeypatch.setattr(overlay_service.sprites, "get_bindings", lambda store, style_id: (1, bindings, None))
    monkeypatch.setattr(overlay_service.sprites, "get", lambda store, sprite_id: PUBLICATIONS[sprite_id])
    return OverlayRenders(store, renderer, settings), renderer, uploads


async def finish(overlays: OverlayRenders) -> None:
    """等待全部后台任务（渲染与通知）结束。"""
    while overlays.tasks:
        await asyncio.gather(*list(overlays.tasks))


# 场景：成功路径按 duration 渲染，转存 ZOS 指定键，并向回调发送四字段通知。
def test_task_renders_uploads_and_notifies(service, monkeypatch) -> None:
    """GET 返回 ZOS 地址；回调体为 taskId/status/videoUrl/errorMessage，源文件在任务结束后被清理。"""
    overlays, renderer, uploads = service
    posted = []

    class Client:
        """记录 POST 的 httpx 替身。"""

        def __init__(self, **_): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *_): ...

        async def post(self, url, json):
            posted.append((url, json))
            return httpx.Response(200)

    monkeypatch.setattr(overlay_service.httpx, "AsyncClient", Client)

    async def scenario():
        task_id = await overlays.accept(uuid4(), None, 4.0, "https://bus.example/cb")
        await finish(overlays)
        return task_id

    task_id = asyncio.run(scenario())
    view = overlays.get(task_id)
    key = f"imv/overlay_render/{task_id}.webm"
    assert view["status"] == "succeeded" and view["result"] == {"videoUrl": f"https://zos.example/{key}"}
    assert uploads == [(key, "video/webm", b"webm")]
    assert renderer.requests[0]["composition"] == {"width": 1080, "height": 1920, "fps": 30, "duration_in_frames": 120}
    assert [i["start_frame"] for i in renderer.requests[0]["instances"]] == [0, 30]
    assert posted == [("https://bus.example/cb", {
        "taskId": task_id, "status": "succeed", "videoUrl": f"https://zos.example/{key}", "errorMessage": None,
    })]
    assert not (overlays.store.root / "overlays" / task_id).exists()


# 场景：渲染失败时任务为 failed，公开错误脱敏且不上传。
def test_render_failure_is_sanitized(service) -> None:
    """失败状态保留错误码，不含本地路径，也没有 ZOS 地址。"""
    overlays, renderer, uploads = service
    renderer.failure = RuntimeError("Traceback at /home/secret/path")

    async def scenario():
        task_id = await overlays.accept(uuid4(), None, 3.0, None)
        await finish(overlays)
        return task_id

    view = overlays.get(asyncio.run(scenario()))
    assert view["status"] == "failed" and view["result"] is None
    assert view["error"]["code"] == "render_failed"
    assert uploads == []


# 场景：受理阶段即拒绝不可能完成的请求，不写任务行。
def test_accept_rejects_unknown_duration_and_overlong(service, monkeypatch) -> None:
    """duration 超过上限和缺少任何时长来源分别 422。"""
    overlays, _, _ = service
    with pytest.raises(HTTPException) as caught:
        asyncio.run(overlays.accept(uuid4(), None, 9999.0, None))
    assert caught.value.status_code == 422
    monkeypatch.setattr(overlay_service, "fixed_end_seconds", lambda *_: None)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(overlays.accept(uuid4(), None, None, None))
    assert caught.value.status_code == 422 and "duration" in caught.value.detail or "时长" in caught.value.detail


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
        assert client.post("/api/v1/overlay-renders", json={"styleId": str(uuid4()), "bogus": 1}).status_code == 422
        assert client.get(f"/api/v1/overlay-renders/{uuid4()}").status_code == 404
        task_id = created.json()["taskId"]
        assert client.get(f"/api/v1/overlay-renders/{task_id}").json()["taskId"] == task_id
    finally:
        app.dependency_overrides.pop(overlay_renders, None)
