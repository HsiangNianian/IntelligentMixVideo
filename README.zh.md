<h1 align="center">IntelligentMixVideo</h1>

<p align="center">
  <em>基于阿里 IMS 云剪辑的智能混剪 Agent 系统，支持自定义模板、服务端文本切片、素材召回、Agent 自动编写编排与自动编写 Remotion 特效等</em>
</p>
<p align="center">
  <a href="https://github.com/HsiangNianian/IntelligentMixVideo/stargazers"><img src="https://img.shields.io/github/stars/HsiangNianian/IntelligentMixVideo?label=Stars" alt="GitHub Stars"></a>
  <a href="https://github.com/HsiangNianian/IntelligentMixVideo/releases/latest"><img src="https://img.shields.io/github/v/release/HsiangNianian/IntelligentMixVideo?style=flat&amp;logo=github&amp;label=Release&amp;labelColor=161B22&amp;color=2EA043" alt="Latest release"></a>
  <a href="https://github.com/HsiangNianian/IntelligentMixVideo/actions/workflows/validation.yml"><img src="https://img.shields.io/github/actions/workflow/status/HsiangNianian/IntelligentMixVideo/validation.yml?style=flat&amp;logo=githubactions&amp;label=CI&amp;labelColor=161B22" alt="Project validation status"></a>
  <a href="LICENSE.md"><img src="https://img.shields.io/badge/license-AGPL--3.0--only-blue" alt="AGPL-3.0-only"></a>
</p>

<p align="center">
  <a href="README.md">English</a> · <strong>中文</strong>
</p>

---

## 它能做什么

IntelligentMixVideo 将桌面模板编辑、服务端媒体处理和云端视频渲染连接起来。你可以先制作可复用的视觉模板，再通过服务端接口将文案、语音和视频素材组织为成片。

- **可视化模板编辑**：添加花字、气泡、滤镜、画面特效、转场与文字动画，调整文字、字号、位置和效果参数。
- **预览与时间轴**：预览模板效果，按秒数或视频时长百分比设置对象开始位置，并选择固定时长或持续到视频结束。
- **本地与云端模板库**：桌面本地模板保存到本机；云端模板通过服务端保存到 MySQL。两套模板库独立使用。
- **文案与音频处理**：提供 ASR 转写能力、文案切片接口和关键词处理，为字幕与合成时间线提供数据。
- **异步视频合成**：根据输入支持标准数字人、有配音纯素材、无配音纯素材等流程，通过任务查询和回调获取结果。
- **Remotion 字效**：通过 Agent 生成字效并预览成功版本，可将成功版本保存为资产，添加到云端模板。

