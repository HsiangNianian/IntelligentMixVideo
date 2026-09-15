"""官方 IMS 异步合成与默认 VOD 存储选择；完整请求和稳定 token 由任务持久化。"""

import json

from alibabacloud_ice20201109.client import Client
from alibabacloud_ice20201109.models import GetMediaInfoRequest, GetMediaProducingJobRequest, GetStorageListRequest, SubmitMediaProducingJobRequest
from alibabacloud_tea_openapi.utils_models import Config
from darabonba.policy.retry import RetryOptions
from darabonba.runtime import RuntimeOptions

from .settings import Settings
from .schema import media_url


def submission(task_id: str, timeline: dict, output: dict, storage_location: str) -> dict:
    """构造可持久化的完整 SDK 请求；重试必须复用本请求和 token，不重新生成路径。"""
    media = {
        "StorageLocation": storage_location, "FileName": f"{task_id}.mp4",
        "Width": output["width"], "Height": output["height"], "Video": {"Fps": output["fps"]},
        "VodTemplateGroupId": "VOD_NO_TRANSCODE",
    }
    return dict(
        client_token=task_id, timeline=json.dumps(timeline, ensure_ascii=False, allow_nan=False),
        output_media_config=json.dumps(media, ensure_ascii=False), output_media_target="vod-media",
    )


class IMS:
    """使用 SDK 原生异步请求；关闭 SDK 自动重试，由持久化编排限定同 token 重试次数。"""

    def __init__(self, settings: Settings, *, region_id: str | None = None):
        """使用配置中的地域与官方主机，不请求环境元数据或修改 Bucket 权限。"""
        region_id = region_id or settings.ims_region_id
        self.client = Client(Config(
            access_key_id=settings.ims_access_key_id.get_secret_value(),
            access_key_secret=settings.ims_access_key_secret.get_secret_value(),
            security_token=settings.ims_security_token.get_secret_value() or None,
            region_id=region_id, endpoint=settings.ims_endpoint if region_id == settings.ims_region_id else f"ice.{region_id}.aliyuncs.com", protocol="https",
            retry_options=RetryOptions(retryable=False),
        ))
        self.options = RuntimeOptions(
            connect_timeout=int(settings.composition_http_timeout_seconds * 1000),
            read_timeout=int(settings.composition_http_timeout_seconds * 1000),
        )

    async def submit(self, payload: dict) -> dict:
        """提交已保存的请求；网络错误向上传递，不能推断上游尚未受理。"""
        response = await self.client.submit_media_producing_job_with_options_async(
            SubmitMediaProducingJobRequest(**payload), self.options,
        )
        return response.body.to_map()

    async def storage_location(self) -> str:
        """只读查询同地域可用 VOD 存储，优先默认项；不存在时明确失败。"""
        response = await self.client.get_storage_list_with_options_async(GetStorageListRequest(), self.options)
        available = [item for item in response.body.to_map().get("StorageInfoList", [])
                     if item.get("StorageType") == "vod_oss_bucket"
                     and str(item.get("Status")).lower() == "normal" and item.get("StorageLocation")]
        if not available:
            raise ValueError("IMS 没有可用的 VOD 输出存储")
        return next((item for item in available if item.get("DefaultStorage")), available[0])["StorageLocation"]

    async def get(self, job_id: str) -> dict:
        """只查询持久化 JobId，成功媒资 ID 和 Duration 均以云端响应为准。"""
        response = await self.client.get_media_producing_job_with_options_async(
            GetMediaProducingJobRequest(job_id=job_id), self.options,
        )
        return response.body.to_map()

    async def result_url(self, media_id: str) -> str:
        """通过 IMS 获取成片源文件的一小时有效地址；不持久化临时签名。"""
        response = await self.client.get_media_info_with_options_async(
            GetMediaInfoRequest(media_id=media_id, output_type="oss", auth_timeout=3600), self.options,
        )
        info = response.body.to_map()["MediaInfo"]
        if info.get("MediaId") != media_id:
            raise ValueError("IMS 返回了不匹配的成片媒资")
        for item in info.get("FileInfoList", []):
            source = item.get("FileBasicInfo", {})
            if source.get("FileType") == "source_file" and source.get("FileStatus") == "Normal":
                url = media_url(source["FileUrl"])
                return "https://" + url[7:] if url.startswith("http://") else url
        raise ValueError("VOD 成片源文件暂不可读取")
