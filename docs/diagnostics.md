# 调试操作链路

桌面设置「通用 → 打开调试日志窗口」沿用应用数据目录的 `data/diagnostics.log`。入口与构建期 `IMV_DEBUG` 无关，浏览器普通运行不收集。打开前，操作、业务和原生命令记录仅进入有界内存缓冲；打开后才包装 HTTP 请求并采集资源性能。关闭终端查看器不会停止客户端或日志写入。

日志来源区分「前端操作 / 前端校验 / 前端模板 / 前端字效 / 前端合成 / 前端设置」「桌面命令 / 桌面更新」「前端请求 / 后端响应 / 后端任务」；第三方字体、媒体等响应标为「网络响应」。每条执行链有 `#编号`，子动作的开始记录带 `来自 #父编号`。查找编号或任务 ID 可以对照交错的请求。

例如检查更新会依次记录：

1. 点击「检查更新」，产生操作编号。
2. 前端开始检查，调用 `client_update/check`，各自保留父编号。
3. 原生并行读取镜像：每个来源的下载或签名校验结果、最高有效版本与选定更新方式。
4. 前端记录发现版本、无需更新或具体失败原因。

一键更新继续记录未保存内容和在途操作的阻塞原因、下载进度、大小/摘要/签名校验、暂存、安装或资源切换、等待新版页面确认和回退。相同等待原因不重复输出，下载进度按 10% 分段记录。安装命令返回不等于新版已启动，原生重启与下一次页面版本确认分开记录。

模板读写、字效生成和参数保存、资产发布、字体、切片与合成的 API 边界记录前置步骤、请求、解析结果和已处理的异常。本机设置与会话等 IPC 记录命令名、操作名、耗时及结果，不记录参数值或返回对象。原生表单校验失败会写明未提交。Enter、Escape、按钮 Space 与保存/撤销等快捷键只记键名，不收集逐字键盘输入。

字效提交记录后端任务 ID、本机登记与后续公开任务状态；SSE 记录连接、收到的事件类型/编号、断开和取消，不保存事件完整载荷。任务按服务地址（来源与部署路径）、类型和 ID 关联；请求或订阅开始时保存服务地址，后续查询只记观察关系，保留首次记录的来源。HTTP 200 只表示已收到响应，SSE 建立只表示连接可用；任务成功、失败、取消、等待补充（`needs_input`）分别以实际任务回执结束本次跟踪。活动任务不因历史容量被淘汰，终态只保留最近 200 条关联。

后端先通过 `X-IMV-Diagnostics: 1` 声明支持；客户端看到该回执后才向当前业务地址发送 `X-IMV-Request-ID`。服务端返回同一编号及 `X-IMV-Elapsed-Ms`，详细服务日志可按编号查找接收、响应、发送结束和异常类型。首个能力探测请求及旧后端只有客户端请求记录；不为补日志重发请求，也不向第三方主动增加该头。远程后端的内部堆栈仍在服务日志中，不会下载到客户端。

日志不保存完整请求/响应、认证头、设置值、模型窗口或图片。任务回复和错误详情即使可在聊天中展示，也不写入任务日志；只记有长度限制的机器 ID、状态、阶段和错误码，拒绝换行及控制字符。状态、阶段或错误码变化才记录，终态后的安全字段变化另记补充，不重新结束任务。URL 去掉账号、查询串和片段，本机媒体令牌隐藏；HTTP 错误 JSON 只提取公开 `detail/message` 或校验 `msg`，不记录 `input/ctx`。错误正文读取有字节和时间上限，诊断不消费业务响应，也不缓冲 SSE。静态内存统计仅在变化时输出。

## 扩展和验证

`client/src/lib/diagnostics.ts` 的 `diagnosticTask` 包住真实业务边界，回调接收 `trace`；跨越 `await` 后用 `trace.run(() => 请求或命令())` 继续同一因果链，用 `trace.step()` 记录实际阶段。同步事件只在派发期间提供编号；未显式传播的后台动作标为独立触发，禁止把它归给最近的用户点击。任务状态使用 `diagnosticTaskState(kind, id, status, { service, stage, code })`，`service` 必须取自该次请求或订阅；不传自由文本。设置与普通会话 IPC 通过 `updateInvoke` 进入诊断并保留更新门禁；只读回执核查使用 `diagnosticInvoke`，允许在更新锁内检查。新增调用方须在实际入口使用这些工具，不添加没有调用方的埋点。

验证命令：

```sh
cd client
bun run test
bun run build
bun tests/action-diagnostics.browser.mjs
cd ..
cargo test --locked --manifest-path client/src-tauri/Cargo.toml --lib
cargo test --locked --manifest-path client/src-tauri/Cargo.toml -p imv-update-build
uv run --locked --project server pytest server/tests/test_diagnostics.py server/tests/test_api.py
```

浏览器测试默认使用本机已安装的 Google Chrome；`playwright-core` 不附带浏览器。其他 Chromium 安装可通过 `IMV_CHROME_PATH=/完整路径/chromium bun tests/action-diagnostics.browser.mjs` 指定。测试使用真实点击和表单校验，业务 HTTP 与桌面 IPC 为隔离替身；不执行真实更新安装，也不代表 Windows/Linux 安装包验证。
