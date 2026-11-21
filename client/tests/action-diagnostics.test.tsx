/** 动作链回归：真实点击/快捷键、异步 IPC、更新阻塞和任务 SSE 的日志，不执行实际下载、安装或外部请求。 */
import { afterEach, expect, test } from "bun:test";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beginDiagnostic, diagnosticInvoke, openDiagnostics, resetDiagnostics, startDiagnostics } from "@/lib/diagnostics";
import { initializeUpdates, tryApplyUpdate } from "@/features/updates/store";
import { ClientUpdate } from "@/features/updates/ClientUpdate";
import * as api from "@/features/remotion_templates/api";
import { events } from "@/features/remotion_templates/events";
import { apiBase, setApiBase } from "@/lib/api-base";
import { fetchMock, mockDesktop } from "./setup";

type Entry = { source: string; message: string; level: string };
afterEach(() => resetDiagnostics());

/** 收集诊断 IPC；业务命令保持原参数并交给场景替身。 */
function desktop(call: (command: string, args: Record<string, unknown>) => Promise<unknown> = async () => null) {
  const entries: Entry[] = [];
  mockDesktop(async (command, args) => {
    if (command === "diagnostics_open") return { path: "/test/diagnostics.log", viewerError: null };
    if (command === "diagnostics_log") { entries.push(args as Entry); return null; }
    return call(command, args);
  });
  startDiagnostics();
  return entries;
}

/** 从日志中提取宿主编号，断言失败时不静默回退空值。 */
function id(entry: Entry) {
  const found = /#([0-9a-f]{16})/.exec(entry.source + entry.message);
  expect(found).not.toBeNull();
  return found![1];
}

// 场景：点击 A 后等待，再点击 B；A 的迟到命令仍属于 A，原生参数与返回值原样保留，密钥不进入日志。
test("并发动作保留原编号且捕获快捷键", async () => {
  const gate = Promise.withResolvers<void>();
  const entries = desktop(async (_command, args) => {
    expect(args.values).toEqual({ api_key: "private-key" });
    return { secret: "private-result" };
  });
  await openDiagnostics();
  let work!: Promise<unknown>;
  render(<><button onClick={() => {
    const trace = beginDiagnostic("前端测试", "动作 A");
    work = gate.promise.then(() => trace.run(() => diagnosticInvoke("local_settings", { id: "test", values: { api_key: "private-key" } }))).then(value => { trace.finish("已保存"); return value; });
  }}>动作 A</button><button>动作 B</button></>);
  fireEvent.click(screen.getByText("动作 A"));
  await Promise.resolve();
  fireEvent.click(screen.getByText("动作 B"));
  fireEvent.keyDown(screen.getByText("动作 B"), { key: "s", ctrlKey: true });
  gate.resolve();
  expect(await work).toEqual({ secret: "private-result" });
  const clickA = entries.find(entry => entry.message === "点击 button「动作 A」")!;
  const actionA = entries.find(entry => entry.message.includes("开始 动作 A"))!;
  const native = entries.find(entry => entry.message.includes("开始 local_settings"))!;
  expect(actionA.message).toContain(`来自 #${id(clickA)}`);
  expect(native.message).toContain(`来自 #${id(actionA)}`);
  expect(entries.some(entry => entry.message.includes("按键 Ctrl+s"))).toBe(true);
  expect(entries.some(entry => entry.message.includes(`[#${id(native)}]`) && entry.message.includes("执行完成"))).toBe(true);
  expect(JSON.stringify(entries)).not.toMatch(/private-key|private-result/);
});

