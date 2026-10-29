# 操练场协作约定

适用于 `client/src/features/video_composition/`，补充根目录与 `client/` 的 `AGENTS.md`。

界面行为见 `client/README.md`「工作室前端」的操练场一段；接口与服务端行为见 `server/src/server/video_composition/AGENTS.md`。

- `PlaygroundWorkspace` 分三栏：左栏选样本与模板（可搜索的单选列表，无效模板只显示警告、不可选，选中后显示完整 styleId 并可复制），中间按 9:16 播放最近成片，右栏按真实任务阶段显示步骤，失败原因写在失败的步骤下（`template` 阶段归入「生成画面与字效」）。窄窗口依次变为两栏、单栏。
- 当前模板、任务与最近成片按服务保存：桌面用本地设置（内置服务用固定标识），浏览器用 localStorage。
- 每 15 秒或手动查询进度；查询失败与任务失败分开显示；创建任务不自动重试，查询断线不重发合成。
- 任务返回 404 时可清除失效任务；本地记录损坏时经用户确认，只重置当前服务的操练场数据。
