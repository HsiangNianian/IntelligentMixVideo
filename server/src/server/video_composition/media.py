"""纯素材时长准备：异步探测视频流，图片固定三秒；够长即停止，超时或取消回收子进程。"""

import asyncio
import json
from math import isfinite
import re

from .errors import CompositionError
from .schema import Material


async def video_duration(url: str, timeout: float) -> float:
    """读取首个视频流时长，缺省回退该轨 DURATION 标签（含语言后缀）；支持代理，诊断仅写脱敏日志。"""
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-protocol_whitelist", "http,https,tcp,tls,httpproxy",
            "-rw_timeout", str(int(timeout * 1_000_000)), "-select_streams", "v:0",
            "-show_entries", "stream=duration,start_time:stream_tags", "-of", "json", url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        output, stderr = await asyncio.wait_for(process.communicate(), timeout)
        if process.returncode:
            raise ValueError(f"FFprobe 退出码 {process.returncode}: {stderr.decode(errors='replace')[:4000]}")
        stream = json.loads(output)["streams"][0]
        raw = stream.get("duration")
        if raw in (None, "N/A"):
            tags = {key.upper(): value for key, value in stream.get("tags", {}).items()}
            tag = tags.get("DURATION")
            if tag is None:
                tag = next((value for key, value in tags.items() if re.fullmatch(r"DURATION-[A-Z]{2,3}", key)), "")
            parts = re.fullmatch(r"(\d+):([0-5]\d):([0-5]\d(?:\.\d+)?)", tag)
            if parts is None:
                raise ValueError("视频流缺少有效 duration 或 DURATION 标签")
            hours, minutes, seconds = map(float, parts.groups())
            # Matroska 的 DURATION 标签包含起始偏移，扣除视频轨起点才是实际时长。
            start = stream.get("start_time")
            raw = hours * 3600 + minutes * 60 + seconds - (0 if start in (None, "N/A") else float(start))
        duration = float(raw)
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
