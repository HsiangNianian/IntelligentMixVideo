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
