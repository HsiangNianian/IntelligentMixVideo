/** 双语核心回归：对象开关、独立样式、协议往返、时间绑定和实际工作区；执行 bun run test。 */
import { expect, test } from "bun:test";
import { create, fromBinary, fromJson, toBinary, toJson } from "@bufbuild/protobuf";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SaveTemplateRequestSchema, SaveTemplateResponseSchema } from "@/generated/imv/template/v1/template_pb";
import { ListSpritesResponseSchema, SpriteKind } from "@/generated/imv/sprite/v1/sprite_pb";
import type { SpriteAsset } from "@/features/sprites/model";
import { activeDraft, boundText, newDraft, toDraft } from "@/features/templates/model";
import { addTrack, previewContext, resolveTemplate, setBilingual, setTrackTiming, trackDraft, updateTrack, validateTemplateTracks } from "@/features/templates/tracks";
import { addClip, bindClip, setClipParameter, timeClips, validateClipSlots } from "@/features/templates/clips";
import { buildPreviewRows, mergeSpriteRows, remotionTexts } from "@/features/templates/timeline";
import { resetTextTarget } from "@/features/templates/effects";
import { saveTemplate } from "@/features/templates/api";
import { TemplateWorkspace } from "@/features/templates/TemplateWorkspace";
import { readCatalog } from "@/features/templates/sdk";
import { buildTestTimeline, jsonTemplateResponse, sampleMedia, savedTemplate } from "./fixtures";
import { fetchMock } from "./setup";

const asset: SpriteAsset = { id: "text-asset", name: "字效", kind: SpriteKind.TEXT, seconds: 3, aspect: 9 / 16,
  width: 1080, height: 1920, fps: 30, box: { x: 100, y: 100, width: 800, height: 100 }, parameters: [] };

// 场景：双语字效（双语字幕）自己显示中英两种语言：只绑定中文对象，英文对象不能再绑字效，中英 IMS 文字一起交给字效显示。
test("双语字效绑定中文对象并同时替换中英文字", () => {
  const bilingual: SpriteAsset = { ...asset, id: "bilingual-asset", name: "双语字幕", lines: 2, bilingual: true };
  let draft = setBilingual(newDraft(), "subtitle", true);
  draft = addClip(draft, bilingual, [bilingual], "subtitle");
  const clip = draft.clips[0];
  // 双语字效连它所作用对象的英文字幕一起替换，普通字效只替换自己绑定的那一个。
  expect([...remotionTexts(draft, new Set([clip.id]))].sort()).toEqual(["subtitle", "subtitle:en"]);
  expect([...remotionTexts(draft)]).toEqual(["subtitle"]);
  expect(buildTestTimeline(draft, [], sampleMedia, [0, 1, 2], new Set([clip.id])).SubtitleTracks).toHaveLength(1);
  expect(buildTestTimeline(draft, []).SubtitleTracks).toHaveLength(2);
  // 英文已由双语字效显示，不能再给英文对象绑字效。
  expect(() => addClip(draft, bilingual, [bilingual], "subtitle", "en")).toThrow("中英两种语言");
});

// 场景：从未开启没有英文；关闭后保留英文外观与字效，重新开启恢复同一个对象，标题和字幕开关独立。
test("双语关闭只隐藏，再开启恢复设置", () => {
  const original = newDraft();
  expect(setBilingual(original, "title", false)).toBe(original);
  let draft = setBilingual(setBilingual(original, "title", true), "subtitle", true);
  const english = draft.tracks.find((track) => track.target === "title" && track.language === "en")!;
  draft = updateTrack(draft, english.id, { ...trackDraft(draft, english), editor: { ...english.editor, titleFont: "Source Han Serif CN", titleSize: 48, titleX: 45 } });
  draft = addClip(draft, asset, [asset], "title", "en");
  draft = setClipParameter(draft, draft.clips[0].id, "fontSize", 64);
  const off = setBilingual(draft, "title", false);
  expect(off.tracks.find((track) => track.id === english.id)?.editor).toMatchObject({ titleFont: "Source Han Serif CN", titleSize: 48, titleX: 45 });
  expect(off.clips).toEqual(draft.clips);
  expect(activeDraft(off).tracks.map((track) => track.id)).not.toContain(english.id);
  expect(activeDraft(off).tracks.some((track) => track.language === "en" && track.target === "subtitle")).toBe(true);
  expect(activeDraft(off).clips).toEqual([]);
  const restored = setBilingual(off, "title", true);
  expect(restored.tracks).toHaveLength(4);
  expect(activeDraft(restored).clips).toEqual(draft.clips);
  expect(original.tracks[0].editor.titleSize).toBe(40);
});

