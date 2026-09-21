/** 模板纯逻辑核心测试：草稿隔离、效果去重及 SDK 时间线；在 client/ 执行 bun run test。 */
import { expect, test } from "bun:test";
import { defaultEditor, newDraft, draftEffects, toDraft } from "@/features/templates/model";
import { trackEditor } from "@/features/templates/tracks";
import { buildTimeline } from "@/features/templates/timeline";
import { catalog, sampleDraft, savedTemplate } from "./fixtures";

// 测试新建和从已保存模板转换的草稿互相独立，编辑不会修改默认值或原记录。
test("草稿不共享编辑配置，也不携带服务端只读字段", () => {
  const original = savedTemplate();
  const draft = toDraft(original);
  const fresh = sampleDraft();
  draft.tracks[0].editor.title = "新标题";
  fresh.tracks[0].editor.title = "另一个标题";
  expect(original.tracks[0].editor.title).toBe(defaultEditor.title);
  expect(sampleDraft().tracks[0].editor.title).not.toBe(fresh.tracks[0].editor.title);
  expect(newDraft().tracks).toEqual([]);
  expect(draft).not.toHaveProperty("editor");
  expect(draft).not.toHaveProperty("template_id");
  expect(draft).not.toHaveProperty("effects");
});

// 测试多个文字使用同一效果时 ID 只提交一次，空效果与普通文字不会进入列表。
test("所选效果去重并忽略非效果字段", () => {
  const draft = sampleDraft();
  expect(draftEffects(draft)).toEqual([]);
  Object.assign(draft.tracks[0].editor, { titleIn: "in/fade_in", title: "filter/m1" });
  draft.tracks[1].editor.subtitleIn = "in/fade_in";
  expect(draftEffects(draft)).toEqual(["in/fade_in"]);
});

// 回归 #35：无转场时只创建一个十秒视频素材，避免 SDK 同时上传两份视频纹理。
test("基础预览时间线使用配置的视频并过滤空字幕", () => {
  const draft = sampleDraft();
  draft.tracks.shift();
  draft.tracks[0].editor.subtitle = "字幕";
  const timeline = buildTimeline(draft, []);
  const clips = timeline.VideoTracks[0].VideoTrackClips;
  expect(
    clips.map(({ MediaURL, In, Out, TimelineIn, TimelineOut }) => [
      MediaURL,
      In,
      Out,
      TimelineIn,
      TimelineOut,
    ]),
  ).toEqual([["http://localhost:1420/sample.mp4", 0, 10, 0, 10]]);
  expect(clips[0].Effects).toEqual([{ Type: "Volume", Gain: 0 }]);
  expect(timeline.SubtitleTracks[0].SubtitleTrackClips.map((clip) => clip.Content)).toEqual(["字幕"]);
});

// 测试效果参数、动画时长和转场重叠正确传给 SDK，100% 坐标保持为相对坐标。
test("动画和转场转换为正确的 SDK 配置", () => {
  const draft = toDraft(savedTemplate());
  draft.tracks[0].editor.titleInDuration = 1;
  draft.tracks[0].editor.titleX = 100;
  draft.tracks.push({ id: "transition", target: "transition", start_mode: "seconds", start: 5, duration: 2,
    editor: trackEditor({ ...defaultEditor, transition: "transition/normal/directional" }, "transition") });
  const timeline = buildTimeline(draft, catalog);
  const [first, second] = timeline.VideoTracks[0].VideoTrackClips;
  expect([first.In, first.Out, first.TimelineIn, first.TimelineOut]).toEqual([
    0, 7, 0, 7,
  ]);
  expect([second.In, second.Out, second.TimelineIn, second.TimelineOut]).toEqual([
    7, 10, 5, 8,
  ]);
  expect(first.Effects).toContainEqual({ Type: "Transition", Duration: 2, SubType: "directional" });
  expect(timeline.SubtitleTracks[0].SubtitleTrackClips[0]).toMatchObject({
    AaiMotionInEffect: "fade_in", AaiMotionIn: 1, X: 0.9999,
  });
});

// 测试未知效果、越界字号和互斥动画被拦截，避免无效配置进入 SDK。
test("预览拒绝必要的非法配置", () => {
  const draft = sampleDraft();
  const editor = draft.tracks[0].editor;
  editor.titleSize = 0;
  expect(() => buildTimeline(draft, catalog)).toThrow("字号");
  editor.titleSize = 40;
  editor.titleIn = "in/unknown";
  expect(() => buildTimeline(draft, catalog)).toThrow("效果不在对应目录中");
  editor.titleIn = "in/fade_in";
  editor.titleLoop = "loop/normal_display";
  expect(() => buildTimeline(draft, catalog)).toThrow("循环动画不能与入场、出场同时使用");
});

// 场景：数据库返回顶层 editor 或缺少 tracks 时，读取和预览均拒绝处理。
test("模板读取与预览要求完整对象格式", () => {
  const saved = savedTemplate();
  for (const value of [{ ...saved, editor: defaultEditor }, { ...saved, tracks: null }, { ...saved, tracks: undefined }]) {
    const decoded = JSON.parse(JSON.stringify(value));
    expect(() => toDraft(decoded)).toThrow("模板格式不支持");
    expect(() => buildTimeline(decoded, catalog)).toThrow("模板格式不支持");
  }
});

// 测试断网时内置目录仍覆盖所有效果分类，静态动画具有可用的参数快照。
test("离线目录不依赖 SDK 下载", async () => {
  const { readCatalog } = await import("@/features/templates/sdk");
  const items = readCatalog();
  expect(new Set(items.map((item) => item.category))).toEqual(new Set(["flower", "bubble", "filter", "vfx/normal", "transition/normal", "in", "out", "loop"]));
  expect(items.find((item) => item.id === "in/fade_in")?.parameters).toEqual({ AaiMotionInEffect: "fade_in" });
});
