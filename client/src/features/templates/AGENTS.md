# 模板编辑协作约定

适用于 `client/src/features/templates/`，补充根目录与 `client/` 的 `AGENTS.md`。

界面行为（主页、模板库三栏、资产、时间轴、转场面板、关键词、上下蒙版等）见 `client/README.md`「示例视频配置」「模板行为」「开发结构」；保存结构与服务端校验见 `server/src/server/template/AGENTS.md`；Remotion 资产接口见 `server/src/server/remotion_templates/AGENTS.md`；桌面预览媒体服务与 Windows localhost 页面服务见 `client/src-tauri/AGENTS.md`。

## 结构与草稿

- 模板 API 请求集中在 `api.ts`，SDK 加载与预览在 `sdk.ts`、`TemplatePreview.tsx` 等独立模块，纯展示组件不散落请求与错误处理。
- 侧栏入口是 `Rail.tsx` 的 `editor` 页「模板编辑」（Solar `clapperboard-edit`）；恢复工作区时旧值 `library` 仍映射到编辑页。
- 模板选择与新建只在主页：新建不调用 POST，模板库首次保存无 ID 创建、之后带原 ID 更新。切换或新建时有未保存修改，提供保存并切换 / 放弃修改 / 取消，失败保留草稿。工作区只读取目标详情，失败保留当前草稿并可重试，卸载时取消请求。
- 删除正在读取的模板时取消对应请求、清除加载与错误状态并忽略迟到回执；删除当前打开的模板清空编辑区；其他草稿与读取不受影响。
- 名称与描述失焦时走完整保存流程，未修改不写入，空白名称拒绝；名称回车保存、Escape 取消；时间输入框回车不触发保存。
- 对象时间与选项变化立即更新内存草稿与预览，保存模板时才写入；非法时间保留当前有效规则并提示，保存前再检查。
- 前端资产列表经 `isSelectableAsset` 排除 `CS0003-000002`：当前对象已选中它时保留选项与勾选，清除或更换后不能再选；完整效果目录保留它，供已有模板读取、预览与保存。
- 「重置特效设置」由 `resetTextTarget` 从 `defaultEditor` 恢复当前文字对象，不修改输入草稿或共享默认配置。

## 对象与时间

- 普通对象保存 `id`、`target`、`start_mode`、`start`、`duration`、`editor`；IMS 转场保存 `id`、`target`、`transition_boundary`、`duration`、`editor`，禁止 `start` 与 `start_mode`；实例最多 100 个（含 Remotion 片段）。
- 标题、气泡、VFX、滤镜支持 `seconds` 或 `percent` 起点（百分比小于 100），`duration: null` 表示持续到结束；字幕固定 `seconds`、0、null。标题、字幕、气泡字号是 12～300 的整数，与云端保存同一范围。循环动画与入出场动画互斥。
- 时间只由 `resolveTemplate`（服务端 `resolve_template`）计算：按模板规则与 `TimelineContext` 返回总长、视频区间、对象区间与说明，预览、时间轴、右侧面板共用。前后端共用 `server/tests/template_timing_cases.json`、`template_sequence_cases.json` 与 `transition_duration_cases.json`。
- 预览用 `centered`：视频连续排列，IMS 与 Remotion 转场围绕连接位置居中，奇数帧时后半段多一帧，总长不变；`sequence` 按帧扣除转场重叠；`fixed` 保留合成的素材时间与音频总长。转场最多用相邻片段各自长度的一半，不足 0.1 秒或缺少连接位置时不应用并说明原因。
- 结尾以外的对象不显示，结束越界时截短，动画按可用帧数缩短并说明，帧数不足时明确报错。

## 转场

