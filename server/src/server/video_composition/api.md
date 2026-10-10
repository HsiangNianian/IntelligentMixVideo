# 视频合成接口文档

本文按当前 `router.py`、`schema.py`、`service.py` 实现整理，更新日期：2026-10-09。

## 1. 接口概览

基础地址示例：`http://服务器IP:20070`。JSON 请求使用 `Content-Type: application/json`。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/v1/video-compositions` | 创建异步合成任务 |
| GET | `/api/v1/video-compositions/{taskId}` | 查询状态和成片地址 |
| POST | `/api/v1/video-compositions/{taskId}/segment-match-callback?token=...` | 素材库向 IMV 回传匹配结果 |

当前模块没有任务列表、取消、删除、重试或重新发送最终通知接口。创建接口没有调用方幂等键，重复 POST 会创建新的任务；响应丢失时不要盲目重复提交。

业务调用流程：创建任务 → 保存返回的 `taskId` → 接收最终通知或查询任务 → 取得视频地址。后台根据 `videoUrl` 和顶层 `audioUrl` 自动判断模式，准备素材与文字，共用模板、IMS、ZOS、查询和通知。`compositionMode` 仅为内部概念，不属于请求字段。

## 2. 创建任务

`POST /api/v1/video-compositions`

### 请求字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `text` / `copy` | string/null | 按模式 | 非空 `text` 优先；缺省、null、空串或全空白时回退 `copy`，最多 20000 字符；内部统一保存为 `text` |
| `videoUrl` | string/null | standard | 数字人视频 HTTP(S) 直链；提供时走原数字人流程，纯素材必须省略或传 null |
| `audioUrl` | string/null | standard / materials_voice | 配音 HTTPS 直链，供 ASR 获取完整音频时长和合成；无数字人视频时由此字段决定有无语音，无语音必须省略或传 null |
| `styleId` | string(UUID) | 是 | 已保存的云端模板 ID；后台从服务端模板存储读取 |
| `title` | string/null | 否 | 标题；省略、null 或全空白时不生成标题 |
| `materials` | array | 纯素材必填 | 纯素材必须非空、按数组顺序播放；standard 省略为纯数字人，显式 `[]` 匹配但不指定候选，非空数组指定候选；不接受 null |
| `packRules` | object | 否 | 当前仅 `backgroundMusic` 生效 |
| `processRules.videoDuration` | number | materials_silent | 秒制有限正数，允许小数，不接受布尔值或数字字符串；其他模式不使用，可省略或为 null |
| `callbackUrl` | string/null | 否 | IMV 向业务系统发送最终结果的 HTTP(S) 地址；省略或 null 不通知 |

纯数字人模式仍对 `audioUrl` 执行 ASR，以原始音频总时长确定时间线；字幕由输入文案与 ASR 对齐后的 segmentation 切片确定，并受模板字幕对象显示区间限制。模板效果、背景音乐、IMS 渲染、ZOS 视频与 PNG 转存、查询和终态通知沿用相同流程。此前省略 `materials` 也会匹配；需要保留该行为的调用方应显式传入 `[]`。已有任务按保存的请求字段和阶段恢复，不重新解释原始请求日志。

`materials[]` 字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `fileUrl` | string | 是 | 素材 HTTP(S) 直链 |
| `type` | string | 是 | `video` 或 `image` |

`packRules.backgroundMusic` 接受对象或 null；省略、传 null 或 `audioSwitch: false` 均表示不使用背景音乐。对象字段如下：

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `audioSwitch` | boolean | false | 是否启用背景音乐 |
| `audioUrl` | string/null | null | 开启时必须提供 HTTP(S) 音频直链 |
| `volume` | number | 0.1 | 音量，范围 0～1 |

URL 不接受 Markdown 链接、空白、用户名密码或 `#fragment`，允许查询参数。媒体需要可被实际处理服务访问；URL 格式通过不代表媒体可用。

`headerSwitch`、`keywordSwitch`、`materialSwitch`、`subtitleSwitch`、`processRules.watermarkShow`、`introduceCard`、`materialSoundSwitch` 等额外字段目前被忽略，不控制输出。正式请求推荐使用本文列出的 camelCase 字段。

### 三种模式

