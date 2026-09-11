import assert from "node:assert/strict";
import { cpSync, mkdtempSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";

const dir = mkdtempSync(join(tmpdir(), "imv-release-smoke-"));
const source = process.cwd();
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
  const metadata = JSON.parse(run("cargo", [
    "metadata", "--manifest-path", "client/src-tauri/Cargo.toml", "--locked", "--no-deps", "--format-version", "1",
  ], dir));
  assert.equal(metadata.packages.find((pkg) => pkg.name === "client").version, "254.254.65534");
  assert.equal(readFileSync(join(dir, "client/src-tauri/Cargo.lock"), "utf8"), cargoBefore);
  console.log("Version injection, frozen Bun install and Cargo lockfile checks passed");
} finally {
  assert.equal(dirname(resolve(dir)), resolve(tmpdir()));
  assert(dir.includes("imv-release-smoke-"));
  rmSync(dir, { recursive: true, force: true });
}
