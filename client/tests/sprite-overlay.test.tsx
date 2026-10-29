/** AI 资产叠加预览核心测试：就绪握手、按预览时间逐帧定位、区间外隐藏、只挂载当前时间附近的播放器、转交字体、旧资产降级、迟迟不就绪写调试日志；执行 bun run test。 */
import { expect, jest, spyOn, test } from "bun:test";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRef } from "react";
import { SpriteOverlay, type SpriteOverlayHandle } from "@/features/templates/SpriteOverlay";
import { openDiagnostics, resetDiagnostics } from "@/lib/diagnostics";
import { fetchMock, mockDesktop } from "./setup";

// 摆放的完整时长 5 秒长于预览里显示的 1～4 秒：同步消息带完整时长，Sprite 按它排布。
const clip = { id: "sprite-a", spriteId: "s1", aspect: 9 / 16, name: "霓虹标题", start: 1, end: 4, length: 5 };

// 开场前后只切换字幕位置，保留原字效时钟；回拖时恢复开场内的位置。
test("圆形开场内使用普通字幕摆放，越过边界只改位置并保持完整字效时钟", async () => {
  const ref = createRef<SpriteOverlayHandle>();
  const normal = { x: 540, y: 1600 }, sameScreen = { x: 540, y: 1200 };
  render(<SpriteOverlay ref={ref} clips={[{ ...clip, transform: sameScreen, opening: { end: 2, transform: normal } }]} stageAspect={9 / 16} initialTime={1} />);
  const frame = screen.getByTitle<HTMLIFrameElement>("AI 资产预览：霓虹标题");
  const post = spyOn(frame.contentWindow!, "postMessage");
  const channel = new URL(frame.src).hash.slice(1);
  await act(async () => { window.dispatchEvent(new MessageEvent("message", { source: frame.contentWindow, data: { type: "imv-preview-ready", channel, sync: true, host: true } })); });
  await waitFor(() => expect(post.mock.calls.some(([message]) => message.type === "imv-preview-update" && message.host.transform.y === 1600)).toBe(true));
  await act(async () => { ref.current!.setTime(2); });
  await waitFor(() => expect(post.mock.calls.some(([message]) => message.type === "imv-preview-update" && message.host.transform.y === 1200)).toBe(true));
  expect(post.mock.calls.some(([message]) => message.type === "imv-preview-sync" && message.time === 1 && message.duration === 5)).toBe(true);
  await act(async () => { ref.current!.setTime(1.5); });
  await waitFor(() => expect(post.mock.calls.filter(([message]) => message.type === "imv-preview-update").at(-1)![0].host.transform).toEqual(normal));
});

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

