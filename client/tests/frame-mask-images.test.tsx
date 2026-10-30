/** 图片蒙版上传、模式切换、草稿与预载入回归；执行 bun run test tests/frame-mask-images.test.tsx，网络和解码均隔离。 */
import { expect, spyOn, test } from "bun:test";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { fromBinary } from "@bufbuild/protobuf";
import { SaveTemplateRequestSchema } from "@/generated/imv/template/v1/template_pb";
import { FrameMaskPanel } from "@/features/templates/FrameMaskPanel";
import { TemplateWorkspace } from "@/features/templates/TemplateWorkspace";
import { frameMaskImage, newFrameMask, type FrameMask } from "@/features/templates/frameMask";
import { useFrameMaskImages } from "@/features/templates/useFrameMaskImages";
import { fetchMock } from "./setup";
import { jsonTemplateResponse, savedTemplate } from "./fixtures";

const first = "a".repeat(64), second = "b".repeat(64);

// 上传只改草稿，保存被阻止到上传结束；重开恢复图片，停用保留，切回颜色显式清除。
test("图片上传成功才替换，保存重开并切换填充", async () => {
  spyOn(HTMLImageElement.prototype, "decode").mockResolvedValue();
  let stored = { ...savedTemplate(), frame_mask: newFrameMask() };
  let finishUpload!: (response: Response) => void;
  let saves = 0;
  fetchMock.mockImplementation((async (url, init) => {
    const path = String(url);
    if (path.endsWith("/frame-mask-images")) {
      expect((init?.body as FormData).get("file")).toBeInstanceOf(File);
      return new Promise<Response>((resolve) => { finishUpload = resolve; });
    }
    if (path.includes("/frame-mask-images/")) return new Response(new Blob(["png"]));
    if (init?.method === "POST") {
      saves++;
      const request = fromBinary(SaveTemplateRequestSchema, new Uint8Array(init.body as Uint8Array));
      stored = { ...stored, frame_mask: request.frameMask as FrameMask };
      return jsonTemplateResponse(stored, "save");
    }
    return jsonTemplateResponse(stored, "get");
  }) as typeof fetch);
  const selection = { templateId: stored.template_id };
  let view = render(<TemplateWorkspace selection={selection} onHome={() => {}} />);
  await screen.findByLabelText("模板名称");
  fireEvent.click(screen.getByRole("button", { name: "编辑上下蒙版对象" }));
  fireEvent.change(screen.getByLabelText("上传上栏图片"), { target: { files: [new File(["png"], "mask.png", { type: "image/png" })] } });
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "保存模板" }).disabled).toBe(true);
  expect(screen.getByLabelText("上栏颜色")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("上栏高度"), { target: { value: "30" } });
  await act(async () => finishUpload(Response.json({ imageId: first }, { status: 201 })));
  expect(screen.queryByLabelText("上栏颜色")).toBeNull();
  expect(screen.getByRole("img", { name: "上栏图片缩略图" })).toBeTruthy();
  expect(screen.getByLabelText<HTMLInputElement>("上栏高度").value).toBe("30");
  expect(saves).toBe(0);
  fireEvent.click(screen.getByRole("button", { name: "保存模板" }));
  await screen.findByText("已保存");
  expect(stored.frame_mask.top.imageId).toBe(first);
  view.unmount();
  view = render(<TemplateWorkspace selection={selection} onHome={() => {}} />);
  await screen.findByLabelText("模板名称");
  fireEvent.click(screen.getByRole("button", { name: "编辑上下蒙版对象" }));
  expect(screen.getByRole("img", { name: "上栏图片缩略图" })).toBeTruthy();
  fireEvent.click(screen.getByRole("switch", { name: "启用上栏" }));
  fireEvent.change(screen.getByLabelText("上栏高度"), { target: { value: "0" } });
  fireEvent.click(screen.getByRole("button", { name: "保存模板" }));
  await screen.findByText("已保存");
  expect(stored.frame_mask.top).toMatchObject({ enabled: false, height: 0, imageId: first });
  fireEvent.click(screen.getByRole("radio", { name: "上栏渐变" }));
  expect(screen.queryByRole("img", { name: "上栏图片缩略图" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "保存模板" }));
  await screen.findByText("已保存");
  expect(stored.frame_mask.top.imageId).toBe("");
  expect(stored.frame_mask.top.gradient?.enabled).toBe(true);
});

