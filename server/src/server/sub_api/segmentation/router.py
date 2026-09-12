from typing import Annotated

from fastapi import APIRouter, Depends

from server.core.config import Settings, get_settings
from server.core.errors import LLMProviderError
from server.llm.client import OpenAIChatClient
from server.sub_api.segmentation.planner import SegmentPlanner
from server.sub_api.segmentation.schemas import SegmentBuildRequest, SegmentBuildResult
from server.sub_api.segmentation.service import SegmentService

router = APIRouter(prefix="/segmentations", tags=["文案切片"])


def get_segment_service(settings: Annotated[Settings, Depends(get_settings)]) -> SegmentService:
    """组装片段构建服务。

    作用与效果：依据模型配置创建规划器与工程约束参数，供路由与测试覆盖复用。
    输入：应用配置。
    输出：可处理文案对齐与切片的 `SegmentService`；模型未配置时抛出应用异常。
    """
    if not settings.llm_base_url or not settings.llm_api_key or not settings.llm_model:
        raise LLMProviderError("模型服务尚未完成配置。")
    planner = SegmentPlanner(
        OpenAIChatClient(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            model=settings.llm_model,
            timeout=settings.llm_timeout_seconds,
        ),
        max_provider_retries=settings.llm_max_retries,
    )
    return SegmentService(
        planner,
        min_duration_ms=settings.segment_min_duration_ms,
        max_duration_ms=settings.segment_max_duration_ms,
        max_keywords=settings.segment_max_keywords,
        keyword_max_length=settings.segment_keyword_max_length,
    )


@router.post("", response_model=SegmentBuildResult)
def build_segments(
    payload: SegmentBuildRequest,
    service: Annotated[SegmentService, Depends(get_segment_service)],
) -> SegmentBuildResult:
    """把正确文案与 ASR 时间轴对齐并切分为可召回片段。

    作用与效果：按文案修正文本、继承 ASR 时间，输出带关键词与定位区间的 `Segment[]`。
    输入：口播文案、ASR 转写结果与片段构建服务。
    输出：片段列表、告警与对齐诊断信息。
    """
    return service.build(script=payload.script, asr_result=payload.asr_result)
