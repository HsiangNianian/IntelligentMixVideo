/** 验证三个默认视频的环境配置、资源路径、内置示例读取、本地视频优先级及截取规则；每例结束恢复环境；bun run test。 */
import { afterEach, beforeEach, expect, test } from "bun:test";
import { defaultPreviewUrls, previewVideos } from "@/features/templates/media";
import { fetchMock } from "./setup";
import { newDraft } from "@/features/templates/model";
import { previewContext, resolveTemplate } from "@/features/templates/tracks";
import { commitLocalVideos } from "@/features/templates/localVideos";

const keys = ["VITE_PREVIEW_VIDEO_1_URL", "VITE_PREVIEW_VIDEO_2_URL", "VITE_PREVIEW_VIDEO_3_URL"] as const;
let previous: (string | undefined)[];

beforeEach(() => {
  previous = keys.map((key) => process.env[key]);
  keys.forEach((key) => { delete process.env[key]; });
});

afterEach(() => {
  keys.forEach((key, index) => {
    if (previous[index] === undefined) delete process.env[key];
    else process.env[key] = previous[index];
  });
});

test("三个视频分别读取环境地址，并清除首尾空白", async () => {
  const urls = keys.map((key, index) => {
    const url = `https://media.example/video-${index + 1}.mp4`;
    process.env[key] = `  ${url}  `;
    return url;
  });
  expect(await defaultPreviewUrls()).toEqual(urls);
});

test("三个视频均支持 public 资源路径", async () => {
  keys.forEach((key, index) => { process.env[key] = `/preview-${index + 1}.mp4`; });
  expect(await defaultPreviewUrls()).toEqual(
    [1, 2, 3].map((index) => `${window.location.origin}/preview-${index}.mp4`),
  );
});

test("未配置、空字符串与空白分别使用对应内置示例", async () => {
  process.env.VITE_PREVIEW_VIDEO_2_URL = "";
  process.env.VITE_PREVIEW_VIDEO_3_URL = "  ";
  expect(await defaultPreviewUrls()).toEqual([1, 2, 3].map((index) => `${window.location.origin}/preview/sample-${index}.mp4`));
});

// 回归 #210：内置示例随客户端打包，读成 blob URL 播放并在应用内复用，不再每次从网络下载；读取失败的示例下次重新读取。
test("内置示例读成 blob URL，只读取一次并可在失败后重试", async () => {
  // blob URL 缓存在模块内，用带查询串的导入取得全新的模块实例。
  const specifier = "@/features/templates/media?bundled";
  const media: typeof import("@/features/templates/media") = await import(specifier);
  const urls = await media.defaultPreviewUrls();
  const source = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "src")!;
  // 设置地址后异步回报 10 秒、1080×1920 的元数据，模拟浏览器读取成功。
  Object.defineProperty(HTMLMediaElement.prototype, "src", {
    configurable: true,
    get: source.get,
    set(this: HTMLMediaElement, value: string) {
      source.set!.call(this, value);
      Object.defineProperties(this, {
        duration: { configurable: true, value: 10 },
        videoWidth: { configurable: true, value: 1080 },
        videoHeight: { configurable: true, value: 1920 },
      });
      queueMicrotask(() => this.dispatchEvent(new Event("loadedmetadata")));
    },
  });
  let missing = true;
  fetchMock.mockImplementation((async (input: RequestInfo | URL) => missing && String(input) === urls[0]
    ? new Response(null, { status: 404 })
    : new Response(new Blob(["video"], { type: "video/mp4" }))) as typeof fetch);
  try {
    const signal = new AbortController().signal;
    await expect(media.readPreviewVideos(urls, signal)).rejects.toThrow("视频 1：内置示例视频读取失败（HTTP 404）");
    missing = false;
    const first = await media.readPreviewVideos(urls, signal);
    expect(first.every((video) => video.url.startsWith("blob:"))).toBe(true);
    expect(first.map((video) => media.sourceUrl(video.url))).toEqual(urls);
    const again = await media.readPreviewVideos(urls, signal);
    expect(again.map((video) => video.url)).toEqual(first.map((video) => video.url));
    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([...urls, urls[0]]);
    // 内置示例同样只取前十秒，不依赖样片自身的时长；用户选择的本地文件仍使用完整时长。
    const local = { url: "blob:http://localhost/local", width: 1080, height: 1920, duration: 12 };
    expect(media.previewVideos([{ ...first[0], duration: 12 }, local, first[2]]).map((video) => video.duration)).toEqual([10, 12, 10]);
  } finally {
    Object.defineProperty(HTMLMediaElement.prototype, "src", source);
  }
});

// 回归：本地文件（blob URL）按完整时长排列，24、12、24 秒共 60 秒；默认与 URL 来源仍各取前十秒。
test("本地文件使用完整时长，URL 来源截取前十秒", () => {
  const video = { url: "blob:http://localhost:1420/local", duration: 24, width: 1080, height: 1920 };
  const local = [video, { ...video, duration: 12 }, video] as const;
  expect(previewContext(local).clips.map((clip) => clip.duration)).toEqual([24, 12, 24]);
  expect(resolveTemplate(newDraft(), previewContext(local)).duration).toBe(60);
  const remote = { ...video, url: "https://media.example/selected.mp4" };
  const mixed = [remote, { ...remote, duration: 4 }, video] as const;
  expect(previewVideos(mixed).map((item) => item.duration)).toEqual([10, 4, 24]);
});

// 浏览器中选择的本地视频只登记在页面内存：登记后该槽位默认使用本地文件，改回 URL 后恢复环境地址。
test("本地视频优先于环境默认地址，改回 URL 后恢复", async () => {
  keys.forEach((key, index) => { process.env[key] = `/preview-${index + 1}.mp4`; });
  const file = new File(["video"], "我的视频.mp4", { type: "video/mp4" });
  await commitLocalVideos([{ kind: "url", url: "" }, { kind: "file", name: file.name, url: "blob:local-2", file }, { kind: "url", url: "" }]);
  expect(await defaultPreviewUrls()).toEqual([`${window.location.origin}/preview-1.mp4`, "blob:local-2", `${window.location.origin}/preview-3.mp4`]);
  await commitLocalVideos([{ kind: "url", url: "" }, { kind: "url", url: "" }, { kind: "url", url: "" }]);
  expect(await defaultPreviewUrls()).toEqual([1, 2, 3].map((index) => `${window.location.origin}/preview-${index}.mp4`));
});
