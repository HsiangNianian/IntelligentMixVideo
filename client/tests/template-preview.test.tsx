/**
 * 模板预览中不属于 IMS 的部分不等 IMS：测试环境不加载外部脚本，用例在播放器页面挂上 SDK 替身，
 * 由用例决定载入时间线与 canplay 的结果，检查 IMS 还在加载、卡住或出错时时间轴照常可用，卡住、出错或播放不前进时切换基础视频，
 * IMS 就绪前由前端时钟推进播放位置、就绪后接着播放；执行 bun run test。
 */
import { afterEach, beforeEach, expect, mock, spyOn, test } from "bun:test";
import { useState, type ReactNode } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TemplatePreview } from "@/features/templates/TemplatePreview";
import { newDraft, type Draft, type PreviewMedia } from "@/features/templates/model";
import { newFrameMask } from "@/features/templates/frameMask";
import { CAN_PLAY_TIMEOUT_MS, readCatalog, STALL_TIMEOUT_MS, type PlayerEvent } from "@/features/templates/sdk";
import { addTrack, previewContext, resolveTemplate } from "@/features/templates/tracks";
import { sampleMedia } from "./fixtures";
import { frames } from "./frames";
import { defaultSplitScreen } from "@/features/templates/splitScreen";
import { defaultAvatarEffects } from "@/features/templates/avatarEffects";
import { AvatarEffectsEditor } from "@/features/templates/AvatarEffectsEditor";
import { AppliedEffects } from "@/features/templates/EffectAssets";

// Happy DOM 用浮点取余误拒绝 1.8/0.1 等合法小数；这里只验证空值和范围，原生步长由浏览器回归验证。
const stepValidity = Object.getOwnPropertyDescriptor(ValidityState.prototype, "stepMismatch")!;
beforeEach(() => { Object.defineProperty(ValidityState.prototype, "stepMismatch", { get: () => false, configurable: true }); });
afterEach(() => { Object.defineProperty(ValidityState.prototype, "stepMismatch", stepValidity); });

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
async function mountWithSdk(draft: Draft, extra: Partial<Parameters<typeof TemplatePreview>[0]> = {}, content?: ReactNode) {
  FakePlayer.last = undefined;
  const append = Element.prototype.append;
  spyOn(Element.prototype, "append").mockImplementation(function (this: Element, ...nodes: (Node | string)[]) {
    append.apply(this, nodes);
    for (const node of nodes)
      if (node instanceof HTMLIFrameElement && node.title === "模板预览播放器") Reflect.set(node.contentWindow!, "AliyunTimelinePlayer", FakePlayer);
  });
  const view = render(content ?? <TemplatePreview {...props(draft)} {...extra} />);
  await waitFor(() => expect(FakePlayer.last?.setTimeline.mock.calls.length).toBe(1));
  return { view, player: FakePlayer.last! };
}

/** 时间轴各行的对象类型。 */
const rowObjects = () => [...screen.getByLabelText("时间轴轨道标签").querySelectorAll("[data-object]")].map((label) => label.getAttribute("data-object"));

/** 使用实际数字输入更新草稿，复现清空、停顿与重新填写的编辑过程。 */
function AvatarPreviewEditor({ initial, media = sampleMedia }: { initial: Draft; media?: PreviewMedia }) {
  const [draft, setDraft] = useState(initial);
  const resolved = resolveTemplate(draft, previewContext(media));
  return <><AvatarEffectsEditor value={draft.avatar_effects!} onChange={(avatar_effects) => setDraft({ ...draft, avatar_effects })} onClose={() => {}} onRemove={() => {}} />
    <AppliedEffects draft={draft} catalog={[]} resolved={resolved} selected={null} onSelect={() => {}} />
    <TemplatePreview {...props(draft)} media={media} resolved={resolved} /></>;
}

