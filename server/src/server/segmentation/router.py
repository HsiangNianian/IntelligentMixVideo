"""切片 HTTP 入口：调用同包业务函数，将输入、模型及内部错误转换为响应。"""

from typing import Annotated

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse
from openai import APIError, APITimeoutError

from .examples import SEGMENTATION_REQUEST_EXAMPLE, SEGMENTATION_RESPONSE_EXAMPLE
from .schema import SegmentationRequest
from .segmentation import segment

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
    """调用切片函数；失败时返回已采集的 trace 与阶段，保留原有状态码。"""
    diagnostics = {"stage": "input", "trace": {}}
    try:
        return segment(payload.model_dump(exclude={"config"}), config=payload.config, diagnostics=diagnostics)
    except APITimeoutError:
        message, status = "模型请求超时。", 504
    except APIError:
        message, status = "模型服务请求失败。", 502
    except (ValueError, RuntimeError, AssertionError) as exc:
        status = 422 if isinstance(exc, ValueError) else 500 if isinstance(exc, AssertionError) else 502
        message = str(exc)
    return JSONResponse({"error": {"message": message, "stage": diagnostics["stage"]},
                         "trace": diagnostics["trace"]}, status_code=status)
