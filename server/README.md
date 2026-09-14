# IntelligentMixVideo API

Python 3.12+、FastAPI 和 MySQL。模板库在连接此服务的客户端之间共享，不包含登录、用户隔离或旧数据迁移。
另提供文案切片接口，使用已有 ASR 时间轴与 OpenAI 兼容模型生成带时间和关键词的片段。

## 本地启动

先启动 MySQL，再复制 `.env.example` 为 `server/.env`，填写 `DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD` 和 `DB_NAME`。
`pydantic-settings` 自动读取并校验配置，进程环境变量优先于 `.env`，缺省项使用代码默认值。
数据库配置文件固定为 `server/.env`，切换工作目录不改变读取位置；`DB_PORT` 自动转换为整数，范围为 1～65535。
`DB_NAME` 为 1～64 字符，默认 `intelligent_mix_video`。修改配置后重启服务。
启动时检查目标数据库，不存在则自动创建，使用 `utf8mb4` 字符集与 `utf8mb4_bin` 排序规则。
建库需要配置的账号具备对应 `CREATE` 权限；已有数据库直接连接，不执行建库或修改已有数据。
配置无效、MySQL 不可达、鉴权或建库权限不足时，应用报错并停止启动；修正后重新启动。
真实 `.env` 已被 Git 忽略，不要把密码写进示例文件或客户端配置。

在本目录执行：

```sh
uv sync --locked
uv run --locked server
```

默认监听 `http://127.0.0.1:8000`，交互文档为 `http://127.0.0.1:8000/docs`。
仓库根目录可执行 `uv run --locked --project server server`。首次模板请求自动创建缺失的 `templates` 表，不执行旧数据迁移。
应用启动后数据库暂时不可用时，模板接口返回 503，恢复后可重试；首页和用户示例接口本身不查询数据库。

端口冲突时可以单独指定端口，并同步修改客户端 `client/.env` 中的 `VITE_API_URL`：

