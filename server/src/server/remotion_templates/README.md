# Remotion Agent

本模块提供 `/api/templates` 会话 API，产出可复用的 Remotion 代码与隔离 Player 预览。
本次生成链路采用 **Outer ReAct → Plan ReAct → Executor ReAct → Plan → Outer**。
工具遵循 [PR76 契约](../../../../docs/remotion-agent-tool-contracts.md)；主画布固定
1080×1920、30 FPS，时长由实例最晚结束帧决定。新版本支持一般 React/Remotion 内容，
不再要求生成旧 `text_layers` 字效方案，也不调用视觉 Judge。

## 文件与执行顺序

- `routes.py`：创建作品、提交消息、参数保存、取消、重试和读取预览。
- `runtime.py`：单进程 FIFO 队列、超时、凭据生命周期、公开事件及原子发布。
- `agent.py`、`planning.py`：三个独立上下文及层间交接；Outer 决定任务与最终交付，
  Plan 创建/修订顺序计划，Executor 只执行当前步骤。
- `provider.py`、`completion_stream.py`：共同模型的 SSE 接收、工具参数拼装、用量记账。
- `tools/registry.py`、`catalog.py`：从装饰器和 Pydantic 类型生成完整入出参契约并执行工具。
- `tools/session.py`：任务内资产引用、验证回执及工具调用；共享资产与会话独立。
- `tool_validation.py`、`../remotion/tool-validation-*.{mjs,tsx}`：隔离 TypeScript 和浏览器行为测试。
- `harness.py`、`publication.py`：完成检查、参数修订、导出与 Player 构建，无隐藏模型循环。
- `store.py`、`evidence.py`：SQLite 事务、产物指纹和取消优先的版本发布。

三层模型都使用同一套模型配置；`outer / plan / executor` 是审计与额度分类。
`IMV_ACTOR_*` 保留为已有配置键，不代表仍有 Actor 模型角色。
`preset.search` 是普通工具，通过 ChromaDB 语义检索，不产生额外模型角色。

复杂任务由 Outer 委派到 Plan。Plan 唤醒 Executor 后，步骤完成、受阻或工具额度用尽
会交还 Plan；Plan 汇总结果后返回 Outer。最终只有 Outer 可以请求交付。
简单问答不需要计划；简单工具操作也可直接执行。普通文字承诺不能替代已开始的生成工作。

每层保存有界完整消息组，工具调用及回执不会拆开。外层窗口按作品持久化，Plan/Executor
窗口仅存在于当前执行中；重试创建新的任务，不复活中断到一半的工具调用。
私有 `jobs/<id>/audit.jsonl` 保存角色、工具回执、用量和结束原因；公开 SSE 只含允许的阶段与结果。

## 工具与完成条件

| 模块 | 工具 |
| --- | --- |
| 图片 | `image.info`、`image.resize`、`image.crop`（不含超分） |
| Preset | `preset.search`、`preset.create`、`preset.modify` |
| Sprite | `sprite.compose`、`sprite.create` |
| 校验 | `validate.code`、`validate.render` |
| 描述 | `tools.inspect` |

计划控制是宿主工具，不属于上述 11 个业务工具。Executor 不获得计划控制权限。
没有 `tools.find`、`jev.search`、`sprite.combine` 或旧校验别名。

调用顺序：观察图片 → 检索/编写 Preset → 创建或修改副本 → 明确实例布局和时间 → compose →
validate.render → 根据实际诊断修复 → sprite.create → Outer 请求完成。

Preset 修改返回副本，不覆盖原记录。组合保留独立实例参数、局部帧与局部画布；同一 Preset
可以多次使用。参数以默认值为基底递归合并对象，数组、标量和 null 整体替换。
创建工具检查代码和数据一致性；工具保存不自动创建聊天成功版本。

`validate.render` 实际运行默认参数和配置参数，并执行提交的 TypeScript 断言。
没有断言的脚本报错；即使脚本捕获断言异常，失败记录仍保留。每份脚本从第 0 帧和输入参数
重新开始。最后可访问帧为 `duration_frames - 1`，不静默截断越界帧。
工具不截图、不抽帧、不做视觉评审；没有提供的测试不声称已经覆盖。

最终完成必须引用本任务保存的 Sprite，且存在与代码、Schema、默认参数、时长完全对应的
最新通过回执。失败的再次检查会覆盖此前通过状态。宿主随后隔离构建 `Export.tsx` 和
`interactive.js`，保存 SHA-256 清单，再在 SQLite 事务内检查任务仍在运行后发布。
新版交付代码与交互预览，不生成用于评审的 PNG 或 MP4。

## 客户端与旧版本

新会话只显示固定主画布；布局、内容、动画与时长通过聊天提出。
PR76 成功版本使用 `spec.schema_version=2`，保留完整 Sprite 定义；参数面板按实例编辑嵌套 JSON。
保存只提交净变化，保留代码，执行参数合法性与基础运行检查，不调用三层模型或旧视觉评审。
撤销恢复最近成功参数；历史预览只读。后续自然语言任务同时看到当前参数与相对 Agent 基线的用户净变化。

旧版本继续读取，历史画布不改写；其手动参数修订保留必要的旧渲染检查。
PR74 `/api/sprites` 的云端发布、绑定和透明视频接口不自动接收新的组合 Sprite，
不把新工具记录迁移为旧 Protobuf 协议。新版结果卡片因此不显示旧“发布 Sprite”入口。

## 配置与启动

配置读取固定的 `server/.env`，进程环境优先；不要覆盖已有真实配置。

