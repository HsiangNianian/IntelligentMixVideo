/** Remotion 资产核心测试：版本卡片保存到资产，以及云端模板编辑中的资产目录、片段编辑与随模板的原子保存；执行 bun run test。 */
import { expect, test } from "bun:test";
import { create, fromBinary, toBinary, toJson, type MessageInitShape } from "@bufbuild/protobuf";
import { cleanup, fireEvent, render, screen, waitFor, waitForElementToBeRemoved, within } from "@testing-library/react";
import { VersionCard } from "@/features/remotion_templates/VersionCard";
import { defaultKind, publishChoice } from "@/features/remotion_templates/spriteChoice";
import { TemplateWorkspace } from "@/features/templates/TemplateWorkspace";
import { SpriteClips } from "@/features/templates/SpritePanels";
import { getTemplate, saveTemplate } from "@/features/templates/api";
import { bindClip } from "@/features/templates/clips";
import type { SpriteAsset } from "@/features/sprites/model";
import { toDraft, type RemotionClip } from "@/features/templates/model";
import { EffectTemplateEditorSchema, GetTemplateResponseSchema, SaveTemplateRequestSchema, SaveTemplateResponseSchema } from "@/generated/imv/template/v1/template_pb";
import {
  ListSpritesResponseSchema,
  OperatorAccess,
  PublishSpriteRequestSchema,
  PublishSpriteResponseSchema,
  SpriteKind,
  SpriteParameterFormat,
  SpriteParameterSchema,
} from "@/generated/imv/sprite/v1/sprite_pb";
import { Toaster } from "@/components/ui/sonner";
import { remotionVersion } from "./remotion-fixtures";
import { fetchMock } from "./setup";
import { protobufTemplateResponse, savedTemplate } from "./fixtures";

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

/** 按路径模拟模板与 Sprite 服务：GET 返回带给定片段的已保存模板，记录每次模板保存请求和所有写入路径（posts，按发送顺序）。 */
function sprites(options: { clips?: RemotionClip[]; saveStatus?: number; saveDetail?: string; catalog?: ReturnType<typeof summary>[] } = {}) {
  const template = { ...savedTemplate(), clips: options.clips ?? [] };
  const saved: ReturnType<typeof fromBinary<typeof SaveTemplateRequestSchema>>[] = [];
  const posts: string[] = [];
  fetchMock.mockImplementation((async (url, init) => {
    const path = new URL(String(url)).pathname;
    const post = init?.method === "POST";
    if (post) posts.push(path);
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
      return protobufTemplateResponse({ ...template, clips }, "save", 201);
    }
    return protobufTemplateResponse(template, "get");
  }) as typeof fetch);
  return { template, saved, posts };
}

/** 从左侧真实资产添加一个顶部标题效果，使新模板满足 IMS 至少一个效果才能保存的要求。 */
async function addTitleEffect() {
  const assets = await screen.findByRole("region", { name: "特效资产" });
  fireEvent.click(within(assets).getByRole("radio", { name: "花字" }));
  fireEvent.click(within(assets).getAllByRole("button", { name: /^应用花字：/ })[0]);
}

// 场景：保存到资产时默认使用 Agent 判断的分类；字效固定 text 字段，声明了关键词参数时同时发布 keywords。
test("版本卡片按 Agent 判断的分类把成功版本保存为 Sprite 资产", async () => {
  let request: ReturnType<typeof fromBinary<typeof PublishSpriteRequestSchema>> | undefined;
  fetchMock.mockImplementation((async (url, init) => {
    expect(new URL(String(url)).pathname).toBe("/api/sprites/publish");
    request = fromBinary(PublishSpriteRequestSchema, new Uint8Array(init!.body as Uint8Array));
    return binary(PublishSpriteResponseSchema, create(PublishSpriteResponseSchema, { sprite: summary() }));
  }) as typeof fetch);
  const version = remotionVersion("version-2");
  version.spec.sprite_kind = "composition";
  version.spec.sprite = { kind: "text", bindings: [{ name: "text", title: "文字" }, { name: "keywords", title: "关键词" }] };
  render(<VersionCard version={version} selected={false} latest disabled={false} previewDisabled={false} onPreview={() => {}} />);
  expect(screen.getByRole("combobox", { name: "资产分类" }).textContent).toContain("字效");
  fireEvent.click(screen.getByRole("button", { name: "保存到资产" }));
  await screen.findByText("已保存到资产「霓虹标题」，可在云端模板的 Remotion 资产中添加。");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(request).toMatchObject({ sourceVersionId: "version-2", kind: SpriteKind.TEXT, textProp: "text", keywordsProp: "keywords" });
});

