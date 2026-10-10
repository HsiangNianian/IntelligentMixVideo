/** Remotion 资产核心测试：版本卡片保存到资产，以及云端模板编辑中的资产目录、绑定编辑与保存；执行 bun run test。 */
import { expect, test } from "bun:test";
import { create, fromBinary, toBinary } from "@bufbuild/protobuf";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { VersionCard } from "@/features/remotion_templates/VersionCard";
import { TemplateWorkspace } from "@/features/templates/TemplateWorkspace";
import {
  GetStyleSpritesResponseSchema,
  ListSpritesResponseSchema,
  PublishSpriteRequestSchema,
  PublishSpriteResponseSchema,
  SaveStyleSpritesRequestSchema,
  SaveStyleSpritesResponseSchema,
  SpriteKind,
  SpriteTarget,
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

/** 按路径模拟模板与 Sprite 服务，记录收到的 Remotion 片段保存请求和所有写入路径（posts，按发送顺序）。 */
function sprites(options: { bindings?: object[]; revision?: bigint; saveStatus?: number } = {}) {
  const template = savedTemplate();
  const saved: ReturnType<typeof fromBinary<typeof SaveStyleSpritesRequestSchema>>[] = [];
  const posts: string[] = [];
  fetchMock.mockImplementation((async (url, init) => {
    const path = new URL(String(url)).pathname;
    const post = init?.method === "POST";
    if (post) posts.push(path);
    if (path === "/api/sprites") return binary(ListSpritesResponseSchema, create(ListSpritesResponseSchema, { sprites: [summary()] }));
    if (path === `/api/sprites/styles/${template.template_id}` && !post)
      return binary(GetStyleSpritesResponseSchema, create(GetStyleSpritesResponseSchema, { bindings: { styleId: template.template_id, revision: options.revision ?? 0n, placements: options.bindings ?? [] } }));
    if (path === `/api/sprites/styles/${template.template_id}`) {
      const request = fromBinary(SaveStyleSpritesRequestSchema, new Uint8Array(init!.body as Uint8Array));
      saved.push(request);
      if (options.saveStatus)
        return new Response(JSON.stringify({ detail: "Remotion 片段已被其他修改更新，请刷新后重试" }), { status: options.saveStatus });
      return binary(SaveStyleSpritesResponseSchema, create(SaveStyleSpritesResponseSchema, { bindings: { styleId: template.template_id, revision: request.expectedRevision + 1n, placements: request.placements } }));
    }
    return protobufTemplateResponse(template, post ? "save" : "get");
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

// 场景：已有片段读取后在右侧列出名称与时间；移除后只保存 Remotion 片段（带着读取到的版本号），不重新提交 IMS 模板。
test("已有片段显示在列表中，移除后只保存 Remotion 片段", async () => {
  const { template, saved, posts } = sprites({
    revision: 3n,
    bindings: [{ id: "sprite-p1", spriteId: "sprite-1", startMode: "seconds", start: 2, duration: 3, order: 0 }],
  });
  render(<><TemplateWorkspace selection={{ environment: "cloud", templateId: template.template_id }} onHome={() => {}} /><Toaster /></>);
  const bound = await screen.findByRole("region", { name: "已添加 Remotion 资产" });
  const item = await within(bound).findByRole("listitem", { name: "Remotion 资产：霓虹标题" });
  expect(item.textContent).toContain("2～5 秒");
  fireEvent.click(within(item).getByRole("button", { name: "移除 Remotion 资产：霓虹标题" }));
  expect(screen.getByText("有未保存的修改")).toBeTruthy();
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await screen.findByText(`模板「${template.name}」已保存`);
  expect(saved).toHaveLength(1);
  expect(saved[0].expectedRevision).toBe(3n);
  expect(saved[0].placements).toHaveLength(0);
  expect(posts).toEqual([`/api/sprites/styles/${template.template_id}`]);
});

// 场景：从目录添加资产，不出现任何配置控件；片段从 0 秒开始、长度为资产自身 3 秒，只按固定值写入，不带作用对象或覆盖。
test("从目录添加资产无需配置，按资产自身长度保存", async () => {
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
  expect(saved[0].expectedRevision).toBe(0n);
  expect(saved[0].placements[0]).toMatchObject({ spriteId: "sprite-1", startMode: "seconds", start: 0, duration: 3, order: 0 });
  expect(saved[0].placements[0].target).toBe(SpriteTarget.UNSPECIFIED);
  expect(saved[0].placements[0].overrides).toHaveLength(0);
  expect(posts).toEqual([`/api/sprites/styles/${template.template_id}`]);
});

// 场景：只改了 Remotion 片段而保存冲突：提示原因、保留未保存状态，没有重新提交 IMS 模板，也不静默覆盖。
test("只改片段时保存失败，提示原因并保留修改", async () => {
  const { template, posts } = sprites({
    saveStatus: 409,
    bindings: [{ id: "sprite-p1", spriteId: "sprite-1", startMode: "seconds", start: 0, duration: 3, order: 0 }],
  });
  render(<TemplateWorkspace selection={{ environment: "cloud", templateId: template.template_id }} onHome={() => {}} />);
  const item = await screen.findByRole("listitem", { name: "Remotion 资产：霓虹标题" });
  fireEvent.click(within(item).getByRole("button", { name: "移除 Remotion 资产：霓虹标题" }));
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await screen.findByText("Remotion 资产绑定保存失败：Remotion 片段已被其他修改更新，请刷新后重试");
  expect(screen.getByText("有未保存的修改")).toBeTruthy();
  expect(posts).toEqual([`/api/sprites/styles/${template.template_id}`]);
});

// 场景：模板和片段都有修改时各自保存：先提交 IMS 模板，片段冲突时提示模板已保存，片段仍保留为未保存。
test("模板和片段都改了时先保存模板，片段失败时提示模板已保存", async () => {
  const { template, posts } = sprites({
    saveStatus: 409,
    bindings: [{ id: "sprite-p1", spriteId: "sprite-1", startMode: "seconds", start: 0, duration: 3, order: 0 }],
  });
  render(<TemplateWorkspace selection={{ environment: "cloud", templateId: template.template_id }} onHome={() => {}} />);
  const item = await screen.findByRole("listitem", { name: "Remotion 资产：霓虹标题" });
  fireEvent.click(within(item).getByRole("button", { name: "移除 Remotion 资产：霓虹标题" }));
  fireEvent.click(screen.getByRole("button", { name: "编辑底部字幕" }));
  fireEvent.click(screen.getByRole("tab", { name: "外观与效果" }));
  fireEvent.click(screen.getByRole("button", { name: "移除当前画面对象" }));
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await screen.findByText("模板已保存，但 Remotion 资产绑定保存失败：Remotion 片段已被其他修改更新，请刷新后重试");
  expect(screen.getByText("有未保存的修改")).toBeTruthy();
  expect(posts).toEqual(["/template", `/api/sprites/styles/${template.template_id}`]);
});

// 场景：新模板只放 Remotion 片段、没有任何 IMS 效果：照样先创建模板，再用返回的 ID 保存片段。
test("新模板只放 Remotion 片段也能保存", async () => {
  const { template, saved, posts } = sprites();
  render(<TemplateWorkspace selection={{ environment: "cloud", templateId: null, name: "新模板", description: "" }} onHome={() => {}} />);
  const catalog = await screen.findByRole("region", { name: "Remotion 资产" });
  fireEvent.click(within(catalog).getByRole("button", { name: "展开" }));
  fireEvent.click(await within(catalog).findByRole("button", { name: "添加 Remotion 资产：霓虹标题" }));
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await waitFor(() => expect(saved).toHaveLength(1));
  expect(screen.queryByText("请至少选择一个效果")).toBeNull();
  expect(posts).toEqual(["/template", `/api/sprites/styles/${template.template_id}`]);
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

// 场景：新模板还没有 ID，必须先创建模板；片段随后用返回的 ID 保存。
test("新模板先创建再用返回的 ID 保存 Remotion 片段", async () => {
  const { template, saved, posts } = sprites();
  render(<TemplateWorkspace selection={{ environment: "cloud", templateId: null, name: "新模板", description: "" }} onHome={() => {}} />);
  await addTitleEffect();
  const catalog = await screen.findByRole("region", { name: "Remotion 资产" });
  fireEvent.click(within(catalog).getByRole("button", { name: "展开" }));
  fireEvent.click(await within(catalog).findByRole("button", { name: "添加 Remotion 资产：霓虹标题" }));
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await waitFor(() => expect(saved).toHaveLength(1));
  expect(posts).toEqual(["/template", `/api/sprites/styles/${template.template_id}`]);
  expect(saved[0].styleId).toBe(template.template_id);
});

// 场景：本地模板不能添加 Remotion 资产，只说明原因且不请求目录或绑定。
test("本地模板不请求也不显示 Remotion 资产", async () => {
  sprites();
  render(<TemplateWorkspace selection={{ environment: "local", templateId: null, name: "本地", description: "" }} onHome={() => {}} />);
  const catalog = await screen.findByRole("region", { name: "Remotion 资产" });
  expect(within(catalog).getByText("Remotion 资产只能添加到云端模板。")).toBeTruthy();
  expect(screen.queryByRole("region", { name: "已添加 Remotion 资产" })).toBeNull();
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/api/sprites"))).toBe(false);
});
