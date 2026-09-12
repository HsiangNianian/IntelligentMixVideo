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


## 文案切片

`POST /segmentations` 的实现集中在 `sub_api/segmentation.py` 的单个函数。
输入正确文案与已有 ASR 词级时间轴（也支持 `transcripts[0]` 外层和 `begin_time/end_time` 字段）：

```json
{"script":"你好世界。","asr_result":{"sentences":[{"words":[{"text":"你好世界","begin_time_ms":0,"end_time_ms":2000}]}]}}
```

返回 `segments`（文本、起止毫秒、带下标的关键词）、`warnings` 和 `trace`。
字符级波前对齐保留替换代价 1，增删在局部修复块中插值；模型只返回语义切点和关键词。
文本完整覆盖、时间不重叠、关键词精确回溯是硬约束；时长无法满足时告警。
模型失败直接报告，不调用 TTS/ASR，也不提供备用算法。
输入文案、ASR 原始字符、词数及句数分别限制为 20000；词时间必须有限、非负、递增且不重叠。
无效输入/超预算返回 422，模型失败或配置无效返回 502，超时返回 504，错误消息位于 `error.message`。

复制 `.env.example` 到 `.env` 并填写模型配置；从 `server/` 启动以读取该文件。
进程环境变量优先于当前工作目录 `.env`，代码不修改全局环境、不缓存配置。
模型地址、Key、模型名必填；默认超时 120 秒、SDK 重试一次；远程 HTTP 默认禁止。
默认片段时长 1200～6000 ms，关键词最多 5 个、每个最多 12 字，工作预算 250000。
其余变量见样例。SDK 客户端在成功或异常返回前关闭。

切片回归测试：`uv run --locked pytest tests/test_segmentation.py -v`。
测试使用小型合成 ASR 和 SDK 替身，不访问真实模型；不提交大型样本或测试专用依赖。