// 审计 #213：每次挂载都是一次页面加载。来回拖动游标跨过范围边缘时，已挂载的播放器按放宽的范围保留（前后各多 2 秒），
// 不反复创建、销毁；放宽的范围不用于新挂载。
test("已挂载的播放器按放宽的范围保留，来回拖动不反复重载", async () => {
  const ref = createRef<SpriteOverlayHandle>();
  const late = { ...clip, id: "sprite-late", spriteId: "s2", name: "转场", start: 20, end: 21, length: 1 };
  render(<SpriteOverlay ref={ref} clips={[late]} stageAspect={16 / 9} initialTime={0} />);
  /** 按资产名称取当前挂载的播放器，未挂载时为 null。 */
  const find = () => screen.queryByTitle<HTMLIFrameElement>("AI 资产预览：转场");
  /** 推送预览时间并等待一次帧回调。 */
  const setTime = (value: number) => act(async () => { ref.current!.setTime(value); await new Promise((resolve) => requestAnimationFrame(() => resolve(undefined))); });
  await setTime(16);
  expect(find()).toBeNull();
  await setTime(17.5);
  const frame = find();
  expect(frame).not.toBeNull();
  // 前沿：回到挂载范围（17 秒）之前 2 秒内仍是同一个页面，再往前才卸载。
  await setTime(15.5);
  expect(find()).toBe(frame);
  await setTime(14.9);
  expect(find()).toBeNull();
  // 放宽的范围不用于新挂载：卸载后要回到 17 秒才重新挂载。
  await setTime(16.5);
  expect(find()).toBeNull();
  await setTime(17.5);
  const again = find();
  expect(again).not.toBeNull();
  // 后沿：片段结束后 1 秒再加 2 秒内仍保留，之后卸载，不会因游标回到 22 秒而重新挂载。
  await setTime(23.9);
  expect(find()).toBe(again);
  await setTime(24);
  expect(find()).toBeNull();
  await setTime(22);
  expect(find()).toBeNull();
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

// 场景：叠加播放器是跨源沙箱页面，内部报错无法监听，预览包缺失或脚本出错时只会一直隐藏；
// 挂载 15 秒还没就绪时写调试日志（资产、页面是否已加载与地址），及时就绪的不记录，旧预览包记录能力不足。
test("播放器迟迟不就绪时写调试日志", async () => {
  const entries: { level: string; message: string }[] = [];
  mockDesktop(async (command, args) => {
    if (command === "diagnostics_open") return { path: "/data/diagnostics.log", viewerError: null };
    if (command === "diagnostics_log") entries.push(args as { level: string; message: string });
    return null;
  });
  await openDiagnostics();
  try {
    jest.useFakeTimers();
    const second = { ...clip, id: "sprite-b", spriteId: "s2", name: "第二个资产" };
    render(<SpriteOverlay ref={createRef<SpriteOverlayHandle>()} clips={[clip, second]} stageAspect={16 / 9} initialTime={2} />);
    const [stuck, old] = ["霓虹标题", "第二个资产"].map((name) => screen.getByTitle<HTMLIFrameElement>(`AI 资产预览：${name}`));
    fireEvent.load(stuck);
    await act(async () => {
      window.dispatchEvent(new MessageEvent("message", { source: old.contentWindow, data: { type: "imv-preview-ready", channel: new URL(old.src).hash.slice(1) } }));
    });
    act(() => { jest.advanceTimersByTime(15_000); });
    jest.useRealTimers();
    await waitFor(() => expect(entries.some((entry) => entry.level === "警告" && entry.message === `「霓虹标题」（资产 s1）的播放器 15 秒内没有就绪：页面已加载但没有发出就绪通知，可能是预览脚本出错；地址 ${stuck.src}`)).toBe(true));
    expect(entries.some((entry) => entry.message === "「第二个资产」（资产 s2）的预览包较旧，不支持逐帧驱动，保持隐藏")).toBe(true);
    expect(entries.some((entry) => entry.message.startsWith("「第二个资产」（资产 s2）的播放器"))).toBe(false);
  } finally {
    jest.useRealTimers();
    resetDiagnostics();
  }
});

// 场景：同一播放器更换资产时 React 复用 iframe 但地址变化；旧页面的握手不能沿用，
// 新页面迟迟不就绪时应按新资产重新记录超时。
test("同一播放器更换资产后重新等待就绪", async () => {
  const entries: { level: string; message: string }[] = [];
  mockDesktop(async (command, args) => {
    if (command === "diagnostics_open") return { path: "/data/diagnostics.log", viewerError: null };
    if (command === "diagnostics_log") entries.push(args as { level: string; message: string });
    return null;
  });
  await openDiagnostics();
  try {
    jest.useFakeTimers();
    const view = render(<SpriteOverlay ref={createRef<SpriteOverlayHandle>()} clips={[clip]} stageAspect={16 / 9} initialTime={2} />);
    const first = screen.getByTitle<HTMLIFrameElement>("AI 资产预览：霓虹标题");
    const oldSrc = first.src;
    fireEvent.load(first);
    await act(async () => {
      window.dispatchEvent(new MessageEvent("message", { source: first.contentWindow, data: { type: "imv-preview-ready", channel: new URL(oldSrc).hash.slice(1), sync: true, host: true } }));
    });
    view.rerender(<SpriteOverlay ref={createRef<SpriteOverlayHandle>()} clips={[{ ...clip, spriteId: "s2" }]} stageAspect={16 / 9} initialTime={2} />);
    const updated = screen.getByTitle<HTMLIFrameElement>("AI 资产预览：霓虹标题");
    expect(updated).toBe(first);
    expect(updated.src).not.toBe(oldSrc);
    fireEvent.load(updated);
    act(() => { jest.advanceTimersByTime(15_000); });
    jest.useRealTimers();
    await waitFor(() => expect(entries.some((entry) => entry.level === "警告" && entry.message === `「霓虹标题」（资产 s2）的播放器 15 秒内没有就绪：页面已加载但没有发出就绪通知，可能是预览脚本出错；地址 ${updated.src}`)).toBe(true));
  } finally {
    jest.useRealTimers();
    resetDiagnostics();
  }
});

// 回归：覆盖同一资产必须重新加载；新页面 ready 可能早于 load，旧通道不能唤醒新页面，参数与当前时间须重发。
test("同 ID 资产覆盖后立即清除握手并向新版重发摆放", async () => {
  const ref = createRef<SpriteOverlayHandle>();
  const version1 = { ...clip, sourceVersionId: "v1", values: { fontSize: 200 }, transform: { x: 300 } };
  const view = render(<SpriteOverlay ref={ref} clips={[version1]} stageAspect={1} initialTime={2} />);
  const frame = screen.getByTitle<HTMLIFrameElement>("AI 资产预览：霓虹标题");
  const oldSrc = frame.src;
  const oldChannel = new URL(oldSrc).hash.slice(1);
  /** 模拟页面经指定通道就绪；保留旧通道用于验证迟到消息。 */
  const ready = (channel: string) => act(async () => {
    window.dispatchEvent(new MessageEvent("message", { source: frame.contentWindow, data: { type: "imv-preview-ready", channel, sync: true, host: true } }));
  });
  await ready(oldChannel);
  await waitFor(() => expect(frame.style.visibility).toBe("visible"));
  fireEvent.load(frame);
  view.rerender(<SpriteOverlay ref={ref} clips={[{ ...version1, name: "改名" }]} stageAspect={1} initialTime={2} />);
  expect(frame.src).toBe(oldSrc);
  view.rerender(<SpriteOverlay ref={ref} clips={[{ ...version1, sourceVersionId: "v2" }]} stageAspect={1} initialTime={2} />);
  expect(new URL(frame.src).searchParams.get("version")).toBe("v2");
  const channel = new URL(frame.src).hash.slice(1);
  expect(channel).not.toBe(oldChannel);
  expect(frame.style.visibility).toBe("hidden");
  const freshPost = spyOn(frame.contentWindow!, "postMessage");
  freshPost.mockClear();
  await ready(oldChannel);
  expect(freshPost).not.toHaveBeenCalled();
  expect(frame.style.visibility).toBe("hidden");
  await ready(channel);
  await waitFor(() => expect(frame.style.visibility).toBe("visible"));
  expect(freshPost.mock.calls.some(([message]) => message.type === "imv-preview-update" && message.values.fontSize === 200 && message.host.transform.x === 300)).toBe(true);
  expect(freshPost).toHaveBeenCalledWith({ type: "imv-preview-sync", channel, time: 1, duration: 5 }, "*");
  fireEvent.load(frame);
  await act(async () => { ref.current!.setTime(3); });
  await waitFor(() => expect(freshPost).toHaveBeenLastCalledWith({ type: "imv-preview-sync", channel, time: 2, duration: 5 }, "*"));
});

// 场景：AI 资产在预览上显示不对。预览页自己发现的问题（如字体加载失败）写进日志；调试模式下参数或位置更新
// 超过 0.5 秒才画到画面上记一条警告，及时画完的不记录。
test("记录预览页报错与偏慢的参数更新", async () => {
  const entries: { level: string; message: string }[] = [];
  mockDesktop(async (command, args) => {
    if (command === "diagnostics_open") return { path: "/data/diagnostics.log", viewerError: null };
    if (command === "diagnostics_log") entries.push(args as { level: string; message: string });
    return null;
  });
  let clock = 0;
  spyOn(performance, "now").mockImplementation(() => clock);
  await openDiagnostics();
  try {
    const { frame, post, channel } = mount();
    const send = (data: Record<string, unknown>) => act(async () => {
      window.dispatchEvent(new MessageEvent("message", { source: frame.contentWindow, data: { channel, ...data } }));
    });
    await send({ type: "imv-preview-ready", sync: true, host: true });
    const update = post.mock.calls.map(([message]) => message as { type: string; requestId: number }).find((message) => message.type === "imv-preview-update")!;
    clock = 800;
    await send({ type: "imv-preview-rendered", requestId: update.requestId });
    await send({ type: "imv-preview-rendered", requestId: update.requestId });
    await send({ type: "imv-preview-error", message: "预览字体加载失败，\n请重试。" });
    await waitFor(() => expect(entries.some((entry) => entry.message.includes("预览页报错"))).toBe(true));
    expect(entries.filter((entry) => entry.message.startsWith("「霓虹标题」")).map((entry) => [entry.level, entry.message])).toEqual([
      ["警告", "「霓虹标题」（资产 s1）参数或位置更新到画面用时 800 ms"],
      ["错误", "「霓虹标题」（资产 s1）的预览页报错：预览字体加载失败， 请重试。"],
    ]);
  } finally {
    resetDiagnostics();
  }
});
