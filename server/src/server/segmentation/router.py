"""切片 HTTP 入口：调用带诊断日志的共用业务入口，将错误转换为响应。"""

from typing import Annotated

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse
from openai import APIError

from .examples import SEGMENTATION_REQUEST_EXAMPLE, SEGMENTATION_RESPONSE_EXAMPLE
from .schema import SegmentationRequest
from .segmentation import segment, segmentation_error

# 应用只注册此路由；业务函数也可由非 HTTP 调用方直接使用。
# 显式声明文档分组，避免未打标签的接口被 Swagger UI 归入默认分组。
router = APIRouter(tags=["文案切片"])


@router.post(
    "/segmentations",
    response_model=None,
    responses={200: {"content": {"application/json": {"example": SEGMENTATION_RESPONSE_EXAMPLE}}}},
)
def create_segmentation(
    payload: Annotated[
        SegmentationRequest,
        Body(openapi_examples={
            "aligned": {
                "value": SEGMENTATION_REQUEST_EXAMPLE,
            },
        }),
    ],
) -> dict | JSONResponse:
    """调用切片函数，由共用入口记录日志；响应保持原有字段与状态码。"""
    try:
        return segment(payload.model_dump(exclude={"config"}), config=payload.config)
    except (APIError, ValueError, RuntimeError, AssertionError) as exc:
        message, status = segmentation_error(exc)
    return JSONResponse({"error": {"message": message}}, status_code=status)
