"""切片功能包：`segmentation.py` 提供业务函数，`schema.py` 定义请求模型，`examples.py` 提供接口文档示例，`router.py` 注册 HTTP 路由，`settings.py` 读取模型配置。"""

from .segmentation import segment

__all__ = ["segment"]
