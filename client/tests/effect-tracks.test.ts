/** 多轨核心测试：真实目录、草稿变更、SDK 时间范围和保存数据；执行 bun run test。 */
import { expect, test } from "bun:test";
import { defaultEditor, draftEffects, newDraft, toDraft, type Template, type EffectTrack } from "@/features/templates/model";
import { sampleDraft, sampleMedia, buildTestTimeline as buildTimeline } from "./fixtures";
import { readCatalog } from "@/features/templates/sdk";
import { addTrack, previewContext, resolveTemplate, removeTrack, resolveTrack, setTrackTiming, trackDraft, updateTrack } from "@/features/templates/tracks";
import { reorderVideos } from "@/features/templates/media";
import { removeTarget, resetTextTarget } from "@/features/templates/effects";
import { buildPreviewRows, formatKeyword, formatSubtitleKeyword, playerTimeline } from "@/features/templates/timeline";
import timingCases from "../../server/tests/template_timing_cases.json";

const catalog = readCatalog();
const vfx = catalog.find((asset) => asset.category === "vfx/normal")!;
const filter = catalog.find((asset) => asset.category === "filter")!;

test("播放器从视频向上叠加滤镜、VFX、字幕和标题", () => {
  let draft = addTrack(newDraft(), vfx, "vfx").draft;
  draft = addTrack(draft, filter, "filter").draft;
  const timeline = buildTimeline(draft, catalog);
  const applied = playerTimeline(timeline);
  expect(applied.VideoTracks.map((row) => row.VideoTrackClips[0].Type)).toEqual(["Video", "Filter", "VFX", "Text", "Text"]);
  expect(applied.VideoTracks.slice(-2).map((row) => {
    const clip = row.VideoTrackClips[0];
    return "Content" in clip ? clip.Content : undefined;
  })).toEqual([defaultEditor.subtitle, defaultEditor.title]);
  expect(applied.VideoTracks[0].VideoTrackClips).toHaveLength(3);
  expect(timeline.VideoTracks[0].VideoTrackClips[0].source).toBe(0);
});

// 场景：字幕关键词组合样式只包围首次匹配文字，关闭后保持原文，重置清除全部选项。
test("字幕关键词样式生成局部指令并随对象重置", () => {
  const editor = { ...defaultEditor, subtitleKeywordBold: true, subtitleKeywordItalic: true,
    subtitleKeywordUnderline: true, subtitleKeywordStrikeout: true };
  expect(formatSubtitleKeyword("甲乙甲乙", "甲乙", editor)).toBe("{\\b1\\i1\\u1\\s1}甲乙{\\b0\\i0\\u0\\s0}甲乙");
  expect(formatSubtitleKeyword("甲乙", "", editor)).toBe("甲乙");
  expect(formatSubtitleKeyword("甲乙", "甲", defaultEditor)).toBe("甲乙");
  expect(() => formatSubtitleKeyword("甲乙", "丙", editor)).toThrow("关键词不在字幕文字中");
  expect(formatSubtitleKeyword("甲乙甲乙", "甲乙", { ...defaultEditor, subtitleKeywordColor: "#12ab34" }))
    .toBe("{\\1c&34AB12&}甲乙{\\1c}甲乙");
  expect(formatSubtitleKeyword("甲乙", "甲", { ...editor, subtitleKeywordColor: "#12AB34" }))
    .toBe("{\\1c&34AB12&\\b1\\i1\\u1\\s1}甲{\\1c\\b0\\i0\\u0\\s0}乙");
  expect(() => formatSubtitleKeyword("甲乙", "甲", { ...editor, subtitleKeywordColor: "red" }))
    .toThrow("关键词颜色须为 #RRGGBB");
  const draft = sampleDraft();
  draft.tracks[1].editor = { ...draft.tracks[1].editor, ...editor, title: "" };
  draft.tracks[1].editor.subtitle = "重点 其他文字";
  const preview = buildTimeline(draft, catalog).SubtitleTracks[1].SubtitleTrackClips[0].Content;
  expect(preview).toContain("{\\b1\\i1\\u1\\s1}重点{\\b0\\i0\\u0\\s0}");
  const reset = resetTextTarget(trackDraft(draft, draft.tracks[1]), "subtitle");
  expect(reset.editor).toMatchObject({ subtitleKeywordBold: false, subtitleKeywordItalic: false,
    subtitleKeywordUnderline: false, subtitleKeywordStrikeout: false, subtitleKeywordColor: "" });
});

