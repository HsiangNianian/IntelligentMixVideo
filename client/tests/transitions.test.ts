/** 转场列表、预览选择与统一时长的真实计算测试；执行 bun run test。 */
import { expect, test } from "bun:test";
import { create, fromBinary, toBinary } from "@bufbuild/protobuf";
import { SaveTemplateRequestSchema } from "@/generated/imv/template/v1/template_pb";
import { newDraft } from "@/features/templates/model";
import { readCatalog } from "@/features/templates/sdk";
import { addTrack, previewContext, resolveTemplate, removeTrack } from "@/features/templates/tracks";
import { previewTransitions, setTransitionDuration, transitionItems } from "@/features/templates/transitions";
import { buildPreviewRows } from "@/features/templates/timeline";
import { sampleMedia, buildTestTimeline } from "./fixtures";

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
  expect(resolveTemplate(draft, previewContext(sampleMedia)).duration).toBe(27);
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
  expect([...random.targets.values()]).toEqual([candidates[3].id, candidates[1].id]);
  const actions = buildPreviewRows(buildTestTimeline(random.draft, catalog)).find((row) => row.id === "transitions")!.actions;
  expect(actions.map((item) => item.effectName)).toEqual([3, 1].map((index) => catalog.find((asset) => asset.id === draft.tracks[index + 2].editor.transition)!.name));
  expect(actions.map((item) => [item.start, item.end])).toEqual([[9, 10], [18, 19]]);
  const disabled = previewTransitions({ ...draft, transition_enabled: false }, [0.8, 0.6]);
  const timeline = buildTestTimeline(disabled.draft, catalog);
  expect(buildPreviewRows(timeline).find((row) => row.id === "transitions")!.actions).toEqual([]);
  expect(timeline.VideoTracks[0].VideoTrackClips.map((clip) => clip.TimelineIn)).toEqual([0, 10, 20]);
  expect(previewTransitions(draft, [0.8, 0.6])).toEqual(sequential);
  expect(draft).toEqual(original);
});

// 两次相同随机输入也只能选中不同候选，预览片段保留各自对象 ID。
test.each([0, 0.5, 0.999])("随机输入 %s 不重复选择转场", (value) => {
  const catalog = readCatalog();
  let draft = newDraft();
  for (const asset of catalog.filter((item) => item.category === "transition/normal").slice(0, 2))
    draft = addTrack(draft, asset, "transition").draft;
  const preview = previewTransitions({ ...draft, transition_mode: "random" }, [value, value]);
  const ids = transitionItems(draft).map((item) => item.id);
  expect([...preview.targets.values()].sort()).toEqual([...ids].sort());
  const actions = buildPreviewRows(buildTestTimeline(preview.draft, catalog)).find((row) => row.id === "transitions")!.actions;
  expect(actions.map((action) => action.targetId).sort()).toEqual([...ids].sort());
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
  expect(actions.map((action) => [action.start, action.end])).toEqual([[9, 10], [18, 19]]);
  expect(timeline.VideoTracks[0].VideoTrackClips.map((clip) => clip.TimelineIn)).toEqual([0, 9, 18]);
  expect(resolveTemplate(preview.draft, previewContext(sampleMedia)).duration).toBe(28);
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
  expect(resolveTemplate(initial, previewContext(sampleMedia)).duration).toBe(29);
  const configured = setTransitionDuration(newDraft(), 2);
  const added = addTrack(configured, asset, "transition").draft;
  const both = addTrack(added, asset, "transition").draft;
  expect(both.transition_duration_seconds).toBe(2);
  expect(transitionItems(both).map((item) => item.duration)).toEqual([2, 2]);
  expect(resolveTemplate(both, previewContext(sampleMedia)).duration).toBe(26);
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
