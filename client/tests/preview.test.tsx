/** SDK 预览行为测试：隔离播放器、字体和媒体请求，验证帧事件节流及资源清理。 */
import { expect, mock, test } from "bun:test";
import { act, render, screen } from "@testing-library/react";
import { TemplatePreview } from "@/features/templates/TemplatePreview";
import { newDraft } from "@/features/templates/model";
import type { Player, PreviewSDK } from "@/features/templates/sdk";
import { fetchMock } from "./setup";

/** 构造可驱动 render 事件的播放器，并返回测试所需的实例和订阅清理记录。 */
function installSDK() {
  let renderEvent: ((event: { type: string }) => void) | undefined;
  const unsubscribe = mock(() => {});
  const setTimeline = mock(async (_timeline: unknown) => {});
  const destroy = mock(() => {});
  let instance: Player | undefined;

  /** 以可控事件替代 SDK 播放器，记录时间线提交与资源释放。 */
  class FakePlayer implements Player {
    currentTime = 0;
    event$ = {
      subscribe: (callback: (event: { type: string }) => void) => {
        renderEvent = callback;
        return { unsubscribe };
      },
    };

    constructor() {
      instance = this;
    }

    /** 测试不启动真实媒体播放。 */
    play() {}

    /** 测试不启动真实媒体播放。 */
    pause() {}

    /** 记录组件卸载时是否释放 SDK 实例。 */
    destroy() {
      destroy();
    }

    /** 记录初始化和草稿更新提交的时间线。 */
    setTimeline(timeline: unknown) {
      return setTimeline(timeline);
    }
  }

  const sdk = FakePlayer as unknown as PreviewSDK;
  sdk.getSubtitleEffectColorStyles = () => [];
  sdk.getSubtitleBubbles = () => [];
  sdk.getVideoFilters = () => [];
  sdk.getVideoEffects = () => [];
  sdk.getVideoTransitions = () => [];
  window.AliyunTimelinePlayer = sdk;
  return {
    destroy,
    emit(time: number) {
      if (!instance || !renderEvent) throw new Error("播放器尚未初始化");
      instance.currentTime = time;
      renderEvent({ type: "render" });
    },
    setTimeline,
    unsubscribe,
  };
}

// 同一十分之一秒内数字时间只更新一次，游标独立跟随帧事件；卸载释放订阅和播放器。
test("预览数字时间限制更新频率并清理播放器", async () => {
  const sdk = installSDK();
  const originalFontFace = globalThis.FontFace;
  const originalFonts = document.fonts;
  /** 避免测试解析真实字体内容，同时保留异步加载契约。 */
  class FakeFontFace {
    /** 字体测试替身直接完成加载，不解析空响应内容。 */
    async load() {
      return this;
    }
  }
  Reflect.set(globalThis, "FontFace", FakeFontFace);
  Reflect.set(document, "fonts", { add: () => {} });
  fetchMock.mockResolvedValueOnce(new Response(new ArrayBuffer(0)));
  const changes: string[] = [];
  const observer = new MutationObserver(() => {
    changes.push(screen.getByText(/^16:9 ·/).textContent ?? "");
  });

  try {
    const view = render(
      <TemplatePreview draft={newDraft()} onCatalog={() => {}} />,
    );
    await screen.findByText("预览已就绪，点击播放查看效果");
    expect(sdk.setTimeline).toHaveBeenCalledTimes(1);
    observer.observe(screen.getByText(/^16:9 ·/), { characterData: true, childList: true, subtree: true });

    await act(async () => sdk.emit(1.01));
    await act(async () => sdk.emit(1.02));
    await act(async () => sdk.emit(1.04));
    expect(screen.getByText("16:9 · 1.0 / 10 秒")).toBeTruthy();
    expect(changes).toEqual(["16:9 · 1.0 / 10 秒"]);

    await act(async () => sdk.emit(1.06));
    expect(screen.getByText("16:9 · 1.1 / 10 秒")).toBeTruthy();
    expect(changes).toEqual(["16:9 · 1.0 / 10 秒", "16:9 · 1.1 / 10 秒"]);

    view.unmount();
    expect(sdk.unsubscribe).toHaveBeenCalledTimes(1);
    expect(sdk.destroy).toHaveBeenCalledTimes(1);
  } finally {
    observer.disconnect();
    delete window.AliyunTimelinePlayer;
    Reflect.set(globalThis, "FontFace", originalFontFace);
    Reflect.set(document, "fonts", originalFonts);
  }
});