以下名称仅在接口内部使用，无需传入。提供 `videoUrl` 时为 `standard`；没有 `videoUrl`、提供顶层 `audioUrl` 时为 `materials_voice`；两者都没有时为 `materials_silent`。地址缺省或 null 均视为没有，`packRules.backgroundMusic.audioUrl` 不参与判断。

- `standard`：仍需非空文案、数字人视频和配音。省略 `materials` 跳过匹配，数字人铺满音频；显式数组保留匹配流程。保存请求时保留 `materials` 缺省状态，旧任务依据已保存字段恢复，不重新解释原始日志。
- `materials_voice`：ASR 获取配音原始总时长（含静音）；有文案才进行字幕切片，没有文案不生成字幕。按素材顺序铺满完整配音，字幕间隙不影响画面，不调用素材匹配。
- `materials_silent`：以 `processRules.videoDuration` 为总长，跳过 ASR、切片和匹配；始终不生成字幕，即使请求带了 `text/copy`。请求 `title` 使用模板标题样式，显示区间覆盖为 `0～videoDuration`，保留原文换行、标点和模板花字、关键词及动画。标题为空就不显示。

两个纯素材模式均使用后端 FFprobe 读取视频流实际时长，图片每张 3 秒。按顺序探测至足够覆盖目标，不访问后续未使用素材；不足报 `materials_duration_insufficient`，视频无法读取或缺少有效视频流时长报 `material_probe_failed`，不拿可能更长的容器音轨时长代替。POST 先返回任务 ID，以上错误通过查询与终态回调报告，不提交 IMS。超长时裁切最后实际使用的素材（图片也可裁短），相等则完整使用，不循环补足。素材时长快照保存后，恢复组装不再探测。

纯素材缺少标题／字幕对象时，仅为本次合成补默认样式：标题 40 px、位置 50% / 8%；字幕 26 px、位置 50% / 82%；阿里巴巴普惠体、白色，无花字或动画。文字仍来自请求及切片，不使用模板示例文案、不修改模板存储。已有对象沿用原样式，除无语音标题外继续应用原时间规则；没有业务文字不补对象。其余模板效果和转场沿用，转场不缩短目标总长。

素材原声全部静音。有语音模式播放配音及可选背景音乐，无语音模式只播放可选背景音乐；关闭背景音乐则无语音成片完全静音。背景音乐开启时空 URL 返回 422。所有模式沿用输出规格及 ZOS 路径，GET 返回 IMS 实际成片时长。

### 请求示例

示例中的模板 ID 和媒体地址必须替换成实际可用值。

```bash
curl -X POST 'http://127.0.0.1:20070/api/v1/video-compositions' \
  -H 'Content-Type: application/json' \
  -d '{
    "text": "这是第一句话。这是第二句话。",
    "videoUrl": "https://media.example.com/background.mp4",
    "audioUrl": "https://media.example.com/narration.wav",
    "styleId": "11111111-1111-4111-8111-111111111111",
    "title": "示例标题",
    "materials": [
      {"fileUrl": "https://media.example.com/scene.jpg", "type": "image"}
    ],
    "packRules": {
      "backgroundMusic": {
        "audioSwitch": true,
        "audioUrl": "https://media.example.com/music.mp3",
        "volume": 0.1
      }
    },
    "callbackUrl": "https://business.example.com/video-result"
  }'
```

纯素材有语音示例（标题可选，删除 `copy` 则不生成字幕）：

```json
{
  "styleId": "11111111-1111-4111-8111-111111111111",
  "videoUrl": null,
  "audioUrl": "https://media.example.com/narration.wav",
  "title": "产品介绍",
  "copy": "这是用于配音对齐的文案。",
  "materials": [
    {"fileUrl": "https://media.example.com/scene.mp4", "type": "video"},
    {"fileUrl": "https://media.example.com/product.jpg", "type": "image"}
  ],
  "callbackUrl": "https://business.example.com/video-result"
}
```

纯素材无语音示例（每图 3 秒，目标 4.5 秒，第二张只显示 1.5 秒）：

```json
{
  "styleId": "11111111-1111-4111-8111-111111111111",
  "title": "产品介绍\n保留多行标题！",
  "processRules": {"videoDuration": 4.5},
  "materials": [
    {"fileUrl": "https://media.example.com/first.jpg", "type": "image"},
    {"fileUrl": "https://media.example.com/second.jpg", "type": "image"}
  ],
  "packRules": {"backgroundMusic": {"audioSwitch": false}},
  "callbackUrl": "https://business.example.com/video-result"
}
```

