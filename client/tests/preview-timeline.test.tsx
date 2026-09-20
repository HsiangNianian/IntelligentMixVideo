/** 视频轨道核心测试：真实数据转换、源时间采样和实际轨道组件键盘交互；执行 bun run test。 */
import { expect, test } from "bun:test";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { PreviewTimeline, type PreviewTimelineHandle } from "@/features/templates/PreviewTimeline";
import { buildPreviewRows, buildTimeline, thumbnailTimes } from "@/features/templates/timeline";
import { newDraft } from "@/features/templates/model";
import { readCatalog } from "@/features/templates/sdk";

// 使用真实目录与草稿生成轨道，检查转场重叠、源素材范围和只读属性。
test("普通与转场轨道保留实际时间范围", () => {
  const draft = newDraft();
  const catalog = readCatalog();
  const ordinary = buildPreviewRows(buildTimeline(draft, catalog));
  expect(ordinary).toHaveLength(1);
  expect(ordinary[0].actions[0]).toMatchObject({ start: 0, end: 10, sourceIn: 0, sourceOut: 10, movable: false, flexible: false });
  expect(ordinary[0].actions[0].transition).toBeUndefined();
  draft.editor.transition = "transition/normal/directional";
  draft.transition_duration_seconds = 2;
  const rows = buildPreviewRows(buildTimeline(draft, catalog));
  expect(rows).toHaveLength(2);
  expect(rows[0].actions[0]).toMatchObject({ start: 0, end: 7, sourceIn: 0, sourceOut: 7, transition: { start: 5, end: 7 } });
  expect(rows[1].actions[0]).toMatchObject({ start: 5, end: 10, sourceIn: 8, sourceOut: 13 });
  expect(new Set(rows.flatMap((row) => row.actions.map((action) => action.id))).size).toBe(2);
  expect(buildPreviewRows({ ...buildTimeline(draft, catalog), VideoTracks: [] })).toEqual([]);
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
  expect(positions.slice(-2)).toEqual([10, 10]);
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
