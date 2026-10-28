/**
 * 模板预览中不属于 IMS 的部分不等 IMS：测试环境不加载外部脚本，用例在播放器页面挂上 SDK 替身，
 * 由用例决定载入时间线与 canplay 的结果，检查 IMS 还在加载、卡住或出错时时间轴照常可用，卡住、出错或播放不前进时切换基础视频，
 * IMS 就绪前由前端时钟推进播放位置、就绪后接着播放；执行 bun run test。
 */
import { afterEach, expect, mock, spyOn, test } from "bun:test";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TemplatePreview } from "@/features/templates/TemplatePreview";
import { newDraft, type Draft } from "@/features/templates/model";
import { newFrameMask } from "@/features/templates/frameMask";
import { CAN_PLAY_TIMEOUT_MS, readCatalog, STALL_TIMEOUT_MS, type PlayerEvent } from "@/features/templates/sdk";
import { addTrack, previewContext, resolveTemplate } from "@/features/templates/tracks";
import { sampleMedia } from "./fixtures";
import { frames } from "./frames";
import { defaultSplitScreen } from "@/features/templates/splitScreen";

/** SDK 播放器替身：记录调用，事件由用例推送；载入时间线一直不完成，除非用例调用 finishTimeline。 */
class FakePlayer {
  static last: FakePlayer | undefined;
  currentTime = 0;
  timeline: unknown = {};
  aspectRatio?: string;
  readonly listeners = new Set<(event: PlayerEvent) => void>();
  readonly event$ = {
    subscribe: (callback: (event: PlayerEvent) => void) => {
      this.listeners.add(callback);
      return { unsubscribe: () => { this.listeners.delete(callback); } };
    },
  };
  readonly play = mock();
  readonly pause = mock();
  readonly destroy = mock();
  readonly mediaInfo?: (id: string, type: string, origin: string) => Promise<unknown>;
  finishTimeline: () => void = () => {};
  failTimeline: (error: Error) => void = () => {};
  readonly setTimeline = mock((_timeline?: unknown) => new Promise<void>((resolve, reject) => { this.finishTimeline = resolve; this.failTimeline = reject; }));
  /** 登记为最近创建的播放器。 */
  constructor(options?: { getMediaInfo: FakePlayer["mediaInfo"] }) {
    FakePlayer.last = this;
    this.mediaInfo = options?.getMediaInfo;
  }
  /** 以 SDK 的身份推送一条事件。 */
  emit(type: string, data?: PlayerEvent["data"]) {
    for (const listener of [...this.listeners]) listener({ type, data });
  }
  // SDK 的目录方法：不返回目录时 readCatalog 使用随包目录。
  static getSubtitleEffectColorStyles() { return undefined; }
  static getSubtitleBubbles() { return undefined; }
  static getVideoFilters() { return undefined; }
  static getVideoEffects() { return undefined; }
  static getVideoTransitions() { return undefined; }
}

/** 预览参数：草稿与按示例视频解析出的时间。 */
function props(draft: Draft) {
  return { draft, media: sampleMedia, resolved: resolveTemplate(draft, previewContext(sampleMedia)), videoInputKey: "sdk", onMediaChange: () => {}, onCatalog: () => {} };
}

/** 挂载模板预览，在播放器页面挂上 SDK 替身，等到 SDK 开始载入第一份时间线。 */
async function mountWithSdk(draft: Draft, extra: Partial<Parameters<typeof TemplatePreview>[0]> = {}) {
  FakePlayer.last = undefined;
  const append = Element.prototype.append;
  spyOn(Element.prototype, "append").mockImplementation(function (this: Element, ...nodes: (Node | string)[]) {
    append.apply(this, nodes);
    for (const node of nodes)
      if (node instanceof HTMLIFrameElement && node.title === "模板预览播放器") Reflect.set(node.contentWindow!, "AliyunTimelinePlayer", FakePlayer);
  });
  const view = render(<TemplatePreview {...props(draft)} {...extra} />);
  await waitFor(() => expect(FakePlayer.last?.setTimeline.mock.calls.length).toBe(1));
  return { view, player: FakePlayer.last! };
}

/** 时间轴各行的对象类型。 */
const rowObjects = () => [...screen.getByLabelText("时间轴轨道标签").querySelectorAll("[data-object]")].map((label) => label.getAttribute("data-object"));