// 回归：临时空值或越界值不能触发永久降级；修正后同一 SDK 实例恢复更新，合法的零羽化也可应用。
test.each([
  ["圆边羽化", "", "0"],
  ["头部圆形留量（倍）", "", "1.8"],
  ["开场时长（秒）", "", "3"],
  ["圆边羽化", "0.3", "0.1"],
])("%s 从无效值 %s 修正为 %s 自动恢复预览", async (label, invalid, valid) => {
  spyOn(HTMLImageElement.prototype, "decode").mockResolvedValue();
  spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ drawImage() {} } as unknown as CanvasRenderingContext2D);
  spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue("data:image/png;base64,cHJldmlldw==");
  const draft = { ...newDraft(), avatar_effects: { ...defaultAvatarEffects(), openingEnabled: true } };
  const { player } = await mountWithSdk(draft, {}, <AvatarPreviewEditor initial={draft} />);
  await act(async () => player.finishTimeline());
  await act(async () => player.emit("canplay"));
  player.currentTime = 1;
  await act(async () => player.emit("render"));
  const rows = rowObjects();
  const input = screen.getByLabelText<HTMLInputElement>(label);
  fireEvent.change(input, { target: { value: invalid } });
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 400)); });
  expect(input.value).toBe(invalid);
  expect(screen.getByRole("alert").textContent).toContain("预览参数无效");
  expect(rowObjects()).toEqual(rows);
  expect(player.currentTime).toBe(1);
  expect(player.setTimeline).toHaveBeenCalledTimes(1);
  expect(screen.queryByText(/已切换基础视频预览/)).toBeNull();
  fireEvent.change(input, { target: { value: valid } });
  await waitFor(() => expect(player.setTimeline).toHaveBeenCalledTimes(2));
  await act(async () => player.finishTimeline());
  await act(async () => player.emit("canplay"));
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByText("预览已就绪，点击播放查看效果")).toBeTruthy();
  expect(player.currentTime).toBe(0);
  expect(player.pause).toHaveBeenCalled();
  expect(player.destroy).not.toHaveBeenCalled();
  expect(FakePlayer.last).toBe(player);
  expect(screen.queryByText(/已切换基础视频预览/)).toBeNull();
});

// 无效输入不得污染其他草稿消费者；换媒体仍生成完整轨道，真实 SDK 失败后基础预览仍用有效几何。
test.each([["开场时长（秒）", ""], ["头部圆形留量（倍）", ""], ["圆边羽化", "0.3"]])("%s 输入 %s 后换媒体仍保留对象、轨道及基础蒙版", async (label, invalid) => {
  spyOn(HTMLImageElement.prototype, "decode").mockResolvedValue();
  spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ drawImage() {} } as unknown as CanvasRenderingContext2D);
  spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue("data:image/png;base64,cHJldmlldw==");
  const initial = { ...newDraft(), avatar_effects: { ...defaultAvatarEffects(), openingEnabled: true }, split_screen: { ...defaultSplitScreen(), enabled: true, layout: "circle" as const } };
  const { view, player } = await mountWithSdk(initial, {}, <AvatarPreviewEditor initial={initial} />);
  await act(async () => player.finishTimeline());
  await act(async () => player.emit("canplay"));
  const rows = rowObjects();
  fireEvent.change(screen.getByLabelText(label), { target: { value: invalid } });
  expect(screen.getByText("开场显示 · 2 秒")).toBeTruthy();
  const media: PreviewMedia = [{ ...sampleMedia[0], url: "http://localhost/updated-0.mp4" }, sampleMedia[1], sampleMedia[2]];
  view.rerender(<AvatarPreviewEditor initial={initial} media={media} />);
  await waitFor(() => { expect(FakePlayer.last).not.toBe(player); expect(FakePlayer.last?.setTimeline).toHaveBeenCalledTimes(1); });
  const next = FakePlayer.last!;
  await act(async () => next.finishTimeline());
  await act(async () => next.emit("canplay"));
  expect(rowObjects()).toEqual(rows);
  expect(screen.getByText("3 个对象")).toBeTruthy();
  expect(screen.getByLabelText<HTMLInputElement>(label).value).toBe(invalid);
  expect(JSON.stringify(next.setTimeline.mock.calls[0][0])).toContain("updated-0.mp4");
  await act(async () => next.emit("renderError", { error: "测试渲染失败" }));
  for (const name of ["圆形开场预览", "数字人同屏预览"]) {
    const mask = screen.getByLabelText<HTMLVideoElement>(name).style.maskImage;
    expect(mask).toContain("radial-gradient");
    expect(mask).not.toContain("NaN");
  }
});

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
  expect(rowObjects()).toContain("frame-mask");
  view.rerender(<TemplatePreview {...stable} draft={draft} />);
  expect(rowObjects()).not.toContain("frame-mask");
  expect(screen.queryByLabelText("上栏蒙版预览")).toBeNull();
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

