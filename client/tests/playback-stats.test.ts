/**
 * 调试模式下的卡顿统计：每次预览播放结束写流畅度汇总（帧率、最长间隔、丢帧、等待数据、主线程长帧及来源、内存），
 * 播放之外超过 300 ms 的主线程长帧逐条记录；日志窗口未打开时不统计。IPC 由 mockIPC 模拟，
 * PerformanceObserver 与时钟由替身控制；执行 bun run test。
 */
import { afterEach, beforeEach, expect, spyOn, test } from "bun:test";
import { waitFor } from "@testing-library/react";
import { openDiagnostics, resetDiagnostics } from "@/lib/diagnostics";
import { watchPlayback } from "@/features/templates/playbackStats";
import type { PlayerEvent } from "@/features/templates/sdk";
import { mockDesktop } from "./setup";

type Entry = { level: string; source: string; message: string };
let entries: Entry[] = [];
let clock = 0;

/** 长帧观察器替身：记下回调，测试经 emit 推送 long-animation-frame 条目。 */
class FakeObserver {
  static supportedEntryTypes = ["long-animation-frame"];
  static current?: FakeObserver;
  /** 记下浏览器会调用的回调。 */
  constructor(private callback: (list: { getEntries(): unknown[] }) => void) { FakeObserver.current = this; }
  /** 与浏览器一致的空实现；观察类型由 supportedEntryTypes 决定。 */
  observe() {}
  /** 停止观察。 */
  disconnect() { FakeObserver.current = undefined; }
  /** 推送一批长帧条目。 */
  emit(entries: unknown[]) { this.callback({ getEntries: () => entries }); }
}
const original = Reflect.get(globalThis, "PerformanceObserver");

/** 一个 SDK 渲染了 180 ms 的长帧：150 ms 来自 SDK 的 render，最后 20 ms 是样式布局与绘制。 */
const sdkFrame = (startTime: number) => ({
  entryType: "long-animation-frame", startTime, duration: 180, blockingDuration: 130, renderStart: startTime + 160,
  scripts: [{ sourceURL: "https://g.alicdn.com/thor-server/video-editing-websdk/5.2.2/player.js", sourceFunctionName: "render", duration: 150 }],
});

beforeEach(() => {
  resetDiagnostics();
  entries = [];
  clock = 1000;
  Reflect.set(globalThis, "PerformanceObserver", FakeObserver);
  spyOn(performance, "now").mockImplementation(() => clock);
  mockDesktop(async (command, args) => {
    if (command === "diagnostics_open") return { path: "/data/diagnostics.log", viewerError: null };
    if (command === "diagnostics_log") entries.push(args as Entry);
    return null;
  });
});

afterEach(() => {
  resetDiagnostics();
  Reflect.set(globalThis, "PerformanceObserver", original);
});

/** 可控的播放器与视频：emit 推送事件，quality 是视频累计的丢帧与总帧数。 */
function setup() {
  let emit: (event: PlayerEvent) => void = () => {};
  const quality = { droppedVideoFrames: 0, totalVideoFrames: 0 };
  const video = Object.assign(document.createElement("video"), { getVideoPlaybackQuality: () => ({ ...quality }) });
  const stop = watchPlayback({ event$: { subscribe(callback) { emit = callback; return { unsubscribe() {} }; } } }, () => [video]);
  return { emit: (event: PlayerEvent) => emit(event), quality, video, stop };
}

