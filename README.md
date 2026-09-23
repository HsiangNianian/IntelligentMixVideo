IntelligentMixVideo
===================

> 基于阿里 IMS 云剪辑的智能混剪 Agent 系统，支持自定义模板、服务端文本切片、素材召回、Agent 自动编写编排与自动编写 Remotion 特效等功能。

## Contributing

Read [CONTRIBUTE.md](CONTRIBUTE.md) for development setup, validation commands,
Conventional Commit messages, and pull request requirements. Use English for
commit messages and pull requests.

## License

IntelligentMixVideo is licensed under the GNU Affero General Public License,
version 3 only (`AGPL-3.0-only`). See [LICENSE.md](LICENSE.md) for the full terms.

Structure
---------

- `client/`：Rust + Tauri 2 + React + TypeScript 桌面客户端，使用 Tailwind CSS 4 和 shadcn/ui。
- `server/`：Python + FastAPI + MySQL 服务端，提供模板持久化、文案切片与异步视频合成接口，以及首页和用户路由示例。

模板配置通过必填的 `tracks` 数组保存独立对象，文字、位置与效果参数保存在各对象的 `editor` 中。云端和桌面本地均只接受此格式，模板顶层不包含 `editor`，不提供旧格式转换。

服务端运行
----------

安装 Python 3.12+、uv 和 MySQL，启动 MySQL 并按 [服务端说明](server/README.md) 填写 `server/.env`，然后执行以下命令；后端启动时会自动创建缺失的数据库：

```sh
cd server
uv run server
```

默认监听 `0.0.0.0:20070`（所有 IPv4 接口），本机 API 文档位于 http://127.0.0.1:20070/docs；远程访问使用服务器 IP 或域名。
通过 `server/.env` 中的 `PORT` 或进程环境变量设置端口（环境变量优先，范围 1～65535）；修改后重启服务。
仓库根目录使用 `uv run --project server server`。模板 API 统一使用 `/template` 前缀，POST 通过可选 `template_id` 区分创建和完整更新；详情见 [server/README.md](server/README.md)。
服务端各配置类共用 `config_base.py` 的读取规则，源码运行固定读取 `server/.env`，不随启动目录变化；构造参数、进程环境变量、文件、字段默认值依次优先。修改后重启服务。

服务端在项目配置中将官方 PyPI 设为默认依赖索引，与 `server/uv.lock` 的来源保持一致，避免本机默认镜像同步滞后导致版本无法解析。

