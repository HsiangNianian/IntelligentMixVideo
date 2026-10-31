/** AI 资产核心测试：版本卡片的资产分类与发布请求，以及云端模板编辑中的资产目录、片段编辑与随模板的原子保存；执行 bun run test。 */
import { expect, spyOn, test } from "bun:test";
import { create, fromBinary, fromJson, toBinary, toJson, type MessageInitShape } from "@bufbuild/protobuf";
import { act, cleanup, fireEvent, render, screen, waitFor, waitForElementToBeRemoved, within } from "@testing-library/react";
import { VersionCard } from "@/features/remotion_templates/VersionCard";
import { defaultKind, publishChoice } from "@/features/remotion_templates/spriteChoice";
import { TemplateWorkspace } from "@/features/templates/TemplateWorkspace";
import { SpriteClips } from "@/features/templates/SpritePanels";
import { SpriteClipInspector } from "@/features/templates/SpriteClipInspector";
import { getTemplate, saveTemplate } from "@/features/templates/api";
import { bindClip } from "@/features/templates/clips";
import { CATALOG_SILENCE_MS, listSprites, watchCatalog } from "@/features/sprites/api";
import type { SpriteAsset } from "@/features/sprites/model";
import { newDraft, toDraft, type RemotionClip } from "@/features/templates/model";
import { EffectTemplateEditorSchema, GetTemplateResponseSchema, SaveTemplateRequestSchema, SaveTemplateResponseSchema } from "@/generated/imv/template/v1/template_pb";
import {
  ListSpritesResponseSchema,
  OperatorAccess,
  RenameSpriteRequestSchema,
  RenameSpriteResponseSchema,
  SpriteKind,
  SpriteParameterFormat,
  SpriteParameterSchema,
} from "@/generated/imv/sprite/v1/sprite_pb";
import { Toaster } from "@/components/ui/sonner";
import { remotionVersion } from "./remotion-fixtures";
import { FakeEventSource, fetchMock } from "./setup";
import { jsonTemplateResponse, savedTemplate } from "./fixtures";

const HEADERS = { "Content-Type": "application/x-protobuf" };

/** 一个 3 秒（90 帧、30 FPS）的资产摘要；parameters 为发布时的参数声明。 */
function summary(id = "sprite-1", kind = SpriteKind.VIDEO_OVERLAY, keywords = false, parameters: MessageInitShape<typeof SpriteParameterSchema>[] = []) {
  return {
    spriteId: id,
    name: "霓虹标题",
    kind,
    keywordsSupported: keywords,
    previewUrl: `/api/sprites/${id}/preview`,
    canvas: { width: 1080, height: 1920, fps: 30, previewFrames: 90 },
    contentBox: { x: 90, y: 760, width: 900, height: 400 },
    parameters,
    lineCount: 0,
  };
}

/** 字效资产：可改的文字、字号、颜色、关键词与全部关键词样式参数，只读的光晕。 */
function textAsset(id = "sprite-t") {
  return { ...summary(id, SpriteKind.TEXT, true, [
    { key: "text", label: "标题文字", access: OperatorAccess.VISIBLE_EDITABLE, defaultValue: { value: { case: "stringValue", value: "标题" } }, minLength: 1, maxLength: 12 },
    { key: "fontSize", label: "字号", access: OperatorAccess.VISIBLE_EDITABLE, defaultValue: { value: { case: "numberValue", value: 64 } }, minimum: 24, maximum: 240 },
    { key: "color", label: "颜色", access: OperatorAccess.VISIBLE_EDITABLE, defaultValue: { value: { case: "stringValue", value: "#FFFFFF" } }, format: SpriteParameterFormat.COLOR },
    { key: "keywords", label: "关键词", access: OperatorAccess.VISIBLE_EDITABLE, defaultValue: { value: { case: "stringListValue", value: { values: [] } } }, maxItems: 3, maxLength: 4 },
    { key: "keywordBold", label: "关键词加粗", access: OperatorAccess.VISIBLE_EDITABLE, defaultValue: { value: { case: "boolValue", value: false } } },
    { key: "keywordItalic", label: "关键词斜体", access: OperatorAccess.VISIBLE_EDITABLE, defaultValue: { value: { case: "boolValue", value: false } } },
    { key: "keywordUnderline", label: "关键词下划线", access: OperatorAccess.VISIBLE_EDITABLE, defaultValue: { value: { case: "boolValue", value: false } } },
    { key: "keywordStrikeout", label: "关键词删除线", access: OperatorAccess.VISIBLE_EDITABLE, defaultValue: { value: { case: "boolValue", value: false } } },
    { key: "keywordColor", label: "关键词颜色", access: OperatorAccess.VISIBLE_EDITABLE, defaultValue: { value: { case: "stringValue", value: "" } } },
    { key: "keywordSize", label: "关键词字号", access: OperatorAccess.VISIBLE_EDITABLE, defaultValue: { value: { case: "numberValue", value: 0 } }, minimum: 0, maximum: 300, format: SpriteParameterFormat.INTEGER },
    { key: "glow", label: "光晕", access: OperatorAccess.VISIBLE_READ_ONLY, defaultValue: { value: { case: "numberValue", value: 0.5 } } },
    { key: "secret", label: "内部", access: OperatorAccess.INTERNAL, defaultValue: { value: { case: "numberValue", value: 1 } } },
  ]), name: "金色标题" };
}

/** 构造二进制响应。 */
function binary(schema: Parameters<typeof toBinary>[0], message: unknown, status = 200): Response {
  return new Response(Uint8Array.from(toBinary(schema, message as never)), { status, headers: HEADERS });
}

/**
 * 按路径模拟模板与 Sprite 服务：GET 返回带给定片段的已保存模板，记录每次模板保存请求和所有写入路径（posts，按发送顺序）。
 * catalogStatus 让资产目录以该状态码失败（AI 资产服务不可用）。
 */
function sprites(options: { clips?: RemotionClip[]; saveStatus?: number; saveDetail?: string; catalog?: ReturnType<typeof summary>[]; catalogStatus?: number } = {}) {
  const template = { ...savedTemplate(), clips: options.clips ?? [] };
  const saved: ReturnType<typeof fromBinary<typeof SaveTemplateRequestSchema>>[] = [];
  const posts: string[] = [];
  fetchMock.mockImplementation((async (url, init) => {
    const path = new URL(String(url)).pathname;
    const post = init?.method === "POST";
    if (post) posts.push(path);
    if (path === "/api/sprites" && options.catalogStatus) return new Response(JSON.stringify({ detail: "AI 资产服务暂不可用" }), { status: options.catalogStatus });
    if (path === "/api/sprites") return binary(ListSpritesResponseSchema, create(ListSpritesResponseSchema, { sprites: options.catalog ?? [summary()] }));
    if (path === "/template" && post) {
      const request = fromBinary(SaveTemplateRequestSchema, new Uint8Array(init!.body as Uint8Array));
      saved.push(request);
      if (options.saveStatus)
        return new Response(JSON.stringify({ detail: options.saveDetail ?? "保存失败" }), { status: options.saveStatus });
      const clips = request.tracks!.tracks.filter((track) => track.target === "remotion")
        .map((track): RemotionClip => ({
          id: track.id, sprite_id: track.spriteId, start: track.start ?? 0, duration: track.duration ?? null,
          parameters: Object.fromEntries((track.editor?.remotion?.parameters ?? []).map((item) => [item.key, item.stringListValue ? [...item.stringListValue.values] : (item.stringValue ?? item.numberValue ?? item.boolValue)!])),
          transform: { ...(track.editor?.remotion?.transform ?? {}) },
        }));
      return jsonTemplateResponse({ ...template, clips }, "save", 201);
    }
    return jsonTemplateResponse(template, "get");
  }) as typeof fetch);
  return { template, saved, posts };
}

/** 从左侧真实资产添加一个顶部标题效果，使新模板满足 IMS 至少一个效果才能保存的要求。 */
async function addTitleEffect() {
  const assets = await screen.findByRole("region", { name: "特效资产" });
  fireEvent.click(within(assets).getByRole("radio", { name: "花字" }));
  fireEvent.click(within(assets).getAllByRole("button", { name: /^应用花字：/ })[0]);
}

/** 通过正式素材页签打开 AI 资产，目录会按当前环境读取。 */
async function openAssets() {
  fireEvent.click(await screen.findByRole("tab", { name: "AI 资产" }));
  return screen.findByRole("region", { name: "AI 资产" });
}

// 场景：卡片只选择资产分类（默认取 Agent 的判断），保存在工作区顶栏，卡片里不再有第二个保存按钮。
test("版本卡片显示 Agent 判断的资产分类，改选交给工作区且不提供保存按钮", async () => {
  const chosen: SpriteKind[] = [];
  const version = remotionVersion("version-2");
  version.spec.sprite = { kind: "text", bindings: [{ name: "text", title: "文字" }] };
  render(<VersionCard version={version} selected={false} latest disabled={false} previewDisabled={false} onPreview={() => {}} onKindChange={(kind) => chosen.push(kind)} />);
  expect(screen.getByRole("combobox", { name: "资产分类" }).textContent).toContain("字效");
  expect(screen.queryByRole("button", { name: "保存到资产" }) === null).toBe(true);
  fireEvent.keyDown(screen.getByRole("combobox", { name: "资产分类" }), { key: "ArrowDown" });
  fireEvent.keyDown(await screen.findByRole("option", { name: "滤镜" }), { key: "Enter" });
  expect(chosen).toEqual([SpriteKind.FILTER_OVERLAY]);
  expect(fetchMock).not.toHaveBeenCalled();
});

