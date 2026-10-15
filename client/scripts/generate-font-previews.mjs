/** 在 ego-browser 的现有任务页读取固定 SDK 字体目录，用真实字体绘制随包 PNG；运行方式见 client/README.md。 */
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

/** 在指定 Vite 页面加载 SDK，按共享目录的预览标识读取字体并顺序绘制，输出仅含字体名称。 */
export async function generateFontPreviews(root, page, origin = "http://localhost:18766") {
  await page.goto(`${origin}/template-preview.html`);
  const catalog = await page.evaluate(async () => {
    const { loadSDK } = await import("/src/features/templates/sdk.ts");
    const sdk = await loadSDK(document, new AbortController().signal);
    sdk.setDefaultLocale("zh-CN");
    return sdk.getDefaultFontList().map((font) => ({ key: font.key, url: font.url }));
  });
  const fonts = JSON.parse(await readFile(join(root, "server/src/server/template/fonts.json"), "utf8"));
  const output = join(root, "client/src/features/templates/font-previews");
  await mkdir(output, { recursive: true });
  for (const font of fonts) {
    const source = catalog.find((item) => item.key === font.preview);
    if (!source?.url) throw new Error(`SDK 缺少字体：${font.preview}`);
    const bytes = await page.evaluate(async ({ url, label }) => {
      const { drawFontPreview } = await import("/src/features/fonts/preview.ts");
      const response = await fetch(url, { signal: AbortSignal.timeout(30_000) });
      if (!response.ok) throw new Error(`字体下载失败：${response.status}`);
      const file = new File([await response.blob()], "preview.ttf");
      const strip = await drawFontPreview(file, label);
      if (!strip) throw new Error("浏览器未能绘制预览条");
      return Array.from(new Uint8Array(await strip.arrayBuffer()));
    }, { url: source.url, label: font.label });
    await writeFile(join(output, `${font.value}.png`), new Uint8Array(bytes));
    console.log(`${font.value}: ${bytes.length} bytes`);
  }
}
