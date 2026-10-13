"""仅为视频合成打包时间适配宿主；发布源码保持不变，生成代码仍在既有沙箱中执行。"""

import json
from pathlib import Path
import subprocess

from .remotion_content import ContentError


def retime_source(sprite) -> str:
    """解析并打包原模块与可信时钟包装，不执行资产；调用方每单每个资产只打包一次。"""
    frames = sprite.canvas.preview_frames
    if frames < 1 or sprite.canvas.fps < 1:
        raise ContentError("Remotion 资产缺少有效的发布时长或帧率")
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
        raise ContentError("Remotion 合成时长适配打包失败") from exc