// 场景：改选其他分类时不带文字字段；字效声明了关键词参数时同时发布 keywords；没有分类的旧版本默认视频特效。
test("发布请求随所选分类变化", () => {
  const version = remotionVersion("version-2");
  version.spec.sprite = { kind: "text", bindings: [{ name: "text", title: "文字" }] };
  expect(publishChoice(version, SpriteKind.TEXT)).toEqual({ kind: SpriteKind.TEXT, textProp: "text", keywordsProp: "" });
  expect(publishChoice(version, SpriteKind.FILTER_OVERLAY)).toEqual({ kind: SpriteKind.FILTER_OVERLAY, textProp: "", keywordsProp: "" });
  version.spec.sprite = { kind: "text", bindings: [{ name: "text", title: "文字" }, { name: "keywords", title: "关键词" }] };
  expect(publishChoice(version, SpriteKind.TEXT)).toEqual({ kind: SpriteKind.TEXT, textProp: "text", keywordsProp: "keywords" });
  expect(defaultKind(undefined)).toBe(SpriteKind.VIDEO_OVERLAY);
});

/** 保存请求里的 remotion 轨道，按线路格式展开为便于断言的数组。 */
function wireClips(request: ReturnType<typeof fromBinary<typeof SaveTemplateRequestSchema>>) {
  return request.tracks!.tracks.filter((track) => track.target === "remotion");
}

// 旧用途原样往返不会隐式改成替代槽位；显式选择槽位后才采用新语义。
test.each([
  ["title", undefined, undefined],
  ["subtitle", undefined, undefined],
  ["subtitle", "title", "title"],
  ["decoration", undefined, undefined],
  [undefined, "subtitle", "subtitle"],
] as const)("旧用途 %s 与文字槽位 %s 往返保存时以槽位为准", async (purpose, textTarget, expectedTarget) => {
  const template = { ...savedTemplate(), clips: [{ id: "legacy", sprite_id: "sprite-1", start: 0, duration: 3, parameters: {}, transform: {} }] };
  const response = jsonTemplateResponse(template, "get");
  const message = fromJson(GetTemplateResponseSchema, await response.json());
  const track = message.template!.tracks!.tracks.find((item) => item.target === "remotion")!;
  track.textTarget = textTarget;
  track.editor = create(EffectTemplateEditorSchema, { remotion: {
    businessTarget: purpose,
    parameters: purpose === "title" || purpose === "subtitle" ? [
      { key: "text", stringValue: "旧示例文字" },
      { key: "keywords", stringListValue: { values: ["旧词"] } },
      { key: "color", stringValue: "#FFFFFF" },
    ] : [],
  } });
  let saved: ReturnType<typeof fromBinary<typeof SaveTemplateRequestSchema>> | undefined;
  fetchMock.mockImplementation((async (_url, init) => {
    if (init?.method === "POST") {
      saved = fromBinary(SaveTemplateRequestSchema, new Uint8Array(init.body as Uint8Array));
      return Response.json(toJson(SaveTemplateResponseSchema, create(SaveTemplateResponseSchema, { template: message.template })));
    }
    return Response.json(toJson(GetTemplateResponseSchema, message));
  }) as typeof fetch);
  const loaded = await getTemplate(template.template_id);
  expect(loaded.clips![0].text_target).toBe(expectedTarget);
  expect(loaded.clips![0].business_target).toBe(purpose);
  if (purpose === "title" || purpose === "subtitle") expect(loaded.clips![0].parameters).toEqual({ color: "#FFFFFF" });
  const rebound = expectedTarget === "title" ? "subtitle" : expectedTarget === "subtitle" ? "title" : undefined;
  const draft = toDraft(loaded);
  await saveTemplate(rebound ? bindClip(draft, "legacy", { text_target: rebound }) : draft, loaded.template_id);
  const [savedClip] = wireClips(saved!);
  expect(savedClip.textTarget).toBe(rebound);
  expect(savedClip.editor?.remotion?.businessTarget).toBe(purpose ? rebound ?? purpose : undefined);
});

// 场景：已有片段随模板读取后在右侧列出名称与时间；移除后随模板一次保存，请求里不再有 remotion 轨道，也没有任何绑定请求。
test("已有片段显示在列表中，移除后随模板保存", async () => {
  const { template, saved, posts } = sprites({ clips: [{ id: "sprite-p1", sprite_id: "sprite-1", start: 2, duration: 3, parameters: {}, transform: {} }] });
  render(<><TemplateWorkspace selection={{ templateId: template.template_id }} onHome={() => {}} /><Toaster /></>);
  const clips = await screen.findByRole("region", { name: "已添加 AI 资产" });
  const item = await within(clips).findByRole("listitem", { name: "AI 资产：霓虹标题" });
  expect(item.textContent).toContain("等待预览时间");
  fireEvent.click(within(item).getByRole("button", { name: "移除 AI 资产：霓虹标题" }));
  expect(screen.getByText("有未保存的修改")).toBeTruthy();
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await screen.findByText(`模板「${template.name}」已保存`);
  expect(saved).toHaveLength(1);
  expect(wireClips(saved[0])).toHaveLength(0);
  expect(posts).toEqual(["/template"]);
});

// 场景：AI 资产服务不可用（目录读取 503）：说明读取失败，IMS 对象照常添加，模板照常保存，已有片段原样保存、不会因读不到资产而丢失。
test("AI 资产服务不可用时模板照常编辑并原样保存已有片段", async () => {
  const { template, saved } = sprites({ catalogStatus: 503, clips: [{ id: "sprite-p1", sprite_id: "sprite-1", start: 2, duration: 3, parameters: {}, transform: { x: 540 } }] });
  render(<TemplateWorkspace selection={{ templateId: template.template_id }} onHome={() => {}} />);
  const clips = await screen.findByRole("region", { name: "已添加 AI 资产" });
  expect((await within(clips).findByRole("alert")).textContent).toContain("AI 资产服务暂不可用");
  await addTitleEffect();
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await waitFor(() => expect(saved).toHaveLength(1));
  expect(saved[0].effectIds.length).toBeGreaterThan(0);
  const [clip] = wireClips(saved[0]);
  expect(clip).toMatchObject({ id: "sprite-p1", spriteId: "sprite-1", start: 2, duration: 3 });
  expect(clip.editor?.remotion?.transform?.x).toBe(540);
});

// 场景：从目录添加资产，不出现任何配置控件；片段从 0 秒开始、长度为资产自身 3 秒，作为 remotion 轨道随模板保存，IMS 对象原样保留。
test("从目录添加资产无需配置，按资产自身长度随模板保存", async () => {
  const { template, saved, posts } = sprites();
  render(<TemplateWorkspace selection={{ templateId: template.template_id }} onHome={() => {}} />);
  const catalog = await openAssets();
  fireEvent.click(await within(catalog).findByRole("button", { name: "添加 AI 资产：霓虹标题" }));
  const item = await screen.findByRole("listitem", { name: "AI 资产：霓虹标题" });
  expect(within(item).queryAllByRole("textbox")).toHaveLength(0);
  expect(within(item).queryAllByRole("spinbutton")).toHaveLength(0);
  expect(item.textContent).toContain("等待预览时间");
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await waitFor(() => expect(saved).toHaveLength(1));
  const [clip] = wireClips(saved[0]);
  expect(clip).toMatchObject({ target: "remotion", spriteId: "sprite-1", startMode: "seconds", start: 0, duration: 3 });
  expect(toJson(EffectTemplateEditorSchema, clip.editor!)).toEqual({});
  expect(saved[0].tracks!.tracks.map((track) => track.target)).toEqual(["title", "subtitle", "remotion"]);
  expect(posts).toEqual(["/template"]);
});

// 场景：不占用模板对象的资产（滤镜）可以多次添加：每次都是独立的摆放（不同的轨道 ID），列表和保存请求里各有一条。
test("同一个资产可以添加多次", async () => {
  const { template, saved } = sprites({ catalog: [summary("sprite-1", SpriteKind.FILTER_OVERLAY)] });
  render(<TemplateWorkspace selection={{ templateId: template.template_id }} onHome={() => {}} />);
  const catalog = await openAssets();
  const add = await within(catalog).findByRole("button", { name: "添加 AI 资产：霓虹标题" });
  fireEvent.click(add);
  fireEvent.click(add);
  expect(await screen.findAllByRole("listitem", { name: "AI 资产：霓虹标题" })).toHaveLength(2);
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await waitFor(() => expect(saved).toHaveLength(1));
  const clips = wireClips(saved[0]);
  expect(clips.map((clip) => clip.spriteId)).toEqual(["sprite-1", "sprite-1"]);
  expect(new Set(clips.map((clip) => clip.id)).size).toBe(2);
});

// 场景：服务端拒绝保存（例如引用了未发布的资产）：提示原因、保留未保存状态，不自动重发。
test("保存被拒绝时提示原因并保留修改", async () => {
  const { template, posts } = sprites({
    saveStatus: 422, saveDetail: "AI 资产尚未发布：sprite-1",
    clips: [{ id: "sprite-p1", sprite_id: "sprite-1", start: 0, duration: 3, parameters: {}, transform: {} }],
  });
  render(<TemplateWorkspace selection={{ templateId: template.template_id }} onHome={() => {}} />);
  const item = await screen.findByRole("listitem", { name: "AI 资产：霓虹标题" });
  fireEvent.click(within(item).getByRole("button", { name: "移除 AI 资产：霓虹标题" }));
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await screen.findByText("AI 资产尚未发布：sprite-1");
  expect(screen.getByText("有未保存的修改")).toBeTruthy();
  expect(posts).toEqual(["/template"]);
});

