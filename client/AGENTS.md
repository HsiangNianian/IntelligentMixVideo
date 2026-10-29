# 客户端协作约定

适用于 `client/`，补充根目录 `AGENTS.md`。开发运行、界面行为、示例视频配置与调试日志窗口的说明见 `client/README.md`。

## 应用结构

- `App.tsx` 挂载 `pages/HomePage.tsx`，左侧导航默认打开「主页」，模板编辑在 `features/templates/`，字效工作区在 `features/remotion_templates/`。新增界面功能按组件拆分，不把逻辑堆到 App 或首页。
- 模板库与字效工作区首次访问才加载；切换页签只隐藏已打开的工作区，保留未保存草稿、字效会话、播放器和 SSE 订阅，离开整个页面才卸载清理。
- 主页选择模板时携带 ID，新建时携带名称与描述进入模板库；已有模板读到最新详情后才替换草稿，新模板点击保存才写入，两种入口都走保存 / 放弃 / 取消保护。主页列表加载不阻塞新建，编辑保存不依赖列表。
- 聊天、任务编排、隔离 Player、参数编辑和 API 请求按职责分开放。组件卸载时清理定时器、订阅和播放器。
- 版本号只取自 `client/package.json`（发版脚本同步它），由 `vite.config.ts` 注入 `import.meta.env.IMV_VERSION`，`lib/version.ts` 提供 `APP_VERSION`。窗口标题「IntelligentMixVideo - v版本号」经 `lib/window-title.ts` 同时设置网页标题与 Tauri 原生标题栏（需要能力 `core:window:allow-set-title`）。
- 设置对话框左下角显示同一版本号与构建校验码 `APP_COMMIT`：构建时读取 git 的 7 位短提交哈希，有未提交改动时带 `-dirty`；可用 `IMV_COMMIT` 指定，没有 .git 时取 CI 的 `GITHUB_SHA`，都没有为 unknown。界面其他地方不另写版本。
- API 地址读取 `client/.env` 的 `VITE_API_URL`，未配置时默认 `http://localhost:20070`；修改后重启 Vite，生产需重新构建，注意 `.env.local` 的同名配置会覆盖它。
- 预览需要联网获取 SDK、字体和媒体，但不发起云端合成；浏览器验证不等于桌面安装包验证。
- 调试日志窗口、预览媒体服务与 Windows localhost 页面服务的约定见 `client/src-tauri/AGENTS.md`。

## 客户端前端开发范式（强制）

