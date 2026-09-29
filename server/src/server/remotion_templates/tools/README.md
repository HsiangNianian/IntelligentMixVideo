# Remotion Agent 工具

工具契约以 [PR76 工具文档](../../../../../docs/remotion-agent-tool-contracts.md) 为准。`registry.py` 的 `@tool` 装饰器登记名称、Pydantic 入参、返回契约、限制和副作用；`catalog.py` 将声明转换为模型函数定义，`ToolSession` 负责取消、额度、资产权限和实际执行。

已接入图片 `info`、`resize`、`crop`，不包含超分辨率。图片只接受 HTTP(S) 单帧 PNG/JPEG/WebP，派生结果保存到任务数据目录并通过 `/api/templates/tool-assets/<id>.png` 读取；原图不覆盖，越界返回结构化错误码。

PR76 的端到端顺序是：图片观察 → Preset 检索/创建或修改副本 → Sprite compose → validate.code/render → sprite.create。工具名称只采用 PR76 契约，图片和行为验证不会启动隐藏模型或重复计算 Agent 工具额度。
