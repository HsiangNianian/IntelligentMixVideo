"""Shared render-time clocks for published text effects; sealed sources stay immutable and execute in the sandbox."""

import json
from pathlib import Path
import subprocess


class RetimingError(ValueError):
    """A published effect cannot be safely adapted to its actual display interval."""


def duration_paths(schema: dict, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    """Locate the existing integer duration role without guessing parameter names or following external refs."""
    paths = []
    for name, field in schema.get("properties", {}).items():
        if not isinstance(field, dict):
            continue
        path = (*prefix, name)
        if field.get("x-imv-role") == "duration":
            if field.get("type") != "integer":
                raise RetimingError("动态时长参数必须是整数")
            paths.append(path)
        if field.get("type") == "object":
            paths.extend(duration_paths(field, path))
    return paths


def dynamic_duration(schema: dict) -> bool:
    """Honor explicit duration-aware assets instead of rescaling their own adaptive clock a second time."""
    flag = schema.get("x-imv-dynamic-duration")
    if "x-imv-dynamic-duration" in schema and type(flag) is not bool:
        raise RetimingError("动态时长声明必须是布尔值")

    return flag is not False and (flag is True or bool(duration_paths(schema)))


def retime_source(sprite) -> str:
    """解析并打包原模块与可信时钟包装，不执行资产；调用方每单每个资产只打包一次。"""
    frames = sprite.canvas.preview_frames
    if frames < 1 or sprite.canvas.fps < 1:
        raise RetimingError("Remotion 资产缺少有效的发布时长或帧率")
    # 新上游不填 static_frame；极短字幕默认取动画中点，旧资产保留已验收的可见帧。
    still = min(frames - 1, sprite.static_frame or (frames - 1) // 2)
    try:
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("remotion-timing.mjs"))],
            input=json.dumps({"code": sprite.tsx_code, "frames": frames,
                              "fps": sprite.canvas.fps, "still": still}),
            text=True, capture_output=True, timeout=30, check=True,
        )
        code = json.loads(result.stdout)["code"]
        if not isinstance(code, str) or not code.strip():
            raise ValueError("empty timing bundle")
        return code
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
        # 工具原始错误可能包含资产源码，只保留在异常链，不进入公开消息。
        raise RetimingError("Remotion 字效时长适配打包失败") from exc