// 场景：用户改选的分类在版本列表刷新（同一版本换成新对象）后保留，发布请求使用它。
test("改选的发布分类在版本对象刷新后保留", async () => {
  let request: ReturnType<typeof fromBinary<typeof PublishSpriteRequestSchema>> | undefined;
  fetchMock.mockImplementation((async (_url, init) => {
    request = fromBinary(PublishSpriteRequestSchema, new Uint8Array(init!.body as Uint8Array));
    return binary(PublishSpriteResponseSchema, create(PublishSpriteResponseSchema, { sprite: summary() }));
  }) as typeof fetch);
  const version = remotionVersion("version-2");
  version.spec.sprite = { kind: "text", bindings: [{ name: "text", title: "文字" }] };
  const { rerender } = render(<VersionCard version={version} selected={false} latest disabled={false} previewDisabled={false} onPreview={() => {}} />);
  fireEvent.keyDown(screen.getByRole("combobox", { name: "资产分类" }), { key: "ArrowDown" });
  fireEvent.keyDown(await screen.findByRole("option", { name: "滤镜" }), { key: "Enter" });
  rerender(<VersionCard version={structuredClone(version)} selected={false} latest disabled={false} previewDisabled={false} onPreview={() => {}} />);
  expect(screen.getByRole("combobox", { name: "资产分类" }).textContent).toContain("滤镜");
  fireEvent.click(screen.getByRole("button", { name: "保存到资产" }));
  await waitFor(() => expect(request).toMatchObject({ kind: SpriteKind.FILTER_OVERLAY, textProp: "" }));
});

// 场景：改选其他分类时不带文字字段；没有分类的旧版本默认视频特效，由服务端说明需要先在聊天中改出新版本。
test("发布请求随所选分类变化，旧版本的拒绝原因原样显示", async () => {
  const version = remotionVersion("version-2");
  version.spec.sprite = { kind: "text", bindings: [{ name: "text", title: "文字" }] };
  expect(publishChoice(version, SpriteKind.TEXT)).toEqual({ kind: SpriteKind.TEXT, textProp: "text", keywordsProp: "" });
  expect(publishChoice(version, SpriteKind.FILTER_OVERLAY)).toEqual({ kind: SpriteKind.FILTER_OVERLAY, textProp: "", keywordsProp: "" });
  delete version.spec.sprite;
  expect(defaultKind(undefined)).toBe(SpriteKind.VIDEO_OVERLAY);
  fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: "该版本由旧版生成，没有效果分类和参数契约；请在聊天中继续修改，生成新版本后再保存到资产" }), { status: 422 }));
  const { rerender } = render(<VersionCard version={version} selected={false} latest disabled={false} previewDisabled={false} onPreview={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "保存到资产" }));
  expect((await screen.findByText(/请在聊天中继续修改/)).getAttribute("role")).toBe("alert");
  rerender(<VersionCard version={version} selected={false} latest disabled previewDisabled={false} onPreview={() => {}} />);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "保存到资产" }).disabled).toBe(true);
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
  const response = protobufTemplateResponse(template, "get");
  const message = fromBinary(GetTemplateResponseSchema, new Uint8Array(await response.arrayBuffer()));
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
      return binary(SaveTemplateResponseSchema, create(SaveTemplateResponseSchema, { template: message.template }));
    }
    return binary(GetTemplateResponseSchema, message);
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
  render(<><TemplateWorkspace selection={{ environment: "cloud", templateId: template.template_id }} onHome={() => {}} /><Toaster /></>);
  const clips = await screen.findByRole("region", { name: "已添加 Remotion 资产" });
  const item = await within(clips).findByRole("listitem", { name: "Remotion 资产：霓虹标题" });
  expect(item.textContent).toContain("等待预览时间");
  fireEvent.click(within(item).getByRole("button", { name: "移除 Remotion 资产：霓虹标题" }));
  expect(screen.getByText("有未保存的修改")).toBeTruthy();
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await screen.findByText(`模板「${template.name}」已保存`);
  expect(saved).toHaveLength(1);
  expect(wireClips(saved[0])).toHaveLength(0);
  expect(posts).toEqual(["/template"]);
});

