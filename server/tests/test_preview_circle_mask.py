"""圆形几何资源、羽化像素、校验与真实 MP4 回归；pytest tests/test_preview_circle_mask.py，不访问人脸检测或云服务。"""
import json
import shutil
import subprocess

import pytest

from server.template.preview_mask import circle_mask_image


def test_circle_geometry_and_inward_feather():
    """非方形源里仍为正圆，留白与软边具有不同灰度；更换圆心和半径直接改变取景。"""
    hard = circle_mask_image(400, 800, 0.3, 0.4, 0.2, 0)
    soft = circle_mask_image(400, 800, 0.3, 0.4, 0.2, 0.2)
    assert hard.getpixel((120, 320)) == soft.getpixel((120, 320)) == 255
    assert hard.getpixel((190, 320)) == 255
    assert 0 < soft.getpixel((190, 320)) < 100
    assert soft.getpixel((200, 320)) == soft.getpixel((120, 400)) == 0
    moved = circle_mask_image(400, 800, 0.7, 0.6, 0.375, 0)
    assert moved.getpixel((280, 480)) == 255 and moved.getpixel((120, 320)) == 0
    assert circle_mask_image(3840, 2160, 0.5, 0.5, 0.2, 0).size == (1920, 1080)


def test_circle_resource_only_uses_geometry(client, monkeypatch):
    """实际路由把有界几何交给编码器，不写模板；非法输入在编码前拒绝。"""
    from server.template import router
    calls = []

    def encode(**params):
        """记录路由传入的参数，避免测试依赖编码器。"""
        calls.append(params)
        return b"circle-mp4"

    monkeypatch.setattr(router, "circle_mask_video", encode)
    params = dict(width=400, height=800, x=0.3, y=0.4, radius=0.2, feather=0.1, seconds=11)
    response = client.get("/template/preview-circle-mask", params=params)
    assert response.status_code == 200 and response.content == b"circle-mp4"
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["cache-control"] == "public, max-age=86400"
    assert calls == [params]
    for change in ({"x": -1}, {"y": 1.1}, {"x": "NaN"}, {"radius": 0}, {"radius": 0.6}, {"feather": 0.21}, {"seconds": 0}, {"width": 32769}):
        assert client.get("/template/preview-circle-mask", params={**params, **change}).status_code == 422
    assert len(calls) == 1
    assert not client.get("/template").json()["templates"]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="真实蒙版校验需要 FFmpeg/ffprobe")
def test_circle_real_mp4(client):
    """真实路由生成单帧长时长资源，解码后仍保留圆心及透明外部。"""
    response = client.get("/template/preview-circle-mask", params=dict(width=100, height=200, x=0.5, y=0.5, radius=0.2, feather=0.1, seconds=11))
    assert response.status_code == 200, response.text
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=duration,nb_frames", "-of", "json", "pipe:0"], input=response.content, capture_output=True, check=True, timeout=15)
    metadata = json.loads(result.stdout)["streams"][0]
    assert metadata["nb_frames"] == "1" and float(metadata["duration"]) == 11
    frame = subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"], input=response.content, capture_output=True, check=True, timeout=15).stdout
    assert frame[100 * 100 + 50] > 245 and frame[100 * 100] < 5


def test_circle_encoding_failure_is_explicit(client, monkeypatch):
    """编码器不可用时返回可识别的 503，不能用空资源假装成功。"""
    from server.template import router

    def unavailable(**params):
        """模拟缺少 FFmpeg。"""
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(router, "circle_mask_video", unavailable)
    response = client.get("/template/preview-circle-mask", params=dict(width=100, height=200, x=0.5, y=0.5, radius=0.2, feather=0, seconds=11))
    assert response.status_code == 503 and "FFmpeg" in response.json()["detail"]
