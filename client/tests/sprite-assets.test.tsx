/** Remotion 资产核心测试：版本卡片保存到资产，以及云端模板编辑中的资产目录、片段编辑与随模板的原子保存；执行 bun run test。 */
import { expect, test } from "bun:test";
import { create, fromBinary, toBinary, toJson } from "@bufbuild/protobuf";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { VersionCard } from "@/features/remotion_templates/VersionCard";
import { TemplateWorkspace } from "@/features/templates/TemplateWorkspace";
import type { RemotionClip } from "@/features/templates/model";
import { EffectTemplateEditorSchema, SaveTemplateRequestSchema } from "@/generated/imv/template/v1/template_pb";
import {
  ListSpritesResponseSchema,
  PublishSpriteRequestSchema,
  PublishSpriteResponseSchema,
  SpriteKind,
} from "@/generated/imv/sprite/v1/sprite_pb";
import { Toaster } from "@/components/ui/sonner";
import { remotionVersion } from "./remotion-fixtures";
import { fetchMock } from "./setup";
import { protobufTemplateResponse, savedTemplate } from "./fixtures";

const HEADERS = { "Content-Type": "application/x-protobuf" };

/** 一个 3 秒（90 帧、30 FPS）的资产摘要。 */
function summary(id = "sprite-1", kind = SpriteKind.VIDEO_OVERLAY, keywords = false) {
  return {
    spriteId: id,
    name: "霓虹标题",
    kind,
    keywordsSupported: keywords,
    previewUrl: `/api/sprites/${id}/preview`,
    canvas: { width: 1080, height: 1920, fps: 30, previewFrames: 90 },
  };
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
        .map((track): RemotionClip => ({ id: track.id, sprite_id: track.spriteId, start: track.start ?? 0, duration: track.duration ?? null }));
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

// 场景：新生成的组合版本一键保存为资产，不弹窗、不要求选择；请求只带版本和视频叠加类型。
test("版本卡片一键把成功版本保存为 Sprite 资产", async () => {
  let request: ReturnType<typeof fromBinary<typeof PublishSpriteRequestSchema>> | undefined;
  fetchMock.mockImplementation((async (url, init) => {
    expect(new URL(String(url)).pathname).toBe("/api/sprites/publish");
    request = fromBinary(PublishSpriteRequestSchema, new Uint8Array(init!.body as Uint8Array));
    return binary(PublishSpriteResponseSchema, create(PublishSpriteResponseSchema, { sprite: summary() }));
  }) as typeof fetch);
  const version = remotionVersion("version-2");
  version.spec.sprite_kind = "composition";
  render(<VersionCard version={version} selected={false} latest disabled={false} previewDisabled={false} onPreview={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "保存到资产" }));
  await screen.findByText("已保存到资产「霓虹标题」，可在云端模板的 Remotion 资产中添加。");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(request).toMatchObject({ sourceVersionId: "version-2", kind: SpriteKind.VIDEO_OVERLAY, textProp: "", keywordsProp: "" });
});

// 场景：旧的文字版本沿用文字类型并自动取嵌套的标题字段；失败显示服务端原因，未保存参数时入口禁用。
test("旧文字版本自动选择文字字段，失败显示原因，未保存参数时禁用", async () => {
  fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: "accepted artifact unavailable" }), { status: 409 }));
  const version = remotionVersion("version-2");
  version.spec.sprite_kind = "text";
  version.candidate.config_schema = { properties: { title_main: { type: "object", properties: { title: { type: "string" }, fontSize: { type: "number" } } } } };
  const { rerender } = render(<VersionCard version={version} selected={false} latest disabled={false} previewDisabled={false} onPreview={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "保存到资产" }));
  expect((await screen.findByText("accepted artifact unavailable")).getAttribute("role")).toBe("alert");
  const body = fetchMock.mock.calls[0][1]!.body as Uint8Array;
  expect(fromBinary(PublishSpriteRequestSchema, new Uint8Array(body))).toMatchObject({ kind: SpriteKind.TEXT, textProp: "title_main.title" });
  rerender(<VersionCard version={version} selected={false} latest disabled previewDisabled={false} onPreview={() => {}} />);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "保存到资产" }).disabled).toBe(true);
});


/** 保存请求里的 remotion 轨道，按线路格式展开为便于断言的数组。 */
function wireClips(request: ReturnType<typeof fromBinary<typeof SaveTemplateRequestSchema>>) {
  return request.tracks!.tracks.filter((track) => track.target === "remotion");
}

// 场景：已有片段随模板读取后在右侧列出名称与时间；移除后随模板一次保存，请求里不再有 remotion 轨道，也没有任何绑定请求。
test("已有片段显示在列表中，移除后随模板保存", async () => {
  const { template, saved, posts } = sprites({ clips: [{ id: "sprite-p1", sprite_id: "sprite-1", start: 2, duration: 3 }] });
  render(<><TemplateWorkspace selection={{ environment: "cloud", templateId: template.template_id }} onHome={() => {}} /><Toaster /></>);
  const clips = await screen.findByRole("region", { name: "已添加 Remotion 资产" });
  const item = await within(clips).findByRole("listitem", { name: "Remotion 资产：霓虹标题" });
  expect(item.textContent).toContain("2～5 秒");
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
  expect(item.textContent).toContain("0～3 秒");
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await waitFor(() => expect(saved).toHaveLength(1));
  const [clip] = wireClips(saved[0]);
  expect(clip).toMatchObject({ target: "remotion", spriteId: "sprite-1", startMode: "seconds", start: 0, duration: 3 });
  expect(toJson(EffectTemplateEditorSchema, clip.editor!)).toEqual({});
  expect(saved[0].tracks!.tracks.map((track) => track.target)).toEqual(["title", "subtitle", "remotion"]);
  expect(posts).toEqual(["/template"]);
});

// 场景：同一个资产可以多次添加：每次都是独立的摆放（不同的轨道 ID），列表和保存请求里各有一条。
test("同一个资产可以添加多次", async () => {
  const { template, saved } = sprites();
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
    clips: [{ id: "sprite-p1", sprite_id: "sprite-1", start: 0, duration: 3 }],
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

// 场景：既没有 IMS 效果也没有 Remotion 片段的新模板仍然不能保存，不发出任何写请求。
test("没有效果也没有 Remotion 片段时仍不能保存", async () => {
  const { posts } = sprites();
  render(<TemplateWorkspace selection={{ environment: "cloud", templateId: null, name: "空模板", description: "" }} onHome={() => {}} />);
  await screen.findByRole("region", { name: "Remotion 资产" });
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await screen.findByText("请至少选择一个效果");
  expect(posts).toEqual([]);
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
  const { template } = sprites({ clips: [{ id: "sprite-p1", sprite_id: "sprite-gone", start: 0, duration: 3 }] });
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
  expect(screen.getAllByRole("listitem", { name: /^Remotion 资产：/ })).toHaveLength(1);
});
