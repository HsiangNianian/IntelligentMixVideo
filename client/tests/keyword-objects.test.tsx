/** 关键词对象核心回归：开关与时间跟随、独立样式预览与叠放、花字应用、Remotion 关键词字效、协议往返和实际工作区；执行 bun run test。 */
import { expect, test } from "bun:test";
import { create, fromBinary, toBinary } from "@bufbuild/protobuf";
import { fireEvent, render, screen } from "@testing-library/react";
import { SaveTemplateRequestSchema, SaveTemplateResponseSchema } from "@/generated/imv/template/v1/template_pb";
import { SpriteKind } from "@/generated/imv/sprite/v1/sprite_pb";
import type { SpriteAsset } from "@/features/sprites/model";
import { activeDraft, newDraft, toDraft } from "@/features/templates/model";
import { addTrack, setKeywordObject, setTrackTiming, trackLabel, validateTemplateTracks } from "@/features/templates/tracks";
import { addClip, bindClip } from "@/features/templates/clips";
import { exampleKeyword } from "@/features/templates/timeline";
import { saveTemplate } from "@/features/templates/api";
import { TemplateWorkspace } from "@/features/templates/TemplateWorkspace";
import { readCatalog } from "@/features/templates/sdk";
import { buildTestTimeline, protobufTemplateResponse, savedTemplate } from "./fixtures";
import { fetchMock } from "./setup";

const asset: SpriteAsset = { id: "text-asset", name: "字效", kind: SpriteKind.TEXT, seconds: 3, aspect: 9 / 16,
  width: 1080, height: 1920, fps: 30, box: { x: 100, y: 100, width: 800, height: 100 }, parameters: [] };

// 场景：首次开启按字幕建立关键词对象（字号放大、位于字幕上方、没有示例文字）；关闭只隐藏并保留外观与字效，再开启恢复；标题改时间时关键词同步。
test("关键词对象开关保留设置，时间跟随所属文字", () => {
  const original = newDraft();
  expect(setKeywordObject(original, "subtitle", false)).toBe(original);
  let draft = setKeywordObject(original, "subtitle", true);
  const keyword = draft.tracks.at(-1)!;
  expect(keyword).toMatchObject({ target: "subtitle_keyword", start_mode: "seconds", start: 0, duration: null });
  expect(keyword.editor).toMatchObject({ title: "", subtitle: "", subtitleSize: 42, subtitleY: 72 });
  expect(trackLabel(keyword, draft.tracks)).toBe("字幕关键词");
  draft = addClip(draft, asset, [asset], "subtitle_keyword");
  const off = setKeywordObject(draft, "subtitle", false);
  expect(activeDraft(off).tracks.some((track) => track.id === keyword.id)).toBe(false);
  expect(activeDraft(off).clips).toEqual([]);
  expect(off.clips).toEqual(draft.clips);
  expect(setKeywordObject(off, "subtitle", true).tracks.find((track) => track.id === keyword.id)?.editor).toEqual(keyword.editor);

  const titled = setTrackTiming(setKeywordObject(newDraft(), "title", true), "title", { start_mode: "percent", start: 20, duration: 3 });
  const titleKeyword = titled.tracks.at(-1)!;
  expect(titleKeyword).toMatchObject({ target: "title_keyword", start_mode: "percent", start: 20, duration: 3 });
  expect(() => setTrackTiming(titled, titleKeyword.id, { start_mode: "seconds", start: 0, duration: 2 })).toThrow("跟随所属文字");
  expect(() => validateTemplateTracks([...titled.tracks, { ...titleKeyword, id: "duplicate" }])).toThrow("最多各一个");
  expect(() => validateTemplateTracks(titled.tracks.map((track) => track.id === titleKeyword.id ? { ...track, start: 0 } : track))).toThrow("关键词时间必须跟随");
});

