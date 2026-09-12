/** 用临时清单验证两端版本、显式 tag 写入、幂等性及失败无副作用，不访问实际仓库清单。 */

import { test } from "bun:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const validator = fileURLToPath(new URL("./validate-release.mjs", import.meta.url));

/** 创建最小项目夹具，运行独立脚本并对比执行前后快照，最后清理。 */
function validate(tag, mutate = () => {}, args = []) {
  const dir = mkdtempSync(join(tmpdir(), "imv-release-test-"));
  try {
    mkdirSync(join(dir, "client/src-tauri"), { recursive: true });
    mkdirSync(join(dir, "server"));
    // 同版本第三方包用于捕获误替换；校验信息用于捕获意外更新依赖。
    const fixtures = {
      "client/package.json": '{"version":"0.1.0"}',
      "client/bun.lock": '{"lockfileVersion":1,"workspaces":{"":{"name":"client"}},"packages":{"example":["example@0.1.0","","keep-me"]}}',
      "client/src-tauri/tauri.conf.json": '{"version":"0.1.0"}',
      "client/src-tauri/Cargo.toml": '[package]\nname = "client"\nversion = "0.1.0"\n\n[dependencies]\ntauri = "2"\n',
      "client/src-tauri/Cargo.lock": 'version = 4\n\n[[package]]\nname = "client"\nversion = "0.1.0"\n\n[[package]]\nname = "example"\nversion = "0.1.0"\nchecksum = "keep-me"\n',
      "server/pyproject.toml": '[project]\nname = "imv-server"\nversion = "0.1.0"\n\n[build-system]\nrequires = ["uv_build"]\n',
      "server/uv.lock": 'version = 1\n\n[[package]]\nname = "imv-server"\nversion = "0.1.0"\nsource = { editable = "." }\n\n[[package]]\nname = "example"\nversion = "0.1.0"\nsource = { registry = "https://pypi.org/simple" }\nwheels = [{ url = "https://example.com/example.whl", hash = "sha256:keep-me" }]\n',
    };
    for (const [path, content] of Object.entries(fixtures)) {
      writeFileSync(join(dir, path), content);
    }
    mutate(dir);
    /** 读取所有测试清单，供失败无副作用与重复执行检查使用。 */
    const snapshot = () => Object.fromEntries(
      Object.keys(fixtures).map((path) => [path, readFileSync(join(dir, path), "utf8")]),
    );
    const before = snapshot();
    const env = { ...process.env };
    if (tag === undefined) delete env.RELEASE_TAG;
    else env.RELEASE_TAG = tag;
    const options = {
      cwd: dir,
      env,
      encoding: "utf8",
    };
    const result = spawnSync(process.execPath, [validator, ...args], options);
    const after = snapshot();
    // Re-running the write must produce the exact same files.
    const repeated = args.includes("--write") && result.status === 0
      ? spawnSync(process.execPath, [validator, ...args], options)
      : undefined;
    const verification = spawnSync(process.execPath, [validator], options);
    return { ...result, before, after, repeated, afterRepeat: snapshot(), verification };
  } finally {
    assert.equal(dirname(resolve(dir)), resolve(tmpdir()));
    assert(dir.includes("imv-release-test-"));
    rmSync(dir, { recursive: true, force: true });
  }
}

test("accepts matching stable tag and all package versions", () => {
  assert.equal(validate("v0.1.0").status, 0);
});

test("without a tag validates against the client version without writing", () => {
  const result = validate(undefined);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(result.after, result.before);
  const stale = validate(undefined, (dir) => {
    const path = join(dir, "server/pyproject.toml");
    writeFileSync(path, readFileSync(path, "utf8").replace("0.1.0", "0.0.9"));
  });
  assert.notEqual(stale.status, 0);
  assert.deepEqual(stale.after, stale.before);
});

test("write and tag-only modes require an explicit tag", () => {
  for (const args of [["--write"], ["--tag-only"]]) {
    const result = validate(undefined, undefined, args);
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /RELEASE_TAG is required/);
    assert.deepEqual(result.after, result.before);
  }
});

