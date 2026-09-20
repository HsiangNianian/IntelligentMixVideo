/** 主页浏览器验证：连接本机开发页面和真实模板 API，只读取模板与编辑未保存草稿；执行 bun tests/template-home.browser.mjs。 */
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { resolve } from "node:path";
import { chromium } from "playwright-core";

// Chromium 中间文件放在已忽略的依赖目录，独立浏览器上下文不读取用户会话。
const cache = resolve("node_modules/.cache/template-home-browser");
mkdirSync(cache, { recursive: true });
process.env.TMPDIR = mkdtempSync(`${cache}/run-`);
const browser = await chromium.launch({
  channel: "chrome",
  headless: true,
});
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  page.setDefaultTimeout(20_000);
  const errors = [];
  const writes = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/template") && request.method() !== "GET")
      writes.push(request.method());
  });
  await page.goto(process.env.IMV_BROWSER_URL || "http://localhost:1420");
  assert.equal(await page.getByRole("tab", { name: "主页", exact: true }).getAttribute("aria-selected"), "true");
  const cloud = page.getByRole("region", { name: "云端模板", exact: true });
  const local = page.getByRole("region", { name: "本地模板", exact: true });
  const choices = cloud.getByRole("button", { name: /^选择模板：/ });
  await choices.first().waitFor();
  const names = await choices.evaluateAll((buttons) => buttons.map((button) => button.getAttribute("aria-label")));
  assert(names.length >= 2, "真实开发模板库需要至少两个已有模板，以验证模板切换保护");
  assert((await cloud.boundingBox()).y < (await local.boundingBox()).y);
  await local.getByText("请在桌面客户端中查看和选择本地模板。").waitFor();

  // 使用真实 CSS 检查主页在手机、平板及桌面尺寸下的顺序和横向边界。
  for (const width of [390, 768, 1280]) {
    await page.setViewportSize({ width, height: 800 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert((await cloud.boundingBox()).y < (await local.boundingBox()).y);
  }

  // 选择模板读取真实详情，编辑只保留在页面，取消切换后仍显示原草稿。
  await cloud.getByRole("button", { name: names[0], exact: true }).click();
  const templateName = page.getByLabel("模板名称", { exact: true });
  await page.waitForFunction(() => document.querySelector('input[id$="-name"]')?.value.length > 0);
  assert.equal(await templateName.inputValue(), names[0].slice("选择模板：".length));
  assert.equal(await page.getByLabel("当前环境", { exact: true }).textContent(), "云端");
  await templateName.fill("主页选择验证草稿");
  await page.getByRole("tab", { name: "主页", exact: true }).click();
  await cloud.getByRole("button", { name: names[1], exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "取消", exact: true }).click();
  assert.equal(await templateName.inputValue(), "主页选择验证草稿");
  await page.getByRole("tab", { name: "主页", exact: true }).click();
  await cloud.getByRole("button", { name: names[1], exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "放弃修改", exact: true }).click();
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  assert.equal(await templateName.inputValue(), names[1].slice("选择模板：".length));
  await page.reload();
  assert.equal(await page.getByRole("tab", { name: "主页", exact: true }).getAttribute("aria-selected"), "true");
  assert.deepEqual(writes, []);
  assert.deepEqual(errors, []);
  console.log("PASS：主页默认入口、云端与本地顺序、真实模板选择、取消与放弃修改、390/768/1280px 布局；未写入模板数据。");
} finally {
  await browser.close();
  rmSync(process.env.TMPDIR, { recursive: true, force: true });
}
