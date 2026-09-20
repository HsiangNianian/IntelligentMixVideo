/** 多轨核心测试：真实目录、草稿变更、SDK 时间范围和保存数据；执行 bun run test。 */
import { expect, test } from "bun:test";
import { defaultEditor, draftEffects, newDraft, toDraft, type Template, type EffectTrack } from "@/features/templates/model";
import { sampleDraft } from "./fixtures";
import { readCatalog } from "@/features/templates/sdk";
import { addTrack, previewDuration, removeTrack, resolveTrack, setTrackRange, setTrackTiming, trackDraft, updateTrack } from "@/features/templates/tracks";
import { removeTarget, resetTextTarget } from "@/features/templates/effects";
import { buildPreviewRows, buildTimeline } from "@/features/templates/timeline";
import timingCases from "../../server/tests/template_timing_cases.json";

const catalog = readCatalog();
const vfx = catalog.find((asset) => asset.category === "vfx/normal")!;

// 场景：IMS 默认转场为一秒、文字入出场各半秒，滤镜和 VFX 覆盖完整视频；用户时长继续保留。
test("新对象采用 IMS 时间默认值并保留已设置的时长", () => {
  const empty = { ...newDraft(), tracks: [] };
  const transition = catalog.find((asset) => asset.category === "transition/normal")!;
  const added = addTrack(empty, transition, "title");
  expect(added.draft.tracks![0].duration).toBe(1);
  expect(buildTimeline(added.draft, catalog).VideoTracks[0].VideoTrackClips[0].Effects.at(-1)).toMatchObject({ Duration: 1 });
  for (const category of ["filter", "vfx/normal", "flower", "bubble"] as const) {
    const asset = catalog.find((item) => item.category === category)!;
    const track = addTrack(empty, asset, "title").draft.tracks![0];
    expect(track).toMatchObject({ start_mode: "seconds", start: 0, duration: null });
    for (const role of ["title", "subtitle", "bubble"] as const) {
      expect(track.editor[`${role}InDuration`]).toBe(0.5);
      expect(track.editor[`${role}OutDuration`]).toBe(0.5);
    }
  }
  const custom = setTrackTiming(added.draft, added.id, { start_mode: "seconds", start: 2, duration: 0.5 });
  expect(addTrack(custom, transition, "title").draft.tracks![0]).toMatchObject({ start: 2, duration: 0.5 });
});

// 场景：空对象列表经过初始化和 SDK 转换仍为空；添加及序列化只包含用户选择的效果。
test("空模板预览和保存配置只包含手动添加的对象", () => {
  const empty = newDraft();
  expect(empty).not.toHaveProperty("editor");
  expect(empty.tracks).toEqual([]);
  expect(draftEffects(empty)).toEqual([]);
  const preview = buildTimeline(empty, catalog);
  expect(preview.SubtitleTracks).toEqual([]);
  expect(preview.EffectTracks).toEqual([]);
  expect(buildPreviewRows(preview)).toHaveLength(1);
  const added = addTrack(empty, vfx, "title");
  expect(added.draft.tracks?.map((track) => track.target)).toEqual(["vfx"]);
  const restored = JSON.parse(JSON.stringify(added.draft));
  expect(restored.tracks).toEqual(added.draft.tracks!);
  expect(draftEffects(restored)).toEqual([vfx.id]);
  expect(removeTrack(restored, added.id).tracks).toEqual([]);
  expect(empty.tracks).toEqual([]);
});

// 场景：前后端共用区间和提示预期，检查帧取整及输入规则保持不变。
for (const item of timingCases) test(item.purpose, () => {
  if (item.mode !== "seconds" && item.mode !== "percent") throw new Error("用例开始方式无效");
  const track: EffectTrack = { ...sampleDraft().tracks[0], start_mode: item.mode, start: item.start, duration: item.length };
  const original = structuredClone(track);
  const result = resolveTrack(track, item.duration, item.fps);
  expect([result.start, result.end]).toEqual([item.expected_start, item.expected_end]);
  expect(result.notice).toBe(item.expected_notice);
  expect(track).toEqual(original);
});

// 场景：两个文字动画至少各占一帧，模板保留原时长，预览使用缩短后的副本。
test("动画副本按可用帧数缩短并拒绝无法容纳的区间", () => {
  const track = sampleDraft().tracks[0];
  track.editor.titleIn = "in/fade_in";
  track.editor.titleOut = "out/fade_out";
  track.duration = 0.2;
  expect(resolveTrack(track, 10).editor).toMatchObject({ titleInDuration: 0.1, titleOutDuration: 0.1 });
  expect(track.editor).toMatchObject({ titleInDuration: 0.5, titleOutDuration: 0.5 });
  track.duration = 1 / 30;
  expect(() => resolveTrack(track, 10)).toThrow("帧数不足");
});

