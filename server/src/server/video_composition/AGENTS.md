# 视频合成协作约定

适用于 `server/src/server/video_composition/`，补充根目录与 `server/` 的 `AGENTS.md`。

接口字段、三种模式、回调与操练场契约见本目录 `api.md`；数字人效果、ZOS 转存、并发与执行日志见 `server/README.md`「异步视频合成」「执行日志」；Remotion 资产在合成中的用法见 `docs/remotion-video-composition.md`。改动前先读对应部分。操练场界面见 `client/src/features/video_composition/AGENTS.md`，Remotion 叠加渲染见 `server/src/server/remotion_templates/AGENTS.md`，文案切片见 `server/src/server/segmentation/AGENTS.md`。

## 请求与模式

- 内部模式由服务端按原始 `text`／`copy` 与顶层 `audioUrl` 判定，保存在 `video_compositions.data.request.compositionMode`，调用方不能指定。`copy` 在判定之后才合并成 `text`；`videoUrl` 与背景音乐不参与分流。
- 恢复任务经 `CompositionRequest.from_snapshot` 读取保存的模式；没有模式字段的旧任务按原 URL 规则恢复，不按新规则重判。
- `processRules.videoDuration` 只接受 JSON 有限正数（整数或小数），布尔值与数字字符串返回 422、不创建任务。

## 字幕上限

- 有字幕文案时，ASR 之前按冻结模板算出 `max(1, 最小单行全角字容量 - 2)`，作为唯一的字数上限传给切片的 `max_segment_chars`。IMS 按字号与位置估算；Remotion 按封存源码、默认值合并摆放覆盖，在共享渲染准入内用隔离浏览器测量全角样本，不能把 `fontSize` 当实际尺寸。新槽位 `text_target=subtitle` 替代的 IMS 字幕不参与；旧 `businessTarget` 两者共存取较小容量。
- 测量不缩字、不移动摆放，遵守发布正文长度契约；逐栏填入样本并检查正文/关键词样式、换行、裁切、宿主变换和输出画布的 cover 裁切。它只代表预览时序下的样本容量，最终文案/关键词/时序仍由成片排版预检核验。无数字字号参数的旧字效也实测。双语字效每组为中文、英文两行，容量按内容组数测量（两行资产一组，四行资产两组），`textEn` 保留对应译文。
- 容量随任务保存，恢复时复用；标题、英文译文与操练场已有的切片不重切。放不下一字以 `subtitle_capacity_too_small` 失败；字体、浏览器或字效无法测量以 `subtitle_capacity_unavailable` 失败，不回退隐藏的 IMS 样式，两者均在 template 阶段、ASR 和模型调用之前。失败根因仅进私有诊断，成功、失败和取消均释放名额并清理样本。

## 数字人效果

- 编辑器 `avatar_preview` 在普通任务和操练场冻结模板时移除，不进入合成快照；人脸定位只用真实视频检测结果。

- 开场与圆形头像共用一次人脸检测，结果（定位或明确无脸）保存为任务的 `avatar_opening`，恢复时复用；读取或检测故障仍然报错，不当作无脸。
- 三种同屏布局都只在 standard 模式命中素材时生效；纯素材模式忽略所有数字人效果。
- YuNet 模型随包放在 `models/`、部署随 wheel；增删或升级模型时同步 `server/README.md` 里的来源、版本与许可声明。

## 转场

- 新任务以真实成片总秒数 `T` 计算最小切镜中心间隔 `min(12, max(4, 8 * sqrt(T / 160)))`；关键词首尾各保留近侧，首尾不足间隔时仍保留两次、中间不加，无词时全部切镜也须限频。候选、短镜头与按序筛选细则见 `server/README.md`「模板接口」。
- IMS 与 Remotion 共用选点，候选检查顺序不受任务 ID 或模板效果选择影响；随机效果仍按任务种子分配。受理时固定 `transition_spacing="ordered"`；已有 `true` 快照保留旧动态洗牌，缺少标识的任务保留更早策略，不能让恢复的 IMS 转场与已保存的 Remotion 产物错位。

## 成片转存

- IMS 渲染完成后，成片必须转存到 ZOS 的 `imv/video_composition/{task_id}.mp4`，并截第 3 帧存为同名 `.png`；只给这两个对象设 `public-read`，核对大小与匿名读取后才算成功。转存失败不能把 IMS 临时直链当成功结果返回。
- PNG 地址不进入 GET 或回调；旧的成功任务不补图。ZOS 凭据只读服务端环境，不进入客户端配置头或任务快照。

