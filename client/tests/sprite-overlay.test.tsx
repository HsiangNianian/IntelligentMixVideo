/** AI 资产叠加预览核心测试：就绪握手、按预览时间逐帧定位、区间外隐藏、只挂载当前时间附近的播放器、转交字体、旧资产降级；执行 bun run test。 */
import { expect, spyOn, test } from "bun:test";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRef } from "react";
import { SpriteOverlay, type SpriteOverlayHandle } from "@/features/templates/SpriteOverlay";
import { fetchMock } from "./setup";

// 摆放的完整时长 5 秒长于预览里显示的 1～4 秒：同步消息带完整时长，Sprite 按它排布。
const clip = { id: "sprite-a", spriteId: "s1", aspect: 9 / 16, name: "霓虹标题", start: 1, end: 4, length: 5 };

/** 渲染叠加层并取出其播放器，返回通道号与消息探针；Happy DOM 不执行 iframe，按协议手动模拟就绪。 */
function mount() {
  const ref = createRef<SpriteOverlayHandle>();
  render(<SpriteOverlay ref={ref} clips={[clip]} stageAspect={16 / 9} initialTime={0} />);
  const frame = screen.getByTitle<HTMLIFrameElement>("AI 资产预览：霓虹标题");
  const post = spyOn(frame.contentWindow!, "postMessage");
  const channel = new URL(frame.src).hash.slice(1);
  /** 播放器页面发出就绪通知；sync 表示该资产的包是否支持逐帧驱动。 */
  const ready = (sync: boolean | undefined) => act(async () => {
    window.dispatchEvent(new MessageEvent("message", { source: frame.contentWindow, data: { type: "imv-preview-ready", channel, sync } }));
  });
  const setTime = (value: number) => act(async () => { ref.current!.setTime(value); await new Promise((resolve) => requestAnimationFrame(() => resolve(undefined))); });
  return { frame, post, channel, ready, setTime };
}

// 场景：播放器指向资产的透明同步预览页（字体由本页转交），按 Cover 方式铺满：竖屏资产在横屏画面里取全宽、上下裁切。
test("叠加播放器使用透明同步页并按画面比例铺满", () => {
  const { frame, channel } = mount();
  expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
  expect(frame.src).toBe(`http://api.test:8000/api/sprites/s1/preview?overlay=true&sync=1&fonts=host#${channel}`);
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
  await waitFor(() => expect(post).toHaveBeenCalledWith({ type: "imv-preview-sync", channel, time: 1.5, duration: 5 }, "*"));
  expect(frame.style.visibility).toBe("visible");
  await setTime(3);
  expect(post).toHaveBeenLastCalledWith({ type: "imv-preview-sync", channel, time: 2, duration: 5 }, "*");
  const calls = post.mock.calls.length;
  await setTime(0.5);
  await setTime(4);
  expect(frame.style.visibility).toBe("hidden");
  expect(post.mock.calls.length).toBe(calls);
});

// 回归 #210：每个播放器都是独立页面，常驻会占用内存与 CPU。只为片段开始前 3 秒到结束后 1 秒内的片段挂载播放器；
// 离开后卸载，回来时是新页面，重新就绪后才发送定位。
test("只为当前时间附近的片段挂载播放器，离开后卸载并重新握手", async () => {
  const ref = createRef<SpriteOverlayHandle>();
  const later = { ...clip, id: "sprite-late", spriteId: "s2", name: "转场", start: 20, end: 21, length: 1 };
  render(<SpriteOverlay ref={ref} clips={[clip, later]} stageAspect={16 / 9} initialTime={0} />);
  /** 按资产名称取当前挂载的播放器，未挂载时为 null。 */
  const find = (name: string) => screen.queryByTitle<HTMLIFrameElement>(`AI 资产预览：${name}`);
  /** 推送预览时间并等待一次帧回调。 */
  const setTime = (value: number) => act(async () => { ref.current!.setTime(value); await new Promise((resolve) => requestAnimationFrame(() => resolve(undefined))); });
  /** 播放器页面按协议回报就绪，并支持逐帧驱动。 */
  const ready = (frame: HTMLIFrameElement) => act(async () => {
    window.dispatchEvent(new MessageEvent("message", { source: frame.contentWindow, data: { type: "imv-preview-ready", channel: new URL(frame.src).hash.slice(1), sync: true } }));
  });
  expect(find("霓虹标题")).not.toBeNull();
  expect(find("转场")).toBeNull();
  await ready(find("霓虹标题")!);
  await setTime(16.9);
  expect(find("转场")).toBeNull();
  await setTime(17.5);
  expect(find("转场")).not.toBeNull();
  expect(find("霓虹标题")).toBeNull();
  await setTime(2);
  const again = find("霓虹标题")!;
  const post = spyOn(again.contentWindow!, "postMessage");
  await setTime(2.5);
  expect(post).not.toHaveBeenCalled();
  await ready(again);
  await waitFor(() => expect(post).toHaveBeenCalledWith({ type: "imv-preview-sync", channel: new URL(again.src).hash.slice(1), time: 1.5, duration: 5 }, "*"));
});

