/** 在临时副本同步两端边界版本，验证 Bun 冻结安装及 Cargo、uv 锁文件，最后清理副本。 */

import assert from "node:assert/strict";
import { cpSync, mkdtempSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";

const dir = mkdtempSync(join(tmpdir(), "imv-release-smoke-"));
const source = process.cwd();
/** 在指定目录执行真实命令，使用固定测试 tag 并让失败中断验证。 */
function run(exe, args, cwd) {
  const result = spawnSync(exe, args, {
    cwd, env: { ...process.env, RELEASE_TAG: "v254.254.65534" }, encoding: "utf8",
  });
  assert.equal(result.status, 0, result.stderr || String(result.error));
  return result.stdout;
}
try {
  for (const path of [
    "client/package.json", "client/bun.lock", "client/bunfig.toml",
    "client/src-tauri/Cargo.toml", "client/src-tauri/Cargo.lock",
    "client/src-tauri/tauri.conf.json", "client/src-tauri/build.rs", "client/src-tauri/src",
    "server/pyproject.toml", "server/uv.lock", "server/README.md", "server/src",
  ]) {
    mkdirSync(dirname(join(dir, path)), { recursive: true });
    cpSync(path, join(dir, path), { recursive: true });
  }
  const lockBefore = readFileSync(join(dir, "client/bun.lock"), "utf8");
  const script = join(source, ".github/scripts/validate-release.mjs");
  run(process.execPath, [script, "--write"], dir);
  run(process.execPath, [script], dir);
  run(process.execPath, ["install", "--frozen-lockfile", "--ignore-scripts"], join(dir, "client"));
  assert.equal(readFileSync(join(dir, "client/bun.lock"), "utf8"), lockBefore);
  const cargoBefore = readFileSync(join(dir, "client/src-tauri/Cargo.lock"), "utf8");
  // Exercise the actual release-only workflow command from its working directory.
  const workflow = Bun.YAML.parse(readFileSync(join(source, ".github/workflows/client-build.yml"), "utf8"));
  const job = workflow.jobs.build;
  const step = job.steps.find((step) => step.name === "Verify Cargo lockfile");
  const [exe, ...args] = step.run.split(" > ")[0].split(/\s+/);
  const metadata = JSON.parse(run(exe, args, join(dir, step["working-directory"] ?? job.defaults.run["working-directory"])));
  assert.equal(metadata.packages.find((pkg) => pkg.name === "client").version, "254.254.65534");
  assert.equal(readFileSync(join(dir, "client/src-tauri/Cargo.lock"), "utf8"), cargoBefore);
  const serverLockBefore = readFileSync(join(dir, "server/uv.lock"), "utf8");
  // 空缓存、离线校验确保版本准备不依赖本机已下载的第三方包元数据。
  run("uv", ["lock", "--check", "--offline", "--cache-dir", join(dir, "uv-cache"), "--project", "server"], dir);
  assert.equal(readFileSync(join(dir, "server/uv.lock"), "utf8"), serverLockBefore);
  console.log("Client/server version sync, frozen Bun install and Cargo/uv lockfile checks passed");
} finally {
  assert.equal(dirname(resolve(dir)), resolve(tmpdir()));
  assert(dir.includes("imv-release-smoke-"));
  rmSync(dir, { recursive: true, force: true });
}