// 场景：中文改时间时同步停用的英文；英文不能单独改时间，重置也保留英文示例。
test("英文标题时间跟随中文，外观独立", () => {
  let draft = setBilingual(newDraft(), "title", true);
  const english = draft.tracks.at(-1)!;
  draft = setTrackTiming(setBilingual(draft, "title", false), "title", { start_mode: "percent", start: 25, duration: 4 });
  expect(draft.tracks.at(-1)).toMatchObject({ start_mode: "percent", start: 25, duration: 4, enabled: false });
  expect(() => setTrackTiming(draft, english.id, { start_mode: "seconds", start: 0, duration: 2 })).toThrow("跟随中文");
  expect(() => validateTemplateTracks([...draft.tracks, { ...english, id: "duplicate" }])).toThrow("最多各一个");
  expect(() => validateTemplateTracks(draft.tracks.filter((track) => track.id !== "title"))).toThrow("对应的中文");
  const reset = resetTextTarget(trackDraft(draft, english), "title", "en");
  expect(reset.editor.title).toBe("Make every frame count");
  const flower = readCatalog().find((item) => item.category === "flower" && item.id !== "flower/CS0003-000002")!;
  const styled = addTrack(draft, flower, "title", english.id).draft;
  expect(styled.tracks[0].editor.titleFlower).toBe("");
  expect(styled.tracks.at(-1)?.editor.titleFlower).toBe(flower.id);
});

// 场景：同一字效可绑定中英文，只有所属语言的 IMS 文字被替换，时间轴可分别选择两个实例。
test("中英文 Remotion 绑定及时间轴相互独立", () => {
  const bilingual = setBilingual(newDraft(), "subtitle", true);
  let draft = addClip(bilingual, asset, [asset], "subtitle", "en");
  const englishClip = draft.clips[0];
  expect(buildTestTimeline(draft, []).SubtitleTracks.map((row) => row.SubtitleTrackClips[0].Content)).toContain(bilingual.tracks[1].editor.subtitle);
  draft = addClip(draft, asset, [asset], "subtitle");
  expect(() => validateClipSlots(draft)).not.toThrow();
  expect(() => addClip(draft, asset, [asset], "subtitle", "en")).toThrow("已有 Remotion");
  expect(() => bindClip(draft, englishClip.id, { text_target: "subtitle" })).toThrow("已有 Remotion");
  const timed = timeClips(draft, resolveTemplate(draft, previewContext(sampleMedia)));
  const sprites = timed.clips.map((clip) => ({ id: clip.id, spriteId: clip.sprite_id, name: "字效", aspect: 9 / 16,
    start: clip.start, end: clip.start + clip.duration!, length: clip.duration!, slot: clip.text_target, textTrackId: boundText(draft.tracks, clip)!.id }));
  const rows = mergeSpriteRows(buildPreviewRows(buildTestTimeline(draft, [])), sprites);
  const english = draft.tracks.at(-1)!;
  expect(rows.find((row) => row.id === english.id)?.actions[0]).toMatchObject({ id: englishClip.id, label: "英文字幕" });
  expect(rows.find((row) => row.id === "subtitle")?.actions[0].id).toBe(draft.clips[1].id);
  const off = setBilingual(draft, "subtitle", false);
  expect(buildTestTimeline(off, []).previewRows.some((row) => row.id === english.id)).toBe(false);
});

// 场景：保存发出的真实协议仍携带停用英文的样式与绑定，服务端返回后保持相同草稿。
test("停用英文配置通过 Protobuf 请求和 JSON 响应往返", async () => {
  const original = savedTemplate();
  const draft = setBilingual(addClip(setBilingual(toDraft(original), "title", true), asset, [asset], "title", "en"), "title", false);
  fetchMock.mockImplementation((async (_url, init) => {
    const request = fromBinary(SaveTemplateRequestSchema, new Uint8Array(init!.body as Uint8Array));
    const english = request.tracks!.tracks.find((track) => track.target === "title" && track.language === "en")!;
    expect(english.enabled).toBe(false);
    expect(request.tracks!.tracks.find((track) => track.target === "remotion")?.language).toBe("en");
    const response = fromJson(SaveTemplateResponseSchema, await jsonTemplateResponse(original, "save").json());
    response.template!.tracks = request.tracks;
    return Response.json(toJson(SaveTemplateResponseSchema, response));
  }) as typeof fetch);
  const saved = await saveTemplate(draft, original.template_id);
  expect(saved.tracks).toEqual(draft.tracks);
  expect(saved.clips).toEqual(draft.clips);
});

