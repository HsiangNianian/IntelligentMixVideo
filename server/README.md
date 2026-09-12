# IntelligentMixVideo API

需要 Python 3.12+ 和 uv。在本目录执行即可安装锁定依赖并启动 API：

```sh
uv run server
```

默认地址为 http://127.0.0.1:8000，交互文档为 http://127.0.0.1:8000/docs。
按 Ctrl+C 停止服务。仓库根目录可执行 `uv run --project server server`。
也支持 `uv run python -m server`。

现有接口为示例数据，尚未接入用户存储：

- `GET /`：首页消息。
- `GET /users/`：用户列表示例。
- `GET /users/{user_id}`：返回整数 ID，非整数返回 422。

需要修改监听地址、端口或启用开发热重载时：

```sh
uv run uvicorn server.app:app --host 127.0.0.1 --port 8001 --reload --reload-dir src
```

验证命令（在本目录执行）：

```sh
uv run --locked pytest -v
uv build --out-dir dist
```

维护 `uv.lock`，CI 使用 `--locked` 检查依赖与配置一致。

测试统一放在 `tests/`，使用 pytest；`conftest.py` 管理客户端夹具，
`test_api.py` 覆盖路由契约、边界和错误请求，`test_entrypoint.py` 覆盖两种启动入口。
每个新 feature 都必须补齐正常、异常及适用边界的测试脚本，详细规则见根目录 AGENTS.md。

项目在 `pyproject.toml` 中将官方 PyPI 设为默认索引，与锁文件来源保持一致。
第三方镜像可能尚未同步所需版本，导致 `uv sync` 和 `uv sync --locked` 报
“No solution found”。本机如另有索引覆盖配置，可用以下命令验证：

```sh
uv sync --locked --default-index https://pypi.org/simple
```

先检查实际使用的索引及镜像同步情况，不要仅为绕过镜像缺失而降低依赖版本或删除锁文件。

## 文案切片

切片业务集中在 `segmentation/segmentation.py` 的单个 `segment` 函数，
可通过 `from server.segmentation import segment` 导入，调用 `segment(payload)` 返回结果字典。
业务不依赖 FastAPI，失败抛出异常；`sub_api/segmentation.py` 负责 `POST /segmentations` 路由和 HTTP 错误转换。
输入正确文案与已有 ASR 词级时间轴（也支持 `transcripts[0]` 外层和 `begin_time/end_time` 字段）：

```json
{"script":"你好世界。","asr_result":{"sentences":[{"words":[{"text":"你好世界","begin_time_ms":0,"end_time_ms":2000}]}]}}
```

返回 `segments`（文本、起止毫秒、关键词）、`warnings` 和 `trace`。
每段的 `keywords` 格式为 `[{"text": "关键词"}]`，不包含偏移字段。

### 处理约束与配置

处理流程为：校验文案与 ASR → 字符对齐并投射时间 → LLM 选择语义切点 → 代码调整时长 → LLM 标注最终片段关键词 → 校验输出。
两次模型调用有先后依赖；最终切点可能因保护词串、合并短段或拆分长段而调整，并非完全由 LLM 决定。
字符级波前对齐的替换、插入和删除代价均为 1，忽略所列标点与空白，并逐字符做 NFKC 和小写归一化。
匹配率低于 50% 拒绝、低于 90% 告警；这只能检查文本差异，无法判断差异来自 TTS 还是 ASR。
词内时间均分，增删在局部修复块中插值；时间来自已有 ASR，不读取音频，也不保证真实字级发音边界。
MVP 只提供中文标点分句供模型选择；无此类标点的长文主要由时长规则拆分。
提示词参考口播语法、条件/动作/对象/结果信息点及6～8字节奏，不强制10字上限；关键词引导全篇优先选3～4个核心词，其他片段留空，不足不凑数。这些是模型偏好，最终仍受候选切点和时长调整影响，全篇关键词数量不由代码强制。
关键词由模型选择，代码只做逐字匹配、去重、长度/数量过滤和原文顺序排列；区分大小写、全半角，保留有效的长短包含词。
文本完整覆盖、时间不重叠、关键词精确回溯是硬约束；时长无法满足时告警。
模型失败或返回非法切点直接报告，不调用 TTS/ASR，也不提供备用算法。
输入文案、ASR 原始字符、词数及句数分别限制为 20000；词时间必须有限、非负、递增且不重叠。
业务错误返回 `{"error":{"message":"错误说明"}}`：输入错误 422、内部约束错误 500、模型错误 502、超时 504。
缺少请求体、非对象 JSON 或 JSON 语法错误由 FastAPI 返回 422 和 `detail` 数组。

复制 `.env.example` 到 `.env` 并填写模型配置；从 `server/` 启动以读取该文件。
在仓库根目录使用 `uv run --project server --env-file server/.env server` 显式加载；`--project` 不切换当前目录。
进程环境变量优先于当前工作目录 `.env`，代码不修改全局环境、不缓存配置。
模型地址、Key、模型名必填；默认超时 120 秒、SDK 重试一次；远程 HTTP 默认禁止。
默认片段时长 1200～6000 ms，关键词最多 5 个、每个最多 12 字，工作预算 250000。
其余变量见样例。SDK 客户端在成功或异常返回前关闭。

切片回归测试：`uv run --locked pytest tests/test_segmentation.py -v`。
共享夹具自动隔离外部 `IMV_` 环境变量与 `.env`；测试使用小型合成 ASR 和 SDK 替身，不访问真实模型。
覆盖对齐代价与时间、模型切点及异常、英文/数字保护、关键词过滤、时长告警、资源关闭和 HTTP 响应契约。
离线测试通过不代表真实 TTS/ASR/LLM 联调通过。
