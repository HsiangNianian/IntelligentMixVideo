"""提供 ASR 包的公开异步转写函数，具体请求和配置由 asr 模块负责。"""

from .asr import transcribe

__all__ = ["transcribe"]
