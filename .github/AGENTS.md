# CI 与发版协作约定

适用于 `.github/` 下的工作流与脚本，以及版本同步、tag 发版和 Release 流程，补充根目录 `AGENTS.md`。

## 客户端构建 CI

`.github/workflows/validation.yml` 是常规 push / PR 验证的统一 Actions 入口，不在工作流级使用 paths 过滤，以确保每个 PR 都有最终检查结果。
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

| 平台    | 架构                 | 安装包        |
| ------- | -------------------- | ------------- |
| Windows | x64                  | NSIS exe、MSI |
| Linux   | x64                  | deb、AppImage |
| macOS   | Apple Silicon、Intel | dmg           |

普通构建的 Actions artifacts 保留 14 天。发版应复用这一构建工作流，避免维护两套不一致的平台构建逻辑。
构建产物来自被触发的提交；正式发布时必须来自对应 tag 的源码。

涉及 CI、原生代码或两端版本清单/锁文件时，Release preflight 检查全部工作流、运行调度与发布脚本测试，
校验两端源码版本一致，并通过临时副本验证版本同步、Bun 冻结安装、Cargo 和 uv 锁文件。
Release preflight 在离线锁文件检查前用 `uv python install "$UV_PYTHON"` 安装所选解释器；`setup-uv` 的 `python-version` 只设置版本选择，不保证解释器已安装。
服务端 job 使用 Python 3.12 和 uv 缓存，并在 pytest 前配置 Node 24、仓库指定的 Bun 和服务端 Remotion 冻结依赖，保证普通合成测试的 esbuild 打包与导入边界检查可运行；不因此启用真实浏览器或媒体集成测试。`server/pyproject.toml` 的 uv 构建模块名显式设为 `server`，对应 `src/server/`。
在 Validate project 上手动运行会执行全部检查（原生检查模式）；需要安装包时手动运行 Build client。

