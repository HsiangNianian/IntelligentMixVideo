/** 字体的草稿、预览和 Protobuf 测试；使用真实转换函数，执行 bun run test。 */
import { expect, test } from "bun:test";
import { create, fromBinary, toBinary } from "@bufbuild/protobuf";
import { EffectTemplateEditorSchema } from "@/generated/imv/template/v1/template_pb";
import { templateFonts } from "@/features/templates/fonts";
import { newDraft } from "@/features/templates/model";
import { resetTextTarget } from "@/features/templates/effects";
import { addTrack, trackDraft, updateTrack, validateTemplateTracks } from "@/features/templates/tracks";
import { readCatalog } from "@/features/templates/sdk";
import { buildTestTimeline } from "./fixtures";

// 场景：十一种字体分别应用到标题和字幕，预览标识转换后保留原始 IMS 保存标识。
test.each(templateFonts)("$label 的预览和保存标识", (font) => {
  let draft = newDraft();
  const title = draft.tracks[0];
  draft = updateTrack(draft, title.id, {
    ...trackDraft(draft, title), editor: { ...title.editor, titleFont: font.value },
  });
  const subtitle = draft.tracks[1];
  draft = updateTrack(draft, subtitle.id, {
    ...trackDraft(draft, subtitle), editor: { ...subtitle.editor, subtitleFont: "Source Han Serif CN" },
  });
  const before = structuredClone(draft);
  const clips = buildTestTimeline(draft, []).SubtitleTracks.map((track) => track.SubtitleTrackClips[0]);
  expect(clips.map((clip) => clip.Font)).toEqual([font.preview, "思源宋体"]);
  const encoded = toBinary(EffectTemplateEditorSchema, create(EffectTemplateEditorSchema, draft.tracks[0].editor));
  expect(fromBinary(EffectTemplateEditorSchema, encoded).titleFont).toBe(font.value);
  expect(draft).toEqual(before);
  const flower = readCatalog().find((item) => item.category === "flower")!;
  const styled = addTrack(draft, flower, "title").draft;
  expect(styled.tracks[0].editor.titleFont).toBe(font.value);
  const reset = updateTrack(draft, title.id, resetTextTarget(trackDraft(draft, draft.tracks[0]), "title"));
  expect(reset.tracks[0].editor.titleFont).toBe("Alibaba PuHuiTi");
  expect(reset.tracks[1].editor.subtitleFont).toBe("Source Han Serif CN");
});

// 场景：未声明字体的旧协议保持字段缺省，默认草稿仍使用普惠体。
test("旧协议和默认字体兼容", () => {
  const editor = fromBinary(EffectTemplateEditorSchema, new Uint8Array());
  expect(editor.titleFont).toBeUndefined();
  expect(editor.subtitleFont).toBeUndefined();
  const clips = buildTestTimeline(newDraft(), []).SubtitleTracks.map((track) => track.SubtitleTrackClips[0]);
  expect(clips.map((clip) => clip.Font)).toEqual(["Alibaba PuHuiTi", "Alibaba PuHuiTi"]);
});

// 场景：未知字体、空值和预览专用标识不能进入模板保存或预览。
test.each(["", "Arial", "思源宋体", "Source Han Sans CN Normal"])("拒绝不支持的模板字体 %s", (font) => {
  for (const role of ["title", "subtitle"] as const) {
    const draft = newDraft();
    draft.tracks.find((track) => track.target === role)!.editor[`${role}Font`] = font;
    expect(() => validateTemplateTracks(draft.tracks)).toThrow("字体不在支持的目录中");
    expect(() => buildTestTimeline(draft, [])).toThrow("字体不在支持的目录中");
  }
});
