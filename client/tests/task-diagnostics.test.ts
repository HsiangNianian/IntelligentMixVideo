/** 任务诊断回归：隐私、服务归属、任务终态与保留策略；隔离 HTTP/IPC，在 client/ 执行 bun run test task-diagnostics.test.ts。 */
import { afterEach, beforeEach, expect, test } from "bun:test";
import { waitFor } from "@testing-library/react";
import { beginDiagnostic, diagnosticTaskState, openDiagnostics, resetDiagnostics } from "@/lib/diagnostics";
import { create, workEvents } from "@/features/remotion_templates/api";
import { syncBackground } from "@/features/remotion_templates/backgroundSync";
import { record, server, snapshot } from "@/features/remotion_templates/localSessions";
import { remotionJob } from "./remotion-fixtures";
import type { WorkEvent } from "@/features/remotion_templates/model";
import { getComposition } from "@/features/video_composition/api";
import { apiBase, setApiBase } from "@/lib/api-base";
import { fetchMock, mockDesktop } from "./setup";

type Entry = { source: string; message: string };
let entries: Entry[];
let previousBase: string;

beforeEach(async () => {
  previousBase = apiBase();
  entries = [];
  mockDesktop(async (command, args) => {
    if (command === "diagnostics_open") return { path: "/test/diagnostics.log", viewerError: null };
    if (command === "diagnostics_log") entries.push(args as Entry);
    if (command === "remotion_sessions" && args.operation === "registrations_list") return [];
    return {};
  });
  await openDiagnostics();
});
afterEach(() => { resetDiagnostics(); setApiBase(previousBase); });

