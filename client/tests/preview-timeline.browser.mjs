/** 视频轨道核心浏览器验证：真实 SDK、媒体与轨道交互；执行 bun tests/preview-timeline.browser.mjs，需要运行 Vite。 */
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { resolve } from "node:path";
import { chromium } from "playwright-core";

const cache = resolve("node_modules/.cache/preview-timeline-browser");
mkdirSync(cache, { recursive: true });
const temporaryDirectory = mkdtempSync(`${cache}/run-`);
process.env.TMPDIR = temporaryDirectory;
const browser = await chromium.launch({ channel: "chrome", headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  page.setDefaultTimeout(20_000);
  const errors = [];
  let stage = "初始化";
  page.on("pageerror", (error) => { errors.push(error.message); console.error(stage, error.message); });
  await page.goto(process.env.IMV_BROWSER_URL || "http://localhost:1420");
  await page.getByRole("region", { name: "云端模板", exact: true }).getByRole("button", { name: "新建模板", exact: true }).click();
  await page.getByRole("dialog").getByLabel("模板名称", { exact: true }).fill("轨道预览验证");
  await page.getByRole("dialog").getByRole("button", { name: "进入编辑", exact: true }).click();
  const preview = page.getByRole("region", { name: "实时预览", exact: true });
  const slider = preview.getByRole("slider", { name: "预览播放位置" });

  /** 等待 SDK 完成本次时间线，错误直接报告界面状态。 */
  async function ready() {
    await page.waitForFunction(() => {
      const region = document.querySelector('[aria-label="实时预览"]');
      return region?.querySelector('[role="alert"]') || [...(region?.querySelectorAll("button") ?? [])].some((button) => button.textContent === "播放" && !button.disabled);
    }, undefined, { timeout: 60_000 });
    assert.equal(await preview.getByRole("alert").count(), 0, await preview.innerText());
  }

  /** 数字时间由 SDK 帧事件提供，等待目标位置确认。 */
  async function atTime(target) {
    await page.waitForFunction((value) => Math.abs(Number(document.querySelector('[aria-label="预览播放位置"]')?.getAttribute("aria-valuenow")) - value) < 0.11, target);
  }

  /** 由实际刻度宽度计算鼠标定位坐标，支持响应式布局。 */
  async function timePoint(time) {
    const box = await preview.locator(".timeline-editor").boundingBox();
    const step = await preview.locator(".timeline-editor-time-unit").nth(1).evaluate((element) => parseFloat(element.style.width) * 5);
    return { x: box.x + 20 + time * step, y: box.y + 15 };
  }

  // 场景：真实素材生成连续缩略图，点击刻度定位，播放从选定位置继续。
  await ready();
  await page.waitForFunction(() => [...document.querySelectorAll('.preview-timeline img')].length >= 10 && [...document.querySelectorAll('.preview-timeline img')].every((image) => image.naturalWidth === 160), undefined, { timeout: 30_000 });
  const point = await timePoint(4);
  stage = "点击定位";
  await page.mouse.click(point.x, point.y);
  await atTime(4);
  stage = "继续播放";
  await preview.getByRole("button", { name: "播放", exact: true }).click();
  await page.waitForFunction(() => Number(document.querySelector('[aria-label="预览播放位置"]')?.getAttribute("aria-valuenow")) > 4.5);
  await preview.getByRole("button", { name: "暂停", exact: true }).click();
  const position = Number(await slider.getAttribute("aria-valuenow"));
  const expected = await timePoint(position);
  const cursor = await preview.locator(".timeline-editor-cursor").boundingBox();
  assert(Math.abs(cursor.x - expected.x) < 8, "游标应跟随 SDK 播放时间");

  // 场景：快速拖动游标到最终位置后保持暂停，键盘支持首尾边界，片段保持只读。
  await page.mouse.move(cursor.x + 1, cursor.y + 20);
  stage = "连续拖动";
  await page.mouse.down();
  for (const value of [2, 7, 3]) {
    const target = await timePoint(value);
    await page.mouse.move(target.x, cursor.y + 20, { steps: 4 });
  }
  await page.mouse.up();
  await atTime(3);
  await preview.getByText("预览已暂停", { exact: true }).waitFor();
  await slider.focus();
  stage = "首尾定位";
  await slider.press("End");
  await atTime(10);
  await slider.press("Home");
  await atTime(0);
  assert(!(await preview.locator(".timeline-editor-action").first().getAttribute("class")).includes("action-movable"));

  // 场景：转场实际重叠范围可见；修改效果后游标回到开头，窄屏不造成页面横向溢出。
  const assets = page.getByRole("region", { name: "特效资产", exact: true });
  stage = "更新转场";
  await assets.getByRole("button", { name: "转场", exact: true }).click();
  await assets.getByLabel("搜索特效", { exact: true }).fill("时钟旋转");
  await assets.getByRole("button", { name: "应用转场：时钟旋转", exact: true }).click();
  assert.equal(await page.getByLabel("持续时间 / 秒", { exact: true }).inputValue(), "1");
  await page.getByRole("combobox", { name: "开始方式" }).click();
  await page.getByRole("option", { name: "指定秒数", exact: true }).click();
  await page.getByLabel("开始时间 / 秒", { exact: true }).fill("5");
  await preview.locator('[title="转场：5～6 秒"]').waitFor();
  await ready();
  // 场景：转场名称位于独立轨道块；短区间的名称和时间均保持单行，片段只显示重叠范围标记。
  assert.equal(await preview.getByText("转场", { exact: true }).count(), 0);
  const transitionAction = preview.getByLabel("镜头转场：5～6 秒", { exact: true });
  await transitionAction.waitFor();
  const layout = await transitionAction.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return [...element.children].map((child) => {
      const bounds = child.getBoundingClientRect();
      return { text: child.textContent, height: bounds.height, inside: bounds.top >= rect.top && bounds.bottom <= rect.bottom, lineHeight: parseFloat(getComputedStyle(child).lineHeight) };
    });
  });
  assert.deepEqual(layout.map((item) => item.text), ["镜头转场", "5.0～6.0 秒"]);
  assert(layout.every((item) => item.inside && item.height === item.lineHeight), JSON.stringify(layout));
  assert.equal(await transitionAction.getAttribute("title"), "镜头转场：5～6 秒");
  await atTime(0);
  assert.equal(await slider.getAttribute("aria-valuemax"), "9");
  await preview.getByLabel("母版视频片段 2：5～9 秒", { exact: true }).waitFor();
  assert.equal(await preview.locator(".timeline-editor-action").count(), 3);
  stage = "播放转场";
  await preview.getByRole("button", { name: "预览转场", exact: true }).click();
  await page.waitForFunction(() => Number(document.querySelector('[aria-label="预览播放位置"]')?.getAttribute("aria-valuenow")) >= 6);
  await preview.getByRole("button", { name: "暂停", exact: true }).click();
  await preview.getByRole("button", { name: "从头重播", exact: true }).click();
  stage = "重播";
  await page.waitForFunction(() => {
    const time = Number(document.querySelector('[aria-label="预览播放位置"]')?.getAttribute("aria-valuenow"));
    return time > 0 && time < 2;
  });
  await preview.getByRole("button", { name: "暂停", exact: true }).click();
  await slider.focus();
  await slider.press("End");
  await atTime(9);
  await page.setViewportSize({ width: 390, height: 900 });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  assert.deepEqual(errors, []);
  console.log("PASS：真实缩略图、刻度定位、播放同步、连续拖动、首尾定位、转场标签位置与单行显示、转场范围、重播及窄屏布局。");
} finally {
  await browser.close();
  rmSync(temporaryDirectory, { recursive: true, force: true });
}