// 场景：从目录添加资产，不出现任何配置控件；片段从 0 秒开始、长度为资产自身 3 秒，作为 remotion 轨道随模板保存，IMS 对象原样保留。
test("从目录添加资产无需配置，按资产自身长度随模板保存", async () => {
  const { template, saved, posts } = sprites();
  render(<TemplateWorkspace selection={{ environment: "cloud", templateId: template.template_id }} onHome={() => {}} />);
  const catalog = await screen.findByRole("region", { name: "Remotion 资产" });
  fireEvent.click(within(catalog).getByRole("button", { name: "展开" }));
  fireEvent.click(await within(catalog).findByRole("button", { name: "添加 Remotion 资产：霓虹标题" }));
  const item = await screen.findByRole("listitem", { name: "Remotion 资产：霓虹标题" });
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
  render(<TemplateWorkspace selection={{ environment: "cloud", templateId: template.template_id }} onHome={() => {}} />);
  const catalog = await screen.findByRole("region", { name: "Remotion 资产" });
  fireEvent.click(within(catalog).getByRole("button", { name: "展开" }));
  const add = await within(catalog).findByRole("button", { name: "添加 Remotion 资产：霓虹标题" });
  fireEvent.click(add);
  fireEvent.click(add);
  expect(await screen.findAllByRole("listitem", { name: "Remotion 资产：霓虹标题" })).toHaveLength(2);
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await waitFor(() => expect(saved).toHaveLength(1));
  const clips = wireClips(saved[0]);
  expect(clips.map((clip) => clip.spriteId)).toEqual(["sprite-1", "sprite-1"]);
  expect(new Set(clips.map((clip) => clip.id)).size).toBe(2);
});

// 场景：服务端拒绝保存（例如引用了未发布的资产）：提示原因、保留未保存状态，不自动重发。
test("保存被拒绝时提示原因并保留修改", async () => {
  const { template, posts } = sprites({
    saveStatus: 422, saveDetail: "Remotion 资产尚未发布：sprite-1",
    clips: [{ id: "sprite-p1", sprite_id: "sprite-1", start: 0, duration: 3, parameters: {}, transform: {} }],
  });
  render(<TemplateWorkspace selection={{ environment: "cloud", templateId: template.template_id }} onHome={() => {}} />);
  const item = await screen.findByRole("listitem", { name: "Remotion 资产：霓虹标题" });
  fireEvent.click(within(item).getByRole("button", { name: "移除 Remotion 资产：霓虹标题" }));
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await screen.findByText("Remotion 资产尚未发布：sprite-1");
  expect(screen.getByText("有未保存的修改")).toBeTruthy();
  expect(posts).toEqual(["/template"]);
});

// 场景：新模板只放 Remotion 片段、没有任何 IMS 效果：一次请求即可创建，效果列表为空。
test("新模板只放 Remotion 片段也能保存", async () => {
  const { saved, posts } = sprites();
  render(<TemplateWorkspace selection={{ environment: "cloud", templateId: null, name: "新模板", description: "" }} onHome={() => {}} />);
  const catalog = await screen.findByRole("region", { name: "Remotion 资产" });
  fireEvent.click(within(catalog).getByRole("button", { name: "展开" }));
  fireEvent.click(await within(catalog).findByRole("button", { name: "添加 Remotion 资产：霓虹标题" }));
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
  render(<TemplateWorkspace selection={{ environment: "cloud", templateId: null, name: "空模板", description: "" }} onHome={() => {}} />);
  await screen.findByRole("region", { name: "Remotion 资产" });
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await waitFor(() => expect(posts).toEqual(["/template"]));
});

