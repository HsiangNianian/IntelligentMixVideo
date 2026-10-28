# 项目协作指南

本文记录适用于整个仓库的约定；各目录的约定写在该目录的 `AGENTS.md`（见下方索引）。只写稳定、容易在局部改错、从代码难以还原的约束，实现细节写进模块 README 或代码注释。约定变化时，同步更新相关实现、README 和负责该约定的那一份 `AGENTS.md`；跨目录的约定只写在负责它的那一份里，其他文件链接过去，不复制。

## 项目结构与当前范围

- 这是一个 monorepo，目录名使用小写的 `client/` 和 `server/`。
- `client/` 是 Rust + Tauri 2 + React + TypeScript 桌面客户端，前端使用 Vite，包管理器与脚本运行时使用 Bun。
- `client/src/` 存放 React 前端；`client/src-tauri/` 存放 Rust 桌面入口、Tauri 配置和图标。
- 项目长期方向见 README；其中提到的云剪辑、Agent、素材召回等功能不代表已经实现，也不构成自动扩展当前任务范围的要求。

目录索引：

| 目录 | 内容 |
| --- | --- |
| `client/` | 应用外壳、前端开发范式（强制）、客户端测试、客户端命令与开发环境 |
| `client/src-tauri/` | 调试日志窗口、预览媒体服务、Windows localhost 页面服务、客户端自动更新 |
| `client/src/features/templates/` | 模板编辑、预览与时间轴，模板中的 Remotion 片段与叠加预览 |
| `client/src/features/remotion_templates/` | 字效工作区：会话、预览、参数、保存资产 |
| `client/src/features/fonts/` | 字体选择组件、字体库页面、上传前修复 |
| `client/src/features/settings/` | 设置模式、客户端配置请求头 |
| `client/src/features/video_composition/` | 操练场界面 |
| `server/` | 后端技术栈、启动、配置与数据库、服务端测试 |
| `server/src/server/template/` | 云端模板保存契约、模板 API、Protobuf 与效果目录 |
| `server/src/server/segmentation/` | 文案切片、提词与翻译 |
| `server/src/server/video_composition/` | 视频合成、数字人效果、操练场接口 |
| `server/src/server/remotion_templates/` | Remotion 字效生成、字体库、资产发布、叠加渲染 |
| `server/src/server/remotion/` | Remotion 渲染资源：renderer 补丁、沙箱字体缓存、worker |
| `.github/` | CI、Debug 客户端与随包后端、版本与 tag 发版、Release 与 CHANGELOG |

在某个目录工作前，先读从根目录到该目录路径上的每一份 `AGENTS.md`。Claude Code 读取某个目录的文件时会自动加载该目录的 `AGENTS.md`；Codex 等只读取启动目录及其上级的工具，需要按上表自行读取。

## 最小改动与源码说明（强制）

- 所有代码改动必须 minimal：只实现当前需求所必需的行为，优先直接、可读的实现。不得加入未使用的代码、无意义包装、空占位文件、推测未来需求的抽象或依赖。
- 不为了形式统一机械拆分文件；只有职责独立或真实复用需要时才提取模块、hook、工具函数。新增依赖、组件、配置项必须有实际使用方。
- 仓库内维护的所有代码文件都必须有文件头注释，简要说明文件包含什么、职责边界和主要执行或数据流。覆盖 TS/TSX、JS/JSX/MJS、CSS、Rust、Python，以及后续新增语言；测试和构建/发布脚本也适用。
- 每个模块、类、组件、函数/hook、具备独立职责的对象和复杂算法都必须有 docstring 或注释，说明用途、逻辑及必要的输入输出、副作用、清理和边界条件。匿名回调或简单字面量由所在逻辑块说明即可，不逐行复述语法；具名测试的描述可作为测试逻辑说明。
- TS/JS 使用 JSDoc 或紧邻声明的注释；Python 使用模块、类、函数 docstring；Rust 使用 `//!` 模块文档与 `///` 项目文档；CSS 用注释说明主题令牌、基础层和复杂选择器。注释简短、具体，并随代码同步维护。
- shebang、编码声明、编译器指令等有位置要求时保留必要顺序。JSON 等不支持注释的配置不要强塞注释，在相邻文档说明；锁文件、生成代码及第三方原样文件不手工加头注释。纳入项目维护的 shadcn/ui 源码需补齐注释，并保留第三方许可证。
- 评审检查实际调用、注释与实现一致、无死代码及无无关依赖升级；不能用注释数量替代可读性和有效验证。

