"""开场与圆形头像共用固定人脸定位；FFmpeg 可取消，检测结果随合成任务保存。"""

import asyncio
from math import floor
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from ..template.avatar import AvatarEffects
from .errors import CompositionError
from .settings import ZosSettings
from .zos import object_prefix, upload_public

MODEL = Path(__file__).with_name("models") / "face_detection_yunet_2023mar.onnx"


def locate(content: bytes, config: AvatarEffects) -> dict | None:
    """同一短片最多八帧，单人脸位置合并后留出头发和轻微移动空间；无脸返回 None。"""
    import cv2
    import numpy as np

    detector = cv2.FaceDetectorYN.create(str(MODEL), "", (320, 320), 0.8)
    boxes = []
    for jpeg in content.split(b"\xff\xd9")[:-1]:
        frame = cv2.imdecode(np.frombuffer(jpeg + b"\xff\xd9", dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("开场帧无法解码")
        height, width = frame.shape[:2]
        detector.setInputSize((width, height))
        _, faces = detector.detect(frame)
        if faces is not None:
            # 业务输入为单人；同一人偶发重复检测时取最高置信度框。
            boxes.append(max(faces, key=lambda face: face[-1])[:4])
    if not content or not content.endswith(b"\xff\xd9"):
        raise ValueError("开场没有完整视频帧")
    if not boxes:
        return None
    boxes = np.asarray(boxes)
    left, top = boxes[:, :2].min(axis=0)
    right, bottom = (boxes[:, :2] + boxes[:, 2:4]).max(axis=0)
    face_height = float(np.median(boxes[:, 3]))
    radius = min(float(max(right - left, bottom - top) * config.circle_scale / 2), width / 2, height / 2)
    return {"x": float(np.clip((left + right) / 2, radius, width - radius) / width),
            "y": float(np.clip((top + bottom) / 2 - face_height * 0.12, radius, height - radius) / height),
            "radius": radius / width,
            "aspect": width / height}


async def prepare(task_id: str, data: dict, timeout: float) -> dict | None:
    """抽取片头短区间供两种圆形效果共用；只有启用开场才上传黑底，故障不伪装为无脸。"""
    config = AvatarEffects.model_validate(data["template"].get("avatar_effects") or {})
    fps = data["output"]["fps"]
    seconds = min(data["duration_ms"] / 1000, max(1, floor(config.opening_duration * fps + 0.5)) / fps)
    command = ["ffmpeg", "-v", "error", "-protocol_whitelist", "http,https,tcp,tls,httpproxy",
               "-rw_timeout", str(int(timeout * 1_000_000)), "-i", data["request"]["videoUrl"],
               "-t", str(seconds), "-an", "-vf",
               f"fps={8 / seconds},scale=iw*sar:ih,setsar=1,scale=640:640:force_original_aspect_ratio=decrease:force_divisible_by=2",
               "-frames:v", "8", "-threads", "1", "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1"]
    process = None
    try:
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        content, stderr = await asyncio.wait_for(process.communicate(), timeout)
        if process.returncode:
            raise ValueError(f"开场抽帧失败: {stderr.decode(errors='replace')[:2000]}")
        circle = await asyncio.to_thread(locate, content, config)
        if circle is None:
            return None
        if not config.opening_enabled:
            return {**circle, "duration": 0}

        def background() -> str:
            """独立全画布黑底覆盖业务视频，圆形视频位于它上方，文字仍在最上层。"""
            with TemporaryDirectory() as directory:
                path = Path(directory) / "black.png"
                Image.new("RGB", (2, 2), "black").save(path)
                return upload_public([(path, f"{object_prefix(data)}/{task_id}/opening.png", "image/png")], ZosSettings(), timeout)[0]

        return {**circle, "duration": seconds, "background": await asyncio.to_thread(background)}
    except Exception as exc:
        raise CompositionError("avatar_opening_failed", "数字人开场检测失败，请检查视频、检测模型和服务环境", "assembling") from exc
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
