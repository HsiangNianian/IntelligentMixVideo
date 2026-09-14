# Remotion 文字模板 Agent MVP

模板功能直接挂载在 `/api/templates`，交互文档为
http://127.0.0.1:8000/api/templates/docs。只增量挂载模板路由，
不接管应用生命周期，不添加统一异常处理，不替换共享 `app` 或其他服务的路由。
普通首页与用户 API 不需要模型密钥、字体或渲染器；模板运行时在首次访问模板接口时启动。

输入至少包含描述或上传图片之一，可同时提供。图片按实际内容验证，支持单帧
PNG/JPEG/WebP，默认最多 10 MiB、两千万像素，去除元数据并缩放到最长边 2048。
视频第一版不解析；请求中的 `video` 字段会被明确拒绝，后续通过版本化协议扩展。
图片文字或目标区域不明确时返回 `needs_input` 和问题；只有图片时默认生成静态模板。

范围是文字及直接相关的背景板、描边、阴影、下划线和高亮，不复刻人物、场景或独立标志。
组件背景透明，不把参考图片嵌入代码。默认画布为 1080×1920、30 fps、150 帧；
可覆盖为偶数边长 64–3840、1–60 fps、1–1800 帧，同时限制八百万级像素及 30 秒。
坐标为归一化中心位置，结束帧为排他边界。

### 本地配置

生成与渲染目前要求 Linux、Node 24、Bun 1.4.2、bubblewrap、util-linux 的 prlimit、
Chrome/Chromium、FFmpeg 的 `ffprobe`，以及 Noto Sans CJK 的常规/粗体 TTC 字体。根据本机调整
`IMV_BROWSER_EXECUTABLE`、`IMV_FONT_REGULAR` 和 `IMV_FONT_BOLD`；默认路径对应开发主机。
只有受管 `Noto Sans CJK SC` 400/700 两种字重，不提供字体上传；预览端点仅提供与成功版本指纹匹配的受管字体。

在 `server/` 执行：

共享服务启动时会初始化上游模板库的 MySQL 连接；先启动 MySQL，并按
[`server/README.md`](../../../README.md) 配置 `.env` 中的 `DB_*` 字段。
该模板库使用 `/template`，本生成服务使用 `/api/templates`，生成任务仍保存在本模块的 SQLite 中。

```sh
uv sync --locked
# 没有现有 .env 时复制示例，再填入自己的模型密钥。
cp -n .env.example .env
chmod 600 .env
cd src/server/remotion
bun install --frozen-lockfile
cd ../../..
uv run --locked server
```

`.env` 已被 Git 忽略。Actor 与 Vision 模型均由服务端配置，不接受请求内模型或密钥覆盖。
`IMV_VISION_BASE_URL`、`IMV_VISION_API_KEY` 省略时继承 Actor 配置。
使用兼容 Chat Completions 的 function tools、JSON 输出与图片输入；`deepseek-flash` 可同时承担两种角色。
Provider 不打印密钥或原始错误响应。部署时可通过环境变量提供配置。
`IMV_DATA_DIR` 可指定可写的绝对路径；默认直接保存到模板模块内的
`src/server/remotion_templates/.data/`，包含 SQLite、上传图片、代码和预览，已被 Git 与构建产物排除。
相对路径也以模板模块目录为基准。默认仅监听本机，MVP 没有多租户或鉴权系统。

### HTTP 流程

| 操作 | 接口（前缀 `/api/templates`） |
| --- | --- |
| 支持的输入、字体和配置就绪状态 | `GET /capabilities` |
| 上传图片，得到 asset ID | `POST /assets`，multipart 字段 `file` |
| 历史参考图片 | `GET /assets/{id}`，校验登记文件的完整性 |
| 创建作品并排队 | `POST /works` |
| 历史会话分页 | `GET /works?history=true&limit=30`，返回 `next_cursor` |
| 会话快照及更早消息 | `GET /works/{id}/session?limit=50`，以 `next_before` 请求更早消息 |
| SSE 会话订阅 | `GET /works/{id}/stream?after=0`，支持 `Last-Event-ID` |
| 列表、作品与成功版本 | `GET /works`、`GET /works/{id}`、`GET /works/{id}/versions` |
| 可用版本及代码/配置 | `GET /versions/{id}` |
| 参数、自然语言修改或回答澄清 | `POST /works/{id}/messages` |
| 运行状态、简短提示和追问 | `GET /jobs/{id}` |
| 兼容的 JSON 事件查询 | `GET /jobs/{id}/events?after=0`，使用返回的 `next_cursor` |
| 取消、重试 | `POST /jobs/{id}/cancel`、`/retry` |
| 可用结果下载链接，无结果时为空列表 | `GET /jobs/{id}/artifacts` |
| 隔离 Player 页面 | `GET /versions/{id}/preview` |
| 对应版本的受管字体 | `GET /versions/{id}/fonts/{weight}`，仅 400/700 |
| 下载已验收代码、PNG 或 MP4 | `GET /versions/{id}/artifacts/{filename}` |

