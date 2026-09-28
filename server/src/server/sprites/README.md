# Remotion Sprite 与合成总线接入

本模块把 Remotion Agent 的**成功验收版本**发布为不可变 Sprite，并把 Sprite 绑定到现有云端 `style_id`。协议为 [sprite.proto](../../../../proto/imv/sprite/v1/sprite.proto)；原有 `imv.template.v1` 协议没有增加 Sprite 字段。当前合成总线尚未调用本模块；下文是总线接入时应实现的接口和时间规则。

## 责任边界

| 一方 | 负责的数据与动作 |
| --- | --- |
| 运营 / Remotion Agent | 选文字、字幕、滤镜叠加、视频动效或转场叠加类型；用示例内容制作外观和动画，完成逐帧验收；点击“发布 Sprite”。样例文字与样例时间仅用于预览。 |
| 模板编辑 | 在同一云端 `style_id` 下，独立保存 IMS `tracks` 和 `StyleSpriteBindings.placements`。一个样式可同时包含 IMS 与 Remotion 效果；轨道重叠由运营决定。 |
| 合成总线 | 取得真实标题、字幕短句、切片关键词、素材片段边界及最终时间；把这些值填入 `SpriteRenderInput`，上传透明 WebM，并在 IMS Timeline 添加 `VideoTrack`。 |

Agent 只绘制透明叠加内容，不能读取业务原视频。滤镜 Sprite 是可调亮度、对比度、饱和度等参数的**视觉叠加层**；它不会逐像素修改底片。需要验证的是 IMS 最终合成画面。气泡字暂未提供生成或绑定入口，协议仅保留气泡资源 URL、宽、高字段，当前始终为空或零。当前字体由服务端管理，上传 TTF 尚未实现。

## 发布与读取

1. Agent 首次创建作品时固定 `sprite_kind`：`text`、`subtitle`、`filter_overlay`、`video_overlay` 或 `transition_overlay`。文本版本把实际文字留给 `0_text`；字幕版本另声明 `keywords: string[]`，用包含重复关键词的样例完成高亮验收。视觉版本把样式标量放在 `visual_parameters`，运动区间放在 `visual_motion`。
2. 只有源码、隔离编译、参数变化、透明帧、运动、媒体规格和视觉 Judge 全部通过的成功版本可发布。`POST /api/sprites/publish` 使用 `PublishSpriteRequest` 二进制消息；服务端校验版本类型、产物摘要，复制源码和预览到独立发布记录。重复发布同一版本与参数返回原 Sprite。删除源聊天不改变已发布 Sprite。
3. `GET /api/sprites` 返回不含源码的目录。模板编辑通过 `GET/POST /api/sprites/styles/{style_id}` 读取或替换独立的 `StyleSpriteBindings`，提交 `expected_revision` 防止覆盖他人编辑。`style_id` 是已有云端模板 UUID；本地模板暂不绑定 Sprite。IMS 模板及 Sprite 绑定分别保存，保存时先写 IMS 模板，再写 Sprite 绑定；后一步失败时草稿仍可重试。
4. 总线按 `style_id` 读取原有 `/template/{style_id}` 及 `/api/sprites/styles/{style_id}`。没有 Sprite 记录时，后者返回空 placements、revision 0；继续原 IMS 路径即可。

所有 Sprite 写入接口和绑定读取接口使用 `application/x-protobuf`。`PublishedSprite.tsx_code` 不向目录或模板编辑返回。发布预览 `GET /api/sprites/{sprite_id}/preview.mp4` 是固定示例；它不是供 IMS 合成的透明媒体。

模板编辑的同屏预览通过 `GET /api/sprites/{sprite_id}/interactive` 加载透明交互层。服务端从不可变发布源码在现有 bwrap 环境内构建并按源码、参数契约与预览宿主指纹缓存 JS；隔离 iframe 运行编译后的代码，字体走 `/{sprite_id}/fonts/{weight}`，原始 TSX 不通过预览接口返回。浏览器可以读取编译后的实现，不能把预览包当成保密源码。客户端 IMS SDK 仍是唯一播放时钟，逐帧定位 Sprite；运营修改样式或示例文案时只更新交互层，不重新提交 IMS Timeline 或调用 ZOS。示例字幕显示 2 秒，片段入场、出场与转场使用示例边界；实际字幕短句和素材边界只由总线决定。预览使用与最终轨道相同的 `Cover` 画布裁切，但 IMS 原生字幕与 Sprite 的最终层级仍以云端成片为准。

## 总线时间与业务字段

总线在**最终合成时间轴**上以秒计算业务窗口和 IMS 放置时间。`SpritePlacement.start_mode=seconds` 用秒数，`percent` 用最终成片时长百分比；`duration` 缺省时持续到成片末尾。总线先求各窗口的交集，再按发布 Sprite 的 FPS 将交集时长量化为帧；不足一帧则跳过。新生成的 Remotion Sprite 固定 30 FPS，IMS 成片帧率可独立设置。`SpriteRenderInput` 不包含最终成片的绝对帧号：`effect_total_frames` 是完整逻辑效果帧数，`effect_offset_frames` 是本段相对效果起点的零基偏移，`render_frame_count` 是本段输出帧数，均为必填且总和不能越界。一个请求最长 30 秒；长效果按相邻偏移分段，每段使用相同的效果总帧数，避免在分段处重新播放入场或出场。总线另行保存每段的 IMS `TimelineIn/TimelineOut` 秒数。

