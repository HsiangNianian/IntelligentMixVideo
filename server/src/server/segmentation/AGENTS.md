# 切片协作约定

适用于 `server/src/server/segmentation/`，补充根目录与 `server/` 的 `AGENTS.md`。

接口、切分规则、模型调用、翻译与日志的完整说明见 `server/README.md`「文案切片」，改动前先读。下面是改代码时必须守住的约束。

- 切片不调用 ASR：输入是正确文案与已有的 ASR 结果。
- 仅用 CT-Punc 与原标点切段：正文、原空白和原标点不变，冲突时原标点优先；只切句读白名单，保护数字/缩写内部标点；开符号随下段、闭符号随前段，空白不切，不生成无正文片段。详细规则见上述 README。
- 片段拼接等于合并标点后的文案，新增标点不占时间；超长仅返回 `segment_over_max_chars`，不硬切、不回退旧策略。
- 标点模型固定使用 FunASR 官方 `AutoModel` 的 `ct-punc`、`v2.0.4`（服务器启用 `ct-punc` extra，设备由官方库选择）；extra 只声明 funasr 与 torch，`pyproject.toml` 用 `python_version < '3'` 的覆盖排除 CT-Punc 用不到的 funasr 依赖（`sys_platform == 'never'` 仍会留在锁文件），保留 torch-complex，不设 `FUNASR_STRICT_IMPORT=1`，升级 funasr 或调整排除项须用真实模型测试和空 `MODELSCOPE_CACHE` 首次下载重新验证；下载与磁盘缓存交给官方库；启用依赖后必须在恢复任务/接收请求前预热，失败阻止启动。进程内串行复用；加载、推理或返回格式出错时同次调用内最多尝试 3 次（缺少依赖不重试），仍失败安全返回 502。请求不可覆盖模型来源。
- 关键词按片段 ID 由代码填回：缺失、不在原文里连续出现或超过 12 字的词置空；`title_keyword` 也必须是标题里的连续原文。英文关键词复用这套校验但不限长度。
- 提词回复不合法时把原回复与错误反馈给模型，共 3 次，耗尽返回 502；只有标题时不做输出纠错。翻译不做输出纠错，网络重试仍由 `llm_max_retries` 控制。
- 单次模型请求最多 30 秒并关闭 SDK 重试；提词与仅标题提词捕获超时、连接错误及 HTTP 408/409/429/5xx，原请求额外重试最多 3 次，其余 HTTP 错误直接失败。整次切片（含推理锁等待与推理，不含服务启动预热；直接 Python 调用仍计入首次加载）共用 120 秒软预算，CT-Punc 按剩余时间等锁并在拿锁后、加载后及每次推理前后检查截止时间；超时释放已持有的锁并返回 504，重试不重置，预算耗尽提前停止；次数、超时与预算是模块内具名常量，改动时同步 README。
- `thinking=disabled` 只在 `IMV_LLM_DISABLE_THINKING=true`（或 `config.llm_disable_thinking`）时发送，默认不发送。
- 日志：INFO 只记摘要；完整回复与成功 trace 脱敏后才写 DEBUG；提词失败的 `keyword_response` 由视频合成写入任务日志，不回显到接口。
- 独立调用时字数告警阈值 `max_segment_chars` 默认 8；视频合成按模板算出上限后传入（见 `server/src/server/video_composition/AGENTS.md`）。
