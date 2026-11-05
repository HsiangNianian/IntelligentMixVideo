# Remotion 渲染资源协作约定

适用于 `server/src/server/remotion/`，补充根目录与 `server/` 的 `AGENTS.md`。

Remotion 字效服务见 `server/src/server/remotion_templates/AGENTS.md`。

- `patches/` 里的 Bun 补丁针对锁定的 `@remotion/renderer@4.0.534`，把 Chrome 启动连接期限改为 90 秒，CJS 与 ESM 两份同步修改。安装一律用 `bun install --frozen-lockfile`，升级 Remotion 时复核补丁。外层渲染期限与逐帧超时不受影响。
- 沙箱在 `/var/cache/fontconfig` 存在时只读挂载它，复用字体缓存，避免每次 Chrome 启动扫描全部字体；目录缺失时保持 fontconfig 原有的回退。
- worker 的页面由 Remotion 生成为 `<html lang="en">`，沙箱 `LANG` 是 `C.UTF-8`，因此 Chrome 的中日共用汉字回退不带中文语言：装有 `fonts-noto-cjk` 时默认落到 TTC 的日文面。中文回退不能依赖页面语言或 `LANG`，只能由 `remotion_templates/fonts.py` 生成的 `fonts.conf` 里通用家族的 SC 优先生效；`loadManagedFonts` 设置的 `documentElement.lang` 只影响它自己加载的字体，不能当作修复。
- 排版预检与字幕容量用的 `tool-validation-host.tsx` 在启动时用 `loadManagedFonts` 加载 worker 提供的受管字体（`/font-400.otf`、`/font-700.otf`、`/font-fallback.otf`，由 `tool-validation-worker.mjs` 从尝试目录的 `public/` 提供），worker 的页面声明 `lang="zh-CN"`。否则测出的汉字字宽来自系统 TTC 的日文面，与成片不一致；加载不到时宿主仍要挂载并按页面回退测量，不能让预检变成基础设施故障。
- `overlay-worker.mjs` 捕获错误后必须立即以非零状态退出，由 bubblewrap 清理残留进程、宿主记录失败并释放共享渲染名额。不能只设置 `process.exitCode`：`openBrowser` 连接失败前产生的子进程可能让 Node 长期存活。
- worker 的私有日志只记浏览器启动、连接与稀疏的帧进度，不写业务正文。
- 渲染名额：异步受理共用 `IMV_OVERLAY_TASK_CONCURRENCY` 个（默认 6）；视频合成的总并发 `COMPOSITION_CONCURRENCY` 默认也是 6，对外的 `assembling` 阶段包含等待名额、渲染与上传。
