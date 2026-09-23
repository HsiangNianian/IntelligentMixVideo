"""将 IMS 临时成片流式转存到 ZOS，并核实对象大小与匿名读取。"""

from contextlib import closing
from tempfile import TemporaryFile

import boto3
from botocore.config import Config
import httpx

from .settings import ZosSettings


def copy_video(source_url: str, task_id: str, settings: ZosSettings, timeout: float) -> tuple[str, str]:
    """只公开本任务的成片对象；固定 key 让中断后的再次上传覆盖同一位置。"""
    key = f"imv/video_composition/{task_id}.mp4"
    public_url = f"{settings.zos_web_url.rstrip('/')}/{key}"
    with TemporaryFile() as file, httpx.Client(timeout=timeout, follow_redirects=False) as client:
        with client.stream("GET", source_url, headers={"Accept-Encoding": "identity"}) as response:
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
        file.seek(0)
        storage = boto3.client(
            "s3", endpoint_url=settings.zos_api_endpoint, region_name=settings.zos_region,
            aws_access_key_id=settings.zos_access_key_id.get_secret_value(),
            aws_secret_access_key=settings.zos_secret_access_key.get_secret_value(),
            config=Config(s3={"addressing_style": "path" if settings.zos_force_path_style else "virtual"},
                          connect_timeout=timeout, read_timeout=timeout, retries={"max_attempts": 1},
                          request_checksum_calculation="when_required", response_checksum_validation="when_required"),
        )
        with closing(storage):
            storage.upload_fileobj(file, settings.zos_bucket, key,
                                   ExtraArgs={"ACL": "public-read", "ContentType": "video/mp4"})
            if storage.head_object(Bucket=settings.zos_bucket, Key=key)["ContentLength"] != size:
                raise ValueError("ZOS 成片大小与源文件不一致")
        with client.stream("GET", public_url, headers={"Range": "bytes=0-0"}) as response:
            response.raise_for_status()
            if response.status_code not in (200, 206) or not next(response.iter_bytes(chunk_size=1), b""):
                raise ValueError("ZOS 成片尚不能匿名读取")
    return key, public_url