```sh
uv run --locked uvicorn server.app:app --host 127.0.0.1 --port 8010
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
- `src/server/segmentation/`：独立切片函数与 `IMV_` 模型配置；`sub_api/segmentation.py` 注册切片路由。
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

切片业务集中在 `segmentation/segmentation.py` 的单个 `segment` 函数，
可通过 `from server.segmentation import segment` 导入，调用 `segment(payload)` 返回结果字典。
业务不依赖 FastAPI，失败抛出异常；`sub_api/segmentation.py` 负责 `POST /segmentations` 路由和 HTTP 错误转换。
输入正确文案与单音轨 fun-asr 原始转写结果；必须包含恰好一个 `transcripts` 元素，词时间使用 `begin_time/end_time`（毫秒）。不再接受顶层 `sentences` 或仅有 `begin_time_ms/end_time_ms` 的旧输入：

```json
{"script":"你好世界。","asr_result":{"transcripts":[{"channel_id":0,"sentences":[{"words":[{"text":"你好世界","begin_time":0,"end_time":2000}]}]}]}}
```

返回 `segments`、`warnings` 和 `trace`；每段含 `segment_id`、`group_id`、`text`、`keyword`、`level` 和起止时间。
`segment_id` 为从 1 递增的整数，`start_time`/`end_time` 为秒（内部时长约束仍以毫秒计算，仅输出换算）。
每段的 `keyword` 为字符串：无关键词时是空字符串 `""`，否则是段内原文中按出现位置保留的一个关键词。
`group_id` 为 `[current, total]`：`current` 是该段在其所属 ASR 句内的序号（从 1 开始），`total` 是该句最终切出的段数，未切分的句子为 `[1, 1]`；跨句片段整体计入其首字所在句。
`level` 只由代码判定：含关键词为重点句 `2`，其余为普通句 `1`；CTA 属模型语义判断，当前不标注。

### 处理约束与配置

处理流程为：校验文案与 ASR → 字符对齐并投射时间 → LLM 选择语义切点 → 代码调整时长 → LLM 标注最终片段关键词 → 校验输出。
两次模型调用有先后依赖；最终切点可能因保护词串、合并短段或拆分长段而调整，并非完全由 LLM 决定。
字符级波前对齐的替换、插入和删除代价均为 1，忽略所列标点与空白，并逐字符做 NFKC 和小写归一化。
匹配率按匹配字符数除以文案与 ASR 两者中较长的有效字符数计算，低于 50% 拒绝、低于 90% 告警；这只能检查文本差异，无法判断差异来自 TTS 还是 ASR。
词内时间均分，增删在局部修复块中插值；时间来自已有 ASR，不读取音频，也不保证真实字级发音边界。
MVP 只提供中文标点分句供模型选择；无此类标点的长文主要由时长规则拆分。
提示词要求逐一判断并列全独立信息点的切点，以6～8字为节奏参考；仅语法不完整、依赖相邻句且合并后不超过10字时建议合并。已有超长分句只能保留边界，不保证最终10字上限。关键词先全篇筛选3～4个，总数不得超过4、每段最多1个（配置为0时不选），不足不凑数，再逐项核对所属片段、保留空数组位置。以上仍是提示词要求，时长后处理可能调整切点，代码不强制全篇4个；响应每段只保留原文最靠前的一个有效词，`IMV_SEGMENT_MAX_KEYWORDS=0` 时不选词。模型请求仍按段位置返回空数组 `{"keywords":[[],["词"],[]]}`，由代码投影为 `keyword` 字符串。
关键词由模型选择，代码只做逐字匹配、去重、长度过滤和原文顺序排列，并只保留最靠前的一个；区分大小写、全半角，被过滤或多余的候选计入 `trace.keyword_rejected_count`。
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
默认片段时长 1200～6000 ms，关键词每段最多保留 1 个（`IMV_SEGMENT_MAX_KEYWORDS` 为 0 时关闭选择）、每个最多 12 字，工作预算 250000。
其余变量见样例。SDK 客户端在成功或异常返回前关闭。

切片回归测试：`uv run --locked pytest tests/test_segmentation.py -v`。
共享夹具自动隔离外部 `IMV_` 环境变量与 `.env`；测试使用 fun-asr 结构的合成 ASR、用户真实转写的前两句摘录和 SDK 替身，不访问音频或真实模型。
输入契约用例覆盖旧结构、旧时间字段、空或非法音轨及多音轨拒绝，真实摘录覆盖多字词、前导空格、独立标点和跨句停顿。
覆盖对齐代价与时间、模型切点及异常、英文/数字保护、关键词过滤、时长告警、资源关闭和 HTTP 响应契约。
离线测试通过不代表真实 TTS/ASR/LLM 联调通过。

## ASR 音频转写

ASR 是独立的 Python 函数和命令行入口，尚未接入 FastAPI 路由。在 `server/` 下准备配置；已有 `.env` 时直接补充 `DASHSCOPE_API_KEY`，保留数据库与切片配置：

```sh
cp .env.example .env
```

填写北京地域的 `DASHSCOPE_API_KEY`；服务地址在 ASR 模块中固定为
`https://dashscope.aliyuncs.com/api/v1`。真实 `.env` 已被 Git 忽略。
模块加载时自动读取一次配置，优先使用源码目录的 `server/.env`；该文件不存在时
回退到当前工作目录的 `.env`。安装后的包只查找工作目录，不读取虚拟环境祖先目录的配置。
环境变量优先于文件，修改配置后需重启进程。

在 `server/` 下运行：

```sh
uv run --locked python -m server.asr "https://example.com/audio.wav"
```

替换为可被云服务访问且不含用户名或密码的 HTTPS 音频直链；结果下载地址也遵守这一限制。
省略地址时显示用法并退出，使用 `--help` 查看帮助。结果写入当前目录的 `asr_result.json`，
覆盖同名文件；保留原始 JSON 和字词时间戳，不额外分词。也可以在代码中调用：

```python
from server.asr import transcribe

result = transcribe("https://example.com/audio.wav", wait_seconds=1800)
```

等待预算必须是有限正数。超时不会取消已提交的云端任务；函数不自动重试提交。
轮询休眠不超过剩余预算，但单次 HTTP 请求可能使实际等待超出预算。
