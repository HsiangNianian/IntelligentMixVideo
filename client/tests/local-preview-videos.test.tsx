/**
 * 预览视频弹窗的本地文件来源：选择、加载、移除、超限、取消释放，以及桌面端写入/删除本机副本。
 * 视频元数据通过替换 HTMLMediaElement 的 src 设置器模拟，不读取真实媒体；执行 bun run test。
 */
import { afterEach, beforeEach, expect, mock, spyOn, test } from "bun:test";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MasterVideoInput } from "@/features/templates/MasterVideoInput";
import { defaultPreviewUrls, previewVideoUrl } from "@/features/templates/media";
import { commitLocalVideos, LOCAL_VIDEO_MAX_BYTES } from "@/features/templates/localVideos";
import type { PreviewMedia } from "@/features/templates/model";
import { mockDesktop } from "./setup";

const source = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "src")!;

beforeEach(() => {
  // 设置地址后异步回报 12 秒、1080×1920 的元数据，模拟浏览器读取成功。
  Object.defineProperty(HTMLMediaElement.prototype, "src", {
    configurable: true,
    get: source.get,
    set(this: HTMLMediaElement, value: string) {
      source.set!.call(this, value);
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
  // 本地视频登记是模块级状态，每例结束改回 URL，避免影响其他用例。
  await commitLocalVideos([0, 1, 2].map(() => ({ kind: "url", url: "" })));
});

/** 打开弹窗并返回对话框元素。 */
function openDialog() {
  fireEvent.click(screen.getByRole("button", { name: "加载预览视频" }));
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
  let dialog = openDialog();
  choose(dialog, 2, new File(["video"], "我的素材.mp4", { type: "video/mp4" }));
  expect(within(dialog).getByLabelText("视频 2 本地文件").textContent).toBe("我的素材.mp4");
  fireEvent.click(within(dialog).getByRole("button", { name: "加载" }));
  await waitFor(() => expect(changes).toHaveLength(1));
  const local = changes[0][1].url;
  expect(local.startsWith("blob:")).toBe(true);
  expect(changes[0].map((video) => video.url)).toEqual([previewVideoUrl(0), local, previewVideoUrl(2)]);
  expect(await defaultPreviewUrls()).toEqual([previewVideoUrl(0), local, previewVideoUrl(2)]);

  view.rerender(<MasterVideoInput media={changes[0]} onChange={(media) => changes.push(media)} />);
  dialog = openDialog();
  expect(within(dialog).getByLabelText("视频 2 本地文件").textContent).toBe("我的素材.mp4");
  fireEvent.click(within(dialog).getByRole("button", { name: "移除视频 2 的本地文件" }));
  expect(within(dialog).getByRole<HTMLInputElement>("textbox", { name: "视频 2 URL" }).value).toBe(previewVideoUrl(1));
  fireEvent.click(within(dialog).getByRole("button", { name: "加载" }));
  await waitFor(() => expect(changes).toHaveLength(2));
  expect(changes[1][1].url).toBe(previewVideoUrl(1));
  expect(await defaultPreviewUrls()).toEqual([0, 1, 2].map((index) => previewVideoUrl(index as 0 | 1 | 2)));
});

// 场景：超过 512 MiB 的文件直接提示，不替换该视频的来源。
test("超过上限的本地文件提示且不替换来源", () => {
  render(<MasterVideoInput onChange={() => {}} />);
  const dialog = openDialog();
  const file = new File(["video"], "large.mp4", { type: "video/mp4" });
  Object.defineProperty(file, "size", { value: LOCAL_VIDEO_MAX_BYTES + 1 });
  choose(dialog, 1, file);
  expect(within(dialog).getByRole("alert").textContent).toBe("视频 1：本地视频不能超过 512 MiB");
  expect(within(dialog).getByRole<HTMLInputElement>("textbox", { name: "视频 1 URL" }).value).toBe(previewVideoUrl(0));
});

// 场景：选择文件后取消，不更新来源并释放本次生成的 blob URL。
test("取消弹窗不提交本地文件并释放 blob URL", () => {
  const revoke = spyOn(URL, "revokeObjectURL");
  const onChange = mock();
  render(<MasterVideoInput onChange={onChange} />);
  const dialog = openDialog();
  choose(dialog, 3, new File(["video"], "c.mp4", { type: "video/mp4" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
  expect(revoke).toHaveBeenCalledTimes(1);
  expect(String(revoke.mock.calls[0][0]).startsWith("blob:")).toBe(true);
  expect(onChange).not.toHaveBeenCalled();
});

// 场景：桌面端加载成功后把文件字节写入本机，改回 URL 时删除对应槽位的本机副本。
test("桌面端保存本地文件并在改回 URL 时删除", async () => {
  const calls: [string, unknown][] = [];
  mockDesktop(async (command, args) => { calls.push([command, args]); return null; });
  const changes: PreviewMedia[] = [];
  const view = render(<MasterVideoInput onChange={(media) => changes.push(media)} />);
  let dialog = openDialog();
  choose(dialog, 1, new File(["abc"], "a.mp4", { type: "video/mp4" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "加载" }));
  await waitFor(() => expect(changes).toHaveLength(1));
  expect(calls.map(([command]) => command)).toEqual(["preview_video_write"]);
  expect([...(calls[0][1] as Uint8Array)]).toEqual([...new TextEncoder().encode("abc")]);

  view.rerender(<MasterVideoInput media={changes[0]} onChange={(media) => changes.push(media)} />);
  dialog = openDialog();
  fireEvent.click(within(dialog).getByRole("button", { name: "移除视频 1 的本地文件" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "加载" }));
  await waitFor(() => expect(changes).toHaveLength(2));
  expect(calls.slice(1)).toEqual([["preview_video_remove", { slot: 1 }]]);
});

// 场景：桌面端写入失败时提示原因，保留弹窗与已选文件，不更新预览来源。
test("桌面端写入失败保留弹窗与来源", async () => {
  mockDesktop(async () => { throw "本地预览视频正在被其他操作使用，请重试"; });
  const onChange = mock();
  render(<MasterVideoInput onChange={onChange} />);
  const dialog = openDialog();
  choose(dialog, 2, new File(["abc"], "b.mp4", { type: "video/mp4" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "加载" }));
  await waitFor(() => expect(within(dialog).getByRole("alert").textContent).toBe("视频 2：本地预览视频正在被其他操作使用，请重试"));
  expect(within(dialog).getByLabelText("视频 2 本地文件").textContent).toBe("b.mp4");
  expect(onChange).not.toHaveBeenCalled();
});
