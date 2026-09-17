# 客户端模块设置

`PluginSettings.tsx` 从 `GET /api/settings/plugins` 的描述生成设置页内的横向模块导航，每个模块一个按钮，默认显示首个模块，点击或方向键切换对应表单。切换模块仅隐藏表单并保留当前输入，窄屏导航可横向滚动。表单支持字符串、密码、数字和布尔值。打开设置时读取，点击保存只写当前客户端，不修改后端 `.env`。没有后台同步、版本迁移或离开设置页的未保存保护。目录依赖后端服务；后端未启动时显示明确提示。

- 桌面通过 `local_settings` 命令保存到应用数据目录 `data/settings/settings.json`，按插件 ID 分组。API Key 目前和普通值一起明文保存；没有接系统凭据库。
- 浏览器预览仅保存在页面进程内存，切换设置页可恢复，刷新页面丢失，不写 localStorage。
- `api.ts` 的 `requestSegmentation({script, asr_result})` 读取已保存的切片配置，随 `POST /segmentations` 的 `config` 一起提交。编辑但未保存的值不进入请求。省略本地配置时发送原有请求。
- 当前没有独立切片业务页面，请求函数通过自动联调用例和下面的开发命令使用；视频合成与 Remotion 不读取这些配置。

## 增加或移除插件

后端 `segmentation/settings.py` 的 `ClientSettings` 同时定义请求参数和可展示字段，`SETTINGS_PLUGIN` 包含 `id`、`name`、`schema`。新模块按同一格式声明，在 `server/settings_plugins.py` 的 `plugins` 字典注册；注册仅用于设置表单，模块业务仍需显式接入请求配置，不能修改共享 Settings 实例。

移除字典条目后，重新进入设置页就不再显示，本地值保留。新 Python 模块代码需要后端重新加载，客户端无需重新构建。`server/tests/test_settings_plugins.py` 使用真实 ASR Settings 注册、移除第二个插件，`client/tests/settings.test.tsx` 验证第二个表单自动显示和恢复；生产目录默认只有切片。

## 开发联调

启动后端和 Vite，打开设置填写切片 API 地址、Key 和模型，点击保存。Vite 开发页面的控制台可以使用以下入口（会真实调用模型，传入自己的 ASR 对象）：

```js
const { requestSegmentation } = await import('/src/features/settings/api.ts');
const result = await requestSegmentation({script: '与 ASR 对应的文案', asr_result: yourAsrResult});
```

自动测试隔离网络与真实密钥，不会调用模型：

```sh
# 仓库根目录
uv run --locked --project server pytest server/tests/test_settings_plugins.py server/tests/test_segmentation.py -v
cargo test --locked --manifest-path client/src-tauri/Cargo.toml --lib settings
# client/ 下
bun run test settings.test.tsx workspace.test.tsx
bun run build
```

覆盖目录脱敏、ASR 插拔、本地读写、保存与重新进入、不同请求的配置隔离及旧请求兼容。浏览器和模拟 IPC 测试不代表桌面安装包验证。