## 上下蒙版

- 两栏全宽、贯穿全片，覆盖原视频、不改变构图；层级在主体与 IMS 转场之上，全局效果、Remotion 与文字之下。
- 后端用 Pillow 按与客户端相同的四舍五入规则生成整数像素的透明 PNG，在线程池里上传 ZOS；IMS 用实际画布像素宽高，与 SDK 预览的单位（Width/Height=1 表示铺满）不能直接互换。
- 渐变按与客户端相同的 sRGB 通道逐行插值及四舍五入生成，栏内不透明，范围外保持结束色；参数契约见模板模块。
- 非空图片引用优先于颜色和渐变：读取任务快照引用的已裁剪 PNG，整图拉伸到单栏像素宽高、按 Alpha 叠加，不再次裁剪或填底色。停用和零像素栏跳过读取；模板换图不改变冻结任务的引用。上传校验和长期存储规则见模板模块。
- 任务保存 `frame_mask_url`，恢复时复用；缺图或上传失败时不提交没有蒙版的成片。

## Remotion 叠加

- 合成侧解析 Remotion 资产业务参数的代码只放在 `remotion_content.py`，读取已发布资产的契约，不修改 Agent 生成或发布流程；固定时长资产的时钟映射只在 `remotion_timing.py` / `remotion-timing.mjs`。
- 新模板用 `text_target`／`transition_boundary` 槽位，旧的 `editor.remotion.businessTarget` 保留兼容，两者都有时新字段优先。
- 渲染前在共享渲染名额内做与叠加渲染相同的排版预检，放不下以 `remotion_text_too_long` 失败；排版调整只改这次渲染的摆放，不改保存的摆放。整段透明 WebM 从 0 秒加入 IMS 最后一条视频轨，实际输出的 IMS 文字在它上层；上传结果持久化供恢复，源码不进执行日志。
- 英文标题和字幕固定单行：普通双栏字效作用在英文上只用上栏（英文标题的换行按空格合并、英文字幕不按句分组），另缩到能单行显示的最大字号，避免中英双语出现四行；启用英文字幕时英文字幕向下、中文字幕向上展开，两条字幕不互相压住。
- 双语字效（声明 `textEn` 的资产）由同一个字效显示两种语言，绑定中文标题或字幕。两行资产每切片一个实例；四行资产按同句最多两个切片分组，原文、译文分别用换行连接，受各自长度上限及行起点上限约束。组内同时出现，行起点如 `[0, 0, 30, 30]`；不做单行退化也不朝另一语言展开。标题四行资产保留两组分行，中英行数必须一致。中文关键词照常注入（英文关键词不注入）；英文独立字效及 IMS 文字不再渲染，缺少译文按内容错误终止。
- Remotion 的 Agent、组合器、校验、发布与编辑预览以 upstream/dev 为准；合成侧只保留 Chrome 多进程配置和合成所需的渲染适配，后续修改等真实测试反馈后再做。
- 合成侧 Remotion 失败时，在清理前把 `worker.log` 与完整异常链私有保存（目录权限 0700）。公开的七模块日志、GET 与回调只保留安全摘要，七模块日志另记根因的异常类型、代码位置与错误码，不含其文本；诊断写入失败不覆盖原任务错误。

## 执行日志

- 日志的归类、引用、脱敏、写入与容量清理都只在 `execution_log.py`。新任务写到 `server/.log/video_composition/<task_id>/` 的七个模块文件；不再创建、读写或迁移数据库日志表，已有的表不自动删除。
- 业务事务提交后才把日志快照入队，由单个后台线程顺序写入；队列满时提交线程等待，不丢记录。凭证一律脱敏。

## 操练场

- `POST /api/v1/video-compositions/playground` 读取随包的 `playground_samples/{mode}.json`，缺失返回 409。样本只含可复用的准备结果，不放回调、凭据或渲染结果；受理时重新固定模板与 Remotion 快照、跳过内容准备，之后与普通合成共用同一流程，成品存 ZOS `imv/playground/`。
- 共享的 ZOS S3 客户端遵循 `HTTP(S)_PROXY`/`NO_PROXY`，大文件串行分片上传，保持 TLS 校验。
