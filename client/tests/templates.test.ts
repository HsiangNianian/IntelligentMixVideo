/** 模板纯逻辑核心测试：草稿隔离、效果去重及 SDK 时间线；在 client/ 执行 bun run test。 */
import { expect, test } from "bun:test";
import { newDraft, selectedEffects, toDraft } from "@/features/templates/model";
import { buildTimeline } from "@/features/templates/timeline";
import { catalog, savedTemplate } from "./fixtures";

// 测试新建和从已保存模板转换的草稿互相独立，编辑不会修改默认值或原记录。
test("草稿不共享编辑配置，也不携带服务端只读字段", () => {
  const original = savedTemplate();
  const draft = toDraft(original);
  const fresh = newDraft();
  draft.editor.title = "新标题";
  fresh.editor.title = "另一个标题";
  expect(original.editor.title).toBe(newDraft().editor.title);
  expect(newDraft().editor.title).not.toBe(fresh.editor.title);
  expect(draft).not.toHaveProperty("template_id");
  expect(draft).not.toHaveProperty("effects");
});

// 测试多个文字使用同一效果时 ID 只提交一次，空效果与普通文字不会进入列表。
test("所选效果去重并忽略非效果字段", () => {
  const draft = newDraft();
  expect(selectedEffects(draft.editor)).toEqual([]);
  Object.assign(draft.editor, { titleIn: "in/fade_in", subtitleIn: "in/fade_in", title: "filter/m1" });
  expect(selectedEffects(draft.editor)).toEqual(["in/fade_in"]);
});

// 回归 #35：无转场时只创建一个十秒视频素材，避免 SDK 同时上传两份视频纹理。
test("基础预览时间线使用配置的视频并过滤空字幕", () => {
  const draft = newDraft();
  draft.editor.title = "";
  draft.editor.subtitle = "字幕";
  const timeline = buildTimeline(draft, []);
  const clips = timeline.VideoTracks[0].VideoTrackClips;
  expect(clips.map(({ MediaURL, TimelineIn, TimelineOut }) => [MediaURL, TimelineIn, TimelineOut]))
    .toEqual([["http://localhost:1420/sample.mp4", 0, 10]]);
  expect(clips[0].Effects).toEqual([{ Type: "Volume", Gain: 0 }]);
  expect(timeline.SubtitleTracks[0].SubtitleTrackClips.map((clip) => clip.Content)).toEqual(["字幕"]);
});

// 测试效果参数、动画时长和转场重叠正确传给 SDK，100% 坐标保持为相对坐标。
test("动画和转场转换为正确的 SDK 配置", () => {
  const draft = toDraft(savedTemplate());
  draft.editor.titleInDuration = 1;
  draft.editor.titleX = 100;
  draft.editor.transition = "transition/normal/directional";
  draft.transition_duration_seconds = 2;
  const timeline = buildTimeline(draft, catalog);
  const [first, second] = timeline.VideoTracks[0].VideoTrackClips;
  expect(first.TimelineOut - second.TimelineIn).toBe(2);
  expect(first.Effects).toContainEqual({ Type: "Transition", Duration: 2, SubType: "directional" });
  expect(timeline.SubtitleTracks[0].SubtitleTrackClips[0]).toMatchObject({
    AaiMotionInEffect: "fade_in", AaiMotionIn: 1, X: 0.9999,
  });
});

// 测试未知效果、越界字号和互斥动画被拦截，避免无效配置进入 SDK。
test("预览拒绝必要的非法配置", () => {
  const draft = newDraft();
  draft.editor.titleSize = 0;
  expect(() => buildTimeline(draft, catalog)).toThrow("字号");
  draft.editor.titleSize = 40;
  draft.editor.titleIn = "in/unknown";
  expect(() => buildTimeline(draft, catalog)).toThrow("效果不在对应目录中");
  draft.editor.titleIn = "in/fade_in";
  draft.editor.titleLoop = "loop/normal_display";
  expect(() => buildTimeline(draft, catalog)).toThrow("循环动画不能与入场、出场同时使用");
});

// 测试断网时内置目录仍覆盖所有效果分类，静态动画具有可用的参数快照。
test("离线目录不依赖 SDK 下载", async () => {
  const { readCatalog } = await import("@/features/templates/sdk");
  const items = readCatalog();
  expect(new Set(items.map((item) => item.category))).toEqual(new Set(["flower", "bubble", "filter", "vfx/normal", "transition/normal", "in", "out", "loop"]));
  expect(items.find((item) => item.id === "in/fade_in")?.parameters).toEqual({ AaiMotionInEffect: "fade_in" });
});
