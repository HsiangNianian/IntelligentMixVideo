/**
 * 预览视频弹窗的本地文件来源：选择、加载、移除、超限、取消释放，以及桌面端写入/删除本机副本、
 * 多槽位失败回滚（含从预览媒体服务读回原视频）、保存期间不可取消、以媒体服务地址恢复本机视频与播放内置示例。
 * 视频元数据通过替换 HTMLMediaElement 的 src 设置器模拟，不读取真实媒体；执行 bun run test。
 */
import { afterEach, beforeEach, expect, mock, spyOn, test } from "bun:test";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MasterVideoInput } from "@/features/templates/MasterVideoInput";
import { defaultPreviewUrls, previewVideoUrl } from "@/features/templates/media";
import * as localVideos from "@/features/templates/localVideos";
import { commitLocalVideos, LOCAL_VIDEO_MAX_BYTES, localVideoSlots } from "@/features/templates/localVideos";
import type { PreviewMedia } from "@/features/templates/model";
import { fetchMock, mockDesktop } from "./setup";

const source = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "src")!;
/** 测试用的预览媒体服务地址前缀；地址在模块内缓存，所有桌面用例回答同一个值。 */
const MEDIA = "http://127.0.0.1:9/token";

/** 模拟桌面端：媒体服务地址固定，其余命令交给用例。 */
function desktop(handler: (command: string, args: Record<string, unknown>) => Promise<unknown>) {
  mockDesktop(async (command, args) => command === "preview_media_base" ? MEDIA : handler(command, args));
}
// 本例生成的 blob URL（按创建顺序）与被设置到视频元素上的地址，用于断言来源确实对应所选文件。
let created: string[] = [];
let assigned: string[] = [];

beforeEach(() => {
  created = [];
  assigned = [];
  const create = URL.createObjectURL.bind(URL);
  spyOn(URL, "createObjectURL").mockImplementation((blob) => {
    const url = create(blob);
    created.push(url);
    return url;
  });
  // 设置地址后异步回报 12 秒、1080×1920 的元数据，模拟浏览器读取成功。
  Object.defineProperty(HTMLMediaElement.prototype, "src", {
    configurable: true,
    get: source.get,
    set(this: HTMLMediaElement, value: string) {
      source.set!.call(this, value);
      assigned.push(value);
      Object.defineProperties(this, {
        duration: { configurable: true, value: 12 },
        videoWidth: { configurable: true, value: 1080 },
        videoHeight: { configurable: true, value: 1920 },
      });
      queueMicrotask(() => this.dispatchEvent(new Event("loadedmetadata")));
    },
  });
});

afterEach(async () => {
  Object.defineProperty(HTMLMediaElement.prototype, "src", source);
  // 本地视频登记是模块级状态，每例结束以浏览器模式改回 URL，避免影响其他用例。
  Reflect.deleteProperty(globalThis, "isTauri");
  await commitLocalVideos([0, 1, 2].map(() => ({ kind: "url", url: "" })));
});

/** 等待本机视频恢复结束后打开弹窗并返回对话框元素。 */
async function openDialog() {
  const trigger = screen.getByRole("button", { name: "加载预览视频" });
  await waitFor(() => expect(trigger.hasAttribute("disabled")).toBe(false));
  fireEvent.click(trigger);
  return screen.getByRole("dialog", { name: "加载预览视频" });
}

/** 通过隐藏文件输入为指定视频选择文件。 */
function choose(dialog: HTMLElement, index: number, file: File) {
  fireEvent.change(within(dialog).getByLabelText(`视频 ${index} 本地视频文件`), { target: { files: [file] } });
}

