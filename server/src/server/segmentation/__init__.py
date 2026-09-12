"""导出独立文案切片函数；算法、配置和模型调用由 segmentation 模块负责。"""

from .segmentation import segment

__all__ = ["segment"]