## Feature 测试约束（强制）

- 每次新增 feature 必须同时提交可重复执行的核心功能测试；修复 bug 必须增加能够复现问题的回归用例。测试断言实际业务结果，避免重复验证同一行为。
- 按核心功能涉及的实际风险选择正常流程及必要的异常、边界和恢复场景；不为凑覆盖率增加测试。提交说明列出已覆盖场景与实际限制。
- API 用例应检查状态码、响应契约和副作用。测试隔离外部服务、密钥和持久化数据，使用 fixture、monkeypatch 或临时目录；不得访问生产系统、依赖执行顺序或使用无界等待。
- 文件头说明测试范围与执行方式，测试函数/夹具的 docstring 说明场景和期望。每次功能改动运行相关用例，交付前运行所属模块的完整测试；CI 使用锁定依赖运行服务端 pytest，测试失败必须修复。
- 服务端与客户端各自的测试位置、工具与命令见 `server/AGENTS.md`、`client/AGENTS.md`。

## 开发与验证

客户端命令与开发环境见 `client/AGENTS.md`。

- 根据改动范围验证：前端改动运行前端构建；Rust 改动检查格式并验证相关编译；服务端改动运行 pytest；发布脚本改动运行其测试；工作流改动使用 actionlint 检查。新增 feature 还必须满足上面的测试约束。
- 纯文档改动检查内容与实现一致及 diff 格式，无需重跑全部构建。

以下命令在仓库根目录执行：

```sh
cargo fmt --manifest-path client/src-tauri/Cargo.toml --check
bun test ./.github/scripts
bun .github/scripts/release-smoke.mjs
actionlint .github/workflows/*.yml
uv build --project server --out-dir server/dist
uv run --locked --project server pytest server/tests -v
git diff --check
```

根目录 `.pre-commit-config.yaml` 供 pre-commit.ci 和本地检查共用。
修改其配置时执行 `uvx pre-commit validate-config` 和 `uvx pre-commit run --all-files`。
保持机器人提交信息符合 Conventional Commits。基础 hooks 不依赖本机 Bun / Rust；
构建及发布脚本验证由 GitHub Actions 承担。带注释的 tsconfig JSON 由 TypeScript 验证。

## CI 与发版

CI 调度、Debug 客户端与随包后端、版本同步与 tag 发版、Release 与 CHANGELOG、自动更新的发布配置见 `.github/AGENTS.md`。

## Git 与交付约定

- Follow [CONTRIBUTE.md](CONTRIBUTE.md) for contributor requirements. Write commit messages, PR titles, and PR descriptions in English using Conventional Commits, and fill in `.github/pull_request_template.md`.
- The project uses GNU AGPL version 3 only (`AGPL-3.0-only`), documented in `LICENSE.md`. Preserve the license text and applicable third-party notices.

- 提交信息遵循 Conventional Commits，例如 `feat(client): add current time component`、`ci: add tag-based releases`、`docs: document development workflow`。
- 修改前检查当前分支和工作区，保留用户已有改动，不把临时构建结果、`node_modules/`、`dist/` 或 `target/` 纳入提交。
- 用户要求 commit / push 时执行实际提交与推送；普通代码或文档修改请求不自动扩大为打 tag 或正式发版。
- 每次提交、推送前检查实际分支及远端。
- 交付时说明完成内容、实际执行的验证及未验证部分，区分“工作流已编写”“本机验证通过”和“GitHub 上已成功构建 / 发布”。
- 修改开发命令、版本来源或发布行为时，同步更新 README 与对应的 `AGENTS.md`，尤其不要恢复成手动修改多个版本文件的旧方案。
