/** 视频轨道核心测试：真实数据转换、源时间采样和实际轨道组件键盘交互；执行 bun run test。 */
import { expect, test } from "bun:test";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { PreviewTimeline, type PreviewTimelineHandle } from "@/features/templates/PreviewTimeline";
import { buildPreviewRows, mergeSpriteRows, thumbnailTimes } from "@/features/templates/timeline";
import { defaultEditor, newDraft } from "@/features/templates/model";
import { addTrack, removeTrack, trackEditor } from "@/features/templates/tracks";
import { readCatalog } from "@/features/templates/sdk";
import { sampleDraft, buildTestTimeline as buildTimeline } from "./fixtures";

// 使用真实目录与草稿生成轨道，检查三个来源、转场重叠和拖动权限。
test("普通与转场轨道保留实际时间范围", () => {
  const draft = newDraft();
  const catalog = readCatalog();
  const ordinary = buildPreviewRows(buildTimeline(draft, catalog));
  expect(ordinary).toHaveLength(6);
  expect(ordinary[2]).toMatchObject({ id: "transitions", actions: [] });
  expect(ordinary.slice(3).map((row) => row.actions.length)).toEqual([1, 1, 1]);
  expect(ordinary[3].actions[0]).toMatchObject({ start: 0, end: 10, sourceIn: 0, sourceOut: 10, movable: false, flexible: false });
  draft.tracks.push({ id: "transition", target: "transition", transition_boundary: 1, duration: 2,
    editor: trackEditor({ ...defaultEditor, transition: "transition/normal/directional" }, "transition") });
  const rows = buildPreviewRows(buildTimeline(draft, catalog));
  expect(rows).toHaveLength(6);
  expect(rows[2].actions[0]).toMatchObject({ start: 9, end: 11 });
  expect(rows[3].actions[0]).toMatchObject({ start: 0, end: 10, sourceIn: 0, sourceOut: 10 });
  expect(rows[4].actions[0]).toMatchObject({ start: 10, end: 20, sourceIn: 0, sourceOut: 10 });
  expect(rows[5].actions[0]).toMatchObject({ start: 20, end: 30, sourceIn: 0, sourceOut: 10 });
  expect(new Set(rows.flatMap((row) => row.actions.map((action) => action.id))).size).toBe(6);
  expect(buildPreviewRows({ ...buildTimeline(draft, catalog), VideoTracks: [] })).toEqual(rows.slice(0, 3));
});

// 添加与移除转场时三个来源始终各占独立轨道，片段禁止移动与调整长度。
test("视频始终按来源顺序各占一条固定轨道", () => {
  const catalog = readCatalog();
  const asset = catalog.find((item) => item.category === "transition/normal")!;
  for (const boundaries of [[], [1], [2], [1, 2]]) {
    let draft = newDraft();
    const ids: string[] = [];
    for (const boundary of boundaries) {
      const added = addTrack(draft, asset, "transition", undefined, boundary);
      draft = added.draft;
      ids.push(added.id);
    }
    const timeline = buildTimeline(draft, catalog);
    const rows = buildPreviewRows(timeline).filter((row) => row.actions[0]?.effectId === "video");
    expect(rows.map((row) => row.actions.map((clip) => clip.videoSource))).toEqual([[0], [1], [2]]);
    expect(rows.every((row) => !row.actions[0].movable && !row.actions[0].flexible)).toBe(true);
    expect(rows.map((row) => [row.actions[0].start, row.actions[0].end]))
      .toEqual(timeline.VideoTracks[0].VideoTrackClips.map((clip) => [clip.TimelineIn, clip.TimelineOut]));
    expect(mergeSpriteRows(buildPreviewRows(timeline), []).filter((row) => row.actions[0]?.effectId === "video")).toEqual(rows);
    for (const id of ids) draft = removeTrack(draft, id);
    const restored = buildPreviewRows(buildTimeline(draft, catalog)).filter((row) => row.actions[0]?.effectId === "video");
    expect(restored.map((row) => row.actions.map((clip) => clip.videoSource))).toEqual([[0], [1], [2]]);
  }
});

