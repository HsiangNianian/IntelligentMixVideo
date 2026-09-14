# 项目协作指南

本文适用于整个仓库，记录项目已确定的结构、开发和发布约定。需求发生变化时，同步更新相关实现、README 和本文件。

## 项目结构与当前范围

- 这是一个 monorepo，目录名使用小写的 `client/` 和 `server/`。
- `client/` 是 Rust + Tauri 2 + React + TypeScript 桌面客户端，前端使用 Vite，包管理器与脚本运行时使用 Bun。
- `client/src/` 存放 React 前端；`client/src-tauri/` 存放 Rust 桌面入口、Tauri 配置和图标。
- `server/` 使用 Python + FastAPI + MySQL，提供模板持久化 API 与 `POST /segmentations` 文案切片接口，首页与用户路由仍为示例、尚未接入用户存储。包内导入使用相对路径，向应用注册 `APIRouter` 实例。
- 模板模块位于 `server/src/server/template/`，与用户示例目录 `sub_api/` 平级；路由、配置校验、数据库存储与效果目录均放在该模块内。
- `server/src/server/asr/` 提供独立的 `transcribe` 函数与 `python -m server.asr` 命令行入口，尚未注册 HTTP 路由；通过北京地域 Fun-ASR 接收 HTTPS 音频直链并返回原始转写 JSON。`DASHSCOPE_API_KEY` 在模块加载时读取一次，优先源码 `server/.env`，不存在时回退工作目录 `.env`，进程环境变量优先；测试隔离文件、密钥、HTTP 和轮询等待。
- 在 `server/` 下执行 `uv run server` 启动 Uvicorn，默认监听 `127.0.0.1:8000`；仓库根目录使用 `uv run --project server server`。维护 `server/uv.lock`，CI 使用 `--locked` 验证依赖。
- `server/pyproject.toml` 显式将官方 PyPI 设为 uv 默认索引，与锁文件来源保持一致。遇到依赖版本不可用时先检查索引覆盖配置和镜像同步情况，不要仅为绕过镜像缺失而降低依赖版本或删除锁文件。
- `App.tsx` 挂载 `pages/HomePage.tsx`，首页组合 `features/templates/` 模板工作区；效果编辑、SDK 预览、API 请求、数据契约与时间线转换按职责分离。
- 组件卸载时清理定时器、订阅和播放器。新增界面功能遵循组件化结构，不把所有逻辑堆到 App 首页。
- 项目长期方向见 README；其中提到的云剪辑、Agent、素材召回等功能不代表已经实现，也不构成自动扩展当前任务范围的要求。

## 最小改动与源码说明（强制）

- 所有代码改动必须 minimal：只实现当前需求所必需的行为，优先直接、可读的实现。不得加入未使用的代码、无意义包装、空占位文件、推测未来需求的抽象或依赖。
- 不为了形式统一机械拆分文件；只有职责独立或真实复用需要时才提取模块、hook、工具函数。新增依赖、组件、配置项必须有实际使用方。
- 仓库内维护的所有代码文件都必须有文件头注释，简要说明文件包含什么、职责边界和主要执行或数据流。覆盖 TS/TSX、JS/JSX/MJS、CSS、Rust、Python，以及后续新增语言；测试和构建/发布脚本也适用。
- 每个模块、类、组件、函数/hook、具备独立职责的对象和复杂算法都必须有 docstring 或注释，说明用途、逻辑及必要的输入输出、副作用、清理和边界条件。匿名回调或简单字面量由所在逻辑块说明即可，不逐行复述语法；具名测试的描述可作为测试逻辑说明。
- TS/JS 使用 JSDoc 或紧邻声明的注释；Python 使用模块、类、函数 docstring；Rust 使用 `//!` 模块文档与 `///` 项目文档；CSS 用注释说明主题令牌、基础层和复杂选择器。注释简短、具体，并随代码同步维护。
- shebang、编码声明、编译器指令等有位置要求时保留必要顺序。JSON 等不支持注释的配置不要强塞注释，在相邻文档说明；锁文件、生成代码及第三方原样文件不手工加头注释。纳入项目维护的 shadcn/ui 源码需补齐注释，并保留第三方许可证。
- 评审检查实际调用、注释与实现一致、无死代码及无无关依赖升级；不能用注释数量替代可读性和有效验证。

## 客户端前端开发范式（强制）