// 场景：新模板同时有 IMS 效果和 Remotion 片段：只发一次保存请求（原子），IMS 对象在前、片段在后。
test("新模板的效果和 Remotion 片段在同一次请求里保存", async () => {
  const { saved, posts } = sprites();
  render(<TemplateWorkspace selection={{ environment: "cloud", templateId: null, name: "新模板", description: "" }} onHome={() => {}} />);
  await addTitleEffect();
  const catalog = await screen.findByRole("region", { name: "Remotion 资产" });
  fireEvent.click(within(catalog).getByRole("button", { name: "展开" }));
  fireEvent.click(await within(catalog).findByRole("button", { name: "添加 Remotion 资产：霓虹标题" }));
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
  render(<TemplateWorkspace selection={{ environment: "cloud", templateId: template.template_id }} onHome={() => {}} />);
  const item = await screen.findByRole("listitem", { name: "Remotion 资产：未找到的资产" });
  await waitFor(() => expect(item.textContent).toContain("资产已不可用，保存前请移除"));
});

// 场景：本地模板不能添加 Remotion 资产，只说明原因且不请求目录。
test("本地模板不请求也不显示 Remotion 资产", async () => {
  sprites();
  render(<TemplateWorkspace selection={{ environment: "local", templateId: null, name: "本地", description: "" }} onHome={() => {}} />);
  const catalog = await screen.findByRole("region", { name: "Remotion 资产" });
  expect(within(catalog).getByText("Remotion 资产只能添加到云端模板。")).toBeTruthy();
  expect(screen.queryByRole("region", { name: "已添加 Remotion 资产" })).toBeNull();
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/api/sprites"))).toBe(false);
});

// 场景：叠加渲染要求画布一致：目录里画布不同的资产在添加时就被提示并拒绝，模板里仍只有第一个片段，目录中显示各资产画布。
test("添加画布不一致的资产时提示原因", async () => {
  const landscape = { ...summary("sprite-2"), name: "横版标题", canvas: { width: 1920, height: 1080, fps: 30, previewFrames: 90 } };
  const { template } = sprites({ catalog: [summary(), landscape] });
  render(<TemplateWorkspace selection={{ environment: "cloud", templateId: template.template_id }} onHome={() => {}} />);
  const catalog = await screen.findByRole("region", { name: "Remotion 资产" });
  fireEvent.click(within(catalog).getByRole("button", { name: "展开" }));
  expect((await within(catalog).findAllByText(/1080×1920 @ 30 FPS/)).length).toBe(1);
  expect(within(catalog).getAllByText(/1920×1080 @ 30 FPS/).length).toBe(1);
  fireEvent.click(await within(catalog).findByRole("button", { name: "添加 Remotion 资产：霓虹标题" }));
  fireEvent.click(await within(catalog).findByRole("button", { name: "添加 Remotion 资产：横版标题" }));
  await screen.findByText(/「横版标题」的画布（1920×1080 @ 30 FPS）与已添加的「霓虹标题」（1080×1920 @ 30 FPS）不一致/);
  fireEvent.click(screen.getByRole("button", { name: "知道了" }));
  expect(screen.getAllByRole("listitem", { name: /^Remotion 资产：/ })).toHaveLength(1);
});


