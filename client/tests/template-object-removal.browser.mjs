/** 对象移除回归：真实浏览器、SDK 与媒体验证轨道更新后页面可用；执行 bun tests/template-object-removal.browser.mjs，需要运行 Vite。 */
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { resolve } from "node:path";
import { chromium } from "playwright-core";

const cache = resolve("node_modules/.cache/template-object-removal-browser");
mkdirSync(cache, { recursive: true });
const directory = mkdtempSync(`${cache}/run-`);
process.env.TMPDIR = directory;
const browser = await chromium.launch({ channel: "chrome", headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(20_000);
  const errors = [];
  const writes = [];
  page.on("pageerror", (error) => { errors.push(error.message); console.error(error.stack); });
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/template") && request.method() !== "GET") writes.push(request.method());
  });
  await page.goto(process.env.IMV_BROWSER_URL || "http://localhost:1420");
  await page.getByRole("region", { name: "云端模板", exact: true }).getByRole("button", { name: "新建模板", exact: true }).click();
  await page.getByRole("dialog").getByLabel("模板名称", { exact: true }).fill("对象移除回归草稿");
  await page.getByRole("dialog").getByRole("button", { name: "进入编辑", exact: true }).click();
  const assets = page.getByRole("region", { name: "特效资产", exact: true });
  const applied = page.getByRole("region", { name: "已添加特效", exact: true });
  const inspector = page.getByRole("region", { name: "特效设置", exact: true });
  const preview = page.getByRole("region", { name: "实时预览", exact: true });

  /** 等待真实轨道数量与播放器就绪同时满足；页面清空或 SDK 失败立即报告。 */
  async function ready(actionCount) {
    await page.waitForFunction((expected) => {
      const root = document.getElementById("root");
      const region = document.querySelector('[aria-label="实时预览"]');
      return !root?.childElementCount || region?.querySelector('[role="alert"]') || (
        region?.querySelectorAll(".timeline-editor-action").length === expected &&
        [...region.querySelectorAll("button")].some((button) => button.textContent === "播放" && !button.disabled)
      );
    }, actionCount, { timeout: 60_000 });
    assert((await page.locator("#root").innerText()).trim(), `对象更新后整页为空：${errors.join("；")}`);
    assert.deepEqual(errors, []);
    assert.equal(await preview.getByRole("alert").count(), 0, await preview.innerText());
    assert.equal(await preview.locator(".timeline-editor-action").count(), actionCount);
  }

  /** 移除选中的对象后，等待 SDK 和时间轴完成更新再核验页面。 */
  async function remove(actionCount) {
    await inspector.getByRole("button", { name: "移除当前画面对象", exact: true }).click();
    await ready(actionCount);
    assert.equal(await inspector.count(), 0);
    assert.equal(await page.getByRole("tab", { name: "主页", exact: true }).count(), 1);
  }

  // 场景：预览实际播放后移除标题，保留字幕；继续移除字幕，母版与页面仍可用。
  await ready(1);
  assert.equal(await applied.getByRole("button").count(), 0);
  for (const [index, label] of ["顶部标题", "底部字幕"].entries()) {
    await assets.getByRole("combobox", { name: "应用到", exact: true }).click();
    await page.getByRole("option", { name: label, exact: true }).click();
    await assets.getByRole("button", { name: /^应用花字：/ }).first().click();
    await ready(index + 2);
  }
  await ready(3);
  await preview.getByRole("button", { name: "播放", exact: true }).click();
  await page.waitForFunction(() => Number(document.querySelector('[aria-label="预览播放位置"]')?.getAttribute("aria-valuenow")) > 0.2);
  await applied.getByRole("button", { name: "编辑顶部标题", exact: true }).click();
  await remove(2);
  await preview.getByLabel("底部字幕：0～10 秒", { exact: true }).waitFor();
  assert.equal(await preview.getByLabel("顶部标题：0～10 秒", { exact: true }).count(), 0);
  await applied.getByRole("button", { name: "编辑底部字幕", exact: true }).click();
  await remove(1);
  assert.equal(await applied.getByRole("button").count(), 0);

  // 场景：六类对象在真实轨道出现后移除、重新添加和再次移除；转场同步恢复单个母版片段。
  for (const [label, category] of [["顶部标题", "花字"], ["底部字幕", "花字"], ["气泡字", "气泡"], ["视频滤镜", "滤镜"], ["画面特效", "画面特效"], ["镜头转场", "转场"]]) {
    await assets.getByRole("button", { name: category, exact: true }).click();
    if (category === "花字") {
      await assets.getByRole("combobox", { name: "应用到", exact: true }).click();
      await page.getByRole("option", { name: label, exact: true }).click();
    }
    const asset = assets.getByRole("button", { name: new RegExp(`^应用${category}：`) }).first();
    for (let attempt = 0; attempt < 2; attempt++) {
      await asset.click();
      await ready(category === "转场" ? 3 : 2);
      await remove(1);
      assert.equal(await applied.getByRole("button").count(), 0);
    }
  }
  await preview.getByRole("button", { name: "播放", exact: true }).click();
  await page.waitForFunction(() => Number(document.querySelector('[aria-label="预览播放位置"]')?.getAttribute("aria-valuenow")) > 0.2);
  assert.deepEqual(errors, []);
  assert.deepEqual(writes, []);
  console.log("PASS：播放中移除、保留其他对象、清空特效、六类对象重复添加与移除、转场母版恢复及继续播放；未写入模板数据。");
} finally {
  await browser.close();
  rmSync(directory, { recursive: true, force: true });
}
