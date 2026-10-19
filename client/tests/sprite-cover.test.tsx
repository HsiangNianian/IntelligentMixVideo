/** AI 资产封面核心测试：进入视口才加载、定格帧定位与悬停轮播、旧资产降级，以及目录卡片的搜索浏览；执行 bun run test。 */
import { afterEach, beforeEach, expect, spyOn, test } from "bun:test";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SpriteCover } from "@/features/templates/SpriteCover";
import { SpriteCatalog } from "@/features/templates/SpritePanels";
import type { useSpriteCatalog } from "@/features/templates/useSpriteCatalog";
import { SpriteKind } from "@/generated/imv/sprite/v1/sprite_pb";
import type { SpriteAsset } from "@/features/sprites/model";

const asset: SpriteAsset = { id: "s1", name: "霓虹标题", kind: SpriteKind.TEXT, seconds: 3, aspect: 9 / 16, width: 1080, height: 1920, fps: 30, box: { x: 0, y: 0, width: 1080, height: 1920 }, parameters: [] };

/** 可手动触发的 IntersectionObserver：测试决定卡片何时进入或离开视口。 */
let observers: { callback: IntersectionObserverCallback; element?: Element }[] = [];
const originalObserver = globalThis.IntersectionObserver;

beforeEach(() => {
  observers = [];
  globalThis.IntersectionObserver = class {
    private entry: (typeof observers)[number];
    constructor(callback: IntersectionObserverCallback) { this.entry = { callback }; observers.push(this.entry); }
    observe(element: Element) { this.entry.element = element; }
    disconnect() { observers = observers.filter((item) => item !== this.entry); }
    unobserve() {}
    takeRecords() { return []; }
  } as unknown as typeof IntersectionObserver;
});
afterEach(() => { globalThis.IntersectionObserver = originalObserver; });

/** 让所有观察者报告可见性变化。 */
const intersect = (isIntersecting: boolean) => act(async () => {
  for (const { callback } of observers) callback([{ isIntersecting } as IntersectionObserverEntry], {} as IntersectionObserver);
});

/** 取封面播放器和通道，并模拟页面按协议回报就绪；sync 表示该资产的包是否支持逐帧驱动。 */
function player() {
  const frame = screen.getByTitle<HTMLIFrameElement>("AI 资产封面：霓虹标题");
  const channel = new URL(frame.src).hash.slice(1);
  const post = spyOn(frame.contentWindow!, "postMessage");
  const send = (data: object) => act(async () => { window.dispatchEvent(new MessageEvent("message", { source: frame.contentWindow, data: { channel, ...data } })); });
  return { frame, channel, post, ready: (sync: boolean | undefined) => send({ type: "imv-preview-ready", sync }), send };
}

// 场景：卡片没进入视口前不加载预览页；进入后使用不带控制条的同步页，离开视口即卸载。
test("封面进入视口才加载播放器，离开视口卸载", async () => {
  render(<SpriteCover asset={asset} active={false} />);
  expect(screen.queryByTitle("AI 资产封面：霓虹标题")).toBeNull();
  await intersect(true);
  const { frame, channel } = player();
  expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
  expect(frame.src).toBe(`http://api.test:8000/api/sprites/s1/preview?sync=1#${channel}`);
  await intersect(false);
  expect(screen.queryByTitle("AI 资产封面：霓虹标题")).toBeNull();
});

// 场景：就绪后定格到时长中点（3 秒的 50% 即 1.5 秒）并在定位后显示；陌生通道或来源的消息被忽略。
test("就绪后定格到中点帧并显示，忽略陌生消息", async () => {
  render(<SpriteCover asset={asset} active={false} />);
  await intersect(true);
  const { frame, channel, post, ready } = player();
  expect(frame.style.visibility).toBe("hidden");
  await act(async () => { window.dispatchEvent(new MessageEvent("message", { source: window, data: { type: "imv-preview-ready", channel, sync: true } })); });
  expect(post).not.toHaveBeenCalled();
  await ready(true);
  expect(post).toHaveBeenCalledWith({ type: "imv-preview-sync", channel, time: 1.5 }, "*");
  await waitFor(() => expect(frame.style.visibility).toBe("visible"));
});

