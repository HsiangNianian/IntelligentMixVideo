"""纯素材时长准备：异步探测视频流，图片固定三秒；够长即停止，超时或取消回收子进程。"""

import asyncio
import json
from math import isfinite

from .errors import CompositionError
from .schema import Material


async def video_duration(url: str, timeout: float) -> float:
    """读取首个视频流时长，支持环境 HTTP 代理；失败诊断由异常链交给日志脱敏，对外仅固定摘要。"""
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-protocol_whitelist", "http,https,tcp,tls,httpproxy",
            "-rw_timeout", str(int(timeout * 1_000_000)), "-select_streams", "v:0",
            "-show_entries", "stream=duration", "-of", "json", url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        output, stderr = await asyncio.wait_for(process.communicate(), timeout)
        if process.returncode:
            raise ValueError(f"FFprobe 退出码 {process.returncode}: {stderr.decode(errors='replace')[:4000]}")
        duration = float(json.loads(output)["streams"][0]["duration"])
        if not isfinite(duration) or duration <= 0:
            raise ValueError("视频流时长无效")
        return duration
    except (OSError, TimeoutError, ValueError, KeyError, IndexError, TypeError):
        raise CompositionError("material_probe_failed", "无法读取素材视频的有效时长", "assembling") from None
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()


async def material_durations(materials: list[Material], duration: float, timeout: float) -> list[float]:
    """按请求顺序读取实际需要的素材；保留源时长供时间线裁切，不访问后续未使用的素材。"""
    durations = []
    total = 0.0
    for material in materials:
        seconds = 3.0 if material.type == "image" else await video_duration(material.file_url, timeout)
        durations.append(seconds)
        total += seconds
        if total + 1e-8 >= duration:
            return durations
    raise CompositionError(
        "materials_duration_insufficient", f"素材总时长 {total:g} 秒不足目标时长 {duration:g} 秒", "assembling",
    )