test("只修改蒙版也刷新 SDK，失败降级保留相同几何，关闭即移除", async () => {
  spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ fillRect() {}, fillStyle: "" } as unknown as CanvasRenderingContext2D);
  spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue("data:image/png;base64,mask");
  const draft = newDraft();
  const stable = props(draft);
  const { view, player } = await mountWithSdk(draft, { resolved: stable.resolved });
  await act(async () => player.finishTimeline());
  await act(async () => player.emit("canplay"));
  const mask = newFrameMask();
  view.rerender(<TemplatePreview {...stable} draft={{ ...draft, frame_mask: mask }} />);
  await waitFor(() => expect(player.setTimeline).toHaveBeenCalledTimes(2));
  expect(screen.queryByLabelText("上栏蒙版预览")).toBeNull();
  await act(async () => player.failTimeline(new Error("SDK 出错")));
  expect(screen.getByLabelText("上栏蒙版预览").style.height).toBe(`${353 / 1920 * 100}%`);
  expect(screen.getByLabelText("下栏蒙版预览").style.top).toBe(`${1601 / 1920 * 100}%`);
  view.rerender(<TemplatePreview {...stable} draft={{ ...draft, frame_mask: {
    top: { ...mask.top, enabled: false }, bottom: { ...mask.bottom, enabled: false },
  } }} />);
  expect(screen.queryByLabelText("上栏蒙版预览")).toBeNull();
  expect(screen.queryByLabelText("下栏蒙版预览")).toBeNull();
});

// 回归：SDK 的动态媒体路径忽略 Timeline 蒙版字段，须从媒体回调一起交给原视频节点；不降级花字和滤镜。
test("同屏圆角从 SDK 媒体回调返回，直角视频保留原地址", async () => {
  spyOn(HTMLImageElement.prototype, "decode").mockResolvedValue();
  spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ drawImage: () => {} } as unknown as CanvasRenderingContext2D);
  spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue("data:image/png;base64,cHJldmlldw==");
  const draft = { ...newDraft(), split_screen: { ...defaultSplitScreen(), enabled: true } };
  const { player } = await mountWithSdk(draft);
  const timeline = player.setTimeline.mock.calls[0][0] as { VideoTracks: { VideoTrackClips: { MediaURL?: string }[] }[] };
  const id = timeline.VideoTracks.flatMap((row) => row.VideoTrackClips).find((clip) => clip.MediaURL?.includes("#imv-mask="))!.MediaURL!;
  const masked = await player.mediaInfo!(id, "Video", "mediaURL") as { url: string; maskUrl: string };
  expect(masked.url).toBe(sampleMedia[0].url);
  expect(new URL(masked.maskUrl).searchParams.get("radius")).toBe("30");
  expect(await player.mediaInfo!(sampleMedia[1].url, "Video", "mediaURL")).toBe(sampleMedia[1].url);
  await act(async () => player.finishTimeline());
  await act(async () => player.emit("canplay"));
  await screen.findByText("预览已就绪，点击播放查看效果");
  expect(screen.queryByText(/已切换基础视频预览/)).toBeNull();
});

// 回归：时间轴行只在 SDK 载入时间线之后才生成和更新，IMS 加载慢或卡住时时间轴没有对象与视频行，修改模板后也不变。
// 时间轴行直接按草稿生成：载入还没完成时已有对象与视频行，修改后立即更新。
test("SDK 还没载入时间线时时间轴行已按草稿显示并随修改更新", async () => {
  const draft = newDraft();
  const { view, player } = await mountWithSdk(draft);
  expect(rowObjects()).toEqual(expect.arrayContaining(["title", "subtitle", "video"]));
  expect(rowObjects()).not.toContain("filter");
  const filter = readCatalog().find((asset) => asset.category === "filter")!;
  view.rerender(<TemplatePreview {...props(addTrack(draft, filter, "filter").draft)} />);
  expect(rowObjects()).toContain("filter");
  expect(player.setTimeline).toHaveBeenCalledTimes(1);
});

/** 本例换下的 setTimeout，用例结束时恢复。 */
let realSetTimeout: typeof setTimeout | undefined;
afterEach(() => {
  if (realSetTimeout) globalThis.setTimeout = realSetTimeout;
  realSetTimeout = undefined;
});

/**
 * 记录 setTimeout，用例取出指定时长的最后一个回调立即执行；计时器照常登记，卸载时由预览清理。
 * 用普通包装而不是 spyOn：Testing Library 见到 mock 的 setTimeout 会当成 Jest 假计时器，waitFor 随之出错。
 */
function timers() {
  const calls: [unknown, number | undefined][] = [];
  const original = globalThis.setTimeout;
  realSetTimeout = original;
  globalThis.setTimeout = ((handler: TimerHandler, delay?: number, ...rest: unknown[]) => {
    calls.push([handler, delay]);
    return original(handler, delay, ...rest);
  }) as typeof setTimeout;
  return { fire: (delay: number) => act(async () => { ([...calls].reverse().find(([, ms]) => ms === delay)![0] as () => void)(); }) };
}

