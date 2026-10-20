/** AI 资产预览字体的转交：宿主页面读取一次两个字重并复用，读取失败时回复 null 让播放器自行下载，下次重新读取；执行 bun run test。 */
import { expect, mock, test } from "bun:test";
import { fetchMock } from "./setup";

// 回归 #210：沙箱预览页不能使用 HTTP 缓存，字体由宿主页面读取后转交；读取失败不影响预览，播放器退回自行下载。
test("字体读取一次后复用，失败时回复 null 并在下次重试", async () => {
  // 读取结果缓存在模块内，用带查询串的导入取得全新的模块实例。
  const specifier = "@/features/sprites/previewFonts?fresh";
  const fonts: typeof import("@/features/sprites/previewFonts") = await import(specifier);
  let failing = true;
  fetchMock.mockImplementation((async (input: RequestInfo | URL) => failing
    ? new Response(null, { status: 503 })
    : new Response(new Blob([String(input).slice(-3)], { type: "font/collection" }))) as typeof fetch);
  const target = { postMessage: mock() };
  /** 发送一次并等待回复。 */
  const answer = async () => {
    fonts.sendPreviewFonts(target as unknown as Window, "c1");
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
    return target.postMessage.mock.calls.at(-1) as [{ type: string; channel: string; fonts: Record<string, Blob> | null }, string];
  };
  expect(await answer()).toEqual([{ type: "imv-preview-fonts", channel: "c1", fonts: null }, "*"]);
  failing = false;
  const [message] = await answer();
  expect([await message.fonts![400].text(), await message.fonts![700].text()]).toEqual(["400", "700"]);
  await answer();
  expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
    "http://api.test:8000/api/sprites/fonts/400", "http://api.test:8000/api/sprites/fonts/700",
    "http://api.test:8000/api/sprites/fonts/400", "http://api.test:8000/api/sprites/fonts/700",
  ]);
});
