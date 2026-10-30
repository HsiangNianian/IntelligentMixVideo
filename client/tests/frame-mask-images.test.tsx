/** 图片蒙版预载入与 PNG 绘制回归；执行 bun run test tests/frame-mask-images.test.tsx，网络和解码均隔离。 */
import { expect, spyOn, test } from "bun:test";
import { act, renderHook, waitFor } from "@testing-library/react";
import { frameMaskImage, newFrameMask } from "@/features/templates/frameMask";
import { useFrameMaskImages } from "@/features/templates/useFrameMaskImages";
import { fetchMock } from "./setup";

const first = "a".repeat(64), second = "b".repeat(64);

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
