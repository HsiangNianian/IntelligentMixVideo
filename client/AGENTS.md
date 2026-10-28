# 客户端协作约定

适用于 `client/`，补充根目录 `AGENTS.md`。

## 应用结构

- `App.tsx` 挂载 `pages/HomePage.tsx`，左侧导航提供默认打开的「主页」、`features/templates/` 模板库和 `features/remotion_templates/` 字效工作区。主页通过 `TemplateHome` 展示云端模板，读取失败可重试；重新进入主页刷新列表。主页选择模板携带 ID，或通过新建表单携带名称与描述进入模板库；已有模板读取最新详情成功后才替换草稿，新模板点击保存才写入。两种入口均复用保存/放弃/取消保护。底部设置入口组合环境信息与 `features/settings/` 动态插件表单；窄屏使用带无障碍名称的图标栏，切换设置同样保留工作区。聊天、任务编排、隔离 Player、参数编辑和 API 请求按职责分离。客户端版本取自 `client/package.json`（发版脚本同步它），由 `vite.config.ts` 注入 `import.meta.env.IMV_VERSION`，`lib/version.ts` 提供 `APP_VERSION` 与窗口标题「IntelligentMixVideo - v版本号」；`App.tsx` 通过 `lib/window-title.ts` 设置网页标题与 Tauri 原生标题栏（后者不跟随网页标题，需要能力 `core:window:allow-set-title`），设置对话框左侧导航栏左下角显示同一个版本号，其下是构建校验码（`APP_COMMIT`：构建时 `vite.config.ts` 读取 git 得到的 7 位短提交哈希，工作区有未提交改动时带 `-dirty`；可用 `IMV_COMMIT` 显式指定，没有 .git 时退到 CI 的 `GITHUB_SHA`，都没有则为 unknown），用来核对运行的是哪一版代码，界面不另写版本。
- 模板库与字效工作区首次访问才加载；切换到其他页签只隐藏已打开的工作区，保留模板库未保存草稿及字效会话、播放器和 SSE 订阅，离开整个页面才卸载清理。
- API 地址读取 `client/.env` 的 `VITE_API_URL`，未配置或留空时默认 `http://localhost:20070`；CORS 允许精确 localhost 主机的动态 HTTP 端口；未处理的异常由 `app.py` 的 `InternalErrors`（位于 CORS 之内）回答带来源头的 500 JSON 后照常抛出记录，跨域客户端不会把服务端错误误报为「无法连接服务端」。修改配置后重启 Vite，生产需重新构建；避免 `.env.local` 同名配置覆盖。主页列表加载不阻塞新建，编辑保存不依赖列表，返回主页时读取最新结果。预览仍需联网获取 SDK、字体和媒体，不发起云端合成，不将浏览器验证等同于桌面安装包验证。
- 组件卸载时清理定时器、订阅和播放器。新增界面功能遵循组件化结构，不把所有逻辑堆到 App 首页。

## 客户端前端开发范式（强制）

