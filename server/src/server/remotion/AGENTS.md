# Remotion 渲染资源协作约定

适用于 `server/src/server/remotion/`，补充根目录与 `server/` 的 `AGENTS.md`。

Remotion 字效服务见 `server/src/server/remotion_templates/AGENTS.md`。

- `patches/` 里的 Bun 补丁针对锁定的 `@remotion/renderer@4.0.534`，把 Chrome 启动连接期限改为 90 秒，CJS 与 ESM 两份同步修改。安装一律用 `bun install --frozen-lockfile`，升级 Remotion 时复核补丁。外层渲染期限与逐帧超时不受影响。
- 沙箱在 `/var/cache/fontconfig` 存在时只读挂载它，复用字体缓存，避免每次 Chrome 启动扫描全部字体；目录缺失时保持 fontconfig 原有的回退。
- `overlay-worker.mjs` 捕获错误后必须立即以非零状态退出，由 bubblewrap 清理残留进程、宿主记录失败并释放共享渲染名额。不能只设置 `process.exitCode`：`openBrowser` 连接失败前产生的子进程可能让 Node 长期存活。
- worker 的私有日志只记浏览器启动、连接与稀疏的帧进度，不写业务正文。
- 渲染名额：异步受理共用 `IMV_OVERLAY_TASK_CONCURRENCY` 个（默认 6）；视频合成的总并发 `COMPOSITION_CONCURRENCY` 默认也是 6，对外的 `assembling` 阶段包含等待名额、渲染与上传。
- `imv-runtime.tsx` 是自由组件字效导入的内容运行时：`compose.mjs` 打包时把它内联进 Sprite（发布代码仍只导入 React 与 Remotion），`typescript.mjs` 按路径映射给类型检查，`tool-validation-worker.mjs` 的源码策略放行 `imv`。它的分行、关键词命中、行出现与测量标记必须与 `compose.mjs` 的文字块和合成侧的注入保持一致。
