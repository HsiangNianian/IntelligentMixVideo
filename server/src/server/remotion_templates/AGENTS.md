# Remotion 字效服务协作约定

适用于 `server/src/server/remotion_templates/`，补充根目录与 `server/` 的 `AGENTS.md`；使用说明见本目录 README。

渲染资源与 worker 见 `server/src/server/remotion/AGENTS.md`；客户端：字效工作区见 `client/src/features/remotion_templates/AGENTS.md`，模板中的 Remotion 片段与叠加预览见 `client/src/features/templates/AGENTS.md`，字体库页面见 `client/src/features/fonts/AGENTS.md`。

本模块的行为细节写在本目录的 [README](README.md)（英文），下面只列改代码时必须守住的约束。改动某一块之前，先读 README 对应章节：

| 改动涉及 | 先读 |
| --- | --- |
| 作品、任务、事件送达、设备归属、对象存储、定时清理 | README「Storage」 |
| Preset 库与 `preset.search` 的返回限制 | README「Preset storage」 |
| 工具目录、未实现的工具、参考图 | README「Tool catalog」 |
| 文字组合、特效与逐字时序、关键词字效、双栏与关键词开关 | README「Text effects are composed from role Presets」 |
| 字体库：预装、上传与归卡、沙箱字体、Agent 的字体枚举 | README「Font library」 |
| 隔离环境、503 预检、macOS 无沙箱开发、即时保存的参数修订 | README「Font library」之后的几段 |
| `sprite.create` 的透明度检查、`text_fit`、回看帧、多行字效 | README「Creating a Sprite」 |
| 三层 ReAct、空转判断、步骤产出、调用参数解析、公开的轮次记录 | README「Loop rounds」 |
| 资产发布、覆盖、删除、改名、目录 SSE、边界样本、单 Sprite 渲染、叠加预览协议 | README「Sprite assets」 |
| 总线叠加渲染：时长、排版预检、准入、执行、性能 | README「Overlay renders for the bus」 |
| 成功版本诊断接口 | README「Code diagnostics」 |
| 工具入参、绑定规则、分类必备参数与回执的完整契约 | `docs/remotion-agent-tool-contracts.md` |
| 视频合成如何使用 Remotion 资产 | `docs/remotion-video-composition.md` |

## 运行与存储

- 只按单实例部署：作品、任务、版本、模型窗口、待送达事件与叠加渲染任务都在共享 MySQL（8.0 及以上，`tables.py`），租约行 `remotion_instance` 保证同一数据库只有一个实例领取任务；后端不创建 SQLite 或运行锁。
- 作品属于创建它的设备：作品相关 JSON 接口必须携带 `X-IMV-Device`，其他设备的作品按不存在处理；服务端不提供作品列表。预览页、字体、参考图与产物下载按不可猜测的 ID 访问，不校验设备。
- 封存版本文件、参考图、预览包与字体文件是 `imv/remotion/` 下的对象（`object_store.py`），后端只经 SHA-256 校验的本地缓存读取；模块 `.data/` 只放任务草稿、缓存与私有审计。对象的上传、下载与删除在工作线程里执行，不放在写锁里。
- 目录读写放到工作线程，不能阻塞事件循环、SSE 或取消；取消的任务不发布版本。
- 删除会话：先持久化删除标记，取消并等待运行任务与队列收尾，再删任务草稿与审计、所有成功版本的对象与缓存（ZOS 上的对象默认保留，见 `ZOS_DELETE_OBJECTS`）、没有其他会话引用的参考图与关联 MySQL 记录；不删其他会话或已发布资产。完成或不存在返回 204；清理失败返回 503（说明为「会话数据清理未完成，请重试删除。」，根因写服务端日志 `Work deletion is incomplete`）并保留标记，供显式重试与启动恢复；删除中的读写返回 410；已有 SSE 先发无 ID 的 `work.deleted` 控制事件再关闭。
- 参考图：新图片在入队事务内校验存在、由本设备上传且不属于正在删除的会话（404/409）；作品用到的每张图记在 `remotion_work_assets`，任务记录送达删除后仍据此判断引用。回答图片记录所答 `clarifications` 序号，该条澄清也写入 `answer_images`。上传 24 小时后仍无作品或任务引用的素材由每小时清理调用 `Store.prune_assets` 回收，正在删除的会话仍引用的图留给删除流程；回收失败只记日志，不影响上传或启动。
- 公开进度存 `remotion_job_progress`，与 `job.updated` 在同一事务发布、送达后随任务清理；循环记录经 `job.round` 增量发布、逐条存 `job_rounds`，终态任务不再接受记录。
- 配置类在 `settings.py`（经 `server.remotion_templates.settings` 导入），继续复用 `config_base.CommonSettings` 读取 `server/.env`，相对数据目录和默认 `server/remotion/` 渲染资源位置不变。渲染默认值集中在这里，`.env.example` 新增的渲染项只写 CPU/内存预留比例；模型预算、超时与重试的默认值与 `.env.example` 同步。