// 场景：重复添加同类特效保留独立 ID、时间和 SDK 效果轨，目录 ID 只保存一次。
test("重复特效独立存在并按指定范围进入 SDK", () => {
  const first = addTrack(sampleDraft(), vfx, "title");
  const second = addTrack(first.draft, vfx, "title");
  expect(first.id).not.toBe(second.id);
  const draft = setTrackRange(setTrackRange(second.draft, first.id, 1, 4), second.id, 3, 8);
  const timeline = buildTimeline(draft, catalog);
  expect(timeline.EffectTracks.map((track) => track.EffectTrackItems[0])).toEqual([
    { Type: "VFX", ...vfx.parameters, TimelineIn: 1, TimelineOut: 4 },
    { Type: "VFX", ...vfx.parameters, TimelineIn: 3, TimelineOut: 8 },
  ]);
  expect(buildPreviewRows(timeline).filter((row) => row.actions[0].effectId === "vfx").map((row) => row.actions[0].start)).toEqual([1, 3]);
  expect(timeline.VideoTracks[0].VideoTrackClips[0].Effects).toEqual([{ Type: "Volume", Gain: 0 }]);
  expect(draftEffects(draft)).toEqual([vfx.id]);
});

// 场景：参数编辑和删除只作用于指定实例，先前草稿和其他对象保持不变。
test("删除实例不会清除其他同类效果", () => {
  const first = addTrack(sampleDraft(), vfx, "title");
  const second = addTrack(first.draft, vfx, "title");
  const current = second.draft.tracks!.find((track) => track.id === first.id)!;
  const before = JSON.stringify(second.draft);
  const next = updateTrack(second.draft, first.id, removeTarget(trackDraft(second.draft, current), "vfx"));
  expect(next.tracks!.map((track) => track.id)).not.toContain(first.id);
  expect(next.tracks!.find((track) => track.id === second.id)).toEqual(second.draft.tracks!.find((track) => track.id === second.id));
  expect(JSON.stringify(second.draft)).toBe(before);
});

// 场景：时间轴拒绝非法区间；短文字的动画在应用时缩短，模板参数保留。
test("时间边界和动画时长在 SDK 更新前校验", () => {
  const draft = sampleDraft();
  const motion = catalog.find((asset) => asset.id === "in/fade_in")!;
  const animated = addTrack(draft, motion, "title", "title").draft;
  for (const [start, end] of [[NaN, 3], [3, 2], [-1, 2], [0, 11]])
    expect(() => setTrackRange(animated, "title", start, end)).toThrow();
  expect(setTrackRange(animated, "title", 0, 0.5).tracks![0].duration).toBe(0.5);
  const short = setTrackRange(animated, "title", 1, 1.1).tracks![0];
  expect(short.editor.titleInDuration).toBe(0.5);
  expect(resolveTrack(short, 10).editor.titleInDuration).toBe(0.1);
  expect(() => addTrack(draft, motion, "title")).toThrow("请选择");
});

// 场景：保存内容只含时间规则；同一模板独立应用到长视频，序列化保留参数和 ID。
test("多轨序列化与预览媒体相互独立", () => {
  const added = addTrack(sampleDraft(), vfx, "title");
  const media = { url: "https://example.com/master.mp4", duration: 24, width: 1080, height: 1920 };
  const draft = setTrackTiming(added.draft, "title", { start_mode: "seconds", start: 12, duration: 8 });
  const saved: Template = { ...draft, template_id: "id", effect_ids: draftEffects(draft), effects: [vfx], created_at: "2026-01-01", updated_at: "2026-01-01" };
  const restored = toDraft(JSON.parse(JSON.stringify(saved)) as Template);
  expect(restored).toEqual(draft);
  restored.tracks![0].editor.title = "修改后的文字";
  expect(saved.tracks![0].editor.title).not.toBe("修改后的文字");
  const timeline = buildTimeline(draft, catalog, media);
  expect(JSON.stringify(saved)).not.toContain("media");
  expect(timeline.VideoTracks[0].VideoTrackClips[0]).toMatchObject({ Out: 24, TimelineOut: 24, MediaURL: media.url });
  expect(timeline.SubtitleTracks[0].SubtitleTrackClips[0]).toMatchObject({ TimelineIn: 12, TimelineOut: 20 });
  expect(timeline.FECanvas).toEqual({ Width: 800, Height: 1422 });
});

