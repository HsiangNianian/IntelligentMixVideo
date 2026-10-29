/** 数字人预览几何、开场时间、抠像与基础播放器回归；bun run test，不访问云服务。 */
import { expect, mock, test } from "bun:test";
import { createRef } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { defaultAvatarEffects } from "@/features/templates/avatarEffects";
import { previewHeadCircle } from "@/features/templates/avatarPreview";
import { BasicVideoPreview } from "@/features/templates/BasicVideoPreview";
import { newDraft, type Draft } from "@/features/templates/model";
import type { PlaybackHandle } from "@/features/templates/previewClock";
import { defaultSplitScreen } from "@/features/templates/splitScreen";
import { PreviewTimeline } from "@/features/templates/PreviewTimeline";
import { buildPreviewRows, buildTimeline } from "@/features/templates/timeline";
import { previewContext, resolveTemplate } from "@/features/templates/tracks";
import { sampleMedia } from "./fixtures";
import { watchMattingFailures } from "@/features/templates/sdk";

// 模型下载失败需明确降级；普通媒体请求不受影响，卸载恢复原请求函数。
test("模型 HTTP 错误和断网直接报告，其他资源不误报，卸载恢复原 fetch", async () => {
  const fetch = mock(async () => new Response("unavailable", { status: 503 }));
  const view = Object.assign(new EventTarget(), { fetch }) as unknown as Window & typeof globalThis;
  const originalFetch = view.fetch;
  const failure = mock();
  const stop = watchMattingFailures(view, failure);
  const model = "https://g.alicdn.com/sdk/wasm/starry/model/v_tfmodel/model.json";
  await view.fetch("https://example.test/video.mp4");
  expect(failure).not.toHaveBeenCalled();
  expect((await view.fetch(model)).status).toBe(503);
  expect(failure).toHaveBeenCalledTimes(1);
  const offline = new Error("offline");
  fetch.mockRejectedValueOnce(offline);
  await expect(view.fetch(model)).rejects.toBe(offline);
  expect(failure).toHaveBeenLastCalledWith(offline);
  stop();
  expect(view.fetch).toBe(originalFetch);
});

// 固定示意头部基准按源短边计算，越界圆心仍保持完整圆形。
test("示意直径固定为短边 25%，留量改变半径，源百分比圆心受边缘约束", () => {
  const source = { width: 1080, height: 1920 };
  expect(previewHeadCircle(source, 1.6, { x: 30, y: 20 })).toEqual({ x: 324, y: 384, radius: 216 });
  expect(previewHeadCircle(source, 3, { x: 0, y: 100 })).toEqual({ x: 405, y: 1515, radius: 405 });
  expect(previewHeadCircle({ width: 1920, height: 1080 }, 1)).toEqual({ x: 960, y: 540, radius: 135 });
});

// 手动取景和留量只改变源裁剪，不改变成片头像窗口或修改草稿。
test("圆形取景只改变 SDK 裁剪与蒙版，留量变化不放大头像摆放窗口", () => {
  const draft: Draft = { ...newDraft(), split_screen: { ...defaultSplitScreen(), enabled: true, layout: "circle" }, avatar_effects: { ...defaultAvatarEffects(), feather: 0.2, removeBackground: true } };
  const before = JSON.stringify(draft);
  const resolved = resolveTemplate(draft, previewContext(sampleMedia));
  const first = buildTimeline(draft, [], sampleMedia, resolved, undefined, 0, { x: 30, y: 40 }).VideoTracks[1].VideoTrackClips[0];
  const next = buildTimeline({ ...draft, avatar_effects: { ...draft.avatar_effects!, circleScale: 3 } }, [], sampleMedia, resolved, undefined, 0, { x: 70, y: 60 }).VideoTracks[1].VideoTrackClips[0];
  expect([first.X, first.Y, first.Width, first.Height]).toEqual([next.X, next.Y, next.Width, next.Height]);
  expect(first.Effects.find((effect) => effect.Type === "Crop")).not.toEqual(next.Effects.find((effect) => effect.Type === "Crop"));
  expect(first.Effects.some((effect) => effect.Type === "AI_RealMatting")).toBe(false);
  const mask = new URL(first.MaskVideoURL!);
  expect(mask.searchParams.get("x")).toBe("0.3");
  expect(mask.searchParams.get("feather")).toBe("0.2");
  expect([...mask.searchParams.keys()].sort()).toEqual(["feather", "height", "radius", "seconds", "width", "x", "y"]);
  expect(JSON.stringify(draft)).toBe(before);
});

