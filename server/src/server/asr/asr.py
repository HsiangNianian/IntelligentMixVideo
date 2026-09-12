"""自动加载服务端配置，通过 HTTPS 提交与轮询 Fun-ASR，返回原始转写 JSON。"""

import math
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# 源码运行优先使用 server/.env；安装后的包在工作目录查找 .env。
_SOURCE_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class ASRSettings(BaseSettings):
    """从源码的 server/.env 或工作目录 .env 自动读取配置，环境变量优先。"""

    model_config = SettingsConfigDict(
        env_file=_SOURCE_ENV_FILE
        if _SOURCE_ENV_FILE.is_file()
        else Path.cwd() / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    dashscope_api_key: SecretStr = SecretStr("")


# 模块加载时读取一次配置；transcribe 复用配置，不反复读取 .env。
settings = ASRSettings()


def _require_https_url(url):
    """拒绝非 HTTPS、缺少主机或携带用户名的地址，防止凭证经明文请求发送。"""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username:
        raise ValueError("请提供有效的 HTTPS URL")


def request_json(url, headers=None, body=None):
    """用 httpx 发送 HTTPS 请求；成功、HTTP 错误或解析失败均关闭响应，不跟随重定向。"""
    _require_https_url(url)
    with httpx.stream(
        "GET" if body is None else "POST",
        url,
        headers=headers,
        json=body,
        timeout=60,
        follow_redirects=False,
    ) as response:
        response.raise_for_status()
        response.read()
        return response.json()


def transcribe(audio_url, wait_seconds=1800):
    """使用已加载的服务端配置，返回单个 HTTPS 音频 URL 的原始转写结果。

    输出任务 ID 到 stderr；wait_seconds 必须为有限正数，表示提交后的轮询预算。
    超时不会取消云端任务。
    单次 HTTP 请求可能使实际等待超过轮询预算。
    配置错误、任务失败和请求异常直接抛出，不重试提交，也不提取字词。
    """
    if not math.isfinite(wait_seconds) or wait_seconds <= 0:
        raise ValueError("wait_seconds 必须是有限正数")
    _require_https_url(audio_url)
    api_key = settings.dashscope_api_key.get_secret_value().strip()
    if not api_key:
        raise ValueError("请在 .env 或环境变量中设置 DASHSCOPE_API_KEY")
    # 固定使用北京地域的 Fun-ASR 服务。
    base = "https://dashscope.aliyuncs.com/api/v1"
    auth = {"Authorization": f"Bearer {api_key}"}
    submitted = request_json(
        f"{base}/services/audio/asr/transcription",
        {**auth, "Content-Type": "application/json", "X-DashScope-Async": "enable"},
        {"model": "fun-asr", "input": {"file_urls": [audio_url]}, "parameters": {}},
    )
    task_id = submitted["output"]["task_id"]
    print(f"task_id={task_id}", file=sys.stderr)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        output = request_json(f"{base}/tasks/{task_id}", auth)["output"]
        status = output["task_status"]
        if status == "SUCCEEDED":
            results = output.get("results", [])
            if len(results) != 1 or results[0].get("subtask_status") != "SUCCEEDED":
                raise RuntimeError(f"文件识别失败：{output}")
            result_url = results[0].get("transcription_url")
            if not result_url:
                raise RuntimeError(f"缺少转写结果地址：{output}")
            # 下载结果时不携带 API Key。
            return request_json(result_url)
        if status not in ("PENDING", "RUNNING"):
            raise RuntimeError(f"任务失败：{output}")
        time.sleep(2)
    raise TimeoutError(f"等待超时，任务可能仍在执行，可用 task_id={task_id} 继续查询")
