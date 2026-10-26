# IntelligentMixVideo API

Python 3.12+、FastAPI 和 MySQL。模板库在连接此服务的客户端之间共享，不包含登录、用户隔离或旧数据迁移。
另提供文案切片接口，使用已有 ASR 时间轴与 OpenAI 兼容模型生成带时间和关键词的片段。
服务端与客户端使用同一项目版本；`pyproject.toml`、`uv.lock` 随发版统一更新并提交，操作见[根目录发版说明](../README.md#tag-发版)。FastAPI 文档版本读取已安装的 `imv-server` 包元数据；更新后通过 `uv run --locked server` 同步安装并重启。

## 本地启动

先启动 MySQL，再复制 `.env.example` 为 `server/.env`，填写 `DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD` 和 `DB_NAME`；可选 `DB_SSL_CA` 指定 CA 文件，启用后驱动校验服务端证书。
`pydantic-settings` 自动读取并校验配置，进程环境变量优先于 `.env`，缺省项使用代码默认值。
各模块通过 `config_base.CommonSettings` 共用读取规则，配置文件固定为 `server/.env`，切换工作目录不改变读取位置；`DB_PORT` 自动转换为整数，范围为 1～65535。
`DB_NAME` 为 1～64 字符，默认 `intelligent_mix_video`。修改配置后重启服务。
启动时检查目标数据库，不存在则自动创建，使用 `utf8mb4` 字符集与 `utf8mb4_bin` 排序规则。
建库需要配置的账号具备对应 `CREATE` 权限；已有数据库直接连接，不执行建库或修改已有数据。
配置无效、MySQL 不可达、鉴权或建库权限不足时，应用报错并停止启动；修正后重新启动。
真实 `.env` 已被 Git 忽略，不要把密码写进示例文件或客户端配置。

公共基类只统一读取规则，各模块保留原配置类、字段、校验和实例化时机。
优先级为构造参数 > 进程环境变量 > `server/.env` > 字段默认值；保留 `_env_file` 显式覆盖与 `None` 禁用文件。
固定路径按当前源码布局计算，不自动适配任意安装位置；非源码部署请显式提供配置文件或使用进程环境变量。
不提供热更新或统一配置快照；运行期间不要修改 `.env`，修改后重启服务。

在本目录执行：

```sh
uv sync --locked
uv run --locked server
```

默认监听 `0.0.0.0:20070`（所有 IPv4 接口），本机交互文档为 `http://127.0.0.1:20070/docs`。
远程访问使用 `http://<服务器 IP 或域名>:20070`，服务器防火墙或云安全组需允许对应端口；客户端 `VITE_API_URL` 配置为实际服务地址。
仓库根目录可执行 `uv run --locked --project server server`。首次模板请求自动创建缺失的 `templates` 表，不执行旧数据迁移；Remotion 已发布 Sprite 的 `remotion_sprites` 表在首次访问资产目录、发布，或保存含 Remotion 片段的模板时创建；字效工作区的其余 `remotion_*` 表（作品、任务、版本、事件、设备、叠加渲染任务、Presets 等）在第一个 Remotion 请求时创建，要求 MySQL 8.0 及以上，且只部署一个后端实例。
应用启动后数据库暂时不可用时，模板接口返回 503，恢复后可重试；首页和用户示例接口本身不查询数据库。

`uv run server` 与 `uv run python -m server` 均读取固定的 `server/.env` 中的 `PORT`，进程环境变量优先。未设置时默认 `20070`；端口须为 1～65535 的整数，空值、非整数或越界值会阻止启动。修改后重启服务。停止服务时最多等待 10 秒收尾，仍未结束的连接（如作品 SSE）随后被断开，旧进程退出后不再运行字效任务队列与清理，新进程可直接接管。
客户端 API 地址同样默认 `http://localhost:20070`；若 `client/.env` 已配置其他 `VITE_API_URL`，需同步修改并重启前端（生产环境重新构建）。

```sh
PORT=8010 uv run --locked server
```

本机 Python 包镜像若落后于锁定版本，可以在 uv 命令中添加 `--default-index https://pypi.org/simple`，无需降级项目依赖。

## 模板接口

标题和字幕分别通过 `tracks[].editor.titleFont` / `subtitleFont` 保存字体，Protobuf 字段为 `title_font` / `subtitle_font`。仅接受 `src/server/template/fonts.json` 中十一种字体的 `value`，无效字体返回 422，缺少字段默认 `Alibaba PuHuiTi`。正式合成分别使用对应对象的字体；思源黑体、思源宋体保存及合成使用 `Source Han Sans CN`、`Source Han Serif CN`，预览专用标识由客户端转换。字体 HTTP 集成测试使用独立 MySQL 服务：设置 `IMV_FONT_TEST_API` 后执行 `uv run --locked pytest --noconftest tests/test_template_fonts.py`，测试会创建并删除自己的模板，覆盖保存、读取、更新及合成时间线。

云端模板保存请求使用 Protobuf 二进制，列表、详情和保存成功响应默认在返回前转换为 JSON。消息定义仍在仓库根目录 `proto/`，Python 生成代码位于 `src/generated/imv/template/v1/`，与客户端生成目录结构一致。

模板接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/template` | 返回 `ListTemplatesResponse`，按更新时间倒序 |
| POST | `/template` | 接收 `SaveTemplateRequest`；无 `template_id` 创建，携带 ID 完整更新，返回 `SaveTemplateResponse` |
| GET | `/template/{template_id}` | 返回 `GetTemplateResponse` |
| DELETE | `/template/{template_id}` | 删除模板，成功返回 204 |

GET 与 POST 成功响应默认使用 `application/json`，采用 Protobuf JSON 映射：字段为 camelCase（如 `templateId`、`createdAt`、`effectIds`），时间为 UTC RFC 3339 字符串；列表为 `{"templates":[...]}`，空库为 `{"templates":[]}`，详情和保存为 `{"template":{...}}`，轨道保留 `template.tracks.tracks` 结构。空数组、空映射和非 optional 标量默认值保留，未设置的 optional 字段省略。POST 请求仍使用 `application/x-protobuf`，JSON 请求返回 415，损坏的 Protobuf 消息返回 400。创建返回 201，更新返回 200。名称重复返回 409，模板不存在返回 404，非法 ID 或配置返回 422；错误响应使用 JSON，删除成功仍为 204 空响应。为兼容旧客户端，单一 `Accept: application/x-protobuf` 仍返回 Protobuf 二进制；其他请求（含省略 Accept、`*/*` 或 `application/json`）默认返回 JSON，成功响应带 `Vary: Accept`。新客户端按响应 `Content-Type` 解析 JSON 或旧服务端的 Protobuf 二进制，JSON 解析忽略未知字段；仅响应格式迁移不要求两端同时升级。
重命名使用携带 ID 的 POST；另存为使用不携带 ID 的 POST。不存在的 ID 不会自动变成创建。

列表返回逐条校验的 `TemplateSummary`，包含名称、ID、更新时间、有效模板的标题/字幕及对象数量，无效记录通过 `validationError` 说明原因。无效记录的详情读取返回 409，删除仍按 ID 执行，不影响同库有效模板。保存必须包含中文标题和中文字幕各一个；双语模板另有对应的英文对象。列表消息与转场字段需要客户端、服务端一起升级；已有记录不自动转换。

`SaveTemplateRequest.tracks` 必须提供，通过 `TrackList.tracks[].editor` 保存参数；缺省参数由业务校验补齐。模板更新完整替换配置。名称去除首尾空白后为 1～100 字符；说明最多 1000 字符；标题、字幕、气泡示例文字最多 60、100、40 字符；字号 12～300 整数；位置 0～100%；动画和转场时长 0.1～3 秒。

顶部标题对象的 `titleKeywordBold`、`titleKeywordItalic`、`titleKeywordUnderline`、`titleKeywordStrikeout` 可组合使用，`titleKeywordColor` 设置局部颜色，`titleKeywordSize` 设置局部字号；合成调用切片时传入请求 `title`，标题局部样式仅作用于返回的 `title_keyword` 首次出现的位置；返回值缺失或为空时保留标题原文，不回退模板 `titleKeyword` 或标题前两个字。旧快照缺少标题词时不高亮；无声模式通过仅标题提词取得关键词；模板预览仍沿用原规则。底部字幕对象独立保存 `subtitleKeywordBold`、`subtitleKeywordItalic`、`subtitleKeywordUnderline`、`subtitleKeywordStrikeout`、`subtitleKeywordColor` 和 `subtitleKeywordSize`，作用于原切片中首次包含 `keyword` 的字幕短句，空关键词保留原文。颜色为空字符串或 `#RRGGBB`，空字符串表示保持文字原色；合成时转换为 IMS 要求的 BGR 顺序。局部字号为 0（沿用原字号）或 12～300 的整数，合成时使用 IMS `\fs` 指令设置并恢复。模板响应在对应对象的 `tracks[].editor` 返回这些值。 视频合成按气泡对象自身时间区间显示其 `bubbleText` 示例文字一次，不使用切片关键词；有气泡对象但未选气泡样式时仍显示文字，无气泡对象则不生成。关键词继续通过标题和字幕的局部样式强调。

独立对象通过 `tracks` 保存，普通对象包含 `id`、`target`、`start_mode`、`start`、`duration` 和 `editor`。标题、字幕、VFX 最多各一个，滤镜可多个；字幕固定 `seconds`、0、null。其他普通对象支持 `seconds`、`percent` 起点，百分比小于 100，`duration` 为正秒数或 `null`（持续到结束）。转场使用唯一的 `transition_boundary` 列表序号（1～100），IMS 转场禁止设置 `start`、`start_mode`。IMS 与 Remotion 共用列表和统一时长 `transition_duration_seconds`。`transition_enabled` 为布尔值，默认 true；false 时保留候选，IMS 与 Remotion 成片均不应用转场。`transition_mode=sequence` 在所有素材连接处按列表循环，`random` 按任务 ID 与连接位置从尚未选中的候选中选择，每项最多一次，候选用完后其余连接位置不应用转场，重试结果一致；每个连接处只选一个效果。编辑预览开启转场且存在候选时应用于两个连接位置，只有一个候选时两处共用，有多个候选时随机选择不重复。未携带 `transition_mode` 的已有模板保持指定连接位置的行为，编辑保存后使用列表规则。转场时长最多为相邻素材各自长度的一半，短于 0.1 秒时跳过。模板不保存预览视频信息。`resolve_template` 的 `sequence` 模式累计扣除重叠，`fixed` 保留素材时间和音频总长。普通对象超出结束时截短，动画按有效帧数缩短，帧数不足时失败。标题使用请求文字，字幕按文案切片显示。
创建和更新在写入前强制检查标题、底部字幕各一个，缺少任意一个返回 422，已有记录保持不变。同一文字角色的循环动画与入场、出场互斥。模板允许空效果列表，最多 500 个不同效果 ID。

Remotion 片段也保存在 `tracks` 里：`target` 为 `remotion`，`sprite_id` 引用已发布的 Sprite；`id` 仍是模板内唯一的摆放 ID，所以同一个 Sprite 可以摆放多次。
`start_mode` 只能是 `seconds`，`duration` 为 `null`（取 Sprite 自身时长）或正秒数；`editor` 只能设置 `remotion`（这次摆放的参数覆盖 `parameters` 与宿主变换 `transform`，没有时响应里为 `{}`），IMS 对象不能设置它；其他对象不能带 `sprite_id`。保存时引用的 Sprite 都必须已在 `remotion_sprites` 发布，且画布宽、高、帧率一致（叠加渲染要把它们叠成同一个视频），每个片段的覆盖须指向可编辑参数、类型与默认值相同、在声明范围内且合并后满足发布 Schema，变换只能设置该分类允许的字段（滤镜与转场只有 `opacity`）并在范围内，否则返回 422。
Sprite 分为字效（TEXT）、滤镜（FILTER_OVERLAY）与转场和视频特效（VIDEO_OVERLAY、TRANSITION_OVERLAY），都是不读取底片像素的透明叠加层；分类、必备参数与发布时的边界抽样见 `src/server/remotion_templates/README.md`。
片段与 IMS 对象共用 100 条上限，也不产生 `effect_ids`。视频合成的 IMS 时间线跳过这些轨道，不为它们生成任何文字或效果；层级为它们在 `tracks` 中的先后（先者在下）。

响应补充 UUID、UTC 创建/更新时间和 `effects` 参数快照。服务端通过固定 SDK 5.2.2 白名单解析效果，
拒绝未知 ID、错误分类和全部 `tracks[].editor` 中的效果与 `effect_ids` 不一致；客户端不能提交渲染参数。
`schema.py` 负责业务字段校验，`router.py` 将生成的 Protobuf 消息转换为业务模型并生成响应。

MySQL 单独列保存唯一名称、ID 和时间，JSON 保存完整编辑配置与效果快照。
保存和删除使用事务；同时保存同名新模板仅一个成功。同时编辑同一个模板时，后一次成功保存覆盖前一次完整配置。

## 异步视频合成

所有模式均接受 `packRules.backgroundMusic: null`，表示不使用背景音乐，等同于省略该字段或传 `audioSwitch: false`。

统一入口先按原始文案字段确定内部模式：非空 `text` 为 standard（必须有 `videoUrl` 和 `audioUrl`）；无非空 `text`、有顶层 `audioUrl` 为 materials_voice（`copy` 提供字幕，可省略）；无配音且无文案为 materials_silent。文案缺省、null、空串或纯空白均视为空；`text` 与 `copy` 同时非空，或有非空文案却没有配音，均返回 422。`videoUrl` 和背景音乐不参与分流。请求不用传 `compositionMode`，服务端将已判定模式保存在 `video_compositions.data.request.compositionMode`，后续读取保存值；旧任务没有模式字段时仍按原 URL 规则恢复。standard 省略 `materials` 为纯数字人，显式数组才匹配；两个纯素材模式要求非空素材数组，严格按顺序拼接且不调用匹配。视频使用 FFprobe 获取实际视频流时长，图片每张 3 秒，超长裁尾、不足异步失败。判定模式后才把 `copy` 合并为内部 `text`；有语音以 ASR 配音总长合成，有文案才切片生成字幕。无语音以 `processRules.videoDuration`（有限正数秒，可为小数，不接受布尔值或数字字符串）合成，始终无字幕，`title` 使用模板标题样式、动画与时间区间。纯素材缺少文字对象时回退内置默认样式，正文仍取请求，不保存回模板。素材原声静音，背景音乐仍可选。无语音不依赖 ASR／匹配配置，有非空标题时需要切片模型配置并仅提取标题关键词；纯素材含视频时 PATH 需有 `ffprobe`；探测白名单包含 HTTP 代理所需的 `httpproxy`，失败的退出码与脱敏诊断写入 `timeline.json`。字段矩阵和完整请求示例见 [视频合成 API 文档](src/server/video_composition/api.md#三种模式)。

standard 模式中，请求不包含 `materials` 时为纯数字人：跳过素材匹配，以 `audioUrl` 的 ASR 原始总时长铺满数字人视频，字幕继续按文案切片和模板时间规则显示。显式 `materials: []` 保留素材匹配但不指定候选，非空数组携带候选匹配，`null` 返回 422。需要保留此前缺省字段也匹配的行为时，调用方须改为显式传入 `[]`；已有任务继续按已保存的请求和阶段恢复。

视频时长优先读取首个视频轨的 `duration`；缺失或为 `N/A` 时读取该轨 `DURATION` 标签（`HH:MM:SS.小数`），普通标签缺失时兼容 `DURATION-eng`、`DURATION-ZH` 等两至三字母语言后缀，标签名不区分大小写，扣除视频轨起始偏移后仍须为有限正数。真实媒体测试覆盖 MP4、WebM（VP8/VP9）和 MKV，并检查音轨更长、语言标签与视频起点偏移的情况。不使用容器或音轨时长代替；两种视频轨时长信息均不可用的素材（如部分 FLV）仍异步报 `material_probe_failed`，具体原因写入 `timeline.json`。测试环境有 FFmpeg/FFprobe 时运行真实样本，无工具时跳过；后端集成工作流安装工具并执行这些用例。

`SEGMENT_MATCH_BASE_URL` 在环境变量、`.env` 或 Debug 桌面设置中留空或仅含空白，均视为未配置。只有 standard 显式传入 `materials` 时必须提供有效匹配地址，否则受理返回 503；纯数字人和两个纯素材模式不要求该配置。非空非法地址仍拒绝加载。

`POST /api/v1/video-compositions` 创建任务，HTTP 200 响应为 `{"code":200,"message":"操作成功","data":"任务ID"}`；`GET /api/v1/video-compositions/{taskId}` 查询结果，查询结构保持不变。终态回调仅含 `taskId/status/videoUrl/errorMessage`：成功为 `succeed`、视频直链、null 错误；失败为 `failed`、null 地址、错误摘要。回调 ID 与创建响应的 `data` 一致，内部和查询的成功状态仍为 `succeeded`。模板按 `tracks[].editor` 读取，标题取请求 `title`，关键词取原切片；字幕使用切片原文与时间，由合成时间线保留中英文问号、数字内部的小数点并去除其他标点；已有快照中的 `subtitle_parts` 仍可读取。素材匹配仍接收原切片文字与时间，结果的数量、编号、顺序及起止时间严格校验，文字仅允许首尾空白差异；字幕继续取本地切片，原始匹配回执保留在脱敏日志中。标题和字幕的模板示例文字不进入成片，气泡对象使用其模板示例文字。

创建请求的字段校验错误返回 HTTP 422，响应含 `{"code":422,"message":"请求参数无效","data":null}`；客户端配置头错误及其他错误沿用原有格式。后台合成失败通过查询结果的 `status: failed` 和 `error` 表示。

视频和图片片段以 `Contain` 方式放入输出画布，保留素材原始宽高比和完整画面；比例不同时使用素材的模糊背景填充留白。已有成片不会自动重新渲染。

云端渲染返回 `Success` 后仍保持 `processing/rendering`，在原渲染截止时间内获取 IMS 临时地址并下载成片。FFmpeg 按解码顺序截取第 3 帧，视频与 PNG 分别上传至 ZOS 的 `imv/video_composition/{taskId}.mp4` 和同名前缀的 `.png`；两个对象单独设置 `public-read`，确认大小及匿名读取后才保存 `succeeded` 和待通知状态。PNG 可通过 `ZOS_WEB_URL/imv/video_composition/{taskId}.png` 访问，不进入 GET 或回调；两者仍只返回视频地址。取址超时为 `playback_timeout`，转存持续失败为 `zos_upload_timeout`，均不重提渲染。历史成功任务不补图，GET 仍按原规则返回地址。

提供 `callbackUrl` 时，终态以 POST JSON 通知，任意 2xx 表示送达；非 2xx、网络错误和超时均在失败后按 5、15、45 秒间隔重试，最多四次，不跟随重定向。次数和下次投递时间落库，等待中的重试可在重启后继续；新成功任务的通知不再依赖 IMS 凭据，历史成功任务在重启后缺少客户端 IMS 凭据且需要重新取址时仍会直接记为通知失败。通知失败不回退合成终态，接收方须按 `taskId` 幂等处理。沿用现有恢复边界：发送中进程退出或送达状态保存失败留下的 `sending` 不自动重放，调用方通过 GET 补查。

`COMPOSITION_MATCH_WAIT_SECONDS` 默认 30 秒，匹配回调未到则只主动查询一次。修改配置后重启；已有任务保留其已保存的截止时间，失败历史通知不自动重新发送。

合成调度 `COMPOSITION_CONCURRENCY` 和共用 Remotion 视频名额 `IMV_OVERLAY_TASK_CONCURRENCY` 默认均为 6。整段合成、模板叠加和单 Sprite 共用固定 15 秒分块，不超过 15 秒只用一块，末块保留剩余帧。每视频最多 6 块并发、每块默认最多 4 页，小核数按有效 CPU 自动减少页数，所有视频共用最多 36 个切片名额，资源充足时允许六视频各六块；CPU、内存不足时先降并发，再排队；容量等待默认最多 300 秒，超时返回 `resource_unavailable`（合成为 `remotion_resource_unavailable`），调用方可稍后重新提交，已失败任务不会自动重试。默认为有效 CPU/内存预留 25%，所有隔离 worker 通过 `taskset` 继承相同的核范围，配额读取支持祖先 cgroup v2（v1 回退到宿主亲和性和内存信息）；内存准入按每页 1 GiB 加每视频额外 1 GiB 估算，36 块×4 页与六视频额外预算共 150 GiB。全局预算默认再限 `IMV_RENDER_MAX_CPUS=144` 个逻辑 CPU、`IMV_RENDER_MAX_MEMORY_MB=153600` MiB，与整机/cgroup 预留后的余量取更严者；编码线程也在该 CPU 核范围内运行。128 个无其他配额限制的逻辑 CPU 预留 25% 后，默认最多运行 24 个四页切片。cgroup 有空闲不取消宿主总内存 25% 的保护线；内存准入估算不能替代部署层硬上限。Python 直接部署还应配置 systemd MemoryHigh/MemoryMax，限制整个服务及其子进程。Remotion 锁定 4.0.534，部署其配套 Headless Shell 可启用 CPU 共享内存和 VP9 编码流水线，系统 Chrome 不会自动替换。VP9 默认使用 `cpu-used=4`、`threads=2`。仅多块输出要求 FFmpeg；音频时长未知时在解析后、渲染前检查。诊断的 `slot_wait_ms` 与 `resource_wait_ms` 分别记录两段等待。渲染默认值集中在 `remotion_templates/settings.py`，合成调度默认值在 `video_composition/settings.py`；`.env.example` 仅保留渲染的 CPU/内存预留比例，其他调优项无需填写，按需仍可通过环境变量覆盖。

新任务还需在 `server/.env` 中设置 `ZOS_API_ENDPOINT`、`ZOS_BUCKET`、`ZOS_ACCESS_KEY_ID`、`ZOS_SECRET_ACCESS_KEY`、`ZOS_WEB_URL`，并确保 `ffmpeg` 在服务端 PATH 中；缺少 FFmpeg 时受理返回 503。`ZOS_REGION` 默认 `hangzhou-7`，`ZOS_FORCE_PATH_STYLE` 默认 false。`ZOS_DELETE_OBJECTS` 默认 false：删除 Remotion 资产、会话等记录时只删记录与本地缓存，不删 ZOS 上的文件（删除按请求收费，也不需要删除权限），设为 true 时一并删除。密钥仅由服务端读取，不进入客户端 IMS 设置。上传与公开地址分别使用 API Endpoint 和 Web URL；目前不读取 `ZOS_ENDPOINT`。对象删除或桶生命周期清理后，公开 URL 也会失效。ZOS 上传与匿名读取遵循进程环境的 `HTTP_PROXY`／`HTTPS_PROXY`／`NO_PROXY`；未配置代理时直连，已有代理但 ZOS 需直连时，将 API 与公开访问域名加入 `NO_PROXY`（保留原有条目），在启动进程的环境中设置。串行分片与 TLS 校验保持开启。字段示例和完整接口见 [视频合成 API 文档](src/server/video_composition/api.md)。

操练场另提供 `POST /api/v1/video-compositions/playground`，通过 `mode` 与 `styleId` 读取随包封存的固定样本，跳过 ASR、切片、翻译和素材匹配，仍由原 GET 查询成片。无需配置基准任务 ID，也不依赖来源数据库中的任务；当前仅封存 IP口播样本，其余模式待补充后初始化。操练场 ZOS 产物使用 `imv/playground/`，初始化与完整契约见[视频合成 API](src/server/video_composition/api.md#操练场)。

### 执行日志

历史日志仍保留在 `server/src/server/.log/`，不迁移或删除。新旧日志目录均由 Git 忽略，避免误提交带签名的业务链接；日志采集需同时覆盖历史目录与当前的 `server/.log/`。

调度异常同时输出到服务端日志，并独立保存最近一次诊断到 `server/.log/video_composition/dispatch-error.json`（原子覆盖、不依赖数据库、写入失败不停止重试）。它不归属某个任务；任务内错误仍记录到对应模块的 `error_log`。数据库诊断保留 MySQL 原始错误码、驱动固定错误名称、连接失效标记和完整调用位置（文件、行号、函数，不含源码或局部变量），连接底层异常另保留类型及系统/TLS 校验错误码，不记录 SQL、参数或驱动原始正文。

新任务执行日志保存到 `.log/video_composition/<task_id>/`，相对根目录固定为 `server/` 项目目录，按源码位置定位，不受启动工作目录影响。每个任务包含下面七个 JSON 文件（如 `asr.json`），时间使用北京时间：

| 文件名（省略 `.json`） | 内容 |
| --- | --- |
| `request` | 原始请求、任务级记录、查询响应和通知请求/响应 |
| `template` | 模板读取输入与完整输出 |
| `asr` | 音频 URL 与阿里返回的完整转写 JSON |
| `segmentation` | 切片输入与输出 |
| `matching` | 输入保存匹配请求；输出保存受理响应、原始回调结果与超时补查结果 |
| `timeline` | 组装输入、提交设置与最终合成请求中的完整 `timeline` 对象 |
| `zos` | 渲染、取址与转存的输入及执行/错误记录；输出只保留两类视频链接 |

每个文件均为 `{"input": [], "output": [], "execute_log": [], "error_log": []}`。输入输出条目使用
`time/action/data`，执行条目严格只有 `time/action/status`，状态沿用该事件发生时的任务状态；错误条目另外保存 `error`。
动作采用 `阶段.事件`，如 `asr.step_finished`；输入输出可按时间和动作定位，重试与查询记录保留。
七模块的新错误（含拒绝、取消及通知重试）在任务目录的 `errors/<随机记录ID>.log` 保存独立的脱敏错误快照，包含时间、动作、状态及当前已捕获的异常链、调用位置、错误码或拒绝原因；没有捕获到的上游信息不会补造。每条错误单独保存，成功事件不生成详细文件，已有记录不补写。对应 `error_log[].error.detail_log_path` 使用相对于该 JSON 所在目录的路径，例如 `errors/abc123.log`；JSON 保留原有诊断字段，不额外嵌入文件正文。详细文件写入失败时仍尝试保存 JSON 错误摘要，不添加无效路径；HTTP 查询和回调不返回该字段。
Remotion 新私有诊断也保存到任务的 `errors/`：异常链为 `remotion-error.log`，worker 输出为 `remotion-worker.log`（存在时保存），路径为 `errors/remotion-error.log`，不重复生成通用错误副本。私有诊断保存失败时，通用错误文件仅保存当前可用的脱敏诊断，不含未保存的渲染器输出。整个 `errors/` 随任务目录计入下述 50 MB 清理；旧 `composition-overlay-failures/` 不迁移、不删除，已有路径仍有效。
任务快照仅补充尚未保存的内容，不重复把输入输出塞入执行记录。`timeline.output` 不保存中间组装的时间线，
只从最终提交请求提取；同模块还保留提交响应和非空组装警告。密钥与回调令牌继续脱敏。

完全相同的对象/数组正文只保存一份，重复处使用同任务内的 JSON Pointer，例如
`{"$log_ref":"#/asr/output/0/data"}` 指向同任务 `asr.json` 的 `output[0].data`，可按路径打开原文。
原始请求和上游输出优先保留；切片输入引用 ASR，组装输入引用模板/切片/匹配结果。
`timeline` 中的 `materials`、`packRules` 始终原文显示，其父对象也不整块引用。仅内容完全相同且引用更短时替换，
不同版本、每次调用的时间/动作及执行/错误记录均保留。`$log_ref` 是日志保留字段，业务 `$ref` 不受影响。
`execution_log.expand_columns` 可完整展开按模块名合并的七文件内容；每次追加先展开再排序、重建引用，避免索引失效。
素材匹配回调按模块视角归 `output`；提交参数和 HTTP 请求
分别保留调用记录，HTTP `body` 引用同一份参数，不重复存正文。
`zos.output` 的 `data` 仅保留 `aliyun_video_url`（阿里云动态视频链接）或 `zos_video_url`（ZOS 视频链接），
同类同址只留一次并直接显示原文；不再保存渲染查询正文、Timeline 或其引用、媒资 ID、时长及对象 key。
动态视频链接保留签名；转存成功以已有转存结果或对象 key 为依据；失败原因继续保存在 `error_log`。
日志归类、正文引用、链接整理、脱敏、异常提取、文件写入与容量清理统一位于 `execution_log.py`。
新事件直接生成七模块内容，不经过 `detail`。不再创建、读写或迁移 `video_composition_logs` 及其备份表；已有表保持原样，可自行删除。
`video_compositions` 业务任务表继续保存恢复所需快照和状态。

业务事务提交后只将独立日志快照入队，由单个后台线程顺序写入；正常入队不等待磁盘，事件时间取入队时刻。
队列最多缓存 128 条，满时提交线程等待空位，不丢弃记录；文件或清理失败报告到运行日志并继续消费，不回滚业务状态。
正常退出先收束任务与提交线程，再写完队列、停止日志线程，最后关闭数据库；进程被强制终止可能丢失尚未落盘的日志。
文件读改写和清理共用单进程锁；先写完七个临时文件，再逐个原子替换 JSON，避免单文件出现半份正文。
数据库与文件之间不保证事务原子性。
每次成功写入后统计整个 `.log` 的文件大小，仅超过 50 MB（50 × 1024 × 1024 字节）时清理：
按任务目录内文件的最新修改时间从旧到新，删除合成与通知均已结束的整个任务目录，直到不超过上限。
超过七天的任务自然优先，没有满七天的也按相同顺序删除；未超容量即使超过七天也保留。
正在执行、等待回调、通知 pending/sending 或仍有日志排队的任务不删；只剩受保护任务或其他日志时允许暂时超限。
不删除 `.log` 内其他业务文件。不设定时器、不在启动时检查、不维护清理目录队列，后台写入后直接统计现有文件。

## 代码结构

- `src/server/database.py`：`DatabaseSettings` 自动加载并校验环境配置，启动时创建缺失数据库，管理 MySQL 连接池。
- `src/server/template/`：模板模块，与用户示例目录 `sub_api/` 平级。
- `src/server/template/router.py`：四个模板接口，向 `app.py` 注册 APIRouter。
- `src/server/template/schema.py`：请求、响应、数值范围与效果组合校验。
- `src/server/template/store.py`：建表、查询和事务写入。
- `src/server/segmentation/`：独立切片函数、`IMV_` 模型配置与 `router.py` 切片路由。
- `sdk_catalog.json`、`motions.json`：来自参考项目的固定 5.2.2 效果白名单；升级 SDK 时同步核对。没有效果目录 API。

首页 `GET /` 和 `GET /users/`、`GET /users/{user_id}` 仍保留示例响应，尚未接入用户存储。

## 验证与打包

常规合成测试会调用 Node 打包封存资产并验证导入边界，需要 Node 24 与仓库指定的 Bun；在 `server/` 下先安装锁定的 Remotion 依赖。浏览器、FFmpeg 和数据库真实集成仍按各测试的开关启用。

```sh
(cd src/server/remotion && bun install --frozen-lockfile)
uv run --locked pytest -v
uv build --out-dir dist
```

维护 `uv.lock`，CI 使用 `--locked` 检查依赖与配置一致。

测试统一放在 `tests/`，使用 pytest；`conftest.py` 管理客户端夹具，
`test_api.py` 覆盖路由契约、边界和错误请求，`test_entrypoint.py` 覆盖两种启动入口。
每个新 feature 都必须补齐正常、异常及适用边界的测试脚本，详细规则见根目录 AGENTS.md。

项目在 `pyproject.toml` 中将官方 PyPI 设为默认索引，与锁文件来源保持一致。
第三方镜像可能尚未同步所需版本，导致 `uv sync` 和 `uv sync --locked` 报
“No solution found”。本机如另有索引覆盖配置，可用以下命令验证：

```sh
uv sync --locked --default-index https://pypi.org/simple
```

先检查实际使用的索引及镜像同步情况，不要仅为绕过镜像缺失而降低依赖版本或删除锁文件。

## 文案切片

`POST /segmentations` 与 `segment` 函数用正确文案和已有 ASR 结果生成带时间和关键词的片段，不调用 ASR。模型连接填在 `server/.env` 的 `IMV_LLM_BASE_URL`、`IMV_LLM_API_KEY`、`IMV_LLM_MODEL`（其余见 [.env.example](.env.example)），环境变量优先。代码中调用：

```python
import json
from pathlib import Path
from server.segmentation import segment

result = segment({
    "title": "示例标题",
    "script": "你好世界。",
    "asr_result": json.loads(Path("asr_result.json").read_text(encoding="utf-8")),
})
```

**请求与返回**：`script`、`asr_result`、`title` 都可省略或为 null。

- 有 ASR：必须有有效 `script`，使用第一音轨的词级时间（毫秒）。返回 `segments`（保留标点的原文、秒制时间、分组、关键词）、`warnings` 和 `trace`；传了 `title` 时另返回 `title_keyword`。
- 只有 `title`：只调用一次模型，返回 `{"title_keyword":"关键词"}`，不校验内容、不纠错，缺失或非字符串按空字符串处理。
- 可选 `config` 只用于本次调用：`llm_base_url`、`llm_api_key`、`llm_model` 必填，`llm_timeout_seconds` 默认 120（切片单次最多取 30 秒），`llm_max_retries` 默认 1（0～3，只用于翻译）；Python 中传 `segment(payload, config=ClientSettings(...))`。`allow_insecure_llm_http` 由服务端决定，错误响应不回显输入。

**模型调用**：使用 `response_format.type=json_object`，先选切点，再提关键词。提词输入为带 `segment_id`、`text` 的片段，模型返回 `{"keywords":{"1":"医院","3":"CT"}}`，只列有关键词的 ID；代码按 ID 填回，缺失、不在对应原文中连续出现或超过 12 字的词置空。`title_keyword` 须在标题中连续出现且最多 12 字。JSON、结构、ID 或标题词不合法（含回复缺少 `choices`/`message`/`content`）时，把上次回复与错误反馈给模型重试（空回复只反馈错误），切分共 2 次、提词共 3 次，耗尽返回 502。单次请求最多 30 秒并关闭 SDK 网络重试；HTTP 与合成共用 90 秒软预算（不含排队、ASR 和翻译），在调用前后检查，耗尽返回 504，无法打断进行中的请求。次数、超时与预算是模块内具名常量。

**切分规则**：模型只能在中文标点处、两侧至少 2 秒的候选边界上选切点；之后代码把超过 8 个有效字（不计标点和空白）的片段继续切开，切点优先两侧至少 2 字，再按标点、结巴词边界、中点。`OpenAI`、`3.14%`、`1,299` 这类英文和数字串不拆开；`70，80，90后`、顿号枚举、`第1、2、3名`、`等2，3天` 这类数字列表整体保留，普通逗号两侧的数字（如 `99，3件`）不合并。关键词只留在完整包含它的子段上。字幕去标点由下游合成负责，保留问号和数字小数点。

**英文翻译**：`translate(result, title=None)` 一次模型调用把切片结果译成英文（双语视频合成在切片后调用），只读服务端配置。返回副本，片段增加 `text_en`、`keyword_en`，传入 `title` 时增加 `title_en`、`title_keyword_en`；英文关键词复用中文校验但不限长度（片段词不在译文中置空，标题词不在英文标题中报错）。段数、类型不符或译文为空时直接报错，不重试。双语模板以文字对象的 `language=en` 区分，`enabled=false` 的英文对象不翻译、不渲染；英文字幕与展示关键词沿用中文字幕去标点规则（另保留英文词内撇号和空格换行），译文不进入素材匹配。翻译失败以 `translation_failed` 结束任务，中断后不自动重放，译文随任务快照恢复并写入 `segmentation.json`。

**日志**：INFO 只记录阶段、尝试次数、耗时与片段数；完整模型回复和成功 `trace` 脱敏后写 DEBUG；纠错 WARNING 只记录阶段、次数与错误；最终失败 WARNING 保留脱敏诊断和最后一次回复。提词失败的 `keyword_response`（最后回复、预期片段数、关键词类型与数量）由视频合成写入任务 `segmentation.json` 与 `errors/*.log`，接口不回显。DEBUG 与失败详情可能含业务文案，按敏感数据管理；桌面日志位于 `backend/server.log`。

`GET /api/settings/plugins` 只返回自动发现的公开描述与代码默认值，不读写服务端配置。一级模块通过 `settings_plugin.py` 导出 `SETTINGS_PLUGIN`，Schema 由自己的配置模型生成；公共层导入时扫描一次，无人工注册名单，重复 ID、坏描述和依赖导入失败直接报错。增删入口后重启后端并重新打开客户端设置；移除入口不删除客户端旧值。当前包含 Remotion Agent、上海 IMS/视频合成、切片、ASR 和服务启动入口；完整目录从入口的 `settings` 模型类生成 Debug Schema；`client_only=true` 只返回标记 `scope: "client"` 的 Agent 与 IMS，测试使用临时包文件验证发现与移除。此机制不自动注册业务路由，也不支持删除整个业务目录后免处理依赖；各模块仍须自行接入请求配置消费。

## ASR 音频转写

ASR 提供独立的 Python 异步函数和命令行入口，尚未接入 FastAPI 路由。Debug 内置后端启动时加载客户端保存的 ASR 密钥，供转写与视频合成使用；独立或远程后端仍用自身环境配置。在 `server/` 下准备配置；已有 `.env` 时直接补充 `DASHSCOPE_API_KEY`，保留数据库与切片配置：

```sh
cp .env.example .env
```

填写北京地域的 `DASHSCOPE_API_KEY`；服务地址在 ASR 模块中固定为
`https://dashscope.aliyuncs.com/api/v1`。真实 `.env` 已被 Git 忽略。
字段声明位于 `asr/settings.py`，设置发现只生成 Schema；`asr/asr.py` 业务模块加载时自动读取一次固定的 `server/.env`，文件不存在时不回退到工作目录。
环境变量优先于文件，修改配置后需重启进程。

在 `server/` 下运行：

```sh
uv run --locked python -m server.asr "https://example.com/audio.wav"
```

替换为可被云服务访问且不含用户名或密码的 HTTPS 音频直链；结果下载地址也遵守这一限制。
省略地址时显示用法并退出，使用 `--help` 查看帮助。结果写入当前目录的 `asr_result.json`，
覆盖同名文件；保留原始 JSON 和字词时间戳，不额外分词。在异步函数中使用 `await` 调用：

```python
from server.asr import transcribe

result = await transcribe("https://example.com/audio.wav", wait_seconds=1800)
```

同步脚本入口可使用 `asyncio.run(transcribe(audio_url))`；已有事件循环中使用 `await`。
HTTP 请求与轮询等待均为异步，不阻塞事件循环。等待预算必须是有限正数。
超时或取消本地协程不会取消已提交的云端任务；函数不自动重试提交。
轮询休眠不超过剩余预算，但单次 HTTP 请求可能使实际等待超出预算。


### 客户端 Agent / IMS 凭据

普通与 Debug 客户端均可保存独立模型与 IMS 参数。Remotion 模型操作使用 `X-Remotion-Config`，合成 POST 和成片 GET 使用 `X-IMS-Config`；值为 URI 编码 JSON，字段分别来自模块 `ClientSettings`。省略请求头沿用 `.env`，提供时按任务覆盖全部可编辑字段；未知字段忽略，服务端策略不可覆盖，字段解析错误不回显输入。两种请求头已纳入本地客户端 CORS。

凭据仅留任务内存，不保存到作品、任务正文、数据库或日志。IMS 任务只保存是否使用客户端配置的标记；GET 使用本次凭据刷新成片地址，访问权限交给云服务；后台通知使用提交时的快照，任务及通知结束后清理。服务重启后未完成的客户端 IMS 任务缺少快照，以 `client_config_missing` 标记为不可恢复并要求重新提交，不会改用服务端账号；已完成任务在已缓存地址有效时仍可通知，地址未取得或已过期则通知直接失败，不携带请求头的 GET 仍返回可重试的 503。Remotion 重启中断后重试重新携带当前配置。ASR、切片、素材匹配及执行环境读取启动配置。Debug 内置服务在业务导入前从本客户端 `data/settings/settings.json` 加载各入口声明的字段，覆盖进程环境；高级配置保存后重启客户端生效，不修改 `.env`，数据库不参与。启动端口定义位于 `startup/settings.py`，内置服务未保存端口时仍自动分配；路径留空使用随包默认，相对路径以应用 `backend/` 为基准。手动启动及远程后端不会加载客户端文件。详见 [客户端设置说明](../client/src/features/settings/README.md)。


视频合成支持同一 `styleId` 下的 Remotion 动态字效；新 `text_target` 槽位沿用上游的 IMS 文字替代规则，旧独立用途快照继续兼容。保存接口接受仅有 `editor.remotion.businessTarget=title/subtitle` 的旧字效轨道，替换资产仍保留 IMS 与 Remotion 共存，不自动迁移为新槽位。使用方式、资产字段声明、旧资产适配、时长限制和联调清单见 [Remotion 接入说明](../docs/remotion-video-composition.md)。

合成侧计划、时长适配打包、渲染或上传失败会在 `server/.log/video_composition/<task_id>/errors/` 私有保留 `remotion-worker.log` 与异常链 `remotion-error.log`（含子进程 stderr），临时视频和编译产物仍清理；公开错误保持稳定摘要，执行日志记录根因的异常类型、代码位置、错误码及详细日志相对路径，排障时按 `timeline.json` 的 `error_log[].error.detail_log_path` 读取私有日志。