/** 预览下方的降级说明。 */
const fallbackNote = (reason: string) => `IMS 渲染暂不可用（${reason}），已切换基础视频预览：AI 资产照常显示，IMS 文字、转场、滤镜与特效暂不显示。`;

// 回归：SDK 卡在载入时间线（setTimeline 一直不完成）时 canplay 的计时还没开始，预览一直转圈，既不就绪也不降级。
// 载入时间线同样最多等 60 秒，到时切换基础视频。
test("SDK 载入时间线 60 秒没有完成时切换基础视频", async () => {
  const { fire } = timers();
  await mountWithSdk(newDraft());
  expect(screen.queryByText(/已切换基础视频预览/) === null).toBe(true);
  await fire(CAN_PLAY_TIMEOUT_MS);
  expect(screen.getByText(fallbackNote("60 秒内没有载入时间线"))).toBeTruthy();
  expect(screen.getByRole("button", { name: "重试 IMS 预览" })).toBeTruthy();
});

// 回归：SDK 卡在载入时间线时预览一直停在「正在应用效果并加载媒体…」，用户需要手动刷新。
// 刷新销毁旧播放器，用当前草稿重新初始化，新的时间线载入并 canplay 后恢复就绪。
test("预览卡在载入时间线时刷新会重建播放器", async () => {
  const { player } = await mountWithSdk(newDraft());
  expect(screen.getByText("正在应用效果并加载媒体…")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "刷新预览" }));
  await waitFor(() => expect(player.destroy).toHaveBeenCalled());
  await waitFor(() => expect(FakePlayer.last).not.toBe(player));
  expect(FakePlayer.last?.setTimeline).toHaveBeenCalledTimes(1);
  await act(async () => FakePlayer.last!.finishTimeline());
  await act(async () => FakePlayer.last!.emit("canplay"));
  await screen.findByText("预览已就绪，点击播放查看效果");
});

// 回归：生成转场画面或 SDK 载入时间线失败时预览只显示错误，播放按钮、进度条与时间轴全部停用，只能重试。
// 失败同样只停用 IMS 部分：切换基础视频并在说明里给出原因。
test("SDK 载入时间线失败时切换基础视频并说明原因", async () => {
  const { player } = await mountWithSdk(newDraft());
  await act(async () => player.failTimeline(new Error("时间线不合法")));
  expect(screen.getByText(fallbackNote("应用时间线失败：时间线不合法"))).toBeTruthy();
  expect(screen.getByLabelText("基础视频预览")).toBeTruthy();
});

// 回归：SDK 10 秒没有确认定位时预览报错并停用全部控件。定位卡住说明 SDK 已不能正常播放，同样切换基础视频。
test("SDK 定位 10 秒没有完成时切换基础视频", async () => {
  const { fire } = timers();
  const { player } = await mountWithSdk(newDraft());
  await act(async () => { player.finishTimeline(); });
  await act(async () => player.emit("canplay"));
  await screen.findByText("预览已就绪，点击播放查看效果");
  fireEvent.change(screen.getByRole("slider", { name: "预览进度" }), { target: { value: "5" } });
  await waitFor(() => expect(player.currentTime).toBe(5));
  await fire(10_000);
  expect(screen.getByText(fallbackNote("10 秒内没有定位到播放位置"))).toBeTruthy();
});

/** 接管 performance.now()，返回设置当前时刻（毫秒）的函数。 */
function clockAt() {
  let now = 0;
  spyOn(performance, "now").mockImplementation(() => now);
  return (value: number) => { now = value; };
}

// 显示在 1～4 秒的 AI 资产，素材完整时长 5 秒。
const sprite = { id: "sprite-a", spriteId: "s1", aspect: 9 / 16, name: "霓虹标题", start: 1, end: 4, length: 5 };

// 回归：系统 WebKitGTK 里 SDK 对 blob 视频一直等不到 canplaythrough，报告在播放、时间却不动，预览定格且不降级。
// IMS 播放中 10 秒时间没有前进就切换基础视频；期间前进过则从新位置接着核对。
test("IMS 播放中时间 10 秒不前进时切换基础视频", async () => {
  const { fire } = timers();
  const { player } = await mountWithSdk(newDraft());
  await act(async () => { player.finishTimeline(); });
  await act(async () => player.emit("canplay"));
  await screen.findByText("预览已就绪，点击播放查看效果");
  fireEvent.click(screen.getByRole("button", { name: "播放" }));
  await waitFor(() => expect(player.play).toHaveBeenCalledTimes(1));
  player.currentTime = 1.2;
  await act(async () => player.emit("render"));
  await fire(STALL_TIMEOUT_MS);
  expect(screen.queryByText(/已切换基础视频预览/) === null).toBe(true);
  await fire(STALL_TIMEOUT_MS);
  expect(screen.getByText(fallbackNote("播放 10 秒没有前进"))).toBeTruthy();
});

