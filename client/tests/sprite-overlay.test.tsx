/** Remotion 资产叠加预览核心测试：就绪握手、按预览时间逐帧定位、区间外隐藏、旧资产降级；执行 bun run test。 */
import { expect, spyOn, test } from "bun:test";
import { act, render, screen, waitFor } from "@testing-library/react";
import { createRef } from "react";
import { SpriteOverlay, type SpriteOverlayHandle } from "@/features/templates/SpriteOverlay";

const clip = { id: "sprite-a", spriteId: "s1", aspect: 9 / 16, name: "霓虹标题", start: 1, end: 4 };

/** 渲染叠加层并取出其播放器，返回通道号与消息探针；Happy DOM 不执行 iframe，按协议手动模拟就绪。 */
function mount() {
  const ref = createRef<SpriteOverlayHandle>();
  render(<SpriteOverlay ref={ref} clips={[clip]} stageAspect={16 / 9} initialTime={0} />);
  const frame = screen.getByTitle<HTMLIFrameElement>("Remotion 资产预览：霓虹标题");
  const post = spyOn(frame.contentWindow!, "postMessage");
  const channel = new URL(frame.src).hash.slice(1);
  /** 播放器页面发出就绪通知；sync 表示该资产的包是否支持逐帧驱动。 */
  const ready = (sync: boolean | undefined) => act(async () => {
    window.dispatchEvent(new MessageEvent("message", { source: frame.contentWindow, data: { type: "imv-preview-ready", channel, sync } }));
  });
  const setTime = (value: number) => act(async () => { ref.current!.setTime(value); await new Promise((resolve) => requestAnimationFrame(() => resolve(undefined))); });
  return { frame, post, channel, ready, setTime };
}

// 场景：播放器指向资产的透明同步预览页，按 Cover 方式铺满：竖屏资产在横屏画面里取全宽、上下裁切。
test("叠加播放器使用透明同步页并按画面比例铺满", () => {
  const { frame, channel } = mount();
  expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
  expect(frame.src).toBe(`http://api.test:8000/api/sprites/s1/preview?overlay=true&sync=1#${channel}`);
  expect(frame.style.width).toBe("100%");
  expect(frame.style.height).toBe("");
  expect(frame.style.visibility).toBe("hidden");
});

// 场景：就绪前不发送定位；就绪后按预览时间减去片段起点逐帧定位，区间外隐藏且不再发送。
test("就绪后按预览时间定位，区间外隐藏", async () => {
  const { frame, post, channel, ready, setTime } = mount();
  await setTime(2.5);
  expect(post).not.toHaveBeenCalled();
  await ready(true);
  await waitFor(() => expect(post).toHaveBeenCalledWith({ type: "imv-preview-sync", channel, time: 1.5 }, "*"));
  expect(frame.style.visibility).toBe("visible");
  await setTime(3);
  expect(post).toHaveBeenLastCalledWith({ type: "imv-preview-sync", channel, time: 2 }, "*");
  const calls = post.mock.calls.length;
  await setTime(0.5);
  await setTime(4);
  expect(frame.style.visibility).toBe("hidden");
  expect(post.mock.calls.length).toBe(calls);
});

// 场景：旧版本包不会回报逐帧驱动能力，保持隐藏、不发送定位，并提示重新保存；其他来源或通道的消息被忽略。
test("旧版本资产保持隐藏并提示重新保存，忽略陌生消息", async () => {
  const { frame, post, ready, setTime } = mount();
  await act(async () => { window.dispatchEvent(new MessageEvent("message", { source: window, data: { type: "imv-preview-ready", channel: "x", sync: true } })); });
  await ready(undefined);
  await setTime(2);
  expect(post).not.toHaveBeenCalled();
  expect(frame.style.visibility).toBe("hidden");
  expect(screen.getByRole("status").textContent).toContain("请重新保存到资产");
});

// 回归：待处理的帧回调被卸载取消后，同一组件实例的后续定位必须仍能调度，否则资产永远保持隐藏。
test("卸载取消待处理帧后重新挂载仍能定位", async () => {
  const ref = createRef<SpriteOverlayHandle>();
  const view = render(<SpriteOverlay ref={ref} clips={[clip]} stageAspect={16 / 9} initialTime={2} />);
  view.unmount();
  document.body.innerHTML = "";
  const again = mount();
  await again.ready(true);
  await again.setTime(2);
  expect(again.frame.style.visibility).toBe("visible");
});

// 回归：先挂载空画布，再增删资产，已有 iframe 地址保持稳定，所有同步消息使用当前通道。
test("增删资产后每个播放器继续使用稳定通道同步", async () => {
  const ref = createRef<SpriteOverlayHandle>();
  const view = render(<SpriteOverlay ref={ref} clips={[]} stageAspect={1} initialTime={2} />);
  const second = { ...clip, id: "sprite-b", spriteId: "s2", name: "第二个资产" };
  view.rerender(<SpriteOverlay ref={ref} clips={[clip]} stageAspect={1} initialTime={2} />);
  const first = screen.getByTitle<HTMLIFrameElement>("Remotion 资产预览：霓虹标题");
  const originalSrc = first.src;
  view.rerender(<SpriteOverlay ref={ref} clips={[clip, second]} stageAspect={1} initialTime={2} />);
  expect(first.src).toBe(originalSrc);
  const other = screen.getByTitle<HTMLIFrameElement>("Remotion 资产预览：第二个资产");
  const frames = [first, other];
  const posts = frames.map((frame) => spyOn(frame.contentWindow!, "postMessage"));
  for (const frame of frames) await act(async () => {
    window.dispatchEvent(new MessageEvent("message", { source: frame.contentWindow, data: { type: "imv-preview-ready", channel: new URL(frame.src).hash.slice(1), sync: true } }));
  });
  await act(async () => { ref.current!.setTime(3); await new Promise((resolve) => requestAnimationFrame(resolve)); });
  frames.forEach((frame, index) => expect(posts[index]).toHaveBeenLastCalledWith({ type: "imv-preview-sync", channel: new URL(frame.src).hash.slice(1), time: 2 }, "*"));
  const otherSrc = other.src;
  view.rerender(<SpriteOverlay ref={ref} clips={[second]} stageAspect={1} initialTime={2} />);
  expect(other.src).toBe(otherSrc);
  await act(async () => { ref.current!.setTime(2); await new Promise((resolve) => requestAnimationFrame(resolve)); });
  expect(posts[1]).toHaveBeenLastCalledWith({ type: "imv-preview-sync", channel: new URL(other.src).hash.slice(1), time: 1 }, "*");
});
