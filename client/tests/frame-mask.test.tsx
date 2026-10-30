/** 蒙版保存边界、面板与图层顺序；整数像素案例与后端生成 PNG 共用。 */
import { expect, spyOn, test } from "bun:test";
import { fromBinary } from "@bufbuild/protobuf";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SaveTemplateRequestSchema, type SaveTemplateRequest } from "@/generated/imv/template/v1/template_pb";
import { AppliedEffects } from "@/features/templates/EffectAssets";
import { FrameMaskCard } from "@/features/templates/FrameMaskPanel";
import { TemplateWorkspace } from "@/features/templates/TemplateWorkspace";
import { PreviewTimeline } from "@/features/templates/PreviewTimeline";
import { frameMaskRects, newFrameMask, validateFrameMask, type FrameMask } from "@/features/templates/frameMask";
import { newDraft, toDraft } from "@/features/templates/model";
import { saveTemplate, getTemplate } from "@/features/templates/api";
import { defaultSplitScreen } from "@/features/templates/splitScreen";
import { defaultAvatarEffects } from "@/features/templates/avatarEffects";
import { readCatalog } from "@/features/templates/sdk";
import { addTrack, removeTrack } from "@/features/templates/tracks";
import { buildPreviewRows, mergeSpriteRows, playerTimeline } from "@/features/templates/timeline";
import { buildTestTimeline, jsonTemplateResponse, savedTemplate } from "./fixtures";
import { fetchMock } from "./setup";
import cases from "../../server/tests/frame_mask_cases.json";

test.each(cases.geometry)("蒙版按成片高度 $height 生成相同整数边界", ({ height, top, bottom, expected }) => {
  const mask = newFrameMask();
  mask.top.height = top;
  mask.bottom.height = bottom;
  expect(frameMaskRects(mask, height).map((rect) => [rect.y, rect.height])).toEqual(expected);
});

// 素材卡片反映两栏独立的方向与范围；切回纯色后恢复单色，对象说明不再限定纯色。
test("蒙版卡片随渐变设置更新，切回纯色保留起始色", () => {
  const mask = newFrameMask();
  mask.top.gradient = { enabled: true, endColor: "#abcdef", direction: "up", range: 50 };
  mask.bottom.gradient = { enabled: true, endColor: "#123456", direction: "down", range: 100 };
  const view = render(<FrameMaskCard mask={mask} onSelect={() => {}} />);
  const bars = screen.getByRole("button", { name: "选择上下蒙版" }).querySelectorAll<HTMLElement>("[style]");
  expect(bars[0].style.background).toBe("linear-gradient(to top, #0E2C42 0%, #abcdef 50%)");
  expect(bars[1].style.background).toBe("linear-gradient(to bottom, #0E2C42 0%, #123456 100%)");
  view.rerender(<FrameMaskCard mask={{ ...mask, top: { ...mask.top, gradient: { ...mask.top.gradient, enabled: false } } }} onSelect={() => {}} />);
  expect(bars[0].style.background).toBe("#0E2C42");
  render(<AppliedEffects draft={{ ...newDraft(), frame_mask: mask }} catalog={[]} selected={null} onSelect={() => {}} />);
  expect(screen.getByRole("button", { name: "编辑上下蒙版对象" }).textContent).toContain("上下色栏 · 贯穿全片");
});

