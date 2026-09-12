# Remotion 字效工作区

首页默认打开字效工作区；“模板库”标签保留原有 SDK 模板编辑器。
顶部浮板显示可复制的 TSX，左侧聊天，右侧上方预览，下方按文字图层展示可配置参数。
桌面默认窗口为 1280×800；窄屏通过“聊天 / 预览与参数”切换面板，切换不丢失当前会话。

## 使用

1. 在 `server/` 配置并启动服务。生成器的依赖、字体和模型配置见
   [服务说明](../../../../server/src/server/remotion_templates/README.md)。
2. `client/.env` 中的 `VITE_API_URL` 指定服务地址，留空默认 `http://localhost:8000`。
   修改后重启 Vite；构建产物需要重新构建。客户端不保存模型密钥。
3. 在 `client/` 执行 `bun install --frozen-lockfile`，随后 `bun run dev` 或 `bun run tauri dev`。
4. 输入描述或上传 PNG/JPEG/WebP 参考图片，至少一种；图片最多 10 MiB，首轮可同时提供文字。
   服务端也会按真实图片内容校验。后续对话用于修改成功模板或回答追问。
5. 可粘贴 HTTP(S) 视频直链作为背景。链接只发给隔离播放器，不参与模型分析，不写入导出的字效代码。
   背景静音、循环并与字效共用 Remotion 时间线；源视频需要能被当前浏览器或 WebView 解码和访问。
6. 文字/数字按 Enter 或失焦提交，滑块松开提交，颜色/选项选择后提交；提交后立即更新预览，再进行服务端验收。生成、验收、预览首帧和背景加载期间，禁止发送、图片变更及参数修改；
   允许编辑聊天文字草稿。完成或失败后释放相应锁；正常播放不锁定操作。
7. 只有成功版本能替换代码浮板；待验收参数不能复制为成功代码。失败时保留上个成功版本，
   轮询失败可“刷新任务”，终止的任务可明确重试，不自动重发写请求。
8. “复制代码”复制带当前成功默认参数的 `Export.tsx`，可在自己的 Remotion 项目中使用；
   消费方仍需安装 React/Remotion 并加载 Noto Sans CJK SC 400/700。导出文件包含画布尺寸和时长常量。
   “新增”取消已知旧任务并清空聊天、图片、代码、参数和背景，下一次发送才创建新作品。

## 模块边界

- `api.ts`：请求、超时及公开协议；`model.ts`：响应契约与参数边界。
- `useTemplateSession.ts`：当前会话、取消、任务轮询与成功结果切换。
- `ChatPanel` / `CodePanel`：聊天输入及成功代码展示。
- `ParametersPanel`：根据已验收 JSON Schema 的 `x-imv-target` 生成标量控件。
- `PreviewPanel`：sandbox iframe 与带通道、请求编号的消息握手，不在客户端编译或执行 TSX。
- `RemotionWorkspace`：统一操作锁、服务提示及响应式布局。

Player 和 TSX 由服务端在隔离渲染器内打成一份受证据清单保护的包。iframe 不允许同源权限，
只允许脚本、受管字体和背景媒体；父页面检查消息来源、当前通道与请求编号。
首帧确认或错误释放加载锁，20 秒未确认显示超时并允许重试。
旧版本如果没有交互包或带默认值的导出，需要重新生成，不在浏览器中降级执行旧源码。

## 验证

在 `client/` 执行：

```sh
bun install --frozen-lockfile
bun run test
bun run build
```

`tests/remotion.test.tsx` 使用 Bun、Happy DOM 与 React Testing Library，隔离 HTTP、剪贴板和 iframe 导航。
覆盖生成、澄清、图片、参数验收和失败恢复、新增与卸载取消、StrictMode 轮询、复制、输入边界、
渲染锁及迟到消息隔离。原模板库用例同时保留。

真实播放需另行检查：生成模板，加载可访问 MP4，播放并拖动时间轴，修改文字/字号/颜色/位置，
检查渲染期间锁定和结束后恢复，再复制到 Remotion 项目中确认默认参数。
另外检查窄屏面板切换、新增清空以及无法加载的视频；模拟 DOM 测试不证明媒体解码或 Tauri 跨平台可用。

本机 KDE/Wayland 若启动报 `Error 71 (Protocol error)` 或 GBM buffer 错误，可使用用户确认有效的启动方式：

```sh
env WEBKIT_DISABLE_DMABUF_RENDERER=1 bun run tauri dev
```

该变量仅影响此次 WebKit 启动，不写入业务代码或全局环境；仍需安装本机 WebKit/GStreamer 所需的媒体插件。
