/** 句中原位关键词核心回归：前提与开关、协议往返、两个隔离播放器之间的定位与工作区开关；执行 bun run test。 */
import { expect, spyOn, test } from "bun:test";
import { create, fromBinary, toBinary } from "@bufbuild/protobuf";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRef } from "react";
import { SaveTemplateRequestSchema, SaveTemplateResponseSchema } from "@/generated/imv/template/v1/template_pb";
import { SpriteKind } from "@/generated/imv/sprite/v1/sprite_pb";
import type { SpriteAsset } from "@/features/sprites/model";
import { toDraft } from "@/features/templates/model";
import { setKeywordInPlace, setKeywordObject, validateTemplateTracks } from "@/features/templates/tracks";
import { addClip, inPlaceProblem, removeClip, validateClipSlots } from "@/features/templates/clips";
import { saveTemplate } from "@/features/templates/api";
import { anchorTransform, SpriteOverlay, type SpriteOverlayHandle } from "@/features/templates/SpriteOverlay";
import { TemplateWorkspace } from "@/features/templates/TemplateWorkspace";
import { protobufTemplateResponse, savedTemplate } from "./fixtures";
import { fetchMock } from "./setup";

const box = { x: 0, y: 0, width: 180, height: 320 };
const asset = (id: string, keywordSlot: boolean): SpriteAsset => ({ id, name: id, kind: SpriteKind.TEXT, seconds: 3, aspect: 9 / 16,
  width: 180, height: 320, fps: 30, box, parameters: [], keywordSlot });
const marked = asset("marked", true);
const unmarked = asset("unmarked", false);
const word = asset("word", false);

// 场景：句中原位要求字幕与字幕关键词都使用 Remotion 字效且字幕字效标记了关键词，原因逐条说明；开关只属于关键词对象，
// 关闭时去掉字段；保存前缺少任一边的字效会被拒绝。
test("句中原位的前提、开关与保存校验", () => {
  let draft = setKeywordObject(toDraft(savedTemplate()), "subtitle", true);
  const keyword = draft.tracks.at(-1)!;
  expect(inPlaceProblem(draft, "subtitle_keyword", [])).toContain("底部字幕使用 Remotion 字效");
  draft = addClip(draft, unmarked, [unmarked, word], "subtitle");
  expect(inPlaceProblem(draft, "subtitle_keyword", [])).toContain("字幕关键词使用 Remotion 字效");
  draft = addClip(draft, word, [unmarked, word], "subtitle_keyword");
  expect(inPlaceProblem(draft, "subtitle_keyword", [unmarked, word])).toContain("没有标记关键词");
  const body = draft.clips[0];
  draft = { ...draft, clips: draft.clips.map((clip) => clip.id === body.id ? { ...clip, sprite_id: marked.id } : clip) };
  expect(inPlaceProblem(draft, "subtitle_keyword", [marked, word])).toBe("");
  const inPlace = setKeywordInPlace(draft, keyword.id, true);
  expect(inPlace.tracks.at(-1)?.in_place).toBe(true);
  expect(() => validateClipSlots(inPlace)).not.toThrow();
  expect(() => validateClipSlots(removeClip(inPlace, body.id))).toThrow("底部字幕使用 Remotion 字效");
  expect("in_place" in setKeywordInPlace(inPlace, keyword.id, false).tracks.at(-1)!).toBe(false);
  expect(() => setKeywordInPlace(draft, draft.tracks[1].id, true)).toThrow("只有关键词对象");
  expect(() => validateTemplateTracks(draft.tracks.map((track, index) => index === 1 ? { ...track, in_place: true } : track))).toThrow("只有关键词对象");
});

// 场景：保存发出的真实协议带上关键词对象的句中原位，读回后保持相同草稿。
test("句中原位通过 Protobuf 往返", async () => {
  const original = savedTemplate();
  let draft = setKeywordObject(toDraft(original), "title", true);
  draft = addClip(addClip(draft, marked, [marked, word], "title"), word, [marked, word], "title_keyword");
  draft = setKeywordInPlace(draft, draft.tracks.at(-1)!.id, true);
  fetchMock.mockImplementation((async (_url, init) => {
    const request = fromBinary(SaveTemplateRequestSchema, new Uint8Array(init!.body as Uint8Array));
    expect(request.tracks!.tracks.find((track) => track.target === "title_keyword")?.inPlace).toBe(true);
    const response = fromBinary(SaveTemplateResponseSchema, new Uint8Array(await protobufTemplateResponse(original, "save").arrayBuffer()));
    response.template!.tracks = request.tracks;
    return new Response(toBinary(SaveTemplateResponseSchema, create(SaveTemplateResponseSchema, response)));
  }) as typeof fetch);
  const saved = await saveTemplate(draft, original.template_id);
  expect(saved.tracks).toEqual(draft.tracks);
});