### 受理响应

HTTP **200 OK**，响应头包含：

```text
Location: /api/v1/video-compositions/22222222-2222-4222-8222-222222222222
```

```json
{
  "code": 200,
  "message": "操作成功",
  "data": "22222222-2222-4222-8222-222222222222"
}
```

200 只表示已保存任务，不表示合成成功。模板不存在、媒体读取失败等后台错误通过任务结果返回。创建请求的字段校验错误返回 HTTP 422，结构如下；客户端配置头错误及其他错误沿用原有响应格式。

```json
{"code":422,"message":"请求参数无效","data":null}
```

## 3. 查询任务

`GET /api/v1/video-compositions/{taskId}`

```bash
curl 'http://127.0.0.1:20070/api/v1/video-compositions/22222222-2222-4222-8222-222222222222'
```

### 响应字段

| 字段 | 说明 |
| --- | --- |
| `taskId` | IMV 本地合成任务 ID |
| `status` | `queued`、`processing`、`succeeded`、`failed` |
| `stage` | 当前阶段，见下表 |
| `result` | 成功时为成片对象，其余状态为 null |
| `result.videoUrl` | 新任务返回已转存 ZOS 的公开直链；历史成功任务仍返回 IMS 临时地址，过期后可重新查询 |
| `result.durationSeconds` | 成片实际时长，单位秒 |
| `error` | 失败时包含 `code`、`message`、`stage`，其余状态为 null |
| `createdAt` / `updatedAt` | 带时区的 ISO 8601 时间，当前使用北京时间 `+08:00` |

| stage | 含义 |
| --- | --- |
| `queued` | 排队 |
| `template` | 读取模板 |
| `asr` | 语音识别 |
| `segmentation` | 文本切片及时间对齐 |
| `matching` | 等待素材匹配结果 |
| `assembling` | 组装合成时间线 |
| `submitting` | 提交 IMS 合成 |
| `rendering` | 查询 IMS 合成进度，成功后继续等待成片地址 |
| `completed` | IMS 合成及 ZOS 转存成功 |
| `failed` | 任务失败；具体失败环节见 `error.stage` |

成功示例，HTTP **200**：

```json
{
  "taskId": "22222222-2222-4222-8222-222222222222",
  "status": "succeeded",
  "stage": "completed",
  "result": {
    "videoUrl": "https://media.example.com/output.mp4",
    "durationSeconds": 30.4
  },
  "error": null,
  "createdAt": "2026-09-22T10:00:00+08:00",
  "updatedAt": "2026-09-22T10:02:00+08:00"
}
```

失败示例，同样返回 HTTP **200**：

```json
{
  "taskId": "22222222-2222-4222-8222-222222222222",
  "status": "failed",
  "stage": "failed",
  "result": null,
  "error": {
    "code": "matching_timeout",
    "message": "回调等待超时，单次补查仍未完成",
    "stage": "matching"
  },
  "createdAt": "2026-09-22T10:00:00+08:00",
  "updatedAt": "2026-09-22T10:30:00+08:00"
}
```

任务不存在返回 404；UUID 非法返回 422；历史成片已成功但暂时无法获取 IMS 播放地址返回 503，此时可重试 GET，任务不会被改成失败。查询本地任务不会主动触发素材匹配补查。

## 4. IMV 向业务系统发送最终通知

创建任务时提供 `callbackUrl` 后，IMV 在任务成功或失败落库后尝试向该地址发送 **POST JSON**。回调固定为以下四个字段，`taskId` 与创建响应的 `data` 一致：

```json
{
  "taskId": "22222222-2222-4222-8222-222222222222",
  "status": "succeed",
  "videoUrl": "https://media.example.com/output.mp4",
  "errorMessage": null
}
```

失败时 `status` 为 `failed`、`videoUrl` 为 null，`errorMessage` 为可公开的错误摘要。成功回调使用 `succeed`，数据库与 GET 查询仍使用 `succeeded`；GET 查询结构不变。视频地址为普通 URL 字符串，不包含 Markdown 链接格式。

