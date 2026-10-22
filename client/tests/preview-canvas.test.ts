/** 固定 1080×1920 画布的数据转换与 SDK 绘制倍数测试；使用实际目录与转换函数，SDK 由假的 iframe 文档代替，执行 bun run test。 */
import { expect, test } from "bun:test";
import { newDraft } from "@/features/templates/model";
import { loadSDK, readCatalog, type PreviewSDK } from "@/features/templates/sdk";
import { addTrack } from "@/features/templates/tracks";
import { previewTransitions } from "@/features/templates/transitions";
import { playerTimeline } from "@/features/templates/timeline";
import { sampleDraft, sampleMedia, buildTestTimeline as buildTimeline } from "./fixtures";

const catalog = readCatalog();

// 高分屏上 SDK 内置的 Konva 默认按屏幕倍数放大画布；加载完成、创建播放器之前绘制倍数固定为 1，画布与视频原尺寸一致。
test("SDK 加载后按视频原尺寸绘制，不随屏幕缩放倍数放大", async () => {
  const player = function Player() {} as unknown as PreviewSDK;
  const view = { AliyunTimelinePlayer: undefined as PreviewSDK | undefined, Konva: { pixelRatio: 2 } };
  // 只实现 loadSDK 用到的接口：追加脚本与样式后模拟两者都加载完成，脚本执行时挂上 SDK 与 Konva。
  const target = {
    defaultView: view,
    createElement: () => ({ remove() {} }) as unknown as HTMLElement,
    head: {
      append(...elements: { onload?: () => void }[]) {
        queueMicrotask(() => {
          view.AliyunTimelinePlayer = player;
          for (const element of elements) element.onload?.();
        });
      },
    },
  } as unknown as Document;
  expect(await loadSDK(target, new AbortController().signal)).toBe(player);
  expect(view.Konva.pixelRatio).toBe(1);
});

// 回归：换了比例不同的预览视频后画布跟着变（1080×1440、720×1280），字号与位置都随之错位。画布固定为成片的
// 1080×1920，视频与正式合成一样等比放入；比例不同时用模糊背景填充留白，比例相同（含 720×1280）时不加模糊。
// 文字字号与相对位置不随视频变化。
test.each([[1920, 1080], [1080, 1920], [1080, 1080], [2560, 1080], [1080, 1440], [720, 1280]])(
  "%i × %i 视频固定使用 1080 × 1920 画布并保留文字参数",
  (width, height) => {
    const draft = sampleDraft();
    draft.tracks[0].editor.titleSize = 120;
    draft.tracks[0].editor.titleX = 25;
    draft.tracks[0].editor.titleY = 75;
    const original = structuredClone(draft);
    const defaults = sampleMedia;
    const timeline = buildTimeline(draft, catalog, [{
      url: "https://example.com/video.mp4", width, height, duration: 12,
    }, defaults[1], defaults[2]]);
    expect(timeline.FECanvas).toEqual({ Width: 1080, Height: 1920 });
    expect(timeline.AspectRatio).toBe("1080:1920");
    expect(timeline.VideoTracks[0].VideoTrackClips[0]).toMatchObject({ AdaptMode: "Contain" });
    expect(timeline.VideoTracks[0].VideoTrackClips[0].Effects.some((effect) => effect.SubType === "Blur"))
      .toBe(Math.abs(width / height - 9 / 16) > 0.005);
    expect(timeline.SubtitleTracks[0].SubtitleTrackClips[0]).toMatchObject({
      FontSize: 120, X: 0.25, Y: 0.75, TimelineOut: 30,
    });
    expect(draft).toEqual(original);
  },
);

// 默认三个竖屏视频各取前十秒，两个基础文字覆盖完整视频。
test("新模板使用 1080 × 1920 三十秒画布", () => {
  const timeline = buildTimeline(newDraft(), catalog);
  expect(timeline.FECanvas).toEqual({ Width: 1080, Height: 1920 });
  expect(timeline.AspectRatio).toBe("1080:1920");
  expect(timeline.VideoTracks[0].VideoTrackClips[0]).toMatchObject({ Out: 10, TimelineOut: 10 });
  expect(timeline.SubtitleTracks.map((row) => row.SubtitleTrackClips[0].TimelineOut)).toEqual([30, 30]);
});

// 转场补帧画面与原视频比例相同：横屏视频的补帧沿用视频片段的模糊背景，与成片一致；竖屏视频的补帧没有留白，仍用黑色。
test("转场补帧沿用视频的留白填充", () => {
  const draft = addTrack(newDraft(), catalog.find((item) => item.category === "transition/normal")!, "transition").draft;
  const landscape = { url: "https://example.com/landscape.mp4", width: 1920, height: 1080, duration: 12 };
  const timeline = buildTimeline(previewTransitions(draft, [0, 0]).draft, catalog, [landscape, sampleMedia[1], sampleMedia[2]]);
  const frames = new Map(["1:0", "1:1", "2:0", "2:1"].map((key) => [key, "data:image/png;base64,AA=="]));
  const backgrounds = playerTimeline(timeline, frames).VideoTracks.slice(1, 3)
    .map((row) => row.VideoTrackClips.map((clip) => (clip as { Effects: { SubType?: string }[] }).Effects[0].SubType));
  expect(backgrounds).toEqual([["Blur", "Color"], ["Color", "Color"]]);
});
