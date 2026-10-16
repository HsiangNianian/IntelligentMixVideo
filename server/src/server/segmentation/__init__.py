"""切片功能包：`segmentation.py` 提供切片 `segment` 与英文翻译 `translate`，`schema.py` 定义请求模型，`examples.py` 提供接口文档示例，`router.py` 注册 HTTP 路由，`settings.py` 读取模型配置。"""

from .segmentation import segment, translate

__all__ = ["segment", "translate"]
