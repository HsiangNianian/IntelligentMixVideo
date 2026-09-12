# 项目协作指南

本文适用于整个仓库，记录项目已确定的结构、开发和发布约定。需求发生变化时，同步更新相关实现、README 和本文件。

## 项目结构与当前范围

- 这是一个 monorepo，目录名使用小写的 `client/` 和 `server/`。
- `client/` 是 Rust + Tauri 2 + React + TypeScript 桌面客户端，前端使用 Vite，包管理器与脚本运行时使用 Bun。
- `client/src/` 存放 React 前端；`client/src-tauri/` 存放 Rust 桌面入口、Tauri 配置和图标。
- `server/` 使用 Python + FastAPI，当前提供首页与用户路由示例，尚未接入用户存储。包内导入使用相对路径，向应用注册 `APIRouter` 实例。
- 在 `server/` 下执行 `uv run server` 启动 Uvicorn，默认监听 `127.0.0.1:8000`；仓库根目录使用 `uv run --project server server`。维护 `server/uv.lock`，CI 使用 `--locked` 验证依赖。
- `server/pyproject.toml` 显式将官方 PyPI 设为 uv 默认索引，与锁文件来源保持一致。遇到依赖版本不可用时先检查索引覆盖配置和镜像同步情况，不要仅为绕过镜像缺失而降低依赖版本或删除锁文件。
- 当前客户端保持最小可运行结构。`App.tsx` 挂载 `pages/HomePage.tsx`，首页引用独立的 `components/CurrentTime.tsx`，按本机时区显示日期和时间，每秒刷新。
- 时间组件需在卸载时清理定时器。新增界面功能时遵循组件化结构，不把所有逻辑堆到 App 首页。
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
- `src/components/` 放当前业务组件，例如 `CurrentTime.tsx`；`src/components/ui/` 放 shadcn/ui 基础组件，只负责可组合的 UI，不导入页面、不请求 API、不调用 Tauri command。
- `src/lib/` 放实际共享的工具，当前只有合并类名的 `cn`。真实复用后再增加 `hooks/`；业务增长时才按功能提取 `features/<功能>/`，不提前创建空层。
- 依赖方向为页面 → 业务组件 → 基础 UI / 工具。跨层导入使用 `@/`（指向 `src/`）；文件内或同目录的相对导入可保留。组件命名保持现有 PascalCase，shadcn/ui 文件遵循上游小写命名。
- 有交互和无障碍语义的基础控件优先使用 shadcn/ui；在 `client/` 执行 `bunx --bun shadcn@latest add <组件>` 按需添加，随后审阅生成代码、依赖和主题令牌，只保留有调用方的导出。不要预装整个组件库。
- `client/components.json` 维护 shadcn/ui 路径与别名；`src/styles/globals.css` 是全局样式入口，只放 Tailwind 导入、主题令牌和基础样式。组件布局使用工具类，颜色使用语义令牌，条件类名通过 `cn` 合并。
- 主题令牌按使用需求补齐，不预先创建暗色切换、动画、路由或状态管理设施。局部状态优先留在组件；effect 必须清理定时器、订阅及监听器。重复渲染组件的关联 ID 用 `useId`，保留语义 HTML 和无障碍属性。
- 当前时间只需本机时钟，不增加服务端请求。后续实际出现 API/Tauri 通信时再集中到对应功能模块，避免在纯展示组件中散落请求与错误处理。
- 前端改动运行冻结依赖安装和 `bun run build`，并检查浏览器/桌面中的相关行为。新增 feature 必须提供行为测试；测试方案按实际运行环境选择，不用 Python 强行测试 React。纯样式调整验证渲染与响应式，不写重复实现的断言。

## Feature 测试约束（强制）