// 场景：新模板只放 Remotion 片段、没有任何 IMS 效果：一次请求即可创建，效果列表为空。
test("新模板只放 Remotion 片段也能保存", async () => {
  const { saved, posts } = sprites();
  render(<TemplateWorkspace selection={{ templateId: null, name: "新模板", description: "" }} onHome={() => {}} />);
  const catalog = await openAssets();
  fireEvent.click(await within(catalog).findByRole("button", { name: "添加 AI 资产：霓虹标题" }));
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await waitFor(() => expect(saved).toHaveLength(1));
  expect(screen.queryByText("请至少选择一个效果")).toBeNull();
  expect(saved[0].effectIds).toEqual([]);
  expect(wireClips(saved[0])).toHaveLength(1);
  expect(posts).toEqual(["/template"]);
});

// 场景：新模板自带基础文字，无附加效果时也可以保存。
test("基础文字模板可以直接保存", async () => {
  const { posts } = sprites();
  render(<TemplateWorkspace selection={{ templateId: null, name: "空模板", description: "" }} onHome={() => {}} />);
  await openAssets();
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await waitFor(() => expect(posts).toEqual(["/template"]));
});

// 场景：新模板同时有 IMS 效果和 Remotion 片段：只发一次保存请求（原子），IMS 对象在前、片段在后。
test("新模板的效果和 Remotion 片段在同一次请求里保存", async () => {
  const { saved, posts } = sprites();
  render(<TemplateWorkspace selection={{ templateId: null, name: "新模板", description: "" }} onHome={() => {}} />);
  await addTitleEffect();
  const catalog = await openAssets();
  fireEvent.click(await within(catalog).findByRole("button", { name: "添加 AI 资产：霓虹标题" }));
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await waitFor(() => expect(saved).toHaveLength(1));
  expect(posts).toEqual(["/template"]);
  expect(saved[0].effectIds.length).toBeGreaterThan(0);
  expect(saved[0].tracks!.tracks.at(-1)!.target).toBe("remotion");
  expect(saved[0].tracks!.tracks.filter((track) => track.target !== "remotion").length).toBeGreaterThan(0);
});

// 场景：模板里的片段引用的资产已不在目录里：显示"未找到"并提示保存前移除，因为服务端会拒绝未发布的资产。
test("目录里找不到的资产提示先移除", async () => {
  const { template } = sprites({ clips: [{ id: "sprite-p1", sprite_id: "sprite-gone", start: 0, duration: 3, parameters: {}, transform: {} }] });
  render(<TemplateWorkspace selection={{ templateId: template.template_id }} onHome={() => {}} />);
  const item = await screen.findByRole("listitem", { name: "AI 资产：未找到的资产" });
  await waitFor(() => expect(item.textContent).toContain("资产已不可用，保存前请移除"));
});

// 场景：叠加渲染要求画布一致：目录里画布不同的资产在添加时就被提示并拒绝，模板里仍只有第一个片段，目录中显示各资产画布。
test("添加画布不一致的资产时提示原因", async () => {
  const landscape = { ...summary("sprite-2"), name: "横版标题", canvas: { width: 1920, height: 1080, fps: 30, previewFrames: 90 } };
  const { template } = sprites({ catalog: [summary(), landscape] });
  render(<TemplateWorkspace selection={{ templateId: template.template_id }} onHome={() => {}} />);
  const catalog = await openAssets();
  expect((await within(catalog).findAllByText(/1080×1920 @ 30 FPS/)).length).toBe(1);
  expect(within(catalog).getAllByText(/1920×1080 @ 30 FPS/).length).toBe(1);
  fireEvent.click(await within(catalog).findByRole("button", { name: "添加 AI 资产：霓虹标题" }));
  fireEvent.click(await within(catalog).findByRole("button", { name: "添加 AI 资产：横版标题" }));
  await screen.findByText(/「横版标题」的画布（1920×1080 @ 30 FPS）与已添加的「霓虹标题」（1080×1920 @ 30 FPS）不一致/);
  fireEvent.click(screen.getByRole("button", { name: "知道了" }));
  expect(screen.getAllByRole("listitem", { name: /^AI 资产：/ })).toHaveLength(1);
});


// 场景：字效作用于当前选择的文字对象（默认标题），不另开时间轴行：列表注明作用对象，设置里不再单独提供文字与关键词；改为作用于底部字幕后随模板保存。
test("字效作用于文字对象并可改为字幕", async () => {
  const { template, saved } = sprites({ catalog: [textAsset()] });
  render(<TemplateWorkspace selection={{ templateId: template.template_id }} onHome={() => {}} />);
  const catalog = await openAssets();
  fireEvent.click(await within(catalog).findByRole("button", { name: "添加 AI 资产：金色标题" }));
  const item = await screen.findByRole("listitem", { name: "AI 资产：金色标题" });
  expect(item.textContent).toContain("顶部标题字效");
  fireEvent.click(within(item).getByRole("button", { name: "设置 AI 资产：金色标题" }));
  const inspector = await screen.findByRole("region", { name: "AI 资产设置" });
  expect(within(inspector).queryByLabelText("标题文字")).toBeNull();
  expect(within(inspector).queryByRole("combobox", { name: "Remotion 合成用途" })).toBeNull();
  expect(within(inspector).getByLabelText("字号")).toBeTruthy();
  fireEvent.click(within(inspector).getByRole("radio", { name: "底部字幕" }));
  await waitFor(() => expect(screen.getByRole("listitem", { name: "AI 资产：金色标题" }).textContent).toContain("底部字幕字效"));
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await waitFor(() => expect(saved).toHaveLength(1));
  expect(wireClips(saved[0]).map((clip) => [clip.textTarget, clip.transitionBoundary])).toEqual([["subtitle", undefined]]);
});

// 场景：字效作用的标题在当前预览里没有显示时间，标题被隐藏，列表里的字效也标为未应用，不显示原有区间。
test("所作用文字不在预览内时字效标为未应用", () => {
  const asset: SpriteAsset = { id: "sprite-t", name: "金色标题", kind: SpriteKind.TEXT, seconds: 3, aspect: 9 / 16, width: 1080, height: 1920, fps: 30, box: { x: 0, y: 0, width: 1080, height: 1920 }, parameters: [] };
  const clip: RemotionClip = { id: "clip-t", sprite_id: "sprite-t", start: 0, duration: 3, parameters: {}, transform: {}, text_target: "title" };
  const props: Parameters<typeof SpriteClips>[0] = { clips: [clip], catalog: { assets: [asset], error: "", refresh: () => {}, peek: () => {} }, duration: 30, selected: null, onSelect: () => {}, onRemove: () => {} };
  const { rerender } = render(<SpriteClips {...props} />);
  expect(screen.getByRole("listitem", { name: "AI 资产：金色标题" }).textContent).toContain("顶部标题字效 · 0～3 秒");
  rerender(<SpriteClips {...props} hidden={new Set([clip.id])} />);
  expect(screen.getByRole("listitem", { name: "AI 资产：金色标题" }).textContent).toContain("顶部标题字效 · 当前视频未应用");
});

/** 多行字效的行间距参数（像素）。 */
const LINE_GAP: MessageInitShape<typeof SpriteParameterSchema> = { key: "lineGap", label: "行间距", access: OperatorAccess.VISIBLE_EDITABLE, defaultValue: { value: { case: "numberValue", value: 24 } }, minimum: 0, maximum: 300 };
/** 双栏字效的上栏字号比例（相对 fontSize），与服务端生成的配置参数同名同范围。 */
const LINE1_SIZE_RATIO: MessageInitShape<typeof SpriteParameterSchema> = { key: "line1SizeRatio", label: "上栏字号比例", access: OperatorAccess.VISIBLE_EDITABLE, defaultValue: { value: { case: "numberValue", value: 0.6 } }, minimum: 0.4, maximum: 2 };

/** 字效第一个特效（淡入）的开关、作用位置与时长，与服务端为文字块特效生成的配置参数同名同形。 */
const FADE_IN_PARAMETERS: MessageInitShape<typeof SpriteParameterSchema>[] = [
  { key: "effect1", label: "入场·淡入", access: OperatorAccess.VISIBLE_EDITABLE, defaultValue: { value: { case: "boolValue", value: true } } },
  { key: "effect1Target", label: "淡入位置", access: OperatorAccess.VISIBLE_EDITABLE, defaultValue: { value: { case: "stringValue", value: "tiers" } },
    allowedValues: ["block", "tiers", "line1", "line2", "keyword"].map((value) => ({ value: { case: "stringValue" as const, value } })) },
  { key: "effect1Seconds", label: "淡入时长（秒）", access: OperatorAccess.VISIBLE_EDITABLE, defaultValue: { value: { case: "numberValue", value: 0.5 } }, minimum: 0.1, maximum: 3 },
];

/**
 * 打开带一个字效片段的已保存模板，并选中该片段；lineCount 是目录里字效的固定行数，多行字效带行间距与上栏字号比例参数，
 * extra 是字效另带的参数（如特效）。
 */
