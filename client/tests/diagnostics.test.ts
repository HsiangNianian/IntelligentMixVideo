/**
 * `--debug` 调试日志的前端转发：原生层未开启时不转发也不改动 console；开启后转发未捕获错误、Promise 拒绝、
 * 资源与媒体加载失败、console.error/warn 与 iframe 里的报错，连续相同记录只发一次并汇报重复次数；
 * 本地预览视频恢复失败这类界面不提示的错误也写入日志。IPC 由 mockIPC 模拟；执行 bun run test。
 */
import { afterEach, beforeEach, expect, mock, test } from "bun:test";
import { waitFor } from "@testing-library/react";
import { describe as describeValue, debugLog, resetDiagnostics, startDiagnostics, watchWindow } from "@/lib/diagnostics";
import { mockDesktop } from "./setup";

type Entry = { level: string; source: string; message: string };
let entries: Entry[] = [];
const originalError = console.error;
const originalWarn = console.warn;
let printed: ReturnType<typeof mock>;

/** 模拟桌面端：`diagnostics_enabled` 返回给定值，日志记入 entries，其他命令交给 extra。 */
function desktop(enabled: boolean, extra: (command: string, args: Record<string, unknown>) => unknown = () => null) {
  mockDesktop(async (command, args) => {
    if (command === "diagnostics_enabled") return enabled;
    if (command === "diagnostics_log") { entries.push(args as Entry); return null; }
    return extra(command, args);
  });
}

beforeEach(() => {
  entries = [];
  // console 输出由替身接收，既避免测试输出噪声，也能检查转发后仍调用原方法。
  printed = mock();
  console.error = printed;
  console.warn = printed;
});

afterEach(() => {
  resetDiagnostics();
  console.error = originalError;
  console.warn = originalWarn;
});

// 场景：没有带 --debug 启动时不改动 console，报错也不经 IPC 转发。
test("未开启调试日志时不转发也不替换 console", async () => {
  desktop(false);
  await startDiagnostics();
  expect(console.error).toBe(printed);
  console.error("不应转发");
  debugLog("模板预览", "不应转发");
  window.dispatchEvent(new ErrorEvent("error", { message: "boom" }));
  await Promise.resolve();
  expect(entries).toEqual([]);
});

// 场景：开启后先报告环境与解码能力，再转发未捕获错误、Promise 拒绝和 console.error，原 console 照常输出。
test("开启后转发环境、未捕获错误、Promise 拒绝与 console", async () => {
  desktop(true);
  await startDiagnostics();
  const error = new TypeError("读取失败");
  window.dispatchEvent(new ErrorEvent("error", { message: error.message, error, filename: "app.js", lineno: 3, colno: 9 }));
  const rejection = new Event("unhandledrejection") as PromiseRejectionEvent;
  Object.defineProperty(rejection, "reason", { value: "网络中断" });
  window.dispatchEvent(rejection);
  console.error("SDK 报错", { code: 7 });
  console.warn("仅提示");
  await waitFor(() => expect(entries.length).toBe(5));
  expect(entries[0]).toMatchObject({ level: "信息", source: "环境" });
  expect(entries[0].message).toContain("解码能力 H.264=");
  expect(entries[1].source).toBe("页面");
  expect(entries[1].message).toStartWith("TypeError: 读取失败");
  expect(entries[1].message).toContain("位于 app.js:3:9");
  expect(entries[2].message).toBe("未处理的 Promise 拒绝：网络中断");
  expect(entries[3]).toEqual({ level: "错误", source: "页面", message: 'SDK 报错 {"code":7}' });
  expect(entries[4]).toEqual({ level: "警告", source: "页面", message: "仅提示" });
  expect(printed).toHaveBeenCalledWith("SDK 报错", { code: 7 });
});

