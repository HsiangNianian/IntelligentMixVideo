/**
 * 模板预览应用时间线后等 SDK 的 canplay：只有 canplay 返回可播放，超时和取消返回独立结果；计时从应用时间线完成后开始，
 * 计时用 Bun 的假计时器；执行 bun run test。
 */
import { afterEach, expect, jest, mock, test } from "bun:test";
import { waitForCanPlay, type PlayerEvent } from "@/features/templates/sdk";

afterEach(() => {
  jest.useRealTimers();
});

/** 可控的播放器事件流：emit 推送事件，unsubscribe 记录取消订阅。 */
function player() {
  let emit: (event: PlayerEvent) => void = () => {};
  const unsubscribe = mock();
  return {
    emit: (event: PlayerEvent) => emit(event),
    unsubscribe,
    event$: { subscribe(callback: (event: PlayerEvent) => void) { emit = callback; return { unsubscribe }; } },
  };
}

/** 等待 Promise 的后续回调执行完。 */
const settle = () => new Promise<void>((resolve) => queueMicrotask(resolve));

// 回归：setTimeline 返回时 SDK 还没建好播放节点，此时点播放会在 0 秒立即“播放结束”。就绪要等 canplay，加载完成与定位确认都不算。
test("收到 canplay 才结束等待", async () => {
  const source = player();
  let result: Awaited<ReturnType<typeof waitForCanPlay>> | undefined;
  const waiting = waitForCanPlay(source, Promise.resolve()).then((value) => { result = value; });
  source.emit({ type: "loadEnd", data: { useTime: 30 } });
  source.emit({ type: "playerSeeked", data: { currentTime: 0 } });
  await settle();
  expect(result).toBeUndefined();
  source.emit({ type: "canplay", data: { currentTime: 0 } });
  await waiting;
  expect(result).toBe("canplay");
  expect(source.unsubscribe).toHaveBeenCalledTimes(1);
});

// 场景：SDK 一直没有发出 canplay。3 秒内没有事件可确认可播放，必须返回 timeout 而不是 canplay；计时只在 setTimeline 完成后开始。
test("没有 canplay 时返回超时，应用时间线的耗时不计入等待窗口", async () => {
  jest.useFakeTimers();
  const source = player();
  let finishTimeline = () => {};
  const timeline = new Promise<void>((resolve) => { finishTimeline = resolve; });
  let result: Awaited<ReturnType<typeof waitForCanPlay>> | undefined;
  void waitForCanPlay(source, timeline).then((value) => { result = value; });
  jest.advanceTimersByTime(3_000);
  await settle();
  expect(result).toBeUndefined();
  finishTimeline();
  await settle();
  jest.advanceTimersByTime(2_999);
  await settle();
  expect(result).toBeUndefined();
  jest.advanceTimersByTime(1);
  await settle();
  expect(result).toBe("timeout");
  expect(source.unsubscribe).toHaveBeenCalledTimes(1);
});

// 场景：用户在新一轮 canplay 到来前继续编辑或卸载播放器。取消必须立即结束等待并清理订阅与计时器，不产生可播放结论。
test("取消等待时清理订阅和计时器", async () => {
  jest.useFakeTimers();
  const source = player();
  const controller = new AbortController();
  let result: Awaited<ReturnType<typeof waitForCanPlay>> | undefined;
  void waitForCanPlay(source, Promise.resolve(), controller.signal).then((value) => { result = value; });
  await settle();
  controller.abort();
  await settle();
  expect(result).toBe("cancelled");
  expect(source.unsubscribe).toHaveBeenCalledTimes(1);
  jest.advanceTimersByTime(3_000);
  await settle();
  expect(result).toBe("cancelled");
  expect(source.unsubscribe).toHaveBeenCalledTimes(1);
});