test("rejects malformed and prerelease tags", () => {
  for (const tag of ["0.1.0", "v01.1.0", "v0.1.0-rc.1", "v0.1.0+build"]) {
    assert.notEqual(validate(tag).status, 0, tag);
  }
});

test("rejects tag that differs from package versions", () => {
  assert.notEqual(validate("v0.2.0").status, 0);
});

test("tag-only validation accepts a new version without changing files", () => {
  const result = validate("v0.2.0", undefined, ["--tag-only"]);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(result.after, result.before);
});

test("prepares all client and server versions from tag while preserving dependencies", () => {
  const result = validate("v0.2.0", undefined, ["--write"]);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.verification.status, 0, result.verification.stderr);
  assert.equal(result.repeated.status, 0);
  assert.deepEqual(result.afterRepeat, result.after);
  assert.equal(result.after["client/bun.lock"], result.before["client/bun.lock"]);
  assert(result.after["client/src-tauri/Cargo.lock"].endsWith(
    '[[package]]\nname = "example"\nversion = "0.1.0"\nchecksum = "keep-me"\n',
  ));
  assert.match(result.after["client/src-tauri/Cargo.lock"], /^version = 4/);
  assert(result.after["server/uv.lock"].endsWith(
    '[[package]]\nname = "example"\nversion = "0.1.0"\nsource = { registry = "https://pypi.org/simple" }\nwheels = [{ url = "https://example.com/example.whl", hash = "sha256:keep-me" }]\n',
  ));
  assert.match(result.after["server/uv.lock"], /^version = 1/);
});

test("invalid tags never modify files in write mode", () => {
  for (const tag of ["v0.2.0-rc.1", "v256.0.0", "v1.256.0", "v1.0.65536"]) {
    const result = validate(tag, undefined, ["--write"]);
    assert.notEqual(result.status, 0, tag);
    assert.deepEqual(result.after, result.before);
  }
});

test("missing client lock entry fails before any files are changed", () => {
  const result = validate("v0.2.0", (dir) => {
    writeFileSync(join(dir, "client/src-tauri/Cargo.lock"), 'version = 4\n');
  }, ["--write"]);
  assert.notEqual(result.status, 0);
  assert.deepEqual(result.after, result.before);
});

test("missing project versions or server lock entry fails before any writes", () => {
  for (const [path, content] of [
    ["client/package.json", '{}\n'],
    ["client/src-tauri/tauri.conf.json", '{}\n'],
    ["server/pyproject.toml", '[project]\nname = "imv-server"\n'],
    ["server/uv.lock", 'version = 1\n'],
  ]) {
    const result = validate("v0.2.0", (dir) => {
      writeFileSync(join(dir, path), content);
    }, ["--write"]);
    assert.notEqual(result.status, 0, path);
    assert.deepEqual(result.after, result.before);
  }
});

test("rejects each stale manifest or lockfile", () => {
  for (const path of [
    "client/package.json",
    "client/src-tauri/tauri.conf.json",
    "client/src-tauri/Cargo.toml",
    "client/src-tauri/Cargo.lock",
    "server/pyproject.toml",
    "server/uv.lock",
  ]) {
    const result = validate("v0.1.0", (dir) => {
      const file = join(dir, path);
      writeFileSync(file, readFileSync(file, "utf8").replaceAll("0.1.0", "0.0.9"));
    });
    assert.notEqual(result.status, 0, path);
  }
});

test("rejects MSI-incompatible versions even when manifests agree", () => {
  const result = validate("v256.1.0", (dir) => {
    for (const path of [
      "client/package.json", "client/src-tauri/tauri.conf.json", "client/src-tauri/Cargo.toml",
      "client/src-tauri/Cargo.lock", "server/pyproject.toml", "server/uv.lock",
    ]) {
      const file = join(dir, path);
      writeFileSync(file, readFileSync(file, "utf8").replaceAll("0.1.0", "256.1.0"));
    }
  });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /MSI limits/);
});