- 接收方返回任意 **2xx** 表示成功，不要求特定响应体。
- 不跟随重定向；非 2xx、连接失败或超时视为通知失败。
- 首次失败后按 5、15、45 秒间隔额外重试三次，最多四次；通知失败不改变合成成功/失败状态。接收方按 `taskId` 幂等处理。
- 单次 HTTP 默认超时 30 秒。新任务在 IMS 渲染、ZOS 上传校验和匿名读取验证全部成功后才保存 `succeeded`，成功通知和 GET 复用同一个 ZOS 地址；历史成功任务仍按原规则获取 IMS 地址。
- 尝试次数和下次投递时间落库，等待重试可在重启后继续；发送中中断或送达状态保存失败留下的 `sending` 沿用不自动重放的恢复边界，由调用方 GET 补查。
- 未收到通知时，通过 GET 查询结果。HTTP 查询失败与任务业务失败需要分别处理。
- 通知状态记录在服务端数据库/日志中，公开任务响应不包含 `notification_status`。
- 当前实现不附加自定义鉴权头或签名。

`callbackUrl` 与 `COMPOSITION_PUBLIC_BASE_URL` 不同：前者是 **IMV → 业务系统**，后者用于 **素材库 → IMV**。

## 5. 素材库向 IMV 回调匹配结果

此接口供素材库集成使用，普通业务调用方无需自行调用。

`POST /api/v1/video-compositions/{taskId}/segment-match-callback?token={token}`

路径 `taskId` 是 **IMV 本地任务 ID**；JSON 中的 `taskId` 是 **素材库返回的上游任务 ID**，两者不能混用。IMV 自动生成带 token 的完整 URL，作为 `callback_url` 传给素材库，素材库应原样使用。

成功回调示例：

```json
{
  "taskId": "33333333-3333-4333-8333-333333333333",
  "status": "success",
  "result": {
    "segments": [
      {
        "segment_id": 1,
        "text": "这是第一句话。",
        "start_time": 0.0,
        "end_time": 2.5,
        "matched_candidate_url": "https://media.example.com/scene.jpg",
        "matched_candidate_type": "image"
      }
    ]
  }
}
```

`segments` 必须非空，片段数量、顺序、编号、起止时间必须与 IMV 提交的切片严格一致。文字仅允许首尾空白差异，正文、标点和内部空白必须一致；回调与超时补查共用此规则。合成字幕继续使用本地切片原文及时间，匹配服务的原始回执保留在 `matching.json`，沿用日志脱敏规则，不用校验后的文字覆盖回执。命中时必须提供 HTTP(S) 素材 URL 和 `video`/`image` 类型；未命中时对应 URL 和类型可为 null。

失败回调：

```json
{"taskId":"33333333-3333-4333-8333-333333333333","status":"failed","result":null}
```

成功接收返回 HTTP 200：`{"status":"ok"}`。有效的重复或迟到回调确认收件，不重复渲染。

| HTTP 状态 | 含义 |
| --- | --- |
| 403 | token 缺失或无效 |
| 404 | IMV 任务不存在 |
| 409 | 上游匹配任务 ID 不符 |
| 422 | 请求字段或匹配片段不符合约定 |
| 503 | 回调暂未保存，可重试 |

## 6. 等待、查询和配置

在 `server/.env` 中设置，进程环境变量优先；修改后重启后端。以下是默认值，时间单位均为秒：

```dotenv
# 素材库可以访问的 IMV 基础地址；留空沿用创建请求的基础地址
COMPOSITION_PUBLIC_BASE_URL=

COMPOSITION_HTTP_TIMEOUT_SECONDS=30
COMPOSITION_POLL_SECONDS=2
COMPOSITION_ASR_WAIT_SECONDS=1800
COMPOSITION_MATCH_WAIT_SECONDS=30
COMPOSITION_RENDER_WAIT_SECONDS=3600
COMPOSITION_CONCURRENCY=2
COMPOSITION_WIDTH=1080
COMPOSITION_HEIGHT=1920
COMPOSITION_FPS=30

# 新合成视频及第 3 帧 PNG 的 ZOS 存储；凭据仅由服务端读取
ZOS_API_ENDPOINT=https://hangzhou7.zos.ctyun.cn
ZOS_BUCKET=archives
ZOS_ACCESS_KEY_ID=<Access Key ID>
ZOS_SECRET_ACCESS_KEY=<Access Key Secret>
ZOS_WEB_URL=https://oss.joyfile.net
ZOS_REGION=hangzhou-7
ZOS_FORCE_PATH_STYLE=false
```

