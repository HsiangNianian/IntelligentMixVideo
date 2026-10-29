# 云端模板协作约定

适用于 `server/src/server/template/` 与仓库根目录的 `proto/`、Buf 配置，补充根目录与 `server/` 的 `AGENTS.md`。

模板接口、字段范围、同屏与数字人效果配置、转场规则的完整说明见 `server/README.md`「模板接口」，改动前先读；模板编辑界面见 `client/src/features/templates/AGENTS.md`。

## 模块边界

- 路由、配置校验、数据库存储与效果目录都放在本模块（与示例目录 `sub_api/` 平级）：`schema.py` 做业务校验，`router.py` 在生成的 Protobuf 消息与业务模型之间转换。
- 云端模板库是共享的：没有登录、用户隔离或旧数据迁移，配置存 MySQL（SQLAlchemy + PyMySQL）。
- 客户端只能新建和编辑云端模板。不要恢复本地模板入口、「本地模板保存路径」设置或 Tauri `local_templates` 命令；旧版留在磁盘上的 `data/template/templates.json` 与 `$client.template_path` 不读取、不删除、不迁移。

## 保存契约

- `tracks` 必填，对象参数只放在 `tracks[].editor`；拒绝缺少 `tracks`、`tracks: null` 或带顶层 `editor` 的模板，不提供旧格式转换。云端保存、详情读取与合成按完整规则校验；前端保存前只先检查对象时间、文字槽位、同屏与数字人配置等（`client/src/features/templates/api.ts` 的 `saveTemplate`）。
- 创建与更新都必须恰好有中文标题和中文字幕各一个，缺少即 422 且不写入。
- 名称去除首尾空白后不能为空，重名由 MySQL 唯一约束拒绝（409）；数值范围、效果目录、动画互斥不合法返回 422。
- ID、UTC 时间与效果参数快照由服务端生成，不接受客户端提交的渲染参数。
- POST 无 ID 才创建（201），有 ID 完整更新（200）；不存在的 ID 返回 404，不做 upsert。没有独立的 Connect 路由。
- 成功响应默认经 `MessageToDict` 转成 JSON；只有请求带单一 `Accept: application/x-protobuf` 才返回二进制，成功响应都带 `Vary: Accept`；删除返回 204 空响应。客户端按 `Content-Type` 解析，JSON 用 `ignoreUnknownFields: true` 容忍新增字段。
- 列表逐条校验：无效记录带 `validation_error`，其详情返回 409，不影响同库的有效模板。列表消息与转场字段要求客户端、服务端一起升级。
- `split_screen`、`avatar_effects`、`frame_mask` 等后加的配置缺省为关闭；旧客户端省略这些字段重存时保留已有配置。
- 省略配置必须保留旧客户端的已有蒙版，因此删除不能沿用省略语义。保存请求中空的 `frame_mask` 消息表示明确移除，路由转换为显式 `None`；只有省略字段才保留已有配置，缺少单栏的非空配置仍按非法输入拒绝。详情省略被移除的配置。
- 模板级 `frame_mask` 保存上下蒙版：`top`/`bottom` 各有 `enabled`、`height`（画布高度百分比 0～50）与 `color`（`#RRGGBB`）。可选 `gradient` 含必填的 enabled、endColor（`#RRGGBB`）、direction（up/down）与 range（整数 1～100），color 作为起始色；缺省为纯色，旧客户端按栏省略渐变时保留已有值，显式 false 关闭但保留参数。它不是 `tracks` 里的对象，使用它需要客户端与服务端一起升级。

## 字体与效果目录

- 标题、字幕字体只能是 `fonts.json` 十一项的 `value`（前端与 Python 共用这份目录），保存与合成用 `value`、预览用 `preview`；未知字体、空值与 SDK 专用标识不能保存；缺少字段的旧模板默认 `Alibaba PuHuiTi`。
- 效果目录固定为 SDK 5.2.2，服务端维护同版本白名单，不提供 `/template/effects`；升级 SDK 时同步核对前端目录与白名单。`proto/` 的生成步骤见根目录 `AGENTS.md`「生成文件」。

## Remotion 片段

- `target=remotion` 的轨道用 `sprite_id` 引用已发布 Sprite，`id` 在模板内唯一（同一 Sprite 可摆放多次）；`start_mode` 只能是 `seconds`，`duration` 为 `null`（以发布预览长度为默认值）或正秒数。其他对象不能带 `sprite_id`，IMS 对象不能设置 `editor.remotion`，没有覆盖时返回 `{}`。
- `editor.remotion.parameters` 每项恰好一个 `stringValue`/`numberValue`/`boolValue`/`stringListValue`；`transform` 只含内容中心 `x`/`y`、`scale`（0.25～4）、`rotation`（-180～180）、`opacity`（0～1），滤镜与转场只能设 `opacity`。
- 覆盖必须指向可编辑参数，类型、范围、枚举与合并结果都符合发布 Schema，否则 422 并指明片段。没有 `content_box` 的旧发布只接受宿主变换；例外是字效的字体参数（`fontFamily`、双栏的 `line1FontFamily`/`line2FontFamily`），按实时字体库而不是发布时冻结的枚举校验，所以仍可替换（视频合成复核合并配置时同样如此）。其他未采样参数以及其他分类的同名参数保持只读。
- 所有片段引用的 Sprite 必须已发布且画布宽、高、帧率一致；片段与 IMS 对象共用 100 条上限，不产生 `effect_ids`，按 `tracks` 顺序叠放（先者在下）。
- 字效必须设 `text_target=title/subtitle`，每个文字对象最多一个，替代对应 IMS 文字、时间跟随该文字；`text`/`keywords` 由文字对象提供，摆放不能覆盖，绑定时清除已有覆盖。
- 转场片段必须设 `transition_boundary`，与 IMS 转场共用候选列表和选择方式；预览与合成都围绕素材连接位置居中计算，保存保留原有时长。
- 视频特效（含未指定分类的旧资产）与 IMS VFX 合计最多一个，滤镜可多个；分类不符、缺少或多余的绑定字段返回 422。
- 兼容旧字效轨道：没有 `text_target` 但带 `editor.remotion.businessTarget=title/subtitle` 时照常保存，替换资产时两套文字共存，仍校验发布资产、画布、参数并禁止覆盖正文与关键词。`businessTarget` 只属于字效，非字效资产带它返回 422；新的 `text_target` 保持 IMS 替代规则。
