"""通过 python -m server.asr 异步转写音频 URL，完成后将原始 JSON 写入当前目录。"""

import argparse
import asyncio
import json
from pathlib import Path

from .asr import transcribe

if __name__ == "__main__":
    # 参数缺失时显示用法；帮助与参数错误均在提交云端任务前退出。
    parser = argparse.ArgumentParser(description="转写 HTTPS 音频，保存原始 JSON。")
    parser.add_argument("audio_url", help="可被云服务访问的 HTTPS 音频直链")
    args = parser.parse_args()
    # 保留完整结果和可读中文，覆盖当前目录的同名输出文件。
    result = asyncio.run(transcribe(args.audio_url))
    Path("asr_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