// 场景：页面里的图片与视频加载失败不冒泡，捕获阶段仍能记录地址与媒体错误码。
test("记录资源与媒体加载失败", async () => {
  desktop(true);
  await startDiagnostics();
  const image = document.createElement("img");
  image.setAttribute("src", "https://cdn.test/cover.png");
  const video = document.createElement("video");
  Object.defineProperty(video, "error", { value: { code: 3, message: "PIPELINE_ERROR_DECODE" } });
  Object.defineProperty(video, "currentSrc", { value: "blob:local-video" });
  document.body.append(image, video);
  image.dispatchEvent(new Event("error"));
  video.dispatchEvent(new Event("error"));
  await waitFor(() => expect(entries.length).toBe(3));
  expect(entries[1]).toEqual({ level: "警告", source: "页面", message: "img 加载失败；地址 https://cdn.test/cover.png" });
  expect(entries[2].message).toStartWith("video 加载失败；地址 blob:local-video；媒体错误 3（解码失败） PIPELINE_ERROR_DECODE");
  image.remove();
  video.remove();
});

// 场景：SDK 每帧重复同一条报错时只发送一次，换成其他记录前汇报省略的次数。
test("连续相同的记录只发送一次并汇报重复次数", async () => {
  desktop(true);
  await startDiagnostics();
  for (let index = 0; index < 4; index++) debugLog("模板预览 SDK", "渲染失败");
  debugLog("模板预览", "应用时间线失败");
  await waitFor(() => expect(entries.length).toBe(4));
  expect(entries.slice(1).map((entry) => entry.message)).toEqual(["渲染失败", "上一条又出现了 3 次", "应用时间线失败"]);
  expect(entries[2].source).toBe("模板预览 SDK");
});

// 场景：模板预览 SDK 运行在同源 iframe 里，它的 console 与错误按来源标注转发；iframe 的错误对象跨窗口也带名称与调用栈。
test("监听 iframe 的报错并标注来源", async () => {
  desktop(true);
  await startDiagnostics();
  const frameError = mock();
  const frameConsole = { error: frameError, warn: mock() };
  const frame = Object.assign(new EventTarget(), { console: frameConsole }) as unknown as Window;
  watchWindow(frame, "模板预览 SDK");
  watchWindow(frame, "重复监听");
  // 来自另一个窗口的错误不是本窗口 Error 的实例，按字段识别；WebKit 的调用栈不含消息本身。
  // watchWindow 原地替换了 iframe 的 console 方法，经同一个对象调用即走转发。
  frameConsole.error({ name: "RangeError", message: "画布过大", stack: "render@player.js:1:2" });
  frame.dispatchEvent(Object.assign(new Event("error"), { message: "Script error.", filename: "", error: null }));
  await waitFor(() => expect(entries.length).toBe(3));
  expect(entries[1]).toEqual({ level: "错误", source: "模板预览 SDK", message: "RangeError: 画布过大\nrender@player.js:1:2" });
  expect(entries[2]).toEqual({ level: "错误", source: "模板预览 SDK", message: "Script error." });
  expect(frameError).toHaveBeenCalledTimes(1);
  expect(describeValue(new Error("x"))).toStartWith("Error: x");
});

// 场景：桌面端某个本地预览视频读不回来时界面静默改用默认地址，调试日志写明槽位、文件名与原因。
test("本地预览视频恢复失败写入调试日志", async () => {
  desktop(true, (command) => {
    if (command === "preview_video_list") return [{ name: "a.mp4", type: "video/mp4" }, null, null];
    if (command === "preview_video_read") throw "文件不可读";
    return null;
  });
  await startDiagnostics();
  // 恢复结果缓存在模块内，用带查询串的导入取得全新的模块实例。
  const specifier = "@/features/templates/localVideos?diagnostics";
  const fresh: typeof import("@/features/templates/localVideos") = await import(specifier);
  expect((await fresh.localPreviewVideos())[0]).toBeUndefined();
  await waitFor(() => expect(entries.length).toBe(2));
  expect(entries[1]).toEqual({ level: "错误", source: "本地预览视频", message: "视频 1（a.mp4）读取失败，改用默认地址：文件不可读" });
});
