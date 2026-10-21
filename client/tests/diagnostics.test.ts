/**
 * 设置按钮打开调试日志后的前端收集：窗口打开前只进入内存缓冲，打开后补发环境、错误、媒体和控件操作；
 * 连续相同记录只发送一次并汇报重复次数。IPC 由 mockIPC 模拟；执行 bun run test。
 */
import { afterEach, beforeEach, expect, mock, test } from "bun:test";
import { waitFor } from "@testing-library/react";
import { describe as describeValue, debugLog, openDiagnostics, resetDiagnostics, startDiagnostics, watchWindow } from "@/lib/diagnostics";
import { mockDesktop } from "./setup";

type Entry = { level: string; source: string; message: string };
let entries: Entry[] = [];
const originalError = console.error;
const originalWarn = console.warn;
let printed: ReturnType<typeof mock>;

/** 模拟桌面端：打开日志窗口成功，日志记入 entries，其他命令交给 extra。 */
function desktop(extra: (command: string, args: Record<string, unknown>) => unknown = () => null) {
  mockDesktop(async (command, args) => {
    if (command === "diagnostics_open") return null;
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

// 场景：窗口还没打开时错误只留在内存里，点击设置按钮后能补看到此前发生的错误。
test("打开日志窗口后补发此前缓冲的错误", async () => {
  desktop();
  startDiagnostics();
  const error = new TypeError("读取失败");
  window.dispatchEvent(new ErrorEvent("error", { message: error.message, error, filename: "app.js", lineno: 3, colno: 9 }));
  console.error("窗口前错误");
  await Promise.resolve();
  expect(entries).toEqual([]);
  await openDiagnostics();
  await waitFor(() => expect(entries.length).toBe(4));
  expect(entries[0]).toMatchObject({ level: "信息", source: "环境" });
  expect(entries[0].message).toContain("解码能力 H.264=");
  expect(entries[1].message).toStartWith("TypeError: 读取失败");
  expect(entries[2]).toEqual({ level: "错误", source: "页面", message: "窗口前错误" });
  expect(entries[3]).toEqual({ level: "信息", source: "调试日志", message: "日志窗口已打开" });
  expect(printed).toHaveBeenCalledWith("窗口前错误");
});

// 场景：窗口打开后继续转发未处理的 Promise 拒绝与 console 警告。
test("打开后转发错误、Promise 拒绝与 console", async () => {
  desktop();
  startDiagnostics();
  await openDiagnostics();
  const rejection = new Event("unhandledrejection") as PromiseRejectionEvent;
  Object.defineProperty(rejection, "reason", { value: "网络中断" });
  window.dispatchEvent(rejection);
  console.error("SDK 报错", { code: 7 });
  console.warn("仅提示");
  await waitFor(() => expect(entries.some((entry) => entry.message === "未处理的 Promise 拒绝：网络中断")).toBe(true));
  await waitFor(() => expect(entries.some((entry) => entry.message === 'SDK 报错 {"code":7}')).toBe(true));
  await waitFor(() => expect(entries.some((entry) => entry.message === "仅提示")).toBe(true));
});

// 场景：页面里的图片与视频加载失败不冒泡，捕获阶段仍能记录地址与媒体错误码。
test("记录资源与媒体加载失败", async () => {
  desktop();
  startDiagnostics();
  await openDiagnostics();
  const image = document.createElement("img");
  image.setAttribute("src", "https://cdn.test/cover.png");
  const video = document.createElement("video");
  Object.defineProperty(video, "error", { value: { code: 3, message: "PIPELINE_ERROR_DECODE" } });
  Object.defineProperty(video, "currentSrc", { value: "blob:local-video" });
  document.body.append(image, video);
  image.dispatchEvent(new Event("error"));
  video.dispatchEvent(new Event("error"));
  await waitFor(() => expect(entries.some((entry) => entry.message === "img 加载失败；地址 https://cdn.test/cover.png")).toBe(true));
  await waitFor(() => expect(entries.some((entry) => entry.message.startsWith("video 加载失败；地址 blob:local-video；媒体错误 3（解码失败） PIPELINE_ERROR_DECODE"))).toBe(true));
  image.remove();
  video.remove();
});

// 场景：按钮、页签、输入框和表单的交互都进入操作日志；密码值不落盘。
test("记录所有控件的点击、修改和提交", async () => {
  desktop();
  startDiagnostics();
  const button = document.createElement("button");
  button.setAttribute("aria-label", "保存模板");
  const tab = document.createElement("div");
  tab.setAttribute("role", "tab");
  tab.textContent = "通用";
  const input = document.createElement("input");
  input.setAttribute("aria-label", "后端服务地址");
  input.value = "http://api.test";
  const password = document.createElement("input");
  password.type = "password";
  password.setAttribute("aria-label", "API Key");
  password.value = "private-secret";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.setAttribute("aria-label", "启用");
  checkbox.checked = true;
  const select = document.createElement("select");
  select.setAttribute("aria-label", "排序");
  for (const [text, value] of [["最新", "newest"], ["最早", "oldest"]]) {
    const option = document.createElement("option");
    option.textContent = text;
    option.value = value;
    select.append(option);
  }
  const form = document.createElement("form");
  form.setAttribute("aria-label", "通用设置");
  form.append(input, password, checkbox, select, button, tab);
  document.body.append(form);
  button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  tab.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
  password.dispatchEvent(new Event("change", { bubbles: true }));
  checkbox.dispatchEvent(new Event("change", { bubbles: true }));
  select.dispatchEvent(new Event("change", { bubbles: true }));
  form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  await openDiagnostics();
  await waitFor(() => expect(entries.some((entry) => entry.message === "点击 button「保存模板」")).toBe(true));
  await waitFor(() => expect(entries.some((entry) => entry.message === "点击 tab「通用」")).toBe(true));
  await waitFor(() => expect(entries.some((entry) => entry.message === "更改 input[text]「后端服务地址」：值「http://api.test」")).toBe(true));
  await waitFor(() => expect(entries.some((entry) => entry.message === "更改 input[password]「API Key」：值已隐藏")).toBe(true));
  await waitFor(() => expect(entries.some((entry) => entry.message === "更改 input[checkbox]「启用」：选中")).toBe(true));
  await waitFor(() => expect(entries.some((entry) => entry.message === "更改 select「排序」：选项「最新」")).toBe(true));
  await waitFor(() => expect(entries.some((entry) => entry.message === "提交表单「通用设置」")).toBe(true));
  expect(entries.map((entry) => entry.message).join(" ")).not.toContain("private-secret");
  form.remove();
});

// 场景：SDK 每帧重复同一条报错时只发送一次，换成其他记录前汇报省略的次数。
test("连续相同的记录只发送一次并汇报重复次数", async () => {
  desktop();
  startDiagnostics();
  await openDiagnostics();
  for (let index = 0; index < 4; index++) debugLog("模板预览 SDK", "渲染失败");
  debugLog("模板预览", "应用时间线失败");
  await waitFor(() => expect(entries.some((entry) => entry.message === "上一条又出现了 3 次")).toBe(true));
  expect(entries.filter((entry) => entry.message === "渲染失败")).toHaveLength(1);
  expect(entries.some((entry) => entry.message === "应用时间线失败")).toBe(true);
});

// 场景：模板预览 SDK 运行在同源 iframe 里，它的 console 与错误按来源标注转发；iframe 的错误对象跨窗口也带名称与调用栈。
test("监听 iframe 的报错并标注来源", async () => {
  desktop();
  startDiagnostics();
  await openDiagnostics();
  const frameError = mock();
  const frameConsole = { error: frameError, warn: mock() };
  const frame = Object.assign(new EventTarget(), { console: frameConsole }) as unknown as Window;
  watchWindow(frame, "模板预览 SDK");
  watchWindow(frame, "重复监听");
  // 来自另一个窗口的错误不是本窗口 Error 的实例，按字段识别；WebKit 的调用栈不含消息本身。
  // watchWindow 原地替换了 iframe 的 console 方法，经同一个对象调用即走转发。
  frameConsole.error({ name: "RangeError", message: "画布过大", stack: "render@player.js:1:2" });
  frame.dispatchEvent(Object.assign(new Event("error"), { message: "Script error.", filename: "", error: null }));
  await waitFor(() => expect(entries.some((entry) => entry.message === "RangeError: 画布过大\nrender@player.js:1:2")).toBe(true));
  await waitFor(() => expect(entries.some((entry) => entry.message === "Script error.")).toBe(true));
  expect(frameError).toHaveBeenCalledTimes(1);
  expect(describeValue(new Error("x"))).toStartWith("Error: x");
});

// 场景：桌面端某个本地预览视频读不回来时界面静默改用默认地址，调试日志写明槽位、文件名与原因。
test("本地预览视频恢复失败写入调试日志", async () => {
  desktop((command) => {
    if (command === "preview_video_list") return [{ name: "a.mp4", type: "video/mp4" }, null, null];
    if (command === "preview_video_read") throw "文件不可读";
    return null;
  });
  startDiagnostics();
  await openDiagnostics();
  // 恢复结果缓存在模块内，用带查询串的导入取得全新的模块实例。
  const specifier = "@/features/templates/localVideos?diagnostics";
  const fresh: typeof import("@/features/templates/localVideos") = await import(specifier);
  expect((await fresh.localPreviewVideos())[0]).toBeUndefined();
  await waitFor(() => expect(entries.some((entry) => entry.message === "视频 1（a.mp4）读取失败，改用默认地址：文件不可读")).toBe(true));
});
