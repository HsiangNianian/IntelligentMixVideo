# IntelligentMixVideo 前端重设计

本文是客户端界面重设计的交接说明：设计语言、各页面规格，以及把原型接入正式客户端时要做的事。原型代码在本目录，入口是 `client/design.html`。

## 1. 现状

- **原型，不是功能**：本目录是独立的设计沙盒，全部使用假数据，不连接后端，也不引用 `src/features/` 的业务代码。生产构建只打包 `index.html`，不包含沙盒；`bun run build` 的类型检查会覆盖这里的代码。
- **已确认的设计**：启动开场、导航栏、主页、模板编辑、AI 字效、设置。
- **还没有设计**：字体库（见第 7 节「待决定的问题」）。
- **接入完成后**：删除本目录、`client/design.html`，以及只供沙盒使用的依赖。

### 打开方式

```sh
cd client
bun install --frozen-lockfile
bun run dev
```

浏览器访问 <http://localhost:1420/design.html>。每次打开先播放启动开场，点击或按任意键跳过。

左侧导航栏中段有一个虚线圆形按钮「原型状态」，只在原型里存在。它可以：
- 重播开场；
- 切换配色、桌面 / 浏览器环境；
- 切换模板列表的加载 / 失败 / 空状态；
- 切换内置服务的启动状态；
- 切换未保存标记与删除结果。

### 文件结构

| 文件 | 内容 |
| --- | --- |
| `main.tsx`、`App.tsx` | 沙盒入口；组合导航栏、开场、各页面与对话框，持有假数据与原型状态 |
| `design.css` | 主题令牌、动画关键帧与基础样式 |
| `ui.tsx`、`dialog.tsx`、`controls.tsx` | 基础组件：按钮、提示、骨架、输入框、对话框外壳、分段选择、开关、滑杆、数值输入、下拉、属性分组 |
| `icons.tsx`、`Logo.tsx` | Solar 图标子集与产品标志 |
| `Rail.tsx` | 左侧导航栏 |
| `Splash.tsx`、`BootScreen.tsx` | 启动开场；内置服务启动中与启动失败 |
| `home/` | 主页：开始创作入口、模板分组、模板卡片、新建与删除对话框 |
| `editor/` | 模板编辑：顶栏、素材栏、预览、时间轴、属性栏 |
| `effects/` | AI 字效：会话列表、对话、输入区、预览、参数 |
| `SettingsDialog.tsx` | 设置 |
| `mock.ts`、`PrototypePanel.tsx` | 假数据与原型状态面板，接入时删除 |

## 2. 设计原则

1. **画面是主角**：界面本身是中性的冷白与浅灰；模板封面、预览画面这些「内容」才是色彩最丰富的部分。
2. **只有一个强调色，而且用得少**：靛蓝只用于选中、进度、链接，以及 AI 发送键。主要操作按钮用墨色（近黑），不用强调色铺满大块面积。
3. **状态都要有原因**：不能发送、不能修改、失败时，都用一行字说明原因和恢复办法，不只是把按钮置灰。
4. **选择优先于输入**：提供预设、灵感提示与追问建议，减少运营同事从零写描述的负担。
5. **动效要有含义**：动效用来表达状态变化或因果关系，比如开场的「三段素材混剪成片」、输入框从页面中央落到底部；都可以跳过，系统开启「减少动态效果」时统一压缩。

## 3. 视觉基础

### 3.1 配色令牌

所有颜色都定义在 `design.css` 的 `@theme` 里。沙盒清空了 Tailwind 默认色板，组件只能使用下面这些语义色。模板封面、素材缩略图等「画面内容」的颜色不随配色变化。

| 令牌 | 值（冷白） | 用途 |
| --- | --- | --- |
| `canvas` | `oklch(0.973 0.002 265)` | 工作区底色 |
| `surface` / `rail` / `overlay` | `oklch(1 0 0)` | 卡片、导航栏、对话框 |
| `sunken` | `oklch(0.99 0.001 265)` | 输入框底色 |
| `raised` / `hover` | `oklch(0.955 / 0.925 …)` | 悬停、选中底、分段控件底 |
| `line` / `line-strong` | 墨色 9% / 16% 透明度 | 分隔线、边框 |
| `fg` / `fg-muted` / `fg-subtle` | `oklch(0.21 / 0.46 / 0.6 …)` | 正文、次要文字、说明文字 |
| `ink` / `ink-hover` / `on-ink` | `oklch(0.24 0.008 265)` 等 | 主要按钮的底色与文字 |
| `accent` / `accent-hover` / `accent-ink` | `oklch(0.55 0.2 275)` 等 | 强调色及其上的文字 |
| `danger` / `success` / `warning` | 红 / 绿 / 橙 | 只表达状态 |
| `brand-from` / `brand-to` | 蓝 → 紫 | 标志渐变 |
| `spectrum-1~3` | 蓝、靛、紫 | 开场素材块与插图点缀 |
| `scrim` / `shimmer` / `splash` | — | 对话框遮罩、骨架高光、开场背景 |