`POST /segmentations` 将文案与单音轨 Fun-ASR 原始结果切为带整数 `segment_id`、秒制 `start_time/end_time`、字符串 `keyword` 及 `level/group_id` 的片段，并在片段内提供按标点拆分、时间相接且保留中英文问号的 `subtitle_parts`；输入必须恰好包含一个 `transcripts` 元素，词时间使用 `begin_time/end_time` 毫秒，不接受顶层 `sentences` 或仅有旧 `*_ms` 时间字段的输入。模型配置使用 `server/.env.example` 中的 `IMV_` 变量；从仓库根目录启动且需要该配置时使用 `uv run --project server server`。请求与处理约束见 [server/README.md](server/README.md#文案切片)。

ASR 转写另提供独立 Python 函数与命令行入口，读取北京地域的 `DASHSCOPE_API_KEY`，尚未注册 HTTP 路由；用法见 [ASR 音频转写](server/README.md#asr-音频转写)。

`POST /api/v1/video-compositions` 持久化合成任务后返回本地 ID，可传入 `callbackUrl` 接收成功/失败通知，调用方等待超时后用 `GET /api/v1/video-compositions/{task_id}` 补查一次。实现位于 `server/src/server/video_composition/`，直接复用本地 ASR、切片和模板函数；素材匹配与上海 IMS 使用外部接口，IMS 成片再转存 ZOS。匹配结果通过任务回调接收，等待超时仅补查一次，随后继续生成时间线和渲染。回调优先使用 `COMPOSITION_PUBLIC_BASE_URL`（可填 ngrok HTTPS 地址），留空沿用合成请求的基础地址。默认输出 1080×1920、30 FPS，自动选择 VOD 存储；新任务上传 ZOS 的 `imv/video_composition/` 前缀并仅公开该成片对象，GET 和成功回调返回同一固定地址，历史成功任务仍查询 IMS 临时地址。执行过程及每步输入输出写入独立 `video_composition_logs` 表，一个任务一行，`detail` 展示原始输入、最终输出及中文阶段执行/错误日志，数据库时间统一北京时间；保留实际媒体链接，服务凭证和回调鉴权脱敏。当前使用单实例、单进程调度；配置、恢复边界及联调限制见 [视频合成说明](server/README.md#异步视频合成)。

客户端运行
----------

安装 Bun（版本以 client/package.json 的 packageManager 字段为准）、Rust stable，以及 [Tauri 2 平台依赖](https://v2.tauri.app/start/prerequisites/)。
CI 使用同一 Bun 版本。`client/bunfig.toml` 配置构建脚本使用 Bun 运行。
Windows 需要 Visual Studio C++ Build Tools 和 WebView2；macOS 需要 Xcode Command Line Tools。
Visual Studio Installer 中需启用“使用 C++ 的桌面开发”，包含 MSVC 和 Windows SDK；
Windows ARM64 主机还需 ARM64 C++ 构建工具。
若报错出现 `link: extra operand`，说明误用了 Cygwin 的 `link.exe`，
请确认 C++ 工具链安装完整，并在匹配架构的 Visual Studio Developer PowerShell 中运行。

```sh
cd client
bun install --frozen-lockfile
bun run tauri dev
```

左侧提供「主页」「模版编辑」「Remotion 字效」和底部「设置」，模版编辑使用方框与铅笔线条图标，窄屏显示图标栏。启动默认进入主页，云端模板显示在上方，本地模板显示在下方；各库独立加载和重试，选择模板进入对应环境的编辑页面。浏览器提示本地模板需要桌面客户端。两种模式的设置均提供 Remotion Agent 与上海 IMS 配置，保存后通过各自业务请求使用客户端凭据；Debug 展示除数据库外的全部服务端配置及启动端口；高级字段保存后重启使用内置后端的客户端生效，涵盖 ASR、切片、素材匹配、合成与 Remotion。浏览器或远程后端不走这条本地启动加载路径。配置不修改共享 `.env`，也不进入任务日志；未保存时兼容服务端默认值。切换保留工作区草稿和订阅，IMS 目前提供提交/查询联调函数，未新增合成页面。存储、插拔与边界见 [客户端设置](client/src/features/settings/README.md)。

主页各环境提供「选择模板」和「新建模板」入口。新建时填写名称与描述，然后进入模板库设置效果；点击保存才写入对应环境。已有模板保存时更新原模板。直接进入模板库且尚未选择模板时，显示前往主页的入口。云端沿用现有 MySQL API；连接失败、超时或服务端 5xx 时提示使用桌面本地环境。本地在客户端应用数据目录的 `data/template/templates.json` 保存，无需 Python 服务。两套模板库独立，从主页选择其他模板或新建模板时保护未保存修改，目标读取失败保留原环境和草稿。桌面本地操作通过官方 `@tauri-apps/api/core` 模块调用，使用 `isTauri()` 判断环境，无需开启全局 Tauri API。

模板库采用三栏编辑：左侧按花字、气泡、滤镜、画面特效、转场和动画分类浏览真实资产，支持名称或编号搜索；中间显示实时视频和已添加对象，右侧编辑当前对象的文字、字号、位置、动画或转场时长。文字资产可以指定应用对象，同类资产替换时保留文字和位置，移除只影响当前对象。顶部只读展示环境、模板名称、描述和保存状态，并提供保存按钮；窄屏自动调整排列。操作和验证说明见 [客户端模板库说明](client/README.md#模板行为)。

模板独立于预览视频保存。每个对象可以按秒数或视频时长百分比开始，并指定持续秒数或持续到视频结束；更换视频只重新计算显示区间。时间轴拖动保留开始方式，视频结尾处理与动画调整会在预览中显示说明。云端、本地存储和现有文案合成都支持这些时间规则。

浏览器开发使用 `bun run dev` 后打开 `http://localhost:1420`；Windows 安装包内置回环静态服务，以 `http://localhost:<动态端口>` 加载页面，预览沿用阿里云 SDK 5.2.2。IPC 仅允许本次绑定的精确 localhost URL 调用已有桌面命令；macOS / Linux 保留原有 Tauri 加载方式。
本地草稿编辑与预览不依赖 Python API；共享模板读写需要服务端，SDK、字体与示例媒体仍需联网。
示例视频可在 `client/.env` 中通过 `VITE_PREVIEW_VIDEO_URL` 配置，修改后重启前端；详见 [客户端说明](client/README.md#示例视频配置)。
客户端 API 地址通过 `client/.env` 中的 `VITE_API_URL` 配置，未配置或留空时默认 `http://localhost:20070`。

客户端按页面、业务组件、基础 UI 和共享工具分层；结构见 [client/README.md](client/README.md)，
最小改动与源码注释要求见 [AGENTS.md](AGENTS.md)。

```sh
# 运行客户端核心测试（不需要后端或 SDK）
bun run test
# 编译前端（包含 TypeScript 检查）
bun run build
# 编译桌面程序和安装包
bun run tauri build
```

本地安装包输出到 `client/src-tauri/target/release/bundle/`。

跨平台 CI
---------

`.github/workflows/client-build.yml` 参考 [DropOut 的平台矩阵、缓存及产物上传配置](https://github.com/HydroRoll-Team/DropOut/blob/main/.github/workflows/test.yml)。
常规 push / PR 验证由 `validation.yml` 按改动范围调度；Debug client 独立构建测试包。

| 事件 | 检查与构建 |
| --- | --- |
| 开发分支 push | 按改动检查；同一提交已有 PR 时跳过重复任务 |
| PR | 前端核心测试与构建、服务端 pytest / 包构建按需执行；涉及 Rust/Tauri、客户端依赖或 CI 时做四平台原生编译检查，不打包 |
| 默认分支 push | 按需验证集成结果；客户端或 CI 改动生成四平台安装包 |
| 正式 tag | 完整四平台打包、附件校验及 Release 发布 |
| main/dev push | Debug client 生成四平台内置后端测试包；原有验证和普通打包独立执行 |
| 手动运行 | Validate project 执行全部检查；Build client 生成普通安装包；Debug client 生成内置后端测试包 |

常规验证中，纯前端改动不跑 Rust 矩阵，纯服务端改动不构建客户端，纯文档 PR 由 pre-commit.ci 检查。Debug client 不按改动路径过滤，`main` / `dev` 的每次推送均打包。
PR 的统一结果是 `CI result`，分支保护建议同时要求该检查和 pre-commit.ci；不建议要求会按路径跳过的单个平台 job。
`cargo check` 验证原生代码与依赖的编译，不替代完整链接、安装器构建和安装验证。

| 平台 | 架构 | 安装包 |
| --- | --- | --- |
| Windows | x64 | NSIS exe、MSI |
| Linux | x64 | deb、AppImage |
| macOS | Apple Silicon、Intel | dmg |

Linux AppImage 打包通过 `client/src-tauri/.appimageignore` 排除 Wayland 和 PulseAudio 库，使用宿主机版本，避免图形库冲突和音频时钟阻塞；保留 GStreamer OpenGL、播放及解码插件。CI 上传前使用 `bun .github/scripts/appimage-smoke.mjs <AppImage>` 检查实际产物；本地也可在仓库根目录执行该命令。
Linux 启动默认设置 `WEBKIT_GST_DMABUF_SINK_DISABLED=1`、`WEBKIT_GST_USE_PLAYBIN3=1` 修正视频纹理和重播路径，保留用户显式配置；网页缓存按 WebKit 版本隔离，本地模板数据目录不变。

手动运行 **Validate project** 时，`integration-tests` 默认开启：在 Linux 临时 MySQL 8.4 上测试建库、模板读写、重名回滚与重启持久化，并复用三条真实 Remotion 渲染/沙箱测试。
环境包含 Python 3.12、uv、Node 24、Bun、锁定的 Remotion 依赖、Chrome、FFmpeg/ffprobe、Noto CJK 字体、bubblewrap 和 prlimit；不需要真实模型密钥。
运行报告、环境版本及测试产物上传为 `backend-integration-*`，保留 14 天。普通 push/PR 不启动这些重型集成测试。
勾选 `build-installers` 可同时生成四平台优化构建安装包；普通包由 `api-url` 指定 API 地址，留空为 `http://localhost:20070`。
**Build client** 也支持手动填写 `api-url`。这里的测试包仍使用 release 优化，不等于 Rust debug 编译；CI 临时后端随任务结束清理，不供客户长期连接。

**Debug client**（`.github/workflows/client-debug.yml`）在 `main` / `dev` 推送时自动运行，也支持 `workflow_dispatch`，不监听 PR。它向共享构建传入 `debug-backend: true`，由构建设置 `IMV_DEBUG="true"`，不再依赖分支名称；普通构建和正式发布默认 `false`。Debug 安装包的 artifact 名称为 `intelligent-mix-video-debug-<平台>-<提交 SHA>`。网页手动入口要求工作流已存在于默认分支，之后可选择含该工作流的目标分支；`push.branches` 不改变这一要求。

Debug 包中，Linux AppImage/deb、Windows MSI/NSIS 和 macOS 双架构 DMG 额外携带完整 `server/`、Python 3.12、uv、MySQL（Linux 8.0，Windows/macOS 8.4.8）、Node 24、Bun、Remotion、Chrome、FFmpeg/ffprobe及字体；Linux 另带 bubblewrap/prlimit 渲染隔离工具。这个标记编译进客户端，双击无需设置变量或另装后端。首次运行展开运行时并初始化私有数据库，界面等待 API 的真实就绪回执后使用回环端口（未保存启动端口时自动分配），覆盖构建时的 `api-url`。Unix 数据库使用私有 socket；Windows 使用随机密码和动态回环端口，不使用宿主 MySQL；退出客户端会关闭 API 和数据库。

Linux 数据、模型配置和日志位于 `${XDG_DATA_HOME:-~/.local/share}/com.intelligentmixvideo.client/backend/`；Windows 为 `%APPDATA%/com.intelligentmixvideo.client/backend/`，macOS 为 `~/Library/Application Support/com.intelligentmixvideo.client/backend/`。目录内容分别为 `mysql/`、`.env`、`server.log`；Remotion 数据在 `remotion/`。首次从无密钥 `.env.example` 创建配置，已有配置及数据不覆盖。可在 Debug 设置中填写模型、ASR、云合成和启动配置，保存后重启；启动从同一应用的 `data/settings/settings.json` 加载本地值覆盖进程环境，不改写 `.env`，数据库不接入设置；未配置凭据也能启动 API、测试模板读写，但云服务相关功能仍需有效配置和网络。Linux 运行时缓存位于 `${XDG_CACHE_HOME:-~/.cache}/com.intelligentmixvideo.client/backend/`；Windows 为 `%LOCALAPPDATA%/com.intelligentmixvideo.client/backend/`，macOS 为 `~/Library/Caches/com.intelligentmixvideo.client/backend/`。安装包不会包含开发机 `.env` 或数据库。

Linux 内置后端需要 glibc 2.35+；macOS 使用对应架构的 macOS 15 构建机与运行时，Windows 使用 x64 原生运行时。普通非 debug 包仍连接外部 API。构建时从最终 AppImage/MSI/DMG 解包，验证工具可执行、首次启动、模板写入与重启持久化、重复实例拒绝、退出清理和桌面连接。FFmpeg/ffprobe 在 CI 从官方 `FFmpeg/FFmpeg` 最新稳定 tag 固定提交下载源码并原生编译，归档包含版本、提交与许可证；不使用 nightly 或第三方 Release 二进制。Remotion 新字效的隔离生成目前仍仅支持 Linux，依赖非特权 user namespace；Windows/macOS 先支持内置 API、模板存储及已有预览，携带渲染依赖不等于新字效生成已支持。实际 Wayland 桌面播放仍需机器验证。仅在 CI 安装软件不会增加 AppImage 体积；现在通过资源归档携带运行时，包体积会增加。

Wayland 卡顿可在相同场景分别运行 `IMV_GDK_BACKEND=wayland ./应用.AppImage` 和 `IMV_GDK_BACKEND=x11 ./应用.AppImage` 对比。
该选项在 GTK 初始化前覆盖 AppImage hook 的后端设置，默认不改变后端或关闭硬件加速；排除旧 Wayland 库和构建通过不代表已验证帧率改善。

从 Actions 对应运行的 Artifacts 下载产物，保留 14 天。CI 使用依赖锁文件，不需要额外配置发布密钥。
当前安装包未配置代码签名或 macOS 公证；正式分发时需另行配置。

涉及 CI、原生代码或两端版本清单/锁文件时，验证流程运行 actionlint、CI 调度与发布回归测试，
检查两端版本一致，并在临时副本中验证版本同步及 Bun、Cargo、uv 锁文件。服务端改动运行 pytest 和 `uv build --project server`。
同一分支的新提交会取消旧检查；PR 与 push 使用独立并发组，避免相互取消。

提交前检查
----------

根目录的 `.pre-commit-config.yaml` 为 pre-commit.ci 提供基础检查：空白和文件末尾、
YAML / JSON / TOML 与 Python 语法、合并冲突、文件名大小写冲突和私钥检测。
TypeScript 配置允许注释，由客户端 CI 中的 TypeScript 检查验证。
机器人修复和依赖更新的提交信息遵循 Conventional Commits。

本地安装 [uv](https://docs.astral.sh/uv/) 后，在仓库根目录执行：

```sh
uvx pre-commit run --all-files
# 可选：安装本地 Git 提交钩子
uvx pre-commit install
```

Bun / Rust 构建和发布脚本验证仍由 GitHub Actions 执行。

Tag 发版
--------

`.github/workflows/release.yml` 在推送 `vX.Y.Z` 格式的正式版本 tag 时触发。
流程会先校验 tag 格式及两端源码版本，再复用客户端 CI 构建 Windows x64、Linux x64、
macOS ARM64 / Intel 安装包；所有构建成功后才创建 GitHub Release。
安装包和 `CHANGELOG.md` 都会作为 Release 附件上传，Release 正文使用自动生成的变更记录。

**client 与 server 版本统一，源码版本必须与正式 tag 一致。** 发版前执行一次脚本，
同步客户端 `package.json`、`tauri.conf.json`、`Cargo.toml`、`Cargo.lock`，以及服务端
`pyproject.toml`、`uv.lock` 的项目自身版本，并将这些变更提交、合并。第三方依赖版本与
`bun.lock` 保持不变。Tauri 和 FastAPI 文档分别读取应用配置与已安装的服务端包版本。
CI 会拒绝版本不一致；正式构建直接使用 tag 对应源码，不再临时改版。

例如在仓库根目录准备 `0.3.0`：

```sh
RELEASE_TAG=v0.3.0 bun .github/scripts/validate-release.mjs --write
bun .github/scripts/validate-release.mjs
# 将版本变更提交并合入 main 后：
git tag -a v0.3.0 -m "Release v0.3.0"
git push origin v0.3.0
```

PowerShell 先执行 `$env:RELEASE_TAG = "v0.3.0"`，再运行同一条 `bun ... --write` 命令。

日志使用与 [HydroRoll 示例](https://github.com/HydroRoll-Team/HydroRoll/blob/main/.github/workflows/changelog.yml)
相同的 `requarks/changelog-action`，按 Conventional Commits 分类生成。
比较范围为前一个祖先版本 tag 到当前 tag；首次发布以仓库最初提交为基线，不包含最初的引导提交。
发布成功后，机器人用 `docs: update CHANGELOG.md for vX.Y.Z [skip ci]`
提交到仓库的默认分支，不移动 tag，也不把默认分支源码混入安装包。

无需额外发布密钥，使用内置 `GITHUB_TOKEN`；仓库策略必须允许该 job 的 `contents: write`
权限及机器人向默认分支提交。若默认分支保护规则禁止此类提交，需允许机器人写入后重跑失败的 job。
发布先创建或更新草稿，校验六个安装包与 `CHANGELOG.md` 全部上传完成后才公开。
失败时可在 Actions 中重跑；草稿可继续上传，已公开 Release 的附件和正文保持不变，日志回写可单独补齐。
不同 tag 独立运行，避免互相取消等待中的发布。日志回写每次获取最新默认分支，
仅合并当前版本条目，按版本号降序排列；遇到并发提交最多尝试五次，不强制推送，也不重复插入已有版本。
目前只接受正式版本（不含 `-beta` / `-rc`），且版本须满足 Windows MSI 的数值限制。
正式构建仍使用上述未签名安装包配置，代码签名和 macOS 公证需另行接入。