// 场景：转场范围对应母版两个片段的重叠时间，替换转场保留唯一切换位置。
test("转场对应两个母版片段", () => {
  const transition = catalog.find((asset) => asset.category === "transition/normal")!;
  const added = addTrack(sampleDraft(), transition, "title");
  const draft = setTrackTiming(added.draft, added.id, { start_mode: "seconds", start: 3, duration: 1 });
  const timeline = buildTimeline(draft, catalog);
  expect(timeline.VideoTracks[0].VideoTrackClips.map((clip) => [clip.In, clip.Out, clip.TimelineIn, clip.TimelineOut])).toEqual([[0, 4, 0, 4], [4, 10, 3, 9]]);
  expect(timeline.VideoTracks[0].VideoTrackClips[0].Effects.at(-1)).toMatchObject({ Type: "Transition", Duration: 1 });
  const replaced = addTrack(draft, transition, "title");
  expect(replaced.id).toBe(added.id);
  expect(replaced.draft.tracks!.filter((track) => track.target === "transition")).toMatchObject([{ start: 3, duration: 1 }]);
  const boundary = buildTimeline(setTrackRange(draft, added.id, 4, 4.1), catalog);
  expect(boundary.VideoTracks[0].VideoTrackClips[0].Effects.at(-1)?.Duration).toBe(0.1);
});

// 场景：转场连接连续源片段，调整持续时间只改变应用结果，全长对象规则始终保留。
test("转场使用不同源画面并按重叠时长缩短合成", () => {
  const transition = catalog.find((asset) => asset.id === "transition/normal/angular")!;
  const original = sampleDraft();
  const media = { url: "https://example.com/master.mp4", duration: 10, width: 1920, height: 1080 };
  const added = addTrack(original, transition, "title");
  const draft = setTrackTiming(added.draft, added.id, { start_mode: "seconds", start: 5, duration: 1 });
  const timeline = buildTimeline(draft, catalog, media);
  const [first, second] = timeline.VideoTracks[0].VideoTrackClips;
  expect(first.MediaURL).toBe(second.MediaURL);
  expect(first.Out).toBe(second.In);
  expect(first.Effects.at(-1)).toEqual({ Type: "Transition", Duration: 1, SubType: "angular" });
  expect(first.In + 5.3 - first.TimelineIn).toBeCloseTo(5.3);
  expect(second.In + 5.3 - second.TimelineIn).toBeCloseTo(6.3);
  expect(second.Out).toBe(10);
  expect(second.TimelineOut).toBe(9);
  expect(previewDuration(draft)).toBe(9);
  expect(draft.tracks!.slice(0, 2)).toEqual(original.tracks);
  expect(timeline.SubtitleTracks.flatMap((track) => track.SubtitleTrackClips).map((clip) => clip.TimelineOut)).toEqual([9, 9]);
  expect(buildPreviewRows(timeline)[1].actions[0]).toMatchObject({ sourceIn: 6, sourceOut: 10, start: 5, end: 9 });
  expect(() => setTrackRange(draft, "title", 8, 9.1)).toThrow();
  const ranged = setTrackTiming(draft, "title", { start_mode: "seconds", start: 7, duration: 2 });
  const changed = setTrackTiming(ranged, added.id, { start_mode: "seconds", start: 5, duration: 2 });
  expect(buildTimeline(changed, catalog).SubtitleTracks[0].SubtitleTrackClips[0].TimelineOut).toBe(8);
  expect(ranged.tracks!.find((track) => track.id === "title")).toMatchObject({ start: 7, duration: 2 });
  const selected = draft.tracks!.find((track) => track.id === added.id)!;
  const edited = updateTrack(draft, added.id, { ...trackDraft(draft, selected), transition_duration_seconds: 0.9707 });
  expect(previewDuration(edited)).toBeCloseTo(10 - 29 / 30);
  const longer = { ...media, duration: 20 };
  expect(previewDuration(draft, longer)).toBe(19);
  expect(buildTimeline(draft, catalog, longer).VideoTracks[0].VideoTrackClips[1]).toMatchObject({ In: 6, Out: 20, TimelineIn: 5, TimelineOut: 19 });
  expect(previewDuration(removeTrack(draft, added.id))).toBe(10);
  expect(removeTrack(draft, added.id).tracks).toEqual(original.tracks);
});

// 场景：短视频截短有效对象并跳过结尾以外的对象；更换视频不修改任何模板规则。
test("更换预览视频只重新计算显示区间", () => {
  const media = { url: "https://example.com/short.mp4", duration: 4, width: 1080, height: 1920 };
  const draft = sampleDraft();
  expect(buildTimeline(draft, catalog, media).SubtitleTracks.map((row) => row.SubtitleTrackClips[0].TimelineOut)).toEqual([4, 4]);
  const ranged = setTrackRange(draft, "title", 2, 6);
  const original = JSON.stringify(ranged);
  const timeline = buildTimeline(ranged, catalog, media);
  expect(timeline.SubtitleTracks[0].SubtitleTrackClips[0]).toMatchObject({ TimelineIn: 2, TimelineOut: 4 });
  expect(timeline.notices[0]).toContain("缩短");
  expect(buildTimeline(ranged, catalog, { ...media, duration: 1 }).SubtitleTracks).toHaveLength(1);
  expect(JSON.stringify(ranged)).toBe(original);
});

