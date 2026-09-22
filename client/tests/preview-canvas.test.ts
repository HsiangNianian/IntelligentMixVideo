/** 原始分辨率画布的数据转换测试；使用实际目录与转换函数，执行 bun run test。 */
import { expect, test } from "bun:test";
import { newDraft } from "@/features/templates/model";
import { readCatalog } from "@/features/templates/sdk";
import { buildTimeline } from "@/features/templates/timeline";
import { sampleDraft } from "./fixtures";

const catalog = readCatalog();

// 横屏、竖屏、方形及超宽视频保留原始像素尺寸，文字字号与相对位置不随画布缩放。
test.each([[1920, 1080], [1080, 1920], [1080, 1080], [2560, 1080]])(
  "%i × %i 视频使用原始画布并保留文字参数",
  (width, height) => {
    const draft = sampleDraft();
    draft.tracks[0].editor.titleSize = 120;
    draft.tracks[0].editor.titleX = 25;
    draft.tracks[0].editor.titleY = 75;
    const original = structuredClone(draft);
    const timeline = buildTimeline(draft, catalog, {
      url: "https://example.com/video.mp4", width, height, duration: 12,
    });
    expect(timeline.FECanvas).toEqual({ Width: width, Height: height });
    expect(timeline.AspectRatio).toBe(`${width}:${height}`);
    expect(timeline.SubtitleTracks[0].SubtitleTrackClips[0]).toMatchObject({
      FontSize: 120, X: 0.25, Y: 0.75, TimelineOut: 12,
    });
    expect(draft).toEqual(original);
  },
);

// 未提供媒体时保留十秒默认画布，空模板也能生成视频轨道。
test("默认空模板使用 1920 × 1080 十秒画布", () => {
  const timeline = buildTimeline(newDraft(), catalog);
  expect(timeline.FECanvas).toEqual({ Width: 1920, Height: 1080 });
  expect(timeline.AspectRatio).toBe("1920:1080");
  expect(timeline.VideoTracks[0].VideoTrackClips[0]).toMatchObject({ Out: 10, TimelineOut: 10 });
  expect(timeline.SubtitleTracks).toEqual([]);
});
