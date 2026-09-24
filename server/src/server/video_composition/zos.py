"""将 IMS 临时成片和第 3 帧 PNG 转存到 ZOS，并核实公开读取。"""

from contextlib import closing
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

import boto3
from botocore.config import Config
import httpx

from .settings import ZosSettings


def extract_third_frame(video: Path, image: Path, timeout: float) -> None:
    """按解码顺序提取索引 2 的画面；不足三帧时不生成图片。"""
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(video),
         "-map", "0:v:0", "-vf", r"select=eq(n\,2)", "-frames:v", "1",
         "-f", "image2", "-update", "1", str(image)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout,
    )
    if not image.is_file() or image.stat().st_size == 0:
        raise ValueError("IMS 成片不足三帧，无法生成封面")


def copy_video(source_url: str, task_id: str, settings: ZosSettings, timeout: float) -> tuple[str, str]:
    """同一次下载生成视频和公开 PNG；两者核验通过后只返回视频地址。"""
    key = f"imv/video_composition/{task_id}.mp4"
    image_key = f"imv/video_composition/{task_id}.png"
    public_url = f"{settings.zos_web_url.rstrip('/')}/{key}"
    image_url = f"{settings.zos_web_url.rstrip('/')}/{image_key}"
    with TemporaryDirectory() as directory, httpx.Client(timeout=timeout, follow_redirects=False) as client:
        video = Path(directory) / "video.mp4"
        image = Path(directory) / "frame.png"
        with video.open("wb") as file, client.stream("GET", source_url, headers={"Accept-Encoding": "identity"}) as response:
            response.raise_for_status()
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise ValueError("IMS 成片返回了压缩传输内容")
            declared = response.headers.get("Content-Length")
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                file.write(chunk)
            if size == 0 or (declared is not None and size != int(declared)):
                raise ValueError("IMS 成片下载不完整")
        extract_third_frame(video, image, timeout)
        storage = boto3.client(
            "s3", endpoint_url=settings.zos_api_endpoint, region_name=settings.zos_region,
            aws_access_key_id=settings.zos_access_key_id.get_secret_value(),
            aws_secret_access_key=settings.zos_secret_access_key.get_secret_value(),
            config=Config(s3={"addressing_style": "path" if settings.zos_force_path_style else "virtual"},
                          connect_timeout=timeout, read_timeout=timeout, retries={"max_attempts": 1},
                          request_checksum_calculation="when_required", response_checksum_validation="when_required"),
        )
        with closing(storage):
            for path, object_key, url, content_type in (
                (video, key, public_url, "video/mp4"),
                (image, image_key, image_url, "image/png"),
            ):
                with path.open("rb") as file:
                    storage.upload_fileobj(file, settings.zos_bucket, object_key,
                                           ExtraArgs={"ACL": "public-read", "ContentType": content_type})
                if storage.head_object(Bucket=settings.zos_bucket, Key=object_key)["ContentLength"] != path.stat().st_size:
                    raise ValueError("ZOS 对象大小与本地文件不一致")
                with client.stream("GET", url, headers={"Range": "bytes=0-0"}) as response:
                    response.raise_for_status()
                    if response.status_code not in (200, 206) or not next(response.iter_bytes(chunk_size=1), b""):
                        raise ValueError("ZOS 对象尚不能匿名读取")
    return key, public_url