// 场景：百分比随视频时长变化，时间轴移动保留百分比方式和固定持续秒数。
test("百分比规则与时间轴修改可以往返计算", () => {
  const draft = setTrackTiming(sampleDraft(), "title", { start_mode: "percent", start: 25, duration: 3 });
  for (const [duration, start] of [[20, 5], [60, 15], [120, 30]])
    expect(resolveTrack(draft.tracks![0], duration)).toMatchObject({ start, end: start + 3 });
  const moved = setTrackRange(draft, "title", 6, 9, { url: "https://example.com/video.mp4", duration: 20, width: 1920, height: 1080 });
  expect(moved.tracks![0]).toMatchObject({ start_mode: "percent", start: 30, duration: 3 });
  expect(resolveTrack(moved.tracks![0], 60)).toMatchObject({ start: 18, end: 21 });
});

// 场景：调整持续到结束对象的左侧位置保留结束规则，缩短右侧位置改为固定持续时间。
test("时间轴分别处理开始位置和结束位置", () => {
  const draft = sampleDraft();
  const left = setTrackRange(draft, "title", 2, 10);
  expect(left.tracks![0]).toMatchObject({ start: 2, duration: null });
  expect(setTrackRange(left, "title", 2, 8).tracks![0]).toMatchObject({ start: 2, duration: 6 });
  const transition = catalog.find((asset) => asset.id === "transition/normal/angular")!;
  const added = addTrack(draft, transition, "title");
  const resized = setTrackRange(added.draft, added.id, 3, 4);
  const track = resized.tracks!.find((item) => item.id === added.id)!;
  expect(track.start_mode).toBe("percent");
  expect(resolveTrack(track, previewDuration(resized))).toMatchObject({ start: 3, end: 4 });
});

// 场景：非法百分比、持续时间和数值直接报错；固定秒数允许超过预览时长。
test("规则校验独立于预览视频", () => {
  const draft = sampleDraft();
  for (const timing of [
    { start_mode: "percent" as const, start: 100, duration: 3 },
    { start_mode: "seconds" as const, start: -1, duration: 3 },
    { start_mode: "seconds" as const, start: NaN, duration: 3 },
    { start_mode: "seconds" as const, start: 1, duration: 0 },
    { start_mode: "seconds" as const, start: 1, duration: Infinity },
  ]) expect(() => setTrackTiming(draft, "title", timing)).toThrow();
  const distant = setTrackTiming(draft, "title", { start_mode: "seconds", start: 200, duration: null });
  expect(resolveTrack(distant.tracks![0], 300)).toMatchObject({ start: 200, end: 300 });
  expect(resolveTrack(distant.tracks![0], 10).notice).toContain("没有可显示");
});

// 场景：结尾以外的对象仍验证真实效果目录，未知效果不能通过不显示规则隐藏。
test("跳过显示的对象仍校验效果引用", () => {
  const added = addTrack(sampleDraft(), vfx, "title");
  const draft = setTrackTiming(added.draft, added.id, { start_mode: "seconds", start: 100, duration: 3 });
  draft.tracks!.find((track) => track.id === added.id)!.editor.vfx = "vfx/normal/unknown";
  expect(() => buildTimeline(draft, catalog)).toThrow("效果不在对应目录");
});

// 场景：重置气泡样式保留该文字实例及其时间，仍可生成基础文字预览。
test("重置文字实例保留轨道", () => {
  const asset = catalog.find((item) => item.category === "bubble")!;
  const added = addTrack(sampleDraft(), asset, "title");
  const track = added.draft.tracks!.find((item) => item.id === added.id)!;
  const reset = updateTrack(added.draft, track.id, resetTextTarget(trackDraft(added.draft, track), "bubble"));
  expect(reset.tracks!.find((item) => item.id === track.id)?.editor.bubbleText).toBe("超值特惠");
  expect(buildTimeline(reset, catalog).SubtitleTracks.flatMap((item) => item.SubtitleTrackClips).some((item) => item.Content === "超值特惠")).toBe(true);
});

// 场景：移除文字后通过花字资产重新添加获得独立实例，其余文字参数保留。
test("文字通过资产重新添加并保留其他对象", () => {
  const draft = sampleDraft();
  const asset = catalog.find((item) => item.category === "flower")!;
  const added = addTrack(removeTrack(draft, "title"), asset, "title");
  expect(added.id).not.toBe("title");
  expect(added.draft.tracks!.find((track) => track.id === "subtitle")).toEqual(draft.tracks[1]);
  expect(added.draft.tracks!.find((track) => track.id === added.id)?.editor.title).toBe(defaultEditor.title);
  expect(added.draft.tracks!.find((track) => track.id === added.id)?.editor.titleFlower).toBe(asset.id);
});
