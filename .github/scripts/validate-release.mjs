/** 校验客户端与服务端版本一致及 MSI 范围；显式 tag 可同步自身版本，不改第三方依赖。 */

import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";

/** 读取 UTF-8 JSON 清单，解析失败时直接终止发版准备。 */
const readJson = (path) => JSON.parse(readFileSync(path, "utf8"));
const requestedTag = process.env.RELEASE_TAG ?? "";
const write = process.argv.includes("--write");
const tagOnly = process.argv.includes("--tag-only");
if (write || tagOnly) assert(requestedTag, "RELEASE_TAG is required for --write and --tag-only");
const packageJson = tagOnly ? undefined : readJson("client/package.json");
const tag = requestedTag || `v${packageJson.version}`;
assert.match(tag, /^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/, "Use a stable release tag such as v0.1.0");
const version = tag.slice(1);
// WiX/MSI uses an 8-bit major/minor and a 16-bit patch version.
const [major, minor, patch] = version.split(".").map(Number);
assert(major <= 255 && minor <= 255 && patch <= 65535, "Version exceeds Windows MSI limits");
if (tagOnly) {
  console.log(`Release tag validated: ${tag}`);
  process.exit(0);
}

// bun.lock omits the root package version and must stay byte-for-byte unchanged.
// The build workflow verifies it with a frozen install after version preparation.
const tauri = readJson("client/src-tauri/tauri.conf.json");
const cargoToml = readFileSync("client/src-tauri/Cargo.toml", "utf8");
const cargoPackage = cargoToml.match(/^\[package\]([\s\S]*?)(?=^\[|(?![\s\S]))/m)?.[1];
const cargoVersion = cargoPackage?.match(/^version\s*=\s*"([^"]+)"/m)?.[1];
const cargoName = cargoPackage?.match(/^name\s*=\s*"([^"]+)"/m)?.[1];
const cargoLock = readFileSync("client/src-tauri/Cargo.lock", "utf8");
const cargoLockPackage = cargoLock.split("[[package]]").find(
  (entry) => entry.match(/^name\s*=\s*"([^"]+)"/m)?.[1] === cargoName,
);
const cargoLockVersion = cargoLockPackage?.match(/^version\s*=\s*"([^"]+)"/m)?.[1];
const serverToml = readFileSync("server/pyproject.toml", "utf8");
const serverProject = serverToml.match(/^\[project\]([\s\S]*?)(?=^\[|(?![\s\S]))/m)?.[1];
const serverVersion = serverProject?.match(/^version\s*=\s*"([^"]+)"/m)?.[1];
const serverName = serverProject?.match(/^name\s*=\s*"([^"]+)"/m)?.[1];
const serverLock = readFileSync("server/uv.lock", "utf8");
const serverLockPackage = serverLock.split("[[package]]").find(
  (entry) => entry.match(/^name\s*=\s*"([^"]+)"/m)?.[1] === serverName,
);
const serverLockVersion = serverLockPackage?.match(/^version\s*=\s*"([^"]+)"/m)?.[1];

if (write) {
  // Check all inputs before changing any files. Never refresh dependency versions.
  assert([packageJson.version, tauri.version].every((value) => typeof value === "string" && value),
    "Cannot find the client version in JSON manifests");
  assert(cargoName && cargoVersion && cargoLockVersion, "Cannot find the client package in Cargo manifests");
  assert(serverName && serverVersion && serverLockVersion, "Cannot find the server package in Python manifests");
  packageJson.version = version;
  tauri.version = version;
  // 只替换已定位的自身包段中的版本，避免改动同版本的第三方依赖。
  const replaceVersion = (section) =>
    section.replace(/^(version\s*=\s*)"[^"]+"/m, `$1"${version}"`);
  // 校验完成后构造全部文件内容，再统一落盘。
  const updates = {
    "client/package.json": JSON.stringify(packageJson, null, 2) + "\n",
    "client/src-tauri/tauri.conf.json": JSON.stringify(tauri, null, 2) + "\n",
    "client/src-tauri/Cargo.toml": cargoToml.replace(cargoPackage, replaceVersion(cargoPackage)),
    "client/src-tauri/Cargo.lock": cargoLock.replace(cargoLockPackage, replaceVersion(cargoLockPackage)),
    "server/pyproject.toml": serverToml.replace(serverProject, replaceVersion(serverProject)),
    "server/uv.lock": serverLock.replace(serverLockPackage, replaceVersion(serverLockPackage)),
  };
  for (const [path, content] of Object.entries(updates)) {
    writeFileSync(path, content);
  }
  console.log(`Client and server manifests and lockfiles updated to ${version}`);
  process.exit(0);
}

for (const [name, actual] of Object.entries({
  "package.json": packageJson.version,
  "tauri.conf.json": tauri.version,
  "Cargo.toml": cargoVersion,
  "Cargo.lock": cargoLockVersion,
  "server/pyproject.toml": serverVersion,
  "server/uv.lock": serverLockVersion,
})) {
  assert.equal(actual, version, `${name} version must match ${tag}`);
}

console.log(`Release version validated: ${tag}`);
