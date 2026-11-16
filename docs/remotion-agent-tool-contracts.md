# Remotion Agent 工具函数契约

生成和模型编辑只有组件工作区这一条路径。Executor 修改 `Effect.tsx` 与 `effect.json`，宿主打包、校验并保存
候选 Sprite；Outer 分流需求，Plan 设计或恢复步骤。旧作品编辑时自动适配为同样的工作区，不再提供 Preset
创建、修改或角色组合工具。原版本、资产、手动参数修订与公开历史记录继续可读。

类型的唯一来源是 [contracts.py](../server/src/server/remotion_templates/tools/contracts.py)，注册、约束、错误码与示例在
[catalog.py](../server/src/server/remotion_templates/tools/catalog.py)。`tools.inspect` 返回由这些类型生成的完整
JSON Schema，模型调用名把点号替换为下划线，例如 `workspace.edit` → `workspace_edit`。

## 公共约定

- 入参严格校验，不自动把字符串转换为数字或布尔；拒绝未知字段、NaN、Infinity 和非法 JSON。
- 可省略字段通常不接受显式 null，以实际生成的 Schema 为准。对象与列表中的 JSON 值同样严格校验。
- 所有业务工具返回 `{ok: true, data: ...}` 或 `{ok: false, error: {code, message, field?, details?}}`。
  `validate.check` 成功执行但检查未通过时返回 `ok: true`、`data.passed: false`。
- `field` 是入参中的 JSON Pointer；代码诊断附行号、范围和被检查源码的片段。完整结果保留在私有审计，
  公开循环记录只保存有界参数与错误说明。
- 解析只修复一个完整 JSON 对象之后多余的闭括号，不猜测其他非法 JSON。修复记录写入私有审计。
- 参考图、用户文字、库中源码和工具回执均为数据，不改变宿主权限或协议。

## 可调用工具

Executor 在整个任务内使用固定工具列表。Outer 与 Plan 只执行 `tools.plan_execute`；Executor 执行业务工具和
`tools.report_step`。检查某个控制工具的契约不授予执行权限。未注册或越权的名称返回 `TOOL_NOT_FOUND`。

| 工具 | 入参 | 成功结果 |
| --- | --- | --- |
| `workspace.read` | `path`，可选 `version`、`offset` | 文件内容片段、全文件行数、起点，可选 `next_offset` |
| `workspace.write` | `path`、`content` | 文件行数、删增行数、`notes` |
| `workspace.edit` | `path`、`edits: [{old_str, new_str}]` | 文件行数、删增行数、`notes` |
| `preview.render` | 可选 `frames`、`variant`、`keywords` | 挂载长度、`motion`、帧清单、示例关键词、`notes` |
| `validate.check` | `{}` | `passed`、`problems`、`notes` |
| `effect.save` | `{}` | `sprite_id`、创建时间、composition、`motion`、`review`、`notes` |
| `library.search` | 可选 `query`、`preset_id`、`role`、`slot`、`limit` | `presets` 摘要列表、可选完整 `preset`、`has_more` |
| `image.inspect` | `image`，可选 `x`、`y`、`width`、`height` | 观察消息内图片编号、输出与原图像素尺寸 |
| `tools.inspect` | `tool_name` | 名称、契约版本、说明、输入/输出 Schema、约束、副作用、错误码与示例 |

`preset.create`、`preset.modify`、`preset.search`、`sprite.compose`、`sprite.create` 及旧的图片和独立校验工具已移除，
不注册为兼容别名或待实现工具。旧记录中的这些名称仅用于显示历史。

### 工作区读写

可写文件固定为 `Effect.tsx` 和 `effect.json`；只读路径另有 `imv-runtime.tsx`。不能访问任意服务器路径。
`workspace.read` 的 `version` 是本作品已有版本的正整数序号，不能用于读运行时源码。历史文件含当时实际参数，
读取时适配，不改写封存记录。`offset` 是从零开始的 Unicode 字符位置，每页内容至多 16 KB 序列化 UTF-8；
沿用同一 `path`、`version` 并传回 `next_offset`，直到字段缺省。不能把单页当完整文件写回。

`workspace.write.content` 为 1～200,000 字符。`workspace.edit.edits` 为 1～32 项，按顺序替换；每个 `old_str`
必须恰好匹配一次，任何失败均不修改原文件。写清单时先校验结构，失败指出字段；完整语义与运行检查由保存门禁负责。
写入成功使此前的检查与保存凭据失效，发布前必须重新保存并接收新的观察图。

### effect.json