- 技术栈固定为 React + TypeScript strict + Vite + Tailwind CSS 4 + shadcn/ui，使用 Bun 管理依赖。Tailwind 使用 `@tailwindcss/vite`；不混用 Tailwind 3 配置或另加样式框架。
- `src/main.tsx` 只挂载应用和全局样式；`src/App.tsx` 只组合页面与确有需求的全局 provider；`src/pages/` 负责页面布局和组件组合。
- `src/features/templates/` 放模板业务组件与通信逻辑；其他业务组件可放 `src/components/`；`src/components/ui/` 放 shadcn/ui 基础组件，只负责可组合的 UI，不导入页面、不请求 API、不调用 Tauri command。
- `src/lib/` 放实际共享的工具，当前只有合并类名的 `cn`。真实复用后再增加 `hooks/`；业务增长时才按功能提取 `features/<功能>/`，不提前创建空层。
- 依赖方向为页面 → 业务组件 → 基础 UI / 工具。跨层导入使用 `@/`（指向 `src/`）；文件内或同目录的相对导入可保留。组件命名保持现有 PascalCase，shadcn/ui 文件遵循上游小写命名。
- 有交互和无障碍语义的基础控件优先使用 shadcn/ui；在 `client/` 执行 `bunx --bun shadcn@latest add <组件>` 按需添加，随后审阅生成代码、依赖和主题令牌，只保留有调用方的导出。不要预装整个组件库。
- `client/components.json` 维护 shadcn/ui 路径与别名；`src/styles/globals.css` 是全局样式入口，只放 Tailwind 导入、主题令牌和基础样式。组件布局使用工具类，颜色使用语义令牌，条件类名通过 `cn` 合并。
- 主题令牌按使用需求补齐，不预先创建暗色切换、动画、路由或状态管理设施。局部状态优先留在组件；effect 必须清理定时器、订阅及监听器。重复渲染组件的关联 ID 用 `useId`，保留语义 HTML 和无障碍属性。
- 模板 API 请求集中在 `features/templates/api.ts`，SDK 加载与预览在独立模块，避免在纯展示组件中散落请求与错误处理。
- 前端改动运行冻结依赖安装和 `bun run build`，并检查浏览器/桌面中的相关行为。新增 feature 必须提供行为测试；测试方案按实际运行环境选择，不用 Python 强行测试 React。纯样式调整验证渲染与响应式，不写重复实现的断言。

## 模板功能约定