`html[data-palette="graphite"]` 是一套保留的深色候选（Linear 式的中性深灰）。它用同一组令牌名覆盖颜色值，可以在原型状态面板里切换查看。

### 3.2 字体

- **拉丁字母与数字**：Geist（`@fontsource-variable/geist`）。
- **ID、地址、计数等**：Geist Mono（`@fontsource-variable/geist-mono`）。
- **中文**：依次回落到苹方、鸿蒙、MiSans、微软雅黑、思源黑体。
- **宋体**：只在示意画面里使用（`--font-serif`）。
- **字号**：正文 13～14px，说明文字 11～12px，页面标题 26px，对话框标题 17px。

### 3.3 图标

- 使用 [Solar](https://www.figma.com/community/file/1166831539721848736)（480 Design，CC BY 4.0），替换现在的 Lucide。
- 默认用线性（linear）；选中项、彩色底块与强调处用粗体双色（duotone）。
- `icons.tsx` 只收录用到的图标，SVG 原样取自 Iconify：`https://api.iconify.design/solar.json?icons=名称-linear,名称-bold-duotone`。新增图标时用同样的方式追加。
- 叉号 `close` 是按 Solar 线性风格补画的，Solar 没有单独的叉号。

### 3.4 标志

标志是品牌渐变的圆角底板上，一个播放三角被横切成三条，中间一条略向右错开，像时间线上三段素材合成一个播放键。见 `Logo.tsx`，颜色取自 `brand-from` / `brand-to` 令牌。

接入时还要导出成应用图标：`src-tauri/icons` 下的各尺寸 png、icns、ico。

### 3.5 形状与阴影

| 元素 | 圆角 |
| --- | --- |
| 按钮 | 8～10px |
| 输入框 | 10px |
| 卡片 | 12～18px |
| 对话框 | 20px |
| AI 输入框 | 22px |

- `shadow-lift`：悬停浮起、浮层。
- `shadow-pop`：对话框、弹出层。

### 3.6 动效

- **常用时长**：浮层与提示 120～220ms；卡片悬停 300～500ms，用带回弹的 `cubic-bezier(0.34,1.4,0.64,1)`；页面切换 180ms。
- **关键帧**：都写在 `design.css`，包括骨架高光、阶段名流光、打字光标、呼吸圆点、开场锁定弹跳等。
- **减少动态效果**：系统开启时，CSS 动画与过渡统一压缩，`MotionConfig reducedMotion="user"` 处理 motion 动画；开场改为只淡入淡出。

## 4. 基础组件

| 原型组件 | 用途 | 接入时 |
| --- | --- | --- |
| `Button` | primary（墨色）、secondary、ghost、danger 四级 | 改写 `components/ui/button.tsx` 的变体样式 |
| `IconButton`、`Tooltip` | 纯图标按钮与悬停提示 | 对应 `components/Hint.tsx` 与 tooltip |
| `Dialog*` | 对话框外壳：图标标题区、底部操作栏、请求中禁止关闭 | 改写 `components/ui/dialog.tsx` |
| `TextField` | 带字数统计与错误提示的输入框 | 基于 `input` / `textarea` / `label` |
| `Segmented` | 分段选择，选中块滑动 | 基于 ObsidianUI `toggle-group` |
| `Switch`、`Slider` | 开关、滑杆 | 对应已有的 `switch`、`slider` |
| `NumberField` | 数值输入：前缀、单位，失焦或回车时按范围钳制 | 新增 |
| `SelectMenu` | 下拉选择，可自定义选项预览 | 对应 `select` 或 `command` |
| `Section`、`Row` | 属性面板的分组与行 | 新增 |
| `Skeleton`、`Spinner`、`PulseDot` | 骨架、加载圈、呼吸圆点（未保存提示） | 对应已有组件，换样式 |

这些组件都是用 Radix 原语加令牌重写的，与现有的 shadcn / ObsidianUI 接口不完全一致。接入时建议**保留现有组件的接口，只替换样式**，这样业务组件的改动最小。

## 5. 页面规格

### 5.1 导航栏（`Rail.tsx`）

- **尺寸与布局**：宽 72px，图标下方始终显示文字；顶部是标志，中间是「主页 / 模板编辑 / AI 字效 / 字体库」，底部是设置与版本号。
- **选中态**：图标从线性换成双色，强调色底在项目之间滑动。
- **未保存提示**：正在编辑的模板有未保存修改时，「模板编辑」图标上显示呼吸橙点；悬停提示写明模板名与保存状态。
- **与现在的差异**：去掉了「收起侧边栏」和「主题切换」，因为只做一套主题；窄屏也保持 72px，不再单独收起。

### 5.2 启动开场（`Splash.tsx`）

主题是「三段素材，混剪成片」，总长约 3.5 秒，加 0.65 秒转场：

1. **入轨**：时间线标尺展开；Intelligent / Mix / Video 三段素材块从左、上、右飞入，吸附成一条轨道。
2. **渲染**：播放头从左扫到右，扫过的素材擦除成文字；刚被扫到的字母先滚动几个宽度相近的字符，再锁定并轻轻一弹；「Mix」锁定时闪一下强调色光。
3. **落版**：标志的底板浮现，三条素材从两侧滑入，合成播放键；下方是「智能混剪工作台」，底部是版本号。
4. **转场**：背景淡出，开场里的标志飞到导航栏标志的位置，完成交接。

开场还有三条规则：

- **与服务启动并行**：开场和内置服务启动同时进行。播完时服务还在启动，就停在落版画面并显示启动进度；启动失败则直接淡出，露出失败页。
- **可以跳过**：点击或按任意键跳过；减少动态效果时只淡入淡出。
- **对应现有代码**：`App.tsx` 里的 `readSettings` / `start_backend` 流程。

### 5.3 内置服务启动页（`BootScreen.tsx`）

- **启动中**：标志、说明文字和不确定进度条。
- **失败**：显示原始诊断信息，并提供「复制诊断信息」。

### 5.4 主页（`home/`）

- **页头**：标题「开始创作」，右侧是服务状态胶囊。胶囊根据云端模板能否读取来推断状态（已连接 / 连接中 / 连接失败），点击打开设置，不额外发请求。
- **开始创作**：三张入口卡片，构图统一为「左侧图标、标题与说明 + 右上标签 + 右下靛蓝画面」，悬停时插图有轻微动作。
  - **云端**：团队头像，三张竖屏画面扇形展开。
  - **本地**：桌面小窗口里排着三个缩略图。浏览器环境下置灰，并标注「仅桌面端」。
  - **AI 字效**：提示词标签带输入光标，悬停时提示词逐字重打，一道光泽扫过字效画面。
- **我的模板**：云端在上、本地在下，各自加载、各自重试；有骨架屏、失败提示、空状态（引导新建），浏览器里显示「本地模板需要桌面客户端」。
- **模板卡片**：
  - **封面**：竖版，按模板 ID 生成固定配色与四种花字写法，排版模板自身的标题与字幕。
  - **悬停**：封面在指针下方「下陷」（参考 bencho 的 Tilt card）。右上角依次弹出「打开 / 复制 ID / 删除」三个圆形按钮。
  - **状态胶囊**：左上角在「图层数 / 编辑中 / 未保存」之间平滑变形。
  - **无效模板**：显示警示斜纹，只能删除。
- **对话框**：
  - **新建**：名称必填，最多 100 字；说明选填，最多 1000 字。脚注说明「进入编辑器后点保存才会写入」。
  - **删除**：显示缩略封面、所属库与完整 ID；删除中不能关闭，失败后原地重试。
- **与现在的差异**：卡片上的模板 ID 只显示前 8 位并提供复制，完整 ID 在删除确认里显示；现在的约定是卡片显示完整 `styleid`。

### 5.5 模板编辑（`editor/`）

布局分顶栏和三栏：

| 区域 | 内容 |
| --- | --- |
| 顶栏 | 返回；模板名与说明点击即可修改（回车或失焦保存，Esc 放弃）；云端或本地标识；保存状态；保存按钮，Ctrl/⌘ + S 也能保存 |
| 左栏 · 素材（284px） | 「特效 / AI 资产」切换；特效分「文字：花字 / 气泡 / 入场 / 出场 / 循环」和「画面：滤镜 / 特效 / 转场」两组；可搜索；文字类可选「应用到」标题或字幕；每类素材都有自己的预览缩略图，已应用的带勾，点击即应用并选中对应对象 |
| 中 · 预览 | 点阵画布上居中一帧画面；点画面里的文字即选中，显示选框、控制点与对象名标签，点空白处取消选择；下方是播放条（空格播放 / 暂停、时间、画布尺寸、预览视频） |
| 中 · 时间轴 | 标题、字幕、气泡、画面特效、滤镜、视频共六条轨道；片段平时用同色系淡色，选中后变实色；转场作为标签放在视频拼接处，没有转场的拼接处悬停时显示「+」；点击或拖动标尺定位 |
| 右栏 · 属性（300px） | 没有选中时是按「文字 / 画面 / 转场」分组的对象列表；选中后是对应设置（见下） |

右栏的属性面板按对象类型分三种：

- **文字对象**：分「外观 / 时间 / 关键词」三个页签；字幕没有「时间」，气泡没有「关键词」。
  - **外观**：示例文字、花字（带缩略预览，点「更换」跳到左栏对应分类）、字体、字号（滑杆加数值）、中心点 X / Y、入场 / 出场 / 循环动画，循环与入出场互斥并有说明；底部是「重置文字样式」。
- **画面特效与滤镜**：当前效果、更换入口、时间设置与移除。
- **转场**：启用开关（关闭后其余设置变灰但保留）、顺序 / 随机、统一时长、候选列表、每个连接处实际使用的转场。

**选中联动**：画面、时间轴、对象列表三处选中的是同一个对象。

**还没有设计的部分**：

- AI 资产片段的摆放与设置（原型里点击只弹提示）；
- 更换预览视频的弹窗；
- 离开编辑页时的未保存提示；
- 在画面上拖动文字、在时间轴上拖动片段。

### 5.6 AI 字效（`effects/`）

布局是「会话 | 对话 | 预览 | 参数」四栏，排版参考 Vercel AI Elements、v0 与 Claude。窗口窄于 1440px 时，会话列表默认收起。

- **会话列表**：
  - 顶部「新对话」，按今天 / 昨天 / 更早分组，一行一个标题。
  - 状态点只在需要注意时显示：生成中转圈，等待补充为橙点，失败为红点；悬停时状态点换成删除按钮。
  - 正在清理的会话变灰、不能打开，行尾常驻「重试删除」。
- **新对话**：标题、居中的大输入框与一排灵感提示。发出第一条后，输入框滑到底部（`layoutId` 动画）。
- **用户消息**：右侧浅灰气泡，参考图在上方，不显示时间。
- **助手消息**：
  - 不加头像。
  - **处理过程**：一行灰字。生成中显示当前阶段（文字流光）与实时计时，并自动展开；完成后收起成「已完成 · 用时 N 秒」。
  - **阶段与工具调用**：展开后是一条竖线串起的阶段列表；工具调用默认只显示次数，展开后每次调用一行（工具名、层级、完成 / 出错），再点开是格式化后的参数。
  - **等待补充、失败**：用左侧色条标注，失败时提供「重新生成」。
  - **版本卡片**：整卡点击即预览；显示版本号、「最新」、来源与时间；右侧是「代码」和「保存到资产」（可改名称与分类）。查看代码时展开在卡片下方，带行号与复制。
  - **追问建议**：最新版本下方的圆角提示，点击填入输入框。
- **输入区**：
  - 最多 4 张参考图，可以点击、拖入或粘贴；拖入时整块显示投放提示。
  - 画布胶囊（竖屏 / 横屏 / 方形、帧率、预览时长）只在新对话里可改。
  - Enter 发送；生成中发送键变成停止键。
  - 不能发送时，在输入框上方用一行字说明原因：生成中、参数未保存、查看历史版本、会话清理中。
- **预览**：
  - 画框按画布比例适配（横竖都能完整显示）。
  - 背景可选深色棋盘（默认，浅色文字也看得清）、浅色棋盘或背景视频。
  - 查看历史版本时顶部提示「只读」，并提供「返回最新」；还没有版本时显示同比例的虚线空画框。
- **参数**：
  - 按「文字 / 效果 / 动画与位置」分组，改动实时预览。
  - 底部是「撤销 / 保存为新版本」。
  - 生成中或查看历史版本时只读，并在顶部说明原因。

### 5.7 设置（`SettingsDialog.tsx`）

- **服务地址**：
  - 留空表示使用默认的 `http://localhost:20070`。
  - 只接受不带账号密码的 http(s) 地址，末尾斜杠会去掉。
  - 可以「测试连接」，结果显示在输入框下方。
  - 「恢复默认」一键清空。
- **关于**：标志、版本号、运行环境；构建校验码可点击复制，用于反馈问题。
- **保存**：只有地址真正改变时才能保存；保存时提示「已打开的 AI 字效会话需要重启客户端后切换」。
- **入口**：导航栏的「设置」，以及主页右上角的服务状态胶囊。
- **与现在的差异**：现在的通用设置还有「本地模板保存路径」（仅桌面端），Debug 模式下还有各模块的配置表单。原型按「只有一个地址」设计，这两项是否保留见第 7 节。

### 5.8 字体库

本轮没有重新设计。接入时先沿用现有页面（`features/fonts/FontLibrary.tsx`），把配色和控件换成新的令牌与基础组件。

## 6. 接入指南

### 6.1 原型与正式代码的对应关系

| 原型 | 正式代码 | 说明 |
| --- | --- | --- |
| `App.tsx`、`Splash.tsx`、`BootScreen.tsx` | `src/App.tsx` | 开场与 `start_backend` 并行；导航栏标志加 `data-splash-target` 作为交接落点 |
| `Rail.tsx` | `src/pages/HomePage.tsx` 的侧栏 | 未保存圆点需要 `TemplateWorkspace` 把 dirty 状态提到页面层 |
| `home/*` | `features/templates/TemplateHome.tsx` | 列表、重试、删除逻辑沿用 `useTemplateCollection`、`listTemplates`、`deleteTemplate` |
| `editor/EditorPage.tsx` | `features/templates/TemplateWorkspace.tsx` | 草稿、保存保护、名称说明的行内编辑沿用现有逻辑 |
| `editor/Stage.tsx` | `TemplatePreview.tsx`、`SpriteOverlay.tsx` | 画面换成 SDK iframe；画面上的选框需要 SDK 提供对象区域，做不到时先只在时间轴和列表里选中 |
| `editor/Timeline.tsx` | `PreviewTimeline.tsx` | 现在用的是 `@xzdarcy/react-timeline-editor`，要决定是改样式，还是按原型自己实现（转场放在拼接处） |
| `editor/AssetPanel.tsx` | `EffectAssets.tsx`、`AssetBrowser.tsx`、`SpritePanels.tsx`、`SpriteCover.tsx` | 缩略图换成真实封面；AI 资产沿用 `SpriteCover` 定格封面 |
| `editor/Inspector.tsx`、`TextInspector.tsx` | `TemplateInspector.tsx`、`EffectEditor.tsx`、`TrackTiming.tsx`、`SpriteClipInspector.tsx` | 时间、关键词、转场字段与校验规则不变 |
| `effects/EffectsPage.tsx` | `RemotionWorkspace.tsx`、`useTemplateSession.ts` | 锁定原因与现有的互斥规则一一对应 |
| `effects/SessionList.tsx` | `HistorySidebar.tsx`、`localSessions.ts` | 会话来自本地库，删除与重试规则不变 |
| `effects/Messages.tsx` | `ChatPanel.tsx`、`TaskStatus.tsx`、`LoopRounds.tsx`、`VersionCard.tsx`、`CodeBlock.tsx` | 阶段名取自公开阶段白名单；工具调用来自 `job.round`；诊断沿用 diagnostics 接口 |
| `effects/Composer.tsx` | `ChatPanel.tsx`、`DurationPicker.tsx` | 参考图规则（4 张、10 MiB、格式）与画布边界不变 |
| `effects/PreviewPanel.tsx` | `PreviewPanel.tsx`（隔离播放器） | 棋盘背景放在透明播放器下层；背景视频沿用现有直链逻辑 |
| `effects/ParamsPanel.tsx` | `ParametersPanel.tsx`、`components/ParameterControl.tsx`、`features/fonts/FontPicker.tsx` | 控件由参数 Schema 生成；字体选择沿用 `FontPicker` |
| `SettingsDialog.tsx` | `features/settings/SettingsDialog.tsx`、`PluginSettings.tsx` | 测试连接可请求现有的 `GET /`；保存仍写 `$client.api_url` |

### 6.2 建议的接入顺序

1. **基础层**：把 `design.css` 的令牌并入 `src/styles/globals.css`；按第 4 节改写 shadcn / ObsidianUI 组件的样式；引入 Geist 与 Solar 图标。
2. **框架**：导航栏、开场、内置服务启动页、设置。
3. **主页**。
4. **AI 字效**：业务逻辑集中在 `useTemplateSession`，替换界面风险较小。
5. **模板编辑**：最复杂，需要先决定时间轴方案，以及画面选框是否可行。
6. **收尾**：删除沙盒，更新文档与教程截图。

### 6.3 需要同步修改的项目约定

- **`AGENTS.md`**：现在规定了「浅色与深色两套主题」「主色为中性近黑 / 近白，不使用额外品牌强调色」「侧栏底部 ThemeToggle」。新设计是冷白单主题加靛蓝强调色，需要改写这几条；深色主题若保留，用石墨候选。
- **图标**：从 Lucide 换成 Solar 后，删除 `lucide-react` 依赖，并在 `public/THIRD_PARTY_NOTICES.txt` 补充 Solar 的 CC BY 4.0 署名。
- **字体**：Geist 是 SIL OFL 1.1，在第三方声明里补充。
- **教程**：`docs/tutorial` 的截图需要重新截取。
- **测试**：按 `AGENTS.md` 的「Feature 测试约束」补上行为测试。原型本身没有测试。

### 6.4 原型里模拟或简化的部分

接入时这些都要换成真实实现：

- 所有数据（`mock.ts`、`editor/model.ts`、`effects/model.ts`）与定时器模拟的请求。
- **预览画面**：模板编辑的 SDK 画面、AI 字效的 Remotion 播放器，原型里都是 CSS 绘制的示意。
- **缩略图**：素材缩略图、模板封面的花字写法，原型里按编号生成。
- **AI 字效**：生成过程与生成结果（按提示词里的颜色词换色）、版本源码、追问建议的内容。
- **设置**：测试连接的结果，原型里按地址判断（本机与局域网视为成功）。

## 7. 待决定的问题

| 问题 | 说明 |
| --- | --- |
| 深色主题 | 只做冷白一套，还是把石墨候选做成第二套 |
| 模板 ID | 卡片上显示前 8 位加复制，还是保持完整 ID |
| 开场频率 | 每次启动都播，还是只在首次启动或版本更新后播完整版 |
| 开场标语 | 现在是「智能混剪工作台」 |
| 时间轴 | 改 `react-timeline-editor` 的样式，还是按原型自己实现 |
| 画面选框 | SDK 能否提供对象区域；做不到时退回只在时间轴和列表里选中 |
| AI 字效布局 | 四栏（现在），还是预览在上、参数在下 |
| 工具调用 | 对运营同事是否只在 Debug 模式下显示 |
| 追问建议 | 固定几条，还是按版本分类或由模型给出 |
| 设置项 | 「本地模板保存路径」与 Debug 模块设置是否保留，放在哪里 |
| 字体库 | 本轮没有设计 |

## 8. 参考与第三方

- **配色**：Linear 的界面重做说明（<https://linear.app/now/how-we-redesigned-the-linear-ui>）、Vercel Geist 设计规范（<https://vercel.com/design.md>）、Figma 插件颜色变量（<https://developers.figma.com/docs/plugins/css-variables>）。
- **编辑器**：Figma UI3 设计复盘（<https://www.figma.com/blog/our-approach-to-designing-ui3/>）、Adobe Premiere iOS 设计复盘（<https://adobe.design/ideas/behind-the-design-adobe-premiere-on-ios>）、Remotion Editor Starter（<https://www.remotion.dev/docs/editor-starter>）。
- **AI 对话**：Vercel AI Elements（<https://ai-sdk.dev/elements>）、ChatGPT 输入框拆解（<https://aiuxplayground.com/teardowns/chatgpt/composer>）、AI UX 模式合集（<https://www.lazarev.agency/articles/ai-ux-patterns>）。
- **开场动效**：Imaginar 的 Dufry 字标动画（<https://imaginar.co.uk/projects/dufry-animated-logo-kinetic-typography-motion-design/>）、Framer Text Scramble（<https://www.framer.com/marketplace/components/text-scramble/>）。
- **交互组件**：bencho（<https://github.com/cabralorenzo/bencho>，MIT）。模板卡片的下陷倾斜、操作按钮弹出、状态胶囊参考了它的 Tilt card、Action node、Dynamic island，用 motion 重新实现，没有复制代码。
- **图标**：Solar（480 Design，CC BY 4.0）。
- **字体**：Geist / Geist Mono（SIL OFL 1.1，经 Fontsource 引入）。
