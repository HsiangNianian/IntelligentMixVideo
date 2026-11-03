"""通过 FunASR 官方 AutoModel 下载、缓存 CT-Punc；启动时预热并串行复用进程内实例，偶发失败在单次调用内重试。"""

from functools import lru_cache
from importlib.util import find_spec
import logging
from threading import Lock


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


def restore(text: str) -> str:
    """返回单段恢复后的正文；加载、推理及回复错误在锁内最多尝试 MAX_ATTEMPTS 次，
    仍失败时转为安全摘要，原异常仅供私有诊断；重试日志只记次数与异常类型。"""
    with _lock:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                result = _model().generate(input=text)
                if (not isinstance(result, list) or len(result) != 1
                        or not isinstance(result[0], dict) or not isinstance(result[0].get("text"), str)
                        or not result[0]["text"].strip()):
                    raise ValueError("CT-Punc 返回格式无效。")
                return result[0]["text"]
            except Exception as exc:
                if isinstance(exc, ModuleNotFoundError) and exc.name == "funasr":
                    raise RuntimeError("请使用 --extra ct-punc 安装并启动服务端，以启用 FunASR 标点恢复。") from exc
                if attempt == MAX_ATTEMPTS:
                    raise RuntimeError("CT-Punc 标点恢复失败，请检查模型缓存及运行环境。") from exc
                logging.getLogger("uvicorn.error").warning(
                    "CT-Punc 标点恢复重试 retry=%s error=%s", attempt, type(exc).__name__)


def initialize() -> None:
    """已安装标点依赖时，在服务就绪前下载、加载并试推理；失败阻止启动，未启用 extra 的服务跳过。"""
    if find_spec("funasr") is None:
        return
    logger = logging.getLogger("uvicorn.error")
    logger.info("CT-Punc 正在准备模型，首次运行将下载权重。")
    restore("你好世界")
    logger.info("CT-Punc 模型已就绪。")
