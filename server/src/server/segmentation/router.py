"""切片 HTTP 入口：调用同包业务函数，记录诊断并将错误转换为响应。"""

import logging
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
# Uvicorn 为此 logger 配置终端输出；桌面内置服务会将同一输出写入 server.log。
logger = logging.getLogger("uvicorn.error")


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
    """调用切片函数；在服务端记录阶段和 trace，响应保持原有字段与状态码。"""
    diagnostics = {"stage": "input", "trace": {}}
    try:
        result = segment(payload.model_dump(exclude={"config"}), config=payload.config, diagnostics=diagnostics)
    except APITimeoutError:
        message, status = "模型请求超时。", 504
    except APIError:
        message, status = "模型服务请求失败。", 502
    except (ValueError, RuntimeError, AssertionError) as exc:
        status = 422 if isinstance(exc, ValueError) else 500 if isinstance(exc, AssertionError) else 502
        message = str(exc)
    else:
        logger.info("切片成功 trace=%s", diagnostics["trace"])
        return result
    logger.warning("切片失败 stage=%s status=%s error=%s trace=%s",
                   diagnostics["stage"], status, message, diagnostics["trace"])
    return JSONResponse({"error": {"message": message}}, status_code=status)
