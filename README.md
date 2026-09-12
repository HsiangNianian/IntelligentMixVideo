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
- `server/`：Python + FastAPI 服务端，提供首页和用户路由示例。

服务端运行
----------

安装 Python 3.12+ 和 uv，然后执行：

```sh
cd server
uv run server
```

默认监听 http://127.0.0.1:8000，API 文档位于 http://127.0.0.1:8000/docs。
仓库根目录使用 `uv run --project server server`。路由仍返回示例数据，详情见 [server/README.md](server/README.md)。

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

首页显示本机当前日期和时间，每秒更新，组件位于 `client/src/components/CurrentTime.tsx`。
客户端按页面、业务组件、基础 UI 和共享工具分层；结构见 [client/README.md](client/README.md)，
最小改动与源码注释要求见 [AGENTS.md](AGENTS.md)。

```sh
# 编译前端（包含 TypeScript 检查）
bun run build
# 编译桌面程序和安装包
bun run tauri build
```

本地安装包输出到 `client/src-tauri/target/release/bundle/`。

跨平台 CI
---------

`.github/workflows/client-build.yml` 参考 [DropOut 的平台矩阵、缓存及产物上传配置](https://github.com/HydroRoll-Team/DropOut/blob/main/.github/workflows/test.yml)。
修改客户端或该工作流的 push / PR 会自动构建，也可在 Actions 页面手动触发。

| 平台 | 架构 | 安装包 |
| --- | --- | --- |
| Windows | x64 | NSIS exe、MSI |
| Linux | x64 | deb、AppImage |
| macOS | Apple Silicon、Intel | dmg |

从 Actions 对应运行的 Artifacts 下载产物，保留 14 天。CI 使用依赖锁文件，不需要额外配置发布密钥。
当前安装包未配置代码签名或 macOS 公证；正式分发时需另行配置。

`.github/workflows/validation.yml` 在相关 push / PR 中检查全部工作流、运行发布脚本及失败恢复测试，
并在临时副本中验证版本注入、Bun 冻结安装和 Cargo 锁文件。它也通过 `uv build --project server`
验证 Python 包可构建，并运行 API 路由测试。

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
流程会先校验 tag 格式，再复用客户端 CI 构建 Windows x64、Linux x64、
macOS ARM64 / Intel 安装包；所有构建成功后才创建 GitHub Release。
安装包和 `CHANGELOG.md` 都会作为 Release 附件上传，Release 正文使用自动生成的变更记录。

**正式版本由 tag 唯一决定，无需手动修改版本文件。** 各平台 CI 会将 `v0.2.0`
解析为 `0.2.0`，自动写入构建工作区中的 `package.json`、`tauri.conf.json`、
`Cargo.toml` 及 `Cargo.lock` 的客户端包版本，保留已锁定的依赖版本，然后校验并构建。
`bun.lock` 不记录根项目版本，发布时保持不变，并通过冻结锁文件安装验证。
这些版本修改不提交回仓库；本地开发继续使用源码中的开发版本，只有 CHANGELOG 会回写。

例如发布 `0.2.0`（先提交源码，并确保发布工作流已包含在该提交中）：

```sh
git tag -a v0.2.0 -m "Release v0.2.0"
git push origin v0.2.0
```

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