// 场景：标题未指定关键词时，预览自动标记示例标题的前两个字。
test("标题预览自动显示关键词样式", () => {
  const draft = sampleDraft();
  draft.tracks[0].editor.titleKeywordBold = true;
  draft.tracks[0].editor.titleKeywordColor = "#FF0000";
  expect(buildTimeline(draft, catalog).SubtitleTracks[0].SubtitleTrackClips[0].Content)
    .toContain("{\\1c&0000FF&\\b1}让每{\\1c\\b0}一");
});

// 场景：标题和字幕局部字号只作用于首次关键词，结尾恢复原字号，独立对象编辑和重置保留默认值。
test("关键词字号进入两类文字预览并可恢复默认", () => {
  const draft = sampleDraft();
  draft.tracks[0].editor.titleKeywordSize = 64;
  draft.tracks[1].editor.subtitleKeywordSize = 48;
  const clips = buildTimeline(draft, catalog).SubtitleTracks.map((row) => row.SubtitleTrackClips[0]);
  expect(clips[0].Content).toContain("{\\fs64}让每{\\fs}");
  expect(clips[1].Content).toContain("{\\fs48}选择{\\fs}");
  expect(clips[0].FontSize).toBe(defaultEditor.titleSize);
  expect(clips[1].FontSize).toBe(defaultEditor.subtitleSize);
  expect(formatKeyword("词语词语", "词语", { ...defaultEditor, titleKeywordSize: 64 }, "title"))
    .toBe("{\\fs64}词语{\\fs}词语");
  expect(() => formatSubtitleKeyword("词语", "词", { ...defaultEditor, subtitleKeywordSize: 11 }))
    .toThrow("关键词字号须为 12～300 的整数");
  expect(resetTextTarget(trackDraft(draft, draft.tracks[0]), "title").editor.titleKeywordSize).toBe(0);
  expect(removeTarget(trackDraft(draft, draft.tracks[1]), "subtitle").editor.subtitleKeywordSize).toBe(0);
});

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
  const custom = updateTrack(added.draft, added.id, { ...trackDraft(added.draft, added.draft.tracks[0]), transition_duration_seconds: 0.5 });
  expect(addTrack(custom, transition, "title", undefined, 1).draft.tracks![0]).toMatchObject({ transition_boundary: 1, duration: 0.5 });
  expect(custom.tracks[0]).not.toHaveProperty("start");
});