- 云端模板库共享，不包含登录、用户隔离或旧数据迁移，配置存入 MySQL，使用 SQLAlchemy 和 PyMySQL。页面提供本地 / 云端下拉框；默认为云端环境，连接失败、超时或服务端 5xx 时提示用户手动切换本地。桌面通过 `@tauri-apps/api/core` 的 `invoke` 调用 Tauri `local_templates` 命令，使用 `isTauri()` 判断桌面环境，不开启 `withGlobalTauri`；命令读写应用数据目录的 `data/template/templates.json`，不请求 Python 服务；浏览器本地模式明确报错。两库独立，切换复用未保存保护，目标读取失败保留原环境和草稿。本地 JSON 通过文件锁和临时文件原子替换保护；本地校验及效果快照使用随包 SDK 目录，预览仍需联网。Rust 存储测试使用临时目录，在 `client/` 执行 `cargo test --locked --manifest-path src-tauri/Cargo.toml`。
- 数据库配置由 `database.py` 的 `DatabaseSettings`（`pydantic-settings`）自动读取固定的 `server/.env`，字段为 `DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD`、`DB_NAME`，进程环境变量优先；端口校验 1～65535，库名校验 1～64 字符。启动初始化时加载，修改后重启服务。真实环境文件不得入库，维护无密码示例 `.env.example`。
- 数据库、ASR 与切片模型配置共用 `server/.env.example`；从 `server/` 启动可读取全部配置，根目录启动且需要切片模型配置时使用 `uv run --project server --env-file server/.env server`，因为切片设置按当前目录查找 `.env`。
- FastAPI lifespan 启动时连接目标库，仅在 MySQL 返回 1049（库不存在）时通过临时无库连接执行 `CREATE DATABASE IF NOT EXISTS`，使用 `utf8mb4` / `utf8mb4_bin` 并正确引用库名；已有库直接复用。配置无效、连接或建库失败时停止启动，建库账号须具备对应权限。首次模板请求自动创建缺失表；运行中的数据库失败返回可重试的 503。启动失败和退出时释放连接池，临时建库连接始终关闭。
- 四个路由为 `GET /template`、`POST /template`、`GET /template/{template_id}`、`DELETE /template/{template_id}`。POST 无 ID 创建（201），有 ID 完整更新（200）；不存在的 ID 返回 404，不做 upsert。
- 名称去除首尾空白后不能为空，MySQL 唯一约束拒绝重名（409）；保存校验数值范围、效果目录与动画互斥关系（422）。服务端生成 ID、UTC 时间和效果参数快照，不接受客户端渲染参数。
- 重命名和另存为复用 POST；另存为不携带 ID。未保存切换须提供保存并切换、放弃修改、取消，失败保留草稿。删除前确认。
- 前端使用 SDK 5.2.2 的效果目录和静态动画 JSON，服务端维护同版本白名单；不提供 `/template/effects`。升级 SDK 时同步核对目录。保留用户已有 proto 文件，本次 API 使用 JSON。
- 示例视频地址通过 `client/.env` 中的 `VITE_PREVIEW_VIDEO_URL` 配置，支持 HTTP(S) 直链与 public 资源路径；空值回退内置示例。修改后重启 Vite，生产使用需重新构建；当前固定片段要求源视频至少 14 秒。
- 预览保留阿里云 SDK 5.2.2；Windows 生产页面通过 `src-tauri/src/localhost.rs` 在 `127.0.0.1` 的系统分配端口提供打包资源，窗口使用 `http://localhost:<端口>`，避免 `tauri.localhost` 不满足空 License 的 localhost 预览条件。仅允许对应 Host 的 GET/HEAD，不暴露任意磁盘文件；开发模式与 macOS / Linux 保持原加载方式，不扩大 Tauri IPC 权限。
- API 地址读取 `client/.env` 的 `VITE_API_URL`，未配置或留空时默认 `http://localhost:8000`；CORS 允许精确 localhost 主机的动态 HTTP 端口。修改配置后重启 Vite，生产需重新构建；避免 `.env.local` 同名配置覆盖。模板列表加载不阻塞本地编辑，但保存须防止晚到列表覆盖结果。预览仍需联网获取 SDK、字体和媒体，不发起云端合成，不将浏览器验证等同于桌面安装包验证。

## Feature 测试约束（强制）

- 每次新增 feature 必须同时提交详细、覆盖全面且可重复执行的测试脚本；修复 bug 必须增加能够复现问题的回归用例。不能只测成功路径，也不能只断言函数被调用或复制实现来凑测试数量。
- 服务端测试统一放在 `server/tests/`，使用 pytest 的 `test_*.py`、fixture 和参数化用例；共享夹具放 `conftest.py`，不再新增 unittest 风格测试。按功能组织文件，规模增大后再分目录。
- 客户端核心测试在 `client/tests/`，使用 Bun 自带运行器、Happy DOM 和 React Testing Library；在 `client/` 执行 `bun run test`，每个用例上方写中文场景注释。测试隔离 HTTP 与 SDK，不连接真实服务；只覆盖必要业务行为，不把模拟 DOM 验证等同于真实视频播放、浏览器原生表单校验或 Tauri 验证。`bun run build` 同时检查测试类型。
- 根据功能适用范围覆盖正常流程、异常输入、边界值、空数据、失败恢复、资源清理，以及涉及的权限、并发与幂等行为。不存在的能力不为凑覆盖率编写空测试；提交说明列出已覆盖场景与实际限制。
- API 用例应检查状态码、响应契约和副作用。测试隔离外部服务、密钥和持久化数据，使用 fixture、monkeypatch 或临时目录；不得访问生产系统、依赖执行顺序或使用无界等待。
- 共享服务端夹具保留临时 SQLite 数据库隔离，同时自动清除外部 `IMV_`、`DASHSCOPE_API_KEY` 与旧 `ASR_BASE_URL` 环境变量并切换临时目录，避免读取本机模型配置；ASR 首次导入屏蔽 `.env`，请求使用内存传输。配置用例显式注入，不访问真实 MySQL、ASR 或模型服务。
- 文件头说明测试范围与执行方式，测试函数/夹具的 docstring 说明场景和期望。每次功能改动运行相关用例，交付前运行所属模块的完整测试；CI 使用锁定依赖运行服务端 pytest，测试失败必须修复。
- 服务端目录执行 `uv run --locked pytest -v`；仓库根目录执行 `uv run --locked --project server pytest server/tests -v`。pytest 仅作为开发依赖维护在 `server/pyproject.toml` 和 `server/uv.lock`。

