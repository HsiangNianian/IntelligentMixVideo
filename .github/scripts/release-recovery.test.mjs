import { test } from "bun:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";
import { collectAssets, publishDraft } from "./release-assets.mjs";
import { commitChangelog, mergeChangelog } from "./commit-changelog.mjs";

function temp(callback) {
  const dir = mkdtempSync(join(tmpdir(), "imv-recovery-test-"));
  try { return callback(dir); } finally {
    assert.equal(dirname(resolve(dir)), resolve(tmpdir()));
    assert(dir.includes("imv-recovery-test-"));
    rmSync(dir, { recursive: true, force: true });
  }
}

test("release manifest requires all six installers and a nonempty changelog", () => temp((dir) => {
  const assets = join(dir, "assets");
  const platforms = {
    "linux-x64": ["linux.deb", "linux.AppImage"],
    "windows-x64": ["windows.exe", "windows.msi"],
    "macos-arm64": ["arm.dmg"],
    "macos-x64": ["intel.dmg"],
  };
  for (const [platform, names] of Object.entries(platforms)) {
    const folder = join(assets, `intelligent-mix-video-${platform}-sha`);
    mkdirSync(folder, { recursive: true });
    for (const name of names) writeFileSync(join(folder, name), "installer");
  }
  const log = join(dir, "CHANGELOG.md");
  writeFileSync(log, "# Changelog");
  assert.equal(collectAssets(assets, log).length, 7);
  writeFileSync(join(assets, "intelligent-mix-video-macos-arm64-sha/arm.dmg"), "");
  assert.throws(() => collectAssets(assets, log), /Empty release asset/);
  rmSync(join(assets, "intelligent-mix-video-macos-arm64-sha/arm.dmg"));
  assert.throws(() => collectAssets(assets, log), /Expected one macos-arm64/);
}));

test("partial uploads stay private; retry publishes only after verification", async () => {
  let published = false;
  let uploaded = [{ name: "installer", size: 1, state: "starter" }];
  const github = {
    rest: { repos: {
      async getReleaseByTag() { throw new Error("Drafts cannot be looked up by tag"); },
      async getRelease(input) {
        assert.equal(input.release_id, 1);
        return { data: { id: 1, tag_name: "v0.2.0", draft: !published } };
      },
      listReleaseAssets() {},
      async updateRelease(input) { assert.equal(input.draft, false); published = true; },
    } },
    async paginate() { return uploaded; },
  };
  const expected = [{ name: "installer", size: 2 }];
  await assert.rejects(publishDraft(github, {}, "v0.2.0", 1, expected), /incomplete/);
  assert.equal(published, false);
  uploaded = [{ name: "installer", size: 2, state: "uploaded" }];
  await publishDraft(github, {}, "v0.2.0", 1, expected);
  assert.equal(published, true);
  github.paginate = () => { throw new Error("Published release must be left unchanged"); };
  await publishDraft(github, {}, "v0.2.0", 1, expected);
  await assert.rejects(publishDraft(github, {}, "v0.3.0", 1, expected), /does not match/);
  await assert.rejects(publishDraft(github, {}, "v0.2.0", NaN, expected), /Missing draft release ID/);
});

const generated = "# Changelog\n\n## [v0.2.0] - 2026-09-11\n### Features\n- new feature\n\n[v0.2.0]: https://example.test/compare\n";
test("changelog merge preserves other entries and is idempotent", () => {
  const current = "# Changelog\n\n## [v0.1.0] - 2026-09-10\n- initial\n";
  const merged = mergeChangelog(current, generated, "v0.2.0");
  assert(merged.includes("- initial"));
  assert(merged.includes("- new feature"));
  assert.equal(mergeChangelog(merged, generated, "v0.2.0"), merged);
  assert.throws(() => mergeChangelog(current, generated, "v0.3.0"), /missing/);
});

test("out-of-order tag completion keeps changelog versions sorted", () => {
  const current = "# Changelog\n\n## [v0.3.0] - 2026-09-12\n- newest\n\n## [v0.1.0] - 2026-09-10\n- oldest\n\n[v0.1.0]: old-link\n";
  const merged = mergeChangelog(current, generated, "v0.2.0");
  assert(merged.indexOf("## [v0.3.0]") < merged.indexOf("## [v0.2.0]"));
  assert(merged.indexOf("## [v0.2.0]") < merged.indexOf("## [v0.1.0]"));
  assert(merged.includes("[v0.1.0]: old-link"));
});

function git(repo, ...args) {
  const result = spawnSync("git", ["-C", repo, ...args], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  return result.stdout.trim();
}

test("changelog push retries a real concurrent branch advance without losing files", () => temp((dir) => {
  const remote = join(dir, "origin.git");
  const writer = join(dir, "writer");
  const runner = join(dir, "runner");
  mkdirSync(remote);
  git(remote, "init", "--bare", "--initial-branch=main");
  git(dir, "clone", remote, writer);
  git(writer, "config", "user.name", "test");
  git(writer, "config", "user.email", "test@example.test");
  writeFileSync(join(writer, "CHANGELOG.md"), "# Changelog\n");
  git(writer, "add", ".");
  git(writer, "commit", "-m", "chore: initial");
  git(writer, "push", "origin", "main");
  git(dir, "clone", remote, runner);
  let tries = 0;
  commitChangelog({
    repo: runner, branch: "main", tag: "v0.2.0", generated,
    beforePush(attempt) {
      tries++;
      if (attempt !== 0) return;
      writeFileSync(join(writer, "README.md"), "concurrent source change\n");
      writeFileSync(join(writer, "CHANGELOG.md"), "# Changelog\n\n## [v0.1.0] - 2026-09-10\n- concurrent entry\n");
      git(writer, "add", ".");
      git(writer, "commit", "-m", "docs: concurrent change");
      git(writer, "push", "origin", "main");
    },
  });
  assert.equal(tries, 2);
  const content = git(remote, "show", "main:CHANGELOG.md");
  assert(content.includes("concurrent entry"));
  assert(content.includes("new feature"));
  assert.equal(git(remote, "show", "main:README.md"), "concurrent source change");
  const head = git(remote, "rev-parse", "main");
  commitChangelog({ repo: runner, branch: "main", tag: "v0.2.0", generated });
  assert.equal(git(remote, "rev-parse", "main"), head);
  assert.equal(git(runner, "status", "--porcelain"), "");
}));

test("workflow keeps draft uploads separate from publication and isolates tags", () => {
  const workflow = Bun.YAML.parse(readFileSync(".github/workflows/release.yml", "utf8"));
  assert.equal(workflow.concurrency.group, "client-release-${{ github.ref }}");
  assert.equal(workflow.concurrency["cancel-in-progress"], false);
  const steps = workflow.jobs.publish.steps;
  const upload = steps.find((step) => step.uses === "ncipollo/release-action@v1");
  assert.equal(upload.with.draft, true);
  assert.equal(upload.with.skipIfReleaseExists, true);
  assert.equal(upload.id, "upload");
  const publish = steps.find((step) => step.name === "Verify uploaded assets and publish");
  assert.equal(publish.env.RELEASE_ID, "${{ steps.upload.outputs.id }}");
  assert(steps.findIndex((step) => step.name === "Verify uploaded assets and publish") > steps.indexOf(upload));
});
