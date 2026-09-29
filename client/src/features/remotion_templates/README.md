# Remotion 客户端

新生成使用三层 ReAct 和 PR76 工具，主画布固定为 1080×1920、30 FPS；总时长由实例编排决定。
成功版本 `spec.schema_version=2` 保存组合 Sprite，参数面板按实例编辑嵌套 JSON。
非法 JSON 不进入预览并阻止保存；保存只提交净变化，服务端检查 Schema 和基础运行。
嵌套对象递归合并，数组、标量和 null 整体替换。撤销恢复成功参数，历史预览保持只读。

模型只通过服务端调用，聊天仍使用 `/api/templates` API 和作品级 SSE。
新结果交付 Export.tsx 与隔离 Player；代码、参数与时长由服务端封存，修改后重新构建。
背景视频只留在客户端，不进入 Agent 请求或导出源码。

旧版本继续显示原画布与扁平控件。PR74 的云端 Sprite 发布协议独立，新的组合版本不显示该发布按钮。
会话切换、新增、历史预览继续使用保存/放弃/取消保护，取消请求由服务端持久化后停止工作。

在 `client/` 执行 `bun install --frozen-lockfile`、`bun run test` 和 `bun run build`。
纯 UI 测试不执行真实模型、Remotion 或浏览器。Linux 行为验证命令见
[服务端说明](../../../../../server/src/server/remotion_templates/README.md)。
