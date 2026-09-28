"""隔离 HTTP 与 S3 验证成片、封面上传，并用真实 FFmpeg 核对第 3 帧。"""

from pathlib import Path
import shutil
import subprocess

import httpx
import pytest
from PIL import Image

from server.video_composition import zos
from server.video_composition.settings import ZosSettings


@pytest.mark.parametrize("denied_suffix", [None, ".mp4", ".png"])
def test_copy_video_publishes_video_and_frame(composition_settings, monkeypatch, denied_suffix):
    """视频与 PNG 使用同一任务前缀；任一对象不可公开读取都不能返回成功。"""
    task_id = "11111111-1111-4111-8111-111111111111"
    source = "https://ims.example.test/output.mp4?Signature=source"
    key = f"imv/video_composition/{task_id}.mp4"
    image_key = f"imv/video_composition/{task_id}.png"
    monkeypatch.setenv("ZOS_WEB_URL", "https://oss.joyfile.net")
    public = f"https://oss.joyfile.net/{key}"
    image_public = f"https://oss.joyfile.net/{image_key}"
    requests = []
    uploaded = []

    def handle(request):
        """模拟 IMS 下载与两个对象的匿名读取。"""
        requests.append(request)
        if str(request.url) == source:
            return httpx.Response(200, content=b"video", headers={"Content-Length": "5"})
        assert str(request.url) in (public, image_public) and request.headers["Range"] == "bytes=0-0"
        assert "authorization" not in request.headers
        denied = denied_suffix is not None and str(request.url).endswith(denied_suffix)
        return httpx.Response(403 if denied else 206, content=b"" if denied else b"v")

    def extract(video, image, timeout):
        """验证下载结果并写入要上传的模拟 PNG。"""
        assert video.read_bytes() == b"video" and timeout == 10
        image.write_bytes(b"\x89PNG\r\n\x1a\nx")

    monkeypatch.setattr(zos, "extract_third_frame", extract)

    class Storage:
        """记录对象级上传参数与实际读取的字节。"""

        def close(self):
            """模拟释放 S3 客户端资源。"""

        def upload_fileobj(self, file, bucket, object_key, ExtraArgs):
            """S3 传输读取完整文件后记录 ACL。"""
            uploaded.append(dict(data=file.read(), bucket=bucket, key=object_key, extra=ExtraArgs))

        def head_object(self, *, Bucket, Key):
            """模拟上传后的对象长度核验。"""
            assert Bucket == "archives" and Key == uploaded[-1]["key"]
            return {"ContentLength": len(uploaded[-1]["data"])}

    real_client = httpx.Client
    monkeypatch.setattr(zos.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))

    def storage_client(service, **kwargs):
        """核对服务端凭据与 ZOS 的虚拟主机、校验和兼容设置。"""
        assert service == "s3" and kwargs["endpoint_url"] == "https://hangzhou7.zos.ctyun.cn"
        assert kwargs["region_name"] == "hangzhou-7"
        assert kwargs["config"].s3["addressing_style"] == "virtual"
        assert kwargs["config"].request_checksum_calculation == "when_required"
        return Storage()

    monkeypatch.setattr(zos.boto3, "client", storage_client)

    if denied_suffix:
        with pytest.raises(httpx.HTTPStatusError):
            zos.copy_video(source, task_id, ZosSettings(), 10)
    else:
        assert zos.copy_video(source, task_id, ZosSettings(), 10) == (key, public)
    expected = [{"data": b"video", "bucket": "archives", "key": key,
                 "extra": {"ACL": "public-read", "ContentType": "video/mp4"}}]
    if denied_suffix != ".mp4":
        expected.append({"data": b"\x89PNG\r\n\x1a\nx", "bucket": "archives", "key": image_key,
                         "extra": {"ACL": "public-read", "ContentType": "image/png"}})
    assert uploaded == expected
    assert [str(request.url) for request in requests] == [source, public] + ([] if denied_suffix == ".mp4" else [image_public])


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 FFmpeg 解码真实视频")
def test_extract_third_decoded_frame(tmp_path: Path):
    """用红、绿、蓝三帧视频确认按序号取蓝帧，不按第 3 秒取图。"""
    video = tmp_path / "three.mp4"
    image = tmp_path / "third.png"
    frames = b"".join(bytes(rgb) * 16 * 16 for rgb in ((255, 0, 0), (0, 255, 0), (0, 0, 255)))
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "rawvideo",
         "-pixel_format", "rgb24", "-video_size", "16x16", "-framerate", "1",
         "-i", "pipe:0", "-frames:v", "3", "-c:v", "mpeg4", str(video)],
        input=frames, check=True, timeout=10,
    )
    zos.extract_third_frame(video, image, 10)
    red, green, blue = Image.open(image).convert("RGB").getpixel((8, 8))
    assert red < 20 and green < 20 and blue > 200
