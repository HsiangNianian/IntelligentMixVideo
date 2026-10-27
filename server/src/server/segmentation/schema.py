"""切片请求契约：校验标题、文案、ASR 与可选客户端模型配置，不包含路由或业务逻辑。"""

from pydantic import BaseModel, Field

from .settings import ClientSettings


class SegmentationRequest(BaseModel):
    """无 ASR 时仅提取标题关键词；带 ASR 时由业务层检查有效文案。"""

    title: str | None = None
    script: str | None = None
    asr_result: dict | None = None
    max_segment_chars: int = Field(default=8, ge=1, strict=True)
    config: ClientSettings | None = None