```json
{"description":"标题写‘今日灵感’，白色粗体居中，黄色下划线"}
```

图片请求用 `"image":{"asset_id":"上传返回的 UUID"}`，可以与 `description` 合用。
创建返回 HTTP 202 和 `work`、`job`；读取会话快照后从其游标订阅 SSE。成功后版本包含 `candidate.tsx_code`、
`candidate.config_schema`、`candidate.default_config` 和 `spec`，产物列表可下载 `.tsx`、PNG 和 MP4。
公开任务只有状态、问题、结果 ID 和简短提示，不包含修复次数、阶段、模型用量或内部诊断。
失败候选与报告保留在本地，HTTP 不提供下载入口；尚未运行的检查不能算成功。

修改请求为 `{"parameters":{"0_text":"新的标题"}}` 或 `{"instruction":"把标题改为黄色"}`，
二选一；参数名以返回的 schema 为准。可用 `base_version_id` 选择历史成功版本。
回答问题仍使用消息接口，例如 `{"instruction":"图片上半部分的大标题","reply_to_job_id":"提出问题的任务 UUID"}`。
`reply_to_job_id` 必须指向该作品最新且处于 `needs_input` 的任务；重复回答、跨作品回答或过期回答返回 409。
待澄清时不能把普通修改指令误当成答案。同一作品同时只允许一个排队或运行任务。
默认修改基于最近成功版本；版本按发布次序线性编号，失败候选不改变当前版本。
取消会停止模型请求与隔离子进程。重启后未完成任务标记为 `interrupted`；
重试和澄清均创建新的执行 ID，继承本模板任务的有限窗口，不恢复中断中的工具调用。
仅最新一次执行允许重试；一次新模板任务必须重新 `POST /works`，上下文从空窗口开始。

本次接口调整移除了 `/works/{id}/edits`、`/jobs/{id}/clarify` 与按 attempt 下载的地址。
调用方改用 `/messages` 和产物列表提供的成功版本链接。旧本地版本没有新的证据清单，
仍可读取数据库中的代码；需要新的预览下载时应重新生成并通过当前验收。

### 公开聊天历史与 SSE

一个作品是一段历史会话，包含其后续澄清、修改和重试任务。公开消息保存在 `chat_messages`，
可重放事件保存在 `work_events`；模型的 `conversations` 仍只维护既有滑动窗口。
初始化增量创建表，按旧任务中可确认的输入、问题和结果恢复公开记录，标记 `reconstructed`，
重复启动不重复恢复；不迁移或公开内部工具上下文。

用户消息、任务状态、终态消息和成功版本事件在对应 SQLite 事务内写入；SSE 只读取已提交记录。
`/session` 在同一读取事务中返回消息、最新任务、成功版本指针及事件水位 `cursor`。
消息按正序返回，`next_before` 非空时可以继续读取更早页；列表使用活动时间与作品 ID 的游标分页。

SSE 类型为 `message.created`、`job.updated`、`version.ready`，`data` 为包含
`id`、`work_id`、`type`、`data`、`created_at` 的 JSON。公开任务额外包含创建/更新时间和提交的参数补丁，
内部修复次数与阶段不产生公开进度事件；只有成功版本产生 `version.ready`。
首次连接使用快照游标，重连优先采用 `Last-Event-ID`；0 表示重放全部公开记录。
事件按 ID 顺序分批读完，再等待新记录；跨作品的编号间隙正常，客户端应按 ID 去重。
游标不属于此作品或已不可用返回 409，客户端应重新取快照。

空闲时每 15 秒发送心跳注释，响应为 `text/event-stream`、`Cache-Control: no-cache` 和
`X-Accel-Buffering: no`。服务端每次有界读取后释放数据库连接，空闲等待可中断；
关闭订阅与运行时任务相互独立。客户端切换、新增或退出不会取消旧任务，取消只由显式 `/cancel` 发起。
部署反向代理时关闭事件缓冲，并使读取超时长于心跳间隔。
原 `/works` 默认列表、任务查询与 `/jobs/{id}/events` JSON 接口保持兼容。