```dotenv
IMV_ACTOR_BASE_URL=https://your-provider.example/v1
IMV_ACTOR_MODEL=your-image-capable-model
IMV_ACTOR_API_KEY=
IMV_DISABLE_THINKING=false
IMV_TOOL_ASSET_BASE_URL=http://127.0.0.1:20070/api/templates/tool-assets
```

参考图片直接提供给三层模型，因此共同模型应支持图片。工具图片 URL 应设置为工具运行环境
可访问的 API 地址；处理结果由 `/api/templates/tool-assets/<id>.png` 读取，原图保留。
配置键不保存进作品、聊天、工具回执或版本。客户端 `X-Remotion-Config` 只覆盖本次任务。

模型请求使用 SSE，完整收到终止标记后才执行工具；断流、截断、取消不执行部分结果。
`IMV_DISABLE_THINKING=false` 省略供应商扩展字段；不因模型名称猜测供应商协议。

| 配置 | 默认 | 行为 |
| --- | --- | --- |
| `IMV_ENFORCE_MODEL_BUDGET` | false | 累计模型额度开关；有效用量始终记账 |
| `IMV_MAX_OUTPUT_TOKENS` | 32000 | 单次输出上限，始终有效 |
| `IMV_MAX_MODEL_CALLS / IMV_MAX_TOKENS` | 32 / 200000 | 全任务模型额度 |
| `IMV_MAX_OUTER_CALLS / TOKENS` | 12 / 80000 | Outer 分类额度 |
| `IMV_MAX_PLAN_CALLS / TOKENS` | 12 / 80000 | Plan 分类额度 |
| `IMV_MAX_EXECUTOR_CALLS / TOKENS` | 24 / 160000 | Executor 分类额度 |
| `IMV_MAX_STEPS / IMV_MAX_TOOLUSE` | 8 / 10 | 执行批次、每批工具上限，始终生效 |
| `IMV_MAX_NO_PROGRESS_TURNS` | 4 | 连续无有效新观察保护 |
| `IMV_MODEL_TIMEOUT_SECONDS` | 240 | HTTP 读写空闲超时 |
| `IMV_JOB_TIMEOUT_SECONDS` | 600 | 全任务执行超时，不含排队 |
| `IMV_RENDER_TIMEOUT_SECONDS` | 180 | 隔离进程超时 |

共享服务启动需要模板库 MySQL 配置；生成会话与历史仍保存在模块 `.data/templates.sqlite3`。
Preset 检索使用本地 ChromaDB，首次使用其默认 embedding 模型可能下载模型文件；
部署时提前准备模型缓存。ChromaDB/embedding 不可用会明确失败，不退化为字符串匹配或调用 Agent 评分。

```sh
cd server
uv sync --locked
uv run --locked uvicorn server.app:app --host 0.0.0.0 --port 20070
```

## Linux 验证交接

真实执行仍使用 Linux bubblewrap/prlimit，禁止生成代码在 FastAPI 宿主进程运行。
本项目不再维护本次新增的 macOS Colima/Docker 渲染适配。普通离线用例可在开发机运行；
真实 Chromium 行为验证由 Linux 环境执行。

Linux 需 Node 24、Bun 1.4.2、bubblewrap、util-linux、Chrome/Chromium 和项目锁定依赖；
历史视频渲染另需 FFmpeg/ffprobe。字体和浏览器路径通过 `IMV_FONT_REGULAR`、
`IMV_FONT_BOLD`、`IMV_BROWSER_EXECUTABLE` 指定，bubblewrap 用户命名空间必须可用。

```sh
cd server/src/server/remotion
bun install --frozen-lockfile
cd ../../..
uv run --locked pytest tests/test_remotion_agent.py tests/test_remotion_tools_pr76.py tests/test_remotion_publication.py tests/test_remotion_tool_validation.py -q
# 配好 Linux 浏览器/字体路径后：
IMV_TEST_RENDERER=1 uv run --locked pytest tests/test_remotion_tool_validation.py -v
# 最小工具链，不调用模型、不启动浏览器；真实执行 TypeScript 与 Chroma/文件保存：
uv run --locked python -m server.remotion_templates.tools_smoke --run
# 显式付费模型联调（只在主动测试时执行）：
uv run --locked python -m server.remotion_templates.smoke --live
```

至少核对：标题渐显与说明文字组合、同预设多实例、修改副本不覆盖来源、局部帧/尺寸、
部分参数覆盖、失败断言修复、测试间状态重置、无断言/越界/超时错误、保存与预览、参数保存、取消与重试。
编译/离线测试通过不等于真实 Linux 浏览器验收通过。

## HTTP 与数据生命周期

- `POST /assets` 上传参考图；`POST /works` 创建异步任务（202）。
- `POST /works/{id}/messages` 提问、自然语言修改、参数补丁或回答澄清。
- `GET /works/{id}/session` 返回一致快照与游标；`GET /works/{id}/stream` 订阅 SSE。
- `GET /versions/{id}`、`/preview`、`/artifacts/Export.tsx` 读取成功结果。
- `POST /jobs/{id}/cancel`、`/retry` 取消或重试；取消先持久化，迟到结果不能发布。
- `DELETE /works/{id}` 清理会话专属数据，保持共享 Preset/Sprite 库。

公开消息、任务终态、版本事件在同一事务保存。SSE 支持 Last-Event-ID、去重、心跳和断线重放；
反向代理需关闭事件缓冲。服务重启将未完成任务标记 interrupted，失败或取消不会替换已有结果。
FastAPI `uvicorn.error` 输出具体异常堆栈；任务 `audit.jsonl` 与各工具目录 `worker.log` 用于内部排障。
