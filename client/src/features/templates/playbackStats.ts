/**
 * 调试模式下的预览播放流畅度：日志窗口打开后，每次播放（SDK 进入 playing 到暂停、播完或损坏）结束时写一条汇总，
 * 用来判断卡顿出在哪里：画面帧率与最长间隔（SDK 每画一帧发一次 render 事件；从开始播放到暂停都算在内，
 * 整段没有新画面也能看出来）、SDK 视频元素的丢帧（解码跟不上）、等待数据次数（读取或网络慢）、
 * 期间的主线程长帧及其脚本来源（SDK 渲染还是应用代码）与 JS 内存变化。
 * 日志窗口未打开时不开始统计，普通使用没有额外开销。
 */
import { debugLog, diagnosticsEnabled, heapUsed, longFramesObserved, onLongFrame, summarizeLongFrames, type LongFrame } from "@/lib/diagnostics";
import type { Player } from "./sdk";

/** 帧间隔超过它算一次停顿；30 fps 的一帧约 33 ms，三帧多没有新画面肉眼可见。 */
const STUTTER_MS = 100;
/** 短于它的播放不写汇总：定位、重播的瞬间切换不代表实际播放。 */
const MIN_SESSION_MS = 300;
/** 一次播放最多逐条记录的长帧数：超出部分只计数，避免长时间卡顿积累大量对象。 */
const MAX_SESSION_LONG_FRAMES = 500;

/** 视频元素当前累计的丢帧与总帧数；音频元素与不支持的 WebView 返回 undefined。 */
function quality(media: HTMLMediaElement) {
  if (!("getVideoPlaybackQuality" in media)) return undefined;
  const { droppedVideoFrames, totalVideoFrames } = (media as HTMLVideoElement).getVideoPlaybackQuality();
  return { dropped: droppedVideoFrames, total: totalVideoFrames };
}

/** 记一段没有新画面的间隔（到这一帧或到播放结束为止），超过停顿阈值时计一次停顿。 */
function gap(state: Session, now: number) {
  const interval = now - state.lastFrame;
  state.longestGap = Math.max(state.longestGap, interval);
  if (interval > STUTTER_MS) state.stutters++;
  state.lastFrame = now;
}

/** 一次播放期间的累计数据。 */
type Session = {
  startedAt: number;
  from: number;
  frames: number;
  /** 上一帧的时刻；开始时取播放开始的时刻，第一帧来得慢同样算间隔。 */
  lastFrame: number;
  longestGap: number;
  stutters: number;
  waits: number;
  baseline: Map<HTMLMediaElement, { dropped: number; total: number }>;
  longFrames: LongFrame[];
  omittedLongFrames: number;
  heap?: number;
  release: () => void;
};

/**
 * 订阅播放器的状态与 render 事件，日志窗口打开时为每次播放统计并写汇总。videos 取 SDK 当前的媒体元素（见 watchSdkMedia）。
 * 返回的函数取消订阅；仍在统计的播放立即写出汇总。
 */
export function watchPlayback(player: Pick<Player, "event$">, videos: () => HTMLMediaElement[]): () => void {
  let session: Session | undefined;

  /** 开始统计：记下各视频的丢帧基数与内存，在视频上数等待数据的次数，并接收期间的主线程长帧。 */
  const start = (from: number) => {
    const baseline = new Map<HTMLMediaElement, { dropped: number; total: number }>();
    const current = videos();
    for (const media of current) {
      const value = quality(media);
      if (value) baseline.set(media, value);
    }
    const now = performance.now();
    const state: Session = { startedAt: now, from, frames: 0, lastFrame: now, longestGap: 0, stutters: 0, waits: 0, baseline, longFrames: [], omittedLongFrames: 0, heap: heapUsed(), release: () => {} };
    const waited = () => { state.waits++; };
    for (const media of current) media.addEventListener("waiting", waited);
    const stopFrames = onLongFrame((frame) => {
      if (state.longFrames.length < MAX_SESSION_LONG_FRAMES) state.longFrames.push(frame);
      else state.omittedLongFrames++;
    });
    state.release = () => {
      for (const media of current) media.removeEventListener("waiting", waited);
      stopFrames();
    };
    session = state;
  };

  /** 结束统计并写汇总；太短的播放（定位、重播时的瞬间切换）只结束不写。 */
  const finish = (to?: number) => {
    const state = session;
    if (!state) return;
    session = undefined;
    state.release();
    const now = performance.now();
    const elapsed = now - state.startedAt;
    if (elapsed < MIN_SESSION_MS) return;
    gap(state, now);
    const range = `${state.from.toFixed(2)}→${typeof to === "number" ? to.toFixed(2) : "?"} 秒`;
    const fps = state.frames * 1000 / elapsed;
    let dropped = 0;
    let total = 0;
    for (const [media, before] of state.baseline) {
      const after = quality(media);
      if (!after) continue;
      dropped += Math.max(0, after.dropped - before.dropped);
      total += Math.max(0, after.total - before.total);
    }
    const heap = heapUsed();
    const parts = [
      `画面 ${fps.toFixed(1)} fps，最长间隔 ${Math.round(state.longestGap)} ms，超过 ${STUTTER_MS} ms 的停顿 ${state.stutters} 次`,
      total ? `视频丢帧 ${dropped}/${total}` : "视频丢帧无法统计",
      `等待数据 ${state.waits} 次`,
      !longFramesObserved() ? "主线程长帧：当前 WebView 不支持统计"
        : state.longFrames.length
          ? `主线程长帧 ${summarizeLongFrames(state.longFrames)}${state.omittedLongFrames ? `（另有 ${state.omittedLongFrames} 次未记录）` : ""}`
          : "无主线程长帧",
      ...(heap !== undefined && state.heap !== undefined ? [`JS 内存 ${heap >= state.heap ? "+" : ""}${((heap - state.heap) / 1048576).toFixed(1)} MB`] : []),
    ];
    const poor = fps < 20 || state.longestGap > 250 || (total > 0 && dropped / total > 0.05);
    debugLog("模板预览", `播放 ${(elapsed / 1000).toFixed(1)} 秒（${range}）：${parts.join("；")}`, poor ? "警告" : "信息");
  };

  const subscription = player.event$.subscribe(({ type, data = {} }) => {
    if (type === "playerStateChange") {
      if (data.state === "playing") {
        if (!session && diagnosticsEnabled()) start(data.currentTime ?? 0);
      } else if (data.state !== "stalled") finish(data.currentTime);
      return;
    }
    if (type !== "render" || !session) return;
    gap(session, performance.now());
    session.frames++;
  });
  return () => {
    subscription.unsubscribe();
    finish();
  };
}