async function openTextClip(clip: Partial<RemotionClip> = {}, lineCount = 1, extra: MessageInitShape<typeof SpriteParameterSchema>[] = []) {
  const text = textAsset();
  const fixture = sprites({
    catalog: [{ ...text, lineCount, parameters: [...text.parameters, ...(lineCount > 1 ? [LINE_GAP, LINE1_SIZE_RATIO] : []), ...extra] }, summary("sprite-f", SpriteKind.FILTER_OVERLAY), summary("sprite-v")],
    clips: [{ id: "clip-t", sprite_id: "sprite-t", start: 0, duration: 3, parameters: {}, transform: {}, ...clip }],
  });
  const remotion = { opened: 0 };
  render(<><TemplateWorkspace selection={{ templateId: fixture.template.template_id }} onHome={() => {}} onOpenRemotion={() => { remotion.opened += 1; }} /><Toaster /></>);
  const item = await screen.findByRole("listitem", { name: "AI 资产：金色标题" });
  fireEvent.click(within(item).getByRole("button", { name: "设置 AI 资产：金色标题" }));
  const inspector = await screen.findByRole("region", { name: "AI 资产设置" });
  return { ...fixture, inspector, remotion };
}

// 场景：目录给出的行数决定示例文字的输入方式：两行字效作用于底部字幕时出现两个逐行输入框；两行之间的距离和字号一样按这次摆放调整，并随模板保存。
test("目录里的多行字效在模板中逐行填写字幕示例并调整行间距", async () => {
  const { inspector, saved } = await openTextClip({ text_target: "subtitle" }, 2);
  expect(within(inspector).getByLabelText("示例文字第 1 行")).toBeTruthy();
  fireEvent.change(within(inspector).getByLabelText("示例文字第 2 行"), { target: { value: "第二行" } });
  await waitFor(() => expect(within(inspector).getByLabelText<HTMLInputElement>("示例文字第 2 行").value).toBe("第二行"));
  fireEvent.change(within(inspector).getByLabelText("行间距"), { target: { value: "60" } });
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await waitFor(() => expect(saved).toHaveLength(1));
  expect(wireClips(saved[0]).map((clip) => clip.editor?.remotion?.parameters.map((item) => [item.key, item.numberValue]))).toEqual([[["lineGap", 60]]]);
});

// 场景：双栏字效作用于标题时同样逐栏填写示例（合成按标题里的换行分栏）；逐栏字号比例和字效工作区的配置一样在「外观与位置」里按这次摆放调整，不列入资产默认参数，并随模板保存。
test("双栏字效作用于标题时逐栏填写示例并调整逐栏字号比例", async () => {
  const { inspector, saved } = await openTextClip({ text_target: "title" }, 2);
  fireEvent.change(within(inspector).getByLabelText("示例文字第 1 行"), { target: { value: "判断一个人" } });
  fireEvent.change(within(inspector).getByLabelText("示例文字第 2 行"), { target: { value: "选择花字" } });
  await waitFor(() => expect(within(inspector).getByLabelText<HTMLInputElement>("示例文字第 2 行").value).toBe("选择花字"));
  expect(within(inspector).getByText(/按其中的换行分行/)).toBeTruthy();
  expect(within(inspector).getByRole("heading", { name: "资产默认参数" }).closest("fieldset")!.textContent).not.toContain("上栏字号比例");
  fireEvent.change(within(inspector).getByLabelText("上栏字号比例"), { target: { value: "0.8" } });
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await waitFor(() => expect(saved).toHaveLength(1));
  expect(saved[0].tracks!.tracks.find((track) => track.target === "title")?.editor?.title).toBe("判断一个人\n选择花字");
  expect(wireClips(saved[0]).map((clip) => clip.editor?.remotion?.parameters.map((item) => [item.key, item.numberValue]))).toEqual([[["line1SizeRatio", 0.8]]]);
});

// 场景：字效特效的开关、作用位置与时长和字效工作区的「特效」一样在「外观与位置」里按这次摆放调整，位置显示中文名称；不列入资产默认参数，并随模板保存。
test("字效特效的开关、位置与时长在模板里按摆放调整", async () => {
  const { inspector, saved } = await openTextClip({ text_target: "title" }, 2, FADE_IN_PARAMETERS);
  const effects = within(inspector).getByRole("heading", { name: "特效" }).parentElement!;
  expect(within(effects).getByLabelText("淡入位置").textContent).toBe("各栏");
  expect(within(inspector).getByRole("heading", { name: "资产默认参数" }).closest("fieldset")!.textContent).not.toContain("淡入");
  fireEvent.click(within(effects).getByLabelText("入场·淡入"));
  fireEvent.change(within(effects).getByLabelText("淡入时长（秒）"), { target: { value: "1.2" } });
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await waitFor(() => expect(saved).toHaveLength(1));
  expect(wireClips(saved[0]).map((clip) => clip.editor?.remotion?.parameters.map((item) => [item.key, item.boolValue ?? item.numberValue]))).toEqual([[["effect1", false], ["effect1Seconds", 1.2]]]);
});

// 场景：自由组件的样式参数带分组（发布时的 group）：模板编辑按分组成卡片（以分组名为标题）并按这次摆放调整，不列入资产默认参数，随模板保存。
test("自由组件按分组声明的样式参数在模板里成卡片并按摆放调整", async () => {
  const grouped: MessageInitShape<typeof SpriteParameterSchema>[] = [
    { key: "riseDistance", label: "上浮距离", access: OperatorAccess.VISIBLE_EDITABLE, defaultValue: { value: { case: "numberValue", value: 0.6 } }, minimum: 0, maximum: 2, group: "入场·上浮" },
  ];
  const { inspector, saved } = await openTextClip({ text_target: "title" }, 1, grouped);
  const card = within(inspector).getByRole("region", { name: "入场·上浮" });
  expect(within(inspector).getByRole("heading", { name: "资产默认参数" }).closest("fieldset")!.textContent).not.toContain("上浮距离");
  fireEvent.change(within(card).getByLabelText("上浮距离"), { target: { value: "1.5" } });
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await waitFor(() => expect(saved).toHaveLength(1));
  expect(wireClips(saved[0]).map((clip) => clip.editor?.remotion?.parameters.map((item) => [item.key, item.numberValue]))).toEqual([[["riseDistance", 1.5]]]);
});

// 场景：资产库按字效、滤镜、转场与视频特效分类，显示各类数量，默认打开第一个有资产的分类。
test("AI 资产目录按三类分栏", async () => {
  sprites({ catalog: [textAsset(), summary("sprite-f", SpriteKind.FILTER_OVERLAY), summary("sprite-v"), summary("sprite-x", SpriteKind.TRANSITION_OVERLAY)] });
  render(<TemplateWorkspace selection={{ templateId: null, name: "新模板", description: "" }} onHome={() => {}} />);
  const catalog = await openAssets();
  const tabs = await within(catalog).findByRole("radiogroup", { name: "AI 资产分类" });
  expect(within(tabs).getAllByRole("radio").map((item) => item.textContent)).toEqual(["字效 1", "滤镜 1", "转场与视频特效 2"]);
  expect(within(catalog).getByRole("button", { name: "添加 AI 资产：金色标题" })).toBeTruthy();
  fireEvent.click(within(tabs).getByRole("radio", { name: /转场与视频特效/ }));
  expect(within(catalog).getAllByRole("button", { name: "添加 AI 资产：霓虹标题" })).toHaveLength(2);
  expect(within(catalog).queryByRole("button", { name: "添加 AI 资产：金色标题" })).toBeNull();
});

// 场景：模板里只调这次摆放的示例文字、字号与位置（百分比，像 IMS 文字）：恢复默认的参数不再出现在请求里；颜色、光晕等资产内部参数不提供控件，只在「资产默认参数」里说明并给出回字效工作区的入口。
test("字效在模板里只调示例文字、字号与位置，其余参数回字效工作区改", async () => {
  const { saved, inspector, remotion } = await openTextClip({ text_target: "title" });
  expect(within(inspector).queryByText("内部")).toBeNull();
  for (const hidden of ["光晕", "颜色", "缩放", "旋转（度）", "不透明度", "标题文字"]) expect(within(inspector).queryByLabelText(hidden)).toBeNull();
  expect(within(inspector).getByLabelText<HTMLInputElement>("水平位置 %").value).toBe("50");
  expect(within(inspector).getByLabelText<HTMLInputElement>("垂直位置 %").value).toBe("50");
  const defaults = within(inspector).getByRole("heading", { name: "资产默认参数" }).closest("fieldset")!;
  expect(defaults.textContent).toContain("光晕");
  fireEvent.click(within(defaults).getByRole("button", { name: "前往字效工作区" }));
  expect(remotion.opened).toBe(1);
  fireEvent.change(within(inspector).getByLabelText("示例文字"), { target: { value: "新品上市" } });
  fireEvent.change(within(inspector).getByLabelText("字号"), { target: { value: "96" } });
  fireEvent.click(within(inspector).getByRole("button", { name: "恢复默认：字号" }));
  expect(within(inspector).queryByRole("button", { name: "恢复默认：字号" })).toBeNull();
  fireEvent.change(within(inspector).getByLabelText("水平位置 %"), { target: { value: "25" } });
  expect(within(screen.getByRole("listitem", { name: "AI 资产：金色标题" })).getByText(/已调整参数/)).toBeTruthy();
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await waitFor(() => expect(saved).toHaveLength(1));
  const [clip] = wireClips(saved[0]);
  expect(toJson(EffectTemplateEditorSchema, clip.editor!)).toEqual({ remotion: { transform: { x: 270 } } });
  expect(saved[0].tracks!.tracks.find((track) => track.target === "title")?.editor?.title).toBe("新品上市");
});

