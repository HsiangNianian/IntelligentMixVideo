/**
 * IMS 不可用时的基础视频预览：画布与 AI 资产不等 IMS 加载，降级早于 SDK 加载时时间轴行照常显示；主控件驱动三段视频
 * （换段、定位、结尾重播、只播解析出的段长），播放按钮、进度条、时间轴与文字选择框可用，AI 资产逐帧跟随，
 * 基础视频可以播放时接着前端时钟的位置与播放状态；Happy DOM 不解码视频，媒体元素的播放位置由用例设置；执行 bun run test。
 */
import { expect, mock, spyOn, test } from "bun:test";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRef } from "react";
import { BasicVideoPreview, clipAt } from "@/features/templates/BasicVideoPreview";
import type { PlaybackHandle } from "@/features/templates/previewClock";
import { TemplatePreview } from "@/features/templates/TemplatePreview";
import { newDraft } from "@/features/templates/model";
import { previewContext, resolveTemplate, type ResolvedTimeline } from "@/features/templates/tracks";
import { sampleMedia } from "./fixtures";
import { frames } from "./frames";

// 三段各 3 秒；素材本身 10 秒，比段长更长，基础视频只能播到段尾就换段。
const resolved: ResolvedTimeline = { duration: 9, clips: [{ start: 0, end: 3 }, { start: 3, end: 6 }, { start: 6, end: 9 }], tracks: {}, transitions: [], notices: [] };

/** 给媒体元素换上可控的播放位置与播放、暂停探针。 */
function controllable(video: HTMLVideoElement) {
  let position = 0;
  Object.defineProperty(video, "currentTime", { configurable: true, get: () => position, set: (value: number) => { position = value; } });
  Object.defineProperty(video, "ended", { configurable: true, get: () => false });
  const play = mock(() => Promise.resolve());
  const pause = mock();
  Object.assign(video, { play, pause });
  return { video, play, pause };
}

/** 当前显示的那段视频（只有它带“基础视频预览”标签）。 */
const shown = () => screen.getByLabelText<HTMLVideoElement>("基础视频预览");

// 显示在 1～4 秒的 AI 资产，素材完整时长 5 秒。
const sprite = { id: "sprite-a", spriteId: "s1", aspect: 9 / 16, name: "霓虹标题", start: 1, end: 4, length: 5 };

/** 挂载模板预览；返回时 IMS 还没开始加载（播放器页面在下一个微任务才创建）。 */
function mountPreview(props: Partial<Parameters<typeof TemplatePreview>[0]> = {}) {
  const draft = newDraft();
  const view = render(<TemplatePreview draft={draft} media={sampleMedia} resolved={resolveTemplate(draft, previewContext(sampleMedia))}
    videoInputKey="basic" onMediaChange={() => {}} onCatalog={() => {}} {...props} />);
  return { draft, view };
}

/** 挂载模板预览并等它降级：测试环境不加载外部脚本，SDK 脚本“加载完成”后没有播放器，SDK 加载阶段就降级为基础视频。 */
async function mountEarlyFallback(props: Partial<Parameters<typeof TemplatePreview>[0]> = {}) {
  const mounted = mountPreview(props);
  await screen.findByRole("button", { name: "重试 IMS 预览" });
  return mounted;
}

