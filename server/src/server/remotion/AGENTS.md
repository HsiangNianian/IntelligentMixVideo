# Remotion 渲染资源协作约定

适用于 `server/src/server/remotion/`，补充根目录与 `server/` 的 `AGENTS.md`。

Remotion 字效服务见 `server/src/server/remotion_templates/AGENTS.md`。

- Remotion Linux 沙箱在 `/var/cache/fontconfig` 存在时只读挂载，复用字体缓存以避免每次 Chrome 启动扫描全部字体；缺失时保持 fontconfig 原有回退。Chrome 启动连接期限为 90 秒，通过 `server/src/server/remotion/patches/` 中针对锁定 `@remotion/renderer@4.0.534` 的 Bun 补丁同步修改 CJS/ESM；安装使用 `bun install --frozen-lockfile`，升级依赖时复核补丁。外层渲染期限和逐帧超时不变。
- Remotion 叠加 worker 捕获错误后立即以非零状态退出，由 bubblewrap 清理残留进程、宿主保存失败并释放共享渲染名额；不能只设置 `process.exitCode`，否则 `openBrowser` 连接失败前产生的子进程可能使 Node 长期存活。私有 worker 日志记录浏览器启动/连接与稀疏帧进度，不包含业务正文；异步受理共用 `IMV_OVERLAY_TASK_CONCURRENCY` 个实际渲染名额（默认 6），`video_composition` 总任务并发 `COMPOSITION_CONCURRENCY` 默认也为 6，对外的 `assembling` 包含等待名额、渲染及上传。
