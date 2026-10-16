/** 验证三个默认视频的环境配置、资源路径、本地视频优先级及截取规则；每例结束恢复环境；bun run test。 */
import { afterEach, beforeEach, expect, test } from "bun:test";
import { defaultPreviewUrls, previewVideos } from "@/features/templates/media";
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
  expect(await defaultPreviewUrls()).toEqual([
    "https://oss.joyfile.net/source/2026/09/14/RC202609141904000001/01c1b9a1e9f747408d6522f7ec4cee61.mp4",
    "https://oss.oemi.jdword.com/api/clip/2026-10-13/99083fe0c1d04947b523b0439401a0ee.mp4",
    "https://oss.oemi.jdword.com/api/clip/2026-10-13/80ea1f518457424986fb425bf6da5b36.mp4",
  ]);
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
