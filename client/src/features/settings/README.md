# 客户端模块设置

普通与 Debug 两种模式都展示「Remotion Agent」「上海 IMS 官方 SDK」，配置保存在当前客户端，随实际调用使用。模块入口声明 `scope: "client"`；普通模式请求 `/api/settings/plugins?client_only=true`，Debug 请求完整目录，展示 Remotion、视频合成/IMS、切片、ASR 和启动端口的完整配置（数据库除外）。未声明 scope 的模块仍只在 Debug 展示。通用面板不受目录失败影响，但动态表单需要后端返回 Schema。

`IMV_DEBUG=true` 沿用桌面打包开关；Vite 开发模式不自动开启。普通模式仍不携带本地切片配置，保存时保留当前 Schema 未展示的 Debug 字段。所有模块的字段与代码默认值由后端模型生成，目录不返回服务端 `.env` 实际值。

`SettingsDialog.tsx` 承载现有设置弹窗，`PluginSettings.tsx` 按后端目录生成纵向模块导航，默认显示通用环境与可编辑的后端地址。地址复用本地设置的保留键 `$client.api_url`，不需要服务端插件；保存后新请求使用新地址，重新打开设置读取新服务目录，已有会话连接需重启客户端后切换。桌面启动优先使用保存的地址，未保存时沿用内置服务或 `VITE_API_URL` 默认地址。切换模块隐藏表单并保留草稿；关闭后丢弃未保存输入，重新打开读取最新目录和本地值。通用与模块表单统一使用底部固定「取消 / 保存」操作栏；保存只提交当前页并保留弹窗，取消关闭弹窗并丢弃未保存草稿，不撤销此前已保存的值。模块列表只来自 `GET /api/settings/plugins`，不会从本地存储的键生成。目录依赖后端服务，读取失败显示提示。

`schema.ts` 在渲染前检查扁平字段子集，在保存前校验并规范化当前字段。支持字符串（含密码、JavaScript Unicode 正则 `pattern`、`minLength/maxLength`）、数字/整数（包含及排除上下界）、布尔值、默认值和必填。空的可选数字从保存对象省略，必填数字为空、小数整数、非有限值或越界均拒绝保存；布尔 `false` 有效。对象、数组、联合类型、枚举、引用以及其他尚未支持的约束明确报错，该模块不生成可保存的表单。这里不是完整 JSON Schema 验证器，后端业务入口仍须校验。

- 桌面通过 `local_settings` 命令保存到应用数据目录 `data/settings/settings.json`，按插件 ID 分组。读取和保存全程持有目录文件锁；竞争时提示重试，写入临时文件并刷盘后替换正式文件，失败不先删除旧配置。API Key 目前和普通值一起明文保存；没有接系统凭据库。
- 浏览器预览仅保存在页面进程内存，切换设置页可恢复，刷新页面丢失，不写 localStorage。
- `../segmentation/api.ts` 的 `requestSegmentation({script, asr_result})` 读取已保存的切片配置，随 `POST /segmentations` 的 `config` 一起提交。编辑但未保存的值不进入请求。省略本地配置时发送原有请求。
- 当前没有独立切片业务页面，请求函数通过自动联调用例和下面的开发命令使用；Debug 内置后端重启后，视频合成也使用已保存的切片配置；普通模式不变。
- 设置目录和切片请求复用共享运行时 API 地址，内置后端启动后使用实际回环端口。

## 增加或移除插件

1. 在模块自己的 `settings.py` 定义客户端可编辑模型，字段只维护一份。
2. 在该模块目录新增 `settings_plugin.py`，导出 `SETTINGS_PLUGIN = {"id": "稳定唯一 ID", "name": "显示名称", "schema": ClientSettings.model_json_schema()}`。不要实例化 Settings 或导出实际配置值；入口及父包导入不能连接外部服务、启动任务或依赖已填写的凭据。
   如需 Debug 展示完整服务端配置并在内置服务启动时加载，在同一字典添加 `"settings": Settings`（模型类）。公共层用字段别名或 `env_prefix` 映射环境变量；模型对象不对外返回。`scope: "client"` 模块的普通模式仍使用原 `schema`。
3. 公共 `server/settings_plugins.py` 在导入时按目录名排序扫描一级模块的入口，检查基本结构和重复 ID。无需修改公共名单；坏描述和导入错误直接失败，不吞异常。
4. 模块的客户端业务 API 按自身适用模式调用 `readSettings()` 读取自身 ID 的配置，随请求传给后端；后端仅对该次调用使用配置，不能修改共享实例。公共设置层不分发业务请求。

增删入口文件后需重启后端，再重新打开设置。移除入口只移除设置项，本地旧值保留；同一 ID 再接入可恢复旧值。扫描仅面向普通文件系统包，不递归发现、不监听目录、不热卸载 Python 代码。删除整个业务目录仍需处理应用路由和模块依赖，本改造不提供业务插件框架。

