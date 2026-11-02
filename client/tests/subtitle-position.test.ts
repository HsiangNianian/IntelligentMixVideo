/** 字幕定位适配回归：验证最终 SDK 坐标、开场切段、双语和样式隔离；真实像素回归见 subtitle-position.browser.mjs。执行 bun run test。 */
import { expect, test } from "bun:test";
import { defaultEditor, type Draft } from "@/features/templates/model";
import { defaultAvatarEffects } from "@/features/templates/avatarEffects";
import { defaultSplitScreen } from "@/features/templates/splitScreen";
import { readCatalog } from "@/features/templates/sdk";
import { playerTimeline } from "@/features/templates/timeline";
import { sampleDraft, buildTestTimeline } from "./fixtures";

// 同屏坐标在开场之后才覆盖普通字幕；SDK 适配必须使用每段最终坐标，保留关键词、花字、入出场动画及英文排版设置。
test("SDK 为同屏中英字幕使用最终坐标，兼容标记不进入业务时间线或草稿", () => {
  const draft: Draft = sampleDraft();
  draft.split_screen = { ...defaultSplitScreen(), enabled: true, subtitle: { x: 25, y: 30 }, subtitleEn: { x: 75, y: 40 } };
  draft.avatar_effects = { ...defaultAvatarEffects(), openingEnabled: true };
  const chinese = draft.tracks[1];
  chinese.editor = { ...chinese.editor, subtitle: "字幕定位验证", subtitleX: 40, subtitleY: 80, subtitleFlower: "flower/CS0003-000001", subtitleKeywordColor: "#FFFF00", subtitleKeywordSize: 60, subtitleIn: "in/fade_in", subtitleOut: "out/fade_out" };
  draft.tracks.push({ ...chinese, id: "subtitle-en", language: "en", editor: { ...chinese.editor, subtitle: "Subtitle position test" } });
  const original = structuredClone(draft);
  const timeline = buildTestTimeline(draft, readCatalog());
  const business = structuredClone(timeline.SubtitleTracks);
  const subtitles = playerTimeline(timeline).VideoTracks.filter(row => "Type" in row && row.Type === "Subtitle");
  for (const [index, row] of subtitles.entries()) {
    const source = business[business.length - 1 - index].SubtitleTrackClips;
    expect(row.VideoTrackClips).toEqual(source.map(clip => ({ ...clip, Content: clip.X === 0.5 ? clip.Content : `{\\pos(${Math.round(clip.X * 1080)},${Math.round(clip.Y * 1920)})}${clip.Content}` })));
  }
  const english = subtitles[0].VideoTrackClips;
  const chineseClips = subtitles[1].VideoTrackClips;
  expect(english[1]).toMatchObject({ AdaptMode: "AutoScale", TextWidth: 0.9, Content: expect.stringContaining("{\\pos(810,768)}") });
  expect(chineseClips[0]).toMatchObject({ TimelineOut: 2, Content: expect.stringContaining("{\\pos(432,1536)}"), AaiMotionInEffect: "fade_in" });
  expect(chineseClips[0]).not.toHaveProperty("AaiMotionOutEffect");
  expect(chineseClips[1]).toMatchObject({ TimelineIn: 2, AdaptMode: "AutoWrap", TextWidth: 1, Content: expect.stringContaining("{\\pos(270,576)}"), EffectColorStyle: "CS0003-000001", AaiMotionOutEffect: "fade_out" });
  expect(chineseClips[1]).not.toHaveProperty("AaiMotionInEffect");
  expect(timeline.SubtitleTracks).toEqual(business);
  expect(draft).toEqual(original);
});

// 气泡没有自动换行，不需要兼容；普通标题和字幕共用相同 SDK 缺陷，零坐标也必须被保留。
test("普通文字的非居中坐标同样生效，居中文字和气泡沿用 SDK 原行为", () => {
  const draft = sampleDraft();
  draft.tracks[0].editor.titleX = 0;
  draft.tracks[0].editor.titleY = 0;
  draft.tracks.push({ id: "bubble", target: "bubble", start_mode: "seconds", start: 0, duration: null, editor: { ...defaultEditor, bubbleX: 25 } });
  const timeline = buildTestTimeline(draft, readCatalog());
  const clips = playerTimeline(timeline).VideoTracks.map(row => row.VideoTrackClips).flat();
  expect(clips.find(clip => "Content" in clip && clip.Content === `{\\pos(0,0)}${defaultEditor.title}`)).toBeDefined();
  expect(clips.find(clip => "Content" in clip && clip.Content === defaultEditor.subtitle)).toBeDefined();
  expect(clips.find(clip => "Content" in clip && clip.Content === defaultEditor.bubbleText)).toMatchObject({ X: 0.25, Width: 0.5 });
});
