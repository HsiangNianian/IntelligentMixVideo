/** 蒙版保存边界、面板与图层顺序；整数像素案例与后端生成 PNG 共用。 */
import { expect, spyOn, test } from "bun:test";
import { fromBinary } from "@bufbuild/protobuf";
import { fireEvent, render, screen } from "@testing-library/react";
import { SaveTemplateRequestSchema, type SaveTemplateRequest } from "@/generated/imv/template/v1/template_pb";
import { TemplateWorkspace } from "@/features/templates/TemplateWorkspace";
import { PreviewTimeline } from "@/features/templates/PreviewTimeline";
import { frameMaskRects, newFrameMask, type FrameMask } from "@/features/templates/frameMask";
import { newDraft } from "@/features/templates/model";
import { readCatalog } from "@/features/templates/sdk";
import { addTrack } from "@/features/templates/tracks";
import { buildPreviewRows, mergeSpriteRows, playerTimeline } from "@/features/templates/timeline";
import { buildTestTimeline, jsonTemplateResponse, savedTemplate } from "./fixtures";
import { fetchMock } from "./setup";
import cases from "../../server/tests/frame_mask_cases.json";

test.each(cases)("蒙版按成片高度 $height 生成相同整数边界", ({ height, top, bottom, expected }) => {
  const mask = newFrameMask();
  mask.top.height = top;
  mask.bottom.height = bottom;
  expect(frameMaskRects(mask, height).map((rect) => [rect.y, rect.height])).toEqual(expected);
});

test("旧模板默认无蒙版，选择后编辑、保存、重读及关闭保留样式", async () => {
  let stored = savedTemplate();
  const writes: SaveTemplateRequest[] = [];
  fetchMock.mockImplementation((async (url, init) => {
    if (!String(url).includes("/template")) throw new Error("未配置此请求");
    if (init?.method !== "POST") return jsonTemplateResponse(stored, "get");
    const request = fromBinary(SaveTemplateRequestSchema, new Uint8Array(init.body as Uint8Array));
    writes.push(request);
    stored = { ...stored, frame_mask: request.frameMask as FrameMask };
    return jsonTemplateResponse(stored, "save");
  }) as typeof fetch);
  const selection = { templateId: stored.template_id };
  const view = render(<TemplateWorkspace selection={selection} onHome={() => {}} />);
  await screen.findByLabelText("模板名称");
  expect(screen.queryByLabelText("上下蒙版设置")).toBeNull();
  // 未使用蒙版时保存也不补字段。
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
  expect(buildPreviewRows(buildTestTimeline(draft, catalog)).some((row) => row.id === "frame-mask")).toBe(false);
  draft.frame_mask = newFrameMask();
  const rows = mergeSpriteRows(buildPreviewRows(buildTestTimeline(draft, catalog)), [
    { id: "ai", spriteId: "sprite", name: "装饰", aspect: 9 / 16, start: 0, end: 3, length: 3 },
  ]);
  expect(rows.map((row) => row.actions[0]?.effectId)).toEqual([
    "title", "subtitle", "bubble", "transition", "vfx", "filter", "remotion", "frame-mask", "video", "video", "video",
  ]);
  expect(rows.find((row) => row.id === "frame-mask")?.actions[0]).toMatchObject({
    targetId: "frame-mask", start: 0, end: 30, movable: false, flexible: false,
  });
  draft.frame_mask.top.enabled = draft.frame_mask.bottom.enabled = false;
  const disabledRows = buildPreviewRows(buildTestTimeline(draft, catalog));
  expect(disabledRows.find((row) => row.id === "frame-mask")?.actions[0].effectName).toBe("上下蒙版（未显示）");
  const selected: string[] = [];
  render(<PreviewTimeline ref={null} rows={disabledRows} disabled={false} time={0} duration={30} onSeek={() => {}} onSelect={(id) => selected.push(id)} />);
  fireEvent.click(screen.getByRole("button", { name: "编辑上下蒙版" }));
  expect(selected).toEqual(["frame-mask"]);
});
