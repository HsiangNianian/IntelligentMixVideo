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
    """调用切片函数；输入错误返回 422，内部约束错误 500，模型错误 502，超时 504。"""
    try:
        return segment(payload.model_dump(exclude={"config"}), config=payload.config)
    except APITimeoutError:
        return JSONResponse({"error": {"message": "模型请求超时。"}}, status_code=504)
    except APIError:
        return JSONResponse({"error": {"message": "模型服务请求失败。"}}, status_code=502)
    except (ValueError, RuntimeError, AssertionError) as exc:
        status = 422 if isinstance(exc, ValueError) else 500 if isinstance(exc, AssertionError) else 502
        return JSONResponse({"error": {"message": str(exc)}}, status_code=status)