// 场景：被字效接管的标题，选中它就是选中字效：右栏只显示字效设置（不再出现 IMS 的字号、位置面板），标题的时间在字效的「时间设置」页签里调。
test("选中被接管的标题打开字效设置而不是 IMS 面板，时间在字效页签里调", async () => {
  await openTextClip({ text_target: "title" });
  fireEvent.click(screen.getByRole("button", { name: "编辑顶部标题" }));
  const inspector = await screen.findByRole("region", { name: "AI 资产设置" });
  expect(screen.queryByRole("region", { name: "画面对象设置" })).toBeNull();
  const tabs = within(inspector).getAllByRole("tab");
  expect(tabs.map((tab) => tab.textContent)).toEqual(["外观与位置", "时间设置", "关键词设置"]);
  fireEvent.click(tabs[1]);
  expect(within(inspector).getByLabelText("开始时间 / 秒")).toBeTruthy();
});

// 场景：字效资产声明了关键词参数时，右栏出现「关键词设置」页签：关键词、样式切换与颜色集中在此，其余参数留在原页签；改动随模板保存。
test("关键词参数集中在「关键词设置」页签并随模板保存", async () => {
  const { saved, inspector } = await openTextClip();
  const tabs = within(inspector).getAllByRole("tab");
  expect(tabs.map((tab) => tab.textContent)).toEqual(["外观与位置", "关键词设置"]);
  expect(within(inspector).getByLabelText("关键词").closest("fieldset")!.hidden).toBe(true);
  fireEvent.click(tabs[1]);
  const panel = within(inspector).getByLabelText("关键词").closest("fieldset")!;
  expect(panel.hidden).toBe(false);
  expect(within(panel).queryByLabelText("示例文字")).toBeNull();
  expect(within(panel).queryByText(/发布于关键词设置之前/)).toBeNull();
  fireEvent.click(within(panel).getByRole("button", { name: "加粗" }));
  fireEvent.change(within(panel).getByLabelText("关键词"), { target: { value: "新品" } });
  expect(within(panel).getByText("保持文字原色")).toBeTruthy();
  expect(within(panel).getByLabelText<HTMLInputElement>("关键词颜色").disabled).toBe(true);
  fireEvent.click(within(panel).getByRole("switch", { name: "设置关键词颜色" }));
  fireEvent.change(within(panel).getByLabelText("关键词颜色"), { target: { value: "#FF0000" } });
  fireEvent.click(within(panel).getByRole("switch", { name: "设置关键词字号" }));
  expect(within(panel).getByLabelText<HTMLInputElement>("关键词字号").value).toBe("64");
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await waitFor(() => expect(saved).toHaveLength(1));
  const [clip] = wireClips(saved[0]);
  expect(toJson(EffectTemplateEditorSchema, clip.editor!)).toEqual({ remotion: { parameters: [
    { key: "keywordBold", boolValue: true },
    { key: "keywords", stringListValue: { values: ["新品"] } },
    { key: "keywordColor", stringValue: "#FF0000" },
    { key: "keywordSize", numberValue: 64 },
  ] } });
});

// 场景：非字效资产没有关键词设置页签；发布于关键词参数之前的字效仍显示页签并说明需要重新生成。
test("只有字效显示「关键词设置」页签，旧字效提示重新生成", async () => {
  sprites({ catalog: [summary("sprite-v")], clips: [{ id: "clip-v", sprite_id: "sprite-v", start: 0, duration: 3, parameters: {}, transform: {} }] });
  render(<TemplateWorkspace selection={{ templateId: savedTemplate().template_id }} onHome={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "设置 AI 资产：霓虹标题" }));
  expect(screen.queryByRole("tab", { name: "关键词设置" })).toBeNull();
  cleanup();
  // 审计复现：只带 keywords 的旧字效曾不提示重新生成，只显示缺少样式控件的页签。
  const text = textAsset().parameters;
  for (const parameters of [[text[0]], text.filter((item) => item.key === "text" || item.key === "keywords")]) {
    sprites({ catalog: [summary("sprite-o", SpriteKind.TEXT, true, parameters)], clips: [{ id: "clip-o", sprite_id: "sprite-o", start: 0, duration: 3, parameters: {}, transform: {} }] });
    render(<TemplateWorkspace selection={{ templateId: savedTemplate().template_id }} onHome={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "设置 AI 资产：霓虹标题" }));
    expect(screen.getByRole("tab", { name: "关键词设置" })).toBeTruthy();
    expect(screen.getByText(/发布于关键词设置之前/)).toBeTruthy();
    cleanup();
  }
});

// 场景：切换到另一个字效片段时页签回到「外观与位置」；非字效资产以 keyword 开头的参数同样不在模板里调整。
test("切换片段重置页签，非字效的 keyword 参数不进入关键词页签", async () => {
  const effect = summary("sprite-v", SpriteKind.VIDEO_OVERLAY, false, [
    { key: "keywordGlow", label: "关键光晕", access: OperatorAccess.VISIBLE_EDITABLE, defaultValue: { value: { case: "numberValue", value: 0.5 } }, minimum: 0, maximum: 1 },
  ]);
  sprites({ catalog: [textAsset(), effect], clips: [
    { id: "clip-a", sprite_id: "sprite-t", start: 0, duration: 3, parameters: {}, transform: {} },
    { id: "clip-v", sprite_id: "sprite-v", start: 0, duration: 3, parameters: {}, transform: {} },
    { id: "clip-b", sprite_id: "sprite-t", start: 1, duration: 3, parameters: {}, transform: {} },
  ] });
  render(<TemplateWorkspace selection={{ templateId: savedTemplate().template_id }} onHome={() => {}} />);
  const [first, second] = await screen.findAllByRole("button", { name: "设置 AI 资产：金色标题" });
  fireEvent.click(first);
  fireEvent.click(screen.getByRole("tab", { name: "关键词设置" }));
  expect(screen.getByRole("tab", { name: "关键词设置" }).getAttribute("aria-selected")).toBe("true");
  // 审计复现：右栏不随片段重建时，上一个片段选中的页签会带到下一个片段。
  fireEvent.click(second);
  expect(screen.getByRole("tab", { name: "外观与位置" }).getAttribute("aria-selected")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "设置 AI 资产：霓虹标题" }));
  const inspector = screen.getByRole("region", { name: "AI 资产设置" });
  expect(within(inspector).queryByRole("tab")).toBeNull();
  // 非字效没有关键词页签，以 keyword 开头的参数同其他内部参数一样回字效工作区改。
  expect(within(inspector).queryByLabelText("关键光晕")).toBeNull();
  expect(within(inspector).getByRole("heading", { name: "资产默认参数" }).closest("fieldset")!.textContent).toContain("关键光晕");
});

// 场景：生成时带关键词字效的字效另有 keywordEffect… 参数；「关键词设置」只提供关键词与六个样式选项，关键词字效本身回字效工作区修改。
test("关键词字效参数不在模板里调整，只列在资产默认参数", async () => {
  const text = textAsset();
  sprites({
    catalog: [{ ...text, parameters: [...text.parameters, { key: "keywordEffectScale", label: "关键词弹跳幅度", access: OperatorAccess.VISIBLE_EDITABLE, defaultValue: { value: { case: "numberValue", value: 1.3 } }, minimum: 1, maximum: 2 }] }],
    clips: [{ id: "clip-t", sprite_id: "sprite-t", start: 0, duration: 3, parameters: {}, transform: {} }],
  });
  render(<TemplateWorkspace selection={{ templateId: savedTemplate().template_id }} onHome={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "设置 AI 资产：金色标题" }));
  const inspector = screen.getByRole("region", { name: "AI 资产设置" });
  fireEvent.click(within(inspector).getByRole("tab", { name: "关键词设置" }));
  expect(within(inspector).getByLabelText("关键词")).toBeTruthy();
  expect(within(inspector).queryByLabelText("关键词弹跳幅度") === null).toBe(true);
  fireEvent.click(within(inspector).getByRole("tab", { name: "外观与位置" }));
  expect(within(inspector).getByRole("heading", { name: "资产默认参数" }).closest("fieldset")!.textContent).toContain("关键词弹跳幅度");
});

// 场景：已保存的覆盖随模板读回：位置显示为百分比；不在模板里调整的参数（这里是标题文字）仍列出「已覆盖」并可恢复默认，恢复后移除覆盖；滤镜只提供强度。
test("读回片段的覆盖值，内部参数的覆盖可恢复默认，滤镜只调整强度", async () => {
  const { inspector } = await openTextClip({ parameters: { text: "已保存" }, transform: { x: 300, y: 500, scale: 2 } });
  expect(within(inspector).getByLabelText<HTMLInputElement>("水平位置 %").value).toBe("27.8");
  expect(within(inspector).getByText(/标题文字 · 已覆盖/)).toBeTruthy();
  expect(within(inspector).getByText(/缩放 · 已覆盖/)).toBeTruthy();
  fireEvent.click(within(inspector).getByRole("button", { name: "恢复默认：标题文字" }));
  expect(within(inspector).queryByText(/标题文字 · 已覆盖/)).toBeNull();
  fireEvent.click(within(inspector).getByRole("button", { name: "关闭资产设置" }));
  const catalog = await openAssets();
  fireEvent.click(await within(catalog).findByRole("radio", { name: /滤镜/ }));
  fireEvent.click(within(catalog).getByRole("button", { name: "添加 AI 资产：霓虹标题" }));
  const added = (await screen.findAllByRole("button", { name: "设置 AI 资产：霓虹标题" }))[0];
  fireEvent.click(added);
  const filter = await screen.findByRole("region", { name: "AI 资产设置" });
  expect(within(filter).getByLabelText("强度")).toBeTruthy();
  expect(within(filter).queryByLabelText("水平位置 %")).toBeNull();
});

