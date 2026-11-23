/**
 * 打开日志窗口后才启动的页面采集：请求全生命周期、内存趋势、窗口与显示器变化；关闭后（重置）不再记录，
 * 打开前也不替换 fetch。IPC 由 mockIPC 模拟，网络由 setup 的 fetch 替身代替，时钟与计时器由替身控制；执行 bun run test。
 */
import { afterEach, beforeEach, expect, jest, spyOn, test } from "bun:test";
import { waitFor } from "@testing-library/react";
import { openDiagnostics, resetDiagnostics, startDiagnostics } from "@/lib/diagnostics";
import { apiBase, setApiBase } from "@/lib/api-base";
import { fetchMock, mockDesktop } from "./setup";

type Entry = { level: string; source: string; message: string };
let entries: Entry[] = [];
let clock = 0;
let previousBase = "";

beforeEach(() => {
  resetDiagnostics();
  entries = [];
  clock = 0;
  previousBase = apiBase();
  setApiBase("http://api.test:8000");
  spyOn(performance, "now").mockImplementation(() => clock);
  mockDesktop(async (command, args) => {
    if (command === "diagnostics_open") return { path: "/data/diagnostics.log", viewerError: null };
    if (command === "diagnostics_log") entries.push(args as Entry);
    return null;
  });
});

afterEach(() => {
  jest.useRealTimers();
  resetDiagnostics();
  setApiBase(previousBase);
});

