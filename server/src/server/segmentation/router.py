"""切片 HTTP 入口：调用同包业务函数，将输入、模型及内部错误转换为响应。"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from openai import APIError, APITimeoutError
from pydantic import BaseModel

from .segmentation import segment

# 应用只注册此路由；业务函数也可由非 HTTP 调用方直接使用。
router = APIRouter()


class SegmentationRequest(BaseModel):
    """校验 HTTP 必填字段与类型；ASR 内部结构由上游提供，额外字段忽略。"""

    script: str
    asr_result: dict


@router.post("/segmentations", response_model=None)
def create_segmentation(payload: SegmentationRequest) -> dict | JSONResponse:
    """调用切片函数；输入错误返回 422，内部约束错误 500，模型错误 502，超时 504。"""
    try:
        return segment(payload.model_dump())
    except APITimeoutError:
        return JSONResponse({"error": {"message": "模型请求超时。"}}, status_code=504)
    except APIError:
        return JSONResponse({"error": {"message": "模型服务请求失败。"}}, status_code=502)
    except (ValueError, RuntimeError, AssertionError) as exc:
        status = 422 if isinstance(exc, ValueError) else 500 if isinstance(exc, AssertionError) else 502
        return JSONResponse({"error": {"message": str(exc)}}, status_code=status)
