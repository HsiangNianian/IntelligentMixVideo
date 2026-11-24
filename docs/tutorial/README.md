# IntelligentMixVideo 图文教程

[index.html](index.html) 是可维护的中文使用教程，配套 [print.css](print.css)
和 [capture.ts](capture.ts) 截图步骤，输出可独立分发的 HTML 与 A4 PDF。
**正文在每次发版前人工核对；CI 每次构建自动操作当前客户端、截图、添加编号并导出，不接入 AI。**
内容核对截至 **2026-11-09 的 dev `4f7c23b`**，不是正式版兼容性承诺。

## 构建

在仓库根目录执行，使用 `client/package.json` 指定的 Bun：

```sh
bun install --cwd client --frozen-lockfile
bun client/node_modules/playwright-core/cli.js install chromium
bun --no-env-file .github/scripts/build-tutorial.mjs
```

Linux 构建机需要 Chromium 系统依赖及 `Noto Sans CJK SC` 字体。CI 使用
Playwright 的 `install --with-deps chromium` 和 Ubuntu 的 `fonts-noto-cjk`。
已有 Chrome 的本机可指定可执行文件，无需另下载浏览器：

```sh
IMV_CHROME_PATH=/usr/bin/google-chrome bun --no-env-file .github/scripts/build-tutorial.mjs
```

产物默认写入已忽略的 `dist/tutorial/`：

- `IntelligentMixVideo-Tutorial.pdf`：包含中文字体、可选择文字、目录跳转、书签和页码。
- `IntelligentMixVideo-Tutorial.html`：内嵌图片和样式，可以离线打开；中文显示使用阅读设备的字体。
- `screenshots.json`：截图清单、视口、浏览器版本和构建提交，便于追溯。

脚本也接受一个输出目录参数。构建在临时目录中重新截取全部图片，没有使用旧截图的
回退路径。控件缺失、标注超出视口、编号无法避开标注区域、未覆盖的业务请求、缺失图片、
越界文件、失效的内部链接、重复 ID、页面溢出或导出时依赖网络都会导致失败。
浏览器与依赖安装需要联网；截图只启动本地 Vite，业务 API 使用演示数据，
不启动数据库、不读取 `.env`、不调用 AI/IMS，导出文件可离线使用。

只调试截图时，可单独执行：

```sh
IMV_CHROME_PATH=/usr/bin/google-chrome bun --no-env-file docs/tutorial/capture.ts
```

图片与可预览源码写入 `dist/tutorial/source/`；截图失败时附带 `failure.txt` 页面文字，
便于排查控件变化。版本库中的 HTML 是正文源码，完整图文请打开构建后的离线 HTML/PDF。

验证命令：

```sh
bun test ./.github/scripts
IMV_TEST_TUTORIAL_BROWSER=1 bun test ./.github/scripts/build-tutorial.test.mjs
actionlint .github/workflows/*.yml
pdfinfo dist/tutorial/IntelligentMixVideo-Tutorial.pdf
pdftotext dist/tutorial/IntelligentMixVideo-Tutorial.pdf /tmp/imv-tutorial.txt
git diff --check
```

## CI

`Validate project` 根据变更范围调用 [Build tutorial](../../.github/workflows/tutorial.yml)。
教程正文、样式、截图步骤、构建脚本、前端源码/资源/夹具/构建配置、共享效果目录、
客户端依赖清单和 CI 配置变更会触发构建，
手动 `Validate project` 也会构建。可单独手动运行 `Build tutorial`；GitHub 的
手动入口需要该工作流先存在于默认分支。

在 Actions 的成功运行页面下载 `intelligent-mix-video-tutorial-<提交 SHA>` artifact，
解压得到 PDF、离线 HTML 和截图清单；artifact 保留 14 天。
教程结果纳入现有 `CI result` / `Push result`，失败、取消或意外跳过会阻止通过。
纯教程内容或导出脚本变更不触发桌面安装包、前端或服务端构建。
当前不自动修改 GitHub Release 附件，也不发布网站。

