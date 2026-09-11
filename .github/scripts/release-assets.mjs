import assert from "node:assert/strict";
import { readdirSync, statSync, readFileSync, writeFileSync } from "node:fs";
import { basename, join } from "node:path";
import { pathToFileURL } from "node:url";

export function collectAssets(root = "release-assets", changelog = "CHANGELOG.md") {
  const platforms = {
    "linux-x64": [".deb", ".AppImage"],
    "windows-x64": [".exe", ".msi"],
    "macos-arm64": [".dmg"],
    "macos-x64": [".dmg"],
  };
  const files = [];
  function walk(dir) {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) walk(path);
      else if (entry.isFile()) files.push(path);
    }
  }
  walk(root);
  const selected = [];
  for (const [platform, extensions] of Object.entries(platforms)) {
    for (const extension of extensions) {
      const matches = files.filter((path) =>
        path.split(/[\\/]/).some((part) => part.startsWith(`intelligent-mix-video-${platform}-`))
        && path.endsWith(extension));
      assert.equal(matches.length, 1, `Expected one ${platform} ${extension} installer`);
      selected.push(matches[0]);
    }
  }
  selected.push(changelog);
  const assets = selected.map((path) => ({ name: basename(path), size: statSync(path).size }));
  assert(assets.every((asset) => asset.size > 0), "Empty release asset");
  assert.equal(new Set(assets.map((asset) => asset.name)).size, assets.length, "Duplicate asset names");
  return assets;
}

export async function publishDraft(github, repo, tag, expected = JSON.parse(readFileSync("release-assets.json", "utf8"))) {
  const { data: release } = await github.rest.repos.getReleaseByTag({ ...repo, tag });
  // Never demote or overwrite an already published release on retry.
  if (!release.draft) return;
  const assets = await github.paginate(github.rest.repos.listReleaseAssets, {
    ...repo, release_id: release.id, per_page: 100,
  });
  for (const wanted of expected) {
    assert(assets.some((asset) => asset.name === wanted.name && asset.size === wanted.size && asset.state === "uploaded"),
      `Missing or incomplete uploaded asset: ${wanted.name}`);
  }
  await github.rest.repos.updateRelease({
    ...repo, release_id: release.id, draft: false, prerelease: false, make_latest: "legacy",
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  writeFileSync("release-assets.json", JSON.stringify(collectAssets(), null, 2) + "\n");
}