// 回归 #210：沙箱预览页是不透明来源，浏览器不为它缓存请求；播放器请求字体时由本页读取一次两个字重并转交，
// 多个播放器不重复读取，陌生通道的请求不回应。
test("播放器请求字体时转交本页读取一次的字体", async () => {
  fetchMock.mockImplementation((async (input: RequestInfo | URL) => new Response(new Blob([String(input)], { type: "font/collection" }))) as typeof fetch);
  const second = { ...clip, id: "sprite-b", spriteId: "s2", name: "第二个资产" };
  render(<SpriteOverlay ref={createRef<SpriteOverlayHandle>()} clips={[clip, second]} stageAspect={16 / 9} initialTime={2} />);
  const frames = ["霓虹标题", "第二个资产"].map((name) => screen.getByTitle<HTMLIFrameElement>(`AI 资产预览：${name}`));
  const posts = frames.map((frame) => spyOn(frame.contentWindow!, "postMessage"));
  await act(async () => {
    window.dispatchEvent(new MessageEvent("message", { source: frames[0].contentWindow, data: { type: "imv-preview-fonts-request", channel: "stranger" } }));
    for (const frame of frames) window.dispatchEvent(new MessageEvent("message", { source: frame.contentWindow, data: { type: "imv-preview-fonts-request", channel: new URL(frame.src).hash.slice(1) } }));
  });
  for (const [index, frame] of frames.entries()) {
    await waitFor(() => expect(posts[index]).toHaveBeenCalledTimes(1));
    const [message, origin] = posts[index].mock.calls[0] as [{ type: string; channel: string; fonts: Record<string, Blob> }, string];
    expect([message.type, message.channel, origin]).toEqual(["imv-preview-fonts", new URL(frame.src).hash.slice(1), "*"]);
    expect([await message.fonts[400].text(), await message.fonts[700].text()]).toEqual(["http://api.test:8000/api/sprites/fonts/400", "http://api.test:8000/api/sprites/fonts/700"]);
  }
  expect(fetchMock.mock.calls.length).toBeLessThanOrEqual(2);
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
  const first = screen.getByTitle<HTMLIFrameElement>("AI 资产预览：霓虹标题");
  const originalSrc = first.src;
  view.rerender(<SpriteOverlay ref={ref} clips={[clip, second]} stageAspect={1} initialTime={2} />);
  expect(first.src).toBe(originalSrc);
  const other = screen.getByTitle<HTMLIFrameElement>("AI 资产预览：第二个资产");
  const frames = [first, other];
  const posts = frames.map((frame) => spyOn(frame.contentWindow!, "postMessage"));
  for (const frame of frames) await act(async () => {
    window.dispatchEvent(new MessageEvent("message", { source: frame.contentWindow, data: { type: "imv-preview-ready", channel: new URL(frame.src).hash.slice(1), sync: true } }));
  });
  await act(async () => { ref.current!.setTime(3); await new Promise((resolve) => requestAnimationFrame(resolve)); });
  frames.forEach((frame, index) => expect(posts[index]).toHaveBeenLastCalledWith({ type: "imv-preview-sync", channel: new URL(frame.src).hash.slice(1), time: 2, duration: 5 }, "*"));
  const otherSrc = other.src;
  view.rerender(<SpriteOverlay ref={ref} clips={[second]} stageAspect={1} initialTime={2} />);
  expect(other.src).toBe(otherSrc);
  await act(async () => { ref.current!.setTime(2); await new Promise((resolve) => requestAnimationFrame(resolve)); });
  expect(posts[1]).toHaveBeenLastCalledWith({ type: "imv-preview-sync", channel: new URL(other.src).hash.slice(1), time: 1, duration: 5 }, "*");
});

/** 带参数覆盖、宿主变换与画布信息的可移动片段。 */
const placed = {
  ...clip, values: { text: "新品" }, transform: { x: 300 },
  canvas: { width: 1080, height: 1920, box: { x: 90, y: 760, width: 900, height: 400 }, movable: true },
};

