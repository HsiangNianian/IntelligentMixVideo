/**
 * 模板预览播放器的调试日志：SDK 不挂到文档上的媒体元素（加载失败、卡顿）与公开事件流（播放状态、渲染上下文、
 * 素材加载超时）写进日志；IPC 由 mockIPC 模拟，计时用 Bun 的假计时器；执行 bun run test。
 */
import { afterEach, beforeEach, expect, jest, mock, test } from "bun:test";
import { waitFor } from "@testing-library/react";
import { openDiagnostics, resetDiagnostics } from "@/lib/diagnostics";
import { watchPlayer, watchSdkMedia, type PlayerEvent } from "@/features/templates/sdk";
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
// 接管播放器文档的 createElement 后，加载失败带地址与错误码写进日志并交给预览提示；SDK 清空地址引起的 error 不算失败。
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
});

// 回归：原来只记录类型名含 error 的事件，而 SDK 公开事件流只转发 player 类事件，一条都不会命中。
// 现在记录播放状态（卡住、损坏分别为警告、错误）与渲染上下文丢失；逐帧 render 等事件不记录。
test("记录播放状态变化与渲染上下文丢失", async () => {
  let emit: (event: PlayerEvent) => void = () => {};
  const unsubscribe = mock();
  const stop = watchPlayer({ event$: { subscribe(callback) { emit = callback; return { unsubscribe }; } } });
  emit({ type: "playerStateChange", data: { state: "playing", currentTime: 1 } });
  emit({ type: "render", data: { time: 1.2 } });
  emit({ type: "playerStateChange", data: { state: "stalled", currentTime: 1.5 } });
  emit({ type: "playerStateChange", data: { state: "broken", currentTime: 1.5 } });
  emit({ type: "renderContextLost", data: {} });
  await logged("播放状态 playing（1.00 秒）", "信息");
  await logged("播放状态 stalled（1.50 秒）", "警告");
  await logged("播放状态 broken（1.50 秒）", "错误");
  await logged("WebGL 渲染上下文丢失，画面会黑屏直到恢复", "错误");
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
