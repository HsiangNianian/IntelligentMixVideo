/** 配置 React、Tailwind 和源码别名，并保留 Tauri 固定端口与开发监听规则。 */
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import process from "node:process";
const host = process.env.TAURI_DEV_HOST;
/** 客户端版本的唯一来源；发版脚本同步 package.json，界面标题与设置页都读取注入的这个值。 */
const { version } = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8")) as { version: string };

/** 读取一条 git 命令的输出；不在 git 仓库里（如源码压缩包构建）或没有 git 时返回 null。 */
function git(...args: string[]): string | null {
  try {
    return execFileSync("git", args, { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch {
    return null;
  }
}

/**
 * 构建对应的提交校验码：短提交哈希，工作区有未提交改动时加 `-dirty`。`IMV_COMMIT` 可显式指定（例如没有 .git 的构建环境），
 * 否则读取 git，再退到 CI 的 `GITHUB_SHA`；都没有时为 unknown。
 */
function buildCommit(): string {
  const given = process.env.IMV_COMMIT?.trim();
  if (given) return given;
  const hash = git("rev-parse", "--short=7", "HEAD") ?? process.env.GITHUB_SHA?.slice(0, 7);
  if (!hash) return "unknown";
  return git("status", "--porcelain", "--untracked-files=no") ? `${hash}-dirty` : hash;
}

/** Tauri 指定开发主机时同步 HMR 地址；Rust 文件交给 Cargo 处理。 */
export default defineConfig(() => ({
  plugins: [react(), tailwindcss()],
  // 与 Rust 打包共用显式开关；Vite 开发模式不会自动开启服务端设置。
  define: {
    "import.meta.env.IMV_DEBUG": JSON.stringify(process.env.IMV_DEBUG ?? "false"),
    "import.meta.env.IMV_VERSION": JSON.stringify(version),
    "import.meta.env.IMV_COMMIT": JSON.stringify(buildCommit()),
  },
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },

  // Vite options tailored for Tauri development and only applied in `tauri dev` or `tauri build`
  //
  // 1. prevent Vite from obscuring rust errors
  clearScreen: false,
  // 2. tauri expects a fixed port, fail if that port is not available
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host
      ? {
          protocol: "ws",
          host,
          port: 1421,
        }
      : undefined,
    watch: {
      // 3. tell Vite to ignore watching `src-tauri`
      ignored: ["**/src-tauri/**"],
    },
  },
}));
