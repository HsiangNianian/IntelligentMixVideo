/** 验证三个默认视频的环境配置、资源路径及用户选择的优先级；每例结束恢复环境。 */
import { afterEach, beforeEach, expect, test } from "bun:test";
import { previewVideos, previewUrls } from "@/features/templates/media";

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

test("三个视频分别读取环境地址，并清除首尾空白", () => {
  const urls = keys.map((key, index) => {
    const url = `https://media.example/video-${index + 1}.mp4`;
    process.env[key] = `  ${url}  `;
    return url;
  });
  expect(previewUrls()).toEqual(urls);
});

test("三个视频均支持 public 资源路径", () => {
  keys.forEach((key, index) => { process.env[key] = `/preview-${index + 1}.mp4`; });
  expect(previewUrls()).toEqual(
    [1, 2, 3].map((index) => `${window.location.origin}/preview-${index}.mp4`),
  );
});

test("未配置、空字符串与空白分别使用对应内置示例", () => {
  process.env.VITE_PREVIEW_VIDEO_2_URL = "";
  process.env.VITE_PREVIEW_VIDEO_3_URL = "  ";
  expect(previewUrls()).toEqual([
    "https://oss.joyfile.net/source/2026/09/14/RC202609141904000001/01c1b9a1e9f747408d6522f7ec4cee61.mp4",
    "https://oss.oemi.jdword.com/api/clip/2026-10-13/99083fe0c1d04947b523b0439401a0ee.mp4",
    "https://oss.oemi.jdword.com/api/clip/2026-10-13/80ea1f518457424986fb425bf6da5b36.mp4",
  ]);
});

test("用户选择的视频优先于环境默认地址并继续截取前十秒", () => {
  keys.forEach((key) => { process.env[key] = "/default.mp4"; });
  const video = { url: "https://media.example/selected.mp4", duration: 12, width: 1080, height: 1920 };
  const selected = [video, { ...video, duration: 4 }, { ...video, duration: 6 }] as const;
  expect(previewVideos(selected).map(({ url, duration }) => ({ url, duration }))).toEqual(
    [10, 4, 6].map((duration) => ({ url: video.url, duration })),
  );
});
