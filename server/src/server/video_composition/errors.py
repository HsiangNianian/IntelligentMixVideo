"""合成阶段错误只携带固定安全摘要，供后台持久化和 HTTP 查询。"""

from datetime import UTC, datetime


class CompositionError(Exception):
    """区分本地超时、上游明确失败和受理结果未知，消息不包含原始异常。"""

    def __init__(self, code: str, message: str, stage: str):
        """保存可公开的错误字段，不把供应商响应拼接到消息中。"""
        super().__init__(message)
        self.error = {"code": code, "message": message, "stage": stage}


def remaining(deadline: str, stage: str) -> float:
    """使用可跨进程恢复的 UTC 截止时间；预算耗尽不代表云端取消。"""
    end = datetime.fromisoformat(deadline)
    if end.tzinfo is None:
        raise ValueError("截止时间缺少时区")
    seconds = (end - datetime.now(UTC)).total_seconds()
    if seconds <= 0:
        raise CompositionError(f"{stage}_timeout", "等待预算耗尽，上游任务可能仍在执行", stage)
    return seconds
