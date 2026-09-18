"""ASR 包仅在读取公开转写函数时加载业务；发现设置入口不会读取配置或连接服务。"""

__all__ = ["transcribe"]


def __getattr__(name: str):
    """保留 from server.asr import transcribe 用法，延迟业务模块的配置初始化。"""
    if name == "transcribe":
        from .asr import transcribe
        return transcribe
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