## 开发与验证

客户端命令在 `client/` 下执行：

```sh
bun install --frozen-lockfile
bun run test
bun run tauri dev
bun run build
bun run tauri build
```

- `bun run tauri dev` 启动桌面开发环境；`bun run dev` 仅启动 Vite 前端。
- `bun run build` 包含 TypeScript 检查和前端生产构建，不等同于桌面程序构建成功。
- 本地桌面安装包默认输出到 `client/src-tauri/target/release/bundle/`；指定 target 时位于对应 target 子目录。
- 保留并维护 `client/bun.lock` 和 `client/src-tauri/Cargo.lock`。CI 使用 `bun install --frozen-lockfile` 和 Cargo `--locked`。
- 根据改动范围验证：前端改动运行前端构建；Rust 改动检查格式并验证相关编译；服务端改动运行 pytest；发布脚本改动运行其测试；工作流改动使用 actionlint 检查。新增 feature 还必须满足上面的测试约束。
- 纯文档改动检查内容与实现一致及 diff 格式，无需重跑全部构建。

以下命令在仓库根目录执行：

```sh
cargo fmt --manifest-path client/src-tauri/Cargo.toml --check
bun test ./.github/scripts
bun .github/scripts/release-smoke.mjs
actionlint .github/workflows/client-build.yml .github/workflows/release.yml .github/workflows/validation.yml
uv build --project server --out-dir server/dist
uv run --locked --project server pytest server/tests -v
git diff --check
```

根目录 `.pre-commit-config.yaml` 供 pre-commit.ci 和本地检查共用。
修改其配置时执行 `uvx pre-commit validate-config` 和 `uvx pre-commit run --all-files`。
保持机器人提交信息符合 Conventional Commits。基础 hooks 不依赖本机 Bun / Rust；
构建及发布脚本验证由 GitHub Actions 承担。带注释的 tsconfig JSON 由 TypeScript 验证。

开发环境需要 Bun（版本以 client/package.json 的 packageManager 字段为准）、Rust stable 和对应平台的 Tauri 2 依赖。
CI 使用 `oven-sh/setup-bun` 读取同一版本。`client/bunfig.toml` 的 `run.bun = true` 让 Vite、TypeScript 和 Tauri CLI 使用 Bun 运行。
维护 Bun 文本锁文件 `bun.lock`，不要重新引入其他 JavaScript 包管理器的锁文件。
Windows 需要 MSVC C++ 构建工具、Windows SDK 和 WebView2；ARM64 主机需要匹配架构的 C++ 工具。
若出现 `link: extra operand`，检查是否误用了 Cygwin 的 `link.exe`，以及 Visual Studio C++ 工具链是否安装完整。
这类错误属于环境问题，应报告实际验证范围，不把前端构建通过描述为桌面构建或跨平台 CI 全部通过。

## 客户端构建 CI

`.github/workflows/validation.yml` 是 push / PR 检查的唯一 Actions 入口，不在工作流级使用 paths 过滤，以确保每个 PR 都有最终检查结果。
`.github/scripts/ci-scope.mjs` 根据 Git 差异调度：PR 比较目标分支与合并结果，push 比较前后提交；新分支检查全部文件，缺失比较基线时保守运行全部检查。

- 非默认分支 push：若同一仓库的同一提交已有打开的 PR，跳过重复任务；否则按变更范围检查。默认分支始终验证集成结果，不参与去重。
- PR：前端改动运行冻结安装、核心测试与生产构建；服务端改动运行 pytest 与包构建；Rust/Tauri 或客户端依赖清单改动运行四平台 `cargo check --all-targets --locked`，不生成安装包。CI 工作流或脚本改动触发所有相关检查。
- Windows 原生检查同时运行 `cargo test --lib --locked`，验证回环静态资源服务的 HTTP 行为；桌面真实预览仍需单独验证。
- 默认分支 push：涉及客户端代码、资源或 CI 时生成四平台安装包。默认分支名称从事件读取，不硬编码 main。纯服务端改动不构建客户端，纯 Markdown 客户端文档不触发编译。
- pre-commit.ci 负责 PR 的文件检查；Actions 仅在未去重的 push 或手动检查中运行 pre-commit，避免重复执行。
- PR 汇总检查名为 `CI result`，push 使用 `Push result`，避免重复 push 的成功结果冒充 PR 验证。汇总必须在依赖失败/取消后执行，意外跳过必需任务不得报告成功。
- 分支保护建议要求 `CI result` 和 pre-commit.ci 的检查；不要要求按路径跳过的矩阵 job。修改工作流不会自动修改仓库保护规则。
- 同一事件/分支的新运行取消旧运行；push 与 PR 的并发组分开，不互相取消。去重查询失败必须报告错误，不能静默放行。

