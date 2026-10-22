/**
 * 打开日志窗口后才启动的页面采集：请求失败与慢请求、内存趋势、窗口与显示器变化；关闭后（重置）不再记录，
 * 打开前也不替换 fetch。IPC 由 mockIPC 模拟，网络由 setup 的 fetch 替身代替，时钟与计时器由替身控制；执行 bun run test。
 */
import { afterEach, beforeEach, expect, jest, spyOn, test } from "bun:test";
import { waitFor } from "@testing-library/react";
import { openDiagnostics, resetDiagnostics, startDiagnostics } from "@/lib/diagnostics";
import { fetchMock, mockDesktop } from "./setup";

type Entry = { level: string; source: string; message: string };
let entries: Entry[] = [];
let clock = 0;

beforeEach(() => {
  resetDiagnostics();
  entries = [];
  clock = 0;
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
});

/** 某来源的全部记录内容。 */
const messages = (source: string) => entries.filter((entry) => entry.source === source).map((entry) => entry.message);

// 场景：模板保存失败、资产预览 404、后端连不上时，日志写明请求、状态码、用时与服务端说明；地址去掉查询参数，
// 二进制正文不读，主动取消与 Tauri IPC 不记录，超过 3 秒的成功请求记慢请求。日志窗口打开前不替换 fetch。
test("记录请求失败与慢请求", async () => {
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
  await waitFor(() => expect(messages("请求")).toHaveLength(4));
  expect(messages("请求")).toEqual([
    "POST api.test:8000/template → 409（120 ms）：{\"detail\":\"模板名称已存在\"}",
    "GET api.test:8000/api/sprites/s1/preview → 404（120 ms）",
    "GET cdn.test/fonts/a.ttf 用时 3.6 秒",
    "GET api.test:8000/works 失败（120 ms）：TypeError: Failed to fetch",
  ]);
  expect(entries.find((entry) => entry.message.includes("→ 409"))?.level).toBe("警告");
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
  jest.useRealTimers();
  await waitFor(() => expect(messages("内存")).toHaveLength(1));
  expect(messages("内存")[0]).toMatch(/^(JS 堆 .+，)?页面元素 \d+ 个，iframe 1 个$/);
  document.body.replaceChildren();
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
  await waitFor(() => expect(messages("请求")).toHaveLength(1));
  expect(messages("请求")[0]).toBe(`GET api.test:8000/stream → 500（0 ms）：${"x".repeat(200)}…`);
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
    await waitFor(() => expect(messages("请求")).toHaveLength(1));
    expect(messages("请求")[0]).toBe("GET api.test:8000/works 失败（0 ms）：TypeError: Failed to fetch");
  } finally {
    Reflect.set(globalThis, "PerformanceObserver", original);
  }
});
