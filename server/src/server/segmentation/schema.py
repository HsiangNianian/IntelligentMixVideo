"""切片请求契约：校验文案、ASR 与可选客户端模型配置，不包含路由或业务逻辑。"""

from pydantic import BaseModel

from .settings import ClientSettings


class SegmentationRequest(BaseModel):
    """校验业务输入及可选完整配置；ASR 内部结构由上游提供。"""

    script: str
    asr_result: dict
    config: ClientSettings | None = None