test("旧模板默认无蒙版，选择后编辑、保存、重读及关闭保留样式", async () => {
  let stored = savedTemplate();
  const writes: SaveTemplateRequest[] = [];
  fetchMock.mockImplementation((async (url, init) => {
    if (!String(url).includes("/template")) throw new Error("未配置此请求");
    if (init?.method !== "POST") return jsonTemplateResponse(stored, "get");
    const request = fromBinary(SaveTemplateRequestSchema, new Uint8Array(init.body as Uint8Array));
    writes.push(request);
    stored = { ...stored, frame_mask: request.frameMask?.top ? request.frameMask as FrameMask : undefined };
    return jsonTemplateResponse(stored, "save");
  }) as typeof fetch);
  const selection = { templateId: stored.template_id };
  const view = render(<TemplateWorkspace selection={selection} onHome={() => {}} />);
  await screen.findByLabelText("模板名称");
  expect(screen.queryByLabelText("上下蒙版设置")).toBeNull();
  // 原本无蒙版的普通保存继续省略字段，不要求旧服务端支持明确删除。
  fireEvent.click(screen.getByRole("button", { name: "保存模板" }));
  await screen.findByText("已保存");
  expect(writes[0].frameMask).toBeUndefined();
  fireEvent.click(screen.getByRole("tab", { name: "蒙版" }));
  fireEvent.click(screen.getByRole("button", { name: "选择上下蒙版" }));
  expect(screen.getByRole("switch", { name: "启用上栏" }).getAttribute("aria-checked")).toBe("true");
  fireEvent.change(screen.getByLabelText("上栏高度"), { target: { value: "21.7" } });
  fireEvent.change(screen.getByLabelText("下栏高度"), { target: { value: "0" } });
  fireEvent.change(screen.getByLabelText("上栏颜色"), { target: { value: "#123456" } });
  fireEvent.click(screen.getByRole("switch", { name: "启用下栏" }));
  fireEvent.click(screen.getByRole("button", { name: "保存模板" }));
  await screen.findByText("已保存");
  expect(writes.at(-1)?.frameMask).toMatchObject({
    top: { enabled: true, height: 21.7, color: "#123456" },
    bottom: { enabled: false, height: 0, color: "#0E2C42" },
  });
  view.unmount();
  render(<TemplateWorkspace selection={selection} onHome={() => {}} />);
  await screen.findByLabelText("模板名称");
  fireEvent.click(screen.getByRole("tab", { name: "蒙版" }));
  fireEvent.click(screen.getByRole("button", { name: "选择上下蒙版" }));
  expect(screen.getByLabelText<HTMLInputElement>("上栏高度").value).toBe("21.7");
  expect(screen.getByLabelText<HTMLInputElement>("上栏颜色").value).toBe("#123456");
  expect(screen.getByRole("switch", { name: "启用下栏" }).getAttribute("aria-checked")).toBe("false");
  fireEvent.click(screen.getByRole("switch", { name: "启用上栏" }));
  fireEvent.click(screen.getByRole("button", { name: "保存模板" }));
  await screen.findByText("已保存");
  expect(writes.at(-1)?.frameMask?.top).toMatchObject({ enabled: false, height: 21.7, color: "#123456" });
});

// 纯色只显示一个颜色；开启渐变才显示附加参数，切换和云端重读都保留两栏独立样式。
test("渐变参数按填充方式显示，编辑保存重读后切回纯色仍保留", async () => {
  let stored = { ...savedTemplate(), frame_mask: newFrameMask() };
  fetchMock.mockImplementation((async (_url, init) => {
    if (init?.method === "POST") {
      const request = fromBinary(SaveTemplateRequestSchema, new Uint8Array(init.body as Uint8Array));
      stored = { ...stored, frame_mask: request.frameMask as FrameMask };
    }
    return jsonTemplateResponse(stored, init?.method === "POST" ? "save" : "get");
  }) as typeof fetch);
  const selection = { templateId: stored.template_id };
  const view = render(<TemplateWorkspace selection={selection} onHome={() => {}} />);
  await screen.findByLabelText("模板名称");
  fireEvent.click(screen.getByRole("button", { name: "编辑上下蒙版对象" }));
  expect(screen.getByLabelText("上栏颜色")).toBeTruthy();
  expect(screen.queryByLabelText("上栏结束色")).toBeNull();
  expect(screen.queryByLabelText("上栏渐变范围")).toBeNull();
  fireEvent.click(screen.getByRole("radio", { name: "上栏渐变" }));
  fireEvent.change(screen.getByLabelText("上栏起始色"), { target: { value: "#123456" } });
  fireEvent.change(screen.getByLabelText("上栏结束色"), { target: { value: "#abcdef" } });
  fireEvent.click(screen.getByRole("radio", { name: "上栏上到下" }));
  fireEvent.change(screen.getByLabelText("上栏渐变范围"), { target: { value: "50" } });
  expect(screen.queryByLabelText("下栏结束色")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "保存模板" }));
  await screen.findByText("已保存");
  expect(stored.frame_mask.top).toMatchObject({ color: "#123456", gradient: { enabled: true, endColor: "#abcdef", direction: "down", range: 50 } });
  expect(stored.frame_mask.bottom.gradient).toBeUndefined();
  view.unmount();
  render(<TemplateWorkspace selection={selection} onHome={() => {}} />);
  await screen.findByLabelText("模板名称");
  fireEvent.click(screen.getByRole("button", { name: "编辑上下蒙版对象" }));
  expect(screen.getByLabelText<HTMLInputElement>("上栏结束色").value).toBe("#abcdef");
  expect(screen.getByLabelText<HTMLInputElement>("上栏渐变范围").value).toBe("50");
  fireEvent.click(screen.getByRole("radio", { name: "上栏纯色" }));
  expect(screen.getByLabelText<HTMLInputElement>("上栏颜色").value).toBe("#123456");
  expect(screen.queryByLabelText("上栏结束色")).toBeNull();
  expect(screen.queryByLabelText("上栏渐变方向")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "保存模板" }));
  await screen.findByText("已保存");
  expect(stored.frame_mask.top.gradient).toMatchObject({ enabled: false, endColor: "#abcdef", direction: "down", range: 50 });
  fireEvent.click(screen.getByRole("radio", { name: "上栏渐变" }));
  expect(screen.getByLabelText<HTMLInputElement>("上栏渐变范围").value).toBe("50");
});

