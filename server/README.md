# IntelligentMixVideo API

需要 Python 3.12+ 和 uv。在本目录执行即可安装锁定依赖并启动 API：

```sh
uv run server
```

默认地址为 http://127.0.0.1:8000，交互文档为 http://127.0.0.1:8000/docs。
按 Ctrl+C 停止服务。仓库根目录可执行 `uv run --project server server`。
也支持 `uv run python -m server`。

需要修改监听地址、端口或启用开发热重载时：

```sh
uv run uvicorn server.app:app --host 127.0.0.1 --port 8001 --reload --reload-dir src
```

## 接口

- `GET /`：首页消息。
- `GET /users/`、`GET /users/{user_id}`：用户路由示例，尚未接入用户存储。
- `POST /segmentations`：文案切片，需要先配置模型服务。

### 文案切片

把**正确口播文案**与 **ASR 词级时间轴**对齐，切分为可用于素材召回的片段。

请求：

```json
{
  "script": "正确口播文案",
  "asr_result": { "...": "ASR 原始返回，需含词级 begin_time / end_time" }
}
```

`asr_result` 兼容阿里云 fun-asr 的原始响应结构（`properties` + `transcripts[0]`），
也可直接传精简结构 `{"sentences": [{"words": [...]}]}`。

响应：

```json
{
  "segments": [
    {
      "segment_id": "seg_001",
      "text": "刚才我家人还问我，家里不是还有鸡蛋吗？",
      "start_time_ms": 160,
      "end_time_ms": 2400,
      "keywords": [{ "text": "鸡蛋", "start": 13, "end": 15 }]
    }
  ],
  "warnings": [],
  "trace": {
    "matched_chars": 214, "substitution_chars": 0,
    "script_extra_chars": 0, "asr_extra_chars": 0, "edit_cost": 0,
    "repair_block_count": 0, "merge_count": 0, "split_count": 4,
    "segment_count": 9, "keyword_rejected_count": 0
  }
}
```

`keywords[].start/end` 是片段文本内的字符下标，满足 `text[start:end] == keyword.text`。

职责边界：模型只给出分句切点编号与关键词候选，不产出任何时间数字；
逐字对齐、时间继承与插值、时长约束和关键词校验全部由确定性代码完成。
对齐采用字符级编辑距离：先剥离公共前后缀并按命中输出，只对中间段建表，
一致部分保留 ASR 时间，一对一错字直接替换，差异与相邻字合并为「修复块」
并在块内按文案字数均分时间。

错误码：`asr_timeline_missing`（缺词级时间戳）、`asr_transcript_too_long`（转写超长）、
`alignment_input_too_large`（对齐规模超限）、`script_asr_alignment_failed`（文案与音频不匹配），
以上均为 422；未配置模型为 502 `llm_provider_error`。

## 配置

配置从 `server/.env` 读取，变量使用 `IMV_` 前缀，样例见 `.env.example`。

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `IMV_LLM_BASE_URL` / `IMV_LLM_MODEL` / `IMV_LLM_API_KEY` | 空 | OpenAI 兼容模型服务，三项缺一不可 |
| `IMV_ALLOW_INSECURE_LLM_HTTP` | `false` | 远程 HTTP 模型地址需显式开启 |
| `IMV_LLM_TIMEOUT_SECONDS` / `IMV_LLM_MAX_RETRIES` | `120` / `1` | 单次调用超时与可重试次数 |
| `IMV_SEGMENT_MIN_DURATION_MS` / `IMV_SEGMENT_MAX_DURATION_MS` | `1200` / `6000` | 片段时长上下限（毫秒），代码据此合并与切分 |
| `IMV_SEGMENT_MAX_KEYWORDS` | `5` | 每段关键词数量上限 |
| `IMV_SEGMENT_KEYWORD_MAX_LENGTH` | `12` | 单个关键词字数上限，不设下限 |
| `IMV_SEGMENT_MAX_ALIGNMENT_CELLS` / `IMV_SEGMENT_MAX_ASR_CHARS` / `IMV_SEGMENT_MAX_ASR_WORDS` | `4000000` / `20000` / `20000` | 对齐矩阵与 ASR 转写文本规模上限，超限返回 422 |

未配置模型时 `/segmentations` 返回 502 `llm_provider_error`，不静默降级。

## 验证

```sh
uv run --locked python -m unittest discover -s tests -v
uv build --out-dir dist
```

`tests/fixtures/fun_asr_egg_sample.json` 是真实 fun-asr 返回的固定样本，
全部测试基于它离线运行，不访问外部服务。

维护 `uv.lock`，CI 使用 `--locked` 检查依赖与配置一致。