// 场景：与 keyword-anchor.js 相同的放置：关键词字效静止文字框的中心落在原句关键词的中心，按字高缩放并取它的不透明度。
test("关键词字效按原句关键词的位置与字高放置", () => {
  const transform = anchorTransform({ text: "乙", x: 60, y: 191, width: 40, height: 58, opacity: 0.5 }, { x: 50, y: 120, width: 80, height: 116 }, box);
  expect(transform).toEqual({ x: 80, y: 211, scale: 0.5, opacity: 0.5 });
});

// 场景：正文播放器隐藏并回报标记的关键词，关键词播放器回报自己的文字；两边都回报后关键词播放器才显示，并收到算好的宿主变换。
test("两个隔离播放器经父页面完成句中原位定位", async () => {
  const canvas = { width: 180, height: 320, box, movable: false };
  const body = { id: "body", spriteId: "b", aspect: 9 / 16, name: "正文", start: 0, end: 5, length: 5, hideKeyword: true, canvas, values: { text: "甲乙丙", keywords: ["乙"] } };
  const keyword = { id: "word", spriteId: "k", aspect: 9 / 16, name: "关键词", start: 0, end: 5, length: 5, anchor: "body", canvas, values: { text: "乙", keywords: [] } };
  const ref = createRef<SpriteOverlayHandle>();
  render(<SpriteOverlay ref={ref} clips={[body, keyword]} stageAspect={9 / 16} initialTime={1} />);
  const [bodyFrame, wordFrame] = ["正文", "关键词"].map((name) => screen.getByTitle<HTMLIFrameElement>(`Remotion 资产预览：${name}`));
  expect(bodyFrame.src).toContain("sync=1&measure=true&hide_keyword=true#");
  expect(wordFrame.src).toContain("sync=1&measure=true#");
  const post = spyOn(wordFrame.contentWindow!, "postMessage");
  const send = (frame: HTMLIFrameElement, data: object) => act(async () => {
    window.dispatchEvent(new MessageEvent("message", { source: frame.contentWindow, data: { channel: new URL(frame.src).hash.slice(1), ...data } }));
  });
  const tick = () => act(async () => { ref.current!.setTime(1); await new Promise((resolve) => requestAnimationFrame(() => resolve(undefined))); });
  for (const frame of [bodyFrame, wordFrame]) await send(frame, { type: "imv-preview-ready", sync: true, host: true });
  await tick();
  expect(wordFrame.style.visibility).toBe("hidden");
  expect(post.mock.calls.some(([message]) => (message as { type: string }).type === "imv-preview-update")).toBe(false);
  await send(bodyFrame, { type: "imv-preview-measure", spots: [{ text: "乙", x: 60, y: 191, width: 40, height: 58, opacity: 1 }], text: null });
  await send(wordFrame, { type: "imv-preview-measure", spots: [], text: { x: 50, y: 120, width: 80, height: 116 } });
  await tick();
  await waitFor(() => expect(post).toHaveBeenCalledWith(expect.objectContaining({ type: "imv-preview-update", host: { transform: { x: 80, y: 211, scale: 0.5, opacity: 1 } } }), "*"));
  expect(wordFrame.style.visibility).toBe("visible");
});

// 场景：实际工作区里，关键词对象没有两边的 Remotion 字效时「显示在句中原位」不可勾选，并说明缺什么。
test("工作区在条件不满足时禁用句中原位并说明原因", async () => {
  fetchMock.mockImplementation((async (_url) => protobufTemplateResponse(savedTemplate(), "get")) as typeof fetch);
  render(<TemplateWorkspace selection={{ environment: "cloud", templateId: savedTemplate().template_id }} onHome={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "编辑底部字幕" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "关键词单独字效" }));
  expect(screen.getByRole<HTMLInputElement>("checkbox", { name: "显示在句中原位" }).disabled).toBe(true);
  expect(screen.getByText("句中原位需要底部字幕使用 Remotion 字效")).toBeTruthy();
});