// 外部输入只在通信边界校验，不能把透明结束色、未知方向或无效范围带入预览。
test.each([{ endColor: "#11223300" }, { direction: "left" }, { range: 0 }, { range: 101 }, { range: 1.5 }, { range: NaN }, { enabled: undefined }])("拒绝非法渐变参数 %j", (change) => {
  const mask = newFrameMask();
  mask.top.gradient = { enabled: true, endColor: "#abcdef", direction: "down", range: 50, ...change } as NonNullable<FrameMask["top"]["gradient"]>;
  expect(() => validateFrameMask(mask)).toThrow("蒙版渐变");
});

// 关闭面板保留蒙版；明确移除后立即清空草稿，保存重读不恢复，重新添加使用默认参数。
test("移除蒙版后保存重读不恢复，关闭设置不会移除", async () => {
  let stored = savedTemplate();
  stored.frame_mask = newFrameMask();
  stored.frame_mask.top.height = 25;
  const writes: SaveTemplateRequest[] = [];
  fetchMock.mockImplementation((async (_url, init) => {
    if (init?.method === "POST") {
      const request = fromBinary(SaveTemplateRequestSchema, new Uint8Array(init.body as ArrayBuffer));
      writes.push(request);
      if (request.frameMask) stored = { ...stored, frame_mask: request.frameMask.top ? request.frameMask as FrameMask : undefined };
    }
    return jsonTemplateResponse(stored, init?.method === "POST" ? "save" : "get");
  }) as typeof fetch);
  const selection = { templateId: stored.template_id };
  const view = render(<TemplateWorkspace selection={selection} onHome={() => {}} />);
  await screen.findByLabelText("模板名称");
  fireEvent.click(screen.getByRole("tab", { name: "蒙版" }));
  fireEvent.click(screen.getByRole("button", { name: "选择上下蒙版" }));
  fireEvent.click(screen.getByRole("button", { name: "关闭蒙版设置" }));
  expect(screen.queryByLabelText("上下蒙版设置")).toBeNull();
  expect(screen.getByText("3 个对象")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "选择上下蒙版" }));
  expect(screen.getByLabelText<HTMLInputElement>("上栏高度").value).toBe("25");
  fireEvent.click(screen.getByRole("button", { name: "关闭蒙版设置" }));
  const objects = screen.getByRole("region", { name: "已添加特效" });
  fireEvent.click(within(objects).getByRole("button", { name: "编辑上下蒙版对象" }));
  expect(within(objects).getByRole("button", { name: "编辑上下蒙版对象", pressed: true })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "移除当前画面对象" }));
  expect(within(objects).queryByRole("button", { name: "编辑上下蒙版对象" })).toBeNull();
  expect(within(objects).getAllByRole("button").map((button) => button.getAttribute("aria-label"))).toEqual(["编辑顶部标题", "编辑底部字幕"]);
  expect(screen.queryByLabelText("上下蒙版设置")).toBeNull();
  expect(screen.getByText("2 个对象")).toBeTruthy();
  expect(screen.getByText("点击添加，整段显示")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "保存模板" }));
  await screen.findByText("已保存");
  expect(writes.at(-1)?.frameMask).toBeDefined();
  expect(writes.at(-1)?.frameMask?.top).toBeUndefined();
  expect(writes.at(-1)?.frameMask?.bottom).toBeUndefined();
  expect(stored.frame_mask).toBeUndefined();
  view.unmount();
  render(<TemplateWorkspace selection={selection} onHome={() => {}} />);
  await screen.findByLabelText("模板名称");
  expect(screen.getByText("2 个对象")).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "蒙版" }));
  fireEvent.click(screen.getByRole("button", { name: "选择上下蒙版" }));
  expect(screen.getByLabelText<HTMLInputElement>("上栏高度").value).toBe("18.4");
});

