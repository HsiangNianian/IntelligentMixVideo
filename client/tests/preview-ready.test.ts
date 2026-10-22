/**
 * 模板预览应用时间线后等 SDK 的 canplay 再标为就绪：收到 canplay 或 3 秒超时结束等待并取消订阅，其他事件不算；
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
  let done = false;
  const waiting = waitForCanPlay(source).then(() => { done = true; });
  source.emit({ type: "loadEnd", data: { useTime: 30 } });
  source.emit({ type: "playerSeeked", data: { currentTime: 0 } });
  await settle();
  expect(done).toBe(false);
  source.emit({ type: "canplay", data: { currentTime: 0 } });
  await waiting;
  expect(source.unsubscribe).toHaveBeenCalledTimes(1);
});

// 场景：SDK 一直没有发出 canplay，3 秒后照样允许播放，不让预览一直停在加载中。
test("没有 canplay 时 3 秒后结束等待", async () => {
  jest.useFakeTimers();
  const source = player();
  let done = false;
  void waitForCanPlay(source).then(() => { done = true; });
  jest.advanceTimersByTime(2_999);
  await settle();
  expect(done).toBe(false);
  jest.advanceTimersByTime(1);
  await settle();
  expect(done).toBe(true);
  expect(source.unsubscribe).toHaveBeenCalledTimes(1);
});
