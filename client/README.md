# IntelligentMixVideo Client

基于 Tauri 2、React、TypeScript、Tailwind CSS 4 和 shadcn/ui。
模板预览继续使用阿里云 AliyunTimelinePlayer 5.2.2；Windows 安装包通过内置回环 HTTP 服务加载页面，使 SDK 识别到 `localhost`。
客户端与服务端使用同一项目版本；发版前统一更新并提交清单和锁文件，操作见[根目录发版说明](../README.md#tag-发版)。
界面直接显示这个版本：窗口标题（浏览器网页标题与桌面原生标题栏）是「IntelligentMixVideo - v版本号」，设置对话框左侧导航栏的左下角也显示版本号，其下一行是构建校验码（构建时从 git 读取的 7 位短提交哈希，工作区有未提交改动时带 `-dirty`，可选中复制，用来核对运行的是哪一版代码；可用环境变量 `IMV_COMMIT` 显式指定，没有 `.git` 的构建环境退到 `GITHUB_SHA`，都没有时显示 unknown）。版本由 Vite 构建时从 `package.json` 注入，不在界面里另写一处，所以发版同步版本后重新构建即可。

## 客户端更新

设置「通用」展示当前界面版本、原生版本和更新状态。正式客户端在启动后、打开设置时自动检查，点击「一键更新」才下载。兼容并经过验证的界面版本保持窗口打开、短暂刷新；涉及原生变化时显示重启提示并自动安装重启。Linux deb 的完整升级提供下载链接。Debug 和未配置更新公钥的构建不启用更新。

检查更新同时请求以下全部来源并取最高的有效版本；下载按 `gh-proxy.org` → `ghfast.top` → `ghproxy.net` → `gpp.hydroroll.team` → `ghproxy.hydroroll.team` → GitHub 直连顺序尝试，所有来源的清单及自动下载内容都必须通过 Rust 签名与完整性校验；deb 手动下载后，先按界面显示的签名清单摘要核对文件，再安装。应用前等待未保存内容和后台任务结束（等待说明列出阻止更新的会话），可以取消等待；更新后恢复工作区与已保存内容，预览保持暂停。热更新失败自动回退，多实例同时运行时提示关闭其他实例。

更新实现、签名变量/Secret、兼容验证记录及首次安装要求见[客户端更新方案](../docs/client-update-design.md)。发布工具与更新核心均使用 Rust，完整安装复用官方 Tauri 更新插件。测试命令（仓库根目录）：

```sh
cargo test --manifest-path client/src-tauri/Cargo.toml --lib --bin client --locked -- --test-threads=1
cargo test --manifest-path client/src-tauri/Cargo.toml -p imv-update-build --locked
```

第二条包含调用 Bun/Tauri CLI 的签名集成测试，须先在 `client/` 冻结安装依赖。Rust 最低版本为 1.90。

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

API 使用其他端口时同步修改此地址，填写服务根地址。
客户端配置保存 API 与示例视频地址，数据库连接和密码只放服务端。修改后重启 Vite 并刷新页面；生产使用需重新构建。
避免在 `.env.local` 中重复设置 `VITE_API_URL`，否则会覆盖 `.env` 中的值。

已有远程后端时，直接填写该服务地址，无需再启动本机 MySQL 与 API。浏览器开发版不能读取桌面客户端保存的设置，需在 `client/.env` 中单独配置同一地址；浏览器设置页的临时修改刷新后失效。开发页面请使用 `http://localhost:1420`（或实际启动端口），以匹配后端允许的 localhost 来源。

首页「设置」在普通 Client 中仅保留「通用」页的后端地址、本地模板路径与环境信息，不请求模块目录。`IMV_DEBUG=true` 继续提供 Remotion Agent、上海 IMS、切片、ASR 与启动端口等完整模块设置。模块字段来自后端目录，值保存在当前客户端，和构建环境变量分开；桌面本地 JSON 包含明文密钥，浏览器仅当前页面内存保存。已有模块配置保留，实际请求的配置使用规则不变；未保存时沿用服务端环境默认值。协议、范围与联调步骤见 [模块设置说明](src/features/settings/README.md)。

```sh
bun run build
bun run tauri dev
bun run tauri build
```

Windows 安装包仅在本机回环地址绑定系统分配的空闲端口，窗口访问 `http://localhost:<端口>`；退出程序后释放，无需额外启动 Python 或 Vite。API 允许该 localhost 来源的动态端口。
开发模式仍使用 Vite，macOS / Linux 保留原有 Tauri 加载方式。预览仍需要联网下载 SDK、字体和公开视频；空 License 的 localhost 预览保留 SDK 水印。
macOS 通过 `src-tauri/Info.plist` 的 `NSAllowsArbitraryLoadsInWebContent` 允许 WebView 访问用户设置的 HTTP 后端；Tauri 自动将该配置合并到应用包，修改后重新打包生效。
前端构建不代表桌面打包或跨平台预览验证通过；各平台仍须检查真实 WebView、首帧和播放。

## 工作室前端

正式客户端已采用 [PR #196](https://github.com/HsiangNianian/IntelligentMixVideo/pull/196)（`44d5bff`）的工作室设计。运行 `bun run dev` 后直接打开 <http://localhost:1420/>，桌面端运行 `bun run tauri dev`。独立 Demo 入口、模拟数据和原型调试面板已移除。

- `src/components/studio/`：导航、启动动画、模板卡片和对话框等视图组件。
- `src/styles/studio.css`：冷白与深灰两套主题、Geist 字体和靛蓝强调色。
- `src/features/`：真实模板读写、SDK 播放器、AI 会话、参数与资产保存，沿用现有接口。

模板编辑以素材、预览/时间轴、对象参数三栏排布；未打开模板时，提示和「前往主页」按钮在侧栏右侧可用区域水平、垂直居中。素材切换「特效素材 / AI 资产」，Ctrl/Cmd+S 保存当前编辑页。AI 字效采用可收起的历史列表、聊天、预览和模板参数四栏；右上角「保存资产」保存当前预览版本，按该版本分类发布，有未保存参数时需先保存配置或撤销；新建会话保留在历史栏。窄窗口切换聊天/预览，空画布默认竖屏 9:16，已有版本按真实尺寸展示。页面切换保留未保存草稿与会话。

主页「我的模板」标题栏提供「全部模板 / 云端模板 / 本地模板」页签，默认合并显示可用库的模板，点击页签筛选同一卡片网格，数量与刷新操作跟随当前范围。卡片以云端 / 本地图标区分来源，打开和删除始终操作原库；两库独立读取，一侧失败不隐藏另一侧。浏览器的本地模板说明仅在「本地模板」页签显示。

本地模板需要 Tauri 桌面端；云端模板与 AI 生成需要可用的后端服务。界面显示实际连接和失败状态。图标与字体出处见 `public/THIRD_PARTY_NOTICES.txt`。

工作室界面保留客户端更新与中英双语模板支持。更新后恢复原工作区和已保存模板；新建、删除等业务弹窗打开时继续等待，关闭后才允许应用更新。

左下角「设置」上方的「主题」按钮切换亮色与暗色，太阳 / 月亮图标表示当前模式；主页、模板编辑、AI 字效、字体库和弹窗同步生效。选择沿用 `localStorage` 的 `imv.theme` 保存，刷新或重启后在首帧前恢复；尚未选择时按系统明暗偏好初始化。存储不可用时仍可切换，但只对当前窗口生效。

## 示例视频配置

在 `client/.env` 中设置：

```dotenv
VITE_PREVIEW_VIDEO_1_URL=https://your-domain.example/preview.mp4
VITE_PREVIEW_VIDEO_2_URL=https://your-domain.example/preview-2.mp4
VITE_PREVIEW_VIDEO_3_URL=https://your-domain.example/preview-3.mp4
```

以上配置分别对应视频 1、视频 2、视频 3，均支持 HTTP(S) 直链和 public 资源路径。例如将视频放入 `client/public/preview-2.mp4`，填写 `VITE_PREVIEW_VIDEO_2_URL=/preview-2.mp4`。
每个地址未配置或留空时使用对应的内置示例。默认来源与弹窗填写的 URL 均由工作区读取真实时长和尺寸，读取失败可以重试，读取完成前不计算预览时间。工作区的预览视频与模板独立，保存模板只保存对象配置和时间规则。

时间输入和开始、持续方式的选择立即更新内存草稿及预览；点击「保存模板」才写入存储。空值或非法时间会提示错误，修正后继续编辑。

时间默认值按 IMS 接口配置：[转场默认 1 秒，文字入场与出场各 0.5 秒](https://help.aliyun.com/zh/ims/developer-reference/effect-configuration-description)；滤镜和 VFX 默认从视频开始持续到结束。切换固定时长时按当前视频剩余区间填写；用户已经设置的时长继续保留。

新模板自动创建中文标题和中文字幕各一个。创建和更新时，前端、云端和本地保存都检查中文对象各一个；缺少任意一个便拒绝写入并保留已有记录。通过左侧「特效资产」选择样式，右侧编辑对象参数。
修改后重启 `bun run dev`；生产构建需要重新执行 `bun run build`。
若 `.env.local` 或对应模式的环境文件设置了同名变量，会按 Vite 的优先级覆盖 `.env`。

预览默认使用三个视频，各取前十秒，读取真实媒体信息后播放。点击「加载预览视频」打开弹窗，分别为视频 1、视频 2、视频 3 填写 URL 或点击「本地文件」选择本机视频（每个不超过 512 MiB，可与 URL 混用）；三个来源全部读取成功且时间校验通过后一起更新，失败时标明视频编号并保留原有来源，取消或关闭弹窗会终止读取。每个视频不足十秒时使用完整区间；本地文件不截取，按完整时长排列。画布和 SDK 场景使用视频 1 的原始宽高，字号按画布像素计算；标题和字幕保留原文。预览按可用宽度和窗口高度的 48% 等比例显示。SDK 在独立 iframe 中加载，更换视频后重新初始化，卸载时释放。视频顺序固定，更换来源后重新计算转场时间，其他对象保留规则。远程直链须允许浏览器跨域读取。本地文件只保存在本机、不上传服务端：桌面客户端把文件复制到应用数据目录的 `data/preview_videos/`（Tauri `preview_video_*` 命令），重启后自动读回并优先于默认地址使用，在弹窗中移除文件并加载即删除本机副本、恢复默认地址；浏览器只在页面内存中使用，刷新后恢复默认。

## 模板行为

标题、字幕设置分别提供「中英双语」复选框。开启后英文作为独立对象出现在画面和时间轴，可单独调整字体、字号、位置或绑定 Remotion 字效；英文标题时间跟随中文标题，英文字幕与中文切片同时出现和消失。示例文字可以编辑，成片由服务端翻译实际文案。关闭后隐藏英文并停止输出，配置和字效绑定随模板保存，重新开启即可恢复；从未开启不创建英文配置。普通英文过长时成片自动缩小字号、尽量保持单行；[Web SDK 暂不支持 AutoScale](https://help.aliyun.com/zh/ims/developer-reference/timeline-support-guide-for-editing-and-previewing-the-component-web-sdk)，预览仍使用设置的字号。Remotion 字效沿用资产的文字长度限制和排版预检。

标题和底部字幕分别提供字体选择：阿里巴巴普惠体、方正仿宋简体、方正黑体简体、方正楷体简体、方正书宋简体、宋体、楷体、微软雅黑、站酷快乐体、思源黑体、思源宋体。字体随各自的 `tracks[].editor.titleFont` / `subtitleFont` 保存为 IMS 标识；预览将 `Source Han Sans CN` 转为 `Source Han Sans CN Normal`，将 `Source Han Serif CN` 转为 `思源宋体`。云端与本地使用同一目录，旧模板缺少字段时采用 `Alibaba PuHuiTi`；重置当前对象时恢复该字体，其他对象的选择保留。共享目录维护在 `server/src/server/template/fonts.json`，`value` 是保存及合成标识，`preview` 是 SDK 标识。

字体下拉框与 Remotion 共用 `features/fonts/FontSelect.tsx` 和 `FontName.tsx`：选项及当前值以对应字体的字形展示名称，下面显示保存标识，支持搜索、键盘选择与主题颜色。IMS 的十一张透明 PNG 保存在 `features/templates/font-previews/`，随客户端打包，浏览字体选项无需下载完整字体。图片由固定 SDK 5.2.2 的对应字体绘制，Remotion 继续使用实时字体库的预览地址和上传功能。

字体预览维护与真实浏览器测试使用 ego-browser。启动 Vite（以下默认 `http://localhost:18766`），测试时还需将 `VITE_API_URL` 指向独立测试后端。通过 `ego-browser nodejs` 在同一个任务中执行下面的代码，`root` 填写本地仓库绝对路径；更新预览图片时调用 `generateFontPreviews`，仅测试时省略该调用。生成函数读取当前固定 SDK 的字体 URL，用浏览器 `FontFace` 和 Canvas 绘制名称，不保存完整字体文件。

```js
const root = "/absolute/path/to/IntelligentMixVideo";
const task = await taskSpace("字体预览验证");
const page = task.page("p1");
const { generateFontPreviews } = await import(root + "/client/scripts/generate-font-previews.mjs");
await generateFontPreviews(root, page);
const { testFontSelect } = await import(root + "/client/tests/font-select.browser.mjs");
await testFontSelect(page);
await task.finish({ keep: [] });
```

浏览器回归挂载实际字体组件、访问测试后端，检查十一张预览图片的解码和透明文字、搜索与键盘选择、标题和字幕的独立值、主题着色及 Remotion 目录与上传入口；不创建模板或上传字体。生产构建同时检查测试组件的 TypeScript 类型。

侧栏固定 72px，入口为「主页」「模板编辑」「AI 字效」「字体库」，使用 Solar 图标并保留文字及无障碍名称。

启动默认进入「主页」。顶部「开始创作」提供「新建云端模板」「新建本地模板」和跳转 AI 字效的「AI 生成字效」三个快捷入口；下方「我的模板」通过「全部模板 / 云端模板 / 本地模板」页签筛选统一网格，主页不展示更新日志，各页头均不显示当前时间。标题栏显示当前范围的模板数量与刷新入口，模板以按宽度自动排列的画廊卡片展示：封面用模板 ID 生成稳定配色，并按模板自身的标题、字幕文字排版，角标显示对象数；名称、更新日期与说明位于下方，完整模板 UUID 可通过卡片操作复制，并在删除确认框内展示，点击卡片进入对应环境的模板库，读取最新详情后编辑。每张卡片的删除操作会打开删除确认框，显示模板名称与 ID；确认后删除，取消不修改，失败可重试。删除当前打开的模板会清空编辑区；删除正在读取的模板会取消对应请求并清除加载与错误状态，迟到响应不再恢复模板或显示错误，其他模板的草稿和读取保留。整页随内容滚动；新建时填写名称与可选描述后进入编辑，点击保存或修改名称、描述后失焦时创建存储记录。两个列表独立读取和重试；浏览器提示本地模板需要桌面客户端，并禁用本地新建按钮。名称去除首尾空白后必填，最多 100 个字符，描述最多 1000 个字符。返回主页会刷新列表，已打开的模板库与字效工作区保留草稿、播放器和订阅。界面复用 shadcn/ui 的 Button、Dialog 和表单组件。

模板库顶部展示环境、模板名称、描述和保存状态，并提供保存按钮。点击名称或描述旁的铅笔进入编辑，点击其他地方时自动保存当前完整草稿；名称输入框支持回车保存，Escape 取消当前字段编辑。未修改不写入，名称不能留空，描述可以清空；失败保留输入，可修正后失焦或点击「保存模板」重试。已有模板沿用原 ID 和所属库。编辑区填满侧边导航之外的区域，采用三栏：左侧切换特效素材和 AI 资产，中间实时预览，右侧调整选中对象的参数。桌面布局高度随窗口调整，顶栏下方三栏各自滚动；窄屏按顺序排列。

桌面界面截图：[模板主页与 Style ID](../docs/images/template-management/home.png)、[删除确认](../docs/images/template-management/delete.png)、[名称编辑](../docs/images/template-management/edit.png)。截图使用独立测试模板；预览视频受本机媒体与网络环境限制，不代表播放验收。

右侧设置可通过右上角「×」关闭，关闭时保留对象与未保存参数。移除当前画面对象后自动关闭设置；关闭期间取消对象选中状态，桌面保留设置栏宽度，预览画面尺寸保持不变。选择画面对象或应用资产会重新打开对应设置，切换页面保留关闭状态。

转场条目通过效果选择框旁边的「×」删除，下拉菜单仅列出具体效果。删除后候选连续编号；删除全部转场后仍保留空转场 track 和添加入口。

IMS 转场时长输入超过 3 秒时立即设为 3 秒，低于 0.1 秒时立即设为 0.1 秒，输入框、滑块和全部 IMS 转场参数同步更新。清空时允许继续编辑，保存前须填写有效时长。Remotion 转场新增时使用资产自身时长，读取、编辑和保存保留其 `duration`；`null` 在预览与合成时采用资产时长。相邻片段过短时按可用长度计算显示区间，保存保留原有时长。

- 资产区按花字、气泡、滤镜、画面特效、转场、入场、出场和循环动画分类，支持名称与编号搜索、分批展示和目录封面预览；缺少封面时显示明确占位。搜索框按 Enter 保留当前编辑。
- 花字和动画通过「应用到」选择文字对象。标题、底部字幕各一个且不可删除，VFX 最多一个；选择样式时保留已有对象的文字、位置与时间。气泡、滤镜可添加多个。文字动画应用到最近选择的文字实例。两个视频交界处分别允许一个转场。
- 前端资产列表排除 `CS0003-000002`；花字下拉框仅在当前对象已使用它时保留该选项及勾选标记，清除或更换后无法重新选择。已有模板仍可读取、预览与保存。
- 视频下方的「画面对象与已添加特效」展示已有对象，点击对象打开右侧参数。添加效果统一通过左侧「特效资产」进行，移除对象后也通过资产重新添加，其他对象保持不变。
- 循环动画与入场、出场动画互斥，实际应用时按文字显示时间调整入场和出场时长。每次修改自动应用到 SDK，播放器从下到上叠加视频、滤镜、VFX 和文字。
- 画面下方使用 `@xzdarcy/react-timeline-editor`，从上到下排列标题、字幕、气泡、转场、VFX、滤镜和视频。三个视频各取前 10 秒（用户选择的本地文件使用完整时长），顺序固定，始终每个视频一条 track，视频片段不可拖动和调整长度。初始没有转场，转场共用「镜头转场」一行。其他对象通过右侧设置时间；字幕固定覆盖全程。标题、气泡、VFX、滤镜支持秒数或百分比起点、固定秒数或持续到结束。实例上限为 100。SDK 驱动游标，拖动游标暂停并定位，确认定位后继续播放。方向键移动 0.1 秒，Home/End 定位首尾，修改效果后回到开头。
- 缩略图由独立视频元素提取，最多缓存 48 张，卸载或素材变化时取消任务。普通对象保存 `id`、`target`、`start_mode`、`start`、`duration` 和 `editor`；`start_mode` 为 `seconds` 或 `percent`，百分比范围为 0 至小于 100，`duration: null` 表示持续到结束。转场保存 `id`、`target`、`transition_boundary`、`duration` 和 `editor`，禁止设置 `start`、`start_mode`。云端和桌面本地按相同规则校验，模板不保存 `media`。`resolveTemplate` 根据规则与真实片段生成总长、视频区间、对象区间和提示，预览、时间轴与右侧面板共用结果。预览按 30 FPS 计算帧区间；超过结尾的对象不显示，结束越界时截短，动画按可用帧数缩短并展示说明，帧数不足以表达所选动画时明确报错。
- 选中转场对象或空转场 track 时，右侧显示转场设置；默认及选择其他对象时隐藏。左侧与右侧「＋ 添加转场」均可添加效果，右侧展示全部候选并支持搜索、替换和移除。「使用转场」统一开关通过 `transition_enabled` 保存，默认开启，关闭时保留候选并停止预览及正式合成中的转场；空的「镜头转场」track 始终显示。IMS 统一持续时间为 0.1～3 秒，默认 1 秒，Remotion 使用自身时长。「转场选择方式」包括「顺序选择／随机选择」：开启转场且存在候选时，预览连接视频 1 → 2 和视频 2 → 3，只有一个候选时两处共用；有多个候选时，顺序选择列表前两项，随机从尚未选中的候选中选择，每项最多一次，修改持续时间和其他对象保留本次随机选择。转场按 `transition_boundary` 候选序号排序，删除后连续编号；IMS 与 Remotion 共用列表。`transition_mode=sequence` 在正式合成中按列表循环，`random` 对每个候选最多选择一次，候选用完后其余连接位置不应用转场，任务重试保持选择结果。`transition_duration_seconds` 为 IMS 统一时长。预览转场以视频连接位置为中心，总时长和字幕时间保持不变，保存时才写入模板。
- 居中预览按 30 FPS 计算转场区间；奇数帧时后半段多一帧。三个各 10 秒的视频使用 1 秒转场时，转场位于 9.5～10.5 秒和 19.5～20.5 秒，总长 30 秒。SDK 5.2.2 使用前一段末帧与后一段首帧的图片转场覆盖该区间，补帧位于视频上方、滤镜与文字下方；转场期间使用静帧，SDK 播放与时间轴共用区间。补帧按源地址和采样时间缓存，更换来源或卸载时释放，加载失败显示错误并允许重试。Remotion 转场在同一区间覆盖连续视频。
- 模板保存结构要求 `tracks` 数组，参数仅保存在 `tracks[].editor`。新建草稿自动包含标题和字幕，基础文字无需选择效果即可保存。参数面板使用选中对象的临时视图。云端与桌面保存、详情读取均校验完整规则；模板列表返回 `TemplateSummary`，无效记录显示 ID 与原因，使用同一垃圾桶入口经确认只删除选定记录。有效模板仍可使用。已有记录不自动转换；整个本地 JSON 文件无法解析时明确报错。列表消息与转场字段需要客户端、服务端一起升级。
- Remotion 片段使用 `target: remotion`，读取到 `Draft.clips`，保存时追加在 IMS 对象之后，共用 100 条上限。本地模板不支持。云端模板从资产目录添加，同一资产可重复摆放，画布宽、高、帧率必须一致。字效作用于当前标题或字幕（每个最多一个，替代对应 IMS 文字，时间跟随该文字）；文字没有显示区间时字效也隐藏。转场加入统一列表，预览覆盖居中转场区间；视频特效与 IMS VFX 共用一个名额，滤镜可多个。右侧设置参数与位置，字效可改作用对象，滤镜与视频特效可改开始时间。字效标在对应文字行，其他片段并入转场、VFX、滤镜行。片段与模板在同一次请求中保存。
- 「重置特效设置」清除当前文字对象的样式和动画，并将示例文字、字号、水平位置、垂直位置和入出场动画时长恢复为新建模板的默认值；其他对象和模板信息保持不变。

- 模板选择和新建均在主页完成；直接进入模板库且尚无草稿时，显示前往主页的入口。
- 当前环境由主页选择或新建的模板决定。连接失败、超时或服务端 5xx 时提示使用桌面本地环境；本地无需 Python 或 MySQL，但需使用桌面客户端。从主页选择其他模板或新建模板前，可保存到原环境、放弃修改或取消；读取目标失败保留原环境与草稿，并提供重试，两库不自动同步。
- 本地保存请求使用共享 Protobuf 消息，Rust 端解析后默认将模板保存在 Tauri 应用数据目录下的 `data/template/templates.json`；设置「通用」的「本地模板保存路径」可改为其他 `.json` 绝对路径，留空恢复默认，原文件不迁移；保存后立即使用新路径，已打开的旧库模板需回主页重新打开。macOS 为 `~/Library/Application Support/com.intelligentmixvideo.client/data/template/`，Windows 为 `%APPDATA%/com.intelligentmixvideo.client/data/template/`，Linux 为 `${XDG_DATA_HOME:-~/.local/share}/com.intelligentmixvideo.client/data/template/`。本地目录随首次读取自动创建，JSON 损坏时明确报错，不能当成空库覆盖。
- 离线仍可从内置目录选择效果并保存；SDK、字体与示例视频预览仍需联网。
- 标题、字幕、气泡独立设置文字、字号、位置、样式、入场/出场/循环动画及动画时长；字号支持 12～300 的整数，预览、云端和本地保存使用相同范围。底部字幕仅显示「外观与效果」「关键词设置」页签，其他对象保留适用的时间页签。
- 顶部标题和底部字幕对象可分别选择关键词局部加粗、斜体、下划线、删除线、颜色和字号；关键词设置页只显示这些样式选项。颜色选择器支持 `#RRGGBB`，关闭后保持文字原色，局部字号支持 12～300 的整数，关闭后沿用原文字号。标题预览未指定关键词时从示例标题首段连续文字选取前两个字，已有指定关键词在标题中首次出现时继续应用；正式合成仅使用 segmentation 返回的 `title_keyword`，缺失或为空时不强调关键词，不回退模板词或标题前两个字。字幕预览选取示例文字的首个词语，正式合成使用切片结果中的 `keyword`。选项保存在 `tracks[].editor` 并随模板返回。
- 画面滤镜、特效、转场和转场时长可选；预览不提交云端合成任务。
- 新模板首次保存创建记录，此后保存更新同一模板；环境、名称和描述跟随当前模板，保存包含全部效果配置。
- 切换前提供保存并切换、放弃修改、取消；失败保留草稿。刷新列表不会覆盖正在编辑的内容。
- 效果目录由 SDK 提供，动画来自静态 `motions.json`；服务端独立校验可信效果 ID。保存必须保留标题和字幕，允许不选择 IMS 效果，可同时添加 Remotion 资产。
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
    api.ts                         # 云端 Protobuf、本地 Tauri 模板操作和错误处理
    sdk.ts                         # SDK 加载、目录及类型边界
    timeline.ts                    # 配置到预览时间线的转换
    motions.json                   # 固定版本动画目录
  components/ui/                   # shadcn/ui 基础控件
  generated/imv/template/v1/       # Buf 生成的 Protobuf 消息类型
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
`preview-canvas.test.ts` 检查横屏、竖屏、方形和超宽视频的原始画布尺寸，以及文字参数和三个各十秒的默认片段。
画布的真实浏览器回归执行 `bun tests/preview-canvas.browser.mjs`，需要 Chrome、FFmpeg 和联网访问 SDK、字体。脚本自动启动独立 Vite 服务，生成本地视频，验证 Canvas 像素、横竖屏切换、实际播放、数字时间更新频率、窄屏比例、标题和字幕原文与卸载清理；不访问业务后端。中间文件保存在已忽略的 `node_modules/.cache/preview-canvas-tests/`，结束时关闭浏览器和测试服务。
`effect-tracks.test.ts` 覆盖独立对象增删、时间规则、视频替换、动画帧数、SDK 数据转换和序列化；`track-timing.test.tsx` 验证输入自动更新、单位换算、非法输入恢复和对象切换。前后端共同使用 `server/tests/template_timing_cases.json` 和 `server/tests/template_sequence_cases.json`，后者覆盖累计重叠、短视频、固定语音时间、转场缩短与缺失交界处；预期结果包含区间和提示，并检查计算过程保留原始规则。

服务端核心验证执行 `uv run --locked --project server pytest server/tests/test_template_tracks.py server/tests/test_template_protobuf.py server/tests/test_template_validation.py server/tests/test_video_composition_timeline.py`（仓库根目录）。现有文案合成按成片时长应用每个对象：标题使用请求文字，字幕及关键词与文案时间取交集，重复实例保留独立参数；转场在指定位置连接片段，音频总长保持不变。桌面存储验证执行 `cargo test --locked --manifest-path src-tauri/Cargo.toml --lib templates::tests`（client 目录），使用真实文件检查规则保存与失败保护。通过 `TMPDIR` 和 pytest 的 `--basetemp` 将中间文件指向已忽略的缓存目录。
`server/tests/test_template_tracks.py` 直接校验 Schema、默认参数、时间计算和序列化；`test_video_composition_timeline.py` 检查对象规则生成的合成时间线。
本地模板字段变化导致旧文件无法读取时，页面会提示删除旧模板文件，并显示完整路径及清除全部本地模板的影响。关闭客户端后删除提示中的文件，重新打开客户端即可创建新模板。
工作区测试使用真实表单、Radix 选择器和弹窗，覆盖核心编辑与草稿保护。Happy DOM 无法验证真实视频播放和 Tauri 原生行为。
Happy DOM 的小数 step 校验与浏览器不同，保存流程直接触发表单提交；浏览器原生表单约束仍需浏览器验证。
Windows 原生资源服务的回归测试位于 `src-tauri/src/localhost.rs`，执行 `cargo test --manifest-path src-tauri/Cargo.toml --lib --locked`，覆盖真实 HTTP 资源响应、查询参数、HEAD、错误主机与方法；原生检查 CI 同步执行。

云端模板通过上游 `text_target`／`transition_boundary` 保存 Remotion 文字及转场绑定，兼容旧 `businessTarget` 字段，不额外增加用途控件。旧用途读取与保存时不自动补新槽位，保留 IMS 与 Remotion 共存；右侧说明当前显示方式，用户显式重选作用对象后才采用新槽位。编辑预览与时间轴沿用 upstream/dev 的对象槽位流程，不调用业务输入接口、不等待动态绘制回执。详见 [Remotion 视频合成适配](../docs/remotion-video-composition.md)。