目前提供 Agent、IMS、切片、ASR 和服务启动五个入口；ASR 仍无独立客户端转写页面。服务端测试使用临时真实包目录验证新增、移除、重复 ID 和依赖失败；前端使用第二模块描述验证动态表单、旧值保留和非法输入拦截。数据库不参与发现。

## Debug 启动配置生效

Debug 安装包保存后，退出并重新打开客户端。`server.desktop.configure()` 在导入业务前，从应用数据目录 `data/settings/settings.json` 读取各模块声明的字段，覆盖内置后端的进程环境；ASR 密钥、切片策略、素材匹配、合成规格、Remotion 资源限制和路径都沿用原业务配置类读取。数据库不接入，不修改共享服务或 `.env` 文件。Agent/IMS 原有请求字段仍在下一次调用生效。

「服务启动」端口保存后用于实际回环监听；没有保存端口时继续自动分配。路径留空采用随包默认值，相对路径以应用的 `backend/` 数据目录为基准。修改数据目录会切换数据位置，不迁移原有文件。

这条启动加载路径仅适用于使用内置后端的 Debug 桌面客户端。Vite 浏览器只有内存保存，手动启动或连接远程后端仍使用自己的环境配置；指定了通用后端地址时也不会启动内置后端。不能用浏览器预览验证启动配置生效。

## Agent 与 IMS 实际调用

- Remotion Agent：Actor API 地址、模型、密钥；视觉模型及可选独立地址/密钥（留空复用 Actor）；关闭思考与模型超时。Debug 额外展示目录、浏览器可执行文件和任务资源限制，重启内置后端后生效。保存后，现有字效页面的能力查询、创建、消息与重试读取最新快照；若页面之前提示未配置，点击「重新连接」刷新就绪状态。
- 上海 IMS：Access Key ID、Access Key Secret、可选 Security Token、地域与官方 Endpoint（默认上海）。接入现有视频合成提交与查询，没有新增合成页面；ASR、切片和素材匹配读取后端启动配置；Debug 内置服务会加载本客户端保存值。
- 业务 API 分别使用 `X-Remotion-Config` / `X-IMS-Config` 请求头，内容为 `encodeURIComponent(JSON.stringify(values))`。不放在 URL、聊天正文或合成输入中。未保存对应模块时省略请求头，兼容服务端环境默认值；已保存配置不与服务端凭据混合。请求解析复用字段类型及原有 IMS 校验；未知字段忽略，不覆盖服务端策略。
- 后端按任务持有独立凭据快照，不写共享 `.env`、作品历史、数据库任务或日志。重试读取当前客户端最新值。IMS 成片查询直接使用本次提供的凭据，访问权限由云服务判断；任务只记录是否使用客户端配置，不保存账号指纹。任务结束及通知完成后释放凭据。
- 服务重启会清除内存凭据：Remotion 沿用中断后显式重试；未完成的客户端 IMS 任务因缺少内存快照走已有阶段失败处理，需要重新提交；已完成的 IMS 任务仍可提供有访问权限的凭据查询。

在已保存 IMS 设置的 Vite 页面中可调用真实合成入口（会调用云服务）：

```js
const { createComposition, getComposition } = await import('/src/features/video_composition/api.ts');
const taskId = await createComposition({ text: '文案', videoUrl: videoUrl, audioUrl: audioUrl, styleId: templateId });
const result = await getComposition(taskId);
```

创建接口的受理结果由服务端包在 `data` 中，`createComposition` 已解包并返回任务 ID 字符串；查询接口返回扁平的 `TaskResponse`，不额外包一层。

## 开发联调

启动后端，并在 `client/` 下用 `IMV_DEBUG=true bun run dev` 启动 Vite，打开设置填写切片 API 地址、Key 和模型，点击保存。Vite 开发页面的控制台可以使用以下入口（会真实调用模型，传入自己的 ASR 对象）：

```js
const { requestSegmentation } = await import('/src/features/segmentation/api.ts');
const result = await requestSegmentation({script: '与 ASR 对应的文案', asr_result: yourAsrResult});
```

自动测试隔离网络与真实密钥，不会调用模型：

```sh
# 仓库根目录
uv run --locked --project server pytest server/tests/test_settings_plugins.py server/tests/test_desktop.py server/tests/test_segmentation.py server/tests/test_remotion_templates.py server/tests/test_video_composition.py -v
cargo test --locked --manifest-path client/src-tauri/Cargo.toml --lib settings
# client/ 下
bun run test settings.test.tsx workspace.test.tsx
bun run build
# 另以 IMV_DEBUG=true 启动 Vite 后执行原生数字输入回归（可用 IMV_CHROME_PATH 指定 Chromium）
IMV_BROWSER_URL=http://localhost:1420 bun tests/settings.browser.mjs
```

覆盖目录脱敏、真实入口发现与移除、通用校验、本地读写、保存后请求使用最新值、不同请求的配置隔离及旧请求兼容。浏览器和模拟 IPC 测试不代表桌面安装包验证。