- 技术栈固定为 React + TypeScript strict + Vite + Tailwind CSS 4（`@tailwindcss/vite`）+ shadcn/ui，用 Bun 管理依赖；不混用 Tailwind 3 配置，不另加样式框架。
- 分层：`src/main.tsx` 只挂载应用与全局样式；`src/App.tsx` 只组合页面与确有需要的全局 provider；`src/pages/` 负责页面布局与组合；业务组件放 `src/features/<功能>/` 或 `src/components/`；`src/components/ui/` 只放 shadcn/ui 基础组件，不导入页面、不请求 API、不调用 Tauri command。
- 依赖方向是页面 → 业务组件 → 基础 UI / 工具。跨层导入用 `@/`（指向 `src/`），同目录可用相对导入。组件用 PascalCase，shadcn/ui 文件沿用上游的小写命名。
- `src/lib/` 只放真正共享的工具；真实复用后再加 `hooks/`，业务增长时再按功能提取 `features/<功能>/`，不提前建空层。
- 基础控件优先用 shadcn/ui：在 `client/` 下用 `bunx --bun shadcn@latest add <组件>` 按需添加，审阅生成的代码、依赖与主题令牌，只保留有调用方的导出，不预装整个组件库。`client/components.json` 维护路径与别名。
- 另外复用 ObsidianUI（shadcn 生态，MIT）的 select、popover、command、tooltip、sonner、slider、switch、toggle-group、empty、spinner、skeleton、flip-text 与 loaders-gooey-blobs：原语统一从 `radix-ui` 包导入，许可证记在 `public/THIRD_PARTY_NOTICES.txt`，与上游的差异写在文件头。
- 交互：特效选择用 `features/templates/EffectCombobox.tsx`；纯图标按钮用 `components/Hint.tsx` 提示（文字标签可见时不渲染；依赖 `data-state` 的 TabsTrigger 先包一层元素再交给 Hint）；保存成功等短反馈用 `toast()`，校验与持续错误内联显示。
- 样式：`src/styles/globals.css` 只放 Tailwind 导入、主题令牌与基础样式；布局用工具类，颜色只用语义令牌，条件类名用 `cn` 合并；业务样式（含模板编辑 CSS）不写死某一主题的颜色。主题令牌按需补齐，不预先建动画、路由或状态管理设施。
- 主题：`:root` 为浅色、`.dark` 为深色，用冷灰中性层级区分侧栏、工作区与面板，主色是中性近黑 / 近白，不用额外的品牌强调色，状态色只用于保存状态与提示。`index.html` 的首帧脚本按 `localStorage` 的 `imv.theme` 恢复，未保存时跟随系统；`components/ThemeToggle.tsx` 切换 `<html>` 的 `dark` 类（同时启用 shadcn/ui 的 `dark:` 变体）。系统开启「减少动态效果」时，`globals.css` 压缩 CSS 动画与过渡，`App.tsx` 的 `MotionConfig reducedMotion="user"` 处理 motion 动画。
- 状态与无障碍：局部状态优先留在组件；effect 必须清理定时器、订阅与监听器；重复渲染组件的关联 ID 用 `useId`；保留语义 HTML 与无障碍属性。
- 前端改动运行冻结安装和 `bun run build`，并在浏览器或桌面里检查相关行为；纯样式调整验证渲染与响应式，不写重复实现的断言。

## 客户端测试

- 核心测试在 `client/tests/`，用 Bun 自带运行器、Happy DOM 与 React Testing Library；在 `client/` 下执行 `bun run test`，`bun run build` 同时检查测试的类型。
- 新增 feature 必须有行为测试，按实际运行环境选测试方案，不用 Python 测 React；每个用例上方写中文场景注释。
- 测试隔离 HTTP 与 SDK，不连接真实服务，只覆盖必要的业务行为。模拟 DOM 的结果不等于真实视频播放、浏览器原生表单校验或 Tauri 验证。

## 开发与验证

客户端命令在 `client/` 下执行：

```sh
bun install --frozen-lockfile
bun run test
bun run tauri dev
bun run build
bun run tauri build
```

- `bun run tauri dev` 启动桌面开发环境，`bun run dev` 只启动 Vite 前端。
- `bun run build` 包含 TypeScript 检查与前端生产构建，不等于桌面程序构建成功。
- 本地安装包默认输出到 `client/src-tauri/target/release/bundle/`，指定 target 时在对应的 target 子目录。
- 只用 Bun：版本以 `client/package.json` 的 `packageManager` 为准（CI 的 `oven-sh/setup-bun` 读同一版本），`client/bunfig.toml` 的 `run.bun = true` 让 Vite、TypeScript 与 Tauri CLI 用 Bun 运行；维护文本锁文件 `bun.lock`，不引入其他 JavaScript 包管理器的锁文件。CI 用 `bun install --frozen-lockfile` 与 Cargo `--locked`。
- 开发环境另需 Rust stable 与对应平台的 Tauri 2 依赖；Windows 需要 MSVC C++ 构建工具、Windows SDK 与 WebView2，ARM64 主机需要匹配架构的 C++ 工具。
- 出现 `link: extra operand` 时，检查是否误用了 Cygwin 的 `link.exe`、Visual Studio C++ 工具链是否完整。这类环境问题要如实报告验证范围，不把前端构建通过说成桌面构建或跨平台 CI 通过。