`.github/workflows/client-build.yml` 仅提供手动触发和 `workflow_call`，复用同一平台矩阵、Linux 系统依赖和 Bun / Rust 缓存。
`build-mode=check` 做原生编译检查，`package` 生成并上传安装包；默认 `package`，正式 tag 必须使用打包模式。检查模式不能保证最终链接或安装器成功，完整打包由默认分支、tag 和手动构建验证。

| 平台 | 架构 | 安装包 |
| --- | --- | --- |
| Windows | x64 | NSIS exe、MSI |
| Linux | x64 | deb、AppImage |
| macOS | Apple Silicon、Intel | dmg |

普通构建的 Actions artifacts 保留 14 天。发版应复用这一构建工作流，避免维护两套不一致的平台构建逻辑。
Linux 构建安装 GStreamer base/good/bad/libav 插件供 AppImage 收集；上传前在构建机运行 `.github/scripts/appimage-media-smoke.sh <AppImage路径>`，隔离插件路径和缓存，检查包内插件来源及 H.264/AAC 解码、WebVTT 支持。宿主机插件检测不能代替产物验证；此无界面检查不代表真实 SDK 或桌面预览通过。
构建产物来自被触发的提交；正式发布时必须来自对应 tag 的源码。

涉及 CI、原生代码或两端版本清单/锁文件时，Release preflight 检查全部工作流、运行调度与发布脚本测试，
校验两端源码版本一致，并通过临时副本验证版本同步、Bun 冻结安装、Cargo 和 uv 锁文件。
服务端 job 使用 Python 3.12 和 uv 缓存；`server/pyproject.toml` 的 uv 构建模块名显式设为 `server`，对应 `src/server/`。
在 Validate project 上手动运行会执行全部检查（原生检查模式）；需要安装包时手动运行 Build client。

## 正式版本与 tag 发版

**client 与 server 使用同一版本；发版前用一个命令同步并提交源码，正式 tag 必须与全部版本一致。**

- `.github/workflows/release.yml` 在推送 `v*` tag 时触发，校验后仅接受 `vX.Y.Z` 正式版本，不接受 `-beta`、`-rc` 或构建元数据。
- 版本须符合 Windows MSI 限制：major、minor 不超过 255，patch 不超过 65535。
- 发布工作流通过 `release-tag` 输入把 tag 传给复用的客户端构建工作流。
- 发版前通过脚本将 `v0.3.0` 同步为 `0.3.0`，提交到 dev，经 PR 合入 main 后再在该提交打 tag。不能只在 CI 临时改版而让源码长期停留旧版本。
- 自动同步 `client/package.json`、`client/src-tauri/tauri.conf.json`、`client/src-tauri/Cargo.toml` 与 `Cargo.lock` 的客户端包版本，以及 `server/pyproject.toml` 与 `server/uv.lock` 的 `imv-server` 包版本。`client/bun.lock` 不记录根项目版本，保持原样并用冻结安装验证。
- 只修改项目自身版本，保留所有已锁定的第三方依赖版本及校验信息；不得借版本同步更新依赖。
- Tauri 应用版本由 `tauri.conf.json` 的 `version` 提供；FastAPI/OpenAPI 版本读取已安装的 `imv-server` 包元数据，不增加另一处硬编码版本。
- 普通 CI 检查源码两端版本一致；tag CI 只校验源码与 tag 一致后构建，不自动修正不匹配版本、不移动 tag。

版本脚本为 `.github/scripts/validate-release.mjs`，通过环境变量 `RELEASE_TAG` 接收 tag：