// 场景：字效作用于当前选择的文字对象（默认标题），不另开时间轴行：列表注明作用对象，设置里不再单独提供文字与关键词；改为作用于底部字幕后随模板保存。
test("字效作用于文字对象并可改为字幕", async () => {
  const { template, saved } = sprites({ catalog: [textAsset()] });
  render(<TemplateWorkspace selection={{ environment: "cloud", templateId: template.template_id }} onHome={() => {}} />);
  const catalog = await screen.findByRole("region", { name: "Remotion 资产" });
  fireEvent.click(within(catalog).getByRole("button", { name: "展开" }));
  fireEvent.click(await within(catalog).findByRole("button", { name: "添加 Remotion 资产：金色标题" }));
  const item = await screen.findByRole("listitem", { name: "Remotion 资产：金色标题" });
  expect(item.textContent).toContain("顶部标题字效");
  fireEvent.click(within(item).getByRole("button", { name: "设置 Remotion 资产：金色标题" }));
  const inspector = await screen.findByRole("region", { name: "Remotion 资产设置" });
  expect(within(inspector).queryByLabelText("标题文字")).toBeNull();
  expect(within(inspector).queryByRole("combobox", { name: "Remotion 合成用途" })).toBeNull();
  expect(within(inspector).getByLabelText("字号")).toBeTruthy();
  fireEvent.click(within(inspector).getByRole("radio", { name: "底部字幕" }));
  await waitFor(() => expect(screen.getByRole("listitem", { name: "Remotion 资产：金色标题" }).textContent).toContain("底部字幕字效"));
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await waitFor(() => expect(saved).toHaveLength(1));
  expect(wireClips(saved[0]).map((clip) => [clip.textTarget, clip.transitionBoundary])).toEqual([["subtitle", undefined]]);
});

// 场景：字效作用的标题在当前预览里没有显示时间，标题被隐藏，列表里的字效也标为未应用，不显示原有区间。
test("所作用文字不在预览内时字效标为未应用", () => {
  const asset: SpriteAsset = { id: "sprite-t", name: "金色标题", kind: SpriteKind.TEXT, seconds: 3, aspect: 9 / 16, width: 1080, height: 1920, fps: 30, box: { x: 0, y: 0, width: 1080, height: 1920 }, parameters: [] };
  const clip: RemotionClip = { id: "clip-t", sprite_id: "sprite-t", start: 0, duration: 3, parameters: {}, transform: {}, text_target: "title" };
  const props: Parameters<typeof SpriteClips>[0] = { environment: "cloud", clips: [clip], catalog: { assets: [asset], error: "", refresh: () => {} }, duration: 30, selected: null, onSelect: () => {}, onRemove: () => {} };
  const { rerender } = render(<SpriteClips {...props} />);
  expect(screen.getByRole("listitem", { name: "Remotion 资产：金色标题" }).textContent).toContain("顶部标题字效 · 0～3 秒");
  rerender(<SpriteClips {...props} hidden={new Set([clip.id])} />);
  expect(screen.getByRole("listitem", { name: "Remotion 资产：金色标题" }).textContent).toContain("顶部标题字效 · 当前视频未应用");
});

/** 打开带一个字效片段的已保存模板，并选中该片段。 */
async function openTextClip(clip: Partial<RemotionClip> = {}) {
  const fixture = sprites({
    catalog: [textAsset(), summary("sprite-f", SpriteKind.FILTER_OVERLAY), summary("sprite-v")],
    clips: [{ id: "clip-t", sprite_id: "sprite-t", start: 0, duration: 3, parameters: {}, transform: {}, ...clip }],
  });
  const remotion = { opened: 0 };
  render(<><TemplateWorkspace selection={{ environment: "cloud", templateId: fixture.template.template_id }} onHome={() => {}} onOpenRemotion={() => { remotion.opened += 1; }} /><Toaster /></>);
  const item = await screen.findByRole("listitem", { name: "Remotion 资产：金色标题" });
  fireEvent.click(within(item).getByRole("button", { name: "设置 Remotion 资产：金色标题" }));
  const inspector = await screen.findByRole("region", { name: "Remotion 资产设置" });
  return { ...fixture, inspector, remotion };
}