- Validate project 的 `workflow_dispatch` 支持 `integration-tests`（默认 true）、`build-installers`（默认 false）与 `api-url`。手动集成通过 `backend-integration.yml` 复用工作流运行；普通 push/PR 不开启重型测试，最终汇总必须检查其结果。勾选打包仍使用 release 优化，API 地址仅在构建时注入。
- 集成环境使用 Python 3.12、uv、临时 MySQL 8.4、Node 24、Bun、服务端 Remotion 锁定依赖、Chrome、FFmpeg/ffprobe、Noto CJK、bubblewrap 和 prlimit。真实 MySQL 测试须显式设置 `IMV_TEST_MYSQL=1` 和回环数据库连接，每例随机建库并清理；既有 SQLite 夹具不变。真实渲染通过 `IMV_TEST_RENDERER=1` 启用，只捕获浏览器及字体路径，不读取真实密钥。
- `.github/workflows/client-debug.yml`（Debug client）在 `main` / `dev` push 或 `workflow_dispatch` 时运行，不监听 PR；复用 `client-build.yml` 并传入 `build-mode: package`、`debug-backend: true`。共享构建的 `debug-backend` 为布尔输入，默认 false，仅由此输入设置 `IMV_DEBUG`，不绑定分支名；普通构建和正式发布默认不携带后端。Debug artifact 名称为 `intelligent-mix-video-debug-<平台>-<提交 SHA>`。手动入口要求工作流存在于默认分支，分支 push 触发不绕过此限制。各平台打包时由 `.github/scripts/bundle-backend.py` 生成 `backend.tar`、`backend.id` 和临时 Tauri 资源配置，均不入库。随包完整服务、Python 3.12、MySQL（Linux 8.0，其余 8.4.8）、Node 24、Bun/Remotion、Chrome、FFmpeg/ffprobe及字体，Linux 另带 bubblewrap/prlimit 隔离工具。普通非 debug 构建不启动内置服务。客户端 `start_backend` 等实际就绪回执再挂载页面，模板、字效与设置 API 模块共享动态回环地址，不改变业务接口。
- `server.desktop` 监督私有 MySQL 与完整 FastAPI；`DB_SOCKET` 仅用于本地 Unix socket，去库建库连接也必须保留 socket。数据和无密钥初始 `.env` 保存 app_data_dir/backend，运行时按归档摘要缓存于 app_cache_dir/backend；保留用户配置和数据库。父进程管道关闭时先停 API 再停 MySQL。Unix 内置数据库不开放 TCP；Windows 使用随机密码和动态回环 TCP，初始化时不监听网络，通过 SQL SHUTDOWN 落盘退出。均不读取宿主配置；密钥不得打包。随包库只提供给后端和渲染沙箱，禁止暴露用户目录、凭据或网络。AppImage/MSI/DMG 上传前启用 `IMV_TEST_BUNDLE` 验证真实启动、重启持久化、重复实例、关闭与桌面连接；FFmpeg/ffprobe 在 CI 从官方 `FFmpeg/FFmpeg` 最新稳定 tag 固定提交下载源码并原生编译，归档包含版本、提交与许可证；不使用 nightly 或第三方 Release 二进制。真实 Remotion 渲染目前仅 Linux 支持，Windows/macOS 不得绕过沙箱执行生成代码。macOS 使用 macos-15 / macos-15-intel 原生构建依赖，dylib 转为相对加载路径；Windows 使用 app-local VC 运行库，打包无特权符号链接依赖；常规 pytest 跳过这些真实集成用例。
- Linux AppImage 排除随包 Wayland/PulseAudio 库，上传前检查 WebKit、GStreamer OpenGL/播放/解码插件。linuxdeploy GTK 钩子把 `GTK_IM_MODULE_FILE` 固定为随包清单，因此构建机安装 `fcitx5-frontend-gtk3`、`ibus-gtk3`，把与随包 GTK/GLib 同版本的输入法模块打进包；不要改为加载宿主模块（GTK 以延迟绑定加载模块，宿主模块依赖更新的 GLib 时会在输入时崩溃）。上传前检查 `im-fcitx5.so`、`im-ibus.so`、`libFcitx5GClient.so.2`、`libibus-1.0.so.5` 与清单中的 fcitx、fcitx5、ibus 登记。Linux 在 GTK 初始化前默认设置 `WEBKIT_GST_DMABUF_SINK_DISABLED=1` 和 `WEBKIT_GST_USE_PLAYBIN3=1`，保留显式环境配置；网页缓存按 WebKit 版本隔离，其他应用数据目录不变。`IMV_GDK_BACKEND=wayland|x11` 保留为显示后端对比入口，不将打包检查描述为真实播放与性能验证。

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
- 使用 `ncipollo/release-action` 上传到草稿，随后通过 `release-assets.mjs` 校验安装包、签名更新清单、界面资源、macOS 更新归档及 `CHANGELOG.md` 的文件名、大小和上传状态，全部通过后才公开。上传失败必须保持草稿状态。
- 重跑可修复草稿或补齐日志回写；已公开 Release 的附件与正文不再修改。
- 草稿校验使用上传 action 返回的 Release ID 查询，并确认其 tag 匹配；不要通过按 tag 查询接口查找未公开草稿。
- 发布并发组按 tag 隔离，避免不同版本互相替换等待中的运行。
- 发布后通过 `commit-changelog.mjs` 在单独的默认分支 checkout 中仅合并当前版本日志，按版本号降序排列。每次获取最新分支，并发冲突最多尝试五次，不强制推送、不重复插入已有条目，不能假定默认分支永远是 main。
- 自动提交格式为 `docs: update CHANGELOG.md for vX.Y.Z [skip ci]`。
- 不以手工维护正式版本条目的方式替代自动 changelog 流程。
- 使用内置 `GITHUB_TOKEN`；构建 job 使用读权限，发布 job 需要 `contents: write`。默认分支规则必须允许对应的机器人写入。
- 当前未配置 Windows 代码签名和 macOS 公证，不将生成的安装包描述为已签名或已公证。

## 自动更新的发布配置

- 正式 tag 需要 Actions variable `IMV_UPDATE_PUBLIC_KEY`、secret `TAURI_SIGNING_PRIVATE_KEY` 和可选口令 secret `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`。私钥不入库；未配置密钥不能发正式更新。更新签名不代表 Windows 代码签名或 macOS 公证。
- Release 完整性门禁检查 17 个必需附件：原六个安装包、CHANGELOG、四个签名清单、四个资源包及两个 macOS 更新归档。发布工具测试使用临时密钥和不可执行的测试文件，不安装、不使用真实密钥。

## 用户指定的参考项目

- 客户端与跨平台构建参考：[HydroRoll-Team/DropOut](https://github.com/HydroRoll-Team/DropOut)。
- 自动 changelog 参考：[HydroRoll changelog.yml](https://github.com/HydroRoll-Team/HydroRoll/blob/main/.github/workflows/changelog.yml)。
- 发布流程参考：[HydroRoll release.yml](https://github.com/HydroRoll-Team/HydroRoll/blob/main/.github/workflows/release.yml)。

参考项目用于理解实现方式；本项目发布的是 Tauri 客户端安装包，不照搬参考项目的 Python wheel / PyPI 发布目标。