// 场景：卡片被悬停时依次轮播其余定格帧（20%、80%），离开后回到中点静止封面。
test("悬停时轮播定格帧，离开后回到静止封面", async () => {
  const view = render(<SpriteCover asset={asset} active={false} />);
  await intersect(true);
  const { channel, post, ready } = player();
  await ready(true);
  view.rerender(<SpriteCover asset={asset} active />);
  await waitFor(() => expect(post).toHaveBeenLastCalledWith({ type: "imv-preview-sync", channel, time: 0.6 }, "*"));
  await waitFor(() => expect(post).toHaveBeenLastCalledWith({ type: "imv-preview-sync", channel, time: 2.4 }, "*"), { timeout: 1500 });
  view.rerender(<SpriteCover asset={asset} active={false} />);
  await waitFor(() => expect(post).toHaveBeenLastCalledWith({ type: "imv-preview-sync", channel, time: 1.5 }, "*"));
});

// 场景：用户要求减少动态效果时，悬停也不轮播。
test("减少动态效果时悬停不轮播", async () => {
  spyOn(window, "matchMedia").mockImplementation(((query: string) => ({ matches: query.includes("reduce"), media: query, addEventListener() {}, removeEventListener() {} })) as unknown as typeof window.matchMedia);
  const view = render(<SpriteCover asset={asset} active={false} />);
  await intersect(true);
  const { post, ready } = player();
  await ready(true);
  const calls = post.mock.calls.length;
  view.rerender(<SpriteCover asset={asset} active />);
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 900)); });
  expect(post.mock.calls.length).toBe(calls);
});

// 场景：旧版本资产不支持逐帧定位，播放器移除并显示“暂无封面”；预览页报错则显示加载失败。
test("旧资产显示暂无封面，预览页报错显示加载失败", async () => {
  const view = render(<SpriteCover asset={asset} active={false} />);
  await intersect(true);
  await player().ready(undefined);
  expect(screen.queryByTitle("AI 资产封面：霓虹标题")).toBeNull();
  expect(screen.getByText("暂无封面")).toBeTruthy();
  view.unmount();
  render(<SpriteCover asset={asset} active={false} />);
  await intersect(true);
  await player().send({ type: "imv-preview-error" });
  expect(screen.getByText("封面加载失败")).toBeTruthy();
});

// 场景：Remotion 目录按分类显示，分类内按名称搜索；卡片仍以“添加 AI 资产”为操作名并带封面播放器，点击把资产交给 onAdd。
test("Remotion 目录在分类内按名称搜索并添加", async () => {
  const added: SpriteAsset[] = [];
  const other = { ...asset, id: "s2", name: "流光字幕" };
  const catalog = { assets: [asset, other], error: "", refresh() {} } as unknown as ReturnType<typeof useSpriteCatalog>;
  render(<SpriteCatalog environment="cloud" catalog={catalog} placed={new Set()} onAdd={(item) => added.push(item)} />);
  const region = screen.getByRole("region", { name: "AI 资产" });
  expect(within(region).getAllByRole("radio").length).toBeGreaterThan(0);
  fireEvent.change(within(region).getByRole("searchbox", { name: "搜索 AI 资产" }), { target: { value: "流光" } });
  expect(within(region).queryByRole("button", { name: "添加 AI 资产：霓虹标题" })).toBeNull();
  fireEvent.click(within(region).getByRole("button", { name: "添加 AI 资产：流光字幕" }));
  expect(added).toEqual([other]);
  fireEvent.change(within(region).getByRole("searchbox", { name: "搜索 AI 资产" }), { target: { value: "不存在" } });
  expect(within(region).getByText("没有匹配的资产")).toBeTruthy();
});
