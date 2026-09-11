import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";

const tag = process.env.RELEASE_TAG ?? "";
assert.match(tag, /^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/, "Use a stable release tag such as v0.1.0");
const version = tag.slice(1);
// WiX/MSI uses an 8-bit major/minor and a 16-bit patch version.
const [major, minor, patch] = version.split(".").map(Number);
assert(major <= 255 && minor <= 255 && patch <= 65535, "Version exceeds Windows MSI limits");
if (process.argv.includes("--tag-only")) {
  console.log(`Release tag validated: ${tag}`);
  process.exit(0);
}

const readJson = (path) => JSON.parse(readFileSync(path, "utf8"));
const packageJson = readJson("client/package.json");
// bun.lock omits the root package version and must stay byte-for-byte unchanged.
// The build workflow verifies it with a frozen install after version injection.
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

if (process.argv.includes("--write")) {
  // Check all inputs before changing any files. Never refresh dependency versions.
  assert(cargoName && cargoVersion && cargoLockVersion, "Cannot find the client package in Cargo manifests");
  packageJson.version = version;
  tauri.version = version;
  const replaceVersion = (section) =>
    section.replace(/^(version\s*=\s*)"[^"]+"/m, `$1"${version}"`);
  const updates = {
    "client/package.json": JSON.stringify(packageJson, null, 2) + "\n",
    "client/src-tauri/tauri.conf.json": JSON.stringify(tauri, null, 2) + "\n",
    "client/src-tauri/Cargo.toml": cargoToml.replace(cargoPackage, replaceVersion(cargoPackage)),
    "client/src-tauri/Cargo.lock": cargoLock.replace(cargoLockPackage, replaceVersion(cargoLockPackage)),
  };
  for (const [path, content] of Object.entries(updates)) {
    writeFileSync(path, content);
  }
  console.log(`Client manifests and lockfiles updated to ${version}`);
  process.exit(0);
}

for (const [name, actual] of Object.entries({
  "package.json": packageJson.version,
  "tauri.conf.json": tauri.version,
  "Cargo.toml": cargoVersion,
  "Cargo.lock": cargoLockVersion,
})) {
  assert.equal(actual, version, `${name} version must match ${tag}`);
}

console.log(`Release version validated: ${tag}`);
