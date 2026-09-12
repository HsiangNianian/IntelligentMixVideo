# IntelligentMixVideo Client

基于 Tauri 2、Rust、React、TypeScript、Tailwind CSS 4 和 shadcn/ui。
安装及跨平台构建说明见 [仓库 README](../README.md)，强制开发约束见 [AGENTS.md](../AGENTS.md)。

## 开发结构

```text
src/
  main.tsx                 # 加载全局样式并挂载应用
  App.tsx                  # 组合页面
  pages/HomePage.tsx        # 首页布局
  components/CurrentTime.tsx # 本机时间状态与展示
  components/ui/card.tsx    # shadcn/ui 基础卡片
  lib/utils.ts             # cn 类名合并
  styles/globals.css       # Tailwind、语义主题令牌、基础样式
```

依赖方向是页面 → 业务组件 → 基础 UI / 工具，`@/` 指向 `src/`。
只新增需求实际用到的模块，不预建空的 hooks、features 或 API 层。
所有维护的源码需有文件头说明和必要的声明/逻辑注释；样式优先使用 Tailwind 语义工具类。

在本目录使用 Bun：

```sh
bun install --frozen-lockfile
bun run dev
bun run build
bun run tauri dev
# 需要新基础控件时按需添加，并审阅生成代码、主题令牌和依赖
bunx --bun shadcn@latest add <组件>
```

shadcn/ui 配置位于 `components.json`，当前只保留有使用方的 Card。
其 MIT 许可见 `public/THIRD_PARTY_NOTICES.txt`，Vite 构建时将其复制到输出目录。

## Recommended IDE Setup

- [VS Code](https://code.visualstudio.com/) + [Tauri](https://marketplace.visualstudio.com/items?itemName=tauri-apps.tauri-vscode) + [rust-analyzer](https://marketplace.visualstudio.com/items?itemName=rust-lang.rust-analyzer)
