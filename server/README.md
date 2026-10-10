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
仓库根目录可执行 `uv run --locked --project server server`。首次模板请求自动创建缺失的 `templates` 表，不执行旧数据迁移。
应用启动后数据库暂时不可用时，模板接口返回 503，恢复后可重试；首页和用户示例接口本身不查询数据库。

`uv run server` 与 `uv run python -m server` 均读取固定的 `server/.env` 中的 `PORT`，进程环境变量优先。未设置时默认 `20070`；端口须为 1～65535 的整数，空值、非整数或越界值会阻止启动。修改后重启服务。
客户端 API 地址同样默认 `http://localhost:20070`；若 `client/.env` 已配置其他 `VITE_API_URL`，需同步修改并重启前端（生产环境重新构建）。

```sh
PORT=8010 uv run --locked server
```

本机 Python 包镜像若落后于锁定版本，可以在 uv 命令中添加 `--default-index https://pypi.org/simple`，无需降级项目依赖。

## 模板接口

客户端云端模板在现有 HTTP 地址上传输 Protobuf 二进制消息。协议定义在仓库根目录 `proto/`，Python 生成代码位于 `src/generated/imv/template/v1/`，与客户端生成目录结构一致。

模板接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/template` | 返回 `ListTemplatesResponse`，按更新时间倒序 |
| POST | `/template` | 接收 `SaveTemplateRequest`；无 `template_id` 创建，携带 ID 完整更新，返回 `SaveTemplateResponse` |
| GET | `/template/{template_id}` | 返回 `GetTemplateResponse` |
| DELETE | `/template/{template_id}` | 删除模板，成功返回 204 |

GET 成功响应与 POST 请求、成功响应均使用 `application/x-protobuf`。POST 的 JSON 请求返回 415；损坏的 Protobuf 消息返回 400。创建返回 201，更新返回 200。名称重复返回 409，模板不存在返回 404，非法 ID 或配置返回 422。错误响应仍使用 JSON。
重命名使用携带 ID 的 POST；另存为使用不携带 ID 的 POST。不存在的 ID 不会自动变成创建。

`SaveTemplateRequest.tracks` 必须提供，通过 `TrackList.tracks[].editor` 保存参数；缺省参数由业务校验补齐。模板更新完整替换配置。名称去除首尾空白后为 1～100 字符；说明最多 1000 字符；标题、字幕、气泡示例文字最多 60、100、40 字符；字号 12～300 整数；位置 0～100%；动画和转场时长 0.1～3 秒。

顶部标题对象的 `titleKeywordBold`、`titleKeywordItalic`、`titleKeywordUnderline`、`titleKeywordStrikeout` 可组合使用，`titleKeywordColor` 设置局部颜色，`titleKeywordSize` 设置局部字号；合成时优先在请求标题中查找已有的 `titleKeyword`，空值时自动选取请求标题首段连续文字的前两个字，只标记首次出现的位置。底部字幕对象独立保存 `subtitleKeywordBold`、`subtitleKeywordItalic`、`subtitleKeywordUnderline`、`subtitleKeywordStrikeout`、`subtitleKeywordColor` 和 `subtitleKeywordSize`，作用于原切片中首次包含 `keyword` 的字幕短句，空关键词保留原文。颜色为空字符串或 `#RRGGBB`，空字符串表示保持文字原色；合成时转换为 IMS 要求的 BGR 顺序。局部字号为 0（沿用原字号）或 12～300 的整数，合成时使用 IMS `\fs` 指令设置并恢复。模板响应在对应对象的 `tracks[].editor` 返回这些值。 视频合成按气泡对象自身时间区间显示其 `bubbleText` 示例文字一次，不使用切片关键词；有气泡对象但未选气泡样式时仍显示文字，无气泡对象则不生成。关键词继续通过标题和字幕的局部样式强调。

独立对象通过 `tracks` 保存，每项包含 `id`、`target`、`start_mode`、`start`、`duration` 和 `editor`。`start_mode` 支持 `seconds` 和 `percent`；百分比范围为 0 至小于 100，`duration` 为正秒数或 `null`（持续到视频结束）。模板不保存视频信息，保存校验不依赖预览时长。应用视频时按输出帧率计算区间：结尾以外不显示、结束越界时截短，动画按有效帧数缩短并记录说明，无法容纳所选动画时明确失败。文案合成逐个应用对象，标题使用请求文字，字幕和关键词采用文案时间与对象区间的交集；合成时转场忽略模板开始与持续时间，仅取特效类型并应用于实际素材边界，音频总长保持不变。
同一文字角色的循环动画与入场、出场互斥。效果可以为空（只放 Remotion 片段的模板），最多 500 个不同效果 ID。

响应补充 UUID、UTC 创建/更新时间和 `effects` 参数快照。服务端通过固定 SDK 5.2.2 白名单解析效果，
拒绝未知 ID、错误分类和全部 `tracks[].editor` 中的效果与 `effect_ids` 不一致；客户端不能提交渲染参数。
`schema.py` 负责业务字段校验，`router.py` 将生成的 Protobuf 消息转换为业务模型并生成响应。

