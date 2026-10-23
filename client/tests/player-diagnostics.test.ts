/**
 * 模板预览播放器的调试日志：SDK 不挂到文档上的媒体元素（加载失败、卡顿）与公开事件流（播放状态、渲染上下文、
 * 素材加载超时）写进日志，渲染授权改在后台请求；IPC 由 mockIPC 模拟，计时用 Bun 的假计时器；执行 bun run test。
 */
import { afterEach, beforeEach, expect, jest, mock, test } from "bun:test";
import { waitFor } from "@testing-library/react";
import { openDiagnostics, resetDiagnostics } from "@/lib/diagnostics";
import { deferLicenseRequest, watchPlayer, watchSdkMedia, type PlayerEvent } from "@/features/templates/sdk";
import { mockDesktop } from "./setup";

type Entry = { level: string; source: string; message: string };
let entries: Entry[] = [];

beforeEach(async () => {
  // 其他测试文件挂载 App 时会启动收集且不重置，先恢复初始状态。
  resetDiagnostics();
  entries = [];
  mockDesktop(async (command, args) => {
    if (command === "diagnostics_open") return { path: "/data/diagnostics.log", viewerError: null };
    if (command === "diagnostics_log") entries.push(args as Entry);
    return null;
  });
  await openDiagnostics();
});

afterEach(() => {
  jest.useRealTimers();
  resetDiagnostics();
});

/** 等待某条记录转发到原生层。 */
async function logged(message: string, level?: string) {
  await waitFor(() => expect(entries.some((entry) => entry.message === message && (!level || entry.level === level))).toBe(true));
}

// 回归：SDK 的 <video> 不挂到文档上，error 事件到不了窗口监听，之前日志里只有 SDK 自己打印的组件编号。
// 接管播放器文档的 createElement 后，加载失败带地址与错误码写进日志并交给预览提示；SDK 清空地址或中止加载不算失败。
test("记录 SDK 游离媒体元素的加载失败与卡顿", async () => {
  const target = document.implementation.createHTMLDocument("player");
  const failures = mock();
  watchSdkMedia(target, failures);
  expect(target.createElement("div").tagName).toBe("DIV");
  const video = target.createElement("video");
  expect(video.isConnected).toBe(false);
  Object.defineProperty(video, "error", { value: { code: 4, message: "DEMUXER_ERROR_NO_SUPPORTED_STREAMS" } });
  video.dispatchEvent(new Event("error"));
  expect(failures).not.toHaveBeenCalled();
  video.setAttribute("src", "https://cdn.test/a.mp4#t=0.1");
  Object.defineProperty(video, "currentSrc", { value: "https://cdn.test/a.mp4#t=0.1" });
  video.dispatchEvent(new Event("error"));
  video.dispatchEvent(new Event("waiting"));
  expect(failures).toHaveBeenCalledWith(video);
  await logged("video 加载失败；地址 https://cdn.test/a.mp4#t=0.1；媒体错误 4（格式或地址不受支持） DEMUXER_ERROR_NO_SUPPORTED_STREAMS；networkState=0 readyState=0", "错误");
  await logged("video 等待数据（waiting）；地址 https://cdn.test/a.mp4#t=0.1；媒体错误 4（格式或地址不受支持） DEMUXER_ERROR_NO_SUPPORTED_STREAMS；networkState=0 readyState=0", "警告");
  // 切换素材时 SDK 会中止上一次加载：只记信息，不弹“无法播放”。
  const aborted = target.createElement("video");
  aborted.setAttribute("src", "https://cdn.test/b.mp4");
  Object.defineProperty(aborted, "error", { value: { code: 1 } });
  aborted.dispatchEvent(new Event("error"));
  expect(failures).toHaveBeenCalledTimes(1);
  await logged("video 加载被中止；地址 https://cdn.test/b.mp4；媒体错误 1（加载被中止）；networkState=0 readyState=0", "信息");
});

// 回归：原来只记录类型名含 error 的事件，而 SDK 公开事件流只转发 player 类事件，一条都不会命中。
// 现在记录播放状态（卡住、损坏分别为警告、错误）、渲染失败与上下文丢失；逐帧 render 等事件不记录。
test("记录播放状态、渲染失败与渲染上下文丢失", async () => {
  let emit: (event: PlayerEvent) => void = () => {};
  const unsubscribe = mock();
  const renderError = mock();
  const stop = watchPlayer({ event$: { subscribe(callback) { emit = callback; return { unsubscribe }; } } }, renderError);
  emit({ type: "playerStateChange", data: { state: "playing", currentTime: 1 } });
  emit({ type: "render", data: { time: 1.2 } });
  emit({ type: "playerStateChange", data: { state: "stalled", currentTime: 1.5 } });
  emit({ type: "playerStateChange", data: { state: "broken", currentTime: 1.5 } });
  emit({ type: "renderError", data: { error: "Failed to fetch" } });
  emit({ type: "renderContextLost", data: {} });
  await logged("播放状态 playing（1.00 秒）", "信息");
  await logged("播放状态 stalled（1.50 秒）", "警告");
  await logged("播放状态 broken（1.50 秒）", "错误");
  await logged("渲染失败：Failed to fetch", "错误");
  await logged("WebGL 渲染上下文丢失，画面会黑屏直到恢复", "错误");
  expect(renderError).toHaveBeenCalledWith("Failed to fetch");
  expect(entries.some((entry) => entry.message.includes("render"))).toBe(false);
  stop();
  expect(unsubscribe).toHaveBeenCalledTimes(1);
});

