# 桌面端协作约定

适用于 `client/src-tauri/`，补充根目录与 `client/` 的 `AGENTS.md`。

阿里云 SDK 预览见 `client/src/features/templates/AGENTS.md`；更新设计见 `docs/client-update-design.md`；更新签名密钥与 Release 附件检查见 `.github/AGENTS.md`。

## 调试日志窗口

记录的内容与各平台的行为见 `client/README.md`「调试日志窗口」，改动前先读。代码分布在 `src/diagnostics.rs`、`src/diagnostics/webview2.rs`、`client/src/lib/diagnostics.ts` 与模板预览的 `sdk.ts`、`playbackStats.ts`。

- 入口在设置「通用」页，与构建期 `IMV_DEBUG` 无关；浏览器里不显示入口、不转发日志。
- 启动时只在内存里缓冲页面记录。原生收集（Linux 接管标准错误）、环境探测、卡顿统计与 `fetch` 包装都只在打开日志窗口之后进行（`diagnosticsEnabled()`）；启动时不改环境变量、标准错误或浏览器参数。
- 密码、密钥和凭据的值只记“已隐藏”。新增的记录先进内存缓冲，连续相同的记录合并上报。
- 不打开 Chromium 自身的日志：Windows 上 `--enable-logging` 会给 WebView2 进程弹出控制台窗口，而浏览器参数只能在创建 WebView2 时设置。不修改 wry 默认的浏览器参数，也不默认设置 `GST_DEBUG`。
- `diagnostics_open` 列在 `desktop-commands`，Windows 只对绑定的 localhost 页面开放。打不开终端时日志文件照常写入，并在设置页说明原因；关闭日志窗口不退出客户端。

## 预览媒体服务（`src/media_server.rs`）

细节见 `client/README.md`「桌面端预览媒体服务」。

- 启动时绑定 `127.0.0.1` 的系统分配端口，绑定失败阻止启动；地址前缀 `http://127.0.0.1:端口/令牌` 经 `preview_media_base` 交给前端（`features/templates/mediaServer.ts`），令牌每次启动随机生成。
- 只提供 `/sample/1～3`（内置示例）与 `/local/数据文件名`（`preview_videos::open` 只认各槽位元数据当前指向的文件，替换或删除后旧地址失效）；只接受精确的 `127.0.0.1:端口` 主机与 GET/HEAD（另应答 CORS 预检），支持单个字节范围（206/416）。
- 这个服务存在的原因：系统 WebKitGTK（Fedora 45 的 2.54）里，IMS SDK 的 `<video>` 对 blob URL 一直等不到 canplaythrough，画面定格；同一视频走 HTTP 时各平台都正常。浏览器没有该服务，仍用 blob URL。

## Windows localhost 页面服务（`src/localhost.rs`）

- 生产版在 `127.0.0.1` 的系统分配端口提供打包资源，窗口用 `http://localhost:<端口>`，因为 `tauri.localhost` 不满足阿里云 SDK 空 License 的 localhost 预览条件。
- 只允许对应 Host 的 GET/HEAD，不暴露任意磁盘文件；只向本次绑定的精确 localhost URL 开放模板、设置存储与内置后端启动命令，不授权其他端口或域名。开发模式与 macOS / Linux 保持原加载方式。

## macOS

- `Info.plist` 设置 `NSAppTransportSecurity.NSAllowsArbitraryLoadsInWebContent=true`，允许 WebView 访问用户设置的 HTTP 后端；Tauri 会自动合并进应用包，修改后重新打包生效。

## 客户端自动更新

方案、镜像实测与平台验证记录见 `docs/client-update-design.md`。

- 更新核心在 `src/updates.rs`：Rust 负责检查、镜像下载、签名校验、资源切换、回退与调用官方安装器；React 只负责状态展示、草稿/请求门禁与恢复位置。Debug 构建不启用。
- 更新的信任来源只有镜像列表（默认几个 GitHub 代理加 GitHub 直连，编译变量 `IMV_UPDATE_MIRRORS` 可覆盖），业务 API 地址不能改变它。检查时同时请求全部来源、取最高的有效版本（单源超时 12 秒），下载按顺序尝试；清单与资产必须校验签名、大小与摘要。
- 热更新要求原生指纹相同，且 `client/hot-update-validation.json` 登记了该版本/平台的真实组合验证，否则走完整更新。不能用模拟测试代替真实组合验证。
- 应用更新前保护所有已挂载工作区的草稿、在途请求与本地所有服务的未结束/未知状态任务，等待说明列出阻止更新的会话标题与服务地址；字效工作区挂载时由它负责后台同步，未挂载时才由更新等待同步当前服务。
- 多实例用文件锁阻止同时更新；启动时最多等 10 秒共享锁，更新存储不可用只关闭本次更新并提示，不阻止启动。热更新 60 秒内页面未确认就回退；整包安装失败保留已下载的安装包供重试。恢复信息不包含播放进度与撤销历史。
- `update-tools` 是 Rust workspace 成员，供 build.rs 与发版共用；升级官方 updater 时同步检查 Tauri CLI 的版本绑定签名支持。最低 Rust 1.90。
- 修改更新功能时运行客户端完整测试与构建、原生测试，以及 `cargo test --manifest-path client/src-tauri/Cargo.toml -p imv-update-build --locked`（先冻结安装客户端 Bun 依赖）；发布脚本、release-smoke 与 actionlint 同时验证。
