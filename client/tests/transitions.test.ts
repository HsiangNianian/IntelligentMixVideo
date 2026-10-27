/** 转场列表、预览选择与统一时长的真实计算测试；执行 bun run test。 */
import { expect, test } from "bun:test";
import { create, fromBinary, toBinary } from "@bufbuild/protobuf";
import { SaveTemplateRequestSchema } from "@/generated/imv/template/v1/template_pb";
import { newDraft, toDraft } from "@/features/templates/model";
import { readCatalog } from "@/features/templates/sdk";
import { addTrack, previewContext, resolveTemplate, removeTrack } from "@/features/templates/tracks";
import { previewTransitions, setTransitionDuration, transitionItems } from "@/features/templates/transitions";
import { buildPreviewRows, playerTimeline } from "@/features/templates/timeline";
import { hiddenTextClips, timeClips } from "@/features/templates/clips";
import { sampleMedia, buildTestTimeline, savedTemplate } from "./fixtures";

test.each(["sequence", "random"] as const)("%s 混合转场只更新 IMS 时长", (mode) => {
  const ims = readCatalog().find((item) => item.category === "transition/normal")!;
  const original = addTrack(newDraft(), ims, "transition").draft;
  original.transition_mode = mode;
  original.clips = [{ id: "remotion", sprite_id: "sprite", transition_boundary: 2, duration: 2.4, start: 0, parameters: {}, transform: {} }];
  const changed = setTransitionDuration(original, 0.2);
  expect(changed.clips).toBe(original.clips);
  expect(transitionItems(changed).map((item) => item.duration)).toEqual([0.2, 2.4]);
  const preview = previewTransitions(changed, [0.9, 0]).draft;
  expect(preview.clips[0].duration).toBe(2.4);
  const resolved = resolveTemplate(preview, previewContext(sampleMedia));
  const interval = resolved.transitions[preview.clips[0].transition_boundary! - 1];
  expect(interval.end - interval.start).toBeCloseTo(2.4);
  expect(toDraft({ ...savedTemplate(), ...changed }).clips).toEqual(original.clips);
});

test.each([null, 6])("Remotion 原始时长 %s 在预览计算与保存之间保持独立", (duration) => {
  const draft = newDraft();
  draft.clips = [{ id: "remotion", sprite_id: "sprite", transition_boundary: 1, duration, start: 0, parameters: {}, transform: {} }];
  const preview = previewTransitions(draft, [0, 0], [{ id: "sprite", seconds: 6 }]).draft;
  expect(preview.clips.map((clip) => clip.duration)).toEqual([6, 6]);
  const resolved = resolveTemplate(preview, previewContext(sampleMedia));
  expect(timeClips(preview, resolved).clips.map((clip) => clip.duration)).toEqual([5, 5]);
  expect(timeClips(draft, resolved, "save").clips[0]).toEqual(draft.clips[0]);
  const disabled = previewTransitions({ ...draft, transition_enabled: false }, [0, 0]).draft;
  expect(timeClips(draft, resolveTemplate(disabled, previewContext(sampleMedia)), "save").clips[0]).toEqual(draft.clips[0]);
});

// 默认三个视频连续排列，增加多个不同效果后只有两个预览位置。
test("多个候选共享一行转场并保留固定视频顺序", () => {
  const catalog = readCatalog();
  const assets = catalog.filter((item) => item.category === "transition/normal").slice(0, 4);
  let draft = newDraft();
  expect(transitionItems(draft)).toHaveLength(0);
  expect(buildPreviewRows(buildTestTimeline(draft, catalog)).filter((row) => row.actions[0]?.effectId === "video")).toHaveLength(3);
  for (const asset of assets) draft = addTrack(draft, asset, "transition").draft;
  draft = setTransitionDuration(draft, 1.5);
  expect(transitionItems(draft).map((item) => item.duration)).toEqual([1.5, 1.5, 1.5, 1.5]);
  const timeline = buildTestTimeline(draft, catalog);
  const rows = buildPreviewRows(timeline);
  expect(rows.filter((row) => row.actions[0].effectId === "transition")).toHaveLength(1);
  expect(rows.find((row) => row.id === "transitions")?.actions.map((item) => item.targetId)).toEqual(transitionItems(draft).slice(0, 2).map((item) => item.id));
  expect(timeline.VideoTracks[0].VideoTrackClips.map((item) => item.source)).toEqual([0, 1, 2]);
  expect(resolveTemplate(draft, previewContext(sampleMedia)).duration).toBe(30);
  expect(resolveTemplate(draft, previewContext(sampleMedia)).notices).toEqual([]);
});