MySQL 单独列保存唯一名称、ID 和时间，JSON 保存完整编辑配置与效果快照。
保存和删除使用事务；同时保存同名新模板仅一个成功。同时编辑同一个模板时，后一次成功保存覆盖前一次完整配置。

## 异步视频合成

所有模式均接受 `packRules.backgroundMusic: null`，表示不使用背景音乐，等同于省略该字段或传 `audioSwitch: false`。

统一入口根据现有字段自动确定内部模式：有 `videoUrl` 为 standard；没有 `videoUrl`、有顶层 `audioUrl` 为 materials_voice；两者都没有为 materials_silent。地址缺省或 null 均视为没有，背景音乐不参与分流；请求不包含 `compositionMode`。standard 省略 `materials` 为纯数字人，显式数组才匹配；两个纯素材模式要求非空素材数组，严格按顺序拼接且不调用匹配。视频使用 FFprobe 获取实际视频流时长，图片每张 3 秒，超长裁尾、不足异步失败。非空 `text` 优先，否则读取 `copy`；有语音以 ASR 配音总长合成，有文案才切片生成字幕。无语音以 `processRules.videoDuration`（有限正数秒，可为小数，不接受布尔值或数字字符串）合成，始终无字幕，`title` 使用模板标题样式及动画并全程显示。纯素材缺少文字对象时回退内置默认样式，正文仍取请求，不保存回模板。素材原声静音，背景音乐仍可选。无语音不依赖 ASR／切片／匹配配置；纯素材含视频时 PATH 需有 `ffprobe`；探测白名单包含 HTTP 代理所需的 `httpproxy`，失败的退出码与脱敏诊断写入 `timeline.json`。字段矩阵和完整请求示例见 [视频合成 API 文档](src/server/video_composition/api.md#三种模式)。

standard 模式中，请求不包含 `materials` 时为纯数字人：跳过素材匹配，以 `audioUrl` 的 ASR 原始总时长铺满数字人视频，字幕继续按文案切片和模板时间规则显示。显式 `materials: []` 保留素材匹配但不指定候选，非空数组携带候选匹配，`null` 返回 422。需要保留此前缺省字段也匹配的行为时，调用方须改为显式传入 `[]`；已有任务继续按已保存的请求和阶段恢复。

视频时长优先读取首个视频轨的 `duration`；缺失或为 `N/A` 时读取该轨 `DURATION` 标签（`HH:MM:SS.小数`），普通标签缺失时兼容 `DURATION-eng`、`DURATION-ZH` 等两至三字母语言后缀，标签名不区分大小写，扣除视频轨起始偏移后仍须为有限正数。真实媒体测试覆盖 MP4、WebM（VP8/VP9）和 MKV，并检查音轨更长、语言标签与视频起点偏移的情况。不使用容器或音轨时长代替；两种视频轨时长信息均不可用的素材（如部分 FLV）仍异步报 `material_probe_failed`，具体原因写入 `timeline.json`。测试环境有 FFmpeg/FFprobe 时运行真实样本，无工具时跳过；后端集成工作流安装工具并执行这些用例。

`SEGMENT_MATCH_BASE_URL` 在环境变量、`.env` 或 Debug 桌面设置中留空或仅含空白，均视为未配置。只有 standard 显式传入 `materials` 时必须提供有效匹配地址，否则受理返回 503；纯数字人和两个纯素材模式不要求该配置。非空非法地址仍拒绝加载。

`POST /api/v1/video-compositions` 创建任务，HTTP 200 响应为 `{"code":200,"message":"操作成功","data":"任务ID"}`；`GET /api/v1/video-compositions/{taskId}` 查询结果，查询结构保持不变。终态回调仅含 `taskId/status/videoUrl/errorMessage`：成功为 `succeed`、视频直链、null 错误；失败为 `failed`、null 地址、错误摘要。回调 ID 与创建响应的 `data` 一致，内部和查询的成功状态仍为 `succeeded`。模板按 `tracks[].editor` 读取，标题取请求 `title`，关键词取原切片；字幕使用切片原文与时间，由合成时间线保留中英文问号并去除其他标点；已有快照中的 `subtitle_parts` 仍可读取。素材匹配仍接收原切片文字与时间，结果的数量、编号、顺序及起止时间严格校验，文字仅允许首尾空白差异；字幕继续取本地切片，原始匹配回执保留在脱敏日志中。标题和字幕的模板示例文字不进入成片，气泡对象使用其模板示例文字。

创建请求的字段校验错误返回 HTTP 422，响应含 `{"code":422,"message":"请求参数无效","data":null}`；客户端配置头错误及其他错误沿用原有格式。后台合成失败通过查询结果的 `status: failed` 和 `error` 表示。

视频和图片片段以 `Contain` 方式放入输出画布，保留素材原始宽高比和完整画面；比例不同时使用素材的模糊背景填充留白。已有成片不会自动重新渲染。

云端渲染返回 `Success` 后仍保持 `processing/rendering`，在原渲染截止时间内获取 IMS 临时地址并下载成片。FFmpeg 按解码顺序截取第 3 帧，视频与 PNG 分别上传至 ZOS 的 `imv/video_composition/{taskId}.mp4` 和同名前缀的 `.png`；两个对象单独设置 `public-read`，确认大小及匿名读取后才保存 `succeeded` 和待通知状态。PNG 可通过 `ZOS_WEB_URL/imv/video_composition/{taskId}.png` 访问，不进入 GET 或回调；两者仍只返回视频地址。取址超时为 `playback_timeout`，转存持续失败为 `zos_upload_timeout`，均不重提渲染。历史成功任务不补图，GET 仍按原规则返回地址。

提供 `callbackUrl` 时，终态以 POST JSON 通知，任意 2xx 表示送达；非 2xx、网络错误和超时均在失败后按 5、15、45 秒间隔重试，最多四次，不跟随重定向。次数和下次投递时间落库，等待中的重试可在重启后继续；新成功任务的通知不再依赖 IMS 凭据，历史成功任务在重启后缺少客户端 IMS 凭据且需要重新取址时仍会直接记为通知失败。通知失败不回退合成终态，接收方须按 `taskId` 幂等处理。沿用现有恢复边界：发送中进程退出或送达状态保存失败留下的 `sending` 不自动重放，调用方通过 GET 补查。

`COMPOSITION_MATCH_WAIT_SECONDS` 默认 30 秒，匹配回调未到则只主动查询一次。修改配置后重启；已有任务保留其已保存的截止时间，失败历史通知不自动重新发送。

新任务还需在 `server/.env` 中设置 `ZOS_API_ENDPOINT`、`ZOS_BUCKET`、`ZOS_ACCESS_KEY_ID`、`ZOS_SECRET_ACCESS_KEY`、`ZOS_WEB_URL`，并确保 `ffmpeg` 在服务端 PATH 中；缺少 FFmpeg 时受理返回 503。`ZOS_REGION` 默认 `hangzhou-7`，`ZOS_FORCE_PATH_STYLE` 默认 false。密钥仅由服务端读取，不进入客户端 IMS 设置。上传与公开地址分别使用 API Endpoint 和 Web URL；目前不读取 `ZOS_ENDPOINT`。对象删除或桶生命周期清理后，公开 URL 也会失效。字段示例和完整接口见 [视频合成 API 文档](src/server/video_composition/api.md)。

### 执行日志

历史日志仍保留在 `server/src/server/.log/`，不迁移或删除。新旧日志目录均由 Git 忽略，避免误提交带签名的业务链接；日志采集需同时覆盖历史目录与当前的 `server/.log/`。

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

```sh
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

提供 `POST /segmentations` 接口和独立的 `segment` 函数，使用正确文案与已有 ASR 结果生成带时间和关键词的片段。

在 `server/.env` 填写 `IMV_LLM_BASE_URL`、`IMV_LLM_API_KEY` 和 `IMV_LLM_MODEL`，其余配置见 [.env.example](.env.example)。
配置读取固定的 `server/.env`，环境变量优先；从仓库根目录启动时使用：

```sh
uv run --locked --project server server
```

HTTP 请求体必填 `script`（正确文案字符串）和 `asr_result`（Fun-ASR 原始结果对象），可选 `title`（标题字符串，可省略或为 null），由 Pydantic 校验字段类型。也可在代码中读取 ASR 输出文件并调用：

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

使用 ASR 第一音轨的词级时间（毫秒），返回 `segments`、`warnings` 和 `trace`；片段包含保留标点的原文、秒制时间、分组和关键词。
提供非 null 的 `title` 时，在现有关键词模型调用中同时提取一个标题关键词，响应新增同级 `title_keyword` 字符串；关键词须在标题中连续出现且最多 12 字。标题为空或无合适关键词时由模型返回空字符串；省略或传 null 时不返回此字段。模型结果缺失或非法直接报错，不增加兜底或额外重试。
首次切分和关键词提取后，超过 8 个有效字（不计标点和空白）的片段批量进行语义切分。二次切分结果的 JSON、切点、子段字数或关键词完整性校验失败时，将失败输出和具体错误反馈给模型，最多修正一次；再次失败直接报错，全部通过后才应用结果。网络错误沿用 SDK 策略，不触发此校验重试。
切片不调用 ASR，不生成字幕子段；最终去标点由下游合成负责。

HTTP 与视频合成调用均在 FastAPI 终端记录阶段、切点、关键词与模型耗时，桌面日志写入 `backend/server.log`。
日志包含文案，请按敏感数据管理；请求字段校验失败仍返回 422，不进入切片日志。

请求可另带 `config` 对象：`llm_base_url`、`llm_api_key`、`llm_model` 必填，`llm_timeout_seconds` 默认 120，`llm_max_retries` 默认 1（0～3）。客户端参数仅用于该次切片，完整连接参数不与服务端密钥混用；省略 `config` 仍使用原服务端配置。独立 Python 调用可传 `segment(payload, config=ClientSettings(...))`，模型定义位于 `server.segmentation.settings`。`allow_insecure_llm_http` 仍由服务端决定，不接受客户端覆盖；错误响应不回显请求输入。

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