// 旧服务端拒绝空蒙版消息；新建和已有模板临时添加再移除，都不应影响普通保存。
test.each([false, true])("原本无蒙版的模板临时添加后移除仍兼容旧服务端（已有模板：%s）", async (existing) => {
  const stored = savedTemplate();
  const writes: SaveTemplateRequest[] = [];
  fetchMock.mockImplementation((async (_url, init) => {
    if (init?.method !== "POST") return jsonTemplateResponse(stored, "get");
    const request = fromBinary(SaveTemplateRequestSchema, new Uint8Array(init.body as Uint8Array));
    writes.push(request);
    if (request.frameMask && (!request.frameMask.top || !request.frameMask.bottom))
      return Response.json({ detail: "旧服务端要求完整蒙版配置" }, { status: 422 });
    return jsonTemplateResponse(stored, "save", existing ? 200 : 201);
  }) as typeof fetch);
  render(<TemplateWorkspace selection={existing ? { templateId: stored.template_id } : { templateId: null, name: stored.name, description: "" }} onHome={() => {}} />);
  await screen.findByLabelText("模板名称");
  fireEvent.click(screen.getByRole("tab", { name: "蒙版" }));
  fireEvent.click(screen.getByRole("button", { name: "选择上下蒙版" }));
  fireEvent.click(screen.getByRole("button", { name: "移除当前画面对象" }));
  fireEvent.click(screen.getByRole("button", { name: "保存模板" }));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0].frameMask).toBeUndefined();
  expect(writes[0].templateId).toBe(existing ? stored.template_id : undefined);
  await screen.findByText("已保存");
});

// 保存失败不推进基线；重试仍明确删除，成功后的普通保存不继续发送空消息。
test("删除蒙版保存失败后可重试，成功后更新保存基线", async () => {
  let stored = { ...savedTemplate(), frame_mask: newFrameMask() as FrameMask | undefined };
  const writes: SaveTemplateRequest[] = [];
  fetchMock.mockImplementation((async (_url, init) => {
    if (init?.method !== "POST") return jsonTemplateResponse(stored, "get");
    const request = fromBinary(SaveTemplateRequestSchema, new Uint8Array(init.body as Uint8Array));
    writes.push(request);
    if (writes.length === 1) return Response.json({ detail: "临时保存失败" }, { status: 503 });
    if (request.frameMask) stored = { ...stored, frame_mask: request.frameMask.top ? request.frameMask as FrameMask : undefined };
    return jsonTemplateResponse(stored, "save");
  }) as typeof fetch);
  render(<TemplateWorkspace selection={{ templateId: stored.template_id }} onHome={() => {}} />);
  await screen.findByLabelText("模板名称");
  fireEvent.click(screen.getByRole("button", { name: "编辑上下蒙版对象" }));
  fireEvent.click(screen.getByRole("button", { name: "移除当前画面对象" }));
  fireEvent.click(screen.getByRole("button", { name: "保存模板" }));
  await screen.findByText("临时保存失败");
  expect(stored.frame_mask).toBeDefined();
  expect(screen.queryByRole("button", { name: "编辑上下蒙版对象" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "保存模板" }));
  await screen.findByText("已保存");
  expect(writes).toHaveLength(2);
  for (const request of writes) {
    expect(request.frameMask).toBeDefined();
    expect(request.frameMask?.top).toBeUndefined();
    expect(request.frameMask?.bottom).toBeUndefined();
  }
  expect(stored.frame_mask).toBeUndefined();
  fireEvent.click(screen.getByRole("button", { name: "保存模板" }));
  await waitFor(() => expect(writes).toHaveLength(3));
  expect(writes[2].frameMask).toBeUndefined();
});