历史与流协议的离线回归见 `server/tests/test_remotion_history.py`，覆盖事务回滚、旧记录恢复、
快照并发、分页、素材校验、100 条以上事件重放、心跳、真实 HTTP 重连及主应用跨域预检；
成功版本与后续参数失败的关系由 `test_remotion_templates.py` 验证。

### 代码契约与执行边界

输出是新的组件契约：单个默认导出 React 组件，直接接收扁平的标量 props。
只允许受限的 React/Remotion 导入；不自行注册 Composition、加载字体或读取文件/网络。
所有文字、字体、大小、颜色、位置及已有样式标量均在 JSON Schema 中暴露；
`x-imv-target` 将控件绑定到 `TemplateSpec` 的标量路径。参数修改同时更新默认值和验收目标，
保留 TSX 字节不变，再次渲染验收。结构和时间轴修改需要重新生成代码。
旧 XLS 模板的配置结构与此不同，不承诺可以直接运行旧模板。

客户端复制使用 `Export.tsx`：保留原组件，增加可选 props 包装及当前成功参数的默认值，
并导出画布、帧率和时长常量。消费方需安装 React/Remotion 并加载受管字体；不包含背景视频。
每次验收同时编译导出并以空 props 渲染一帧，与原参数渲染的 PNG 字节比较，防止复制时丢失默认值。
`Template.tsx` 仍保留原始扁平 props 契约，参数修改不会改写它。

`presentation.mjs` 将 `preview-host.tsx`、当前组件和参数打成 `interactive.js`；
新版本把交互包、导出及导出首帧加入 SHA-256 清单，两个新增宿主源码也加入运行时指纹。
`/preview` 验证清单后嵌入该包，使用 CSP sandbox，不允许同源权限或任意 fetch；字体及媒体可加载。
父页面通过 URL fragment 通道发送参数、背景 URL 和请求编号，Player 在字体及媒体就绪并绘制首帧后确认对应编号。
正常播放不重复触发加载锁。字体文件变更、产物缺失或被修改时返回 404。
旧版本缺少交互包或 Export 时也返回 404，重新生成后才提供新的预览能力。

`context.py` 保留单模板任务内最近 8 组完整 user/assistant/tool 交互，总 UTF-8 大小不超过
240,000 字节，超限时按组裁剪，不拆散 tool calls 与对应结果，不截断代码。
单组过大时明确终止本次执行。固定 system 规则和当前任务快照独立于窗口：原始需求、
当前修改要求、成功版本目标、候选 ID、实际检查结果与未解决问题始终可见。
Actor 的快照、窗口与工具定义合计上限为 512,000 字节；输出另受 token 预算限制。
图片仅在分析和独立视觉验收时发送，窗口不累计图片 base64。
SQLite `conversations` 表只覆盖保存这个有限窗口；没有跨任务记忆、对话摘要或向量库。

事实来源明确区分：用户输入保存在作品和执行输入中；模型整理的 spec 是待核验目标，
assumptions 是模型假设；验证报告中的 `source=host` 是工具观察，
`source=visual_model` 是视觉判断。模型的承诺、生成的 TSX 和 spec 都不能自行变成通过证据。
首次生成的视觉验收读取原始要求与实际帧；后续修改以当前成功版本目标和此次用户输入为依据，
不把已被用户修改的旧文字或颜色重新作为要求。不能仅用模型自己写的新 spec 证明需求已满足。
生成前另做独立目标一致性评审，核对规格与用户输入、成功版本以及静态/动画语义是否一致；
不通过则在预算内重新整理，未知则补证或提出澄清问题，不冻结矛盾的规格。

`harness.py` 使用 `while True` 编排真实工具调用：`read_current_template`、
`submit_candidate`、`request_completion`。提交候选会实际运行校验，把结果通过对应
`tool_call_id` 返回；模型的普通完成声明、未知工具和过期候选 ID 都不能发布。
完成请求必须单独调用，服务端重新检查当前候选、证据和文件后才设置 `complete`。
失败、缺失、未知、重复或冲突的证据会阻止完成，并给出具体内部 steer；重复无进展要求
模型改变实现，不再因为固定两轮修复用尽就提前输出失败候选。
模型协议错误可在预算内重新修复；网络不可用、取消、超时或预算耗尽则结束执行，不输出失败代码。
参数补丁直接复用成功代码执行同一验收门禁；不通过时保留原结果，不擅自改写代码或用户参数。
默认每次执行最多 16 次模型请求（含分析和视觉验收）、100,000 token、600 秒，
另有 50 次 actor turn 的硬边界；没有无限调用保证。

