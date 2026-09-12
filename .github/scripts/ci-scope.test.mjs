/** CI 调度回归测试；在仓库根目录执行 bun test ./.github/scripts，使用隔离 Git 仓库与 API 替身。 */
import { test } from "bun:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { execFileSync } from "node:child_process";
import { classifyChanges, changedFiles, planCi, assertCiResults } from "./ci-scope.mjs";

for (const [description, files, expected] of [
  ["documentation", ["README.md", "client/README.md"], [false, false, false, false]],
  ["frontend", ["client/src/pages/HomePage.tsx"], [true, false, false, false]],
  ["frontend assets", ["client/public/icon.svg"], [true, false, false, false]],
  ["server", ["server/tests/test_api.py", "server/uv.lock"], [false, false, true, false]],
  ["native", ["client/src-tauri/src/lib.rs"], [true, true, false, true]],
  ["native config", ["client/src-tauri/tauri.conf.json"], [true, true, false, true]],
  ["dependency update", ["client/bun.lock"], [true, true, false, true]],
  ["workflow", [".github/workflows/validation.yml"], [true, true, true, true]],
  ["CI script", [".github/scripts/ci-scope.mjs"], [true, true, true, true]],
]) {
  test(`scope: ${description} selects the required checks`, () => {
    assert.deepEqual(Object.values(classifyChanges(files)), expected);
  });
}

/** 模拟同仓库事件；默认分支特意不用 main，验证未硬编码分支名。 */
function context(eventName, branch = "dev") {
  return {
    eventName, ref: `refs/heads/${branch}`, sha: "head",
    repo: { owner: "owner", repo: "project" },
    payload: { repository: { default_branch: "trunk" } },
  };
}

/** API 替身仅提供 PR 列表；测试不会联系 GitHub。 */
function github(pulls = []) {
  return { rest: { pulls: { list() {} } }, async paginate() { return pulls; } };
}

test("push with matching open PR skips duplicate work", async () => {
  const plan = await planCi(github([{ head: { sha: "head", repo: { full_name: "owner/project" } } }]), context("push"), () => {
    throw new Error("Duplicate push must not read or build changed files");
  });
  assert.equal(plan.run, false);
  assert(Object.entries(plan).filter(([key]) => !["run", "mode"].includes(key)).every(([, value]) => value === false));
});

test("stale or fork PR cannot suppress a branch push", async () => {
  for (const head of [
    { sha: "old", repo: { full_name: "owner/project" } },
    { sha: "head", repo: { full_name: "fork/project" } },
  ]) {
    const plan = await planCi(github([{ head }]), context("push"), () => ["server/src/server/app.py"]);
    assert.equal(plan.run, true);
    assert.equal(plan.server, true);
    assert.equal(plan.native, false);
    assert.equal(plan.hygiene, true);
  }
});

test("default branch packages integrated client changes even with an open PR", async () => {
  const api = github();
  api.paginate = () => { throw new Error("Default branch must not be deduplicated"); };
  const plan = await planCi(api, context("push", "trunk"), () => ["client/src/App.tsx"]);
  assert.equal(plan.mode, "package");
  assert.equal(plan.native, true);
});

test("frontend PR builds only frontend; native PR checks without packaging", async () => {
  const frontend = await planCi(github(), context("pull_request"), () => ["client/src/App.tsx"]);
  assert.equal(frontend.client, true);
  assert.equal(frontend.native, false);
  assert.equal(frontend.hygiene, false);
  const native = await planCi(github(), context("pull_request"), () => ["client/src-tauri/Cargo.lock"]);
  assert.equal(native.native, true);
  assert.equal(native.mode, "check");
});

test("manual or unavailable baseline checks everything; API errors fail closed", async () => {
  const plan = await planCi(github(), context("workflow_dispatch"), () => null);
  assert(Object.entries(plan).filter(([key]) => key !== "mode").every(([, value]) => value === true));
  const api = github();
  api.paginate = () => { throw new Error("GitHub unavailable"); };
  await assert.rejects(planCi(api, context("push")), /GitHub unavailable/);
});

test("Git scope covers deletions, renames, Unicode, initial and force pushes", () => {
  const dir = mkdtempSync(join(tmpdir(), "imv-ci-scope-"));
  const git = (args) => execFileSync("git", ["-C", dir, ...args], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
  try {
    git(["init"]);
    git(["config", "user.name", "test"]);
    git(["config", "user.email", "test@example.test"]);
    writeFileSync(join(dir, "old.ts"), "old\n");
    writeFileSync(join(dir, "removed.rs"), "old\n");
    git(["add", "."]);
    git(["commit", "-m", "test: baseline"]);
    const base = git(["rev-parse", "HEAD"]).trim();
    git(["mv", "old.ts", "新 文件.ts"]);
    git(["rm", "removed.rs"]);
    git(["commit", "-m", "test: rename and remove"]);
    const expected = ["old.ts", "removed.rs", "新 文件.ts"].sort();
    assert.deepEqual(changedFiles("push", { before: base }, git).sort(), expected);
    assert.deepEqual(changedFiles("pull_request", { pull_request: { base: { sha: base } } }, git).sort(), expected);
    assert.deepEqual(changedFiles("push", { before: "0".repeat(40) }, git), ["新 文件.ts"]);
    assert.equal(changedFiles("push", { before: "f".repeat(40) }, git), null);
    assert.equal(changedFiles("workflow_dispatch", {}, git), null);
  } finally {
    assert.equal(dirname(resolve(dir)), resolve(tmpdir()));
    assert(dir.includes("imv-ci-scope-"));
    rmSync(dir, { recursive: true, force: true });
  }
});

test("aggregate rejects failures, cancellations and accidentally skipped required work", () => {
  const needs = {
    scope: { result: "success", outputs: { run: "true", client: "true", native: "false", server: "false", tooling: "false", hygiene: "false" } },
    client: { result: "success" }, desktop: { result: "skipped" },
    server: { result: "skipped" }, tooling: { result: "skipped" }, hygiene: { result: "skipped" },
  };
  assertCiResults(needs);
  for (const result of ["failure", "cancelled", "skipped"]) {
    assert.throws(() => assertCiResults({ ...needs, client: { result } }), /client/);
  }
  assert.throws(() => assertCiResults({ ...needs, scope: { result: "failure" } }), /scope/);
});

test("workflow keeps PR gate unfiltered and packaging exclusive to package mode", () => {
  const validation = Bun.YAML.parse(readFileSync(".github/workflows/validation.yml", "utf8"));
  const build = Bun.YAML.parse(readFileSync(".github/workflows/client-build.yml", "utf8"));
  assert.equal(validation.on.pull_request, null);
  assert.equal(validation.on.push.paths, undefined);
  assert.equal(validation.jobs.result.if, "always()");
  assert(validation.jobs.result.name.includes("'CI result'"));
  assert(validation.jobs.result.name.includes("'Push result'"));
  assert.deepEqual(validation.jobs.result.needs.sort(), ["scope", "hygiene", "tooling", "client", "server", "desktop"].sort());
  assert.equal(build.on.push, undefined);
  assert.equal(build.on.pull_request, undefined);
  assert.equal(build.on.workflow_call.inputs["build-mode"].default, "package");
  for (const name of ["Build desktop installers", "Upload installers"]) {
    assert.equal(build.jobs.build.steps.find((step) => step.name === name).if, "inputs.build-mode != 'check'");
  }
});
