"""切片请求契约：校验 HTTP 必填字段与类型，不包含接口示例、路由和业务逻辑。"""

from pydantic import BaseModel


class SegmentationRequest(BaseModel):
    """校验 HTTP 必填字段与类型；ASR 内部结构由上游提供，额外字段忽略。"""

    script: str
    asr_result: dict