/** 某来源的全部记录内容。 */
const messages = (source: string) => entries.filter((entry) => entry.source === source).map((entry) => entry.message.replace(/^\[#[0-9a-f]+\] /, ""));

// 场景：模板保存失败、资产预览 404、后端连不上时，日志写明请求、状态码、用时与服务端说明；地址去掉查询参数，
// 二进制正文不读，主动取消单独标记，IPC 不记录，成功与失败都有开始和终态。日志窗口打开前不替换 fetch。
test("记录请求开始、响应、失败和取消", async () => {
  const unpatched = globalThis.fetch;
  startDiagnostics();
  expect(globalThis.fetch).toBe(unpatched);
  await openDiagnostics();
  expect(globalThis.fetch === unpatched).toBe(false);
  fetchMock.mockImplementation((async (input: RequestInfo | URL) => {
    const url = String(input);
    clock += 120;
    if (url.includes("/template")) return Response.json({ detail: "模板名称已存在" }, { status: 409 });
    if (url.includes("/preview")) return new Response(new Uint8Array([1, 2]), { status: 404, headers: { "content-type": "application/x-protobuf" } });
    if (url.includes("/fonts")) { clock += 3500; return new Response("ok"); }
    if (url.includes("/abort")) throw new DOMException("aborted", "AbortError");
    if (url.includes("ipc.localhost")) throw new TypeError("ipc down");
    throw new TypeError("Failed to fetch");
  }) as typeof fetch);
  await fetch("http://api.test:8000/template?token=secret", { method: "POST" });
  await fetch("http://api.test:8000/api/sprites/s1/preview");
  await fetch("https://cdn.test/fonts/a.ttf");
  await fetch("http://api.test:8000/works").catch(() => {});
  await fetch("http://api.test:8000/abort").catch(() => {});
  await fetch("http://ipc.localhost/diagnostics_log").catch(() => {});
  await waitFor(() => expect(messages("后端响应")).toContain("错误说明：模板名称已存在"));
  expect(messages("后端响应")).toContain("POST api.test:8000/template → HTTP 409");
  expect(messages("后端响应")).toContain("GET api.test:8000/api/sprites/s1/preview → HTTP 404");
  expect(messages("前端请求").filter(message => message.startsWith("开始"))).toHaveLength(5);
  expect(messages("前端请求")).toContain("GET cdn.test/fonts/a.ttf：已收到成功响应（3.6 秒）");
  expect(messages("前端请求")).toContain("GET api.test:8000/works：未获得 HTTP 响应（网络、跨域或连接故障）：TypeError: Failed to fetch（120 ms）");
  expect(messages("前端请求").some(message => message.includes("请求已取消/超时"))).toBe(true);
  expect(entries.find((entry) => entry.message.includes("→ HTTP 409"))?.level).toBe("警告");
  expect(entries.map(entry => entry.message).join(" ")).not.toMatch(/token=secret|ipc down/);
  resetDiagnostics();
  expect(globalThis.fetch).toBe(unpatched);
});

// 场景：长时间使用后越来越卡。日志窗口打开后每分钟记录页面元素与 iframe 数量（Chromium 另有 JS 堆），打开前不记录。
test("每分钟记录内存趋势", async () => {
  jest.useFakeTimers();
  startDiagnostics();
  jest.advanceTimersByTime(60_000);
  await openDiagnostics();
  document.body.append(document.createElement("iframe"));
  jest.advanceTimersByTime(60_000);
  jest.advanceTimersByTime(120_000);
  jest.useRealTimers();
  await waitFor(() => expect(messages("内存")).toHaveLength(1));
  expect(messages("内存")[0]).toMatch(/^(JS 堆 .+，)?页面元素 \d+ 个，iframe 1 个$/);
  document.body.replaceChildren();
});

// 场景：先读能力回执再发诊断头；旧后端和第三方资源保持原请求，body、signal 与已有配置头不变。
test("能力协商只向支持诊断的业务后端发送关联编号", async () => {
  const received: RequestInit[] = [];
  fetchMock.mockImplementation((async (_input: RequestInfo | URL, init?: RequestInit) => {
    received.push(init ?? {});
    return Response.json({ ok: true }, { headers: { "X-IMV-Diagnostics": "1", "X-IMV-Elapsed-Ms": "12.5" } });
  }) as typeof fetch);
  await openDiagnostics();
  await fetch("http://api.test:8000/ready");
  const controller = new AbortController();
  const request = new Request("http://api.test:8000/save", { method: "POST", body: "original", headers: { "X-Remotion-Config": "private-config" }, signal: controller.signal });
  const response = await fetch(request);
  await fetch("https://cdn.test/font");
  expect(new Headers(received[0].headers).has("X-IMV-Request-ID")).toBe(false);
  const headers = new Headers(received[1].headers);
  const id = headers.get("X-IMV-Request-ID");
  expect(id).toMatch(/^[0-9a-f]{16}$/);
  expect(headers.get("X-Remotion-Config")).toBe("private-config");
  expect(await request.text()).toBe("original");
  expect(await response.json()).toEqual({ ok: true });
  expect(new Headers(received[2].headers).has("X-IMV-Request-ID")).toBe(false);
  expect(entries.some(entry => entry.source === "后端响应" && entry.message.includes(`[#${id}]`) && entry.message.includes("后端处理 12.5 ms"))).toBe(true);
  expect(entries.map(entry => entry.message).join(" ")).not.toContain("private-config");
});

// 场景：旧后端未声明支持，不尝试新增头或重发写入；错误 JSON 的 input/ctx 和凭据不进入日志，原响应照常可读。
test("旧服务请求不变且错误说明不泄露回显输入", async () => {
  const body = { detail: [{ msg: "字段无效", input: { secret: "input-secret" }, ctx: { token: "context-secret" } }] };
  fetchMock.mockResolvedValue(Response.json(body, { status: 422 }));
  await openDiagnostics();
  const response = await fetch("http://api.test:8000/save?token=url-secret", { method: "POST", body: "private-input" });
  expect(await response.json()).toEqual(body);
  await waitFor(() => expect(messages("后端响应")).toContain("错误说明：字段无效"));
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(new Headers(fetchMock.mock.calls[0][1]?.headers).has("X-IMV-Request-ID")).toBe(false);
  expect(entries.map(entry => entry.message).join(" ")).not.toMatch(/input-secret|context-secret|url-secret|private-input/);
});

// 场景：画面突然变糊或预览暂停。记录页面转入后台与回到前台、进出全屏，以及停止调整一秒后的窗口大小；重置后不再记录。
test("记录窗口与显示器变化", async () => {
  jest.useFakeTimers();
  await openDiagnostics();
  Object.defineProperty(document, "hidden", { configurable: true, value: true });
  document.dispatchEvent(new Event("visibilitychange"));
  window.dispatchEvent(new Event("resize"));
  window.dispatchEvent(new Event("resize"));
  jest.advanceTimersByTime(1000);
  resetDiagnostics();
  document.dispatchEvent(new Event("visibilitychange"));
  jest.useRealTimers();
  Reflect.deleteProperty(document, "hidden");
  await waitFor(() => expect(messages("窗口")).toHaveLength(2));
  expect(messages("窗口")).toEqual([
    "页面转入后台（最小化或被遮挡），预览会自动暂停",
    `窗口大小 ${window.innerWidth}×${window.innerHeight}`,
  ]);
});

// 场景：后端返回大体积或不会结束的错误正文。只读取开头并在达到上限后取消，失败记录仍能写出，不会等待整个响应。
test("失败正文只读开头，不等待整个响应", async () => {
  const body = new ReadableStream<Uint8Array>({
    start(controller) { controller.enqueue(new TextEncoder().encode("x".repeat(4096))); },
  });
  fetchMock.mockImplementation((async () => new Response(body, { status: 500, headers: { "content-type": "text/plain" } })) as unknown as typeof fetch);
  await openDiagnostics();
  await fetch("http://api.test:8000/stream");
  await waitFor(() => expect(messages("后端响应")).toContain(`错误说明：${"x".repeat(200)}…`));
  expect(messages("后端响应")).toContain("GET api.test:8000/stream → HTTP 500");
});

// 场景：部分 WebView 的 PerformanceObserver 拒绝观察长帧。打开日志窗口仍应成功，请求等其他采集照常启动。
test("长帧观察失败不影响其他页面采集", async () => {
  const original = Reflect.get(globalThis, "PerformanceObserver");
  class BrokenObserver {
    static supportedEntryTypes = ["long-animation-frame"];
    observe() { throw new Error("unsupported"); }
    disconnect() {}
  }
  Reflect.set(globalThis, "PerformanceObserver", BrokenObserver);
  try {
    fetchMock.mockImplementation((async () => { throw new TypeError("Failed to fetch"); }) as unknown as typeof fetch);
    await openDiagnostics();
    await fetch("http://api.test:8000/works").catch(() => {});
    expect(messages("前端请求")).toHaveLength(2);
    expect(messages("前端请求")[1]).toBe("GET api.test:8000/works：未获得 HTTP 响应（网络、跨域或连接故障）：TypeError: Failed to fetch（0 ms）");
  } finally {
    Reflect.set(globalThis, "PerformanceObserver", original);
  }
});
