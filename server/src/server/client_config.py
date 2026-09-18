"""客户端凭据头的共同解析边界；只校验当前请求，不保存值或分发业务。"""

from urllib.parse import unquote

from fastapi import HTTPException
from pydantic import BaseModel


def parse_config[T: BaseModel](value: str | None, model: type[T]) -> T | None:
    """接收 URI 编码的 JSON；错误只返回固定摘要，不回显密钥、输入或异常上下文。"""
    if value is None:
        return None
    try:
        if len(value) > 16384:
            raise ValueError("配置过长")
        return model.model_validate_json(unquote(value, errors="strict"))
    except ValueError:
        raise HTTPException(422, "客户端服务配置无效，请检查设置") from None