// 场景（回归）：资产目录还在读取、或资产已被删除时，带位置覆盖的片段也能打开右栏，只允许移除，不能因读不到资产的类型而整页白屏。
test("资产未读到时带位置覆盖的片段仍能打开设置栏", () => {
  const clip: RemotionClip = { id: "clip-x", sprite_id: "sprite-gone", start: 0, duration: 3, parameters: {}, transform: { x: 270, scale: 2 } };
  const noop = () => {};
  render(<SpriteClipInspector clip={clip} asset={undefined} duration={10} onSample={noop} onTextTiming={noop} onParameter={noop} onTransform={noop} onStart={noop} onBind={noop} onRemove={noop} onClose={noop} />);
  expect(screen.getByText("未找到的资产")).toBeTruthy();
});

// 场景：多行字效作用于底部字幕或标题时，示例文字都按资产行数逐行填写，用换行连接写回所作用的文字对象；单行字效仍是一个输入框。
test("多行字效作用于字幕或标题时逐行填写示例文字", () => {
  const asset: SpriteAsset = { id: "sprite-t", name: "两行花字", kind: SpriteKind.TEXT, seconds: 3, aspect: 9 / 16, width: 1080, height: 1920, fps: 30, box: { x: 0, y: 0, width: 1080, height: 1920 }, parameters: [], lines: 2 };
  const clip: RemotionClip = { id: "clip-t", sprite_id: "sprite-t", start: 0, duration: 3, parameters: {}, transform: {}, text_target: "subtitle" };
  const [title, subtitle] = newDraft().tracks;
  const samples: string[] = [];
  const noop = () => {};
  const props = { asset, duration: 10, onTextTiming: noop, onParameter: noop, onTransform: noop, onStart: noop, onBind: noop, onRemove: noop, onClose: noop };
  const { rerender } = render(<SpriteClipInspector {...props} clip={clip} textTrack={{ ...subtitle!, editor: { ...subtitle!.editor, subtitle: "判断一个人\n选择花字" } }}
    onSample={(value) => samples.push(value)} />);
  expect(screen.getByLabelText<HTMLInputElement>("示例文字第 1 行").value).toBe("判断一个人");
  fireEvent.change(screen.getByLabelText("示例文字第 2 行"), { target: { value: "选择滤镜" } });
  expect(samples).toEqual(["判断一个人\n选择滤镜"]);
  rerender(<SpriteClipInspector {...props} clip={{ ...clip, text_target: "title" }} textTrack={{ ...title!, editor: { ...title!.editor, title: "今日灵感" } }} onSample={(value) => samples.push(value)} />);
  expect(screen.getByLabelText<HTMLInputElement>("示例文字第 1 行").value).toBe("今日灵感");
  fireEvent.change(screen.getByLabelText("示例文字第 2 行"), { target: { value: "副标题" } });
  expect(samples.at(-1)).toBe("今日灵感\n副标题");
  rerender(<SpriteClipInspector {...props} asset={{ ...asset, lines: 1 }} clip={{ ...clip, text_target: "title" }} textTrack={title} onSample={(value) => samples.push(value)} />);
  expect(screen.queryByLabelText("示例文字第 1 行") === null).toBe(true);
  expect(screen.getByLabelText("示例文字")).toBeTruthy();
});

// 场景：英文标题和字幕固定单行——即使选中双栏字效，示例文字也只填一行（既有换行按空格合并），并说明下栏设置不参与；
// 中英双语因此不会出现中文两栏加英文两栏的四行。
test("英文字幕绑定双栏字效时只填一行示例文字", () => {
  const asset: SpriteAsset = { id: "sprite-t", name: "两行花字", kind: SpriteKind.TEXT, seconds: 3, aspect: 9 / 16, width: 1080, height: 1920, fps: 30, box: { x: 0, y: 0, width: 1080, height: 1920 }, parameters: [], lines: 2 };
  const clip: RemotionClip = { id: "clip-t", sprite_id: "sprite-t", start: 0, duration: 3, parameters: {}, transform: {}, text_target: "subtitle", language: "en" };
  const [, subtitle] = newDraft().tracks;
  const english = { ...subtitle!, language: "en" as const, editor: { ...subtitle!.editor, subtitle: "First line\nSecond line" } };
  const samples: string[] = [];
  const noop = () => {};
  render(<SpriteClipInspector asset={asset} clip={clip} textTrack={english} duration={10} onSample={(value) => samples.push(value)}
    onTextTiming={noop} onParameter={noop} onTransform={noop} onStart={noop} onBind={noop} onRemove={noop} onClose={noop} />);
  expect(screen.queryByLabelText("示例文字第 1 行")).toBeNull();
  expect(screen.getByLabelText<HTMLInputElement>("示例文字").value).toBe("First line Second line");
  fireEvent.change(screen.getByLabelText("示例文字"), { target: { value: "One row" } });
  expect(samples).toEqual(["One row"]);
  expect(screen.getByText(/英文字幕固定单行/)).toBeTruthy();
});

// 场景：双语字效（双语字幕）的示例文字中英各一行——中文取自中文字幕、英文取自同一用途的英文对象；
// 作用对象只列中文（英文已由双语字效显示），并说明两种语言同时显示。
test("双语字效的示例文字中英各一行", () => {
  const asset: SpriteAsset = { id: "sprite-b", name: "双语字幕", kind: SpriteKind.TEXT, seconds: 3, aspect: 9 / 16, width: 1080, height: 1920, fps: 30, box: { x: 0, y: 0, width: 1080, height: 1920 }, parameters: [], lines: 2, bilingual: true };
  const clip: RemotionClip = { id: "clip-b", sprite_id: "sprite-b", start: 0, duration: 3, parameters: {}, transform: {}, text_target: "subtitle" };
  const [title, subtitle] = newDraft().tracks;
  const english = { ...subtitle!, id: "subtitle-en", language: "en" as const, editor: { ...subtitle!.editor, subtitle: "Today is a good day" } };
  const samples: [string, string | undefined][] = [];
  const noop = () => {};
  render(<SpriteClipInspector asset={asset} clip={clip} textTrack={subtitle} textTracks={[title!, subtitle!, english]} duration={10}
    onSample={(value) => samples.push([value, undefined])} onEnglishSample={(value) => samples.push([value, "en"])}
    onTextTiming={noop} onParameter={noop} onTransform={noop} onStart={noop} onBind={noop} onRemove={noop} onClose={noop} />);
  expect(screen.getByLabelText<HTMLInputElement>("示例文字（中文）").value).toBe(subtitle!.editor.subtitle);
  expect(screen.getByLabelText<HTMLInputElement>("示例文字（英文）").value).toBe("Today is a good day");
  fireEvent.change(screen.getByLabelText("示例文字（英文）"), { target: { value: "Another day" } });
  expect(samples).toEqual([["Another day", "en"]]);
  expect(screen.queryByText("英文字幕")).toBeNull();
  expect(screen.getByText(/中英两种语言/)).toBeTruthy();
});

// 场景：双栏双语的两组中英示例分别填写；修改第二组译文保留第一组，避免界面再次合并或漏行。
test("双栏双语按两组填写原文与译文", () => {
  const asset: SpriteAsset = { id: "four", name: "双栏双语", kind: SpriteKind.TEXT, seconds: 3, aspect: 9 / 16, width: 1080, height: 1920, fps: 30,
    box: { x: 0, y: 0, width: 1080, height: 1920 }, parameters: [], lines: 4, bilingual: true };
  const clip: RemotionClip = { id: "clip", sprite_id: "four", start: 0, duration: 3, parameters: {}, transform: {}, text_target: "subtitle" };
  const original = newDraft().tracks[1]!;
  const chinese = { ...original, editor: { ...original.editor, subtitle: "第一组\n第二组" } };
  const english = { ...original, id: "en", language: "en" as const, editor: { ...original.editor, subtitle: "First\nSecond" } };
  const changes: string[] = [];
  const noop = () => {};
  render(<SpriteClipInspector asset={asset} clip={clip} textTrack={chinese} textTracks={[chinese, english]} duration={10}
    onSample={noop} onEnglishSample={(value) => changes.push(value)} onTextTiming={noop} onParameter={noop} onTransform={noop}
    onStart={noop} onBind={noop} onRemove={noop} onClose={noop} />);
  expect(screen.getByLabelText<HTMLInputElement>("示例文字（中文）第 2 行").value).toBe("第二组");
  expect(screen.getByLabelText<HTMLInputElement>("示例文字（英文）第 1 行").value).toBe("First");
  fireEvent.change(screen.getByLabelText("示例文字（英文）第 2 行"), { target: { value: "Changed" } });
  expect(changes).toEqual(["First\nChanged"]);
});