// 回归：IMS 加载失败后预览只剩原生视频，主控件与时间轴都不能用。基础视频改由主控件驱动：逐帧回报时间、到段尾换下一段、结尾停在总时长。
test("基础视频由主控件播放、换段并在结尾停下", async () => {
  const { step, count } = frames();
  const ref = createRef<PlaybackHandle>();
  const onTime = mock();
  const onPlayingChange = mock();
  const onEnd = mock();
  const { container } = render(<BasicVideoPreview ref={ref} media={sampleMedia} resolved={resolved} order={[0, 1, 2]}
    onTime={onTime} onReady={() => {}} onError={() => {}} onPlayingChange={onPlayingChange} onEnd={onEnd} />);
  const [first, second, third] = [...container.querySelectorAll("video")].map(controllable);
  expect([first, second, third].map(({ video }) => video.getAttribute("src"))).toEqual(sampleMedia.map((item) => item.url));
  expect(first.video.muted).toBe(true);
  expect(shown()).toBe(first.video);

  await act(async () => ref.current!.play());
  expect(onPlayingChange).toHaveBeenLastCalledWith(true);
  expect(first.play).toHaveBeenCalledTimes(1);
  first.video.currentTime = 1.5;
  await step();
  expect(onTime).toHaveBeenLastCalledWith(1.5);

  // 素材还没播完，但已到解析出的段尾：切换到第二段并从开头继续播放。
  first.video.currentTime = 3;
  await step();
  expect(shown()).toBe(second.video);
  expect(first.pause).toHaveBeenCalled();
  expect(second.video.currentTime).toBe(0);
  expect(second.play).toHaveBeenCalledTimes(1);
  expect(onTime).toHaveBeenLastCalledWith(3);

  // 定位到第三段中间：暂停，显示第三段并定位到段内位置。
  await act(async () => ref.current!.seek(7.5));
  expect(onPlayingChange).toHaveBeenLastCalledWith(false);
  expect(shown()).toBe(third.video);
  expect(third.video.currentTime).toBe(1.5);
  expect(onTime).toHaveBeenLastCalledWith(7.5);

  await act(async () => ref.current!.play());
  third.video.currentTime = 3;
  await step();
  expect(onTime).toHaveBeenLastCalledWith(9);
  expect(onEnd).toHaveBeenCalledTimes(1);
  expect(count()).toBe(0);

  // 结尾再点播放从头开始。
  await act(async () => ref.current!.play());
  expect(shown()).toBe(first.video);
  expect(first.video.currentTime).toBe(0);
  expect(first.play).toHaveBeenCalledTimes(2);
  expect(onTime).toHaveBeenLastCalledWith(0);
});

// 场景：片段首尾相接，交界处属于后一段，超出结尾仍算最后一段；视频按排列顺序对应片段。
test("全局时间换算到片段，并按排列顺序加载视频", () => {
  expect([0, 2.99, 3, 8.9, 9, 12].map((time) => clipAt(resolved.clips, time))).toEqual([0, 0, 1, 2, 2, 2]);
  const { container } = render(<BasicVideoPreview ref={createRef()} media={sampleMedia} resolved={resolved} order={[2, 0, 1]}
    onTime={() => {}} onReady={() => {}} onError={() => {}} onPlayingChange={() => {}} onEnd={() => {}} />);
  expect([...container.querySelectorAll("video")].map((video) => video.getAttribute("src"))).toEqual([sampleMedia[2].url, sampleMedia[0].url, sampleMedia[1].url]);
});

// 回归：画布尺寸要等 IMS 预览字体下载完、或降级时才设置，在此之前 AI 资产叠加层（Remotion 字效等）不显示；字体下载失败还会让预览降级。
// 画布固定为 1080×1920，挂载时就显示画布与 AI 资产，不等 IMS 的任何一步；预览不再下载这份没有使用的字体。
test("IMS 还没加载时画布与 AI 资产已显示", async () => {
  mountPreview({ sprites: [sprite] });
  expect(screen.queryByTitle("模板预览播放器") === null).toBe(true);
  expect(screen.getByText("1080 × 1920")).toBeTruthy();
  expect(screen.getByTitle("AI 资产预览：霓虹标题")).toBeTruthy();
  // 测试环境没有 SDK，预览随后降级；等它结束，状态更新不落到用例之外。
  await screen.findByRole("button", { name: "重试 IMS 预览" });
});

// 回归：降级早于 SDK 加载时时间轴没有对象与视频行。降级只能停用 IMS 侧的文字与特效，时间轴用随包效果目录按草稿生成。
test("降级早于 SDK 加载时时间轴行照常显示", async () => {
  await mountEarlyFallback({ sprites: [sprite] });
  const rows = [...screen.getByLabelText("时间轴轨道标签").querySelectorAll("[data-object]")].map((label) => label.getAttribute("data-object"));
  expect(rows).toEqual(expect.arrayContaining(["title", "subtitle", "transition", "video"]));
});