// 外部模板合法轨道可以叫 frame-mask；选择、删除和时间轴不得把它与蒙版混为一项。
test("真实 frame-mask 轨道与蒙版可分别选择删除，两个蒙版入口名称可区分", () => {
  const catalog = readCatalog();
  const draft = addTrack(newDraft(), catalog.find(asset => asset.category === "bubble")!, "bubble").draft;
  const bubble = draft.tracks.find(track => track.target === "bubble")!;
  bubble.id = "frame-mask";
  draft.frame_mask = newFrameMask();
  const withoutBubble = removeTrack(draft, bubble.id);
  expect(withoutBubble.tracks).toEqual(draft.tracks.filter(track => track !== bubble));
  expect(withoutBubble.frame_mask).toEqual(draft.frame_mask);
  const rows = buildPreviewRows(buildTestTimeline(draft, catalog));
  const maskId = rows.find(row => row.actions[0]?.effectId === "frame-mask")!.actions[0].targetId!;
  expect(new Set(rows.map(row => row.id)).size).toBe(rows.length);
  const actions = rows.flatMap(row => row.actions);
  expect(new Set(actions.map(action => action.id)).size).toBe(actions.length);
  const withoutMask = removeTrack(draft, maskId);
  expect(withoutMask.tracks).toEqual(draft.tracks);
  expect(withoutMask.frame_mask).toBeUndefined();
  const selected: string[] = [];
  render(<><AppliedEffects draft={draft} catalog={catalog} selected={bubble.id} onSelect={id => selected.push(id)} />
    <PreviewTimeline ref={null} rows={rows} disabled={false} time={0} onSeek={() => {}} onSelect={id => selected.push(id)} /></>);
  const objects = screen.getByRole("region", { name: "已添加特效" });
  expect(within(objects).getAllByRole("button", { pressed: true })).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "编辑气泡字", pressed: true }));
  fireEvent.click(screen.getByRole("button", { name: "编辑上下蒙版对象" }));
  fireEvent.click(screen.getByRole("button", { name: "编辑蒙版轨道" }));
  expect(selected).toEqual([bubble.id, maskId, maskId]);
});

test("蒙版覆盖视频和转场补帧，位于滤镜、特效和文字下面且贯穿全片", () => {
  // happy-dom 不解码 Canvas；真实 PNG 像素另由浏览器用例检查。
  spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ fillRect() {}, fillStyle: "" } as unknown as CanvasRenderingContext2D);
  spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue("data:image/png;base64,mask");
  const catalog = readCatalog();
  let draft = newDraft();
  for (const category of ["transition/normal", "filter", "vfx/normal"])
    draft = addTrack(draft, catalog.find((asset) => asset.category === category)!, "title").draft;
  const baseline = buildTestTimeline(draft, catalog);
  draft.frame_mask = newFrameMask();
  const timeline = buildTestTimeline(draft, catalog);
  const frames = new Map([["1:0", "before"], ["1:1", "after"]]);
  const layers = playerTimeline(timeline, frames).VideoTracks;
  expect(layers.map((row) => row.VideoTrackClips[0].Type)).toEqual(["Video", "Image", "Image", "Filter", "VFX", "Text", "Text"]);
  expect(layers[2].VideoTrackClips[0]).toMatchObject({
    MediaURL: "data:image/png;base64,mask", TimelineIn: 0, TimelineOut: 30, Duration: 30,
    X: 0, Y: 0, Width: 1, Height: 1, AdaptMode: "Fill",
  });
  expect(timeline.VideoTracks).toEqual(baseline.VideoTracks);
  draft.frame_mask.top.enabled = draft.frame_mask.bottom.enabled = false;
  expect(playerTimeline(buildTestTimeline(draft, catalog), frames)).toEqual(playerTimeline(baseline, frames));
});