// 场景：选中关键词对象时花字只应用到它；预览里它显示字幕示例的关键词，用自己的字号、位置与花字，叠在字幕上层；改用 Remotion 字效后不再交给 SDK。
test("关键词预览使用独立样式并叠在所属文字上层", () => {
  const catalog = readCatalog();
  const flower = catalog.find((item) => item.category === "flower" && item.id !== "flower/CS0003-000002")!;
  let draft = setKeywordObject(newDraft(), "subtitle", true);
  const keyword = draft.tracks.at(-1)!;
  draft = addTrack(draft, flower, "subtitle", keyword.id).draft;
  const subtitle = draft.tracks.find((track) => track.target === "subtitle")!;
  expect(subtitle.editor.subtitleFlower).toBe("");
  expect(draft.tracks.find((track) => track.id === keyword.id)?.editor.subtitleFlower).toBe(flower.id);
  const clips = buildTestTimeline(draft, catalog).SubtitleTracks.map((row) => row.SubtitleTrackClips[0]);
  // 时间线按自上而下的叠放顺序：标题、字幕关键词、字幕；SDK 播放时倒序叠放，关键词在字幕之上。
  expect(clips.map((clip) => clip.Content)).toEqual([draft.tracks[0].editor.title, exampleKeyword(subtitle.editor, "subtitle"), subtitle.editor.subtitle]);
  expect(clips[1]).toMatchObject({ FontSize: 42, Y: 0.72, ...flower.parameters });
  expect(clips[2]).not.toHaveProperty("EffectColorStyle");

  const replaced = buildTestTimeline(addClip(draft, asset, [asset], "subtitle_keyword"), catalog);
  expect(replaced.SubtitleTracks.map((row) => row.SubtitleTrackClips[0].Content)).toEqual([draft.tracks[0].editor.title, subtitle.editor.subtitle]);
  expect(replaced.previewRows.some((row) => row.id === keyword.id)).toBe(true);
});

// 场景：关键词每次只有一个词，多行字效不能添加或改绑到关键词对象，单行字效可以。
test("多行字效不能用于关键词对象", () => {
  const multi = { ...asset, id: "multi-line", lines: 2 };
  const draft = setKeywordObject(newDraft(), "title", true);
  expect(() => addClip(draft, multi, [multi], "title_keyword")).toThrow("多行字效");
  const bound = addClip(draft, multi, [multi], "title");
  expect(() => bindClip(bound, bound.clips[0].id, { text_target: "title_keyword" }, multi)).toThrow("多行字效");
  expect(addClip(draft, asset, [asset], "title_keyword").clips[0].text_target).toBe("title_keyword");
});

// 场景：保存发出的真实协议携带停用的关键词对象与关键词字效，读回后保持相同草稿。
test("关键词对象与关键词字效通过 Protobuf 往返", async () => {
  const original = savedTemplate();
  const enabled = setKeywordObject(toDraft(original), "subtitle", true);
  const draft = setKeywordObject(addClip(enabled, asset, [asset], "subtitle_keyword"), "subtitle", false);
  fetchMock.mockImplementation((async (_url, init) => {
    const request = fromBinary(SaveTemplateRequestSchema, new Uint8Array(init!.body as Uint8Array));
    expect(request.tracks!.tracks.find((track) => track.target === "subtitle_keyword")?.enabled).toBe(false);
    expect(request.tracks!.tracks.find((track) => track.target === "remotion")?.textTarget).toBe("subtitle_keyword");
    const response = fromBinary(SaveTemplateResponseSchema, new Uint8Array(await protobufTemplateResponse(original, "save").arrayBuffer()));
    response.template!.tracks = request.tracks;
    return new Response(toBinary(SaveTemplateResponseSchema, create(SaveTemplateResponseSchema, response)));
  }) as typeof fetch);
  const saved = await saveTemplate(draft, original.template_id);
  expect(saved.tracks).toEqual(draft.tracks);
  expect(saved.clips).toEqual(draft.clips);
});

// 场景：实际工作区在字幕旁提供开关；关键词对象只有外观页签、没有示例文字，关闭后隐藏，重新开启恢复字号。
test("工作区关键词开关建立独立对象并保留外观", async () => {
  fetchMock.mockImplementation((async (_url) => protobufTemplateResponse(savedTemplate(), "get")) as typeof fetch);
  render(<TemplateWorkspace selection={{ environment: "cloud", templateId: savedTemplate().template_id }} onHome={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "编辑底部字幕" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "关键词单独字效" }));
  fireEvent.click(screen.getAllByRole("button", { name: "编辑字幕关键词" })[0]);
  expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["外观与效果"]);
  expect(screen.queryByLabelText("示例文字") === null).toBe(true);
  fireEvent.change(screen.getByLabelText("字号"), { target: { value: "60" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "关键词单独字效" }));
  expect(screen.queryAllByRole("button", { name: "编辑字幕关键词" }).length).toBe(0);
  fireEvent.click(screen.getByRole("checkbox", { name: "关键词单独字效" }));
  fireEvent.click(screen.getAllByRole("button", { name: "编辑字幕关键词" })[0]);
  expect(screen.getByLabelText<HTMLInputElement>("字号").value).toBe("60");
});