// 场景：视频 2 选择本地文件并加载，其余仍用默认地址；再次打开显示为文件，移除后改回默认 URL。
test("本地文件加载为预览来源，移除后恢复默认地址", async () => {
  const changes: PreviewMedia[] = [];
  const view = render(<MasterVideoInput onChange={(media) => changes.push(media)} />);
  let dialog = await openDialog();
  choose(dialog, 2, new File(["video"], "我的素材.mp4", { type: "video/mp4" }));
  expect(within(dialog).getByLabelText("视频 2 本地文件").textContent).toBe("我的素材.mp4");
  fireEvent.click(within(dialog).getByRole("button", { name: "加载" }));
  await waitFor(() => expect(changes).toHaveLength(1));
  const local = changes[0][1].url;
  expect(created).toHaveLength(1);
  expect(local).toBe(created[0]);
  expect(assigned).toContain(created[0]);
  expect(changes[0].map((video) => video.url)).toEqual([previewVideoUrl(0), local, previewVideoUrl(2)]);
  expect(await defaultPreviewUrls()).toEqual([previewVideoUrl(0), local, previewVideoUrl(2)]);

  view.rerender(<MasterVideoInput media={changes[0]} onChange={(media) => changes.push(media)} />);
  dialog = await openDialog();
  expect(within(dialog).getByLabelText("视频 2 本地文件").textContent).toBe("我的素材.mp4");
  fireEvent.click(within(dialog).getByRole("button", { name: "移除视频 2 的本地文件" }));
  expect(within(dialog).getByRole<HTMLInputElement>("textbox", { name: "视频 2 URL" }).value).toBe(previewVideoUrl(1));
  fireEvent.click(within(dialog).getByRole("button", { name: "加载" }));
  await waitFor(() => expect(changes).toHaveLength(2));
  expect(changes[1][1].url).toBe(previewVideoUrl(1));
  expect(await defaultPreviewUrls()).toEqual([0, 1, 2].map((index) => previewVideoUrl(index as 0 | 1 | 2)));
});

// 回归 #210：未配置的视频 3 使用内置示例，以 blob URL 播放；再次打开弹窗仍显示示例的资源地址，而不是 blob URL。
test("内置示例在弹窗中显示资源地址", async () => {
  const configured = process.env.VITE_PREVIEW_VIDEO_3_URL;
  delete process.env.VITE_PREVIEW_VIDEO_3_URL;
  fetchMock.mockImplementation((async (_input: RequestInfo | URL) => new Response(new Blob(["video"], { type: "video/mp4" }))) as typeof fetch);
  try {
    const bundled = previewVideoUrl(2);
    expect(bundled).toBe(`${window.location.origin}/preview/sample-3.mp4`);
    const changes: PreviewMedia[] = [];
    const view = render(<MasterVideoInput onChange={(media) => changes.push(media)} />);
    let dialog = await openDialog();
    expect(within(dialog).getByRole<HTMLInputElement>("textbox", { name: "视频 3 URL" }).value).toBe(bundled);
    fireEvent.click(within(dialog).getByRole("button", { name: "加载" }));
    await waitFor(() => expect(changes).toHaveLength(1));
    const played = changes[0][2].url;
    expect(played.startsWith("blob:")).toBe(true);
    expect(assigned).toContain(played);

    view.rerender(<MasterVideoInput media={changes[0]} onChange={(media) => changes.push(media)} />);
    dialog = await openDialog();
    expect(within(dialog).getByRole<HTMLInputElement>("textbox", { name: "视频 3 URL" }).value).toBe(bundled);
  } finally {
    process.env.VITE_PREVIEW_VIDEO_3_URL = configured;
  }
});

// 场景：超过 512 MiB 的文件直接提示，不替换该视频的来源。
test("超过上限的本地文件提示且不替换来源", async () => {
  render(<MasterVideoInput onChange={() => {}} />);
  const dialog = await openDialog();
  const file = new File(["video"], "large.mp4", { type: "video/mp4" });
  Object.defineProperty(file, "size", { value: LOCAL_VIDEO_MAX_BYTES + 1 });
  choose(dialog, 1, file);
  expect(within(dialog).getByRole("alert").textContent).toBe("视频 1：本地视频不能超过 512 MiB");
  expect(within(dialog).getByRole<HTMLInputElement>("textbox", { name: "视频 1 URL" }).value).toBe(previewVideoUrl(0));
});