## 隔离与渲染

- 生成的代码只在 Linux 沙箱（`bwrap`、`prlimit`、`taskset`）里运行。需要沙箱的入口在入队前检查环境，不满足返回 503 并说明原因，不留下作品或任务。`IMV_ALLOW_UNSANDBOXED_WORKER` 只在 macOS 开发时生效，且不包括 `overlay-worker.mjs`。
- 沙箱命令只在 `Renderer.command` 构造（字体目录的只读挂载与 `FONTCONFIG_FILE` 也在这里）；不在各 worker 里各自加载字体，不给沙箱开放网络或 GPU。
- `preset.create`、`sprite.create` 的校验器无法启动时返回 `VALIDATION_UNAVAILABLE`；组件自身抛错是 `CODE_VALIDATION_FAILED`；`PRESET_STORE_FAILED`、`SPRITE_STORE_FAILED` 只用于存储失败。
- 排版预检、渲染与校验都经 `RenderCapacity` 按 CPU/内存准入，取消或失败时释放。分块渲染保持帧区间连续完整、动画按绝对时钟，拼接无损。
- 叠加渲染失败只公开稳定错误码与固定说明，渲染器输出等根因只写服务端日志。叠加渲染任务与 IMS 合成的代码和表分开，只复用 `ZosSettings` 与 `zos.upload_public`。
- 渲染前的排版预检只改这次渲染的副本（缩小字号、把摆放平移回画布），不改模板、保存的摆放与封存源码；仍放不下以 `text_too_long` 失败，不能输出被裁切的文字。代码或参数点名了本机未缓存的库内字体时先取回，取不到以 `font_unavailable` 失败，不回落默认字体。
- 成功版本的诊断接口只读：不排队、不发布、不新增表；隔离运行时不可用返回 503，不能把「未检查」报告成「无诊断」。

## 字体库

- `remotion_fonts` 只增不删，字体 ID 与家族名不变，只能修正显示名、预览条与字形；库永远不空（系统默认字体在运行时首次使用时登记）。
- 预装字体随后端包发布，安装时不联网；增删预装字体要同时更新 `font_catalog.json`、`bundled_fonts/` 与 `bundled_fonts/LICENSES.txt`，测试会核对三者一致。
- 后端只用有边界检查的纯 Python 读取 name 表，不解码字体，也不参与修复（修复在浏览器端，见 `client/src/features/fonts/AGENTS.md`）。
- 字效的 `fontFamily`（及双栏的逐栏字体）保存时换成普通字符串（`open_font_schema`），之后的选择一律按实时字体库校验（`fonts.require_family`），不按生成时冻结的枚举。

## 生成、校验与发布