`runtime.py`
管理 FIFO 队列、预算、超时、取消和持久化；`trajectory.py` 根据真实检查结果判断
偏离、退步、重复无进展和过期证据，不能让模型用完成声明绕过验收。
这是本项目实现，参考 SimAgentPlg 的职责分离，运行时没有 `ejagent-core` 依赖。

Worker 在无网络的 bubblewrap 中执行，仅暴露系统库、渲染依赖、字体与单次尝试目录，
不挂载用户目录或 `.env`。依次格式化、检查源码策略、TypeScript、bundle、PNG/MP4、
乱序后的重复帧和默认导出帧；宿主比较这些证据，再由视觉模型评估文字、布局、样式、动画与范围。
`ffprobe` 检查真实 MP4 的尺寸、帧率、帧数和时长；Pillow 检查 PNG 尺寸、透明通道、
可见内容与声明动画区间的跨帧变化。全程显示且没有进入／退出动画的目标（包括纯 hold）必须保持帧稳定。
`image_comparison.py` 统一重复帧、默认导出、静态帧及动画/参数变化的比较规则：
将 RGBA 分别合成到黑、白背景，取各通道的最大差异，忽略 PNG 编码和全透明 RGB 差异。
只有最大差异不超过 **2/255**，且变化像素不超过两图可见前景并集的 **1%**，才视为栅格噪声。
两项须同时满足；不使用整张透明画布稀释差异，明显单像素变化与大面积细微变化仍失败。
噪声不能充当动画或参数生效的证据；透明/空白、尺寸、类型、配置及独立视觉验收继续执行。
一致性失败的内部 steer 给出帧号、文件名、差异数量/比例、最大幅度、区域、阈值及对应修复方向，
提示保留用户设计，并区分代码依赖帧/时间/可变状态与需要调查渲染器的情况。
比较器与参数实验源码加入运行时指纹；像素容差只用于画面等价判断，产物防篡改仍严格校验 SHA-256。
一帧 enter/exit 区间使用可用的相邻边界帧核验变化；整段只有一帧等缺少时序证据的情况返回 unknown，不能当作成功。
每个文字层分别变更文字、颜色、字号、x 和 y，
重新渲染并检查像素变化、目标颜色、字号覆盖面积及位移方向；颜色覆盖按 alpha 加权，允许各 RGB 通道最多 2/255 的取整误差，错误色相仍拒绝。
靠边位置优先向画布中心移动以减少探针自身裁切；极小字号使用增大探针，确保实验值不同于原值。参数实验明确覆盖这五类，
其他样式控件仍依靠类型、配置一致性和视觉验收，不宣称所有参数组合都已穷举。
多层遮挡、装饰与极端参数可能让实验结果保守失败；通过验证表示满足当前验收规则，
不保证所有潜在参数值和每一帧都符合审美预期。
当前字号与位置证据仍基于整图 alpha 面积/重心，不能在所有遮挡、裁切与换行情形下隔离单层因果；
渐变或混色也可能缺少接近目标色的像素，不能仅因这些启发式局限而自动放行。
检查指纹绑定代码、参数、目标、字体、浏览器及 worker/依赖版本；全部通过才允许发布。
产物清单记录源码、配置、规格、帧、视频及参数实验的 SHA-256，视觉验收前后和发布前
复核字节。完成时同时复核字体、渲染器、浏览器、Node 与 ffprobe 的环境指纹。
已验收文件复制到独立 `accepted/<version_id>/` 后再次验证，再原子更新当前版本指针。
下载也核验产物清单；文件被修改或缺失时返回 404，不把旧报告套在新内容上。
视觉评估仍是模型判断，关键帧抽样也不能证明每一帧像素级还原；下载产物可人工复核。

离线测试与实际隔离渲染测试分开运行，前者不需要模型、浏览器或 Node：

```sh
uv run --locked pytest -v
IMV_TEST_RENDERER=1 uv run --locked pytest tests/test_remotion_templates.py -k 'real_' -v
```

普通 CI 的服务端 pytest 覆盖契约、错误、恢复、版本、轨迹和 API 兼容行为。
实际渲染测试需先安装上述 Linux 前提；不会由离线测试通过推断渲染或真实模型已通过。

使用自己的 `.env` 显式运行真实模型验收（会消耗模型 token）：

```sh
# 在 server/ 执行；验证文本生成、参数修改、自然语言修改和纯图片生成。
uv run --locked python -m server.remotion_templates.smoke --live
```

每次验收使用模板目录内独立的 `.data/smoke-<id>/`，保留所有尝试与输出；
终端只打印任务状态、用量和产物位置，不输出密钥。