- `--tag-only`：只校验显式 tag 的格式及版本范围，不能替代发版前的完整版本校验。
- `--write`：必须显式设置 `RELEASE_TAG`，一次更新上述六处项目版本；检查 diff 并提交这些变更。回归测试只在临时副本使用此模式。
- 不带参数：有 `RELEASE_TAG` 时检查两端全部版本与 tag 一致；未设置时以 `client/package.json` 为基准检查版本一致性。

发版操作示例（在仓库根目录同步版本，提交并合并后再打 tag）：

```sh
RELEASE_TAG=v0.3.0 bun .github/scripts/validate-release.mjs --write
bun .github/scripts/validate-release.mjs
# 将版本变更提交并合入 main 后：
git tag -a v0.3.0 -m "Release v0.3.0"
git push origin v0.3.0
```

## GitHub Release 与 CHANGELOG

- 四个平台 / 架构的构建全部成功后，再发布 GitHub Release。安装包及 `CHANGELOG.md` 作为附件上传。
- 使用 `requarks/changelog-action`，按 Conventional Commits 分类生成变更记录；Release 正文使用其输出。
- 显式指定前一个祖先版本 tag 到当前 tag 的比较范围。首次发版使用仓库最初提交作为基线，不包含该引导提交，避免 action 默认要求已有两个 tag 的限制。
- 使用 `ncipollo/release-action` 上传到草稿，随后通过 `release-assets.mjs` 校验六个安装包及 `CHANGELOG.md` 的文件名、大小和上传状态，全部通过后才公开。上传失败必须保持草稿状态。
- 重跑可修复草稿或补齐日志回写；已公开 Release 的附件与正文不再修改。
- 草稿校验使用上传 action 返回的 Release ID 查询，并确认其 tag 匹配；不要通过按 tag 查询接口查找未公开草稿。
- 发布并发组按 tag 隔离，避免不同版本互相替换等待中的运行。
- 发布后通过 `commit-changelog.mjs` 在单独的默认分支 checkout 中仅合并当前版本日志，按版本号降序排列。每次获取最新分支，并发冲突最多尝试五次，不强制推送、不重复插入已有条目，不能假定默认分支永远是 main。
- 自动提交格式为 `docs: update CHANGELOG.md for vX.Y.Z [skip ci]`。
- 不以手工维护正式版本条目的方式替代自动 changelog 流程。
- 使用内置 `GITHUB_TOKEN`；构建 job 使用读权限，发布 job 需要 `contents: write`。默认分支规则必须允许对应的机器人写入。
- 当前未配置 Windows 代码签名和 macOS 公证，不将生成的安装包描述为已签名或已公证。

## Git 与交付约定

- Follow [CONTRIBUTE.md](CONTRIBUTE.md) for contributor requirements. Write commit messages, PR titles, and PR descriptions in English using Conventional Commits, and fill in `.github/pull_request_template.md`.
- The project uses GNU AGPL version 3 only (`AGPL-3.0-only`), documented in `LICENSE.md`. Preserve the license text and applicable third-party notices.

- 提交信息遵循 Conventional Commits，例如 `feat(client): add current time component`、`ci: add tag-based releases`、`docs: document development workflow`。
- 修改前检查当前分支和工作区，保留用户已有改动，不把临时构建结果、`node_modules/`、`dist/` 或 `target/` 纳入提交。
- 用户要求 commit / push 时执行实际提交与推送；普通代码或文档修改请求不自动扩大为打 tag 或正式发版。
- 不把本次会话曾在 dev 分支工作视为永久分支限制；每次提交、推送前检查实际分支及远端。
- 交付时说明完成内容、实际执行的验证及未验证部分，区分“工作流已编写”“本机验证通过”和“GitHub 上已成功构建 / 发布”。
- 修改开发命令、版本来源或发布行为时，同步更新 README 与本指南，尤其不要恢复成手动修改多个版本文件的旧方案。

## 用户指定的参考项目

- 客户端与跨平台构建参考：[HydroRoll-Team/DropOut](https://github.com/HydroRoll-Team/DropOut)。
- 自动 changelog 参考：[HydroRoll changelog.yml](https://github.com/HydroRoll-Team/HydroRoll/blob/main/.github/workflows/changelog.yml)。
- 发布流程参考：[HydroRoll release.yml](https://github.com/HydroRoll-Team/HydroRoll/blob/main/.github/workflows/release.yml)。

参考项目用于理解实现方式；本项目发布的是 Tauri 客户端安装包，不照搬参考项目的 Python wheel / PyPI 发布目标。