// 场景：检查更新的 IPC 被捕获处理，日志仍给出真实失败原因；下载被草稿阻止时写明等待并记录取消，不误报成功。
test("更新日志包含失败、草稿阻塞和取消", async () => {
  let fail = true;
  const operations: string[] = [];
  const entries = desktop(async (command, args) => {
    if (command === "remotion_sessions") return [];
    if (command !== "client_update") return 1;
    operations.push(String(args.operation));
    if (args.operation === "status") return { enabled: true, notice: "" };
    if (args.operation === "check") {
      if (fail) throw new Error("签名验证失败 token=private-token");
      return { version: "9.0.0", mode: "native" };
    }
    return null;
  });
  await openDiagnostics();
  await initializeUpdates();
  render(<ClientUpdate />);
  await waitFor(() => expect(entries.some(entry => entry.message.includes("检查失败：Error: 签名验证失败"))).toBe(true));
  expect(JSON.stringify(entries)).not.toContain("private-token");
  fail = false;
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "检查更新" })));
  const draft = document.createElement("input");
  draft.dataset.updateDraft = "测试未保存模板";
  document.body.append(draft);
  try {
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "一键更新" })));
    await act(async () => { await tryApplyUpdate(); });
    expect(entries.filter(entry => entry.message.includes("状态 preparing：等待处理：测试未保存模板"))).toHaveLength(1);
    expect(operations).not.toContain("download");
    fireEvent.click(screen.getByRole("button", { name: "取消此次更新" }));
    expect(entries.some(entry => entry.message.includes("用户取消更新等待"))).toBe(true);
  } finally { draft.remove(); }
});

// 场景：任务提交前需异步读取设置/设备，点击仍能连到 HTTP 请求；SSE 建立不算任务成功，公开状态改变才产生任务终态。
test("字效提交经过异步配置后关联请求和后续 SSE 任务", async () => {
  const previousBase = apiBase();
  setApiBase("http://api.test:8000");
  const entries = desktop(async command => command === "local_settings" ? { remotion_agent: { api_key: "config-secret" } } : null);
  const job = { id: "job-trace", project_id: "work-trace", status: "queued", result_version_id: null, questions: [], message: null };
  fetchMock.mockResolvedValue(Response.json({ work: { id: "work-trace" }, job }));
  await openDiagnostics();
  let submission!: ReturnType<typeof api.create>;
  render(<button onClick={() => { submission = api.create("private-instruction"); }}>生成</button>);
  try {
    fireEvent.click(screen.getByText("生成"));
    await submission;
    const click = entries.find(entry => entry.message === "点击 button「生成」")!;
    const model = entries.find(entry => entry.message.includes("开始 读取模型配置并请求"))!;
    const request = entries.find(entry => entry.message.includes("开始 POST /works"))!;
    const http = entries.find(entry => entry.source === "前端请求" && entry.message.includes("开始 POST"))!;
    expect(model.message).toContain(`来自 #${id(click)}`);
    expect(request.message).toContain(`来自 #${id(model)}`);
    expect(http.message).toContain(`来自 #${id(request)}`);
    const task = entries.find(entry => entry.message.includes("开始 字效任务 job-trace"))!;
    expect(task.message).toContain(`来自 #${id(request)}`);
    const encoder = new TextEncoder();
    let stream!: ReadableStreamDefaultController<Uint8Array>;
    fetchMock.mockResolvedValue(new Response(new ReadableStream<Uint8Array>({ start(controller) { stream = controller; } }), { headers: { "content-type": "text/event-stream" } }));
    const controller = new AbortController();
    const connection = events("work-trace", 0, controller.signal);
    const next = connection.next();
    await waitFor(() => expect(entries.some(entry => entry.message.includes("SSE 已建立"))).toBe(true));
    expect(entries.some(entry => entry.message.includes("状态 succeeded"))).toBe(false);
    stream.enqueue(encoder.encode(`id: 1\nevent: job.updated\ndata: ${JSON.stringify({ id: 1, work_id: "work-trace", type: "job.updated", data: { ...job, status: "succeeded", message: "渲染验收通过" } })}\n\n`));
    expect((await next).value?.type).toBe("job.updated");
    await connection.return(undefined);
    expect(entries.some(entry => entry.source === "后端任务" && entry.message.includes(`[#${id(task)}]`) && entry.message.includes("状态 succeeded"))).toBe(true);
    expect(JSON.stringify(entries)).not.toMatch(/private-instruction|config-secret/);
  } finally { setApiBase(previousBase); }
});