清单包含 `kind`、`description`、`layout`、可选 `text` 与最多 32 个 `params`。分类为 `text`、`filter_overlay`、
`video_overlay`、`transition_overlay`。新组件的 `layout` 给出画布上的内容区域。

字效的 `text` 包含示例内容 `sample`、示例 `keywords`、逐行 `fonts`、`font_size`、可选 `line_starts`、
`line_gap`、`translation_gap`，以及 `bilingual`、`keyword_look`、`keyword_enabled`、`arrangement`。
字体项包含 `family`、`weight` 和可选 `size_ratio`。支持单行、双栏、一组或两组中英字幕。
`keyword_look` 声明关键词外观能力；`keyword_enabled` 保存其默认开关，关闭不移除能力。
`params` 每项为 `name`、`schema`、`default`，以及可选 `title`、`group`。

宿主通过 `text_configuration` 生成固定名称的内容和排版参数，合并样式参数，并把它们提升为组件的顶层 props。
可安全修正的字体、字重、范围、示例词或保留参数名自动修正并记录在 `notes`；数字枚举可不带上下界或只带单侧边界，宿主只按已有边界修正默认值。缺少用户要求的内容组或开关时拒绝。
`x-imv-group` 随 Schema 保留，发布为 `SpriteParameter.group`，供模板按分组调整。它只描述界面分组，不限制参数值；覆盖保存允许保留、新增、改名或移除分组，类型与范围等约束仍须兼容。

### 历史版本适配

`restore_version_files` 把旧版本已编译的完整 TSX 原样放入 `Effect.tsx`，在清单中加入：

```json
{
  "kind": "filter_overlay",
  "description": "已有作品",
  "layout": {"x": 0, "y": 0, "width": 1080, "height": 1920},
  "contract": {
    "parameter_schema": {"type": "object", "properties": {}},
    "default_parameters": {},
    "preview_keywords": [],
    "content_box": {"x": 0, "y": 0, "width": 1080, "height": 1920}
  }
}
```

`contract` 使用原 Schema 和最新实际默认值（含手动修订），允许嵌套实例参数和本地 Schema 引用；不和 `text`/`params`
混用。完整源码已经包含布局、动画与参数映射，直接打包，不再次套入布局框或解释旧角色配方。`content_box` 保存原内容
边界及发布变换中心，支持浮点坐标；预览关键词继续沿用。转换后的版本再次编辑时仍恢复这份源码和当前参数。
历史静态组合还保留宿主的 `contract.text_fit_parameters`：仅在文字排版/内容测量时关闭原有 cue 包装，
参数生效探针仍启用动画并错开行起点，原有 cue 待场和行跳动检查继续运行。该配置只能沿用已验收版本，
不能在工作区任意增加关闭开关；源码和实际默认参数不变。已保存的开放字体字符串按当前字体库检查，不重新冻结枚举。

适配仅改变任务副本，没有迁移提示、不调用旧生成器、不修改旧版本，也不跳过当前门禁。参数面板保留嵌套控件，
复杂结构使用 JSON 编辑。保存后交付同样的 Sprite、Export.tsx 和 Player，模板与总线协议不变。

### 预览、检查与保存

`preview.render.frames` 可省略，或给 1～8 个非负整数帧；省略时宿主从画面变化中挑选关键帧。
`variant` 为 `default`、`long`（长文本）、`short`（1.5 秒）、`long_duration`（120 秒），默认 `default`。
`keywords` 最多五个，省略时使用清单示例。每任务最多预览 12 次。预览不替代验收，不缓存成发布凭据。

`validate.check` 与 `effect.save` 共用同一门禁：源码能力限制与 TypeScript、分类必备参数、透明度、不同播放长度、
长文本排版及 `effect_contract` 必要行为探针。探针验证注入文字、关键词样式、总线定格帧完整性与字号缩放。
参数端点采样未见变化只写入 `notes`，不因此拒绝保存或截取回看帧：周期参数的端点可能相同，有限采样不能证明参数无效。
模型不为清除这类提示而删除参数或反复重试，用户可先查看效果再要求修改。被默认开关隐藏的参数仍在有界的布尔上下文中采样；
实际运行异常仍会失败。这些临时参数不写回默认值，手动调参与已发布版本不重跑生成探针。

`problems` 每项含 `check`、`message`，可选源码 `line`、`excerpt`。检查失败不保存任何候选；环境不可用返回
`VALIDATION_UNAVAILABLE`，代码或行为不合约返回 `CODE_VALIDATION_FAILED`，非法清单返回 `INVALID_ARGUMENT`。