> 当前视频合成主要通过服务端 API 使用，客户端尚无专门的整片合成页面。Remotion 新字效的隔离生成要求 Linux 服务端及相应渲染环境；完整说明见[支持范围](#支持范围)。

### 工作界面

| 页面 | 用途 |
| --- | --- |
| 主页 | 浏览本地与云端模板，新建模板，进入已有作品 |
| 模板编辑 | 左侧选择资产，中间预览，右侧编辑对象参数与时间；下方时间轴调整视频排列 |
| Remotion 字效 | 发起字效生成、查看成功版本与历史、调整参数并保存资产 |
| 设置 | 管理后端地址、本地模板路径及可用的模型、IMS 等配置 |

模板保存的是对象配置与时间规则，预览视频独立于模板保存。更换预览视频后，会按新视频时长重新计算对象的显示区间。

---

## 工作流程

IntelligentMixVideo 的模板编辑、视频合成和 Remotion 字效分别完成不同阶段的工作：

- **模板编辑**：在客户端添加文字与效果、设置对象时间区间，并保存为桌面本地模板或云端模板。保存的是模板配置，预览视频不随模板保存。
- **视频合成**：调用服务端 API，提供云端模板 ID、文案及媒体地址。服务端按输入模式执行 ASR、文案切片、素材匹配或顺序编排，再提交 IMS 渲染，将成片转存到 ZOS，最后通过查询或回调返回结果。
- **Remotion 字效**：通过 Agent 生成并验证字效，成功版本可保存为固定资产，再加入云端模板的时间轴。整组 Remotion 片段通过独立的[叠加渲染接口](#渲染透明叠加视频)输出透明视频。

---

## 快速开始

先根据用途选择运行方式：

| 你想做什么 | 需要准备什么 |
| --- | --- |
| 在桌面端编辑和保存本地模板 | 桌面客户端；本地模板存储无需 Python API 或 MySQL |
| 使用共享模板或服务端接口 | 客户端或 API 调用方，以及可访问的服务端与 MySQL |
| 生成新的 Remotion 字效 | 配置好模型与隔离渲染环境的 Linux 服务端 |
| 完成云端视频合成 | 按所用模式配置 IMS、ZOS，以及需要的 ASR、切片、素材匹配等服务 |

模板预览使用的 SDK、字体及示例媒体仍需要网络。完成程序安装后，云服务相关功能还需要有效凭据和对应配置。

### 使用安装包

正式版本从 [Releases](https://github.com/HsiangNianian/IntelligentMixVideo/releases) 获取；开发测试包从 [GitHub Actions](https://github.com/HsiangNianian/IntelligentMixVideo/actions) 对应运行的 Artifacts 获取，产物通常保留 14 天。

| 平台 | 架构 | 安装包 |
| --- | --- | --- |
| Windows | x64 | NSIS `.exe`、`.msi` |
| Linux | x64 | `.deb`、`.AppImage` |
| macOS | Apple Silicon、Intel | 对应架构的 `.dmg` |

两种安装包的后端行为不同：

| 安装包 | 后端方式 | 使用说明 |
| --- | --- | --- |
| 普通包 | 连接外部 API | 可使用桌面本地模板；云端模板、字效及合成功能需要可用后端 |
| Debug 包 | 携带内置 API、私有数据库及运行环境 | 默认首次启动会展开运行时、初始化数据库并等待 API 就绪；显式设置外部后端地址时连接该地址 |

Debug 安装包的 artifact 名称为 `intelligent-mix-video-debug-<平台>-<提交 SHA>`。它使用优化构建，主要区别是携带内置后端。Linux 内置后端需要 glibc 2.35+；首次初始化可能需要等待。

安装并打开客户端后，可先按[基本使用](#基本使用)创建本地模板。使用云端功能时，在「设置」中确认后端地址和相关配置。

当前安装包未配置 Windows 代码签名或 macOS 公证。平台运行与构建细节见[客户端说明](client/README.md)及[构建与发布](#构建与发布)。

---

### 从源码运行

客户端开发需要 Bun、Rust stable 和 [Tauri 2 平台依赖](https://v2.tauri.app/start/prerequisites/)。Bun 版本以 [client/package.json](client/package.json) 的 `packageManager` 字段为准；服务端需要 Python 3.12+、[uv](https://docs.astral.sh/uv/) 和 MySQL。

- Windows：安装 Visual Studio C++ Build Tools、Windows SDK 和 WebView2；ARM64 主机还需匹配的 ARM64 C++ 工具。
- macOS：安装 Xcode Command Line Tools，并按 Tauri 文档准备环境。
- Linux：按 Tauri 文档安装对应发行版的系统依赖。

尚未克隆仓库时，可以先执行：

```bash
git clone https://github.com/HsiangNianian/IntelligentMixVideo.git
cd IntelligentMixVideo
```

以下步骤以当前检出版本的仓库根目录为起点。已有本地仓库可直接使用；具体功能与配置以所选分支或版本附带的文档为准。

---

### 服务端运行

仅使用桌面本地模板时，可以跳过这一步。使用云端模板或 API 时：

1. 启动 MySQL。
2. 首次配置时，将 `server/.env.example` 复制为 `server/.env`；已有配置文件直接编辑。
3. 填写 `DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD` 和 `DB_NAME`。
4. 在仓库根目录的终端中执行：

```bash
uv sync --locked --project server
uv run --locked --project server server
```

服务端默认监听 `0.0.0.0:20070`。启动成功后，可在本机打开 [API 文档](http://127.0.0.1:20070/docs)。启动时会创建缺失的数据库，因此配置的数据库账号需要相应建库权限。

远程客户端需使用服务器 IP 或域名，并确保服务端端口可访问。数据库、接口及详细配置见[服务端说明](server/README.md)。

---

### 客户端运行

另开一个终端，从仓库根目录进入 `client/`。首次配置时，可将 `client/.env.example` 复制为 `client/.env`；API 地址未配置或留空时默认使用 `http://localhost:20070`。

```bash
cd client
bun install --frozen-lockfile
bun run tauri dev
```

这会启动桌面开发窗口。打开「主页」后，即可新建或选择模板；仅编辑本地模板时，无需额外启动服务端。

也可以在 `client/` 中改用浏览器开发模式：

```bash
bun run dev
```

然后访问 [http://localhost:1420](http://localhost:1420)。浏览器模式不支持桌面本地模板库，云端模板仍需连接服务端。

---

## 基本使用

### 制作并保存一个模板

1. 在「主页」选择新建本地模板或云端模板，填写名称与描述。
2. 新模板自动创建一个标题和一个底部字幕，从左侧资产区选择样式与效果；VFX 最多一个，滤镜可添加多个。
3. 选择画面对象，在右侧调整文字、字号、位置、样式和动画。
4. 在右侧设置对象时间，并在中间画面预览；底部字幕固定覆盖整个视频。拖动时间轴中的视频片段可以调整排列。
5. 点击「保存」。新模板首次保存后创建记录，之后保存会更新同一个模板。
6. 返回主页，确认模板出现在对应的本地或云端列表中。

本地与云端模板不会自动同步。切换模板或新建模板时，界面会提示处理未保存的修改；编辑过程不提供自动保存或崩溃恢复。

三个预览视频各取前 10 秒，不足 10 秒使用完整视频；通过「加载预览视频」弹窗同时设置三个 URL。第一个转场连接视频 1 → 视频 2，第二个连接视频 2 → 视频 3，在右侧修改效果和持续时间。时间轴从上到下显示标题、字幕、气泡、转场、VFX、滤镜和视频。预览来源与视频排列独立于模板保存。

默认来源与用户填写的 URL 均读取真实时长。模板规则与媒体信息统一生成本次时间结果，预览扣除转场重叠，正式合成保留音频和素材的固定时间。转场必须保存交界处和持续时间，缺少对应片段时说明未应用原因。云端与本地保存检查相同的对象数量与时间规则；无效记录显示 ID、原因和单条删除入口，其他模板正常使用。客户端与服务端需要一起升级。

更多时间规则、资产操作与保存约束见[模板行为](client/README.md#模板行为)。

### 使用 Remotion 字效

在「设置」中配置 Remotion Agent，并确认连接的 Linux 服务端已具备隔离渲染环境；然后从「Remotion 字效」页面发起生成，查看成功版本并调整参数。

成功版本可以保存为资产，再从云端模板的「Remotion 资产」中添加到时间轴。本地模板目前不支持此类资产。字效页面的预览与资产保存不等同于提交整片云端合成任务。

操作与版本限制见[Remotion 客户端说明](client/src/features/remotion_templates/README.md)。

### ASR 与文案切片

ASR 提供独立的 Python 异步函数和命令行入口，使用北京地域 Fun-ASR 处理 HTTPS 音频直链，返回带字词时间戳的原始 JSON；目前没有独立的 HTTP 转写接口。

在 `server/.env` 配置 `DASHSCOPE_API_KEY` 后，于仓库根目录运行：

```bash
uv run --locked --project server python -m server.asr "https://example.com/audio.wav"
```

将示例地址替换为云服务可访问的音频直链。结果写入执行目录的 `asr_result.json`，同名文件会被覆盖。

`POST /segmentations` 使用正确文案与已有 ASR 结果生成带时间和关键词的片段，不负责调用 ASR。完整切片要求有效的 `script` 和恰好包含一个 `transcripts` 元素的 Fun-ASR 原始结果，词级 `begin_time/end_time` 使用毫秒，返回的片段时间使用秒。

超过 8 个有效字（不计标点和空白）的片段会由代码继续拆分。没有 ASR、仅提供 `title` 时，接口只提取标题关键词。请求示例与限制见[文案切片](server/README.md#文案切片)和 [ASR 音频转写](server/README.md#asr-音频转写)。

### 通过 API 合成视频

使用 `POST /api/v1/video-compositions` 创建任务，再通过 `GET /api/v1/video-compositions/{task_id}` 查询结果；也可以传入 `callbackUrl` 接收终态通知。

服务端根据媒体字段选择处理方式，不需要额外传入 `compositionMode`：

| 输入情况 | 处理方式 |
| --- | --- |
| 提供 `videoUrl` | 标准模式；省略 `materials` 使用纯数字人，显式数组按约定参与素材匹配 |
| 无 `videoUrl`，有顶层 `audioUrl` | 有配音纯素材模式，要求非空素材列表，按素材顺序合成 |
| 两者都没有 | 无配音纯素材模式，要求非空素材列表和有效目标时长，不生成字幕 |

创建任务成功表示请求已受理，不代表成片已经生成。创建响应的 `data` 为任务 ID，调用方应保存该 ID，通过查询接口或终态回调获取结果。

`styleId` 必须是已保存的云端模板 ID，桌面本地模板不能直接用于服务端合成。标准模式仍需有效文案、`videoUrl` 和 `audioUrl`；省略 `materials` 时跳过素材匹配，显式传入空数组或非空数组时走匹配流程，`materials: null` 不被接受。

使用素材匹配时，`COMPOSITION_PUBLIC_BASE_URL` 应是匹配服务可访问的 IMV 地址，用于匹配结果回传；留空时沿用合成请求的基础地址。请求中的 `callbackUrl` 则是业务系统接收最终成片结果的地址。

终态回调包含 `taskId`、`status`、`videoUrl` 和 `errorMessage`。成功回调的状态为 `succeed`，查询结果中的成功状态为 `succeeded`。通知失败不会回退合成结果，调用方可通过 GET 补查，并按任务 ID 幂等处理通知。

各模式的必填字段、外部服务与字幕行为不同。提交前请阅读[视频合成 API 文档](server/src/server/video_composition/api.md)，尤其是 `materials` 省略、空数组与非空数组的区别。

### 渲染透明叠加视频

`POST /api/v1/overlay-renders` 将指定 `styleId` 下已保存的 Remotion 固定片段，按时间与叠放顺序渲染为一个透明 VP9 Alpha WebM。输出不含音频，上传 ZOS 后返回可访问地址。

请求支持 `styleId`、可选的 `audioUrl`、`duration` 和 `callbackUrl`。总时长优先使用 `audioUrl` 的完整音频时长，其次使用 `duration`，否则取片段的最晚结束时间。提供音频仅用于确定时长，不会将音频混入输出。

使用 `GET /api/v1/overlay-renders/{taskId}` 查询状态。该接口始终要求 Linux 沙箱及相应渲染依赖，配置与限制见 [Remotion 服务端说明](server/src/server/remotion_templates/README.md#overlay-renders-for-the-bus)。

---

## 配置

### 常用配置入口

| 配置 | 位置 | 用途与默认行为 |
| --- | --- | --- |
| `VITE_API_URL` | `client/.env` | 源码运行与构建的 API 默认地址；留空时为 `http://localhost:20070` |
| `VITE_PREVIEW_VIDEO_1_URL`、`VITE_PREVIEW_VIDEO_2_URL`、`VITE_PREVIEW_VIDEO_3_URL` | `client/.env` | 视频 1、2、3 的默认地址；留空使用对应内置示例，也可填写 `public/` 下的资源路径 |
| `DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD`、`DB_NAME` | `server/.env` | MySQL 连接信息；使用服务端前填写 |
| `PORT` | `server/.env` 或进程环境变量 | API 监听端口，默认 `20070`；有效范围为 1～65535 |
| `DASHSCOPE_API_KEY` | `server/.env` | 使用北京地域 Fun-ASR 转写时所需的密钥 |
| `IMV_LLM_BASE_URL`、`IMV_LLM_API_KEY`、`IMV_LLM_MODEL` | `server/.env` | 文案切片模型连接，与 Remotion Agent 分别配置 |
| `IMV_ACTOR_BASE_URL`、`IMV_ACTOR_API_KEY`、`IMV_ACTOR_MODEL`、`IMV_VISION_MODEL` | `server/.env` 或客户端 Remotion Agent 设置 | 字效生成与视觉模型；客户端可提供本次任务使用的配置 |
| `SEGMENT_MATCH_BASE_URL` | `server/.env` | 标准模式显式提供 `materials` 时使用的外部素材匹配地址 |
| `COMPOSITION_PUBLIC_BASE_URL` | `server/.env` | 对外可访问的服务端回调基础地址；留空沿用请求基础地址 |
| `ALIBABA_CLOUD_ACCESS_KEY_ID`、`ALIBABA_CLOUD_ACCESS_KEY_SECRET` | `server/.env` 或客户端上海 IMS 设置 | IMS 云渲染凭据；可选 `ALIBABA_CLOUD_SECURITY_TOKEN`，地域与 Endpoint 默认使用上海 |
| `ZOS_API_ENDPOINT`、`ZOS_BUCKET`、`ZOS_ACCESS_KEY_ID`、`ZOS_SECRET_ACCESS_KEY`、`ZOS_WEB_URL` | `server/.env` | 仅由服务端读取；上传入口与公开访问地址分别配置 |

完整字段见 [server/.env.example](server/.env.example) 和 [client/.env.example](client/.env.example)。媒体直链的访问要求及素材字段约束见[视频合成 API 文档](server/src/server/video_composition/api.md)。

普通客户端设置提供 Remotion Agent 与上海 IMS 配置；Debug 模式展示更多服务端字段，但数据库不在设置面板中。动态表单依赖后端返回配置 Schema，界面显示的默认值不代表服务端 `.env` 中的实际值。

设置中的「保存」只提交当前模块，并保持弹窗打开；「取消」关闭弹窗，丢弃尚未保存的修改，不撤销此前已经保存的值。

### 修改后何时生效

- **源码服务端**：配置固定读取 `server/.env`，优先级为构造参数、进程环境变量、配置文件、字段默认值；修改后重启服务。
- **前端环境文件**：修改 `client/.env` 后重启开发服务；已经构建的客户端需重新构建。注意其他 Vite 环境文件可能覆盖同名变量。
- **客户端后端地址**：可在「设置 → 通用」中保存；新请求使用新地址，已有会话连接需重启客户端后切换。
- **客户端模型与 IMS 设置**：保存后供对应业务请求使用；未保存时沿用服务端默认配置。
- **Debug 内置服务高级设置**：保存后退出并重新打开客户端，启动时加载；该路径不适用于手动启动或远程服务端，数据库配置也不在此设置范围内。

客户端设置不会修改共享服务端的 `.env`。界面配置、保存位置与各模式差异见[客户端设置说明](client/src/features/settings/README.md)。

---

## 支持范围

| 能力 | 当前范围 |
| --- | --- |
| 桌面客户端 | Windows x64、Linux x64、macOS Apple Silicon / Intel |
| 浏览器开发模式 | 可连接 API 使用云端功能；不支持桌面本地模板库 |
| 本地模板编辑与存储 | 无需 Python API 或 MySQL；SDK、字体及示例媒体预览仍可能需要联网 |
| Remotion 新字效生成 | 隔离生成目前要求 Linux 服务端、非特权 user namespace 及相应渲染依赖 |
| Remotion 资产加入模板 | 当前支持云端模板，本地模板不支持 |
| ASR 独立调用 | 提供 Python 函数与命令行入口，尚无独立 HTTP 转写路由 |
| 视频合成 | 服务端异步 API；客户端尚无专门的整片合成页面 |
| 合成任务调度 | 当前为单实例、单进程调度，恢复边界见服务端文档 |
| 云端模板访问 | 当前共享模板 API 不提供登录和用户隔离 |

Windows、macOS 的 Debug 包携带运行环境，可使用内置 API、模板存储和已有预览；携带渲染依赖不代表本机支持新字效的隔离生成。

### 模板兼容性

模板要求使用 `tracks` 数组，各对象参数保存在 `tracks[].editor` 中；旧的顶层 `editor` 格式不提供自动转换。

本地旧模板无法读取时，客户端会显示具体文件路径及删除影响。处理前先备份该文件；删除会清除该文件中的全部本地模板。切换本地模板路径不会自动迁移旧数据。

---

## 常见问题

### 云端模板加载失败怎么办？

确认服务端与 MySQL 已启动，客户端后端地址指向实际 API 地址。服务端启动失败时，检查数据库连接和建库权限；跨机器访问还需确认端口可达。

桌面客户端可以先使用本地模板环境。本地与云端库独立，切换不会自动搬运模板。

### 为什么浏览器里不能新建本地模板？

本地模板读写由 Tauri 桌面能力提供。浏览器模式请使用云端模板，或启动桌面客户端。

### 本地模式能完全离线使用吗？

本地模板可以选择内置目录中的效果并保存；视频预览仍依赖 SDK、字体和所用媒体的可访问性。云端模板、模型调用及云合成需要相应服务和网络。

### 新字效生成提示服务尚未就绪怎么办？

确认后端已配置可用模型，并具有 Linux 隔离校验与渲染环境。更新 Agent 配置后，可在字效页面重新连接；内置后端高级配置需要重启客户端。依赖说明见[Remotion 服务端文档](server/src/server/remotion_templates/README.md)。

### 设置里只有「通用」，或模块列表加载失败？

模块列表来自后端的 `/api/settings/plugins`。先确认后端地址和服务状态，再重新打开设置。浏览器刷新后会丢失仅保存在页面内存中的设置。

### 安装依赖时提示版本不存在？

服务端使用官方 PyPI，并通过 `server/uv.lock` 锁定依赖。先检查本机是否覆盖了 uv 索引，以及所用镜像是否同步；不要直接通过降低版本或删除锁文件绕过问题。

### 视频合成日志保存在哪里？

源码布局下，新任务日志保存在 `server/.log/video_composition/<task_id>/`，包含请求、模板、ASR、切片、素材匹配、时间线与 ZOS 等模块记录。

每个任务包含 `request.json`、`template.json`、`asr.json`、`segmentation.json`、`matching.json`、`timeline.json` 和 `zos.json`，统一按 `input/output/execute_log/error_log` 组织。重复正文可能通过 `$log_ref` 引用同任务的其他记录。

日志在后台顺序写入。整个 `.log` 超过 50 MB 后，才会从旧到新清理合成与通知均已结束的任务目录；正在执行或仍有日志排队的任务保留。历史数据库日志表不再读写或迁移，业务任务表仍保留。

历史日志可能仍在 `server/src/server/.log/`；新版本不会自动迁移或删除历史目录。日志中的文案和带签名的媒体链接仍可能包含业务信息。结构、保留策略和排查方法见[执行日志说明](server/README.md#执行日志)。

<details>
<summary>Windows 构建出现 link: extra operand</summary>

通常是误用了 Cygwin 的 `link.exe`。检查 Visual Studio Installer 是否启用“使用 C++ 的桌面开发”，并安装匹配架构的 MSVC 与 Windows SDK；在对应的 Visual Studio Developer PowerShell 中重新构建。

</details>

<details>
<summary>Linux AppImage 在 Wayland 下播放卡顿</summary>

可以在相同场景分别运行下列命令，比较 Wayland 与 X11 后端：

```bash
IMV_GDK_BACKEND=wayland ./应用.AppImage
IMV_GDK_BACKEND=x11 ./应用.AppImage
```

请将 `应用.AppImage` 替换为实际文件名。该选项用于对比显示后端，不默认关闭硬件加速。构建或产物检查通过，不代表真实桌面播放帧率已经验证。

</details>

---

## 数据与凭据

- 桌面本地模板默认保存在应用数据目录的 `data/template/templates.json`；可在「设置 → 通用」指定其他 `.json` 绝对路径。切换路径不会迁移旧文件。
- 桌面设置保存在应用数据目录的 `data/settings/settings.json`。当前 API Key 与其他设置一起以明文保存，尚未接入系统凭据库；浏览器设置仅保存在当前页面内存中，刷新后丢失。
- 通过客户端请求提供的模型与 IMS 凭据按任务使用，不写入共享 `.env`、作品历史或任务日志。日志仍保留实际文案与媒体链接，分享日志前应检查业务数据。
- 新合成任务的成片及第 3 帧 PNG 会上传到 ZOS，并将对应对象设置为公开可读。GET 与成功回调返回视频地址；对象删除或生命周期清理后，地址会失效。

具体保存方式和使用范围见[客户端设置](client/src/features/settings/README.md)与[视频合成说明](server/README.md#异步视频合成)。

---

## 开发与文档

<a id="structure"></a>

### 项目结构

| 目录或文件 | 内容 |
| --- | --- |
| `client/` | Rust + Tauri 2 + React + TypeScript 桌面客户端，使用 Tailwind CSS 4 与 shadcn/ui |
| `server/` | Python + FastAPI + MySQL 服务端，提供模板、切片、字效与合成等接口 |
| `proto/` | 跨客户端与服务端共用的 Protobuf 消息定义 |
| `.github/` | 验证、跨平台构建和发布工作流及相关脚本 |
| `docs/` | 专项设计与接口约定文档 |

客户端按页面、业务组件、基础 UI 和共享工具分层；具体结构见 [client/README.md](client/README.md#开发结构)，最小改动与源码注释要求见 [AGENTS.md](AGENTS.md#最小改动与源码说明强制)。

### 文档导航

| 主题 | 入口 |
| --- | --- |
| 客户端运行、模板编辑与预览 | [client/README.md](client/README.md) |
| 服务端启动、数据库与 API | [server/README.md](server/README.md) |
| 文案切片与关键词 | [文案切片](server/README.md#文案切片) |
| 独立音频转写 | [ASR 音频转写](server/README.md#asr-音频转写) |
| 视频合成模式、字段与请求示例 | [视频合成 API 文档](server/src/server/video_composition/api.md) |
| 合成执行日志 | [执行日志](server/README.md#执行日志) |
| 客户端设置、凭据与生效方式 | [设置模块说明](client/src/features/settings/README.md) |
| Remotion 页面与资产使用 | [Remotion 客户端说明](client/src/features/remotion_templates/README.md) |
| Remotion 服务端与渲染环境 | [Remotion 服务端说明](server/src/server/remotion_templates/README.md) |
| Remotion 透明叠加渲染 | [叠加渲染](server/src/server/remotion_templates/README.md#overlay-renders-for-the-bus) |
| 协作、结构与构建约定 | [AGENTS.md](AGENTS.md) |
| 贡献与 PR 要求 | [CONTRIBUTE.md](CONTRIBUTE.md) |

<details>
<summary>模板协议与兼容性</summary>

模板数据格式及旧文件处理见[模板兼容性](#模板兼容性)。模板配置独立于预览媒体保存。

云端模板 API 在 `/template` 路径上传输 Protobuf 二进制消息。根目录 `buf.yaml` 管理消息定义与兼容性检查，`buf.gen.yaml` 生成客户端 TypeScript、Rust 和服务端 Python 代码。修改协议后，在仓库根目录执行：

```bash
buf lint
buf generate
```

提交生成结果，并对已有协议版本运行 `buf breaking --against <基线>`，将 `<基线>` 替换为实际比较基线。接口格式见[模板接口](server/README.md#模板接口)。

</details>

### 提交前检查

按改动范围执行验证：

| 改动 | 命令 | 执行目录 |
| --- | --- | --- |
| 前端核心逻辑 | `bun run test` | `client/` |
| 前端类型与构建 | `bun run build` | `client/` |
| 服务端 | `uv run --locked --project server pytest server/tests -v` | 仓库根目录 |
| Python 包 | `uv build --project server` | 仓库根目录 |
| Rust 格式 | `cargo fmt --manifest-path client/src-tauri/Cargo.toml --check` | 仓库根目录 |
| 桌面程序与安装包 | `bun run tauri build` | `client/` |
| CI／发布脚本 | `bun test ./.github/scripts` | 仓库根目录 |
| 发布流程回归 | `bun .github/scripts/release-smoke.mjs` | 仓库根目录 |
| GitHub Actions 工作流 | `actionlint .github/workflows/*.yml` | 仓库根目录 |
| 两端版本一致性 | `bun .github/scripts/validate-release.mjs` | 仓库根目录 |
| 文件基础检查 | `uvx pre-commit run --all-files` | 仓库根目录 |
| 差异格式 | `git diff --check HEAD` | 仓库根目录 |

可选安装本地 Git 提交钩子：`uvx pre-commit install`。

纯文档修改检查内容、链接、Markdown 预览和差异格式即可，无需重跑完整应用构建。前端构建与原生编译检查不能替代安装包和真实桌面播放验证；提交时请说明实际运行的检查及未验证部分。

---

## 构建与发布

普通本地安装包输出到 `client/src-tauri/target/release/bundle/`；指定 target 时位于对应的 target 子目录。工作流入口见 [GitHub Actions](https://github.com/HsiangNianian/IntelligentMixVideo/actions)。

### 跨平台 CI

<details>
<summary>展开查看触发规则、验证范围和构建入口</summary>

| 事件 | 检查与构建 |
| --- | --- |
| 开发分支 push | 按改动检查；同一提交已有打开的 PR 时跳过重复 push 验证 |
| PR | 按改动执行前端、服务端及必要的四平台原生编译检查；不打包安装器 |
| 默认分支 push | 按需验证集成结果；客户端或 CI 改动生成普通安装包 |
| `main` / `dev` push | Debug client 额外生成内置后端测试包，独立于普通验证和构建 |
| 正式版本 tag | 完整跨平台打包、附件校验及 Release 发布 |
| 手动运行 | Validate project 做验证，Build client 生成普通包，Debug client 生成内置后端包 |

常规入口为 [validation.yml](.github/workflows/validation.yml)，构建复用 [client-build.yml](.github/workflows/client-build.yml)。PR 汇总检查名为 `CI result`；分支保护建议同时要求它与 pre-commit.ci，而非会按路径跳过的单个平台 job。

手动运行 Validate project 时，`integration-tests` 默认开启，在临时 MySQL 与隔离渲染环境中执行集成测试；普通 push / PR 不启动这组重型测试。报告和产物保留 14 天。可通过 `build-installers` 同时构建安装包，`api-url` 指定普通包的 API 地址，留空默认 `http://localhost:20070`。

客户端或 CI 改动的具体调度、依赖锁文件校验、四平台原生检查及集成环境，见[客户端构建 CI 约定](AGENTS.md#客户端构建-ci)。

</details>

<details>
<summary>展开查看 Debug 内置后端、数据目录与平台说明</summary>

[Debug client](.github/workflows/client-debug.yml) 在 `main` / `dev` 推送时运行，也支持手动触发，不监听 PR。构建通过 `debug-backend: true` 设置内置后端标记；普通包和正式发布默认不携带后端。手动网页入口要求工作流已存在于默认分支，再选择要构建的目标分支。

Debug 包携带完整服务端、Python、uv、私有 MySQL、Node、Bun、Remotion、Chrome、FFmpeg/ffprobe 与字体；Linux 另带 bubblewrap/prlimit 等隔离工具。首次运行展开资源、初始化私有数据库，等待 API 就绪后连接回环端口；未保存启动端口时自动分配，退出客户端会关闭 API 与数据库。

| 平台 | 内置后端数据目录 |
| --- | --- |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/com.intelligentmixvideo.client/backend/` |
| Windows | `%APPDATA%/com.intelligentmixvideo.client/backend/` |
| macOS | `~/Library/Application Support/com.intelligentmixvideo.client/backend/` |

其中包含私有数据库、配置和 `server.log`，Remotion 数据位于相应 `remotion/` 目录。首次从无密钥示例创建配置，已有配置与数据不会被覆盖；安装包不会携带开发机 `.env` 或数据库。运行时缓存位置和测试边界见[构建约定](AGENTS.md#客户端构建-ci)。

Unix 数据库使用私有 socket；Windows 使用随机密码与动态回环端口，不依赖宿主 MySQL。macOS 按 Apple Silicon、Intel 分别构建；Windows 使用 x64 原生运行时。

Remotion 新字效的隔离生成目前仅支持 Linux 服务端。macOS 本地开发可在服务端设置 `IMV_ALLOW_UNSANDBOXED_WORKER=true`，以非隔离方式运行字效生成；该开关默认关闭，其他系统忽略，启用后生成代码不受沙箱隔离。透明叠加视频渲染仍始终要求 Linux 沙箱，不受此开关影响。依赖与配置详见 [Remotion 服务端说明](server/src/server/remotion_templates/README.md)。

Linux AppImage 排除随包 Wayland 和 PulseAudio 库，使用宿主版本并保留必要的 GStreamer 插件；启动默认设置 `WEBKIT_GST_DMABUF_SINK_DISABLED=1`、`WEBKIT_GST_USE_PLAYBIN3=1`，保留用户显式配置。真实播放行为仍需目标机器验证。

</details>

### Tag 发版

**客户端与服务端使用相同版本，源码版本必须与正式 tag 一致。** 发版前用脚本同步并提交清单与锁文件，合并后在对应提交创建 `vX.Y.Z` tag；构建时校验版本，不临时改写源码版本。

<details>
<summary>展开查看版本同步和发布操作示例</summary>

在仓库根目录执行。以下 `v0.6.0` 仅用于演示，应替换为实际准备发布的版本：

```bash
RELEASE_TAG=v0.6.0 bun .github/scripts/validate-release.mjs --write
bun .github/scripts/validate-release.mjs
```

脚本同步客户端 `package.json`、`tauri.conf.json`、`Cargo.toml`、`Cargo.lock`，以及服务端 `pyproject.toml`、`uv.lock` 的项目自身版本，保留第三方依赖版本。PowerShell 先执行 `$env:RELEASE_TAG = "v0.6.0"`，再运行相同脚本。

检查、提交这些修改，经 PR 合入 `main`，并确认本地当前检出的是对应发布提交后，再创建和推送 tag：

```bash
git tag -a v0.6.0 -m "Release v0.6.0"
git push origin v0.6.0
```

这里的 `origin` 应指向实际执行发布的项目仓库；在个人 Fork 中推送 tag 不会为原项目发布版本。

[release.yml](.github/workflows/release.yml) 在正式 tag 上触发，跨平台构建成功后校验安装包和 `CHANGELOG.md` 附件，再公开 Release。只接受正式版本，不接受 `-beta` / `-rc`，且版本需满足 Windows MSI 数值限制。

变更记录由工作流按 Conventional Commits 自动生成。发布使用内置 `GITHUB_TOKEN`，需要相应写权限；失败可以重跑，草稿可补齐附件，已公开版本不会被重跑覆盖。不得移动已发布 tag。

版本同步、并发发布和恢复细节见[正式版本约定](AGENTS.md#正式版本与-tag-发版)及[Release 与 CHANGELOG 约定](AGENTS.md#github-release-与-changelog)。

</details>

---

<a id="contributing"></a>

## 参与贡献

开始前请阅读 [CONTRIBUTE.md](CONTRIBUTE.md) 和 [AGENTS.md](AGENTS.md)，检查已有 Issue 与 PR，并从当前贡献目标分支创建独立工作分支。

- 提交信息、PR 标题及说明使用英文，遵循 Conventional Commits，例如 `docs: clarify setup instructions`。
- 按 [PR 模板](.github/pull_request_template.md) 说明改动、原因、验证和兼容性。
- 功能与问题修复应附带相应测试；文档应与所针对分支的实际行为一致。
- 保留现有许可证和第三方声明，提交前检查文件范围与配置内容。

问题反馈请附项目版本或提交编号、操作系统、复现步骤及相关日志。

---

## License

IntelligentMixVideo is licensed under the GNU Affero General Public License,
version 3 only (`AGPL-3.0-only`). See [LICENSE.md](LICENSE.md) for the full terms.
