# IntelligentMixVideo Client

基于 Tauri 2、React、TypeScript、Tailwind CSS 4 和 shadcn/ui。
模板功能本次以 `localhost` 浏览器运行，SDK 使用阿里云 AliyunTimelinePlayer 5.2.2。

## 开发运行

云端模式先按 [服务端说明](../server/README.md) 启动 MySQL 与 API；仅使用桌面本地模式可跳过服务端。在本目录执行：

```sh
bun install --frozen-lockfile
bun run dev
```

打开 `http://localhost:1420`。API 地址通过 `client/.env` 中的 `VITE_API_URL` 配置，未配置或留空时默认 `http://localhost:8000`。
若尚无 `.env`，可复制 `.env.example` 创建；已有文件直接修改，保留示例视频配置。

```dotenv
VITE_API_URL=http://localhost:8000
```

API 使用其他端口时同步修改此地址，不包含 `/template` 后缀。
客户端配置保存 API 与示例视频地址，数据库连接和密码只放服务端。修改后重启 Vite 并刷新页面；生产使用需重新构建。
避免在 `.env.local` 中重复设置 `VITE_API_URL`，否则会覆盖 `.env` 中的值。

```sh
bun run build
bun run tauri dev
bun run tauri build
```

前端构建不代表桌面打包或跨平台预览验证通过。本次未验证 Tauri 打包后的 SDK 运行环境。
预览需要联网下载 SDK、字体和公开视频，使用支持硬件加速的 Chrome / Edge；本次按 localhost 运行，不配置 License。

## 示例视频配置

在 `client/.env` 中设置：

```dotenv
VITE_PREVIEW_VIDEO_URL=https://your-domain.example/preview.mp4
```

也可将视频放入 `client/public/preview.mp4`，填写 `VITE_PREVIEW_VIDEO_URL=/preview.mp4`。
未配置或留空时继续使用内置阿里云示例。所有模板共享此地址，不写入模板数据库。
修改后重启 `bun run dev`；生产构建需要重新执行 `bun run build`。
若 `.env.local` 或对应模式的环境文件设置了同名变量，会按 Vite 的优先级覆盖 `.env`。

当前时间线截取源视频的前段和第 8～13 秒，生成十秒预览，SDK 的预览素材窗口仍按 14 秒处理；
请使用至少 14 秒的视频，短视频需要另外调整时间线。远程直链须允许浏览器跨域读取。

## 模板行为

中等及以上窗口采用左右布局：左侧选择和配置模板，右侧预览效果并随滚动保持可见；窄屏自动改为上下排列。

- 新建、选择已有模板、完整保存、重命名、另存为和确认删除。
- 当前环境可选择本地 / 云端。桌面和浏览器均默认云端，连接失败、超时或服务端 5xx 时提示手动切换本地；本地无需 Python 或 MySQL，但需使用桌面客户端。切换前可保存到原环境、放弃修改或取消，读取目标失败保留原草稿，两库不自动同步。
- 本地文件位于 Tauri 应用数据目录下的 `data/template/templates.json`。macOS 为 `~/Library/Application Support/com.intelligentmixvideo.client/data/template/`，Windows 为 `%APPDATA%/com.intelligentmixvideo.client/data/template/`，Linux 为 `${XDG_DATA_HOME:-~/.local/share}/com.intelligentmixvideo.client/data/template/`。本地目录随首次读取自动创建，JSON 损坏时明确报错，不能当成空库覆盖。
- 离线仍可从内置目录选择效果并保存；SDK、字体与示例视频预览仍需联网。
- 标题、字幕、气泡独立设置文字、字号、位置、样式、入场/出场/循环动画及动画时长。
- 画面滤镜、特效、转场和转场时长可选；预览不提交云端合成任务。
- 保存覆盖当前模板，另存为保留原模板；重命名和另存为包含当前编辑配置。
- 切换前提供保存并切换、放弃修改、取消；失败保留草稿。刷新列表不会覆盖正在编辑的内容。
- 效果目录由 SDK 提供，动画来自静态 `motions.json`；服务端独立校验可信效果 ID。至少选择一个效果才能保存。
- 浏览器关闭或刷新时对未保存修改发出提示；不提供崩溃恢复或自动保存。

## 开发结构

```text
src/
  main.tsx                         # React 挂载与全局样式
  App.tsx                          # 页面组合
  pages/HomePage.tsx                # 模板首页布局
  features/templates/
    TemplateWorkspace.tsx          # 列表、草稿、保存及切换保护
    EffectEditor.tsx               # 文字、画面、转场设置控件
    TemplatePreview.tsx            # 播放器生命周期及预览操作
    model.ts                       # 数据类型、默认配置与选择字段映射
    api.ts                         # 四个模板接口和错误处理
    sdk.ts                         # SDK 加载、目录及类型边界
    timeline.ts                    # 配置到预览时间线的转换
    motions.json                   # 固定版本动画目录
  components/ui/                   # shadcn/ui 基础控件
  lib/utils.ts                     # cn 类名合并
  styles/globals.css               # Tailwind 与语义主题令牌
```

页面 → 功能模块 → 基础 UI / 工具；`@/` 指向 `src/`。不在基础控件中请求 API 或调用 SDK。
SDK 调整时同步核对前端动画目录与服务端白名单。预览时间线每次修改回到开头，快速修改合并后串行应用。

shadcn/ui 按实际需要引入并保留第三方声明，许可位于 `public/THIRD_PARTY_NOTICES.txt`。

## 核心测试

在 `client/` 执行：

```sh
bun install --frozen-lockfile
bun run test
bun run build
```

使用 Bun 自带运行器、Happy DOM 和 React Testing Library，测试位于 `tests/`，每个用例上方都有中文场景注释。
`templates.test.ts` 检查草稿隔离、效果去重与预览时间线；`api.test.ts` 检查请求契约、保存前校验和错误提示；
`workspace.test.tsx` 检查创建更新、未保存切换、保存失败重试、另存为和确认删除。
`bun run build` 同时检查源码和测试的 TypeScript 类型，CI 在前端构建前执行这些测试。

测试固定 API 与示例视频地址并拦截 fetch，不需要启动后端、MySQL 或下载 SDK。
工作区测试使用真实表单、Radix 选择器和弹窗，以轻量组件代替 SDK 播放器；不验证实际视频播放、字体排版或 Tauri 原生能力。
Happy DOM 的小数 step 校验与浏览器不同，保存流程直接触发表单提交；浏览器原生表单约束仍需浏览器验证。