// 选择结果进入真实 SDK 数据，关闭后保留候选并恢复三个连续视频及空转场行。
test("预览从完整列表选择效果且开关不删除候选", () => {
  const catalog = readCatalog();
  let draft = newDraft();
  for (const asset of catalog.filter((item) => item.category === "transition/normal").slice(0, 4))
    draft = addTrack(draft, asset, "transition").draft;
  const original = structuredClone(draft);
  const candidates = transitionItems(draft);
  const sequential = previewTransitions(draft, [0.8, 0.6]);
  expect([...sequential.targets.values()]).toEqual(candidates.slice(0, 2).map((item) => item.id));
  const random = previewTransitions({ ...draft, transition_mode: "random" }, [0.8, 0.6]);
  expect([...random.targets.values()]).toEqual([candidates[3].id, candidates[2].id]);
  const actions = buildPreviewRows(buildTestTimeline(random.draft, catalog)).find((row) => row.id === "transitions")!.actions;
  expect(actions.map((item) => item.effectName)).toEqual([3, 2].map((index) => catalog.find((asset) => asset.id === draft.tracks[index + 2].editor.transition)!.name));
  expect(actions.map((item) => [item.start, item.end])).toEqual([[9.5, 10.5], [19.5, 20.5]]);
  const disabled = previewTransitions({ ...draft, transition_enabled: false }, [0.8, 0.6]);
  const timeline = buildTestTimeline(disabled.draft, catalog);
  expect(buildPreviewRows(timeline).find((row) => row.id === "transitions")!.actions).toEqual([]);
  expect(timeline.VideoTracks[0].VideoTrackClips.map((clip) => clip.TimelineIn)).toEqual([0, 10, 20]);
  expect(previewTransitions(draft, [0.8, 0.6])).toEqual(sequential);
  expect(draft).toEqual(original);
});

// 每次从完整候选池抽取；重复效果保留独立实例 ID，并指向同一个可编辑候选。
test.each([0, 0.5, 0.999])("随机输入 %s 允许重复选择转场", (value) => {
  const catalog = readCatalog();
  let draft = newDraft();
  for (const asset of catalog.filter((item) => item.category === "transition/normal").slice(0, 2))
    draft = addTrack(draft, asset, "transition").draft;
  const preview = previewTransitions({ ...draft, transition_mode: "random" }, [value, value]);
  const ids = transitionItems(draft).map((item) => item.id);
  const selected = ids[Math.floor(value * ids.length)];
  expect([...preview.targets.values()]).toEqual([selected, selected]);
  expect(new Set(transitionItems(preview.draft).map((item) => item.id)).size).toBe(2);
  const actions = buildPreviewRows(buildTestTimeline(preview.draft, catalog)).find((row) => row.id === "transitions")!.actions;
  expect(actions.map((action) => preview.targets.get(action.targetId!))).toEqual([selected, selected]);
});

