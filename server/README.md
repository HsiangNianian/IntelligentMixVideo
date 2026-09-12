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
输入正确文案与单音轨 fun-asr 原始转写结果；必须包含恰好一个 `transcripts` 元素，词时间使用 `begin_time/end_time`（毫秒）。不再接受顶层 `sentences` 或仅有 `begin_time_ms/end_time_ms` 的旧输入：

```json
{"script":"你好世界。","asr_result":{"transcripts":[{"channel_id":0,"sentences":[{"words":[{"text":"你好世界","begin_time":0,"end_time":2000}]}]}]}}
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
提示词要求逐一判断并列全独立信息点的切点，以6～8字为节奏参考；仅语法不完整、依赖相邻句且合并后不超过10字时建议合并。已有超长分句只能保留边界，不保证最终10字上限。关键词先全篇筛选3～4个，总数不得超过4、每段最多1个（配置为0时不选），不足不凑数，再逐项核对所属片段、保留空数组位置。以上仍是提示词要求，时长后处理可能调整切点，代码不强制全篇4个或每段1个关键词，仍按现有配置上限校验。
关键词由模型选择，代码只做逐字匹配、去重、长度/数量过滤和原文顺序排列；区分大小写、全半角，保留有效的长短包含词。
文本完整覆盖、时间不重叠、关键词精确回溯是硬约束；时长无法满足时告警。
模型失败或返回非法切点直接报告，不调用 TTS/ASR，也不提供备用算法。
输入文案、ASR 原始字符、词数及句数分别限制为 20000；词时间必须有限、非负、递增且不重叠。
业务错误返回 `{"error":{"message":"错误说明"}}`：输入错误 422、内部约束错误 500、模型错误 502、超时 504。
缺少请求体、非对象 JSON 或 JSON 语法错误由 FastAPI 返回 422 和 `detail` 数组。

复制 `.env.example` 到 `.env` 并填写模型配置；从 `server/` 启动以读取该文件。
在仓库根目录使用 `uv run --project server --env-file server/.env server` 显式加载；`--project` 不切换当前目录。
配置由 `segmentation/settings.py` 的 Pydantic Settings 自动读取并校验类型、范围和时长关系。
`IMV_` 进程环境变量优先于当前工作目录 `.env`，再使用默认值；变量名不区分大小写，忽略无关字段，不修改全局环境、不缓存配置。
布尔配置接受 Pydantic 的标准布尔值（如 `true/false`、`1/0`）；非法值或缺少必填配置返回结构化 502，不暴露配置值。
模型地址、Key、模型名必填；默认超时 120 秒、SDK 重试一次；远程 HTTP 默认禁止。
默认片段时长 1200～6000 ms，关键词最多 5 个、每个最多 12 字，工作预算 250000。
其余变量见样例。SDK 客户端在成功或异常返回前关闭。

切片回归测试：`uv run --locked pytest tests/test_segmentation.py -v`。
共享夹具自动隔离外部 `IMV_` 环境变量与 `.env`；测试使用 fun-asr 结构的合成 ASR、用户真实转写的前两句摘录和 SDK 替身，不访问音频或真实模型。
输入契约用例覆盖旧结构、旧时间字段、空或非法音轨及多音轨拒绝，真实摘录覆盖多字词、前导空格、独立标点和跨句停顿。
覆盖对齐代价与时间、模型切点及异常、英文/数字保护、关键词过滤、时长告警、资源关闭和 HTTP 响应契约。
离线测试通过不代表真实 TTS/ASR/LLM 联调通过。

## ASR 音频转写

ASR 是独立的 Python 函数和命令行入口，尚未接入 FastAPI 路由。在 `server/` 下准备配置：

```sh
cp .env.example .env
```

填写北京地域的 `DASHSCOPE_API_KEY`；服务地址在 ASR 模块中固定为
`https://dashscope.aliyuncs.com/api/v1`。真实 `.env` 已被 Git 忽略。
模块加载时自动读取一次配置，优先使用源码目录的 `server/.env`；该文件不存在时
回退到当前工作目录的 `.env`，支持安装后的包。环境变量优先于文件，修改配置后需重启进程。

在 `server/` 下运行：

```sh
uv run --locked python -m server.asr "https://example.com/audio.wav"
```

替换为可被云服务访问的 HTTPS 音频直链。结果写入当前目录的 `asr_result.json`，
覆盖同名文件；保留原始 JSON 和字词时间戳，不额外分词。也可以在代码中调用：

```python
from server.asr import transcribe

result = transcribe("https://example.com/audio.wav", wait_seconds=1800)
```

等待预算必须是有限正数。超时不会取消已提交的云端任务；函数不自动重试提交。
