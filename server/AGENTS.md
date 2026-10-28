# 服务端协作约定

适用于 `server/`，补充根目录 `AGENTS.md`。

## 结构与启动

- `server/` 使用 Python + FastAPI + MySQL，提供模板持久化 API 与 `POST /segmentations` 文案切片接口，首页与用户路由仍为示例、尚未接入用户存储。包内导入使用相对路径，向应用注册 `APIRouter` 实例。
- 在 `server/` 下执行 `uv run server` 启动 Uvicorn，默认监听 `0.0.0.0:20070`（所有 IPv4 接口，供服务器部署后远程访问）；`startup/settings.py` 的 `ServerSettings`（启动入口复用） 在每次启动时读取固定的 `server/.env` 中的 `PORT`，进程环境变量优先，范围为 1～65535，空值或非法值阻止启动；仓库根目录使用 `uv run --project server server`。命令行入口与 Debug 内置服务的 Uvicorn 优雅退出最多等待 `startup/settings.py` 的 `GRACEFUL_SHUTDOWN_SECONDS`（10 秒），超时后断开仍未结束的连接（如作品 SSE），保证旧进程退出，不与重启后的进程同时运行字效任务队列与清理。维护 `server/uv.lock`，CI 使用 `--locked` 验证依赖。
- `server/pyproject.toml` 显式将官方 PyPI 设为 uv 默认索引，与锁文件来源保持一致。遇到依赖版本不可用时先检查索引覆盖配置和镜像同步情况，不要仅为绕过镜像缺失而降低依赖版本或删除锁文件。
- 客户端设置目录通过 `GET /api/settings/plugins` 返回。一级业务模块的 `settings_plugin.py` 导出 `SETTINGS_PLUGIN`，字段由模块配置类生成；公共层按目录排序自动发现一次，不维护业务名单，不返回配置实例值。重复 ID、坏描述或导入失败直接报错；增删入口后重启后端并重新打开设置。入口及父包不得初始化配置或外部任务；无入口的模块不导入。删除设置入口不等同于删除整个业务模块，路由与业务依赖仍显式维护。当前 Agent、IMS、切片、ASR 与服务启动提供入口；Debug 通过入口的 `settings` 模型类生成完整 Schema，并在内置服务启动前加载客户端保存值；数据库不参与。ASR 仍无独立客户端转写页面；测试用临时真实包验证发现。
- `server/src/server/asr/` 提供独立的 `transcribe` 函数与 `python -m server.asr` 命令行入口，尚未注册 HTTP 路由；通过北京地域 Fun-ASR 接收 HTTPS 音频直链并返回原始转写 JSON。字段声明位于 `asr/settings.py`，公开转写函数通过包入口按需导入；设置发现不加载业务，`DASHSCOPE_API_KEY` 在 `asr/asr.py` 业务模块加载时读取一次，固定读取源码 `server/.env`，不存在时不回退工作目录，进程环境变量优先；测试隔离文件、密钥、HTTP 和轮询等待。

## 配置与数据库

- 数据库配置由 `database.py` 的 `DatabaseSettings`（`pydantic-settings`）自动读取固定的 `server/.env`，字段为 `DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD`、`DB_NAME`，可选 `DB_SSL_CA` 指定 CA 文件启用 TLS 校验，进程环境变量优先；端口校验 1～65535，库名校验 1～64 字符。启动初始化时加载，修改后重启服务。真实环境文件不得入库，维护无密码示例 `.env.example`。
- 启动端口、数据库、ASR、切片、Remotion 与视频合成的现有配置类继承 `config_base.py` 的 `CommonSettings`，统一读取固定的 `server/.env`；构造参数 > 进程环境变量 > 文件 > 默认值，保留 `_env_file` 覆盖与 `None` 禁用。路径仅面向当前源码布局，不做安装位置发现。保留字段、校验、实例化时机和 Remotion 相对数据目录行为，不建立配置树或统一快照；运行期间不修改配置，修改后重启服务。共用 `server/.env.example`，根目录启动使用 `uv run --project server server`。
- FastAPI lifespan 启动时连接目标库，仅在 MySQL 返回 1049（库不存在）时通过临时无库连接执行 `CREATE DATABASE IF NOT EXISTS`，使用 `utf8mb4` / `utf8mb4_bin` 并正确引用库名；已有库直接复用。配置无效、连接或建库失败时停止启动，建库账号须具备对应权限。首次模板请求自动创建缺失表；运行中的数据库失败返回可重试的 503。启动失败和退出时释放连接池，临时建库连接始终关闭。

## 服务端测试

- 服务端测试统一放在 `server/tests/`，使用 pytest 的 `test_*.py`、fixture 和参数化用例；共享夹具放 `conftest.py`，不再新增 unittest 风格测试。按功能组织文件，规模增大后再分目录。
- 共享服务端夹具保留临时 SQLite 数据库隔离，同时自动清除外部 `IMV_`、`DASHSCOPE_API_KEY` 与旧 `ASR_BASE_URL` 环境变量并将各配置类的文件路径指向临时 `server/.env`，切换临时目录，避免读取本机模型配置；ASR 首次导入屏蔽 `.env`，请求使用内存传输。配置用例显式注入，不访问真实 MySQL、ASR 或模型服务。
- 服务端目录执行 `uv run --locked pytest -v`；仓库根目录执行 `uv run --locked --project server pytest server/tests -v`。pytest 仅作为开发依赖维护在 `server/pyproject.toml` 和 `server/uv.lock`。