- 素材匹配：提交一次，默认等待回调最多 30 秒；期间检查本地回调落库状态，不持续查询素材库。超时后有上游 ID 才补查一次；仍未完成则失败，不重新提交匹配。
- IMS：拿到 JobId 后立即查询，未完成时每隔 2 秒继续查询；提交、渲染、取址和 ZOS 转存共用 3600 秒截止时间。视频与封面转存成功才标记成功，超时失败时不重新渲染。
- ZOS：服务端 PATH 须有 `ffmpeg`。下载一次 IMS 成片，按解码顺序截取第 3 帧，分别上传 `imv/video_composition/{taskId}.mp4` 与 `.png`，只给这两个对象设置 `public-read`；校验大小及匿名读取后仅在 GET 和回调返回视频 URL。图片可通过 `ZOS_WEB_URL/imv/video_composition/{taskId}.png` 直接访问，不进入接口结果。`ZOS_API_ENDPOINT` 是上传接口，`ZOS_WEB_URL` 是公开读取域名；当前不读取 `ZOS_ENDPOINT`。对象被删除或命中桶生命周期规则后，固定地址仍会失效。
- IMS 临时查询故障连续 3 次会提前失败；受理不明确时使用同一 ClientToken，最多尝试提交 2 次。
- 最终通知：终态后发送，首次失败再重试三次，HTTP 超时共用 30 秒配置。
- HTTP 超时范围为 `(0, 300]`，查询间隔 `(0, 60]`，ASR/匹配/渲染等待上限各为 `(0, 86400]`，并发数为 1～16。
- `COMPOSITION_PUBLIC_BASE_URL` 可填 `http://公网IP:端口` 或 HTTPS 域名，可包含部署路径前缀；不要附加 `/api/v1/video-compositions`，不能包含查询参数。应确保生成的回调路径能到达该后端。
- 任务已保存的回调地址和截止时间不会随 `.env` 修改而更新；本地超时也不代表云端任务已被取消。

素材匹配使用 `SEGMENT_MATCH_BASE_URL` 和可选的 `SEGMENT_MATCH_AUTHORIZATION`，仅 standard 显式传 `materials` 时要求匹配地址。两个纯素材模式不依赖匹配配置；包含视频素材时服务端 PATH 还须有 `ffprobe`，单个探测使用 `COMPOSITION_HTTP_TIMEOUT_SECONDS` 超时，取消或超时回收子进程。FFprobe 白名单包含 HTTP 代理所需的 `httpproxy`；探测失败的退出码及脱敏 stderr 保存到 `timeline.json` 的 `error_log`，查询与回调仍返回固定错误摘要。无语音模式不依赖 ASR 和切片配置；有语音模式仍依赖 ASR，有文案才要求切片配置。所有模式仍需 IMS、ZOS、数据库和 FFmpeg；缺少所需配置或工具时受理返回 503。参见 `server/.env.example`。

## 7. 可选的客户端 IMS 配置

创建和查询接口支持 `X-IMS-Config` 请求头，其值为 URI 编码 JSON（JavaScript：`encodeURIComponent(JSON.stringify(config))`）。省略时使用服务端配置；提供时使用本次客户端凭据。

编码前对象：

```json
{
  "ALIBABA_CLOUD_ACCESS_KEY_ID": "<AccessKey ID>",
  "ALIBABA_CLOUD_ACCESS_KEY_SECRET": "<AccessKey Secret>",
  "ALIBABA_CLOUD_SECURITY_TOKEN": "",
  "MIX_VIDEO_ALIYUN_IMS_REGION_ID": "cn-shanghai",
  "MIX_VIDEO_ALIYUN_IMS_ENDPOINT": "ice.cn-shanghai.aliyuncs.com"
}
```

AccessKey ID/Secret 必填，临时凭据填写 SecurityToken；地域与官方 Endpoint 必须一致。请求头解析或字段校验失败返回 422。此头不能覆盖合成等待时间、尺寸或素材匹配配置。

客户端 IMS 凭据只保存在任务内存，不写业务正文或任务数据库；新任务成功转存 ZOS 后，GET 不再需要 IMS 配置头。历史成功任务仍需提供有权限的 IMS 配置获取临时地址；服务重启会丢失未完成任务的客户端凭据快照。
