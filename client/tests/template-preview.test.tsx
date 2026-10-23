/**
 * 模板预览中不属于 IMS 的部分不等 IMS：测试环境不加载外部脚本，用例在播放器页面挂上 SDK 替身，
 * 由用例决定载入时间线与 canplay 的结果，检查 IMS 还在加载、卡住或出错时时间轴照常可用；执行 bun run test。
 */
import { expect, mock, spyOn, test } from "bun:test";
import { render, screen, waitFor } from "@testing-library/react";
import { TemplatePreview } from "@/features/templates/TemplatePreview";
import { newDraft, type Draft } from "@/features/templates/model";
import { readCatalog, type PlayerEvent } from "@/features/templates/sdk";
import { addTrack, previewContext, resolveTemplate } from "@/features/templates/tracks";
import { sampleMedia } from "./fixtures";

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
  finishTimeline: () => void = () => {};
  failTimeline: (error: Error) => void = () => {};
  readonly setTimeline = mock(() => new Promise<void>((resolve, reject) => { this.finishTimeline = resolve; this.failTimeline = reject; }));
  /** 登记为最近创建的播放器。 */
  constructor() {
    FakePlayer.last = this;
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
async function mountWithSdk(draft: Draft) {
  FakePlayer.last = undefined;
  const append = Element.prototype.append;
  spyOn(Element.prototype, "append").mockImplementation(function (this: Element, ...nodes: (Node | string)[]) {
    append.apply(this, nodes);
    for (const node of nodes)
      if (node instanceof HTMLIFrameElement && node.title === "模板预览播放器") Reflect.set(node.contentWindow!, "AliyunTimelinePlayer", FakePlayer);
  });
  const view = render(<TemplatePreview {...props(draft)} />);
  await waitFor(() => expect(FakePlayer.last?.setTimeline.mock.calls.length).toBe(1));
  return { view, player: FakePlayer.last! };
}

/** 时间轴各行的对象类型。 */
const rowObjects = () => [...screen.getByLabelText("时间轴轨道标签").querySelectorAll("[data-object]")].map((label) => label.getAttribute("data-object"));

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