// 回归：IMS 加载（下载 SDK、载入时间线、等 canplay，最长要一两分钟）期间播放按钮、进度条与时间轴都停用，AI 资产只能看第一帧。
// IMS 就绪前由前端时钟推进播放位置：照常播放、定位，AI 资产与时间轴跟随，修改模板后回到开头。
test("IMS 还在加载时前端时钟驱动播放、定位与 AI 资产", async () => {
  const { step } = frames();
  const setNow = clockAt();
  const draft = newDraft();
  const { view, player } = await mountWithSdk(draft, { sprites: [sprite] });
  const frame = screen.getByTitle<HTMLIFrameElement>("AI 资产预览：霓虹标题");
  const post = spyOn(frame.contentWindow!, "postMessage");
  const channel = new URL(frame.src).hash.slice(1);
  await act(async () => {
    window.dispatchEvent(new MessageEvent("message", { source: frame.contentWindow, data: { type: "imv-preview-ready", channel, sync: true } }));
  });
  const play = screen.getByRole<HTMLButtonElement>("button", { name: "播放" });
  expect(play.disabled).toBe(false);
  fireEvent.click(play);
  expect(screen.getByRole("button", { name: "暂停" })).toBeTruthy();
  setNow(2500);
  await step();
  await step();
  expect(post).toHaveBeenLastCalledWith({ type: "imv-preview-sync", channel, time: 1.5, duration: 5 }, "*");
  expect(screen.getAllByText("2.5 / 30.0 秒").length).toBeGreaterThan(0);

  // 定位暂停在目标位置。
  fireEvent.change(screen.getByRole("slider", { name: "预览进度" }), { target: { value: "12" } });
  expect(screen.getByRole("button", { name: "播放" })).toBeTruthy();
  expect(screen.getAllByText("12.0 / 30.0 秒").length).toBeGreaterThan(0);

  // 修改模板后回到开头。
  view.rerender(<TemplatePreview {...props({ ...draft })} sprites={[sprite]} />);
  expect(screen.getAllByText("0.0 / 30.0 秒").length).toBeGreaterThan(0);
  expect(player.play).not.toHaveBeenCalled();
});

// 回归：IMS 还在加载时由前端时钟播放，状态栏也必须反映播放、暂停和结束，而不是一直显示加载中。
test("IMS 加载时前端时钟播放、暂停与结束更新状态", async () => {
  const { step } = frames();
  const setNow = clockAt();
  await mountWithSdk(newDraft());
  fireEvent.click(screen.getByRole("button", { name: "播放" }));
  expect(screen.getByText("正在播放预览…")).toBeTruthy();
  setNow(2000);
  await step();
  fireEvent.click(screen.getByRole("button", { name: "暂停" }));
  expect(screen.getByText("预览已暂停")).toBeTruthy();
  setNow(30000);
  fireEvent.click(screen.getByRole("button", { name: "播放" }));
  setNow(60000);
  await step();
  expect(screen.getByText("预览结束，点击播放可重播")).toBeTruthy();
});

// 回归：IMS 就绪时预览回到开头。IMS 就绪后由 SDK 从前端时钟的当前位置接着播放，AI 资产与时间轴不跳回开头。
test("IMS 就绪时 SDK 从前端时钟的位置接着播放", async () => {
  const { step } = frames();
  const setNow = clockAt();
  const { player } = await mountWithSdk(newDraft());
  fireEvent.click(screen.getByRole("button", { name: "播放" }));
  setNow(2000);
  await step();
  await act(async () => { player.finishTimeline(); });
  await act(async () => player.emit("canplay"));
  await waitFor(() => expect(player.currentTime).toBe(2));
  expect(player.play).not.toHaveBeenCalled();
  await act(async () => player.emit("playerSeeked", { currentTime: 2 }));
  await waitFor(() => expect(player.play).toHaveBeenCalledTimes(1));
  expect(screen.getByRole("button", { name: "暂停" })).toBeTruthy();
  // 之后由 SDK 推进时间。
  player.currentTime = 2.4;
  await act(async () => player.emit("render"));
  expect(screen.getAllByText("2.4 / 30.0 秒").length).toBeGreaterThan(0);
});
