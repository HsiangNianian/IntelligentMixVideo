# IntelligentMixVideo API

Python 3.12+、FastAPI 和 MySQL。模板库在连接此服务的客户端之间共享，不包含登录、用户隔离或旧数据迁移。
另提供文案切片接口，使用已有 ASR 时间轴与 OpenAI 兼容模型生成带时间和关键词的片段。
服务端与客户端使用同一项目版本；`pyproject.toml`、`uv.lock` 随发版统一更新并提交，操作见[根目录发版说明](../README.md#tag-发版)。FastAPI 文档版本读取已安装的 `imv-server` 包元数据；更新后通过 `uv run --locked server` 同步安装并重启。

## 本地启动

先启动 MySQL，再复制 `.env.example` 为 `server/.env`，填写 `DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD` 和 `DB_NAME`。
`pydantic-settings` 自动读取并校验配置，进程环境变量优先于 `.env`，缺省项使用代码默认值。
各模块通过 `config_base.CommonSettings` 共用读取规则，配置文件固定为 `server/.env`，切换工作目录不改变读取位置；`DB_PORT` 自动转换为整数，范围为 1～65535。
`DB_NAME` 为 1～64 字符，默认 `intelligent_mix_video`。修改配置后重启服务。
启动时检查目标数据库，不存在则自动创建，使用 `utf8mb4` 字符集与 `utf8mb4_bin` 排序规则。
建库需要配置的账号具备对应 `CREATE` 权限；已有数据库直接连接，不执行建库或修改已有数据。
配置无效、MySQL 不可达、鉴权或建库权限不足时，应用报错并停止启动；修正后重新启动。
真实 `.env` 已被 Git 忽略，不要把密码写进示例文件或客户端配置。

公共基类只统一读取规则，各模块保留原配置类、字段、校验和实例化时机。
优先级为构造参数 > 进程环境变量 > `server/.env` > 字段默认值；保留 `_env_file` 显式覆盖与 `None` 禁用文件。
固定路径按当前源码布局计算，不自动适配任意安装位置；非源码部署请显式提供配置文件或使用进程环境变量。
不提供热更新或统一配置快照；运行期间不要修改 `.env`，修改后重启服务。

在本目录执行：

```sh
uv sync --locked
uv run --locked server
```

默认监听 `0.0.0.0:20070`（所有 IPv4 接口），本机交互文档为 `http://127.0.0.1:20070/docs`。
远程访问使用 `http://<服务器 IP 或域名>:20070`，服务器防火墙或云安全组需允许对应端口；客户端 `VITE_API_URL` 配置为实际服务地址。
仓库根目录可执行 `uv run --locked --project server server`。首次模板请求自动创建缺失的 `templates` 表，不执行旧数据迁移。
应用启动后数据库暂时不可用时，模板接口返回 503，恢复后可重试；首页和用户示例接口本身不查询数据库。

`uv run server` 与 `uv run python -m server` 均读取固定的 `server/.env` 中的 `PORT`，进程环境变量优先。未设置时默认 `20070`；端口须为 1～65535 的整数，空值、非整数或越界值会阻止启动。修改后重启服务。
客户端 API 地址同样默认 `http://localhost:20070`；若 `client/.env` 已配置其他 `VITE_API_URL`，需同步修改并重启前端（生产环境重新构建）。

```sh
PORT=8010 uv run --locked server
```

本机 Python 包镜像若落后于锁定版本，可以在 uv 命令中添加 `--default-index https://pypi.org/simple`，无需降级项目依赖。

## 模板接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/template` | 返回完整模板数组，按更新时间倒序，空库返回 `[]` |
| POST | `/template` | 无 `template_id`（或为 null）创建，携带 ID 完整更新 |
| GET | `/template/{template_id}` | 返回单个模板完整配置 |
| DELETE | `/template/{template_id}` | 删除模板，成功返回 204 |

创建返回 201，更新返回 200。名称重复返回 409，模板不存在返回 404，非法 ID 或配置返回 422。
重命名使用携带 ID 的 POST；另存为使用不携带 ID 的 POST。不存在的 ID 不会自动变成创建。

创建示例：

```json
{
  "name": "简洁字幕",
  "description": "标题使用淡入动画",
  "editor": { "titleIn": "in/fade_in" },
  "effect_ids": ["in/fade_in"],
  "transition_duration_seconds": 0.5
}
```

`editor` 缺省字段补齐默认值，外层更新按完整配置替换，不是局部 PATCH。
字段及范围在 OpenAPI 中列出：名称去除首尾空白后 1～100 字符；说明最多 1000 字符；
标题、字幕、气泡示例文字最多 60、100、40 字符；字号 12～120 整数；位置 0～100%；动画和转场时长 0.1～3 秒。
同一文字角色的循环动画与入场、出场互斥。至少选择 1 个效果，最多 20 个不同效果 ID。

响应补充 UUID、UTC 创建/更新时间和 `effects` 参数快照。服务端通过固定 SDK 5.2.2 白名单解析效果，
拒绝未知 ID、错误分类和 `editor` 与 `effect_ids` 不一致；客户端不能提交渲染参数。
`schema.py` 支持 editor 的 camelCase 输入输出及 snake_case 输入，与原模板字段语义保持一致；本次不启用 protobuf 通信。

MySQL 单独列保存唯一名称、ID 和时间，JSON 保存完整编辑配置与效果快照。
保存和删除使用事务；同时保存同名新模板仅一个成功。同时编辑同一个模板时，后一次成功保存覆盖前一次完整配置。

## 代码结构

- `src/server/database.py`：`DatabaseSettings` 自动加载并校验环境配置，启动时创建缺失数据库，管理 MySQL 连接池。
- `src/server/template/`：模板模块，与用户示例目录 `sub_api/` 平级。
- `src/server/template/router.py`：四个模板接口，向 `app.py` 注册 APIRouter。
- `src/server/template/schema.py`：请求、响应、数值范围与效果组合校验。
- `src/server/template/store.py`：建表、查询和事务写入。
- `src/server/segmentation/`：独立切片函数、`IMV_` 模型配置与 `router.py` 切片路由。
- `sdk_catalog.json`、`motions.json`：来自参考项目的固定 5.2.2 效果白名单；升级 SDK 时同步核对。没有效果目录 API。

首页 `GET /` 和 `GET /users/`、`GET /users/{user_id}` 仍保留示例响应，尚未接入用户存储。

## 验证与打包

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

提供 `POST /segmentations` 接口和独立的 `segment` 函数，使用正确文案与已有 ASR 结果生成带时间和关键词的片段。

在 `server/.env` 填写 `IMV_LLM_BASE_URL`、`IMV_LLM_API_KEY` 和 `IMV_LLM_MODEL`，其余配置见 [.env.example](.env.example)。
配置读取固定的 `server/.env`，环境变量优先；从仓库根目录启动时使用：

```sh
uv run --locked --project server server
```

HTTP 请求体包含 `script`（正确文案字符串）和 `asr_result`（Fun-ASR 原始结果对象），由 Pydantic 校验必填字段与类型。也可在代码中读取 ASR 输出文件并调用：

```python
import json
from pathlib import Path
from server.segmentation import segment

result = segment({
    "script": "你好世界。",
    "asr_result": json.loads(Path("asr_result.json").read_text(encoding="utf-8")),
})
```

使用 ASR 第一音轨的词级时间，输入时间为毫秒。返回 `segments`、提示 `warnings` 和诊断信息 `trace`；
片段包含原文、秒制起止时间、分组和关键词。切片本身不调用 ASR。

## ASR 音频转写

ASR 提供独立的 Python 异步函数和命令行入口，尚未接入 FastAPI 路由。在 `server/` 下准备配置；已有 `.env` 时直接补充 `DASHSCOPE_API_KEY`，保留数据库与切片配置：

```sh
cp .env.example .env
```

填写北京地域的 `DASHSCOPE_API_KEY`；服务地址在 ASR 模块中固定为
`https://dashscope.aliyuncs.com/api/v1`。真实 `.env` 已被 Git 忽略。
模块加载时自动读取一次固定的 `server/.env`，文件不存在时不回退到工作目录。
环境变量优先于文件，修改配置后需重启进程。

在 `server/` 下运行：

```sh
uv run --locked python -m server.asr "https://example.com/audio.wav"
```

替换为可被云服务访问且不含用户名或密码的 HTTPS 音频直链；结果下载地址也遵守这一限制。
省略地址时显示用法并退出，使用 `--help` 查看帮助。结果写入当前目录的 `asr_result.json`，
覆盖同名文件；保留原始 JSON 和字词时间戳，不额外分词。在异步函数中使用 `await` 调用：

```python
from server.asr import transcribe

result = await transcribe("https://example.com/audio.wav", wait_seconds=1800)
```

同步脚本入口可使用 `asyncio.run(transcribe(audio_url))`；已有事件循环中使用 `await`。
HTTP 请求与轮询等待均为异步，不阻塞事件循环。等待预算必须是有限正数。
超时或取消本地协程不会取消已提交的云端任务；函数不自动重试提交。
轮询休眠不超过剩余预算，但单次 HTTP 请求可能使实际等待超出预算。