// 取消文件选择、失败和关闭面板均不替换旧图；成功合并最新参数，两个栏可以独立选图。
test("上传失败保留旧图，关闭面板取消并忽略迟到成功", async () => {
  let mask = newFrameMask();
  mask.top.imageId = first;
  let settle!: (response: Response) => void;
  let signal!: AbortSignal;
  fetchMock.mockImplementation((async (_url, init) => {
    signal = init!.signal!;
    return new Promise<Response>((resolve) => { settle = resolve; });
  }) as typeof fetch);
  const changed = spyOn({ change: (next: FrameMask) => { mask = next; } }, "change");
  const uploading = spyOn({ change: (_value: boolean) => {} }, "change");
  const view = render(<FrameMaskPanel mask={mask} onChange={changed} onClose={() => {}} onRemove={() => {}} onUploadingChange={uploading} />);
  fireEvent.change(screen.getByLabelText("上传上栏图片"), { target: { files: [] } });
  expect(fetchMock).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("上传上栏图片"), { target: { files: [new File(["bad"], "bad.png")] } });
  await act(async () => settle(Response.json({ detail: "只支持静态图片" }, { status: 422 })));
  expect((await screen.findByRole("alert")).textContent).toContain("只支持静态图片");
  expect(changed).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("上传下栏图片"), { target: { files: [new File(["new"], "new.png")] } });
  await act(async () => settle(Response.json({ imageId: second }, { status: 201 })));
  expect(mask.top.imageId).toBe(first);
  expect(mask.bottom.imageId).toBe(second);
  changed.mockClear();
  fireEvent.change(screen.getByLabelText("上传上栏图片"), { target: { files: [new File(["new"], "new.png")] } });
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => settle(Response.json({ imageId: second }, { status: 201 })));
  expect(changed).not.toHaveBeenCalled();
  expect(uploading).toHaveBeenLastCalledWith(false);
});

// 调高不重复读取；缺图显式失败，停用不读取，旧请求不能覆盖新引用。
test("图片预载入按引用复用，切换取消旧请求且失败可重试", async () => {
  spyOn(HTMLImageElement.prototype, "decode").mockResolvedValue();
  const revoke = spyOn(URL, "revokeObjectURL");
  fetchMock.mockResolvedValue(new Response(new Blob(["png"])));
  const mask = newFrameMask(); mask.top.imageId = first;
  const { result, rerender } = renderHook(({ value, attempt }) => useFrameMaskImages(value, 1920, attempt), { initialProps: { value: mask, attempt: 0 } });
  await waitFor(() => expect(result.current.ready).toBe(true));
  const count = fetchMock.mock.calls.length;
  rerender({ value: { ...mask, top: { ...mask.top, height: 40 } }, attempt: 0 });
  expect(result.current.ready).toBe(true);
  expect(fetchMock.mock.calls.length).toBe(count);
  expect(revoke).toHaveBeenCalled();
  let settle!: (response: Response) => void;
  let pendingSignal!: AbortSignal;
  fetchMock.mockImplementation((async (_url, init) => {
    pendingSignal = init!.signal!;
    return new Promise<Response>((resolve) => { settle = resolve; });
  }) as typeof fetch);
  rerender({ value: { ...mask, top: { ...mask.top, imageId: second } }, attempt: 0 });
  await waitFor(() => expect(settle).toBeDefined());
  rerender({ value: { ...mask, top: { ...mask.top, enabled: false } }, attempt: 0 });
  expect(pendingSignal.aborted).toBe(true);
  await act(async () => settle(new Response(new Blob(["old"]))));
  expect(result.current.images.has(second)).toBe(false);
  fetchMock.mockResolvedValue(Response.json({ detail: "蒙版图片不存在" }, { status: 404 }));
  rerender({ value: { ...mask, top: { ...mask.top, imageId: second } }, attempt: 0 });
  await waitFor(() => expect(result.current.error).toContain("蒙版图片不存在"));
  expect(result.current.ready).toBe(false);
  rerender({ value: { ...mask, top: { ...mask.top, enabled: false } }, attempt: 0 });
  expect(result.current.ready).toBe(true);
  fetchMock.mockResolvedValue(new Response(new Blob(["png"])));
  rerender({ value: mask, attempt: 1 });
  await waitFor(() => expect(result.current.ready).toBe(true));
  expect(result.current.error).toBe("");
});

// 服务端上传已裁成 9:16，预览整图拉伸；真实像素另由浏览器及服务端 PNG 用例验证。
test("已适配图片整张压到栏高，不绘制图片下面的颜色", () => {
  const draw = spyOn({ drawImage: (..._args: unknown[]) => {} }, "drawImage");
  const fill = spyOn({ fillRect: (..._args: unknown[]) => {} }, "fillRect");
  spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ drawImage: draw, fillRect: fill } as unknown as CanvasRenderingContext2D);
  spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue("data:image/png;base64,test");
  const image = new Image();
  Object.defineProperties(image, { naturalWidth: { value: 90 }, naturalHeight: { value: 160 } });
  const mask = newFrameMask(); mask.top.imageId = first; mask.top.height = 20; mask.bottom.enabled = false;
  frameMaskImage(mask, 1080, 1920, new Map([[first, image]]));
  expect(draw).toHaveBeenCalledWith(image, 0, 0, 1080, 384);
  expect(fill).not.toHaveBeenCalled();
  expect(() => frameMaskImage(mask, 1080, 1920)).toThrow("蒙版图片尚未读取");
});