// 场景：选择文件后取消，不更新来源并释放本次生成的 blob URL。
test("取消弹窗不提交本地文件并释放 blob URL", async () => {
  const revoke = spyOn(URL, "revokeObjectURL");
  const onChange = mock();
  render(<MasterVideoInput onChange={onChange} />);
  const dialog = await openDialog();
  choose(dialog, 3, new File(["video"], "c.mp4", { type: "video/mp4" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
  expect(created).toHaveLength(1);
  expect(revoke).toHaveBeenCalledTimes(1);
  expect(revoke).toHaveBeenCalledWith(created[0]);
  expect(onChange).not.toHaveBeenCalled();
});

// 场景：桌面端加载成功后把文件字节写入本机，改回 URL 时删除对应槽位的本机副本。
test("桌面端保存本地文件并在改回 URL 时删除", async () => {
  const calls: [string, unknown][] = [];
  desktop(async (command, args) => { calls.push([command, args]); return null; });
  const changes: PreviewMedia[] = [];
  const view = render(<MasterVideoInput onChange={(media) => changes.push(media)} />);
  let dialog = await openDialog();
  choose(dialog, 1, new File(["abc"], "a.mp4", { type: "video/mp4" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "加载" }));
  await waitFor(() => expect(changes).toHaveLength(1));
  // 保持 URL 的视频 2、3 也删除各自可能残留的本机副本。
  expect(calls.map(([command]) => command)).toEqual(["preview_video_write", "preview_video_remove", "preview_video_remove"]);
  expect([...(calls[0][1] as Uint8Array)]).toEqual([...new TextEncoder().encode("abc")]);

  view.rerender(<MasterVideoInput media={changes[0]} onChange={(media) => changes.push(media)} />);
  dialog = await openDialog();
  fireEvent.click(within(dialog).getByRole("button", { name: "移除视频 1 的本地文件" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "加载" }));
  await waitFor(() => expect(changes).toHaveLength(2));
  expect(calls.slice(3)).toEqual([0, 1, 2].map((slot) => ["preview_video_remove", { slot: slot + 1 }]));
});

// 场景：桌面端写入失败时提示原因，保留弹窗与已选文件，不更新预览来源。
test("桌面端写入失败保留弹窗与来源", async () => {
  desktop(async (command) => {
    if (command === "preview_video_write") throw "本地预览视频正在被其他操作使用，请重试";
    return null;
  });
  const onChange = mock();
  render(<MasterVideoInput onChange={onChange} />);
  const dialog = await openDialog();
  choose(dialog, 2, new File(["abc"], "b.mp4", { type: "video/mp4" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "加载" }));
  await waitFor(() => expect(within(dialog).getByRole("alert").textContent).toBe("视频 2：本地预览视频正在被其他操作使用，请重试"));
  expect(within(dialog).getByLabelText("视频 2 本地文件").textContent).toBe("b.mp4");
  expect(onChange).not.toHaveBeenCalled();
});

// 场景：视频 1 已写入、视频 2 写入失败时，视频 1 的本机副本与登记回到提交前，预览来源不变。
test("多槽位保存失败时回滚已完成的槽位", async () => {
  const calls: [string, unknown][] = [];
  desktop(async (command, args) => {
    calls.push([command, args]);
    if (command === "preview_video_write" && (args as unknown as Uint8Array).length === 2) throw "磁盘已满";
    return null;
  });
  const onChange = mock();
  render(<MasterVideoInput onChange={onChange} />);
  const dialog = await openDialog();
  choose(dialog, 1, new File(["a"], "a.mp4", { type: "video/mp4" }));
  choose(dialog, 2, new File(["bb"], "b.mp4", { type: "video/mp4" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "加载" }));
  await waitFor(() => expect(within(dialog).getByRole("alert").textContent).toBe("视频 2：磁盘已满"));
  expect(calls.map(([command]) => command).filter((command) => command !== "preview_video_list")).toEqual([
    "preview_video_write", "preview_video_write", "preview_video_remove",
  ]);
  expect(calls.at(-1)![1]).toEqual({ slot: 1 });
  expect(localVideoSlots().every((video) => !video)).toBe(true);
  expect(onChange).not.toHaveBeenCalled();
  // 回滚后新选择的文件仍可重试；取消时释放它们的 blob URL。
  const revoke = spyOn(URL, "revokeObjectURL");
  fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
  expect(revoke.mock.calls.map(([url]) => url).sort()).toEqual([...created].sort());
});

// 场景：保存期间取消按钮不可用，关闭请求被忽略，保存完成后仍更新预览来源；桌面端改用保存后的媒体服务地址播放，
// 读元数据用的 blob URL 随弹窗关闭释放。
test("保存期间不能取消，完成后更新来源", async () => {
  let finish!: () => void;
  const written = new Promise<void>((resolve) => { finish = resolve; });
  desktop(async (command) => {
    if (command !== "preview_video_write") return null;
    await written;
    return "video-3-abc";
  });
  const revoke = spyOn(URL, "revokeObjectURL");
  const changes: PreviewMedia[] = [];
  render(<MasterVideoInput onChange={(media) => changes.push(media)} />);
  const dialog = await openDialog();
  choose(dialog, 3, new File(["abc"], "c.mp4", { type: "video/mp4" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "加载" }));
  await waitFor(() => expect(within(dialog).getByRole<HTMLButtonElement>("button", { name: "取消" }).disabled).toBe(true));
  fireEvent.keyDown(dialog, { key: "Escape" });
  expect(screen.queryByRole("dialog", { name: "加载预览视频" })).not.toBeNull();
  finish();
  await waitFor(() => expect(changes).toHaveLength(1));
  expect(changes[0][2].url).toBe(`${MEDIA}/local/video-3-abc`);
  expect(localVideoSlots()[2]?.url).toBe(`${MEDIA}/local/video-3-abc`);
  expect(revoke).toHaveBeenCalledWith(created[0]);
});

// 场景：桌面端读回本机视频前弹窗来源都是默认 URL，此时提交会删除本机副本，因此恢复结束前不能打开。
test("桌面端恢复本机视频前不能打开弹窗", async () => {
  desktop(async () => null);
  let finish!: () => void;
  spyOn(localVideos, "localPreviewVideos").mockReturnValue(new Promise((resolve) => { finish = () => resolve([]); }));
  render(<MasterVideoInput onChange={() => {}} />);
  const trigger = screen.getByRole("button", { name: "加载预览视频" });
  await Promise.resolve();
  expect(trigger.hasAttribute("disabled")).toBe(true);
  finish();
  await waitFor(() => expect(trigger.hasAttribute("disabled")).toBe(false));
});

// 场景：桌面端提交 URL 来源时先等待恢复结束，并对每个槽位删除本机副本，包括恢复失败而未登记的槽位。
test("桌面端 URL 来源删除所有槽位的本机副本", async () => {
  const calls: string[] = [];
  desktop(async (command, args) => {
    calls.push(command === "preview_video_remove" ? `${command}:${(args as { slot: number }).slot}` : command);
    return command === "preview_video_list" ? [null, null, null] : null;
  });
  await commitLocalVideos([0, 1, 2].map((index) => ({ kind: "url" as const, url: previewVideoUrl(index as 0 | 1 | 2) })));
  expect(calls.slice(-3)).toEqual(["preview_video_remove:1", "preview_video_remove:2", "preview_video_remove:3"]);
});

// 场景：桌面端恢复时按数据文件名登记预览媒体服务地址，视频字节不经 IPC 读回页面；HEAD 请求确认不可读的槽位
// 回退默认地址，不影响其他槽位。
test("桌面端以媒体服务地址恢复本机视频，不可读的槽位回退默认地址", async () => {
  const commands: string[] = [];
  desktop(async (command) => {
    commands.push(command);
    return command === "preview_video_list" ? ["a", "b", "c"].map((name, index) => ({ name: `${name}.mp4`, type: "video/mp4", file: `video-${index + 1}-${name}` })) : null;
  });
  fetchMock.mockImplementation((async (input: RequestInfo | URL) => new Response(null, { status: String(input).endsWith("video-2-b") ? 404 : 200 })) as typeof fetch);
  // 恢复结果缓存在模块内，用带查询串的导入取得全新的模块实例。
  const specifier = "@/features/templates/localVideos?restore";
  const fresh: typeof import("@/features/templates/localVideos") = await import(specifier);
  const videos = await fresh.localPreviewVideos();
  expect(videos.map((video) => video?.url)).toEqual([`${MEDIA}/local/video-1-a`, undefined, `${MEDIA}/local/video-3-c`]);
  expect(fetchMock.mock.calls.every(([, init]) => init?.method === "HEAD")).toBe(true);
  expect(commands).toEqual(["preview_video_list"]);
});

// 场景：桌面端读回的视频不在页面内存，替换前先从媒体服务读回原视频；后续槽位保存失败时用它写回，登记改为写回后的新地址。
test("替换读回的本机视频失败时用媒体服务读回的原视频回滚", async () => {
  const writes: string[] = [];
  desktop(async (command, args) => {
    if (command === "preview_video_list") return [{ name: "old.mp4", type: "video/mp4", file: "video-1-old" }, null, null];
    if (command !== "preview_video_write") return null;
    writes.push(new TextDecoder().decode(args as unknown as Uint8Array));
    if (writes.at(-1) === "bb") throw "磁盘已满";
    return `video-${writes.length}`;
  });
  fetchMock.mockImplementation((async (_input: RequestInfo | URL, init?: RequestInit) => new Response(init?.method === "HEAD" ? null : "old bytes")) as typeof fetch);
  const specifier = "@/features/templates/localVideos?rollback";
  const fresh: typeof import("@/features/templates/localVideos") = await import(specifier);
  await fresh.localPreviewVideos();
  await expect(fresh.commitLocalVideos([
    { kind: "file", name: "a.mp4", url: "blob:a", file: new File(["a"], "a.mp4", { type: "video/mp4" }) },
    { kind: "file", name: "b.mp4", url: "blob:b", file: new File(["bb"], "b.mp4", { type: "video/mp4" }) },
    { kind: "url", url: "" },
  ])).rejects.toThrow("视频 2：磁盘已满");
  expect(fetchMock.mock.calls.map(([input, init]) => `${init?.method ?? "GET"} ${String(input)}`)).toEqual([
    `HEAD ${MEDIA}/local/video-1-old`, `GET ${MEDIA}/local/video-1-old`,
  ]);
  expect(writes).toEqual(["a", "bb", "old bytes"]);
  expect(fresh.localVideoSlots()[0]).toMatchObject({ name: "old.mp4", url: `${MEDIA}/local/video-3` });
});

// 回归：桌面端内置示例整段读成 blob URL 播放，系统 WebKitGTK 里 IMS SDK 对 blob 视频一直等不到 canplaythrough，画面定格。
// 桌面端改用预览媒体服务的 HTTP 地址，不再下载示例字节；弹窗仍显示资源地址，示例只取前十秒，本机视频使用完整时长。
test("桌面端内置示例经预览媒体服务播放", async () => {
  desktop(async (command) => command === "preview_video_list" ? [null, null, null] : null);
  const specifier = "@/features/templates/media?desktop-samples";
  const fresh: typeof import("@/features/templates/media") = await import(specifier);
  const bundled = [1, 2, 3].map((number) => new URL(`/preview/sample-${number}.mp4`, window.location.href).href);
  const media = await fresh.readPreviewVideos(bundled, new AbortController().signal);
  expect(media.map((video) => video.url)).toEqual([1, 2, 3].map((number) => `${MEDIA}/sample/${number}`));
  expect(assigned).toEqual(media.map((video) => video.url));
  expect(fetchMock).not.toHaveBeenCalled();
  expect(fresh.sourceUrl(media[0].url)).toBe(bundled[0]);
  const local = { ...media[1], url: `${MEDIA}/local/video-2-a` };
  expect(fresh.previewVideos([media[0], local, media[2]]).map((video) => video.duration)).toEqual([10, 12, 10]);
});