// 只有一个候选时，两种选择方式均用于两个连接位置，两个预览片段指向同一个可编辑对象。
test.each(["sequence", "random"] as const)("%s 单个转场应用于两个预览位置", (mode) => {
  const catalog = readCatalog();
  const one = addTrack(newDraft(), catalog.find((item) => item.category === "transition/normal")!, "transition").draft;
  const preview = previewTransitions({ ...one, transition_mode: mode }, [0.2, 0.8]);
  expect(transitionItems(preview.draft)).toHaveLength(2);
  expect(new Set(transitionItems(preview.draft).map((item) => item.id)).size).toBe(2);
  expect([...preview.targets.values()]).toEqual([transitionItems(one)[0].id, transitionItems(one)[0].id]);
  const timeline = buildTestTimeline(preview.draft, catalog);
  const actions = buildPreviewRows(timeline).find((row) => row.id === "transitions")!.actions;
  expect(actions.map((action) => [action.start, action.end])).toEqual([[9.5, 10.5], [19.5, 20.5]]);
  expect(timeline.VideoTracks[0].VideoTrackClips.map((clip) => clip.TimelineIn)).toEqual([0, 10, 20]);
  expect(resolveTemplate(preview.draft, previewContext(sampleMedia)).duration).toBe(30);
});

// 统一时长拒绝非有限值和范围外的输入。
test("转场时长校验非法输入", () => {
  const draft = newDraft();
  expect(() => setTransitionDuration(draft, NaN)).toThrow();
  expect(() => setTransitionDuration(draft, 0)).toThrow();
});

// 新增转场与现有转场共享默认一秒或用户设置的时长。
test("新增转场使用默认或已设置的统一时长", () => {
  const asset = readCatalog().find((item) => item.category === "transition/normal")!;
  const initial = addTrack(newDraft(), asset, "transition").draft;
  expect(initial.transition_duration_seconds).toBe(1);
  expect(transitionItems(initial).map((item) => item.duration)).toEqual([1]);
  expect(resolveTemplate(initial, previewContext(sampleMedia)).duration).toBe(30);
  const configured = setTransitionDuration(newDraft(), 2);
  const added = addTrack(configured, asset, "transition").draft;
  const both = addTrack(added, asset, "transition").draft;
  expect(both.transition_duration_seconds).toBe(2);
  expect(transitionItems(both).map((item) => item.duration)).toEqual([2, 2]);
  expect(resolveTemplate(both, previewContext(sampleMedia)).duration).toBe(30);
});

// 新增选择方式通过真实 Protobuf 编解码保留，列表可以超过两个。
test("转场设置方式与多个列表序号通过协议往返", () => {
  const message = create(SaveTemplateRequestSchema, { name: "转场设置", transitionMode: "random", transitionEnabled: false, transitionDurationSeconds: 1.5,
    tracks: { tracks: [1, 2, 3].map((transitionBoundary) => ({ id: `transition-${transitionBoundary}`, target: "transition", transitionBoundary, duration: 1.5 })) } });
  const restored = fromBinary(SaveTemplateRequestSchema, toBinary(SaveTemplateRequestSchema, message));
  expect(restored.transitionMode).toBe("random");
  expect(restored.transitionEnabled).toBe(false);
  expect(restored.tracks?.tracks.map((item) => item.transitionBoundary)).toEqual([1, 2, 3]);
});

// 已有模板仅保存第二个位置时，新增候选补充第一个位置，删除其他对象保留候选序号。
test("第二个连接位置先创建时仍可补充第一个转场", () => {
  const catalog = readCatalog();
  const asset = catalog.find((item) => item.category === "transition/normal")!;
  const second = addTrack(newDraft(), asset, "transition", undefined, 2).draft;
  const bubble = addTrack(second, catalog.find((item) => item.category === "bubble")!, "bubble");
  expect(transitionItems(removeTrack(bubble.draft, bubble.id))[0].transition_boundary).toBe(2);
  const both = addTrack(second, asset, "transition").draft;
  expect(transitionItems(both).map((item) => item.transition_boundary)).toEqual([1, 2]);
  expect(buildPreviewRows(buildTestTimeline(both, catalog)).find((row) => row.id === "transitions")?.actions).toHaveLength(2);
});