// 场景：404 或解码失败时 SDK 开始加载素材后再也没有 loadEnd；10 秒未完成写警告，之后完成时补记用时，
// 及时完成的加载不记录，停止监听后不再触发。
test("素材加载 10 秒仍未完成时写警告", async () => {
  jest.useFakeTimers();
  let emit: (event: PlayerEvent) => void = () => {};
  const stop = watchPlayer({ event$: { subscribe(callback) { emit = callback; return { unsubscribe() {} }; } } });
  emit({ type: "loading", data: { currentTime: 0 } });
  emit({ type: "loadEnd", data: { useTime: 900 } });
  jest.advanceTimersByTime(10_000);
  emit({ type: "loading", data: { currentTime: 2 } });
  emit({ type: "loading", data: { currentTime: 2 } });
  jest.advanceTimersByTime(10_000);
  emit({ type: "loadEnd", data: { useTime: 12_400 } });
  emit({ type: "loading", data: { currentTime: 3 } });
  stop();
  jest.advanceTimersByTime(10_000);
  jest.useRealTimers();
  await logged("素材加载完成，用时 12.4 秒", "信息");
  expect(entries.filter((entry) => entry.message.startsWith("素材加载 10 秒仍未完成")).map((entry) => entry.message)).toEqual(["素材加载 10 秒仍未完成（2.00 秒）"]);
});

// 场景：旧 WebView 没有 WeakRef。仍能接管 createElement 创建媒体元素，只是放弃丢帧统计，不中断预览。
test("没有 WeakRef 时仍可创建媒体元素", () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "WeakRef");
  Object.defineProperty(globalThis, "WeakRef", { configurable: true, writable: true, value: undefined });
  try {
    const target = document.implementation.createHTMLDocument("player");
    const alive = watchSdkMedia(target, () => {});
    const video = target.createElement("video");
    expect(video.tagName).toBe("VIDEO");
    expect(alive()).toEqual([]);
  } finally {
    if (descriptor) Object.defineProperty(globalThis, "WeakRef", descriptor);
  }
});

// 回归：Windows 测试机上渲染授权请求失败后，SDK 把渲染引擎标为初始化失败且不再重试：视频一直黑屏，点播放时间不走，只能拖动看字幕；
// 网络慢时第一次播放也要等这个请求。现在 SDK 立即拿到无证书结果（空 JSON），真实请求带同样的方法与正文在后台发出：
// 出错、非 2xx、回应不是 JSON 或 10 秒没有回应时只记警告，服务端的正常回应不记；其他请求原样转发。
test("渲染授权在后台请求，不阻塞播放器", async () => {
  jest.useFakeTimers();
  const license = "https://vpp-license-proxy.aliyuncs.com/getWebCert";
  const answers: ((init?: RequestInit) => Promise<Response>)[] = [
    async () => new Response('{"message":"Signature verification error on the server."}'),
    async () => { throw new TypeError("Failed to fetch"); },
    async () => new Response('{"message":"bad gateway"}', { status: 502 }),
    async () => new Response("<html>captive portal</html>"),
    (init) => new Promise((_resolve, reject) => init?.signal?.addEventListener("abort", () => reject(init.signal?.reason))),
  ];
  const other = new Response("wasm");
  const fetch = mock(async (input: RequestInfo | URL, init?: RequestInit) => input === license ? answers.shift()!(init) : other);
  const view: { fetch: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>; closed: boolean } = { fetch, closed: false };
  deferLicenseRequest(view as unknown as Window);
  /** 按 SDK 的方式请求授权并解析 JSON。 */
  const post = () => view.fetch(license, { method: "POST", body: "{}" }).then((response) => response.json());
  expect(await view.fetch("https://g.alicdn.com/sdk.wasm.txt")).toBe(other);
  // 第五次的真实请求一直没有回应，SDK 照样立即拿到结果。
  for (let index = 0; index < 5; index++) expect(await post()).toEqual({});
  expect(fetch.mock.calls[1][1]).toMatchObject({ method: "POST", body: "{}", signal: expect.any(AbortSignal) });
  jest.advanceTimersByTime(10_000);
  jest.useRealTimers();
  await logged("渲染授权请求失败（TimeoutError: 10 秒没有回应）；预览不等授权，照常播放", "警告");
  expect(entries.filter((entry) => entry.message.startsWith("渲染授权请求失败")).map((entry) => entry.message)).toEqual([
    "渲染授权请求失败（TypeError: Failed to fetch）；预览不等授权，照常播放",
    "渲染授权请求失败（Error: HTTP 502）；预览不等授权，照常播放",
    "渲染授权请求失败（Error: HTTP 200 的回应不是 JSON）；预览不等授权，照常播放",
    "渲染授权请求失败（TimeoutError: 10 秒没有回应）；预览不等授权，照常播放",
  ]);
});

// 场景：播放器停用或卸载后，后台请求随页面中止也不再记失败；停止后恢复原 fetch，授权请求重新交给原实现。
test("停用后恢复原 fetch 且不再记录授权失败", async () => {
  const license = "https://vpp-license-proxy.aliyuncs.com/getWebCert";
  let fail: (error: Error) => void = () => {};
  const original = mock((input: RequestInfo | URL) => input === license
    ? new Promise<Response>((_resolve, reject) => { fail = reject; })
    : Promise.resolve(new Response("wasm")));
  const view: { fetch: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>; closed: boolean } = { fetch: original, closed: false };
  const stop = deferLicenseRequest(view as unknown as Window);
  expect(await (await view.fetch(license)).json()).toEqual({});
  stop();
  expect(view.fetch).toBe(original);
  fail(new TypeError("Failed to fetch"));
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(entries.some((entry) => entry.message.startsWith("渲染授权请求失败"))).toBe(false);
});
