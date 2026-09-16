"""外部素材匹配的异步提交与超时单次补查；单次序列化、本地/上游 ID 分离、同源鉴权。"""

import asyncio
import json
from urllib.parse import quote, urljoin, urlsplit

import httpx
from pydantic import BaseModel, Field, TypeAdapter

from .errors import CompositionError
from .schema import CompositionRequest, MatchResult, Segment, media_url
from .timeline import validate_matches
from .settings import Settings


class Submission(BaseModel):
    """匹配 POST 只返回受理信息，不代表已有业务结果。"""

    taskId: str = Field(min_length=1, max_length=200)
    status: str


def payload(task_id: str, request: CompositionRequest, segments: list[Segment], callback_url: str) -> dict:
    """直接序列化已校验的切片字段；候选缺省时省略整个字段。"""
    # ASR 对齐已在本地完成；用空 ASR 保留切片时间，避免上游重复对齐。
    body = {
        "taskId": task_id, "text": request.text, "asr": {}, "callback_url": callback_url,
        "llm": json.dumps({"segments": [
            item.model_dump(mode="json") for item in segments
        ]}, ensure_ascii=False, allow_nan=False),
    }
    if request.materials:
        body["asset_url_list"] = [{"file_url": item.file_url, "type": item.type} for item in request.materials]
    return body


def query_url(base: str, location: str | None, task_id: str) -> str:
    """遵循 Location 的 URL 语义；拒绝跨源或逃离部署前缀的查询，防止凭证转发。"""
    submitted = f"{base}/api/v1/compat/segment-match"
    url = urljoin(submitted, location) if location else f"{base}/api/v1/tasks/{quote(task_id, safe='')}"
    media_url(url)
    source, target = urlsplit(base), urlsplit(url)
    origin = lambda item: (item.scheme, item.hostname, item.port or (443 if item.scheme == "https" else 80))
    prefix = source.path.rstrip("/") + "/"
    if origin(source) != origin(target) or not target.path.startswith(prefix):
        raise ValueError("匹配查询地址必须位于同源服务前缀下")
    return url


class Matching:
    """每个后台任务持有一个异步 HTTP 客户端，取消和退出由调用方关闭。"""

    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        """鉴权只发送至已配置服务及校验过的 Location，不跟随重定向。"""
        self.client = client
        self.settings = settings
        authorization = settings.match_authorization.get_secret_value()
        self.headers = {"Authorization": authorization} if authorization else {}

    async def submit(self, body: dict) -> tuple[str, str]:
        """POST 不自动重试；网络错误或损坏受理结果均标记为受理情况未知。"""
        try:
            async with asyncio.timeout(self.settings.composition_http_timeout_seconds):
                response = await self.client.post(
                    f"{self.settings.match_base_url}/api/v1/compat/segment-match", json=body,
                    headers=self.headers,
                )
            if 400 <= response.status_code < 500:
                raise CompositionError("matching_rejected", f"匹配服务拒绝请求（HTTP {response.status_code}）", "matching")
            if response.status_code != 202:
                raise ValueError("匹配服务未返回 202")
            accepted = Submission.model_validate(response.json())
            if accepted.status != "processing":
                raise ValueError("匹配受理状态无效")
            return accepted.taskId, query_url(self.settings.match_base_url, response.headers.get("Location"), accepted.taskId)
        except CompositionError:
            raise
        except Exception:
            raise CompositionError("matching_submission_unknown", "匹配受理结果未知，未自动重复提交", "matching") from None

    async def query_once(self, task_id: str, url: str) -> dict:
        """回调等待超时后只 GET 一次；仍未完成或网络失败均不重查、不重提。"""
        query_url(self.settings.match_base_url, url, task_id)
        try:
            async with asyncio.timeout(self.settings.composition_http_timeout_seconds):
                response = await self.client.get(url, headers=self.headers)
            if response.status_code >= 500:
                raise httpx.HTTPStatusError("匹配查询暂不可用", request=response.request, response=response)
        except (httpx.TransportError, httpx.HTTPStatusError, TimeoutError):
            raise CompositionError("matching_unavailable", "单次补查失败，上游任务可能仍在执行", "matching") from None
        if response.status_code != 200:
            raise CompositionError("matching_query_error", f"匹配查询失败（HTTP {response.status_code}），未重新创建任务", "matching")
        try:
            data = response.json()
            if not isinstance(data, dict) or data.get("task_id") != task_id or data.get("task_type") != "match":
                raise ValueError("匹配任务身份不符")
            if data["status"] == "done":
                return TypeAdapter(dict).validate_python(data["result"])
            if data["status"] == "failed":
                raise CompositionError("matching_failed", "素材匹配任务失败", "matching")
            if data["status"] in ("queued", "running"):
                raise CompositionError("matching_timeout", "回调等待超时，单次补查仍未完成", "matching")
            raise ValueError("未知匹配状态")
        except (ValueError, KeyError, TypeError):
            raise CompositionError("matching_contract_error", "匹配查询响应不符合约定", "matching") from None


def validated_matches(segments: list[dict], result: dict) -> list[dict]:
    """回调与补查共用片段校验；仅保存与本地文本、顺序及时间完全一致的结果。"""
    matches = MatchResult.model_validate(result).segments
    validate_matches(TypeAdapter(list[Segment]).validate_python(segments), matches)
    return [item.model_dump(mode="json") for item in matches]
