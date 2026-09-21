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
未配置或留空时使用内置阿里云示例。工作区的预览视频与模板独立，保存模板只保存对象配置和时间规则。

时间输入和开始、持续方式的选择立即更新内存草稿及预览；点击「保存模板」才写入存储。空值或非法时间会提示错误，修正后继续编辑。

时间默认值按 IMS 接口配置：[转场默认 1 秒，文字入场与出场各 0.5 秒](https://help.aliyun.com/zh/ims/developer-reference/effect-configuration-description)；滤镜和 VFX 默认从视频开始持续到结束。切换固定时长时按当前视频剩余区间填写；用户已经设置的时长继续保留。

新模板的画面对象列表为空。标题、字幕及其他效果均通过左侧「特效资产」手动添加，添加后才能编辑对应参数。
修改后重启 `bun run dev`；生产构建需要重新执行 `bun run build`。
若 `.env.local` 或对应模式的环境文件设置了同名变量，会按 Vite 的优先级覆盖 `.env`。

未选择视频时使用十秒示例。填写预览视频地址并点击「加载预览视频」，浏览器读取时长和尺寸，更新预览比例、刻度和对象的实际区间。更换预览视频保留模板规则，不产生模板未保存状态。远程直链须允许浏览器跨域读取。

## 模板行为

侧栏编辑入口显示「模版编辑」，使用 Lucide `SquarePen` 的方框与铅笔线条图标；窄屏保留图标、悬停提示及无障碍名称。

启动默认进入「主页」，最大宽度为 576px 的模板列表上方显示云端模板，下方显示本地模板。右侧更新日志使用 react-markdown 渲染仓库根目录的 `CHANGELOG.md`，通过 Vite 原文导入随客户端构建打包，更新文件后重新构建即可更新发布内容。日志独立滚动，窄屏时排列在模板区域下方。每行展示名称、说明和进入箭头，点击整行进入对应环境的模板库，读取最新详情后编辑。列表内部滚动，底部并排保留「新建云端模板」与「新建本地模板」按钮；填写名称与可选描述后进入编辑，点击保存才创建存储记录。两个列表独立读取和重试；浏览器提示本地模板需要桌面客户端，并禁用本地新建按钮。名称去除首尾空白后必填，最多 100 个字符，描述最多 1000 个字符。返回主页会刷新列表，已打开的模板库与字效工作区保留草稿、播放器和订阅。界面复用 shadcn/ui 的 Card、Button、Dialog 和表单组件。

更新日志紧靠主页内容区右侧，宽屏宽度为 448px，区域没有外框和阴影。

模板库顶部只读展示环境、模板名称、描述和保存状态，并提供保存按钮。编辑区采用三栏：左侧浏览特效资产，中间实时预览，右侧调整选中对象的参数。编辑区宽度不足时参数区移到下方，窄屏按顺序排列。

右侧设置可通过右上角「×」关闭，关闭时保留对象与未保存参数。移除当前画面对象后自动关闭设置；关闭期间取消对象选中状态，桌面保留设置栏宽度，预览画面尺寸保持不变。选择画面对象或应用资产会重新打开对应设置，切换页面保留关闭状态。

- 资产区按花字、气泡、滤镜、画面特效、转场、入场、出场和循环动画分类，支持名称与编号搜索、分批展示和目录封面预览；缺少封面时显示明确占位。搜索框按 Enter 保留当前编辑。
- 花字和动画通过「应用到」选择文字对象。基础文字首次选择样式时保留文字与位置；重复添加花字、气泡、滤镜或画面特效生成独立实例。文字动画应用到最近选择的文字实例，当前实例的样式可在右侧替换。转场关联母版的两个重叠片段，允许一个转场实例。
- 视频下方的「画面对象与已添加特效」展示已有对象，点击对象打开右侧参数。添加效果统一通过左侧「特效资产」进行，移除对象后也通过资产重新添加，其他对象保持不变。
- 循环动画与入场、出场动画互斥，实际应用时按文字显示时间调整入场和出场时长。每次修改自动应用到 SDK，画面效果使用独立 `EffectTracks`，文字使用独立字幕轨道。
- 画面下方使用 `@xzdarcy/react-timeline-editor` 展示视频和独立特效轨道。视频片段只读；对象可以选中、移动和调整区间。右侧开始方式支持秒数和视频时长百分比，持续方式支持固定秒数和持续到视频结束。实例上限为 100，轨道允许重叠。拖动保留开始方式，调整右侧结束位置改为固定持续时间；调整持续到结束对象的左侧位置保留结束规则。SDK 驱动游标，点击刻度或拖动游标暂停并定位，确认定位后可以继续播放。方向键移动 0.1 秒，Home/End 定位首尾，修改效果后回到开头。
- 缩略图由独立视频元素提取，最多缓存 48 张，卸载或素材变化时取消任务。`tracks` 保存 `id`、`target`、`start_mode`、`start`、`duration` 和 `editor`；`start_mode` 为 `seconds` 或 `percent`，百分比范围为 0 至小于 100，`duration: null` 表示持续到结束。云端和桌面本地按相同规则校验，模板不保存 `media`。预览按 30 FPS 计算帧区间；超过结尾的对象不显示，结束越界时截短，动画按可用帧数缩短并展示说明，帧数不足以表达所选动画时明确报错。转场使用固定持续时间，按合成时长计算开始位置，实际应用时要求前后片段有效。
- 转场连接源视频中前后相邻的片段，预览合成时长为源视频时长减去转场重叠。添加、移除或调整转场时重新计算对象区间，保留全部时间规则。`effect-tracks.test.ts` 验证转场两路源时间、合成时长、效果范围和移除恢复。
- 模板保存结构要求 `tracks` 数组，参数仅保存在 `tracks[].editor`。新建草稿的数组为空，参数面板使用选中对象的临时视图。云端与桌面读取、保存均拒绝缺少 `tracks`、`tracks: null` 或包含顶层 `editor` 的数据；已有文件和数据库记录不自动转换。
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
`effect-editor.test.tsx` 检查三个文字对象的字段更新和重置按钮；`effect-assets-components.test.tsx` 检查所选资产与文字对象的传递。两者使用真实组件与随包目录。
`bun run build` 同时检查源码和测试的 TypeScript 类型，CI 在前端构建前执行这些测试。

测试固定 API 与示例视频地址并拦截 fetch，不需要启动后端、MySQL 或下载 SDK。

`preview-timeline.test.tsx` 检查轨道数据转换、缩略图源时间采样、播放时间同步和键盘定位边界。
`effect-tracks.test.ts` 覆盖独立对象增删、时间规则、视频替换、动画帧数、SDK 数据转换和序列化；`track-timing.test.tsx` 验证输入自动更新、单位换算、非法输入恢复和对象切换。前后端共同使用 `server/tests/template_timing_cases.json`，其中 `purpose` 说明场景，预期结果包含区间和提示，并检查计算过程保留原始规则。

服务端核心验证执行 `uv run --locked --project server pytest server/tests/test_template_tracks.py server/tests/test_template_api.py server/tests/test_video_composition_timeline.py`（仓库根目录）。现有文案合成按成片时长应用每个对象：标题使用请求文字，字幕及关键词与文案时间取交集，重复实例保留独立参数；转场在指定位置连接片段，音频总长保持不变。桌面存储验证执行 `cargo test --locked --manifest-path src-tauri/Cargo.toml --lib templates::tests`（client 目录），使用真实文件检查规则保存与失败保护。通过 `TMPDIR` 和 pytest 的 `--basetemp` 将中间文件指向已忽略的缓存目录。
`server/tests/test_template_tracks.py` 直接校验 Schema、默认参数、时间计算和序列化；`test_video_composition_timeline.py` 检查对象规则生成的合成时间线。
本地模板字段变化导致旧文件无法读取时，页面会提示删除旧模板文件，并显示完整路径及清除全部本地模板的影响。关闭客户端后删除提示中的文件，重新打开客户端即可创建新模板。
工作区测试使用真实表单、Radix 选择器和弹窗，覆盖核心编辑与草稿保护。Happy DOM 无法验证真实视频播放和 Tauri 原生行为。
Happy DOM 的小数 step 校验与浏览器不同，保存流程直接触发表单提交；浏览器原生表单约束仍需浏览器验证。
Windows 原生资源服务的回归测试位于 `src-tauri/src/localhost.rs`，执行 `cargo test --manifest-path src-tauri/Cargo.toml --lib --locked`，覆盖真实 HTTP 资源响应、查询参数、HEAD、错误主机与方法；原生检查 CI 同步执行。