// 居中区间按帧计算，修改时长不会移动视频、字幕或百分比起点。
test.each([0.1, 0.9, 1, 1.5, 3])("居中转场 %s 秒保持视频与文字时间", (duration) => {
  let draft = addTrack(newDraft(), readCatalog().find((item) => item.category === "transition/normal")!, "transition").draft;
  draft = setTransitionDuration(draft, duration);
  draft.tracks[0] = { ...draft.tracks[0], start_mode: "percent", start: 50 };
  const preview = previewTransitions(draft, [0, 0]).draft;
  const result = resolveTemplate(preview, previewContext(sampleMedia));
  expect(result.duration).toBe(30);
  expect(result.clips).toEqual([{ start: 0, end: 10 }, { start: 10, end: 20 }, { start: 20, end: 30 }]);
  expect(result.tracks.title.start).toBe(15);
  expect(result.tracks.subtitle.end).toBe(30);
  for (const [index, transition] of result.transitions.entries()) {
    const boundary = (index + 1) * 300;
    const frames = Math.round(duration * 30);
    expect(transition.start * 30).toBeCloseTo(boundary - Math.floor(frames / 2));
    expect(transition.end * 30).toBeCloseTo(boundary + Math.ceil(frames / 2));
  }
});

// Remotion 叠加转场与 IMS 使用同一组居中区间，短视频不足时不显示转场。
test("Remotion 居中转场保留显示状态并使用可用时长", () => {
  const draft = newDraft();
  draft.clips = [{ id: "transition", sprite_id: "sprite", transition_boundary: 1, duration: 1, start: 0, parameters: {}, transform: {} }];
  const result = resolveTemplate(draft, previewContext(sampleMedia));
  expect(timeClips(draft, result).clips[0]).toMatchObject({ start: 9.5, duration: 1 });
  expect([...hiddenTextClips(draft, result)]).toEqual([]);
  const short = resolveTemplate(draft, previewContext([{ ...sampleMedia[0], duration: 0.1 }, sampleMedia[1], sampleMedia[2]]));
  expect([...hiddenTextClips(draft, short)]).toEqual(["transition"]);
  expect(short.duration).toBeCloseTo(20.1);
});

// 使用真实 PNG 验证 SDK 收到两张具有完整重叠区间的图片，基础视频不携带会缩短总长的效果。
test("SDK 图片转场使用居中区间并保留文字图层", async () => {
  const image = `data:image/png;base64,${Buffer.from(await Bun.file(new URL("../src-tauri/icons/32x32.png", import.meta.url)).arrayBuffer()).toString("base64")}`;
  const catalog = readCatalog();
  const draft = addTrack(newDraft(), catalog.find((item) => item.category === "transition/normal")!, "transition").draft;
  const timeline = buildTestTimeline(previewTransitions(draft, [0, 0]).draft, catalog);
  expect(() => playerTimeline(timeline)).toThrow("缺少视频补帧画面");
  const original = structuredClone(timeline);
  const applied = playerTimeline(timeline, new Map(["1:0", "1:1", "2:0", "2:1"].map((key) => [key, image])));
  expect(applied.VideoTracks.map((row) => row.VideoTrackClips[0].Type)).toEqual(["Video", "Image", "Image", "Text", "Text"]);
  expect(applied.VideoTracks[0].VideoTrackClips.map((clip) => [clip.TimelineIn, clip.TimelineOut])).toEqual([[0, 10], [10, 20], [20, 30]]);
  expect(applied.VideoTracks.slice(1, 3).map((row) => row.VideoTrackClips.map((clip) => [clip.TimelineIn, clip.TimelineOut]))).toEqual([[[9.5, 10.5], [9.5, 10.5]], [[19.5, 20.5], [19.5, 20.5]]]);
  expect(timeline).toEqual(original);
});