/** 只筛任务输出，其他请求日志另有完整边界用例。 */
function messages() { return entries.filter(entry => entry.source === "后端任务").map(entry => entry.message); }
/** 取开始记录中的宿主编号，不把后端任务 ID 当作追踪编号。 */
function traceId(message: string) { return /^\[#([0-9a-f]{16})\]/.exec(message)![1]; }

// 场景：任务公开回复和失败详情仍返回业务调用方，但不能进入可分享的诊断日志。
test("任务消息和错误自由文本不写入日志", async () => {
  const job = { id: "private-job", project_id: "work", status: "answered", message: "客户张三的私人诊断结论" };
  fetchMock.mockResolvedValueOnce(Response.json({ work: { id: "work" }, job }));
  expect((await create("私人输入")).job.message).toBe(job.message);
  const task = { taskId: "private-composition", status: "failed", stage: "rendering", error: { code: "ims_failed", message: "客户李四的未公开文案" } };
  fetchMock.mockResolvedValueOnce(Response.json(task));
  expect((await getComposition(task.taskId)).error?.message).toBe(task.error.message);
  expect(JSON.stringify(entries)).not.toMatch(/张三|李四|私人输入/);
  expect(messages().some(message => message.includes("ims_failed"))).toBe(true);
});

// 场景：相同任务 ID 在不同服务或部署前缀下分别关联；默认端口、主机大小写和尾斜杠不创建新任务。
test("服务隔离且后续观察不替换提交归属", () => {
  const submit = beginDiagnostic("测试", "提交 A");
  submit.run(() => diagnosticTaskState("合成", "same", "queued", { service: "https://API.test:443/a/" }));
  const first = messages().find(message => message.includes("开始"))!;
  diagnosticTaskState("合成", "same", "queued", { service: "https://api.test/b" });
  diagnosticTaskState("合成", "same", "queued", { service: "https://other.test/a" });
  const poll = beginDiagnostic("测试", "查询 A");
  poll.run(() => diagnosticTaskState("合成", "same", "processing", { service: "https://api.test/a" }));
  diagnosticTaskState("合成", "same", "succeeded", { service: "https://api.test/a" });
  expect(messages().filter(message => message.includes("开始"))).toHaveLength(3);
  expect(first).toContain(`来自 #${submit.id}`);
  expect(messages().some(message => message.includes(`[#${traceId(first)}]`) && message.includes("状态 succeeded"))).toBe(true);
  expect(messages().filter(message => message.includes("关联操作"))).toHaveLength(0);
});

// 场景：查询使用显式服务；请求等待时用户切换默认地址，旧服务回执仍归原任务。
test("合成回执使用实际请求服务", async () => {
  const response = Promise.withResolvers<Response>();
  setApiBase("https://old.test");
  diagnosticTaskState("合成", "same", "queued", { service: apiBase() });
  const first = messages()[0];
  fetchMock.mockReturnValueOnce(response.promise);
  const pending = getComposition("same", undefined, "https://old.test/");
  setApiBase("https://new.test");
  response.resolve(Response.json({ taskId: "same", status: "succeeded", stage: "completed" }));
  await pending;
  expect(messages().filter(message => message.includes("开始"))).toHaveLength(1);
  expect(messages().at(-1)).toContain(`[#${traceId(first)}]`);
});

// 场景：未打开的会话通过后台分页同步完成，日志也结束原任务；本地写入和 ACK 行为不变。
test("后台事件同步记录终态并保留原任务关联", async () => {
  const job = remotionJob("queued");
  await record({ id: job.project_id, current_version_id: null, created_at: "2026-11-23", request: {} }, job);
  diagnosticTaskState("字效", job.id, job.status, { service: server() });
  const first = messages()[0];
  const page: WorkEvent[] = [{ id: 1, work_id: job.project_id, created_at: "2026-11-23", type: "job.updated", data: {
    ...job, status: "answered", message: "私人后台回复", created_at: "2026-11-23", updated_at: "2026-11-23", parameters: null,
    progress: [{ phase: "answering", started_at: "2026-11-23", ended_at: null, status: "done" }],
  } }];
  fetchMock.mockResolvedValueOnce(Response.json(page)).mockResolvedValueOnce(new Response(null, { status: 204 })).mockResolvedValueOnce(Response.json([]));
  expect(await syncBackground(() => false, new AbortController().signal)).toBe(true);
  expect((await snapshot(job.project_id)).job.status).toBe("answered");
  expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/ack"))).toBe(true);
  expect(messages().filter(message => message.includes("开始 字效任务"))).toHaveLength(1);
  expect(messages().some(message => message.includes(`[#${traceId(first)}]`) && message.includes("：状态 answered；阶段 answering"))).toBe(true);
  expect(JSON.stringify(entries)).not.toContain("私人后台回复");
});

// 场景：分页中首次发现的任务在异步返回后仍关联读取入口，不丢失原操作编号。
test("事件分页首次发现任务时保留读取来源", async () => {
  const origin = beginDiagnostic("测试", "恢复后台任务");
  const job = { ...remotionJob("succeeded"), created_at: "2026-11-23", updated_at: "2026-11-23", parameters: null };
  const page: WorkEvent[] = [{ id: 1, work_id: job.project_id, created_at: "2026-11-23", type: "job.updated", data: job }];
  fetchMock.mockResolvedValueOnce(Response.json(page));
  await origin.run(() => workEvents(job.project_id, 0));
  expect(messages().find(message => message.includes("开始 字效任务"))).toContain(`来自 #${origin.id}`);
});

// 场景：分页请求在旧服务上发出后切换配置，终态仍归旧服务；其他会话、旧编号和普通消息不产生任务记录。
test("事件分页使用请求服务且只观察本会话的新任务事件", async () => {
  const response = Promise.withResolvers<Response>();
  setApiBase("https://old.test");
  diagnosticTaskState("字效", "job-1", "running", { service: apiBase() });
  const first = messages()[0];
  fetchMock.mockReturnValueOnce(response.promise);
  const pending = workEvents("work-1", 3);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  setApiBase("https://new.test");
  diagnosticTaskState("字效", "job-1", "queued", { service: apiBase() });
  const event = { work_id: "work-1", created_at: "2026-11-23" };
  const job = { ...remotionJob("succeeded"), created_at: event.created_at, updated_at: event.created_at, parameters: null };
  const page: WorkEvent[] = [
    { ...event, id: 2, type: "job.updated", data: { ...job, id: "stale" } },
    { ...event, id: 4, work_id: "other-work", type: "job.updated", data: { ...job, id: "other" } },
    { ...event, id: 5, type: "message.created", data: { id: "message-5", role: "assistant", text: "私人消息" } },
    { ...event, id: 6, type: "job.updated", data: job },
  ];
  response.resolve(Response.json(page));
  expect(await pending).toEqual(page);
  expect(messages().filter(message => message.includes("开始 字效任务"))).toHaveLength(2);
  expect(messages().some(message => message.includes(`[#${traceId(first)}]`) && message.includes("：状态 succeeded"))).toBe(true);
  expect(JSON.stringify(entries)).not.toContain("私人消息");
});

// 场景：恶意 ID、状态、阶段或错误码不能注入日志行；元数据过长或结构不合法也不能影响业务。
test("不可信任务元数据不能伪造日志", () => {
  diagnosticTaskState("字效", "bad\n[伪造]", "queued", { service: apiBase() });
  diagnosticTaskState("字效", "a".repeat(200), "queued", { service: apiBase() });
  diagnosticTaskState("字效", "safe", "bad\r\n[伪造]", { service: apiBase(), stage: "rendering\n[伪造]", code: "failure\u001b[31m" });
  expect(messages().join(" ")).not.toMatch(/伪造|\r|\n|\u001b|a{200}/);
});

// 场景：澄清已经结束本次执行；终态后重复事件不再输出，安全错误码变化仍有补充记录。
test("澄清结束且终态补充按安全字段去重", () => {
  const scope = { service: apiBase() };
  diagnosticTaskState("字效", "clarify", "queued", scope);
  diagnosticTaskState("字效", "clarify", "needs_input", scope);
  diagnosticTaskState("字效", "clarify", "needs_input", scope);
  expect(messages().filter(message => message.includes("：状态 needs_input") && message.includes("ms）"))).toHaveLength(1);
  diagnosticTaskState("合成", "failure", "failed", { ...scope, code: "matching_failed" });
  diagnosticTaskState("合成", "failure", "failed", { ...scope, code: "storage_error" });
  diagnosticTaskState("合成", "failure", "failed", { ...scope, code: "storage_error" });
  expect(messages().filter(message => message.includes("storage_error"))).toHaveLength(1);
});

// 场景：超过历史容量的终态不应淘汰仍在运行的旧任务，否则最终回执会丢掉提交链路。
test("只淘汰已结束任务", () => {
  const scope = { service: apiBase() };
  diagnosticTaskState("字效", "active", "running", scope);
  const first = messages()[0];
  for (let index = 0; index < 205; index++) diagnosticTaskState("字效", `done-${index}`, "succeeded", scope);
  diagnosticTaskState("字效", "active", "succeeded", scope);
  expect(messages().filter(message => message.includes("开始 字效任务 active"))).toHaveLength(1);
  expect(messages().at(-1)).toContain(`[#${traceId(first)}]`);
});