// 场景：同一资产的英文摆放可从画面单独选中，区间外不留下可选热区。
test("画面选择按文字摆放 ID 区分中英文", () => {
  const selected: string[] = [];
  const ref = createRef<SpriteOverlayHandle>();
  const english = { ...placed, id: "english", textTrackId: "title-en", name: "英文标题", transform: { y: 1200 } };
  const view = render(<SpriteOverlay ref={ref} clips={[placed, english]} stageAspect={9 / 16} initialTime={2} onSelect={(id) => selected.push(id)} />);
  fireEvent.pointerDown(screen.getByRole("button", { name: "选择文字：英文标题" }));
  expect(selected).toEqual(["english"]);
  view.rerender(<SpriteOverlay ref={ref} clips={[placed, english]} stageAspect={9 / 16} initialTime={5} onSelect={(id) => selected.push(id)} />);
  expect(screen.queryByRole("button", { name: "选择文字：英文标题" })).toBeNull();
});

// 场景：回报 host 能力的播放器在就绪后收到这次摆放的参数与宿主变换；内容变化时重发，未变化时不重复发送。
test("支持参数覆盖的播放器收到参数与宿主变换", async () => {
  const ref = createRef<SpriteOverlayHandle>();
  const view = render(<SpriteOverlay ref={ref} clips={[placed]} stageAspect={16 / 9} initialTime={0} />);
  const frame = screen.getByTitle<HTMLIFrameElement>("AI 资产预览：霓虹标题");
  const post = spyOn(frame.contentWindow!, "postMessage");
  const channel = new URL(frame.src).hash.slice(1);
  await act(async () => {
    window.dispatchEvent(new MessageEvent("message", { source: frame.contentWindow, data: { type: "imv-preview-ready", channel, sync: true, host: true } }));
  });
  const updates = () => post.mock.calls.map(([message]) => message as Record<string, unknown>).filter((message) => message.type === "imv-preview-update");
  expect(updates()).toEqual([{ type: "imv-preview-update", channel, requestId: 1, values: { text: "新品" }, background: "", host: { transform: { x: 300 } } }]);
  view.rerender(<SpriteOverlay ref={ref} clips={[{ ...placed }]} stageAspect={16 / 9} initialTime={0} />);
  expect(updates()).toHaveLength(1);
  view.rerender(<SpriteOverlay ref={ref} clips={[{ ...placed, transform: { x: 400, scale: 2 } }]} stageAspect={16 / 9} initialTime={0} />);
  expect(updates().at(-1)).toMatchObject({ requestId: 2, host: { transform: { x: 400, scale: 2 } } });
});

// 场景：能逐帧同步但不支持参数覆盖的旧包仍按时间显示，只提示参数调整不显示在预览里，不发送参数。
test("不支持参数覆盖的资产只同步时间并提示", async () => {
  const { post, ready, setTime } = mount();
  await ready(true);
  await setTime(2);
  expect(post.mock.calls.some(([message]) => (message as { type: string }).type === "imv-preview-update")).toBe(false);
  expect(screen.getByRole("status").textContent).toContain("预览不显示参数调整");
});

// 场景：选中的可移动资产在画面上显示位置框；方向键按步长移动并限制在画布内，未选中或不可移动的资产没有位置框。
test("选中的资产可以用位置框调整位置", () => {
  const moves: [string, number, number][] = [];
  const ref = createRef<SpriteOverlayHandle>();
  const view = render(<SpriteOverlay ref={ref} clips={[placed]} stageAspect={16 / 9} initialTime={0} selectedId="sprite-a" onMove={(id, x, y) => moves.push([id, x, y])} />);
  const handle = screen.getByRole("slider", { name: "拖动调整位置：霓虹标题" });
  expect(handle.style.left).toBe(`${((300 - 450) / 1080) * 100}%`);
  fireEvent.keyDown(handle, { key: "ArrowRight" });
  fireEvent.keyDown(handle, { key: "ArrowUp", shiftKey: true });
  fireEvent.keyDown(handle, { key: "ArrowLeft", shiftKey: true });
  expect(moves).toEqual([["sprite-a", 310, 960], ["sprite-a", 300, 910], ["sprite-a", 250, 960]]);
  view.rerender(<SpriteOverlay ref={ref} clips={[{ ...placed, canvas: { ...placed.canvas, movable: false } }]} stageAspect={16 / 9} initialTime={0} selectedId="sprite-a" onMove={() => {}} />);
  expect(screen.queryByRole("slider")).toBeNull();
});
