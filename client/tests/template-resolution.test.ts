/** 前后端共享输入验证规则应用，直接调用真实目录、编辑与时间计算函数。 */
import { expect, test } from "bun:test";
import { newDraft } from "@/features/templates/model";
import { addTrack, resolveTemplate, validateTemplateTracks } from "@/features/templates/tracks";
import { readCatalog } from "@/features/templates/sdk";
import cases from "../../server/tests/template_sequence_cases.json";

const asset = readCatalog().find((item) => item.category === "transition/normal")!;

// 场景：同一份数据约束预览与正式合成的时间、提示和输入不变性。
for (const item of cases) test(item.name, () => {
  if (item.context.layout !== "sequence" && item.context.layout !== "fixed") throw new Error("用例模式无效");
  let draft = newDraft();
  const ids: string[] = [];
  for (const transition of item.transitions) {
    const added = addTrack({ ...draft, transition_duration_seconds: transition.duration }, asset, "transition", undefined, transition.boundary);
    draft = added.draft; ids.push(added.id);
  }
  const original = structuredClone(draft);
  const result = resolveTemplate(draft, { ...item.context, layout: item.context.layout });
  expect(result.duration).toBe(item.duration);
  expect(result.clips.map((clip) => [clip.start, clip.end])).toEqual(item.clips);
  expect(ids.map((id) => [result.tracks[id].start, result.tracks[id].end])).toEqual(item.ranges);
  expect(ids.map((id) => result.tracks[id].notice)).toEqual(item.notices);
  expect(result.tracks.subtitle).toMatchObject({ start: 0, end: item.subtitle_end ?? item.duration });
  expect(draft).toEqual(original);
});

// 场景：无交界处与携带预览秒数的转场不能通过客户端规则校验。
test("转场只接受明确的交界处和持续时间", () => {
  const { draft, id } = addTrack(newDraft(), asset, "transition");
  const track = draft.tracks.find((item) => item.id === id)!;
  delete track.transition_boundary;
  expect(() => validateTemplateTracks(draft.tracks)).toThrow("转场列表序号");
  track.transition_boundary = 1;
  track.start = 9;
  expect(() => validateTemplateTracks(draft.tracks)).toThrow("只保存交界处");
});
