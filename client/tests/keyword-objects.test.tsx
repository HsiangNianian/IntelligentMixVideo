/** 关键词对象核心回归：开关与时间跟随、独立样式预览与叠放、Remotion 文字下不显示、协议往返和实际工作区；执行 bun run test。 */
import { expect, test } from "bun:test";
import { create, fromBinary, toBinary } from "@bufbuild/protobuf";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SaveTemplateRequestSchema, SaveTemplateResponseSchema, type SaveTemplateRequest } from "@/generated/imv/template/v1/template_pb";
import { SpriteKind } from "@/generated/imv/sprite/v1/sprite_pb";
import type { SpriteAsset } from "@/features/sprites/model";
import { activeDraft, newDraft, toDraft } from "@/features/templates/model";
import { addTrack, setKeywordObject, setTrackTiming, trackLabel, validateTemplateTracks } from "@/features/templates/tracks";
import { addClip } from "@/features/templates/clips";
import { exampleKeyword } from "@/features/templates/timeline";
import { saveTemplate } from "@/features/templates/api";
import { TemplateWorkspace } from "@/features/templates/TemplateWorkspace";
import { readCatalog } from "@/features/templates/sdk";
import { buildTestTimeline, protobufTemplateResponse, savedTemplate } from "./fixtures";
import { fetchMock } from "./setup";

const asset: SpriteAsset = { id: "text-asset", name: "字效", kind: SpriteKind.TEXT, seconds: 3, aspect: 9 / 16,
  width: 1080, height: 1920, fps: 30, box: { x: 100, y: 100, width: 800, height: 100 }, parameters: [] };

// 场景：首次开启按字幕建立关键词对象（字号放大、位于字幕上方、没有示例文字）；关闭只隐藏并保留外观，再开启恢复；标题改时间时关键词同步。
test("关键词对象开关保留设置，时间跟随所属文字", () => {
  const original = newDraft();
  expect(setKeywordObject(original, "subtitle", false)).toBe(original);
  const draft = setKeywordObject(original, "subtitle", true);
  const keyword = draft.tracks.at(-1)!;
  expect(keyword).toMatchObject({ target: "subtitle_keyword", start_mode: "seconds", start: 0, duration: null });
  expect(keyword.editor).toMatchObject({ title: "", subtitle: "", subtitleSize: 42, subtitleY: 72 });
  expect(trackLabel(keyword, draft.tracks)).toBe("字幕关键词");
  const off = setKeywordObject(draft, "subtitle", false);
  expect(activeDraft(off).tracks.some((track) => track.id === keyword.id)).toBe(false);
  expect(setKeywordObject(off, "subtitle", true).tracks.find((track) => track.id === keyword.id)?.editor).toEqual(keyword.editor);

  const titled = setTrackTiming(setKeywordObject(newDraft(), "title", true), "title", { start_mode: "percent", start: 20, duration: 3 });
  const titleKeyword = titled.tracks.at(-1)!;
  expect(titleKeyword).toMatchObject({ target: "title_keyword", start_mode: "percent", start: 20, duration: 3 });
  expect(() => setTrackTiming(titled, titleKeyword.id, { start_mode: "seconds", start: 0, duration: 2 })).toThrow("跟随所属文字");
  expect(() => validateTemplateTracks([...titled.tracks, { ...titleKeyword, id: "duplicate" }])).toThrow("最多各一个");
  expect(() => validateTemplateTracks(titled.tracks.map((track) => track.id === titleKeyword.id ? { ...track, start: 0 } : track))).toThrow("关键词时间必须跟随");
});

// 场景：选中关键词对象时花字只应用到它；预览里它显示字幕示例的关键词，用自己的字号、位置与花字，叠在字幕上层，
// 时间轴不为它单独加一行；字幕改用 Remotion 字效后，它的 IMS 关键词对象也不再交给 SDK。
test("关键词预览使用独立样式、叠在所属文字上层且不单独成行", () => {
  const catalog = readCatalog();
  const flower = catalog.find((item) => item.category === "flower" && item.id !== "flower/CS0003-000002")!;
  let draft = setKeywordObject(newDraft(), "subtitle", true);
  const keyword = draft.tracks.at(-1)!;
  draft = addTrack(draft, flower, "subtitle", keyword.id).draft;
  const subtitle = draft.tracks.find((track) => track.target === "subtitle")!;
  expect(subtitle.editor.subtitleFlower).toBe("");
  expect(draft.tracks.find((track) => track.id === keyword.id)?.editor.subtitleFlower).toBe(flower.id);
  const timeline = buildTestTimeline(draft, catalog);
  const clips = timeline.SubtitleTracks.map((row) => row.SubtitleTrackClips[0]);
  // SDK 把后面的 SubtitleTracks 叠在上层，所以顺序是标题、字幕、字幕关键词：关键词在字幕之上。
  expect(clips.map((clip) => clip.Content)).toEqual([draft.tracks[0].editor.title, subtitle.editor.subtitle, exampleKeyword(subtitle.editor, "subtitle")]);
  expect(clips[2]).toMatchObject({ FontSize: 42, Y: 0.72, ...flower.parameters });
  expect(clips[1]).not.toHaveProperty("EffectColorStyle");
  expect(timeline.previewRows.some((row) => row.id === keyword.id)).toBe(false);

  const replaced = buildTestTimeline(addClip(draft, asset, [asset], "subtitle"), catalog);
  expect(replaced.SubtitleTracks.map((row) => row.SubtitleTrackClips[0].Content)).toEqual([draft.tracks[0].editor.title]);
});

