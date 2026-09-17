/** CI 调度：按 Git 差异选择检查，已有 PR 时去重分支 push；失败不静默跳过验证。 */
import { execFileSync } from "node:child_process";

/** 识别生产代码和构建配置；Markdown 文档不触发客户端编译。 */
export function classifyChanges(files) {
  const ci = files.some((path) => path.startsWith(".github/workflows/") || path.startsWith(".github/scripts/"));
  const client = files.some((path) => path.startsWith("client/") && !path.endsWith(".md"));
  const manifests = files.some((path) => ["client/package.json", "client/bun.lock", "client/bunfig.toml"].includes(path));
  const native = manifests || files.some((path) => path.startsWith("client/src-tauri/") && !path.endsWith(".md"));
  return {
    client: client || ci,
    native: native || ci,
    server: files.some((path) => path.startsWith("server/")) || ci,
    tooling: ci || native || files.some((path) => ["server/pyproject.toml", "server/uv.lock"].includes(path)),
  };
}

/** 获取完整文件列表而非事件内截断列表；删除和重命名同时保留旧路径以避免漏检。 */
export function changedFiles(eventName, payload, git = (args) => execFileSync("git", args, { encoding: "utf8" })) {
  if (eventName === "workflow_dispatch") return null;
  const base = eventName === "pull_request" ? payload.pull_request.base.sha : payload.before;
  if (!base || /^0+$/.test(base)) return git(["ls-files", "-z"]).split("\0").filter(Boolean);
  // 强制推送后旧 SHA 可能已无法获取；保守运行全部检查，不能当作没有改动。
  try { git(["cat-file", "-e", `${base}^{commit}`]); } catch { return null; }
  return git(["diff", "--no-renames", "--name-only", "-z", base, "HEAD"]).split("\0").filter(Boolean);
}

/** 默认分支始终验证集成结果；其他分支只在同一提交已有 PR 时省略 push 重复任务。 */
export async function planCi(github, context, readChanges = changedFiles) {
  const { eventName, payload, repo, ref, sha } = context;
  const defaultPush = eventName === "push" && ref === `refs/heads/${payload.repository.default_branch}`;
  if (eventName === "push" && !defaultPush) {
    const pulls = await github.paginate(github.rest.pulls.list, {
      ...repo, state: "open", head: `${repo.owner}:${ref.replace("refs/heads/", "")}`, per_page: 100,
    });
    if (pulls.some((pr) => pr.head.sha === sha && pr.head.repo?.full_name === `${repo.owner}/${repo.repo}`)) {
      return { run: false, hygiene: false, client: false, native: false, server: false, tooling: false, integration: false, mode: "check" };
    }
  }
  const files = readChanges(eventName, payload);
  const scope = files === null ? { client: true, native: true, server: true, tooling: true } : classifyChanges(files);
  const manual = eventName === "workflow_dispatch";
  const integration = manual && String(payload.inputs?.["integration-tests"] ?? true) === "true";
  const mode = defaultPush || (manual && String(payload.inputs?.["build-installers"]) === "true") ? "package" : "check";
  return { run: true, hygiene: eventName !== "pull_request", ...scope, integration, native: defaultPush ? scope.client : scope.native, mode };
}

/** 汇总仅接受成功或按计划跳过；意外跳过、失败和取消均禁止报告通过。 */
export function assertCiResults(needs) {
  if (needs.scope.result !== "success") throw new Error("CI scope detection did not succeed");
  const outputs = needs.scope.outputs;
  for (const [name, job] of Object.entries(needs)) {
    if (name === "scope") continue;
    const required = outputs.run === "true" && outputs[name === "desktop" ? "native" : name] === "true";
    if (job.result !== (required ? "success" : "skipped")) throw new Error(`${name}: unexpected result ${job.result}`);
  }
}