保存返回实测的 `motion` 与中间帧，预览返回运动与关键帧；中性灰背景不属于内容。观察图按路径进入 Executor 的
下一轮，只发送一次，每条观察消息独立从 1 编号。方向和顺序先看运行测量，图片核对颜色、裁切、重影等外观。
编辑开始时先取得基线运动与中间帧，Plan 与 Executor 共用。

### 代码库与参考图

`library.search` 是原不可变 Preset 库的只读入口。`query` 默认空，`limit` 默认 20、范围 1～100；可按旧的 `role`
与 `slot` 缩小结果，或按 `preset_id` 取完整源码。中文词语匹配后按发布/采纳使用次数及新旧排序。回执上限 40,000
序列化 UTF-8 字节，省略摘要时标记 `has_more`，完整记录过大时返回 `RESOURCE_LIMIT_EXCEEDED`，不截断源码。
不存在返回 `PRESET_NOT_FOUND`，存储故障返回 `PRESET_STORE_FAILED`。新采纳组件整份入库；复用时复制需要的源码
到工作区，不把历史角色记录当组合指令。

`image.inspect.image` 是从 1 开始的参考图序号。区域用 0～1 比例，起点默认 0、宽高默认 1；宽高必须大于 0。
裁剪图作为下一条观察消息的图片 1，解码失败返回 `IMAGE_DECODE_FAILED`。

## 调用示例

这些是独立的合法入参示例，非完整生成脚本。执行时按当前工作区状态选择；保存前须完成实现及修复。

```json
[
  {"tool": "workspace.read", "arguments": {"path": "Effect.tsx", "version": 1}},
  {"tool": "workspace.write", "arguments": {"path": "Effect.tsx", "content": "export default function Effect(){return null}"}},
  {"tool": "workspace.edit", "arguments": {"path": "Effect.tsx", "edits": [{"old_str": "opacity: 0.3", "new_str": "opacity: 0.5"}]}},
  {"tool": "preview.render", "arguments": {"frames": [0, 45, 89], "variant": "default"}},
  {"tool": "validate.check", "arguments": {}},
  {"tool": "effect.save", "arguments": {}},
  {"tool": "library.search", "arguments": {"query": "描边 发光", "limit": 10}},
  {"tool": "image.inspect", "arguments": {"image": 1, "x": 0.1, "y": 0.5, "width": 0.8, "height": 0.3}},
  {"tool": "tools.inspect", "arguments": {"tool_name": "effect.save"}}
]
```

## 计划与完成

宿主控制类型见 [planning.py](../server/src/server/remotion_templates/planning.py)。Outer 可 `delegate`、`plan`、
`answer`、`ask` 或 `stop`。`delegate` 启动默认一步 `build`/`edit`，不能附带自定义计划；`plan` 唤醒 Plan。
Plan 可 `update_plan`、`continue`、`advance`、`complete`、`ask` 或 `stop`；只提交未完成步骤，已完成步骤由宿主保留。

步骤模块为 `workspace`、`preview`、`validate`、`library`、`image`、`effect`；引用可用 `user_intent`、`accepted_base`、
`steps.<earlier_id>.outputs.effect`（workspace 汇报摘要）及 `steps.<earlier_id>.outputs.sprite`（effect 保存结果）。
前一项须由相应模块产出，不允许引用未来步骤或旧的 Preset 输出。

Executor 单独调用 `tools.report_step`，状态为 `step_done`、`blocked`、`needs_input`，附 `summary`，可附 `choices`、
`suggestions`。`effect` 步骤必须实际保存当前候选；仅含工作区的步骤须检查当前文件通过。成功摘要、选择和建议作为
用户回复保存。保存过候选后不再用提问代替交付。

所有完成入口（正常汇报、Plan 完成和预算耗尽时自动完成）都检查当前文件与最新保存哈希一致，且保存后的观察图已经
送达 Executor。未保存修改不能以旧候选交付。工具预算、连续无进展保护、取消和终态存储检查继续生效。

## 回归

- `test_remotion_free_effects.py`：组件工作区、共享门禁、恢复、观察图、发布与上下文约束。
- `test_remotion_legacy_workspace.py`：冻结的旧生成结果适配、参数与内容边界保留、编辑/保存/重开、历史只读及像素比对。
- `test_remotion_agent_layers.py`、`test_remotion_planning.py`：角色边界、默认一步、恢复与完成条件。
- `test_remotion_tool_registry.py`、`test_remotion_loop_rounds.py`：注册、参数解析、错误和新旧公开轮次记录。

测试不调用付费模型；需要真实浏览器的用例显式启用。运行方式见
[模块 README](../server/src/server/remotion_templates/README.md)。