// 场景：保存发出的真实协议携带停用的关键词对象及其外观，读回后保持相同草稿。
test("关键词对象通过 Protobuf 往返", async () => {
  const original = savedTemplate();
  const draft = setKeywordObject(setKeywordObject(toDraft(original), "subtitle", true), "subtitle", false);
  fetchMock.mockImplementation((async (_url, init) => {
    const request = fromBinary(SaveTemplateRequestSchema, new Uint8Array(init!.body as Uint8Array));
    expect(request.tracks!.tracks.find((track) => track.target === "subtitle_keyword")?.enabled).toBe(false);
    const response = fromBinary(SaveTemplateResponseSchema, new Uint8Array(await protobufTemplateResponse(original, "save").arrayBuffer()));
    response.template!.tracks = request.tracks;
    return new Response(toBinary(SaveTemplateResponseSchema, create(SaveTemplateResponseSchema, response)));
  }) as typeof fetch);
  const saved = await saveTemplate(draft, original.template_id);
  expect(saved.tracks).toEqual(draft.tracks);
});

// 场景：实际工作区在字幕的「关键词设置」页签提供开关与关键词花字，关键词对象不出现在画面对象列表；它的设置只有外观页签、
// 没有示例文字，开关也在那里；关闭后隐藏，重新开启恢复字号与花字。
test("工作区关键词设置建立独立对象并保留外观", async () => {
  fetchMock.mockImplementation((async (_url) => protobufTemplateResponse(savedTemplate(), "get")) as typeof fetch);
  render(<TemplateWorkspace selection={{ environment: "cloud", templateId: savedTemplate().template_id }} onHome={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "编辑底部字幕" }));
  fireEvent.click(screen.getByRole("tab", { name: "关键词设置" }));
  fireEvent.click(screen.getByRole("switch", { name: "关键词单独字效" }));
  const flower = readCatalog().find((item) => item.category === "flower" && item.id !== "flower/CS0003-000002")!;
  fireEvent.click(screen.getByRole("combobox", { name: "关键词花字" }));
  fireEvent.click(await screen.findByRole("option", { name: new RegExp(flower.effect_id) }));
  expect(screen.getByRole("combobox", { name: "关键词花字" }).textContent).toBe(flower.name);
  expect(screen.queryAllByRole("button", { name: "编辑字幕关键词" }).length).toBe(1);
  fireEvent.click(screen.getByRole("button", { name: "编辑字幕关键词" }));
  expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["外观与效果"]);
  expect(screen.queryByLabelText("示例文字") === null).toBe(true);
  fireEvent.change(screen.getByLabelText("字号"), { target: { value: "60" } });
  fireEvent.click(screen.getByRole("switch", { name: "关键词单独字效" }));
  expect(screen.queryAllByRole("button", { name: "编辑字幕关键词" }).length).toBe(0);
  fireEvent.click(screen.getByRole("tab", { name: "关键词设置" }));
  fireEvent.click(screen.getByRole("switch", { name: "关键词单独字效" }));
  expect(screen.getByRole("combobox", { name: "关键词花字" }).textContent).toBe(flower.name);
  fireEvent.click(screen.getByRole("button", { name: "编辑字幕关键词" }));
  expect(screen.getByLabelText<HTMLInputElement>("字号").value).toBe("60");
});

// 场景：关键词对象存在并选中时，左侧「应用到」切到该对象后添加花字，只写入关键词轨道；父字幕保持无花字。
test("工作区向关键词对象应用资产", async () => {
  const original = savedTemplate();
  const draft = setKeywordObject(toDraft(original), "subtitle", true);
  original.tracks = draft.tracks;
  let request: SaveTemplateRequest | undefined;
  fetchMock.mockImplementation((async (url, init) => {
    if (String(url).endsWith("/template") && init?.method === "POST") {
      request = fromBinary(SaveTemplateRequestSchema, new Uint8Array(init.body as Uint8Array));
      const response = fromBinary(SaveTemplateResponseSchema, new Uint8Array(await protobufTemplateResponse(original, "save").arrayBuffer()));
      response.template!.tracks = request.tracks;
      return new Response(toBinary(SaveTemplateResponseSchema, create(SaveTemplateResponseSchema, response)));
    }
    return protobufTemplateResponse(original, "get");
  }) as typeof fetch);
  render(<TemplateWorkspace selection={{ environment: "cloud", templateId: original.template_id }} onHome={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "编辑底部字幕" }));
  fireEvent.click(screen.getByRole("tab", { name: "关键词设置" }));
  fireEvent.click(screen.getByRole("button", { name: "编辑字幕关键词" }));
  const assets = screen.getByRole("region", { name: "特效资产" });
  fireEvent.keyDown(within(assets).getByRole("combobox", { name: "应用到" }), { key: "ArrowDown" });
  fireEvent.click(await screen.findByRole("option", { name: "字幕关键词" }));
  const flower = readCatalog().find((item) => item.category === "flower" && item.id !== "flower/CS0003-000002")!;
  fireEvent.click(within(assets).getByRole("button", { name: `应用花字：${flower.name}` }));
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await waitFor(() => expect(request).toBeDefined());
  const tracks = request!.tracks!.tracks;
  expect(tracks.find((track) => track.target === "subtitle_keyword")?.editor?.subtitleFlower).toBe(flower.id);
  expect(tracks.find((track) => track.target === "subtitle")?.editor?.subtitleFlower).toBe("");
});