- Sprite 由 style（Agent 生成的代码与样式参数）和 content（文字、关键词，由总线每次渲染传入）组成；时长不属于 Sprite，由渲染请求决定，Agent 的规则与提示词不处理时长。
- `sprite.create` 的透明度检查与 `text_fit` 只用于生成路径：用户手动改参数与已发布版本不被它们阻止，也不重新检查。
- 透明度之后再以 Sprite 可能按自身长度播放的最短（0.5 秒，15 帧；字效更短时会被定格，所以字效为 1.5 秒）与最长（120 秒，3600 帧）长度各挂载首、中、尾帧（失败后恢复预览长度再跑自定义测试），抛错以 `render_lengths` 失败交给 Actor 修复（说明长度与帧号），不新增时长相关的提示词。
- 契约违规的诊断携带行列与范围，跨行节点的终点取自节点自身的结束位置，空转判断忽略消息里的位置，同一条违规只是换了行号不算新证据。
- 工具调用与检查共用 `registry.get_tool`，执行时只在当前层可用的工具中解析。
- 生成任务从不向用户提问：先按合理的选择做出 Sprite，用户之后再改。宿主拒绝 Outer 的 `ask`、Executor 的 `needs_input` 与 Plan 的 needs_input 结果；已委派（或已保存）的任务不能再以普通回答结束。字效的文字与关键词由模板每次渲染时传入，生成只需要预览用的示例文字，不为文案、关键词或其语言追问。
- 交给 Executor 的交接消息带上用户需求 `user_intent`（不含基线 spec），快照也把它放在最前。
- 回看帧：`sprite.create` 各项检查通过后，为任务保存的前两个 Sprite（`session.VISUAL_REVIEW_ROUNDS`）按该 Sprite 自身时序截取至多六帧附给 Executor，特效窗口按其作用栏实际播放的帧计算，切换帧只挤掉停留帧、不挤掉入场帧；回执只列帧号与采样时机，不宣称视觉通过。这些帧只供 Agent 对照要求与参考图，宿主不据此判定通过与否。
- 编辑任务只改用户要求的部分：基线已暴露为参数的数值走参数，写死在代码里的数值与画法从 `base_*` 草稿出发用 `edits` 只替换相关行，不改名、不重排、不重构其余代码，只在基线没有的元素上用 `preset_create`。
- 新字效用文字块（`sprite_compose` 的 `text`）组合；配置参数由宿主按固定名称生成（`compose.text_configuration`），Agent 只绑定样式参数，不能占用配置名。
- 逐字时序（契约 v10）：`chars`、`keywordChars`、`plainChars` 只选择作用范围；非持续特效必须在条目或 Preset 的 `usage.stagger` 里明确给出 0（同时）、1（依次）或其间值，漏填按字段拒绝，宿主不暗中选择逐字。已封存的版本不改写。
- Preset 库就是此前作品做成功的配方，规则里不维护「需求→做法」的配方表。使用记录在 `remotion_preset_uses`，登记失败只记日志；`role=effect` 的 Preset 新建时必须记下默认用法 `usage`。检索复用要读源码核对描述，错误的旧配方通过副本修订，不覆盖不可变的原件。
- Executor 的可执行示例在 `agent_examples.py`，浏览器回归运行同一份源码，检查显字、颜色、范围与光标；其中分片示例演示同一内容的互补裁片与独立位移，是绘制原语，不是预装配方或「需求→做法」的匹配表。
- 字效必须声明七个关键词参数，默认全部关闭；各分类的必备参数由 `categories.py` 定义，`sprite.create` 与发布都检查。
- 发布前渲染边界样本，样本失败只记日志、照常保存（可用性优先）。覆盖发布必须保留已有可编辑参数的契约，收紧即 422、提示另存；重复保存、覆盖、预览修复与删除共用该资产的 bundle 锁。
- `SpriteRenderInput` 已删除的 `effect_offset_frames`、`render_frame_count` 保留字段号，不能复用。
- 宿主生成的 `still` 文字块只按内容区域核对排版：`text_fit` 经仅宿主可用的 `text_fit_parameters` 临时关掉 `cue` 包裹，入场、出场与持续特效仍参与测量，`cue` 另在停留期初态测量，藏起该栏即拒绝；各栏可独立时序，旧自带动画组件保持同进同出。该补丁不写入资产与渲染参数，真实运行、透明度、不同时长与回看保留原动画。运行通过不等于视觉正确，完成说明要引用回看帧核对要求。
- 调整生成规则（如 `text` 的长度、提示词示例）不追溯已发布资产与任务快照，它们保留各自的封存 Schema；要更新资产需生成新版本。