// 开场结束沿用素材绝对进度，双语字幕切回同屏位置，抠像仅作用于数字人。
test("开场不补时，结束后保持原素材源进度，中英字幕切回各自同屏位置", () => {
  const draft: Draft = { ...newDraft(), split_screen: { ...defaultSplitScreen(), enabled: true }, avatar_effects: { ...defaultAvatarEffects(), openingEnabled: true, openingDuration: 2.01, removeBackground: true } };
  const subtitle = draft.tracks.find((track) => track.target === "subtitle")!;
  draft.tracks.push({ ...subtitle, id: "subtitle-en", language: "en" });
  const resolved = resolveTemplate(draft, previewContext(sampleMedia));
  const timeline = buildTimeline(draft, [], sampleMedia, resolved);
  expect(timeline.openingTracks).toHaveLength(2);
  expect(timeline.openingTracks[0].VideoTrackClips[0].TimelineOut).toBe(2);
  expect(timeline.duration).toBe(resolved.duration);
  for (const row of timeline.VideoTracks) for (const clip of row.VideoTrackClips.filter((clip) => clip.Type === "Video")) {
    expect(clip.TimelineIn).toBeGreaterThanOrEqual(2);
    if (clip.TimelineIn === 2) expect(clip.In).toBe(2);
  }
  const subtitles = timeline.SubtitleTracks.slice(1);
  expect(subtitles.map((track) => track.SubtitleTrackClips.map((clip) => [clip.TimelineIn, clip.Y]))).toEqual([
    [[0, subtitle.editor.subtitleY / 100], [2, 0.62]], [[0, subtitle.editor.subtitleY / 100], [2, 0.655]],
  ]);
  expect(timeline.VideoTracks.flatMap((row) => row.VideoTrackClips).filter((clip) => clip.Effects.some((effect) => effect.Type === "AI_RealMatting")).every((clip) => clip.MediaURL === sampleMedia[0].url && !clip.MaskVideoURL)).toBe(true);
});

// SDK 降级后的基础播放器沿用同一圆形与时间规则，调参后回零暂停。
test("基础预览切换开场和头像且保留绝对源时间，取景修改回零", () => {
  const ref = createRef<PlaybackHandle>();
  const layout = { ...defaultSplitScreen(), enabled: true, layout: "circle" as const };
  const effects = { ...defaultAvatarEffects(), openingEnabled: true, openingDuration: 2, feather: 0.2 };
  const props = { ref, media: sampleMedia, resolved: resolveTemplate(newDraft(), previewContext(sampleMedia)), order: [0, 1, 2], layout, avatarEffects: effects,
    onTime() {}, onReady() {}, onError() {}, onPlayingChange() {}, onEnd() {} };
  const view = render(<BasicVideoPreview {...props} previewCenter={{ x: 30, y: 40 }} />);
  const opening = screen.getByTestId("circle-opening-preview");
  expect(opening.style.display).toBe("");
  act(() => ref.current!.seek(2.5));
  expect(opening.style.display).toBe("none");
  expect(screen.getByLabelText<HTMLVideoElement>("数字人同屏预览").currentTime).toBe(2.5);
  expect(screen.getByLabelText<HTMLVideoElement>("素材同屏预览").currentTime).toBe(2.5);
  const style = screen.getByLabelText<HTMLVideoElement>("数字人同屏预览").style;
  expect(style.maskImage).toContain("60%");
  expect(style.left).not.toBe("");
  view.rerender(<BasicVideoPreview {...props} previewCenter={{ x: 70, y: 60 }} />);
  expect(opening.style.display).toBe("");
  expect(screen.getByLabelText<HTMLVideoElement>("圆形开场预览").currentTime).toBe(0);
});


// 时间轴使用真实开场区间，长开场截到片尾；头像单独开启不产生开场轨道。
test("圆形蒙版轨道按开场时长显示、可选中，移除后消失", () => {
  const draft: Draft = { ...newDraft(), avatar_effects: { ...defaultAvatarEffects(), openingEnabled: true, openingDuration: 2.01 } };
  const resolved = resolveTemplate(draft, previewContext(sampleMedia));
  const rows = buildPreviewRows(buildTimeline(draft, [], sampleMedia, resolved));
  expect(rows.find((row) => row.id === "avatar-mask")?.actions[0]).toMatchObject({ targetId: "avatar-mask", label: "圆形蒙版", start: 0, end: 2, movable: false, flexible: false });
  expect(rows.findIndex((row) => row.id === "avatar-mask")).toBeLessThan(rows.findIndex((row) => row.actions[0]?.effectId === "video"));
  const select = mock();
  render(<PreviewTimeline ref={null} rows={rows} disabled={false} time={0} duration={resolved.duration} onSeek={() => {}} onSelect={select} />);
  fireEvent.click(screen.getByRole("button", { name: "编辑圆形蒙版" }));
  expect(select).toHaveBeenCalledWith("avatar-mask");
  draft.avatar_effects!.openingDuration = 300;
  expect(buildPreviewRows(buildTimeline(draft, [], sampleMedia, resolved)).find((row) => row.id === "avatar-mask")?.actions[0].end).toBe(30);
  draft.avatar_effects!.openingEnabled = false;
  draft.split_screen = { ...defaultSplitScreen(), enabled: true, layout: "circle" };
  expect(buildPreviewRows(buildTimeline(draft, [], sampleMedia, resolved)).some((row) => row.id === "avatar-mask")).toBe(false);
});