// 场景：资产库按字效、滤镜、转场与视频特效分类，显示各类数量，默认打开第一个有资产的分类。
test("Remotion 资产目录按三类分栏", async () => {
  sprites({ catalog: [textAsset(), summary("sprite-f", SpriteKind.FILTER_OVERLAY), summary("sprite-v"), summary("sprite-x", SpriteKind.TRANSITION_OVERLAY)] });
  render(<TemplateWorkspace selection={{ environment: "cloud", templateId: null, name: "新模板", description: "" }} onHome={() => {}} />);
  const catalog = await screen.findByRole("region", { name: "Remotion 资产" });
  fireEvent.click(within(catalog).getByRole("button", { name: "展开" }));
  const tabs = await within(catalog).findByRole("radiogroup", { name: "Remotion 资产分类" });
  expect(within(tabs).getAllByRole("radio").map((item) => item.textContent)).toEqual(["字效 1", "滤镜 1", "转场与视频特效 2"]);
  expect(within(catalog).getByRole("button", { name: "添加 Remotion 资产：金色标题" })).toBeTruthy();
  fireEvent.click(within(tabs).getByRole("radio", { name: /转场与视频特效/ }));
  expect(within(catalog).getAllByRole("button", { name: "添加 Remotion 资产：霓虹标题" })).toHaveLength(2);
  expect(within(catalog).queryByRole("button", { name: "添加 Remotion 资产：金色标题" })).toBeNull();
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
  expect(within(screen.getByRole("listitem", { name: "Remotion 资产：金色标题" })).getByText(/已调整参数/)).toBeTruthy();
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
  const inspector = await screen.findByRole("region", { name: "Remotion 资产设置" });
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
  render(<TemplateWorkspace selection={{ environment: "cloud", templateId: savedTemplate().template_id }} onHome={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "设置 Remotion 资产：霓虹标题" }));
  expect(screen.queryByRole("tab", { name: "关键词设置" })).toBeNull();
  cleanup();
  // 审计复现：只带 keywords 的旧字效曾不提示重新生成，只显示缺少样式控件的页签。
  const text = textAsset().parameters;
  for (const parameters of [[text[0]], text.filter((item) => item.key === "text" || item.key === "keywords")]) {
    sprites({ catalog: [summary("sprite-o", SpriteKind.TEXT, true, parameters)], clips: [{ id: "clip-o", sprite_id: "sprite-o", start: 0, duration: 3, parameters: {}, transform: {} }] });
    render(<TemplateWorkspace selection={{ environment: "cloud", templateId: savedTemplate().template_id }} onHome={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "设置 Remotion 资产：霓虹标题" }));
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
  render(<TemplateWorkspace selection={{ environment: "cloud", templateId: savedTemplate().template_id }} onHome={() => {}} />);
  const [first, second] = await screen.findAllByRole("button", { name: "设置 Remotion 资产：金色标题" });
  fireEvent.click(first);
  fireEvent.click(screen.getByRole("tab", { name: "关键词设置" }));
  expect(screen.getByRole("tab", { name: "关键词设置" }).getAttribute("aria-selected")).toBe("true");
  // 审计复现：右栏不随片段重建时，上一个片段选中的页签会带到下一个片段。
  fireEvent.click(second);
  expect(screen.getByRole("tab", { name: "外观与位置" }).getAttribute("aria-selected")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "设置 Remotion 资产：霓虹标题" }));
  const inspector = screen.getByRole("region", { name: "Remotion 资产设置" });
  expect(within(inspector).queryByRole("tab")).toBeNull();
  // 非字效没有关键词页签，以 keyword 开头的参数同其他内部参数一样回字效工作区改。
  expect(within(inspector).queryByLabelText("关键光晕")).toBeNull();
  expect(within(inspector).getByRole("heading", { name: "资产默认参数" }).closest("fieldset")!.textContent).toContain("关键光晕");
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
  const catalog = screen.getByRole("region", { name: "Remotion 资产" });
  fireEvent.click(within(catalog).getByRole("button", { name: "展开" }));
  fireEvent.click(await within(catalog).findByRole("radio", { name: /滤镜/ }));
  fireEvent.click(within(catalog).getByRole("button", { name: "添加 Remotion 资产：霓虹标题" }));
  const added = (await screen.findAllByRole("button", { name: "设置 Remotion 资产：霓虹标题" }))[0];
  fireEvent.click(added);
  const filter = await screen.findByRole("region", { name: "Remotion 资产设置" });
  expect(within(filter).getByLabelText("强度")).toBeTruthy();
  expect(within(filter).queryByLabelText("水平位置 %")).toBeNull();
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
  render(<><Toaster /><TemplateWorkspace selection={{ environment: "cloud", templateId: null, name: "新模板", description: "" }} onHome={() => {}} /></>);
  const region = await screen.findByRole("region", { name: "Remotion 资产" });
  fireEvent.click(within(region).getByRole("button", { name: "展开" }));
  fireEvent.click(await within(region).findByRole("button", { name: "删除 Remotion 资产：霓虹标题" }));
  const dialog = await screen.findByRole("dialog", { name: "删除 Remotion 资产" });
  expect(dialog.textContent).toContain("霓虹标题");
  fireEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));
  expect((await within(dialog).findByRole("alert")).textContent).toBe("资产正被模板使用，请先在这些模板里移除后再删除：片头模板");
  // 弹窗打开时背景对辅助技术隐藏，资产仍在目录里。
  expect(within(region).getByRole("button", { name: "添加 Remotion 资产：霓虹标题", hidden: true })).toBeTruthy();
  fireEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));
  // 等待元素消失不用 expect(元素).toBeNull()：失败时格式化整棵 DOM 会耗尽内存。
  await waitForElementToBeRemoved(() => screen.queryByRole("dialog", { name: "删除 Remotion 资产" }));
  expect(await screen.findByText("已删除资产「霓虹标题」")).toBeTruthy();
  await waitFor(() => expect(within(region).queryByRole("button", { name: "添加 Remotion 资产：霓虹标题" }) === null).toBe(true));
  expect(within(region).getByRole("button", { name: "添加 Remotion 资产：冷色光斑" })).toBeTruthy();
  expect(deletes).toEqual(["/api/sprites/sprite-1", "/api/sprites/sprite-1"]);
});