// 缩略图按源素材位置采样，并拒绝空区间、负时间及非有限值。
test("缩略图使用源时间且避开结束边界", () => {
  expect(thumbnailTimes({ sourceIn: 8, sourceOut: 13 })).toEqual([8.5, 9.5, 10.5, 11.5, 12.5]);
  expect(thumbnailTimes({ sourceIn: 0, sourceOut: 0.1 })).toEqual([0.05]);
  for (const [sourceIn, sourceOut] of [[0, 0], [2, 1], [-1, 2], [0, Infinity], [NaN, 1]])
    expect(() => thumbnailTimes({ sourceIn, sourceOut })).toThrow("源时间范围");
});

// 使用组件的真实 Timeline 引用，验证 SDK 时间同步不触发定位，键盘定位与禁用状态有效。
test("轨道接收播放时间，键盘定位限制范围并遵守禁用状态", async () => {
  const ref = createRef<PreviewTimelineHandle>();
  const positions: number[] = [];
  const onSeek = (time: number) => positions.push(time);
  const view = render(<PreviewTimeline ref={ref} rows={[]} disabled={false} time={0} onSeek={onSeek} />);
  const slider = screen.getByRole("slider", { name: "预览播放位置" });
  await act(async () => ref.current!.setTime(4));
  expect(positions).toEqual([]);
  fireEvent.keyDown(slider, { key: "ArrowRight" });
  expect(positions.at(-1)).toBeCloseTo(4.1);
  fireEvent.keyDown(slider, { key: "End" });
  fireEvent.keyDown(slider, { key: "ArrowRight" });
  expect(positions.slice(-2)).toEqual([30, 30]);
  fireEvent.keyDown(slider, { key: "Home" });
  fireEvent.keyDown(slider, { key: "ArrowLeft" });
  expect(positions.slice(-2)).toEqual([0, 0]);
  view.rerender(<PreviewTimeline ref={ref} rows={[]} disabled time={0} onSeek={onSeek} />);
  const count = positions.length;
  fireEvent.keyDown(slider, { key: "ArrowRight" });
  expect(positions).toHaveLength(count);
  expect(slider.getAttribute("aria-disabled")).toBe("true");
  view.unmount();
  expect(ref.current).toBeNull();
});

// 视频、标题与字幕轨道各自显示名称和类别图标。
test("时间轴显示轨道图标与名称", () => {
  const rows = buildPreviewRows(buildTimeline(sampleDraft(), readCatalog()));
  const view = render(<PreviewTimeline ref={createRef<PreviewTimelineHandle>()} rows={rows} disabled time={0} onSeek={() => {}} />);
  const labels = screen.getByLabelText("时间轴轨道标签").querySelectorAll(".template-timeline-label");
  expect([...labels].map((label) => label.textContent)).toEqual(["顶部标题", "底部字幕", "镜头转场", "视频", "视频", "视频"]);
  expect([...labels].every((label) => label.querySelector("svg"))).toBe(true);
  expect(new Set([...labels].map((label) => label.querySelector("svg")?.getAttribute("class"))).size).toBe(4);
  view.unmount();
});

// 改由 Remotion 字效显示的标题不交给 SDK 渲染，但保留时间轴行供选择和设置时间。
test("Remotion 字效替换的文字不进入 SDK 时间线", () => {
  const draft = newDraft();
  const before = buildTimeline(draft, readCatalog());
  const timeline = buildTimeline({ ...draft, clips: [{ id: "c", sprite_id: "s", start: 0, duration: 3, parameters: {}, transform: {}, text_target: "title" }] }, readCatalog());
  expect(timeline.SubtitleTracks).toEqual(before.SubtitleTracks.slice(1));
  expect(buildPreviewRows(timeline).map((row) => row.actions[0]?.effectId ?? row.id)).toEqual(["title", "subtitle", "transitions", "video", "video", "video"]);
});