// 回归：降级后播放按钮、进度条、时间轴和画面上的文字选择框都被禁用，预览只剩原生视频。它们都不属于 IMS 侧，必须照常可用并控制基础视频。
test("降级为基础视频后主控件、时间轴与选择框仍可用", async () => {
  frames();
  const onSelect = mock();
  const { draft, view } = await mountEarlyFallback({ onSelect });
  expect(screen.getByText("IMS 渲染暂不可用（播放器初始化失败：SDK 未正确初始化），已切换基础视频预览：AI 资产照常显示，IMS 文字、转场、滤镜与特效暂不显示。")).toBeTruthy();
  const play = screen.getByRole<HTMLButtonElement>("button", { name: "播放" });
  // 基础视频还没读到元数据时由前端时钟推进，控件已经可用。
  expect(play.disabled).toBe(false);
  const videos = [...view.container.querySelectorAll("video")].map(controllable);
  await act(async () => { fireEvent.loadedMetadata(videos[0].video); });
  expect(play.disabled).toBe(false);
  expect(screen.getByRole<HTMLInputElement>("slider", { name: "预览进度" }).disabled).toBe(false);
  expect(screen.getByRole("slider", { name: "预览播放位置" }).getAttribute("aria-disabled")).toBe("false");
  fireEvent.click(screen.getByRole("button", { name: "选择中文标题" }));
  expect(onSelect).toHaveBeenCalledWith(draft.tracks.find((item) => item.target === "title")!.id);

  fireEvent.click(play);
  expect(videos[0].play).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(screen.getByRole("button", { name: "暂停" })).toBeTruthy());

  // 拖动进度条定位到第二段：暂停并显示第二段，界面时间随之更新。
  fireEvent.change(screen.getByRole("slider", { name: "预览进度" }), { target: { value: "12" } });
  await waitFor(() => expect(shown()).toBe(videos[1].video));
  expect(videos[1].video.currentTime).toBeCloseTo(2, 5);
  expect(screen.getByRole("button", { name: "播放" })).toBeTruthy();
  expect(screen.getAllByText("12.0 / 30.0 秒").length).toBeGreaterThan(0);
});

// 回归：基础视频只按 timeupdate（约每秒 4 次）回报时间，AI 资产跳着动。现在随基础视频的播放位置逐帧定位（全局时间减片段起点）。
test("降级后 AI 资产逐帧跟随基础视频", async () => {
  const { step } = frames();
  const { view } = await mountEarlyFallback({ sprites: [sprite] });
  const frame = await screen.findByTitle<HTMLIFrameElement>("AI 资产预览：霓虹标题");
  const post = spyOn(frame.contentWindow!, "postMessage");
  const channel = new URL(frame.src).hash.slice(1);
  await act(async () => {
    window.dispatchEvent(new MessageEvent("message", { source: frame.contentWindow, data: { type: "imv-preview-ready", channel, sync: true } }));
  });
  const videos = [...view.container.querySelectorAll("video")].map(controllable);
  await act(async () => { fireEvent.loadedMetadata(videos[0].video); });
  fireEvent.click(screen.getByRole("button", { name: "播放" }));
  videos[0].video.currentTime = 2.5;
  await step();
  await step();
  expect(post).toHaveBeenLastCalledWith({ type: "imv-preview-sync", channel, time: 1.5, duration: 5 }, "*");
  videos[0].video.currentTime = 3.25;
  await step();
  await step();
  expect(post).toHaveBeenLastCalledWith({ type: "imv-preview-sync", channel, time: 2.25, duration: 5 }, "*");
});

// 回归：基础视频读到元数据、可以播放时预览从开头开始，之前的播放位置与播放状态丢失。
// 基础视频接着前端时钟的位置与播放状态播放。
test("基础视频可以播放时接着前端时钟的位置播放", async () => {
  const { step } = frames();
  let now = 0;
  spyOn(performance, "now").mockImplementation(() => now);
  const { view } = await mountEarlyFallback();
  fireEvent.click(screen.getByRole("button", { name: "播放" }));
  now = 4000;
  await step();
  expect(screen.getAllByText("4.0 / 30.0 秒").length).toBeGreaterThan(0);
  const videos = [...view.container.querySelectorAll("video")].map(controllable);
  await act(async () => { fireEvent.loadedMetadata(videos[0].video); });
  expect(shown()).toBe(videos[0].video);
  expect(videos[0].video.currentTime).toBeCloseTo(4, 5);
  expect(videos[0].play).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "暂停" })).toBeTruthy();
});