- `transition_enabled` 默认 true，关闭时保留候选，预览与合成都不应用；「镜头转场」track 始终存在。开启同屏布局时预览不应用转场。
- `transition_boundary` 是 IMS 与 Remotion 共用的候选序号（1～100），删除后连续编号；`transition_duration_seconds` 是 IMS 统一时长，0.1～3 秒，输入越界立即钳制，空值或无效值阻止保存。
- 预览在两个连接位置各选一个效果：只有一个候选时两处共用；顺序方式取列表前两项；随机方式每次从完整候选列表取，允许重复，修改时长与其他对象时保留本次的随机结果。正式合成的选择规则见 `server/README.md`「模板接口」。
- Remotion 转场新增时把发布预览长度（`preview_frames / fps`）写入显式 `duration` 并一直保留；已有的空值以发布预览长度为默认值；显式时长优先，最终按相邻镜头的可用长度裁剪。

## 画布、预览视频与文字

- 画布与 SDK 场景固定为 1080×1920（`timeline.ts` 的 `PREVIEW_CANVAS`，与合成默认值、AI 资产画布一致），不随预览视频变化；视频按 Contain 放入，留白用 `Background/Blur`（Radius 0.1）填充，比例相同时不加模糊，转场补帧沿用所属视频的填充。
- `loadSDK` 创建播放器前把 SDK 内置 Konva 的全局 `pixelRatio` 固定为 1，画布按 1080×1920 绘制，不随屏幕缩放倍数放大。
- 客户端不往标题和字幕里插入换行，也不下载 IMS 测量字体：中文由 SDK 的 `AutoWrap`（`TextWidth: 1`）做与合成端相同的有界换行，英文用 `AutoScale`（`TextWidth: 0.9`；Web SDK 不支持缩放，面板有提示）。
- 预览视频：URL 来源各取前十秒（不足十秒用完整区间），本地文件与内置示例用完整时长（`previewVideos` 按 blob URL 或媒体服务地址区分）。三个来源的真实时长与尺寸全部读到、本地文件提交成功后才一起替换，再由工作区计算时间；失败保留原来源并标明视频编号，关闭或取消终止读取。视频信息独立于模板保存。
- 本地文件（`localVideos.ts`，每个不超过 512 MiB，不上传）：桌面经 Tauri `preview_video_list/write/remove` 存到 `data/preview_videos/`。写入与删除共用短暂重试的独占目录锁，数据文件唯一命名、元数据替换是提交点；任一槽位保存失败时已完成的槽位回滚（替换前先从媒体服务读回原视频，读不到就不开始提交），保存期间弹窗不可关闭；改用 URL 即删除本机副本。浏览器只用页面内存的 blob URL。
- 独立视频元素与缩略图（有界缓存）在完成、失败、取消与卸载时释放。

## 播放与降级