- 技术栈固定为 React + TypeScript strict + Vite + Tailwind CSS 4 + shadcn/ui，使用 Bun 管理依赖。Tailwind 使用 `@tailwindcss/vite`；不混用 Tailwind 3 配置或另加样式框架。
- `src/main.tsx` 只挂载应用和全局样式；`src/App.tsx` 只组合页面与确有需求的全局 provider；`src/pages/` 负责页面布局和组件组合。
- `src/features/templates/` 放模板业务组件与通信逻辑；其他业务组件可放 `src/components/`；`src/components/ui/` 放 shadcn/ui 基础组件，只负责可组合的 UI，不导入页面、不请求 API、不调用 Tauri command。
- `src/lib/` 放实际共享的工具，当前只有合并类名的 `cn`。真实复用后再增加 `hooks/`；业务增长时才按功能提取 `features/<功能>/`，不提前创建空层。
- 依赖方向为页面 → 业务组件 → 基础 UI / 工具。跨层导入使用 `@/`（指向 `src/`）；文件内或同目录的相对导入可保留。组件命名保持现有 PascalCase，shadcn/ui 文件遵循上游小写命名。
- 有交互和无障碍语义的基础控件优先使用 shadcn/ui；在 `client/` 执行 `bunx --bun shadcn@latest add <组件>` 按需添加，随后审阅生成代码、依赖和主题令牌，只保留有调用方的导出。不要预装整个组件库。
- `client/components.json` 维护 shadcn/ui 路径与别名；`src/styles/globals.css` 是全局样式入口，只放 Tailwind 导入、主题令牌和基础样式。组件布局使用工具类，颜色使用语义令牌，条件类名通过 `cn` 合并。
- 应用提供浅色与深色两套主题：`globals.css` 的 `:root` 为浅色、`.dark` 为深色，以冷灰中性层级区分侧栏、工作区与面板，主色为中性近黑 / 近白，不使用额外品牌强调色；状态色只用于保存状态与提示。`index.html` 首帧脚本按 `localStorage` 的 `imv.theme` 恢复选择，未保存时跟随系统；侧栏底部 `components/ThemeToggle.tsx` 切换 `<html>` 的 `dark` 类（同时启用 shadcn/ui 的 `dark:` 变体）。业务样式（含模版编辑 CSS）只引用令牌，不写死某一主题的颜色。系统开启「减少动态效果」时，`globals.css` 统一压缩 CSS 动画与过渡，`App.tsx` 的 `MotionConfig reducedMotion="user"` 处理 motion 动画。
- 基础组件另复用 ObsidianUI（shadcn 生态，MIT）的 select、popover、command、tooltip、sonner、slider、switch、toggle-group、empty、spinner、skeleton、flip-text 与 loaders-gooey-blobs，统一从 `radix-ui` 包导入原语，许可证记录在 `public/THIRD_PARTY_NOTICES.txt`，与上游差异写在文件头。模版编辑的特效选择使用 `features/templates/EffectCombobox.tsx` 可搜索下拉框；纯图标按钮使用 `components/Hint.tsx` 悬停提示，文字标签可见时不渲染提示，依赖 `data-state` 的 TabsTrigger 须先包一层元素再交给 Hint；保存成功等短反馈使用 `toast()`，校验与持续错误仍内联显示。
- 主题令牌按使用需求补齐，不预先创建动画、路由或状态管理设施。局部状态优先留在组件；effect 必须清理定时器、订阅及监听器。重复渲染组件的关联 ID 用 `useId`，保留语义 HTML 和无障碍属性。
- 前端改动运行冻结依赖安装和 `bun run build`，并检查浏览器/桌面中的相关行为。新增 feature 必须提供行为测试；测试方案按实际运行环境选择，不用 Python 强行测试 React。纯样式调整验证渲染与响应式，不写重复实现的断言。

## 客户端测试

- 客户端核心测试在 `client/tests/`，使用 Bun 自带运行器、Happy DOM 和 React Testing Library；在 `client/` 执行 `bun run test`，每个用例上方写中文场景注释。测试隔离 HTTP 与 SDK，不连接真实服务；只覆盖必要业务行为，不把模拟 DOM 验证等同于真实视频播放、浏览器原生表单校验或 Tauri 验证。`bun run build` 同时检查测试类型。

## 开发与验证

客户端命令在 `client/` 下执行：

```sh
bun install --frozen-lockfile
bun run test
bun run tauri dev
bun run build
bun run tauri build
```

- `bun run tauri dev` 启动桌面开发环境；`bun run dev` 仅启动 Vite 前端。
- `bun run build` 包含 TypeScript 检查和前端生产构建，不等同于桌面程序构建成功。
- 本地桌面安装包默认输出到 `client/src-tauri/target/release/bundle/`；指定 target 时位于对应 target 子目录。
- 保留并维护 `client/bun.lock` 和 `client/src-tauri/Cargo.lock`。CI 使用 `bun install --frozen-lockfile` 和 Cargo `--locked`。

开发环境需要 Bun（版本以 client/package.json 的 packageManager 字段为准）、Rust stable 和对应平台的 Tauri 2 依赖。
CI 使用 `oven-sh/setup-bun` 读取同一版本。`client/bunfig.toml` 的 `run.bun = true` 让 Vite、TypeScript 和 Tauri CLI 使用 Bun 运行。
维护 Bun 文本锁文件 `bun.lock`，不要重新引入其他 JavaScript 包管理器的锁文件。
Windows 需要 MSVC C++ 构建工具、Windows SDK 和 WebView2；ARM64 主机需要匹配架构的 C++ 工具。
若出现 `link: extra operand`，检查是否误用了 Cygwin 的 `link.exe`，以及 Visual Studio C++ 工具链是否安装完整。
这类错误属于环境问题，应报告实际验证范围，不把前端构建通过描述为桌面构建或跨平台 CI 全部通过。
