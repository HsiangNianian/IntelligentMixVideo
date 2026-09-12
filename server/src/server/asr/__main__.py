"""通过 python -m server.asr 异步转写音频 URL，完成后将原始 JSON 写入当前目录。"""

import asyncio
import json
import sys
from pathlib import Path

from .asr import transcribe

if __name__ == "__main__":
    # 保留完整结果和可读中文，覆盖当前目录的同名输出文件。
    result = asyncio.run(transcribe(sys.argv[1]))
    Path("asr_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