// 场景：目录里声明英文文字参数 textEn 的资产是双语字效，界面据此把它与普通双栏字效区分开。
test("目录把声明英文文字的资产标记为双语字效", async () => {
  const bilingual = textAsset("sprite-b");
  bilingual.parameters = [...bilingual.parameters, { key: "textEn", label: "英文文字", access: OperatorAccess.VISIBLE_EDITABLE, defaultValue: { value: { case: "stringValue", value: "Today" } }, minLength: 1, maxLength: 100 }];
  fetchMock.mockImplementation((async (_url, _init) => new Response(Uint8Array.from(toBinary(ListSpritesResponseSchema, create(ListSpritesResponseSchema, { sprites: [bilingual] }))), { headers: HEADERS })) as typeof fetch);
  const [asset] = await listSprites();
  expect(asset.bilingual).toBe(true);
});

// 场景：资产库里的资产可确认后删除；仍被模板使用时弹窗显示服务端列出的模板并保留资产，确认删除成功后目录刷新不再显示它。
test("确认后删除资产，被模板使用时说明原因", async () => {
  const catalog = [summary("sprite-1"), summary("sprite-2", SpriteKind.VIDEO_OVERLAY)];
  catalog[1].name = "冷色光斑";
  sprites({ catalog });
  const fallback = fetchMock.getMockImplementation()!;
  const deletes: string[] = [];
  fetchMock.mockImplementation((async (url, init) => {
    const path = new URL(String(url)).pathname;
    if (init?.method !== "DELETE") return fallback(url, init);
    deletes.push(path);
    if (deletes.length === 1)
      return new Response(JSON.stringify({ detail: "资产正被模板使用，请先在这些模板里移除后再删除：片头模板" }), { status: 409 });
    catalog.splice(0, 1);
    return new Response(null, { status: 204 });
  }) as typeof fetch);
  render(<><Toaster /><TemplateWorkspace selection={{ templateId: null, name: "新模板", description: "" }} onHome={() => {}} /></>);
  const region = await openAssets();
  fireEvent.click(await within(region).findByRole("button", { name: "删除 AI 资产：霓虹标题" }));
  const dialog = await screen.findByRole("dialog", { name: "删除 AI 资产" });
  expect(dialog.textContent).toContain("霓虹标题");
  fireEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));
  expect((await within(dialog).findByRole("alert")).textContent).toBe("资产正被模板使用，请先在这些模板里移除后再删除：片头模板");
  // 弹窗打开时背景对辅助技术隐藏，资产仍在目录里。
  expect(within(region).getByRole("button", { name: "添加 AI 资产：霓虹标题", hidden: true })).toBeTruthy();
  fireEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));
  // 等待元素消失不用 expect(元素).toBeNull()：失败时格式化整棵 DOM 会耗尽内存。
  await waitForElementToBeRemoved(() => screen.queryByRole("dialog", { name: "删除 AI 资产" }));
  expect(await screen.findByText("已删除资产「霓虹标题」")).toBeTruthy();
  await waitFor(() => expect(within(region).queryByRole("button", { name: "添加 AI 资产：霓虹标题" }) === null).toBe(true));
  expect(within(region).getByRole("button", { name: "添加 AI 资产：冷色光斑" })).toBeTruthy();
  expect(deletes).toEqual(["/api/sprites/sprite-1", "/api/sprites/sprite-1"]);
});

// 场景（回归）：删除失败后目录仍显示资产，再点删除只看到「sprite not found」；现在失败时刷新目录，资产已不存在（404）视为删除完成。
test("删除失败后刷新目录，资产已不存在时按删除完成处理", async () => {
  const catalog = [summary("sprite-1"), summary("sprite-2", SpriteKind.VIDEO_OVERLAY)];
  catalog[1].name = "冷色光斑";
  sprites({ catalog });
  const fallback = fetchMock.getMockImplementation()!;
  let listings = 0;
  let deletes = 0;
  fetchMock.mockImplementation((async (url, init) => {
    const path = new URL(String(url)).pathname;
    if (init?.method !== "DELETE") {
      if (path === "/api/sprites") listings++;
      return fallback(url, init);
    }
    deletes++;
    if (deletes === 1)
      return new Response(JSON.stringify({ detail: "资产文件暂时无法删除，请稍后重试" }), { status: 503 });
    // 第一次请求之后资产已被删除（例如超时后服务端完成，或其他人删掉了）。
    catalog.splice(0, 1);
    return new Response(JSON.stringify({ detail: "sprite not found" }), { status: 404 });
  }) as typeof fetch);
  render(<><Toaster /><TemplateWorkspace selection={{ templateId: null, name: "新模板", description: "" }} onHome={() => {}} /></>);
  const region = await openAssets();
  fireEvent.click(await within(region).findByRole("button", { name: "删除 AI 资产：霓虹标题" }));
  const dialog = await screen.findByRole("dialog", { name: "删除 AI 资产" });
  const before = listings;
  fireEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));
  expect((await within(dialog).findByRole("alert")).textContent).toBe("资产文件暂时无法删除，请稍后重试");
  // 一次核对资产是否其实已删除，一次刷新目录。
  await waitFor(() => expect(listings).toBe(before + 2));
  fireEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));
  await waitForElementToBeRemoved(() => screen.queryByRole("dialog", { name: "删除 AI 资产" }));
  expect(await screen.findByText("资产「霓虹标题」已被删除")).toBeTruthy();
  await waitFor(() => expect(within(region).queryByRole("button", { name: "添加 AI 资产：霓虹标题" }) === null).toBe(true));
  expect(within(region).getByRole("button", { name: "添加 AI 资产：冷色光斑" })).toBeTruthy();
  expect(deletes).toBe(2);
});

// 场景（回归）：资产库共享，资产在别处被删除后，本窗口目录原先要手动刷新才更新；现在窗口重新聚焦时重新读取。
// 右栏的字效入口也需要目录，进入页面时读一次，但资产区未展开时不订阅推送、聚焦也不再重读。
test("窗口重新聚焦时刷新 AI 资产目录", async () => {
  const catalog = [summary("sprite-1"), summary("sprite-2", SpriteKind.VIDEO_OVERLAY)];
  catalog[1].name = "冷色光斑";
  sprites({ catalog });
  const fallback = fetchMock.getMockImplementation()!;
  let listings = 0;
  fetchMock.mockImplementation((async (url, init) => {
    if (new URL(String(url)).pathname === "/api/sprites" && init?.method !== "DELETE") listings++;
    return fallback(url, init);
  }) as typeof fetch);
  render(<TemplateWorkspace selection={{ templateId: null, name: "新模板", description: "" }} onHome={() => {}} />);
  await screen.findByRole("tab", { name: "AI 资产" });
  await waitFor(() => expect(listings).toBe(1));
  fireEvent.focus(window);
  expect(listings).toBe(1);
  const region = await openAssets();
  await within(region).findByRole("button", { name: "添加 AI 资产：霓虹标题" });
  // 其他人或其他窗口删除了资产。
  catalog.splice(0, 1);
  fireEvent.focus(window);
  await waitFor(() => expect(within(region).queryByRole("button", { name: "添加 AI 资产：霓虹标题" }) === null).toBe(true));
  expect(within(region).getByRole("button", { name: "添加 AI 资产：冷色光斑" })).toBeTruthy();
});

// 场景（回归）：服务端已删除记录、随后才出错且响应被浏览器拦截（旧服务端 500 不带 CORS 头），请求以「无法连接服务端」失败；
// 原先弹窗停在这条错误上，现在核对目录，资产已不在就按删除完成关闭。资产仍在时才显示连接错误。
test("删除请求连接失败时核对目录，资产已删除则关闭弹窗", async () => {
  const catalog = [summary("sprite-1"), summary("sprite-2", SpriteKind.VIDEO_OVERLAY)];
  catalog[1].name = "冷色光斑";
  sprites({ catalog });
  const fallback = fetchMock.getMockImplementation()!;
  let deletes = 0;
  fetchMock.mockImplementation((async (url, init) => {
    if (init?.method !== "DELETE") return fallback(url, init);
    deletes++;
    // 第一次：服务端没删成，连接也断了；第二次：服务端删掉了记录，但响应没能送达。
    if (deletes === 2) catalog.splice(0, 1);
    throw new TypeError("Load failed");
  }) as typeof fetch);
  render(<><Toaster /><TemplateWorkspace selection={{ templateId: null, name: "新模板", description: "" }} onHome={() => {}} /></>);
  const region = await openAssets();
  fireEvent.click(await within(region).findByRole("button", { name: "删除 AI 资产：霓虹标题" }));
  const dialog = await screen.findByRole("dialog", { name: "删除 AI 资产" });
  fireEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));
  expect((await within(dialog).findByRole("alert")).textContent).toBe("无法连接服务端，请确认 API 已启动后重试。");
  fireEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));
  await waitForElementToBeRemoved(() => screen.queryByRole("dialog", { name: "删除 AI 资产" }));
  expect(await screen.findByText("资产「霓虹标题」已被删除")).toBeTruthy();
  await waitFor(() => expect(within(region).queryByRole("button", { name: "添加 AI 资产：霓虹标题" }) === null).toBe(true));
  expect(deletes).toBe(2);
});