// AI 资产并入 IMS 对象的行：字效标在所作用的标题行上，转场、画面特效和滤镜按图层插入，不再每个资产独占一行；读回的转场尚未选择交界处时只显示对象名称。
test("AI 资产按所占对象并入时间轴行", () => {
  const clip = { spriteId: "s1", aspect: 9 / 16, start: 1, end: 4, length: 3 };
  const rows = mergeSpriteRows(buildPreviewRows(buildTimeline(newDraft(), readCatalog())), [
    { ...clip, id: "text", name: "霓虹标题", slot: "title" },
    { ...clip, id: "filter", name: "暖色", slot: "filter" },
    { ...clip, id: "cut", name: "闪白", slot: "transition", boundary: 2 },
    { ...clip, id: "glow", name: "光斑", slot: "vfx" },
    { ...clip, id: "legacy", name: "旧字效" },
    { ...clip, id: "unbound", name: "旧转场", slot: "transition" },
  ]);
  expect(rows.map((row) => row.actions[0].effectId)).toEqual(["title", "subtitle", "transition", "vfx", "filter", "remotion", "video", "video", "video"]);
  expect(rows[2].actions[1]).toMatchObject({ id: "unbound", label: "镜头转场" });
  expect(rows[0].actions[0]).toMatchObject({ effectId: "title", effectName: "Remotion · 霓虹标题" });
  expect(rows[2].actions[0]).toMatchObject({ id: "cut", label: "镜头转场 2", effectName: "Remotion · 闪白", start: 1, end: 4, movable: false, targetId: "cut" });
  render(<PreviewTimeline ref={createRef()} rows={rows} disabled={false} time={0} onSeek={() => {}} />);
  expect([...screen.getByLabelText("时间轴轨道标签").querySelectorAll("[data-object]")].map((label) => label.textContent))
    .toEqual(["顶部标题", "底部字幕", "镜头转场", "画面特效", "视频滤镜", "AI 资产", "视频", "视频", "视频"]);
});

// 场景：全部转场共用一行，滤镜按时间重叠情况分行；标题行使用字效 ID，点击时间轴可以选中字效。
test("Remotion 片段与同类 IMS 对象共用一行，字效接管的文字行选中字效", () => {
  const draft = newDraft();
  draft.tracks.push({ id: "cut", target: "transition", transition_boundary: 1, duration: 2,
    editor: trackEditor({ ...defaultEditor, transition: "transition/normal/directional" }, "transition") });
  const clip = { spriteId: "s1", aspect: 9 / 16, length: 2 };
  const rows = mergeSpriteRows(buildPreviewRows(buildTimeline(draft, readCatalog())), [
    { ...clip, id: "remotion-cut", name: "闪白", slot: "transition", boundary: 2, start: 18, end: 20 },
    { ...clip, id: "overlap", name: "叠加转场", slot: "transition", boundary: 1, start: 9.5, end: 10.5 },
    { ...clip, id: "text", name: "霓虹标题", slot: "title", start: 0, end: 5 },
    { ...clip, id: "filter-first", name: "暖色", slot: "filter", start: 0, end: 5 },
    { ...clip, id: "filter-next", name: "冷色", slot: "filter", start: 5, end: 10 },
    { ...clip, id: "filter-overlap", name: "柔光", slot: "filter", start: 2, end: 4 },
  ]);
  const transitionRows = rows.filter((row) => row.actions[0].effectId === "transition");
  expect(transitionRows.map((row) => row.actions.map((action) => action.id))).toEqual([["cut", "overlap", "remotion-cut"]]);
  expect(rows.filter((row) => row.actions[0].effectId === "filter").map((row) => row.actions.map((action) => action.id)))
    .toEqual([["filter-first", "filter-next"], ["filter-overlap"]]);
  expect(rows.find((row) => row.actions[0].effectId === "title")!.actions[0]).toMatchObject({ id: "text", targetId: "text", effectName: "Remotion · 霓虹标题" });
});