- 播放位置由前端持有：IMS 就绪后由 SDK 帧事件推进，降级后由基础视频推进，都不可用时由 `previewClock.ts` 按真实时间推进；三者交接时接着当前位置与播放状态。修改模板、视频或排列后回到开头并暂停。播放按钮、进度条、时间轴、文字选择框与 AI 资产只跟随这个位置，不等 IMS。
- 游标与数字时间最多每 0.1 秒更新一次（时间轴每次移动游标都整体重渲染，与 SDK 共用主线程），暂停与播放结束时校准；SDK 定位时暂停，等 `playerSeeked` 后才允许继续。
- 每次应用时间线后用 `waitForCanPlay` 等到 canplay 才标为「预览已就绪」并交给 SDK 推进：`setTimeline` 返回时 SDK 内部时长仍是 0，这时播放会立即结束。canplay 的计时从 `setTimeline` 完成后开始，满 60 秒（`CAN_PLAY_TIMEOUT_MS`）才降级，不能把超时当成就绪。
- 降级为 `BasicVideoPreview` 的条件：SDK `renderError`、播放器初始化失败、生成转场画面或载入时间线失败、载入时间线 60 秒未完成、canplay 60 秒未到、定位 10 秒未确认，或播放中 10 秒时间不前进（`STALL_TIMEOUT_MS`）。降级只停用 IMS 侧的文字、转场、滤镜、VFX 与模糊留白，并在预览下方说明原因；时间轴、文字选择框与 AI 资产照常可用。
- 授权请求：SDK 要等 `getWebCert` 回应才初始化，请求失败还会把渲染引擎标为失败且不重试。所以 `sdk.ts` 的 `deferLicenseRequest` 在 SDK 加载前接管播放器窗口的 fetch，立即返回无证书的空 JSON，真实请求在后台发出、出错或 10 秒无回应只写调试日志；授权结果不触发降级，停用时恢复原 fetch。
- 画布与 AI 资产叠加层挂载时就显示，不等 SDK；「刷新预览」在任何状态都可用，点击后销毁播放器并按当前草稿重新初始化。
- 时间轴（`@xzdarcy/react-timeline-editor`）用独立编辑副本，轨道与说明由 `TemplatePreview` 用生成 SDK 时间线的同一个 `buildTimeline` 生成，不等 SDK 载入；片段都不可拖动或调整长度（`movable`/`flexible` 为 false），时间在右侧修改。播放器图层从下到上是视频、滤镜、VFX、文字。
- 测试环境不加载外部脚本与样式表，`template-preview.test.tsx` 用 SDK 替身检查 IMS 各阶段。

## 关键词与双语

- 标题关键词（`titleKeyword*`）与字幕关键词（`subtitleKeyword*`）只存在各自对象的 `tracks[].editor`，其他对象不得启用：颜色为空串或 `#RRGGBB`，局部字号为 0 或 12～300 的整数，只标记首次出现；缺字段的旧模板读默认值。合成时颜色转成 BGR，字号用 IMS `\fs` 设置并恢复。
- 预览关键词：标题未指定时取示例标题首段连续文字的前两个字（英文标题取首个词语），修改示例标题时清除指定关键词；字幕取示例文字的首个词语。正式合成只用切片返回的 `title_keyword` 与 `keyword`，不回退模板词。
- 双语：首次开启「中英双语」才新增 `language=en` 的独立对象，同用途每种语言最多一个；英文外观与 Remotion 绑定独立，时间跟随中文。关闭只设 `enabled=false`，配置与资产引用保留，预览、翻译、合成与 Remotion 环境检查都跳过它。需要客户端与服务端一起升级。

## 上下蒙版与同屏

- 上下蒙版是模板级的 `frame_mask`，不在 `tracks` 里。客户端透明 PNG 与后端 Pillow PNG 按同一四舍五入规则生成整数像素；SDK 5.2.2 的蒙版图片用 Width/Height=1 精确铺满（0.9999 会在右边与底边留下黑线），IMS 用实际画布像素宽高，两种单位不能互换；降级时按同样的整数像素几何显示。
- 蒙版层级在主体与 IMS 转场之上、全局效果/Remotion/文字之下；时间轴左侧类别统一显示「蒙版」，片段及画面对象保留「上下蒙版」名称；蒙版轨道位于文字/全局效果行之下、视频行之上，两栏关闭或取整为零像素时标记「未显示」。
- 蒙版也显示在「画面对象」列表，复用 `RemoveObjectButton` 与 `removeTrack` 删除入口；面板的 × 只关闭设置。「移除当前画面对象」清空草稿配置并立即移除预览和轨道，保存后持久化，重新添加用默认样式。关闭两栏仅停用并保留参数；仅保存基线原有蒙版、草稿已删除时发送清空消息，普通无蒙版保存仍省略字段；保存协议见服务端模板约定。编辑器的蒙版对象 ID 使用 `mask:frame`，与持久化轨道允许的字母、数字、下划线和连字符区分。
- 同屏预览：SDK 5.2.2 通过媒体回调 `{url, maskUrl}` 与带几何的缓存键预览圆角，蒙版来自 `GET /template/preview-mask`；SDK 图像装饰转 PNG，字体换行与合成保持一致，基础预览共用同一套几何。

