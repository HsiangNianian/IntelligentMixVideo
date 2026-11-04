"""通过 FunASR 官方 AutoModel 下载、缓存 CT-Punc；启动时预热并串行复用进程内实例，偶发失败在单次调用内重试。"""

from functools import lru_cache
from importlib.util import find_spec
import logging
from threading import Lock
from time import perf_counter


# 初始化和推理共用锁，避免并发首次加载多份模型；失败不缓存，下次请求可以重试。
_lock = Lock()
# 单次调用内加载、推理或回复格式出错时的总尝试次数（含首次），防止偶发失败；缺少依赖不重试。
MAX_ATTEMPTS = 3


@lru_cache(maxsize=1)
def _model():
    """官方库管理 ct-punc v2.0.4 的磁盘缓存；设备选择交给官方库，关闭更新检查与进度条。"""
    from funasr import AutoModel

    return AutoModel(
        model="ct-punc", model_revision="v2.0.4", ncpu=2,
        disable_update=True, disable_pbar=True, check_latest=False,
    )


def restore(text: str, *, deadline: float | None = None) -> str:
    """返回单段恢复后的正文；加载、推理及回复错误在锁内最多尝试 MAX_ATTEMPTS 次，
    等锁、加载和重试共用 perf_counter 截止时间；预热不设截止时间，预算耗尽原样抛 TimeoutError。"""
    def remaining() -> float:
        """返回等锁的剩余秒数；无请求预算时无限等待，超时不再开始推理或重试。"""
        if deadline is None:
            return -1
        seconds = deadline - perf_counter()
        if seconds <= 0:
            raise TimeoutError("切片处理超时。")
        return seconds

    if not _lock.acquire(timeout=remaining()):
        raise TimeoutError("切片处理超时。")
    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            remaining()
            try:
                model = _model()
                remaining()
                result = model.generate(input=text)
                if (not isinstance(result, list) or len(result) != 1
                        or not isinstance(result[0], dict) or not isinstance(result[0].get("text"), str)
                        or not result[0]["text"].strip()):
                    raise ValueError("CT-Punc 返回格式无效。")
            except Exception as exc:
                # 预算耗尽优先于模型错误，不能包装成 502 或继续重试。
                remaining()
                if isinstance(exc, ModuleNotFoundError) and exc.name == "funasr":
                    raise RuntimeError("请使用 --extra ct-punc 安装并启动服务端，以启用 FunASR 标点恢复。") from exc
                if attempt == MAX_ATTEMPTS:
                    raise RuntimeError("CT-Punc 标点恢复失败，请检查模型缓存及运行环境。") from exc
                logging.getLogger("uvicorn.error").warning(
                    "CT-Punc 标点恢复重试 retry=%s error=%s", attempt, type(exc).__name__)
            else:
                remaining()
                return result[0]["text"]
    finally:
        _lock.release()


def initialize() -> None:
    """已安装标点依赖时，在服务就绪前下载、加载并试推理；失败阻止启动，未启用 extra 的服务跳过。"""
    if find_spec("funasr") is None:
        return
    logger = logging.getLogger("uvicorn.error")
    logger.info("CT-Punc 正在准备模型，首次运行将下载权重。")
    restore("你好世界")
    logger.info("CT-Punc 模型已就绪。")