// 场景：实际工作区提供两个独立开关；英文可以单独编辑，关闭时从对象列表隐藏，重新开启恢复字体大小。
test("工作区双语开关保留英文设置", async () => {
  fetchMock.mockImplementation((async (_url) => jsonTemplateResponse(savedTemplate(), "get")) as typeof fetch);
  render(<TemplateWorkspace selection={{ templateId: savedTemplate().template_id }} onHome={() => {}} />);
  fireEvent.click(await screen.findByRole("checkbox", { name: "中英双语" }));
  fireEvent.click(screen.getAllByRole("button", { name: "编辑英文标题" })[0]);
  fireEvent.click(screen.getByRole("tab", { name: "外观与效果" }));
  expect(screen.queryByRole("tab", { name: "时间设置" })).toBeNull();
  fireEvent.change(screen.getByLabelText("字号"), { target: { value: "55" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "中英双语" }));
  expect(screen.queryByRole("button", { name: "编辑英文标题" })).toBeNull();
  fireEvent.click(screen.getByRole("checkbox", { name: "中英双语" }));
  fireEvent.click(screen.getAllByRole("button", { name: "编辑英文标题" })[0]);
  expect(screen.getByLabelText<HTMLInputElement>("字号").value).toBe("55");
  fireEvent.click(screen.getByRole("button", { name: "编辑底部字幕" }));
  expect(screen.getByRole<HTMLInputElement>("checkbox", { name: "中英双语" }).checked).toBe(false);
  fireEvent.click(screen.getByRole("checkbox", { name: "中英双语" }));
  expect(screen.getAllByRole("button", { name: "编辑英文字幕" }).length).toBeGreaterThan(0);
});

// 场景：选中英文字幕后右栏直接提供「用 Remotion 字效显示」，不必先去左侧 AI 资产里找资产；绑定后右栏交给字效设置
// （字体来自共享字体库），保存请求里这条 remotion 轨道带 textTarget=subtitle 与 language=en。
test("右栏可直接把英文字幕改由 Remotion 字效显示", async () => {
  const template = savedTemplate();
  let request: ReturnType<typeof fromBinary<typeof SaveTemplateRequestSchema>> | undefined;
  fetchMock.mockImplementation((async (url, init) => {
    const path = new URL(String(url)).pathname;
    if (path === "/api/sprites") {
      const catalog = create(ListSpritesResponseSchema, { sprites: [{
        spriteId: "sprite-t", name: "金色字幕", kind: SpriteKind.TEXT,
        canvas: { width: 1080, height: 1920, fps: 30, previewFrames: 90 },
        contentBox: { x: 90, y: 760, width: 900, height: 400 }, parameters: [], lineCount: 0,
      }] });
      return new Response(Uint8Array.from(toBinary(ListSpritesResponseSchema, catalog)), { headers: { "Content-Type": "application/x-protobuf" } });
    }
    if (path === "/template" && init?.method === "POST") {
      request = fromBinary(SaveTemplateRequestSchema, new Uint8Array(init.body as Uint8Array));
      return jsonTemplateResponse(template, "save");
    }
    return jsonTemplateResponse(template, "get");
  }) as typeof fetch);
  render(<TemplateWorkspace selection={{ templateId: template.template_id }} onHome={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "编辑底部字幕" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "中英双语" }));
  fireEvent.click(screen.getAllByRole("button", { name: "编辑英文字幕" })[0]);
  fireEvent.click(await screen.findByRole("combobox", { name: "用 Remotion 字效显示" }));
  fireEvent.click(await screen.findByRole("option", { name: /金色字幕/ }));
  // 绑定后右栏改由字效设置接管，字效的字体在「外观与位置」里换。
  expect(await screen.findByRole("region", { name: "AI 资产设置" })).toBeTruthy();
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await waitFor(() => expect(request).toBeDefined());
  const clip = request!.tracks!.tracks.find((track) => track.target === "remotion")!;
  expect(clip.textTarget).toBe("subtitle");
  expect(clip.language).toBe("en");
});
