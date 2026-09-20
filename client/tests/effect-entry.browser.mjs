/** 特效添加入口浏览器回归：真实页面验证资产添加、对象选择和清空后恢复；执行 bun tests/effect-entry.browser.mjs。 */
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { resolve } from "node:path";
import { chromium } from "playwright-core";

// 独立浏览器目录保存在已忽略的缓存目录中，验证只修改未保存草稿。
const cache = resolve("node_modules/.cache/effect-entry-browser");
mkdirSync(cache, { recursive: true });
const directory = mkdtempSync(`${cache}/run-`);
process.env.TMPDIR = directory;
const browser = await chromium.launch({ channel: "chrome", headless: true });
try {
  const page = await browser.newPage();
  page.setDefaultTimeout(20_000);
  const errors = [];
  const writes = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/template") && request.method() !== "GET") writes.push(request.method());
  });
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto(process.env.IMV_BROWSER_URL || "http://localhost:1420");
    await page.getByRole("region", { name: "云端模板", exact: true }).getByRole("button", { name: "新建模板", exact: true }).click();
    await page.getByRole("dialog").getByLabel("模板名称", { exact: true }).fill("特效添加入口验证");
    await page.getByRole("dialog").getByRole("button", { name: "进入编辑", exact: true }).click();
    const applied = page.getByRole("region", { name: "已添加特效", exact: true });
    const assets = page.getByRole("region", { name: "特效资产", exact: true });
    const inspector = page.getByRole("region", { name: "特效设置", exact: true });
    await applied.waitFor();

    // 场景：新模板没有画面对象或参数面板，提示从左侧资产添加。
    assert.equal(await applied.getByRole("button").count(), 0);
    assert.equal(await inspector.count(), 0);
    await applied.getByText("从左侧特效资产选择并添加效果。", { exact: true }).waitFor();

    // 场景：两个文字对象均能从左侧资产恢复，重复应用生成独立对象，选择后保留参数。
    for (const label of ["顶部标题", "底部字幕"]) {
      await assets.getByRole("combobox", { name: "应用到", exact: true }).click();
      await page.getByRole("option", { name: label, exact: true }).click();
      assert.equal(await applied.getByRole("button").count(), label === "顶部标题" ? 0 : 2);
      const asset = assets.getByRole("button", { name: /^应用花字：/ }).first();
      await asset.click();
      await inspector.getByLabel("示例文字", { exact: true }).fill(`${label}独立内容`);
      await asset.click();
      await applied.getByRole("button", { name: `编辑${label} 1`, exact: true }).click();
      assert.equal(await inspector.getByLabel("示例文字", { exact: true }).inputValue(), `${label}独立内容`);
      assert.equal(await applied.getByRole("button", { name: /^添加/ }).count(), 0);
    }
    assert.equal(await applied.getByRole("button").count(), 4);
    // 场景：全部移除后仍为空，重新添加只创建用户指定的对象。
    while (await applied.getByRole("button").count()) {
      await applied.getByRole("button").first().click();
      await inspector.getByRole("button", { name: "移除当前画面对象", exact: true }).click();
    }
    await applied.getByText("从左侧特效资产选择并添加效果。", { exact: true }).waitFor();
    await assets.getByRole("button", { name: /^应用花字：/ }).first().click();
    assert.equal(await applied.getByRole("button").count(), 1);
  }
  assert.deepEqual(writes, []);
  assert.deepEqual(errors, []);
  console.log("PASS：桌面与窄屏新模板为空、左侧资产添加、重复添加、独立参数选择、清空及重新添加；未写入模板数据。");
} finally {
  await browser.close();
  rmSync(directory, { recursive: true, force: true });
}