| 目标 | 总线取值与窗口 |
| --- | --- |
| `TITLE` | `text = CompositionRequest.title`；窗口为绑定轨道与成片范围的交集。 |
| `SUBTITLE` | 使用原切片的 `subtitle_parts`；旧数据缺失时使用原切片。`text` 为当前短句清理标点后的文字，`keywords` 从该短句所属原切片的非空 `keyword` 构成；服务端计算**当前短句内所有字面出现**的字符区间后交给 Sprite 着色。窗口为短句 `[start_time,end_time)` 与绑定轨道的交集。 |
| `FILTER` / `VIDEO_EFFECT` | 窗口为绑定轨道与成片范围的交集；不提供 `text`。这种全画面叠加可以盖住多个源素材片段。 |
| `VIDEO_ENTER` / `VIDEO_EXIT` | 对总线已经确定的每个源片段边界建立窗口；入场从片段 `TimelineIn` 开始，出场在片段 `TimelineOut` 结束，时长由绑定的必填 `duration` 确定并裁到该片段。 |
| `TRANSITION` | 对相邻源片段的每个实际边界建立窗口，遵循现有 IMS 转场的片段时长约束。绑定的 `start` 不定位素材边界；编辑器添加时将必填 `duration` 初始化为 1 秒。 |

例如绑定字幕轨道 `[0s,30s)`，真实短句是 `[4.2s,5.0s)`：30 FPS 的 Sprite 请求为 `effect_total_frames=24, effect_offset_frames=0, render_frame_count=24`，输出视频本地时间从 0 开始，IMS `TimelineIn=4.2`、`TimelineOut=5.0`。若轨道是 `[4.5s,30s)`，交集是 `[4.5s,5.0s)`，请求 15 帧并由 IMS 放在 4.5～5.0 秒。运营预览中的 5 秒不会替代这两个真实时间。跨 30 秒分段时，比如 40 秒效果，第一段传 `1200/0/900`，第二段传 `1200/900/300`，总线分别计算其 IMS 放置时间。

字幕的入/出动画合计帧数若**大于**业务窗口帧数，服务端使用发布时选出的可见帧冻结画面，直接显示文字与高亮。业务文字最多取前 2000 字符，之后直接截断；本期不做自动缩放，实际框内可见范围仍由 Sprite 样式决定。标题与其他视觉 Sprite 仍按自己的帧驱动代码在传入窗口中播放。

`SpriteRenderInput.output` 必须填写发布 Sprite 的宽、高和 FPS。总线最后可以用 IMS `Cover` 把这段视频缩放到成片画布；画幅不同时可能裁切边缘。`resolved_style` 只发送绑定中经校验的可编辑标量覆盖，不能放入文字、关键词、时间、只读字体或任意源码。绑定顺序 `order` 表示 Sprite 视频轨道从下到上；IMS 原生轨道与 Sprite 轨道可以同时存在。总线在任务开始时读取并固定模板及 Sprite 绑定版本，渲染中不重新读取运营编辑结果；任一必需 Sprite 渲染、上传或 IMS 合成失败应使任务失败并保留可重试信息。

## 透明媒体与 IMS Timeline

`POST /api/sprites/render` 接收二进制 `SpriteRenderInput`，返回 `video/webm`，服务端在无网络、无密钥的 bwrap 进程中渲染，核对 VP9、画幅、FPS、帧数与实际解码 Alpha。该接口只返回临时媒体，**不会上传或提交 IMS**。总线负责把响应上传到 IMS 可读取的 HTTPS 地址，然后为每个区间增加一个独立的 `VideoTrack`，示例片段：

```json
{"VideoTrackClips":[{"Type":"Video","MediaURL":"https://<public-object>/sprite.webm","TimelineIn":4.2,"TimelineOut":5.0,"In":0,"Out":0.8,"Width":1080,"Height":1920,"AdaptMode":"Cover"}]}
```

新增视频轨道排在当前底片 `VideoTrack` 后面；多个 Sprite 按 `order` 排列。上传对象须在 IMS 抓取期间可读取，合成完成前不要删除。当前 IMS 原生 `SubtitleTrack` 的可见层级可能高于视频叠加轨；若同一画面还使用 IMS 字幕，应在成片检查最终叠放效果。2026-09-27 的上海 IMS 实测将 320×180、2 秒 VP9 Alpha WebM 叠在底片上，任务 `00f41d41f4ea46a5af4747bbc15b53e8` 成功并保留透明混合；再加原生字幕的任务 `11b35e76849340109069e984f17cfdd3` 成功，原生字幕显示在透明视频上方。本机原始测试脚本和产物位于 `/tmp/imv-alpha-validation/`，该验证尚不代表新 Sprite 的完整总线联调。

## 当前限制与验证

- 合成总线代码未改，需按上表实现业务字段注入、相邻片段边界、透明媒体上传到 ZOS 或其他 IMS 可读取的公网存储、Timeline 排层和失败重试。编辑器同屏预览使用本地浏览器交互层，不代表 IMS 云端成片已经接入 Sprite。
- 发布预览复制到服务端本地数据目录，源代码记录在 MySQL。多实例部署需要共享该预览目录或另建统一对象存储；目前按单实例部署。
- Sprite 渲染依赖现有 Linux bwrap/Chromium/FFmpeg 环境；Windows/macOS 内置服务还没有生成验证。每次渲染至多 30 秒、1800 帧，单个逻辑效果最多 216000 帧，长片须由总线分段。
- 仓库内执行 `buf lint && buf breaking --against '.git#branch=HEAD' && buf generate`、`server/.venv/bin/python -m pytest -q server/tests/test_sprites.py`、客户端冻结安装和 `bun run build`。实际云端 IMS 验证需在总线接入后以真实媒体重新执行。
