# 切片协作约定

适用于 `server/src/server/segmentation/`，补充根目录与 `server/` 的 `AGENTS.md`。

接口、切分规则、模型调用、翻译与日志的完整说明见 `server/README.md`「文案切片」，改动前先读。下面是改代码时必须守住的约束。

- 切片不调用 ASR：输入是正确文案与已有的 ASR 结果。
- 代码不硬切：超过上限的片段只交给模型用 `[br]` 切分，代码只采用逐字一致、切点不在受保护串或修复块内的切法；最多 3 轮，之后仍超长就完整保留并返回 `segment_over_max_chars` 警告。整段受保护的超长串不请求模型。
- 关键词按片段 ID 由代码填回：缺失、不在原文里连续出现或超过 12 字的词置空；`title_keyword` 也必须是标题里的连续原文。英文关键词复用这套校验但不限长度。
- 提词回复不合法时把原回复与错误反馈给模型，共 3 次，耗尽返回 502；只有标题时只调用一次、不纠错。翻译不纠错、不重试。
- 单次模型请求最多 30 秒并关闭 SDK 重试，整次切片共用 90 秒软预算；次数、超时、并发与预算是模块内具名常量，改动时同步 README。
- `thinking=disabled` 只在 `IMV_LLM_DISABLE_THINKING=true`（或 `config.llm_disable_thinking`）时发送，默认不发送。
- 日志：INFO 只记摘要；完整回复与成功 trace 脱敏后才写 DEBUG；提词失败的 `keyword_response` 由视频合成写入任务日志，不回显到接口。
- 独立调用时 `max_segment_chars` 默认 8；视频合成按模板算出上限后传入（见 `server/src/server/video_composition/AGENTS.md`）。