// 取景和羽化变化必须重新应用 SDK 时间线，预览不得反向修改模板草稿。
test("工作区传入预览圆心和羽化后刷新 SDK 并暂停回零，不改变草稿", async () => {
  spyOn(HTMLImageElement.prototype, "decode").mockResolvedValue();
  spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ drawImage() {} } as unknown as CanvasRenderingContext2D);
  spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue("data:image/png;base64,cHJldmlldw==");
  const draft = { ...newDraft(), avatar_effects: { ...defaultAvatarEffects(), openingEnabled: true } };
  const original = JSON.stringify(draft), stable = props(draft);
  const { view, player } = await mountWithSdk(draft, { resolved: stable.resolved });
  /** 完成当前更新，模拟 SDK 就绪后播放到中间。 */
  const ready = async () => { await act(async () => { player.finishTimeline(); }); await act(async () => { player.emit("canplay"); }); };
  await ready();
  player.currentTime = 1;
  await act(async () => player.emit("timeupdate", { currentTime: 1 }));
  view.rerender(<TemplatePreview {...stable} previewCenter={{ x: 30, y: 50 }} />);
  await waitFor(() => expect(player.setTimeline).toHaveBeenCalledTimes(2));
  await ready();
  expect(player.currentTime).toBe(0);
  const timeline = player.setTimeline.mock.calls[1][0] as { VideoTracks: { VideoTrackClips: { MediaURL?: string }[] }[] };
  const id = timeline.VideoTracks.flatMap((row) => row.VideoTrackClips).find((clip) => clip.MediaURL?.includes("#imv-mask="))!.MediaURL!;
  const mask = await player.mediaInfo!(id, "Video", "mediaURL") as { maskUrl: string };
  expect(new URL(mask.maskUrl).searchParams.get("x")).toBe("0.3");
  view.rerender(<TemplatePreview {...stable} draft={{ ...draft, avatar_effects: { ...draft.avatar_effects, feather: 0.2 } }} />);
  await waitFor(() => expect(player.setTimeline).toHaveBeenCalledTimes(3));
  await ready();
  expect(screen.queryByLabelText("数字人预览来源")).toBeNull();
  expect(JSON.stringify(draft)).toBe(original);
});

// 开启抠像也不能把字体、埋点或不明网络错误误判成模型故障；后续合法参数仍可更新同一 SDK。
test("无关 Promise 拒绝不使抠像和 IMS 时间线降级", async () => {
  const draft = { ...newDraft(), split_screen: { ...defaultSplitScreen(), enabled: true, layout: "overlay" as const }, avatar_effects: { ...defaultAvatarEffects(), removeBackground: true } };
  const { view, player } = await mountWithSdk(draft);
  await act(async () => player.finishTimeline());
  await act(async () => player.emit("canplay"));
  const frame = screen.getByTitle<HTMLIFrameElement>("模板预览播放器");
  for (const reason of [new TypeError("Failed to fetch https://g.alicdn.com/font/x.woff2"), new Error("analytics request failed"), new TypeError("Failed to fetch")]) {
    await act(async () => frame.contentWindow!.dispatchEvent(Object.assign(new Event("unhandledrejection"), { reason })));
    expect(screen.queryByText(/已切换基础视频预览/)).toBeNull();
  }
  view.rerender(<TemplatePreview {...props({ ...draft, avatar_effects: { ...draft.avatar_effects, feather: 0.1 } })} />);
  await waitFor(() => expect(player.setTimeline).toHaveBeenCalledTimes(2));
  await act(async () => player.finishTimeline());
  await act(async () => player.emit("canplay"));
  expect(player.destroy).not.toHaveBeenCalled();
  expect(screen.getByText("预览已就绪，点击播放查看效果")).toBeTruthy();
});

// 可归因于模型的拒绝仍需切换基础播放器并明确说明抠像不可用。
test("SDK 抠像模型未处理的拒绝明确降级，保留原背景并提示差异", async () => {
  const draft = { ...newDraft(), split_screen: { ...defaultSplitScreen(), enabled: true, layout: "overlay" as const }, avatar_effects: { ...defaultAvatarEffects(), removeBackground: true } };
  const { player } = await mountWithSdk(draft);
  await act(async () => { player.finishTimeline(); });
  const frame = screen.getByTitle<HTMLIFrameElement>("模板预览播放器");
  await act(async () => {
    const event = new Event("unhandledrejection");
    Object.defineProperty(event, "reason", { value: new Error("model unavailable: https://g.alicdn.com/sdk/wasm/starry/model/v_tfmodel/model.json") });
    frame.contentWindow!.dispatchEvent(event);
  });
  expect(screen.getByText(/SDK 抠像预览不可用，当前保留原视频背景/)).toBeTruthy();
  expect(screen.getByLabelText("数字人同屏预览")).toBeTruthy();
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