// 场景：当前草稿（含尚未保存的添加）摆放的资产不能删除，并说明原因；否则之后保存模板会因资产未发布被拒绝。移除片段后恢复可删除。
test("当前模板摆放的资产禁止删除", async () => {
  const catalog = [summary("sprite-1"), summary("sprite-2")];
  catalog[1].name = "冷色光斑";
  sprites({ catalog });
  render(<TemplateWorkspace selection={{ environment: "cloud", templateId: savedTemplate().template_id }} onHome={() => {}} />);
  const region = await screen.findByRole("region", { name: "Remotion 资产" });
  fireEvent.click(within(region).getByRole("button", { name: "展开" }));
  /** 删除按钮随禁用状态重新挂载（禁用时不包悬停提示），每次重新查询。 */
  const remove = (name: string) => within(region).getByRole<HTMLButtonElement>("button", { name: `删除 Remotion 资产：${name}`, hidden: true });
  await within(region).findByRole("button", { name: "删除 Remotion 资产：霓虹标题" });
  expect(remove("霓虹标题").disabled).toBe(false);
  fireEvent.click(within(region).getByRole("button", { name: "添加 Remotion 资产：霓虹标题" }));
  await waitFor(() => expect(remove("霓虹标题").disabled).toBe(true));
  expect(remove("霓虹标题").getAttribute("aria-describedby")).toBe("sprite-in-use-sprite-1");
  expect(document.getElementById("sprite-in-use-sprite-1")!.textContent).toBe("当前模板正在使用，移除片段后才能删除");
  expect(remove("冷色光斑").disabled).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "移除 Remotion 资产：霓虹标题", hidden: true }));
  await waitFor(() => expect(remove("霓虹标题").disabled).toBe(false));
  expect(document.getElementById("sprite-in-use-sprite-1")).toBeNull();
  fireEvent.click(remove("霓虹标题"));
  expect(await screen.findByRole("dialog", { name: "删除 Remotion 资产" })).toBeTruthy();
});