test("时间轴显示整段蒙版并按层级排列，关闭后仍可点击配置", () => {
  const catalog = readCatalog();
  let draft = newDraft();
  for (const category of ["bubble", "filter", "vfx/normal", "transition/normal"])
    draft = addTrack(draft, catalog.find((asset) => asset.category === category)!, "title").draft;
  expect(buildPreviewRows(buildTestTimeline(draft, catalog)).some((row) => row.id === "mask:frame")).toBe(false);
  draft.frame_mask = newFrameMask();
  const rows = mergeSpriteRows(buildPreviewRows(buildTestTimeline(draft, catalog)), [
    { id: "ai", spriteId: "sprite", name: "装饰", aspect: 9 / 16, start: 0, end: 3, length: 3 },
  ]);
  expect(rows.map((row) => row.actions[0]?.effectId)).toEqual([
    "title", "subtitle", "bubble", "transition", "vfx", "filter", "remotion", "frame-mask", "video", "video", "video",
  ]);
  expect(rows.find((row) => row.id === "mask:frame")?.actions[0]).toMatchObject({
    targetId: "mask:frame", start: 0, end: 30, movable: false, flexible: false,
  });
  draft.frame_mask.top.enabled = draft.frame_mask.bottom.enabled = false;
  const disabledRows = buildPreviewRows(buildTestTimeline(draft, catalog));
  expect(disabledRows.find((row) => row.id === "mask:frame")?.actions[0].effectName).toBe("上下蒙版（未显示）");
  const selected: string[] = [];
  render(<PreviewTimeline ref={null} rows={disabledRows} disabled={false} time={0} duration={30} onSeek={() => {}} onSelect={(id) => selected.push(id)} />);
  fireEvent.click(screen.getByRole("button", { name: "编辑蒙版轨道" }));
  expect(selected).toEqual(["mask:frame"]);
});

// 合并上游协议后，三个配置必须同时经过二进制请求及 JSON 响应，不能互相覆盖。
test("蒙版、同屏与数字人配置独立保存并读回草稿", async () => {
  const settings = {
    frame_mask: newFrameMask(),
    split_screen: { ...defaultSplitScreen(), enabled: true },
    avatar_effects: { ...defaultAvatarEffects(), openingEnabled: true, feather: 0 },
  };
  const stored = { ...savedTemplate(), ...settings };
  fetchMock.mockImplementation((async (_url, init) => {
    if (init?.method === "POST") {
      const message = fromBinary(SaveTemplateRequestSchema, new Uint8Array(init.body as ArrayBuffer));
      expect(message.frameMask).toMatchObject(settings.frame_mask);
      expect(message.splitScreen).toMatchObject(settings.split_screen);
      expect(message.avatarEffects).toMatchObject(settings.avatar_effects);
    }
    return jsonTemplateResponse(stored, init?.method === "POST" ? "save" : "get");
  }) as typeof fetch);
  const saved = await saveTemplate({ ...newDraft(), name: "同屏蒙版", ...settings });
  expect(saved).toMatchObject(settings);
  expect(toDraft(await getTemplate(saved.template_id))).toMatchObject(settings);
});

// 蒙版叠在整套同屏画面上，不能插进素材与数字人之间，也不能盖住文字与全局特效。
test.each(["split", "overlay", "circle"] as const)("%s 布局的蒙版位于全部视频之上、特效与文字之下", (layout) => {
  spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ fillRect() {}, fillStyle: "" } as unknown as CanvasRenderingContext2D);
  spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue("data:image/png;base64,mask");
  const catalog = readCatalog();
  let draft = newDraft();
  draft.split_screen = { ...defaultSplitScreen(), layout, enabled: true };
  for (const category of ["filter", "vfx/normal"])
    draft = addTrack(draft, catalog.find((asset) => asset.category === category)!, "title").draft;
  const baseline = buildTestTimeline(draft, catalog);
  const baseLayers = playerTimeline(baseline).VideoTracks;
  const videoCount = baseline.VideoTracks.length;
  draft.frame_mask = newFrameMask();
  const layers = playerTimeline(buildTestTimeline(draft, catalog)).VideoTracks;
  expect(layers.slice(0, videoCount)).toEqual(baseLayers.slice(0, videoCount));
  expect(layers[videoCount].VideoTrackClips[0]).toMatchObject({
    Type: "Image", MediaURL: "data:image/png;base64,mask", TimelineIn: 0, TimelineOut: 30,
    X: 0, Y: 0, Width: 1, Height: 1, AdaptMode: "Fill",
  });
  expect(layers.slice(videoCount + 1)).toEqual(baseLayers.slice(videoCount));
});
