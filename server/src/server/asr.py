"""提交音频 URL 到 Fun-ASR，轮询后返回原始 JSON；命令行入口将结果写入当前目录。"""

import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from dotenv import dotenv_values


def request_json(url, headers=None, body=None):
    """用 GET 或携带 JSON 的 POST 请求解析结果，60 秒请求超时，退出时关闭响应。"""
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(url, data=data, headers=headers or {})
    with urlopen(request, timeout=60) as response:
        return json.load(response)


def transcribe(audio_url, wait_seconds=1800, *, env_file=None):
    """从根目录 .env 读取配置（环境变量优先），返回单个音频的原始转写结果。

    输出任务 ID 到 stderr；wait_seconds 是提交后的轮询预算，超时不会取消云端任务。
    单次 HTTP 请求可能使实际等待超过轮询预算。
    配置错误、任务失败和请求异常直接抛出，不重试提交，也不提取字词。
    """
    parsed = urlparse(audio_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("请提供可访问的 HTTP/HTTPS 音频 URL")
    if env_file is None:
        env_file = Path(__file__).resolve().parents[3] / ".env"
    config = {**dotenv_values(env_file), **os.environ}
    api_key = (config.get("DASHSCOPE_API_KEY") or "").strip()
    if not api_key:
        raise ValueError("请在 .env 或环境变量中设置 DASHSCOPE_API_KEY")
    base = (config.get("ASR_BASE_URL") or "").strip().rstrip("/")
    if not base:
        raise ValueError("请在 .env 或环境变量中设置 ASR_BASE_URL")
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


if __name__ == "__main__":
    # 命令行接收音频 URL，将完整结果写为 UTF-8 JSON，覆盖当前目录的同名文件。
    result = transcribe(sys.argv[1])
    Path("asr_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
