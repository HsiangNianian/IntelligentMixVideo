IntelligentMixVideo
===================

> 基于阿里 IMS 云剪辑的智能混剪 Agent 系统，支持自定义模板、服务端文本切片、素材召回、Agent 自动编写编排与自动编写 Remotion 特效等功能。

Structure
---------

- `client/`：Rust + Tauri 2 + React + TypeScript 桌面客户端。
- `server/`：Python 业务 API 预留目录，暂不实现业务。

客户端运行
----------

安装 Node.js 22.12+、Rust stable，以及 [Tauri 2 平台依赖](https://v2.tauri.app/start/prerequisites/)。
Windows 需要 Visual Studio C++ Build Tools 和 WebView2；macOS 需要 Xcode Command Line Tools。
Visual Studio Installer 中需启用“使用 C++ 的桌面开发”，包含 MSVC 和 Windows SDK；
Windows ARM64 主机还需 ARM64 C++ 构建工具。
若报错出现 `link: extra operand`，说明误用了 Cygwin 的 `link.exe`，
请确认 C++ 工具链安装完整，并在匹配架构的 Visual Studio Developer PowerShell 中运行。

```sh
cd client
npm ci
npm run tauri dev
```

首页显示本机当前日期和时间，每秒更新，组件位于 `client/src/components/CurrentTime.tsx`。

```sh
# 编译前端（包含 TypeScript 检查）
npm run build
# 编译桌面程序和安装包
npm run tauri build
```

本地安装包输出到 `client/src-tauri/target/release/bundle/`。

跨平台 CI
---------

`.github/workflows/client-build.yml` 参考 [DropOut 的平台矩阵、缓存及产物上传配置](https://github.com/HydroRoll-Team/DropOut/blob/main/.github/workflows/test.yml)。
修改客户端或该工作流的 push / PR 会自动构建，也可在 Actions 页面手动触发。

| 平台 | 架构 | 安装包 |
| --- | --- | --- |
| Windows | x64 | NSIS exe、MSI |
| Linux | x64 | deb、AppImage |
| macOS | Apple Silicon、Intel | dmg |

从 Actions 对应运行的 Artifacts 下载产物，保留 14 天。CI 使用依赖锁文件，不需要额外配置发布密钥。
当前安装包未配置代码签名或 macOS 公证；正式分发时需另行配置。
