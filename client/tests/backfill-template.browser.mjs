/** bun tests/backfill-template.browser.mjs 在 Chrome 的真实主页验证跨页补图和找不到 ID 时的退出。 */
import assert from "node:assert/strict";
import { chromium } from "playwright-core";
import { createServer } from "vite";
import { openBackfillTemplate } from "../scripts/open-backfill-template.mjs";

const templates = Array.from({ length: 16 }, (_, index) => ({
  templateId: crypto.randomUUID(), name: index < 2 ? "Demo" : `Demo ${index}`,
  createdAt: "2026-01-01T00:00:00Z",
  updatedAt: new Date(Date.UTC(2026, 0, index + 1)).toISOString(),
}));
const server = await createServer({ define: { "import.meta.env.VITE_API_URL": "window.location.origin" }, server: { host: "127.0.0.1", port: 0 } });
let browser;
try {
  await server.listen();
  browser = await chromium.launch(process.env.IMV_CHROME_PATH ? { executablePath: process.env.IMV_CHROME_PATH } : { channel: "chrome" });
  const page = await browser.newPage();
  const base = `http://127.0.0.1:${server.httpServer.address().port}`;
  await page.route(base + "/template", route => route.fulfill({ json: { templates } }));
  await page.route(base + "/template/*", route => route.fulfill({ status: 404 }));
  await page.route("**/api/**", route => route.fulfill({ status: 404 }));
  await page.goto(base);
  await assert.rejects(openBackfillTemplate(page, { name: "Demo", templateId: crypto.randomUUID() }), /搜索结果中找不到模板/);
  await page.reload();
  const target = templates[0];
  const detail = page.waitForRequest(req => new URL(req.url()).pathname.startsWith("/template/"));
  await openBackfillTemplate(page, target);
  assert.equal(new URL((await detail).url()).pathname, `/template/${target.templateId}`);
  console.log("PASS: 跨页打开指定 ID 的同名模板，缺少目标时在末页退出");
} finally {
  await browser?.close();
  await server.close();
}
