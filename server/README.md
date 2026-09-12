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
uv run --locked python -m unittest discover -s tests -v
uv build --out-dir dist
```

维护 `uv.lock`，CI 使用 `--locked` 检查依赖与配置一致。