// 场景：用户反馈预览卡顿。打开日志窗口后播放一次，汇总说明帧率低、有一次 234 ms 的停顿、丢帧与等待数据，
// 期间主线程长帧主要来自阿里 SDK 的 render，据此判断瓶颈在 SDK 渲染还是解码；暂停时写出，帧率过低记警告。
test("播放结束写流畅度汇总", async () => {
  await openDiagnostics();
  const { emit, quality, video } = setup();
  quality.droppedVideoFrames = 2;
  quality.totalVideoFrames = 100;
  emit({ type: "playerStateChange", data: { state: "playing", currentTime: 1 } });
  for (const time of [1000, 1033, 1066, 1300, 1333]) {
    clock = time;
    emit({ type: "render", data: { time: 1 } });
  }
  FakeObserver.current!.emit([sdkFrame(1070)]);
  video.dispatchEvent(new Event("waiting"));
  quality.droppedVideoFrames = 5;
  quality.totalVideoFrames = 160;
  clock = 1400;
  emit({ type: "playerStateChange", data: { state: "paused", currentTime: 2 } });
  await waitFor(() => expect(entries.some((entry) => entry.message.startsWith("播放 "))).toBe(true));
  const summary = entries.find((entry) => entry.message.startsWith("播放 "))!;
  expect(summary).toMatchObject({ level: "警告", source: "模板预览" });
  expect(summary.message).toBe("播放 0.4 秒（1.00→2.00 秒）：画面 12.5 fps，最长间隔 234 ms，超过 100 ms 的停顿 1 次；视频丢帧 3/60；等待数据 1 次；主线程长帧 1 次共 180 ms（阻塞 130 ms，样式布局与绘制 20 ms），主要来自 阿里 SDK 150 ms（render）");
  // 播放中的长帧只进汇总，不逐条记录。
  expect(entries.some((entry) => entry.message.startsWith("主线程卡顿"))).toBe(false);
});

// 场景：点了播放但画面一直不动（解码或加载卡死）。整段没有新画面时，最长间隔就是整段播放时长，帧率为 0。
test("整段没有新画面时如实记录为停顿", async () => {
  await openDiagnostics();
  const { emit } = setup();
  emit({ type: "playerStateChange", data: { state: "playing", currentTime: 0 } });
  clock = 2900;
  emit({ type: "playerStateChange", data: { state: "paused", currentTime: 0 } });
  await waitFor(() => expect(entries.some((entry) => entry.message.startsWith("播放 "))).toBe(true));
  expect(entries.find((entry) => entry.message.startsWith("播放 "))!.message).toStartWith("播放 1.9 秒（0.00→0.00 秒）：画面 0.0 fps，最长间隔 1900 ms，超过 100 ms 的停顿 1 次；");
});

// 场景：普通使用（没打开日志窗口）时播放不做任何统计；定位、重播时一闪而过的播放也不写汇总。
test("日志窗口未打开或播放太短时不写汇总", async () => {
  const { emit } = setup();
  emit({ type: "playerStateChange", data: { state: "playing", currentTime: 0 } });
  clock = 3000;
  emit({ type: "playerStateChange", data: { state: "paused", currentTime: 2 } });
  await openDiagnostics();
  emit({ type: "playerStateChange", data: { state: "playing", currentTime: 2 } });
  clock = 3200;
  emit({ type: "playerStateChange", data: { state: "paused", currentTime: 2.2 } });
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(entries.some((entry) => entry.message.startsWith("播放 "))).toBe(false);
});

// 场景：编辑时（没有播放）界面卡住。超过 300 ms 的主线程长帧逐条记录并指出脚本来源，更短的不记录。
test("播放之外的长帧超过 300 ms 才逐条记录", async () => {
  await openDiagnostics();
  FakeObserver.current!.emit([{ ...sdkFrame(10), duration: 120 }, {
    entryType: "long-animation-frame", startTime: 500, duration: 420, blockingDuration: 370, renderStart: 0,
    scripts: [{ sourceURL: `${window.location.origin}/assets/index.js`, sourceFunctionName: "commitRoot", duration: 400 }],
  }]);
  await waitFor(() => expect(entries.some((entry) => entry.message.startsWith("主线程卡顿"))).toBe(true));
  expect(entries.filter((entry) => entry.message.startsWith("主线程卡顿")).map((entry) => entry.message))
    .toEqual(["主线程卡顿 1 次共 420 ms（阻塞 370 ms，样式布局与绘制 0 ms），主要来自 应用 400 ms（commitRoot）"]);
});
