# 服务端协作约定

适用于 `server/`，补充根目录 `AGENTS.md`。启动、配置项、接口与部署的使用说明见 `server/README.md`。

## 结构

- 技术栈是 Python + FastAPI + MySQL。包内导入用相对路径，各模块向应用注册 `APIRouter` 实例；首页与用户路由仍是示例，尚未接入用户存储。
- CORS 允许精确 localhost 主机的动态 HTTP 端口。未处理的异常由 `app.py` 的 `InternalErrors`（位于 CORS 之内）回答带来源头的 500 JSON 后照常抛出并记录，跨域客户端才不会把服务端错误误报成「无法连接服务端」。
- 客户端设置目录（`GET /api/settings/plugins`）由一级业务模块的 `settings_plugin.py` 导出 `SETTINGS_PLUGIN`，公共层按目录自动发现，不维护业务名单。入口及其父包不得初始化配置或启动外部任务；删除设置入口不等于删除业务模块，路由与业务依赖仍显式维护。
- ASR（`asr/`）只有 `transcribe` 函数和 `python -m server.asr` 命令行入口，没有 HTTP 路由，也没有客户端转写页面。`DASHSCOPE_API_KEY` 在 `asr/asr.py` 加载时读取一次；设置发现不加载 ASR 业务模块。

## 启动与配置

- 在 `server/` 下用 `uv run server` 启动，仓库根目录用 `uv run --project server server`；端口取 `server/.env` 的 `PORT`，空值或非法值阻止启动。
- 优雅退出最多等 `startup/settings.py` 的 `GRACEFUL_SHUTDOWN_SECONDS`（10 秒），超时就断开仍未结束的连接（如作品 SSE），保证旧进程退出，不与重启后的进程同时运行字效任务队列与清理。
- 所有配置类继承 `config_base.py` 的 `CommonSettings`，读取固定的 `server/.env`，优先级为构造参数 > 进程环境变量 > 文件 > 默认值，保留 `_env_file` 覆盖与 `None` 禁用。不做安装位置发现，不建立配置树或统一快照；运行期间不改配置，改了要重启。
- 真实 `.env` 不入库；新增配置项时同步无密码的 `server/.env.example`。
- 数据库：lifespan 启动时连接目标库，只在 MySQL 返回 1049（库不存在）时用临时无库连接建库（`utf8mb4` / `utf8mb4_bin`，正确引用库名）；配置、连接或建库失败都停止启动。首次模板请求自动建表；运行中的数据库失败返回可重试的 503。启动失败和退出时释放连接池，临时建库连接始终关闭。
- `pyproject.toml` 把官方 PyPI 设为 uv 默认索引。依赖版本拉不到时先检查索引覆盖和镜像同步，不要为绕过镜像降级依赖或删除 `uv.lock`；CI 用 `--locked` 校验依赖。

## 测试

- 测试放在 `server/tests/`，用 pytest 的 `test_*.py`、fixture 与参数化，共享夹具放 `conftest.py`；不新增 unittest 风格的测试。按功能组织文件，规模增大后再分目录。
- 共享夹具会隔离临时 SQLite、清除外部 `IMV_*`、`DASHSCOPE_API_KEY` 与旧 `ASR_BASE_URL` 环境变量，并把各配置类的文件路径指向临时 `server/.env`。新用例显式注入配置，不访问真实 MySQL、ASR 或模型服务。
- ASR 测试隔离文件、密钥、HTTP 与轮询等待：首次导入屏蔽 `.env`，请求走内存传输。设置发现用临时真实包测试。
- 运行：`server/` 下 `uv run --locked pytest -v`，仓库根目录 `uv run --locked --project server pytest server/tests -v`。pytest 只作为开发依赖维护在 `pyproject.toml` 与 `uv.lock`。
