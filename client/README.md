# IntelligentMixVideo Client

基于 Tauri 2、React、TypeScript、Tailwind CSS 4 和 shadcn/ui。
模板预览继续使用阿里云 AliyunTimelinePlayer 5.2.2；Windows 安装包通过内置回环 HTTP 服务加载页面，使 SDK 识别到 `localhost`。
客户端与服务端使用同一项目版本；发版前统一更新并提交清单和锁文件，操作见[根目录发版说明](../README.md#tag-发版)。

## 开发运行

云端模式先按 [服务端说明](../server/README.md) 启动 MySQL 与 API；仅使用桌面本地模式可跳过服务端。在本目录执行：

```sh
bun install --frozen-lockfile
bun run dev
```

打开 `http://localhost:1420`。API 地址通过 `client/.env` 中的 `VITE_API_URL` 配置，未配置或留空时默认 `http://localhost:20070`。
若尚无 `.env`，可复制 `.env.example` 创建；已有文件直接修改，保留示例视频配置。

```dotenv
VITE_API_URL=http://localhost:20070
```

API 使用其他端口时同步修改此地址，不包含 `/template` 后缀。
客户端配置保存 API 与示例视频地址，数据库连接和密码只放服务端。修改后重启 Vite 并刷新页面；生产使用需重新构建。
避免在 `.env.local` 中重复设置 `VITE_API_URL`，否则会覆盖 `.env` 中的值。

首页「设置」在两种模式均提供 Remotion Agent 与上海 IMS 凭据配置并接通实际请求；`IMV_DEBUG=true` 额外显示切片、ASR。字段来自后端插件目录，值保存在当前客户端，和构建环境变量分开；桌面本地 JSON 包含明文密钥，浏览器仅当前页面内存保存。未保存时兼容服务端环境默认值，ASR 仍仅展示/保存。协议、范围与联调步骤见 [模块设置说明](src/features/settings/README.md)。

```sh
bun run build
bun run tauri dev
bun run tauri build
```

Windows 安装包仅在本机回环地址绑定系统分配的空闲端口，窗口访问 `http://localhost:<端口>`；退出程序后释放，无需额外启动 Python 或 Vite。API 允许该 localhost 来源的动态端口。
开发模式仍使用 Vite，macOS / Linux 保留原有 Tauri 加载方式。预览仍需要联网下载 SDK、字体和公开视频；空 License 的 localhost 预览保留 SDK 水印。
前端构建不代表桌面打包或跨平台预览验证通过；各平台仍须检查真实 WebView、首帧和播放。

## 示例视频配置

在 `client/.env` 中设置：

```dotenv
VITE_PREVIEW_VIDEO_URL=https://your-domain.example/preview.mp4
```

也可将视频放入 `client/public/preview.mp4`，填写 `VITE_PREVIEW_VIDEO_URL=/preview.mp4`。
未配置或留空时继续使用内置阿里云示例。所有模板共享此地址，不写入模板数据库。
修改后重启 `bun run dev`；生产构建需要重新执行 `bun run build`。
若 `.env.local` 或对应模式的环境文件设置了同名变量，会按 Vite 的优先级覆盖 `.env`。

无转场时截取源视频 0～10 秒；有转场时使用前段和第 8～13 秒生成十秒预览。SDK 的预览素材窗口仍按 14 秒处理；
请使用至少 14 秒的视频，短视频需要另外调整时间线。远程直链须允许浏览器跨域读取。

## 模板行为

启动默认进入「主页」，上方显示云端模板，下方显示本地模板。两个列表独立读取和重试；浏览器显示本地模板需要桌面客户端的提示。点击「选择模板」进入对应环境的模板库，读取最新详情后编辑。点击各环境的「新建模板」，填写名称与可选描述后进入编辑，点击保存才创建存储记录。名称去除首尾空白后必填，最多 100 个字符，描述最多 1000 个字符。返回主页会刷新列表，已打开的模板库与字效工作区保留草稿、播放器和订阅。

模板库顶部只读展示环境、模板名称、描述和保存状态，并提供保存按钮。编辑区采用三栏：左侧浏览特效资产，中间实时预览，右侧调整选中对象的参数。编辑区宽度不足时参数区移到下方，窄屏按顺序排列。

右侧设置可通过右上角「×」关闭，关闭时保留对象与未保存参数。移除当前画面对象后自动关闭设置；关闭期间取消对象选中状态，桌面保留设置栏宽度，预览画面尺寸保持不变。选择画面对象或应用资产会重新打开对应设置，切换页面保留关闭状态。

- 资产区按花字、气泡、滤镜、画面特效、转场、入场、出场和循环动画分类，支持名称与编号搜索、分批展示和目录封面预览；缺少封面时显示明确占位。搜索框按 Enter 保留当前编辑。
- 花字和动画通过「应用到」选择文字对象；点击资产即应用，同类效果替换保留该对象的文字与位置。当前结构支持标题、字幕、气泡及单个滤镜、画面特效和转场。
- 视频下方的「画面对象与已添加特效」显示各对象使用的样式与动画，点击对象打开右侧参数。移除文字对象会清除其内容和效果并恢复基础数值，其他对象保持不变；可以重新编辑基础文字或从左侧添加效果。
- 循环动画与入场、出场动画互斥；两侧控件共同阻止冲突选择。视频预览使用现有 16:9、十秒时间线，每次修改自动应用到 SDK。
- 「重置特效设置」清除当前文字对象的样式和动画，并将示例文字、字号、水平位置、垂直位置和入出场动画时长恢复为新建模板的默认值；其他对象和模板信息保持不变。

- 模板选择和新建均在主页完成；直接进入模板库且尚无草稿时，显示前往主页的入口。
- 当前环境由主页选择或新建的模板决定。连接失败、超时或服务端 5xx 时提示使用桌面本地环境；本地无需 Python 或 MySQL，但需使用桌面客户端。从主页选择其他模板或新建模板前，可保存到原环境、放弃修改或取消；读取目标失败保留原环境与草稿，并提供重试，两库不自动同步。
- 本地文件位于 Tauri 应用数据目录下的 `data/template/templates.json`。macOS 为 `~/Library/Application Support/com.intelligentmixvideo.client/data/template/`，Windows 为 `%APPDATA%/com.intelligentmixvideo.client/data/template/`，Linux 为 `${XDG_DATA_HOME:-~/.local/share}/com.intelligentmixvideo.client/data/template/`。本地目录随首次读取自动创建，JSON 损坏时明确报错，不能当成空库覆盖。
- 离线仍可从内置目录选择效果并保存；SDK、字体与示例视频预览仍需联网。
- 标题、字幕、气泡独立设置文字、字号、位置、样式、入场/出场/循环动画及动画时长。
- 画面滤镜、特效、转场和转场时长可选；预览不提交云端合成任务。
- 新模板首次保存创建记录，此后保存更新同一模板；环境、名称和描述跟随当前模板，保存包含全部效果配置。
- 切换前提供保存并切换、放弃修改、取消；失败保留草稿。刷新列表不会覆盖正在编辑的内容。
- 效果目录由 SDK 提供，动画来自静态 `motions.json`；服务端独立校验可信效果 ID。至少选择一个效果才能保存。
- 浏览器关闭或刷新时对未保存修改发出提示；不提供崩溃恢复或自动保存。
- 主页列表加载或失败仍允许新建；编辑工作区只读取所选模板详情，保存不依赖列表。尚未保存的新模板同样受切换保护。

## 开发结构

```text
src/
  main.tsx                         # React 挂载与全局样式
  App.tsx                          # 页面组合
  pages/HomePage.tsx                # 模板首页布局
  features/templates/
    TemplateHome.tsx               # 主页云端、本地模板列表和选择入口
    TemplateWorkspace.tsx          # 列表、草稿、保存及切换保护
    EffectAssets.tsx               # 资产浏览、应用和已添加对象列表
    EffectEditor.tsx               # 当前对象的文字、画面、转场设置
    effects.ts                     # 资产字段分配、互斥与移除规则
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
`workspace.test.tsx` 检查主页入口、创建表单、只读信息、环境来源、StrictMode、保存前校验、新草稿切换保护和页签保留，以及移除与关闭设置、再次应用资产的文字对象、关闭后的切换保护和退出监听清理。
`effect-assets.test.ts` 使用随包真实目录，检查所有分类的字段分配、重复选择、效果替换、文字参数保留、动画互斥、非法目标、移除和重新添加、无效数值清理以及历史未知效果。
`effect-editor.test.tsx` 检查三个文字对象的实际控件编辑、重置按钮、空数字恢复、动画互斥与转场时长；`effect-assets-components.test.tsx` 检查资产搜索、分页、作用对象、同类替换和动画禁用状态。两者使用真实组件与随包目录，无需加载视频 SDK。
`bun run build` 同时检查源码和测试的 TypeScript 类型，CI 在前端构建前执行这些测试。

测试固定 API 与示例视频地址并拦截 fetch，不需要启动后端、MySQL 或下载 SDK。
工作区测试使用真实表单、Radix 选择器和弹窗；Happy DOM 无法加载外部 SDK，实际播放与 HTTP 存储流程通过独立浏览器脚本验证，不将组件测试视为 Tauri 原生验证。
Happy DOM 的小数 step 校验与浏览器不同，保存流程直接触发表单提交；浏览器原生表单约束仍需浏览器验证。
主页真实浏览器检查执行 `bun tests/template-home.browser.mjs`，需要本机 Chrome、运行中的前端和后端，以及至少两个已有云端模板；通过 `IMV_BROWSER_URL` 指定前端地址，默认 `http://localhost:1420`。脚本只读取模板和编辑未保存草稿，验证默认主页、模板选择、取消/放弃修改与 390/768/1280 像素布局，不保存或删除模板，也不验证 Tauri 本地存储。
资产编辑的真实浏览器检查执行 `bun tests/template-assets.browser.mjs`，需要本机 Chrome、运行中的前端，以及可访问的 SDK、字体和示例视频；前端地址同样由 `IMV_BROWSER_URL` 配置。脚本使用真实视频播放和页面控件，检查三栏顺序、资产搜索与应用、独立参数、动画互斥、移除、未保存保护、页签保留与 390/768/1280/1440 像素布局，只修改未保存草稿。浏览器缓存写入已经忽略的 `node_modules/.cache/`，结束后清理独立会话目录。

完整保存流程使用本机独立 MySQL 数据库和真实 FastAPI 应用。仓库根目录执行 `uv run --locked --project server python server/tests/run_template_browser_server.py`，使用 `server/.env` 中的本机 MySQL 凭据创建随机测试数据库，监听 `127.0.0.1:20171`，退出时删除本次数据库。账号需要创建和删除数据库权限，脚本拒绝远程 MySQL。随后在 `client/` 执行 `VITE_API_URL=http://127.0.0.1:20171 IMV_DEBUG=false bun run dev --port 1427`，另一终端执行 `bun tests/template-save.browser.mjs`。每次运行使用新启动的测试服务。浏览器脚本检查测试数据库标识后，验证创建和更新、防重复提交、同名校验、三种未保存切换选择、真实离线失败与恢复、详情 404 和重试、最大长度与四种宽度布局。结束后关闭这两个测试服务；浏览器缓存自动清理。
Windows 原生资源服务的回归测试位于 `src-tauri/src/localhost.rs`，执行 `cargo test --manifest-path src-tauri/Cargo.toml --lib --locked`，覆盖真实 HTTP 资源响应、查询参数、HEAD、错误主机与方法；原生检查 CI 同步执行。