## Actor 与 Judge 评审

README 没有写这部分，以下是完整约定。

- Remotion Harness 区分代码失败、评审协议故障、证据不足与渲染环境故障：有效代码失败才交给 Actor 修复；无效 Judge 结果对同一候选/证据有限纠错，合法负面结论不得重试成通过。纯未知证据由宿主有限补采样并保存独立清单，仍未知或环境不可用则停止，Agent 生成只有全部验收通过的版本可发布；用户手动参数修订使用独立渲染可用性门禁。整批工具回执保存后，宿主复核最新候选的证据、产物与环境，直接完成，不再要求模型调用完成工具；发布事务仍检查取消状态。合法空 motion 不强制补 hold；宿主观察到静态不代表用户允许静态，不能覆盖 Judge 对缺少所要求动画的有效失败。
- Judge 接收原始请求、最新修改、成功基线与完整候选方案；方案只解释实现。失败项须引用实际要求并声明作用对象、观察与不匹配，宿主校验引用和结构化作用域，不能把无依据否决交给 Actor，也不能声称字符串检查能证明语义正确。unknown/conflict 必须提供非空 missing_evidence，缺少说明先纠正评审，不直接补采样。检查灰底不属于模板内容。
- Judge 需求引用使用 `/user_intent/instruction` 等完整路径，宿主只移除一层明确的 `user_intent` 前缀并兼容旧相对路径；原参考图保持 `/reference_images/N`。规范化后仍校验原文、来源和作用域，重复前缀或候选路径不得放行；协议 steer 指出错误路径与原因，合法负面结论交给 Actor 修复。
- 模型读取代表帧，宿主保留全部 frame/probe 校验；Judge/Actor 通常最多接收 12/6 帧，不按剩余预算缩减，明确要求的补充帧与失败引用帧优先保留。模型帧号映射只对应实际发送图片，未展示的已有帧也可请求。输入估算默认仅用于诊断；只有开启预算时才预留输出并按完整旧工具组裁剪本次 Actor 请求，不修改持久窗口。有效 token 回执继续记账，缺失用量在私有审计记为 null。私有审计记录图片数、输入估算和可用的 token 明细。
- `IMV_ENFORCE_MODEL_BUDGET` 默认 false，暂不执行累计调用/token 配额、输入预算拦截、预算裁剪及 50 轮上限；设为 true 才恢复。开启时 Actor、Judge 的分类调用/token 预算共用任务总上限，恢复次数与连续无进展阈值通过 `server/.env` 配置并同步 `.env.example`。每轮检查实际观察，超时、取消、有限纠错/补证据和无进展保护始终保留，纯等待、重复读取或相同失败不得无限循环；私有任务 `audit.jsonl` 和候选 `reviews.jsonl` 保留诊断，独立于模型八组滑动窗口及公开聊天。允许公开宿主筛选的阶段名称与时间，以及循环记录中每次工具调用的有界原始参数与工具错误说明（见 README「Loop rounds」，有意公开，参数里可含计划步骤目标与候选源码）；不公开工具成功结果、原始评审与 steer 原文，完整内容留在私有审计。
- Remotion 使用统一 Actor 理解需求、制定方案、写代码和修复，不设置独立 Planner、目标预审或重规划工具。`submit_candidate` 首次提供 `spec` 与 TSX，后续可修订方案；模型估算的字号、位置、行高不是冻结目标。Judge 对照用户要求、参考与实际帧，只拒绝明显文字错误、不可读遮挡/裁切、缺少要求效果及未授权修改，允许合理字体近似和轻微布局差异。宿主保护显式画布约束、当前参数契约和成功版本证据，旧候选回执不能用于新方案。Runtime 每次执行只记一次用户请求，Actor 工具交互保持八组滑动窗口；连续无进展受既有阈值限制，总预算只在开关启用时限制。整组旋转按共同中心和像素坐标计算后归一化。
- Remotion 手动调参不调用 Actor/Judge，不重跑参数/动画语义探针；保留源码。组合版本（PR76）且基线播放器包能读取页面注入默认值（首行为 `presentation.mjs` 写入的协议声明 `/* imv-preview-protocol 1 */` 或更新的版本；生成代码与默认参数也会打进包里，只认首行，不在包内搜索标记字样）时，参数修订只做参数 Schema 校验，不进入任务队列、不启动沙箱、不重新打包（更早构建的包会继续显示编译时的默认值，这样的修订仍排队渲染并重建播放器包，之后的修订即可即时保存）：任务以运行中状态直接记录，复用基线的源码、播放器包与运行证据（`tool-validation.json` 的 `inherited_from` 指向基线），在宿主上用 `export.mjs` 只解析格式化地重写默认导出，立即封存 `source=user_parameters` 修订，预览页注入该版本自己的默认值，渲染检查留给发布时的边界抽样；更早版本仍经参数合法性、编译、预览与默认导出一致性、媒体规格及产物指纹检查后保存。发布事务必须核对代码与基线一致、参数/spec 与用户补丁一致；生成仍走完整验收。用户修订关联最近 Agent 版本，完整参数保存在任务输入，不进入模型窗口；后续任务从两份状态计算有来源的 `user_parameter_changes` 净变化，改回原值则移除，Actor/Judge 均据此尊重当前基线。生成成功才建立新基线，失败、回答、澄清不清除用户调整。
- Remotion 重复帧、默认导出与静态帧使用宿主统一的可见像素比较：黑白背景合成后的最大通道差不超过 2/255，且变化像素不超过可见前景并集的 1%，才视为栅格噪声；噪声不能证明动画或参数生效。失败 steer 包含实际差异与修复方向，产物清单仍严格使用 SHA-256 防篡改。
- Remotion 编译失败的 steer 保留原始诊断，并明确以当前 `config_schema/default_props` 为宿主参数契约，同步修正类型声明和属性读取。无进展判断忽略 TypeScript 报错行列号变化，保留文件、错误码、字段与类型差异。本次执行已提交候选后，宿主拒绝用普通回答结束生成；正常问答与必要澄清仍可用。
- Remotion 颜色探针允许最多 2/255 的 RGB 取整误差并比较 alpha 加权覆盖，边缘位置探针优先向内移动；一帧转场核验相邻边界，缺少时序证据仍为 unknown。视觉请求提供图片序号与实际帧号映射，不把无效帧引用自动解释为图片序号。
- Remotion 模型预算由 `server/.env` 的 `IMV_MAX_OUTPUT_TOKENS`（32000）、`IMV_MAX_TOKENS`（200000）、`IMV_MAX_MODEL_CALLS`（32）和 `IMV_MODEL_TIMEOUT_SECONDS`（240）配置；单次输出上限始终生效，仅开启预算时按剩余额度缩小，所有模型角色共用任务预算。任务与渲染超时独立配置为 600 / 180 秒；模型请求遇到连接失败、超时或 429/5xx 时按 `IMV_MODEL_RETRIES`（2）与 `IMV_MODEL_RETRY_DELAY_SECONDS`（2，指数退避、上限 30 秒）等待后重试，重试不重复计入模型调用次数，等待期间取消任务立即停止；默认值与 `.env.example` 同步，修改后重启服务。