## Remotion 片段

- 读取为 `Draft.clips`，保存时追加为 `remotion` 轨道，随模板在同一次 `POST /template` 里原子保存，没有单独的片段保存步骤；SDK 不渲染它们。
- 添加时就拒绝与已添加片段画布（宽×高、帧率）不同的资产并说明两边画布；目录里找不到的资产提示保存前移除。新摆放写入 seconds 起点、以发布预览长度初始化的时长与这次的覆盖，不带作用对象；同一资产可重复摆放（轨道 ID 不同）。
- 右侧只开放这次摆放适用的设置：字效为示例文字、排版参数（`sprites/parameters.ts` 的 `TEXT_LAYOUT_PARAMETERS`，与聊天参数面板共用）、特效的开关、位置与时长、水平与垂直位置（0～100%，存为资产画布像素）、关键词与标题时间；滤镜与转场只有强度；视频特效为位置、缩放、旋转与不透明度。颜色、速度、动画等资产内部参数列在「资产默认参数」，已有覆盖继续生效并可逐项恢复默认；逐栏颜色等样式只在字效工作区修改。
- 字效的「关键词设置」页签只开放七个关键词参数，其他以 keyword 开头的参数算内部参数；颜色与字号开关打开时分别取 `#FFFF00` 与当前字号（限制在 12～300）；缺样式参数的旧字效提示在工作区改出新版本后重新保存到资产。右栏按片段 ID 重建，切换片段回到第一个页签。多行字效的示例文字逐行编辑，单行字效不变。
- 只有 `editor.remotion.businessTarget` 的旧字效轨道读取后不自动补写 `text_target`，右侧说明两套文字共存，显式重选作用对象才采用新槽位。
- 叠加播放器只为当前时间附近的片段挂载（开始前 3 秒到结束后 1 秒，已挂载的再多留 2 秒），离开即卸载，回来重新握手。它由播放时间经 `imv-preview-sync`（带摆放的完整时长 `duration`，0～120 秒）逐帧定位，自身不播放；只有回报 `sync` 能力的包能叠加，回报 `host` 的才收 `imv-preview-update`（参数覆盖与 `host.transform`），旧包按默认值显示并提示重新保存。
- 字体由编辑页经 `GET /api/sprites/fonts/{weight}`（`no-cache` 加 ETag）读一次，按 `imv-preview-fonts-request` 以 `imv-preview-fonts` 转交；读取失败回 `fonts: null`，30 秒无回答时播放器改走同级 `/fonts/{weight}`。原因：沙箱页是不透明来源，浏览器不缓存它的请求。
- 封面（`SpriteCover`）不新增服务端产物：卡片进入视口才加载隔离播放器（`sync=1&fonts=host`），定格在 50%，悬停或聚焦时轮播 20%、80%（减少动态效果时不轮播），离开视口即卸载；不支持 `sync` 显示「暂无封面」，报错或 15 秒未就绪显示「封面加载失败」。目录卡片与「特效资产」共用 `AssetBrowser`，分类沿用 Remotion 自己的分类。
- 目录里来源版本变化时，封面与叠加 iframe 地址带 `version` 重新加载，清除旧的握手、能力与失败状态后再发送当前摆放与时间；改名不重载，来源版本不写进模板轨道（真实浏览器回归见 `client/tests/sprite-preview.browser.mjs`）。
- 资产删除：当前草稿（含未保存的添加）摆放的资产禁用删除并说明原因；请求失败后重新读取目录核对，资产已不在目录里就按删除完成处理。目录在用期间订阅 `GET /api/sprites/stream`，收到 `catalog.changed` 重新读取（45 秒无事件换新连接），窗口重新聚焦也重新读取。重命名成功后刷新目录，卡片、片段列表与时间轴标签随之更新。