// 场景：新模板自带两个独立文字对象，效果添加和移除保留基础文字。
test("新模板固定包含标题和字幕", () => {
  const empty = newDraft();
  expect(empty).not.toHaveProperty("editor");
  expect(empty.tracks.map((track) => track.target)).toEqual(["title", "subtitle"]);
  expect(draftEffects(empty)).toEqual([]);
  const preview = buildTimeline(empty, catalog);
  expect(preview.SubtitleTracks).toHaveLength(2);
  expect(preview.EffectTracks).toEqual([]);
  expect(buildPreviewRows(preview)).toHaveLength(3);
  const added = addTrack(empty, vfx, "title");
  expect(added.draft.tracks?.map((track) => track.target)).toEqual(["title", "subtitle", "vfx"]);
  const restored = JSON.parse(JSON.stringify(added.draft));
  expect(restored.tracks).toEqual(added.draft.tracks!);
  expect(draftEffects(restored)).toEqual([vfx.id]);
  expect(removeTrack(restored, added.id).tracks).toEqual(empty.tracks);
  for (const target of ["title", "subtitle"]) expect(() => removeTrack(empty, target)).toThrow("固定对象");
  expect(() => setTrackTiming(empty, "subtitle", { start_mode: "seconds", start: 1, duration: null })).toThrow("不能修改时间");
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
test("多个滤镜独立存在并按指定范围进入 SDK", () => {
  const first = addTrack(sampleDraft(), filter, "title");
  const second = addTrack(first.draft, filter, "title");
  expect(first.id).not.toBe(second.id);
  const draft = setTrackTiming(setTrackTiming(second.draft, first.id, { start_mode: "seconds", start: 1, duration: 3 }), second.id, { start_mode: "seconds", start: 3, duration: 5 });
  const timeline = buildTimeline(draft, catalog);
  expect(timeline.EffectTracks.map((track) => track.EffectTrackItems[0])).toEqual([
    { Type: "Filter", ...filter.parameters, TimelineIn: 1, TimelineOut: 4 },
    { Type: "Filter", ...filter.parameters, TimelineIn: 3, TimelineOut: 8 },
  ]);
  expect(buildPreviewRows(timeline).filter((row) => row.actions[0].effectId === "filter").map((row) => row.actions[0].start)).toEqual([1, 3]);
  expect(timeline.VideoTracks[0].VideoTrackClips[0].Effects).toEqual([{ Type: "Volume", Gain: 0 }]);
  expect(draftEffects(draft)).toEqual([filter.id]);
});

// 场景：参数编辑和删除只作用于指定实例，先前草稿和其他对象保持不变。
test("删除实例不会清除其他同类效果", () => {
  const first = addTrack(sampleDraft(), filter, "title");
  const second = addTrack(first.draft, filter, "title");
  const current = second.draft.tracks!.find((track) => track.id === first.id)!;
  const before = JSON.stringify(second.draft);
  const next = updateTrack(second.draft, first.id, removeTarget(trackDraft(second.draft, current), "filter"));
  expect(next.tracks!.map((track) => track.id)).not.toContain(first.id);
  expect(next.tracks!.find((track) => track.id === second.id)).toEqual(second.draft.tracks!.find((track) => track.id === second.id));
  expect(JSON.stringify(second.draft)).toBe(before);
});

// 场景：时间轴拒绝非法区间；短文字的动画在应用时缩短，模板参数保留。
test("时间边界和动画时长在 SDK 更新前校验", () => {
  const draft = sampleDraft();
  const motion = catalog.find((asset) => asset.id === "in/fade_in")!;
  const animated = addTrack(draft, motion, "title", "title").draft;
  for (const [start, duration] of [[NaN, 3], [3, -1], [-1, 2], [0, 0]])
    expect(() => setTrackTiming(animated, "title", { start_mode: "seconds", start, duration })).toThrow();
  expect(setTrackTiming(animated, "title", { start_mode: "seconds", start: 0, duration: 0.5 }).tracks![0].duration).toBe(0.5);
  const short = setTrackTiming(animated, "title", { start_mode: "seconds", start: 1, duration: 0.1 }).tracks![0];
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
  const timeline = buildTimeline(draft, catalog, [media, sampleMedia[1], sampleMedia[2]]);
  expect(JSON.stringify(saved)).not.toContain("media");
  expect(timeline.VideoTracks[0].VideoTrackClips[0]).toMatchObject({ Out: 10, TimelineOut: 10, MediaURL: media.url });
  expect(timeline.SubtitleTracks[0].SubtitleTrackClips[0]).toMatchObject({ TimelineIn: 12, TimelineOut: 20 });
  expect(timeline.FECanvas).toEqual({ Width: 1080, Height: 1920 });
});

// 场景：两个交界处各自选择转场，替换或移除只影响对应交界处。
test("两个转场独立选择并按各自时长生成重叠", () => {
  const transition = catalog.find((asset) => asset.category === "transition/normal")!;
  const added = addTrack(sampleDraft(), transition, "title");
  const second = addTrack(added.draft, transition, "title");
  expect(added.draft.tracks.find((track) => track.id === added.id)?.transition_boundary).toBe(1);
  expect(second.draft.tracks.find((track) => track.id === second.id)?.transition_boundary).toBe(2);
  const draft = updateTrack(second.draft, second.id, { ...trackDraft(second.draft, second.draft.tracks.at(-1)!), transition_duration_seconds: 2 });
  const timeline = buildTimeline(draft, catalog);
  expect(timeline.VideoTracks[0].VideoTrackClips.map((clip) => [clip.In, clip.Out, clip.TimelineIn, clip.TimelineOut])).toEqual([[0, 10, 0, 10], [0, 10, 9, 19], [0, 10, 17, 27]]);
  expect(timeline.VideoTracks[0].VideoTrackClips[0].Effects.at(-1)).toMatchObject({ Type: "Transition", Duration: 1 });
  expect(timeline.VideoTracks[0].VideoTrackClips[1].Effects.at(-1)).toMatchObject({ Type: "Transition", Duration: 2 });
  const replaced = addTrack(draft, transition, "title", undefined, 2);
  expect(replaced.id).toBe(second.id);
  expect(replaced.draft.tracks.filter((track) => track.target === "transition")).toHaveLength(2);
  expect(() => addTrack(draft, transition, "title", undefined, 3)).toThrow("交界处");
  expect(() => addTrack(draft, transition, "title")).toThrow("最多添加两个转场");
  expect(resolveTemplate(removeTrack(draft, added.id), previewContext(sampleMedia)).duration).toBe(28);
  expect(buildTimeline(removeTrack(draft, added.id), catalog).VideoTracks[0].VideoTrackClips[0].Effects).toEqual([{ Type: "Volume", Gain: 0 }]);
  const refilled = addTrack(removeTrack(draft, added.id), transition, "title");
  expect(refilled.draft.tracks.find((track) => track.id === refilled.id)?.transition_boundary).toBe(1);
  expect(refilled.draft.tracks.find((track) => track.id === second.id)).toEqual(draft.tracks.find((track) => track.id === second.id));
});

// 场景：重排保持三个独立来源，转场绑定交界处，字幕覆盖新总时长。
test("视频重排保留三个来源及两处转场", () => {
  const transition = catalog.find((asset) => asset.id === "transition/normal/angular")!;
  const original = sampleDraft();
  const added = addTrack(original, transition, "title");
  const draft = addTrack(added.draft, transition, "title", undefined, 2).draft;
  const order = reorderVideos([0, 1, 2], 2, 0);
  const timeline = buildTimeline(draft, catalog, undefined, order);
  const [first, second, third] = timeline.VideoTracks[0].VideoTrackClips;
  expect(order).toEqual([2, 0, 1]);
  expect([first.MediaURL, second.MediaURL, third.MediaURL]).toEqual([2, 0, 1].map((index) => sampleMedia[index].url));
  expect(new Set([first.MediaURL, second.MediaURL, third.MediaURL]).size).toBe(3);
  expect(first.Effects.at(-1)).toEqual({ Type: "Transition", Duration: 1, SubType: "angular" });
  expect(resolveTemplate(draft, previewContext(sampleMedia)).duration).toBe(28);
  expect(draft.tracks!.slice(0, 2)).toEqual(original.tracks);
  expect(timeline.SubtitleTracks.flatMap((track) => track.SubtitleTrackClips).map((clip) => clip.TimelineOut)).toEqual([28, 28]);
  expect(buildPreviewRows(timeline).at(-1)!.actions.map((clip) => clip.videoSource)).toEqual(order);
  const selected = draft.tracks!.find((track) => track.id === added.id)!;
  const edited = updateTrack(draft, added.id, { ...trackDraft(draft, selected), transition_duration_seconds: 0.9707 });
  expect(resolveTemplate(edited, previewContext(sampleMedia)).duration).toBeCloseTo(29 - 29 / 30);
  expect(() => reorderVideos([0, 0, 2], 2, 0)).toThrow("排列无效");
  const short = buildTimeline(draft, catalog, [{ ...sampleMedia[0], duration: 0.5 }, sampleMedia[1], sampleMedia[2]]);
  expect(short.VideoTracks[0].VideoTrackClips[0].Effects.at(-1)?.Duration).toBeCloseTo(7 / 30);
  expect(short.notices).toContain("镜头转场 1：转场已按相邻片段可用时间缩短");
});

// 场景：短视频截短有效对象并跳过结尾以外的对象；更换视频不修改任何模板规则。
test("更换预览视频只重新计算显示区间", () => {
  const media = [{ url: "https://example.com/short.mp4", duration: 4, width: 1080, height: 1920 }, sampleMedia[1], sampleMedia[2]] as const;
  const draft = sampleDraft();
  expect(buildTimeline(draft, catalog, media).SubtitleTracks.map((row) => row.SubtitleTrackClips[0].TimelineOut)).toEqual([24, 24]);
  const ranged = setTrackTiming(draft, "title", { start_mode: "seconds", start: 22, duration: 4 });
  const original = JSON.stringify(ranged);
  const timeline = buildTimeline(ranged, catalog, media);
  expect(timeline.SubtitleTracks[0].SubtitleTrackClips[0]).toMatchObject({ TimelineIn: 22, TimelineOut: 24 });
  expect(timeline.notices[0]).toContain("缩短");
  expect(buildTimeline(ranged, catalog, [{ ...media[0], duration: 1 }, media[1], media[2]]).SubtitleTracks).toHaveLength(1);
  expect(JSON.stringify(ranged)).toBe(original);
});

// 场景：三个自定义来源按各自长度截取，排列、转场、字幕和画布使用同一组媒体。
test("三个预览视频的地址与时长共同驱动时间轴", () => {
  const media = [
    { url: "https://example.com/one.mp4", duration: 12, width: 800, height: 600 },
    { url: "https://example.com/two.mp4", duration: 4, width: 600, height: 800 },
    { url: "https://example.com/three.mp4", duration: 6, width: 500, height: 500 },
  ] as const;
  const asset = catalog.find((item) => item.category === "transition/normal")!;
  const first = addTrack(newDraft(), asset, "title");
  const second = addTrack(first.draft, asset, "title");
  const draft = updateTrack(second.draft, second.id, { ...trackDraft(second.draft, second.draft.tracks.at(-1)!), transition_duration_seconds: 2 });
  const timeline = buildTimeline(draft, catalog, media);
  expect(timeline.VideoTracks[0].VideoTrackClips.map((clip) => [clip.MediaURL, clip.Out, clip.TimelineIn, clip.TimelineOut])).toEqual([
    [media[0].url, 10, 0, 10], [media[1].url, 4, 9, 13], [media[2].url, 6, 11, 17],
  ]);
  expect(timeline.FECanvas).toEqual({ Width: 800, Height: 600 });
  expect(timeline.SubtitleTracks.flatMap((row) => row.SubtitleTrackClips).map((clip) => clip.TimelineOut)).toEqual([17, 17]);
  const reordered = resolveTemplate(draft, previewContext(media, [2, 0, 1]));
  expect([reordered.tracks[first.id].start, reordered.tracks[second.id].start]).toEqual([5, 13]);
  expect(buildTimeline(draft, catalog, media, [2, 0, 1]).VideoTracks[0].VideoTrackClips.map((clip) => clip.MediaURL)).toEqual([media[2].url, media[0].url, media[1].url]);
  const short = [media[0], { ...media[1], duration: 1.5 }, media[2]] as const;
  expect(resolveTemplate(first.draft, previewContext(short)).notices).toHaveLength(1);
  expect(resolveTemplate(draft, previewContext(short)).notices).toHaveLength(2);
  expect(resolveTemplate(first.draft, previewContext(short, [0, 2, 1])).duration).toBe(16.5);
});

// 场景：右侧修改百分比与持续时间后，时间轴显示对应区间。
test("百分比规则随右侧修改进入时间轴", () => {
  const draft = setTrackTiming(sampleDraft(), "title", { start_mode: "percent", start: 25, duration: 3 });
  for (const [duration, start] of [[20, 5], [60, 15], [120, 30]])
    expect(resolveTrack(draft.tracks![0], duration)).toMatchObject({ start, end: start + 3 });
  const changed = setTrackTiming(draft, "title", { start_mode: "percent", start: 20, duration: 3 });
  expect(buildPreviewRows(buildTimeline(changed, catalog))[0].actions[0]).toMatchObject({ start: 6, end: 9, movable: false, flexible: false });
});

// 场景：添加顺序不影响图层，VFX 重复选择只修改同一对象。
test("图层顺序固定且 VFX 唯一", () => {
  const first = addTrack(sampleDraft(), filter, "title");
  const added = addTrack(first.draft, vfx, "title");
  const replacement = catalog.find((asset) => asset.category === "vfx/normal" && asset.id !== vfx.id)!;
  const replaced = addTrack(added.draft, replacement, "title");
  expect(replaced.id).toBe(added.id);
  expect(replaced.draft.tracks.filter((track) => track.target === "vfx")).toHaveLength(1);
  const transition = catalog.find((asset) => asset.id === "transition/normal/angular")!;
  const rows = buildPreviewRows(buildTimeline(addTrack(replaced.draft, transition, "title").draft, catalog));
  expect(rows.map((row) => row.actions[0].effectId)).toEqual(["title", "subtitle", "transition", "vfx", "filter", "video"]);
  expect(rows.slice(0, -1).every((row) => !row.actions[0].movable && !row.actions[0].flexible)).toBe(true);
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

// 场景：Remotion 片段与 IMS 对象共用 100 条上限，片段占满后不能再添加对象。
test("片段与对象共用轨道上限", () => {
  const draft = sampleDraft();
  draft.clips = Array.from({ length: 98 }, (_, index) => ({ id: `clip-${index}`, sprite_id: "sprite", start: 0, duration: null, parameters: {}, transform: {} }));
  expect(() => addTrack(draft, vfx, "title")).toThrow("轨道数量不能超过 100");
  draft.clips.pop();
  expect(addTrack(draft, vfx, "title").draft.tracks).toHaveLength(3);
});

// 场景：反复选择花字只修改固定文字对象，其他文字保留。
test("文字样式替换保留唯一对象", () => {
  const draft = sampleDraft();
  const asset = catalog.find((item) => item.category === "flower")!;
  const added = addTrack(addTrack(draft, asset, "title").draft, asset, "title");
  expect(added.id).toBe("title");
  expect(added.draft.tracks).toHaveLength(2);
  expect(added.draft.tracks!.find((track) => track.id === "subtitle")).toEqual(draft.tracks[1]);
  expect(added.draft.tracks!.find((track) => track.id === added.id)?.editor.title).toBe(defaultEditor.title);
  expect(added.draft.tracks!.find((track) => track.id === added.id)?.editor.titleFlower).toBe(asset.id);
});
