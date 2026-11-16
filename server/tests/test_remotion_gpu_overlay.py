"""IMS 三路线、故障归因与逐帧成对校验的离线用例。运行 pytest tests/test_remotion_gpu_overlay.py。"""

import asyncio
from pathlib import Path

import pytest

from server.remotion_templates import overlay_render as render


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
