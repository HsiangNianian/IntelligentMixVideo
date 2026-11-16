/** 在服务端机器运行真实编辑器与无头 Chrome，用内置样片补旧模板。支持提前生成 PNG 和按相同模板更新时间导入。 */
import { chromium } from "playwright-core";
import { createServer } from "vite";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const { values } = parseArgs({ options: { api: { type: "string" }, output: { type: "string" }, "template-id": { type: "string" }, input: { type: "string" }, startup: { type: "boolean" } } });
if (!values.api) throw new Error("用法：bun scripts/backfill-template-covers.mjs --api 服务地址 [--output 导出目录 | --input 导入目录] [--template-id 模板ID]");
if (values.input && values.output) throw new Error("只能指定一个导入或导出目录");
const api = values.api.replace(/\/+$/, "");
const root = fileURLToPath(new URL("../", import.meta.url));
// lifespan 后台任务可能先于 HTTP 监听启动。仅在启动模式下短暂等候本机 API。
if (values.startup) {
  const deadline = Date.now() + 30_000;
  while (true) {
    try {
      const health = await fetch(api + "/", { signal: AbortSignal.timeout(1500) });
      if (health.ok) break;
    } catch { /* 监听尚未就绪 */ }
    if (Date.now() >= deadline) throw new Error("等待本机模板服务超时，请核对 PORT");
    await Bun.sleep(250);
  }
}
const response = await fetch(api + "/template", { headers: { Accept: "application/json" } });
if (!response.ok) throw new Error("读取模板列表失败：" + response.status);
const { templates } = await response.json();
const selected = values["template-id"] ? templates.filter(entry => entry.templateId === values["template-id"]) : templates;
if (values["template-id"] && !selected.length) throw new Error("找不到指定模板");
if (!values.output) {
  const schema = await (await fetch(api + "/openapi.json")).json();
  if (!schema.paths?.["/template/{template_id}/cover/{revision}"]?.put) throw new Error("请先部署模板封面写入接口");
} else await mkdir(values.output, { recursive: true });

let server, browser, page;

/** 只有取帧时启动无头 Chrome。导入预先验证的 PNG 不依赖浏览器或 FFmpeg。 */
async function capture(entry) {
  if (!page) {
    // 空浏览器上下文没有本机素材；固定三个配置槽位，忽略部署机器自己的预览 URL。
    server = await createServer({
      root, logLevel: "error",
      define: {
        "import.meta.env.VITE_API_URL": "window.location.origin",
        ...Object.fromEntries([1, 2, 3].map(i => [
          `import.meta.env.VITE_PREVIEW_VIDEO_${i}_URL`, JSON.stringify(`/preview/sample-${i}.mp4`),
        ])),
      },
      server: { host: "localhost", port: 0, proxy: {
        "^/template(?:/|$)": { target: api, changeOrigin: true }, "/api/": { target: api, changeOrigin: true },
      } },
    });
    await server.listen();
    const executablePath = process.env.IMV_BROWSER_EXECUTABLE || process.env.IMV_TEST_CHROME;
    const proxyUrl = process.env.https_proxy || process.env.HTTPS_PROXY;
    const proxy = proxyUrl ? new URL(proxyUrl) : undefined;
    browser = await chromium.launch({
      ...(executablePath ? { executablePath } : { channel: "chrome" }),
      ...(proxy ? { proxy: { server: proxy.origin, username: decodeURIComponent(proxy.username), password: decodeURIComponent(proxy.password),
        bypass: ["localhost", "127.0.0.1", process.env.no_proxy || process.env.NO_PROXY].filter(Boolean).join(",") } } : {}),
      args: ["--enable-unsafe-swiftshader", "--autoplay-policy=no-user-gesture-required"],
    });
    page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.setDefaultTimeout(90000);
  }
  await page.goto(server.resolvedUrls.local[0], { waitUntil: "domcontentloaded" });
  await page.getByRole("searchbox", { name: "搜索模板名称" }).fill(entry.name);
  await page.locator(`button[data-template-id="${entry.templateId}"]`).last().click();
  const canvas = page.locator(".template-preview-canvas");
  await canvas.waitFor();
  await canvas.evaluate(el => { el.dataset.capturing = ""; });
  const frame = canvas.locator(".template-preview-frame");
  await frame.evaluate(async element => {
    // 使用编辑器同一个取帧条件，不能仅凭时间轴已到 1 秒就发布。
    const { prepareCoverFrame } = await import("/src/features/templates/coverCapture.ts");
    const progress = document.querySelector('input[aria-label="预览进度"]');
    await prepareCoverFrame(element, seconds => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(progress, String(seconds));
      progress.dispatchEvent(new Event("input", { bubbles: true }));
    });
  });
  for (const frame of page.frames().filter(f => f.url().includes("overlay=true"))) await frame.waitForFunction(() => document.fonts.status === "loaded");
  await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
  return frame.screenshot({ type: "png" });
}

/** 发布接口同时校验版本；重复导入会跳过已经成功或已由编辑器接管的模板。 */
async function publish(entry, png) {
  const body = new FormData();
  body.append("file", new Blob([png], { type: "image/png" }), "cover.png");
  if (!values.input) body.append("refresh", "true");
  const saved = await fetch(api + "/template/" + entry.templateId + "/cover/" + (entry.cover?.revision ?? entry.templateId), { method: "PUT", body });
  if (!saved.ok) throw new Error("封面写入失败 HTTP " + saved.status);
}

const prepared = values.input ? JSON.parse(await readFile(resolve(values.input, "manifest.json"), "utf8")) : {};
let failures = 0;
try {
  for (const entry of selected) {
    const cover = entry.cover;
    if (entry.validationError || (cover && (cover.revision !== entry.templateId || cover.source !== "builtin" || (values.input && cover.publishedRevision)))) {
      console.log("跳过", entry.name);
      continue;
    }
    try {
      if (!/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(entry.templateId)) throw new Error("模板 ID 无效");
      if (values.input && prepared[entry.templateId] !== entry.updatedAt) throw new Error("缺少匹配的清单记录或模板已修改，请重新取帧");
      const png = values.input ? await readFile(resolve(values.input, entry.templateId + ".png")) : await capture(entry);
      if (values.output) {
        await writeFile(resolve(values.output, entry.templateId + ".png"), png);
        prepared[entry.templateId] = entry.updatedAt;
      } else await publish(entry, png);
      console.log(values.output ? "已取帧" : "已发布", entry.name, entry.templateId);
    } catch (error) {
      failures++;
      console.error("失败", entry.name, String(error));
    }
  }
  if (values.output) {
    const manifest = resolve(values.output, "manifest.json");
    const existing = await readFile(manifest, "utf8").then(JSON.parse, error => {
      if (error.code === "ENOENT") return {};
      throw error;
    });
    await writeFile(manifest, JSON.stringify({ ...existing, ...prepared }, null, 2));
  }
  if (failures) process.exitCode = 1;
} finally {
  await browser?.close();
  await server?.close();
}