// 场景：一直停在模板编辑页时，别处删除资产也要及时消失：目录在用时订阅服务端推送，收到 catalog.changed 即重新读取，离开页面断开。
test("收到目录推送后刷新 AI 资产目录，未展开时不订阅、卸载时断开", async () => {
  const catalog = [summary("sprite-1"), summary("sprite-2", SpriteKind.VIDEO_OVERLAY)];
  catalog[1].name = "冷色光斑";
  sprites({ catalog });
  const { unmount } = render(<TemplateWorkspace selection={{ templateId: null, name: "新模板", description: "" }} onHome={() => {}} />);
  await screen.findByRole("tab", { name: "AI 资产" });
  expect(FakeEventSource.instances).toHaveLength(0);
  const region = await openAssets();
  await within(region).findByRole("button", { name: "添加 AI 资产：霓虹标题" });
  expect(FakeEventSource.instances.map((source) => source.url)).toEqual(["http://api.test:8000/api/sprites/stream"]);
  const [source] = FakeEventSource.instances;
  // 其他人删除了资产，服务端推送变化；窗口始终没有失去焦点。
  catalog.splice(0, 1);
  source.emit("catalog.changed");
  await waitFor(() => expect(within(region).queryByRole("button", { name: "添加 AI 资产：霓虹标题" }) === null).toBe(true));
  expect(within(region).getByRole("button", { name: "添加 AI 资产：冷色光斑" })).toBeTruthy();
  unmount();
  expect(source.readyState).toBe(FakeEventSource.CLOSED);
});

// 回归：目录推送的同 ID 新版本必须穿过工作区映射传入叠加播放器；刷新资产不能改写当前模板草稿。
test("工作区收到覆盖事件后刷新正在使用的资产预览", async () => {
  const source = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "src")!;
  Object.defineProperty(HTMLMediaElement.prototype, "src", {
    ...source,
    set(this: HTMLMediaElement, value: string) {
      source.set!.call(this, value);
      Object.defineProperties(this, { duration: { configurable: true, value: 10 }, videoWidth: { configurable: true, value: 1080 }, videoHeight: { configurable: true, value: 1920 } });
      queueMicrotask(() => this.dispatchEvent(new Event("loadedmetadata")));
    },
  });
  try {
    const catalog = [{ ...summary(), sourceVersionId: "v1" }];
    const { template, posts } = sprites({ catalog, clips: [{ id: "placed", sprite_id: "sprite-1", start: 0, duration: 3, parameters: {}, transform: { x: 540 } }] });
    render(<TemplateWorkspace selection={{ templateId: template.template_id }} onHome={() => {}} />);
    const frame = await screen.findByTitle<HTMLIFrameElement>("AI 资产预览：霓虹标题");
    expect(new URL(frame.src).searchParams.get("version")).toBe("v1");
    catalog[0] = { ...catalog[0], sourceVersionId: "v2" };
    await act(async () => { FakeEventSource.instances.find((item) => item.url.endsWith("/api/sprites/stream"))!.emit("catalog.changed"); });
    await waitFor(() => expect(new URL(frame.src).searchParams.get("version")).toBe("v2"));
    expect(posts).toEqual([]);
    expect(screen.queryByText("有未保存的修改")).toBeNull();
  } finally {
    Object.defineProperty(HTMLMediaElement.prototype, "src", source);
  }
});

// 场景：网络切换或代理静默断开时浏览器察觉不到，45 秒收不到 ping 或变化就换一条新连接，新连接的首条事件补读断线期间的变化。
test("目录推送静默超时后重新连接", () => {
  const timers: { run: () => void; delay?: number }[] = [];
  spyOn(globalThis, "setTimeout").mockImplementation(((run: () => void, delay?: number) => {
    timers.push({ run, delay });
    return timers.length;
  }) as unknown as typeof setTimeout);
  const cleared = spyOn(globalThis, "clearTimeout").mockImplementation(() => {});
  let changes = 0;
  const stop = watchCatalog(() => changes++);
  const [first] = FakeEventSource.instances;
  expect(timers.at(-1)?.delay).toBe(CATALOG_SILENCE_MS);
  // ping 只说明连接有效：重新计时，不读取目录。
  const pending = timers.length;
  first.emit("ping");
  expect(timers.length).toBe(pending + 1);
  expect(cleared).toHaveBeenCalled();
  expect(changes).toBe(0);
  timers.at(-1)!.run();
  expect(first.readyState).toBe(FakeEventSource.CLOSED);
  expect(FakeEventSource.instances).toHaveLength(2);
  const second = FakeEventSource.instances[1];
  first.emit("catalog.changed");
  expect(changes).toBe(0);
  second.emit("catalog.changed");
  expect(changes).toBe(1);
  stop();
  expect(second.readyState).toBe(FakeEventSource.CLOSED);
});

// 场景：资产可重命名，包括当前模板正在使用的资产：服务端拒绝时原因留在弹窗里可改后重试，成功后请求只带新名称（无设备头），目录卡片和已添加片段列表都显示新名称。
test("重命名资产，失败可重试，使用中的资产也能改名", async () => {
  const catalog = [summary("sprite-1")];
  sprites({ catalog });
  const fallback = fetchMock.getMockImplementation()!;
  const renames: { path: string; name: string; device: string | null }[] = [];
  fetchMock.mockImplementation((async (url, init) => {
    const path = new URL(String(url)).pathname;
    if (!path.endsWith("/rename")) return fallback(url, init);
    renames.push({ path, name: fromBinary(RenameSpriteRequestSchema, new Uint8Array(init!.body as Uint8Array)).name, device: new Headers(init!.headers).get("X-IMV-Device") });
    if (renames.length === 1) return new Response(JSON.stringify({ detail: "名称去除首尾空白后需为 1～100 个字符" }), { status: 422 });
    catalog[0].name = renames.at(-1)!.name;
    return binary(RenameSpriteResponseSchema, create(RenameSpriteResponseSchema, { sprite: catalog[0] }));
  }) as typeof fetch);
  render(<><Toaster /><TemplateWorkspace selection={{ templateId: savedTemplate().template_id }} onHome={() => {}} /></>);
  const region = await openAssets();
  fireEvent.click(await within(region).findByRole("button", { name: "添加 AI 资产：霓虹标题" }));
  expect(await screen.findByRole("button", { name: "设置 AI 资产：霓虹标题", hidden: true })).toBeTruthy();
  fireEvent.click(within(region).getByRole("button", { name: "重命名 AI 资产：霓虹标题", hidden: true }));
  const dialog = await screen.findByRole("dialog", { name: "重命名 AI 资产" });
  const input = within(dialog).getByLabelText<HTMLInputElement>("资产名称");
  expect(input.value).toBe("霓虹标题");
  fireEvent.change(input, { target: { value: "  冷色标题  " } });
  fireEvent.click(within(dialog).getByRole("button", { name: "保存名称" }));
  expect((await within(dialog).findByRole("alert")).textContent).toBe("名称去除首尾空白后需为 1～100 个字符");
  fireEvent.click(within(dialog).getByRole("button", { name: "保存名称" }));
  // 等待元素消失不用 expect(元素).toBeNull()：失败时格式化整棵 DOM 会耗尽内存。
  await waitForElementToBeRemoved(() => screen.queryByRole("dialog", { name: "重命名 AI 资产" }));
  expect(await screen.findByText("已重命名为「冷色标题」")).toBeTruthy();
  expect(renames).toEqual([
    { path: "/api/sprites/sprite-1/rename", name: "冷色标题", device: null },
    { path: "/api/sprites/sprite-1/rename", name: "冷色标题", device: null },
  ]);
  expect(await within(region).findByRole("button", { name: "添加 AI 资产：冷色标题" })).toBeTruthy();
  expect(await screen.findByRole("button", { name: "设置 AI 资产：冷色标题" })).toBeTruthy();
});

// 场景：当前草稿（含尚未保存的添加）摆放的资产不能删除，并说明原因；否则之后保存模板会因资产未发布被拒绝。移除片段后恢复可删除。
test("当前模板摆放的资产禁止删除", async () => {
  const catalog = [summary("sprite-1"), summary("sprite-2")];
  catalog[1].name = "冷色光斑";
  sprites({ catalog });
  render(<TemplateWorkspace selection={{ templateId: savedTemplate().template_id }} onHome={() => {}} />);
  const region = await openAssets();
  /** 删除按钮随禁用状态重新挂载（禁用时不包悬停提示），每次重新查询。 */
  const remove = (name: string) => within(region).getByRole<HTMLButtonElement>("button", { name: `删除 AI 资产：${name}`, hidden: true });
  await within(region).findByRole("button", { name: "删除 AI 资产：霓虹标题" });
  expect(remove("霓虹标题").disabled).toBe(false);
  fireEvent.click(within(region).getByRole("button", { name: "添加 AI 资产：霓虹标题" }));
  await waitFor(() => expect(remove("霓虹标题").disabled).toBe(true));
  expect(remove("霓虹标题").getAttribute("aria-describedby")).toBe("sprite-in-use-sprite-1");
  expect(document.getElementById("sprite-in-use-sprite-1")!.textContent).toBe("当前模板正在使用，移除片段后才能删除");
  expect(remove("冷色光斑").disabled).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "移除 AI 资产：霓虹标题", hidden: true }));
  await waitFor(() => expect(remove("霓虹标题").disabled).toBe(false));
  expect(document.getElementById("sprite-in-use-sprite-1")).toBeNull();
  fireEvent.click(remove("霓虹标题"));
  expect(await screen.findByRole("dialog", { name: "删除 AI 资产" })).toBeTruthy();
});