- 每次新增 feature 必须同时提交详细、覆盖全面且可重复执行的测试脚本；修复 bug 必须增加能够复现问题的回归用例。不能只测成功路径，也不能只断言函数被调用或复制实现来凑测试数量。
- 服务端测试统一放在 `server/tests/`，使用 pytest 的 `test_*.py`、fixture 和参数化用例；共享夹具放 `conftest.py`，不再新增 unittest 风格测试。按功能组织文件，规模增大后再分目录。
- 根据功能适用范围覆盖正常流程、异常输入、边界值、空数据、失败恢复、资源清理，以及涉及的权限、并发与幂等行为。不存在的能力不为凑覆盖率编写空测试；提交说明列出已覆盖场景与实际限制。
- API 用例应检查状态码、响应契约和副作用。测试隔离外部服务、密钥和持久化数据，使用 fixture、monkeypatch 或临时目录；不得访问生产系统、依赖执行顺序或使用无界等待。
- 文件头说明测试范围与执行方式，测试函数/夹具的 docstring 说明场景和期望。每次功能改动运行相关用例，交付前运行所属模块的完整测试；CI 使用锁定依赖运行服务端 pytest，测试失败必须修复。
- 服务端目录执行 `uv run --locked pytest -v`；仓库根目录执行 `uv run --locked --project server pytest server/tests -v`。pytest 仅作为开发依赖维护在 `server/pyproject.toml` 和 `server/uv.lock`。

## 开发与验证

客户端命令在 `client/` 下执行：

```sh
bun install --frozen-lockfile
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

`.github/workflows/client-build.yml` 负责客户端跨平台构建，支持分支 push、PR、手动触发和 `workflow_call` 复用。
路径过滤覆盖客户端、该工作流及 `.github/scripts/`。
保留平台矩阵、Linux 系统依赖安装、Bun / Rust 缓存和构建产物上传。

| 平台 | 架构 | 安装包 |
| --- | --- | --- |
| Windows | x64 | NSIS exe、MSI |
| Linux | x64 | deb、AppImage |
| macOS | Apple Silicon、Intel | dmg |

普通构建的 Actions artifacts 保留 14 天。发版应复用这一构建工作流，避免维护两套不一致的平台构建逻辑。
构建产物来自被触发的提交；正式发布时必须来自对应 tag 的源码。

`.github/workflows/validation.yml` 在相关 push / PR 中检查全部工作流、运行发布脚本与失败恢复测试，
并通过临时副本中的真实配置验证版本注入、Bun 冻结安装和 Cargo 锁文件不变。
该工作流同时测试 API 路由并构建 Python 包；`server/pyproject.toml` 的 uv 构建模块名显式设为 `server`，对应 `src/server/`。

## 正式版本与 tag 发版

**正式发布版本以 tag 为唯一来源，不要求手动同步多个版本文件。**

- `.github/workflows/release.yml` 在推送 `v*` tag 时触发，校验后仅接受 `vX.Y.Z` 正式版本，不接受 `-beta`、`-rc` 或构建元数据。
- 版本须符合 Windows MSI 限制：major、minor 不超过 255，patch 不超过 65535。
- 发布工作流通过 `release-tag` 输入把 tag 传给复用的客户端构建工作流。
- 各平台在 CI 构建工作区中执行版本注入，例如 `v0.2.0` 转为 `0.2.0`。
- 自动同步 `client/package.json`、`client/src-tauri/tauri.conf.json`、`client/src-tauri/Cargo.toml` 及 `client/src-tauri/Cargo.lock` 的客户端包版本。`client/bun.lock` 不记录根项目版本，发版时保持其内容不变，并通过 `bun install --frozen-lockfile` 验证。
- 只修改项目自身版本，保留所有已锁定的第三方依赖版本及校验信息；不得借版本注入更新依赖。
- Tauri 应用版本当前由 `tauri.conf.json` 的 `version` 提供，正式构建时该值由 tag 注入。
- 版本修改只用于当前构建，不提交回源码、不移动 tag。普通开发构建继续使用仓库中的开发版本。

版本脚本为 `.github/scripts/validate-release.mjs`，通过环境变量 `RELEASE_TAG` 接收 tag：

- `--tag-only`：只校验 tag 格式及版本范围，不要求源码中的开发版本与 tag 一致。
- `--write`：将 tag 版本写入当前工作区的配置和锁文件。只在 CI 或临时副本中验证此模式，避免意外改动本地开发版本。
- 不带参数：验证配置和锁文件的客户端版本均与 tag 一致，用于注入后校验。

发版操作示例（先提交源码，确保该提交包含发布工作流）：

```sh
git tag -a v0.2.0 -m "Release v0.2.0"
git push origin v0.2.0
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