导出基于 [Playwright page.pdf](https://playwright.dev/docs/api/class-page#page-pdf)，
产物交付使用 [GitHub Actions artifacts](https://docs.github.com/en/actions/tutorials/store-and-share-data)。

## 每次发版怎样维护

每个 `.page` 对应一页 A4。内容超长时拆页，保留内部链接的稳定 ID。
发版前核对并修改功能说明，更新 `reviewed-date`、`reviewed-commit` 与封面适用版本和日期；构建 SHA
由脚本注入，表示产物来自哪次检出，不表示内容已经自动核对到该提交。
CI 自动更新截图、标注、排版和导出，不会自行理解功能变化或改写正文。

截图按 [capture.ts](capture.ts) 的固定步骤执行，使用真实的按钮名称和可访问标签定位，
首页先等待开场遮罩消失并检查创作入口可交互，避免把遮罩后的控件位置标到启动画面上。
首页四个编号分别覆盖导航、两个创作入口、模板列表、主题与设置，与正文逐项对应。
标注由 [callouts.mjs](callouts.mjs) 绘制为 SVG：采用略带手绘弧度的圆角轮廓、弧线箭头和
大号圆形编号，沿用原稿的标注风格。框的位置来自实际控件边界；关联控件可合并成一个区域，
例如首页只圈住已有模板与标题。截图步骤用 `mark` 指定编号的方向、位置和大小，
编号自动避开标注区域与其他编号，所有曲线均固定生成，不使用随机数或 AI。
SVG 作为图片加载，解码完成后再截图，不将标注字符串作为页面 HTML 插入，也不执行其中的脚本或事件。
界面操作流程改变时同步修改这些步骤，新增插图时增加相应
截图步骤和 HTML 引用；控件改名或消失会让构建失败，不会自动猜测新的操作。
构建后审阅实际版面、截图可读性和文字准确性，再交付对应发布版本的教程。

原始输入为用户提供的 `IntelligentMixVideo-Tutorial.pdf`（25 页，2026-10-28），
没有找到其 HTML 源码。本目录重建了可编辑正文，并将全部 21 张插图替换为当前客户端
自动截图，1440×1000，使用独立浏览器、固定演示时间、演示模板和六类资产目录。
字体演示使用依赖内自带的 Geist 字体。全部业务请求被本地替身拦截，只允许读取演示
数据；不读取真实业务数据、不发送生成请求、不提交云端任务。
编辑器截图采用内置基础视频，IMS 封面显示离线占位；这些截图用于定位操作，
不构成桌面更新、IMS 渲染、翻译或 AI 生成结果的验证。

## 本次内容核对

| 主题 | 更新内容 | 实现依据 |
| --- | --- | --- |
| AI 资产 | 六类目录、普通字效手动归类、双语能力优先 | [parameters.ts](../../client/src/features/sprites/parameters.ts)、[SpritePanels.tsx](../../client/src/features/templates/SpritePanels.tsx) |
| 双语绑定 | 添加双语资产自动恢复英文对象、冲突与关闭限制 | [clips.ts](../../client/src/features/templates/clips.ts)、[TemplateInspector.tsx](../../client/src/features/templates/TemplateInspector.tsx) |
| 字效生成 | 三个开关、两行/四行双语、自定义预览时长 | [ChatPanel.tsx](../../client/src/features/remotion_templates/ChatPanel.tsx)、[DurationPicker.tsx](../../client/src/features/remotion_templates/DurationPicker.tsx) |
| 关键词保存 | 旧资产关键词默认值的修复入口 | [字效工作区说明](../../client/src/features/remotion_templates/README.md) |
| 字幕 | 中文语义切分、超长告警、英文意群切分 | [segmentation.py](../../server/src/server/segmentation/segmentation.py)、[服务端说明](../../server/README.md) |
| 转场 | 随机允许重复、动态间隔、短镜头限制 | [模板转场约定](../../client/src/features/templates/AGENTS.md)、[服务端说明](../../server/README.md) |
| 蒙版与人物 | 纯色/渐变/图片蒙版、圆形开场和三种同屏布局 | [FrameMaskPanel.tsx](../../client/src/features/templates/FrameMaskPanel.tsx)、[客户端说明](../../client/README.md) |
| 设置 | 以实际实现为准，保留保存成功后关闭的说明 | [PluginSettings.tsx](../../client/src/features/settings/PluginSettings.tsx) |
| 操练场 | 搜索模板、三栏布局、真实阶段、查询与提交的区别 | [PlaygroundWorkspace.tsx](../../client/src/features/video_composition/PlaygroundWorkspace.tsx) |

修改这些功能时，需要同步审阅教程；不能只依赖旧 README 或 AGENTS 的文字判断界面行为。
