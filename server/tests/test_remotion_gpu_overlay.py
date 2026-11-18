"""IMS 三路线、故障归因与逐帧成对校验的离线用例。运行 pytest tests/test_remotion_gpu_overlay.py。"""

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.remotion_templates import overlay_render as render
from server.remotion_templates.gpu_resources import GPUGrant
from server.remotion_templates.settings import Settings


@pytest.mark.parametrize("failures,expected", [
    ([], ["h264-pair"]),
    (["encoder"], ["h264-pair", "gpu-webm"]),
    (["dimensions"], ["h264-pair", "gpu-webm"]),
    (["encoder", "graphics"], ["h264-pair", "gpu-webm", "cpu-webm"]),
    (["graphics"], ["h264-pair", "cpu-webm"]),
    (["business"], ["h264-pair"]),
    (["cancelled"], ["h264-pair"]),
])
def test_ims_routes_release_claim_and_discard_partial_output(tmp_path, monkeypatch, failures, expected):
    """已识别设备故障才换路线。每次先释放名额并移除半成品，业务异常和取消保持原样。"""
    calls, released, cooled, probed, events = [], [], [], [], []
    device = "test-card"
    overlay = render.Overlay(1080, 1920, 30, 6, [])

    @asynccontextmanager
    async def claim(width, height, pages, chunks, *, skip_graphics, skip_nvenc):
        """测试一张健康的卡，根据上一次故障类型重新给出允许的资源。"""
        assert (width, height, pages, chunks) == (1080, 1920, 4, 1)
        assert len(calls) == len(released)
        route = "h264-pair"
        if device in skip_graphics:
            route = "cpu-webm"
        elif device in skip_nvenc or "*" in skip_nvenc:
            route = "gpu-webm"
        try:
            yield GPUGrant(route, None if route == "cpu-webm" else device, chunks, "test")
        finally:
            released.append(route)

    async def worker(directory, **options):
        """模拟设备启动后的可信失败标记或普通渲染错误，检查超时/取消不会重复进入 worker。"""
        route = json.loads((directory / "request.json").read_text())["route"]
        assert ("gpu" in options) == (route != "cpu-webm")
        assert not (directory / "partial.mp4").exists()
        calls.append(route)
        if len(calls) <= len(failures):
            fault = failures[len(calls) - 1]
            (directory / "partial.mp4").write_bytes(b"incomplete")
            if fault == "cancelled":
                raise asyncio.CancelledError
            if fault in ("graphics", "encoder"):
                (directory / "gpu-failure.json").write_text(json.dumps({"kind": fault}))
            elif fault == "dimensions":
                (directory / "gpu-failure.json").write_text(json.dumps({"kind": "encoder", "reason": "nvenc_dimensions_unsupported"}))
            raise RuntimeError("private worker failure")
        for name in (("color.mp4", "mask.mp4") if route == "h264-pair" else ("overlay.webm",)):
            (directory / name).write_bytes(b"complete")

    async def probe(path, spec, *, stream_role):
        """两份完整文件均须送检再返回给上传器。"""
        assert released == calls
        assert spec == overlay and path.read_bytes() == b"complete"
        probed.append(stream_role)

    monkeypatch.setattr(render, "prepare_fonts", lambda *args: None)
    monkeypatch.setattr(render, "probe_overlay", probe)
    pool = SimpleNamespace(claim=claim, failed=lambda uuid, kind: cooled.append((uuid, kind)))
    renderer = SimpleNamespace(settings=Settings(_env_file=None), gpu_pool=pool, run_worker=worker, worker_browser_path=lambda: "/chrome")

    async def log(event, *, step, output):
        """线上任务的私有步骤持久记录每次选择和硬件回退原因。"""
        assert step == "remotion_video" and output["reason"]
        events.append((event, output["route"]))

    operation = render.render_ims_overlay(renderer, overlay, tmp_path, chunk_concurrency=2, frame_concurrency=4, log=log)
    if failures == ["business"]:
        with pytest.raises(render.OverlayError, match="叠加视频渲染失败"):
            asyncio.run(operation)
    elif failures == ["cancelled"]:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(operation)
    else:
        output, mask = asyncio.run(operation)
        if expected[-1] == "h264-pair":
            assert (output.name, mask.name) == ("color.mp4", "mask.mp4")
            assert probed == ["color", "mask"]
        else:
            assert output.name == "overlay.webm" and mask is None and probed == ["webm"]
    assert calls == released == expected
    assert [route for event, route in events if event == "route_selected"] == expected
    assert [route for event, route in events if event == "route_retry"] == expected[:-1]
    assert cooled == [(device, fault) for fault in failures if fault in ("encoder", "graphics")]
    if failures in (["business"], ["cancelled"]):
        assert not probed


@pytest.mark.parametrize("role,pts,color_range,frames,valid", [
    ("color", [0, 512, 1024], "tv", 3, True),
    ("mask", [0, 512, 1024], "pc", 3, True),
    ("mask", [0, 512, 1024], "tv", 3, False),
    ("mask", [512, 1024, 1536], "pc", 3, False),
    ("color", [0, 1024, 512], "tv", 3, False),
    ("mask", [0, 512], "pc", 3, False),
    ("color", [0, 512, 1024], "tv", 2, False),
])
def test_ims_stream_uses_exact_overlay_clock(monkeypatch, role, pts, color_range, frames, valid):
    """丢帧、重复或错位的 PTS、有限灰阶 mask 都不得交给 IMS。"""
    async def probe(path, timeout, *options, remote):
        """返回包含真实时间基的 FFprobe 结构，并确认按包获取 PTS。"""
        assert "-show_packets" in options and remote is False
        return {"streams": [{"codec_name": "h264", "width": 1080, "height": 1920, "avg_frame_rate": "30/1",
                             "nb_read_frames": str(frames), "time_base": "1/15360", "color_range": color_range}],
                "packets": [{"pts": value} for value in pts]}

    monkeypatch.setattr(render, "ffprobe_json", probe)
    operation = render.probe_overlay(Path(f"{role}.mp4"), render.Overlay(1080, 1920, 30, 3, []), stream_role=role)
    if valid:
        asyncio.run(operation)
    else:
        with pytest.raises(render.OverlayError) as error:
            asyncio.run(operation)
        assert error.value.code == "render_invalid"
