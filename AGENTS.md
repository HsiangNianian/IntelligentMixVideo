# 项目协作指南

本文适用于整个仓库，记录项目已确定的结构、开发和发布约定。需求发生变化时，同步更新相关实现、README 和本文件。

## 项目结构与当前范围

- 这是一个 monorepo，目录名使用小写的 `client/` 和 `server/`。
- `client/` 是 Rust + Tauri 2 + React + TypeScript 桌面客户端，前端使用 Vite，包管理器与脚本运行时使用 Bun。
- `client/src/` 存放 React 前端；`client/src-tauri/` 存放 Rust 桌面入口、Tauri 配置和图标。
- `server/` 预留给 Python 业务 API。目前仅保留已有的 Python 包骨架，在收到后端需求前不添加业务 API 或服务框架。
- 当前客户端保持最小可运行结构。首页 `client/src/App.tsx` 引用独立的 `client/src/components/CurrentTime.tsx` 组件，按本机时区显示日期和时间，每秒刷新。
- 时间组件需在卸载时清理定时器。新增界面功能时遵循组件化结构，不把所有逻辑堆到 App 首页。
- 项目长期方向见 README；其中提到的云剪辑、Agent、素材召回等功能不代表已经实现，也不构成自动扩展当前任务范围的要求。

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
- 根据改动范围验证：前端改动运行前端构建；Rust 改动检查格式并验证相关编译；发布脚本改动运行其测试；工作流改动使用 actionlint 检查。
- 纯文档改动检查内容与实现一致及 diff 格式，无需重跑全部构建。

以下命令在仓库根目录执行：

```sh
cargo fmt --manifest-path client/src-tauri/Cargo.toml --check
bun test ./.github/scripts
bun .github/scripts/release-smoke.mjs
actionlint .github/workflows/client-build.yml .github/workflows/release.yml .github/workflows/validation.yml
uv build --project server --out-dir server/dist
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
该工作流同时构建 Python 包骨架；`server/pyproject.toml` 的 uv 构建模块名显式设为 `server`，对应 `src/server/`。

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
