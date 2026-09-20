/** 时间规则浏览器验证：真实 SDK 与 FFmpeg 生成的视频，检查表单、视频替换、缩短提示及播放；执行 bun tests/template-timing.browser.mjs。 */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { resolve } from "node:path";
import { chromium } from "playwright-core";

const cache = resolve("node_modules/.cache/template-timing-browser");
mkdirSync(cache, { recursive: true });
const directory = mkdtempSync(`${cache}/run-`);
process.env.TMPDIR = directory;
// 场景：生成真实可解码的不同长度视频，测试期间只使用当前目录的独立媒体文件。
for (const duration of [4, 20]) {
  const result = spawnSync("ffmpeg", ["-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30", "-t", String(duration), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", `${directory}/${duration}.mp4`], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
}
const browser = await chromium.launch({ channel: "chrome", headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  page.setDefaultTimeout(20_000);
  const errors = [];
  const writes = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => { if (request.method() === "POST" && new URL(request.url()).pathname === "/template") writes.push(request); });
  const base = process.env.IMV_BROWSER_URL || "http://localhost:1420";
  await page.goto(base);
  await page.getByRole("region", { name: "云端模板", exact: true }).getByRole("button", { name: "新建模板", exact: true }).click();
  await page.getByRole("dialog").getByLabel("模板名称", { exact: true }).fill("时间规则浏览器验证");
  await page.getByRole("dialog").getByRole("button", { name: "进入编辑", exact: true }).click();
  const preview = page.getByRole("region", { name: "实时预览", exact: true });
  const timing = page.getByLabel("轨道时间设置", { exact: true });

  /** 等待真实 SDK 完成媒体加载，失败状态直接结束测试。 */
  async function ready() {
    await page.waitForFunction(() => {
      const region = document.querySelector('[aria-label="实时预览"]');
      return region?.querySelector('[role="alert"]') || [...(region?.querySelectorAll("button") ?? [])].some((button) => button.textContent === "播放" && !button.disabled);
    }, undefined, { timeout: 60_000 });
    assert.equal(await preview.getByRole("alert").count(), 0, await preview.innerText());
  }

  /** 选择本次生成的媒体，等待界面实际使用该视频的完整时长。 */
  async function loadVideo(duration) {
    await page.getByLabel("预览视频地址", { exact: true }).fill(new URL(`/@fs${directory}/${duration}.mp4`, base).href);
    await page.getByRole("button", { name: "加载预览视频", exact: true }).click();
    await page.waitForFunction((value) => Number(document.querySelector('[aria-label="预览播放位置"]')?.getAttribute("aria-valuemax")) === value, duration);
    await ready();
  }

  /** 验证真实轨道，失败时输出表单与预览文字，便于定位计算或媒体加载问题。 */
  async function range(label) {
    try { await preview.getByLabel(label, { exact: true }).waitFor(); }
    catch (error) { console.error(await timing.innerText(), await preview.innerText(), await preview.locator('[aria-label^="顶部标题"]').evaluateAll((elements) => elements.map((element) => element.getAttribute("aria-label")))); throw error; }
  }

  // 场景：20 秒视频中的 25% 开始和 3 秒持续时间显示为 5～8 秒，实际轨道使用同一区间。
  await loadVideo(20);
  await page.getByRole("region", { name: "特效资产", exact: true }).getByRole("button", { name: /^应用花字：/ }).first().click();
  await timing.getByRole("combobox", { name: "开始方式" }).click();
  await page.getByRole("option", { name: "视频时长百分比", exact: true }).click();
  await timing.getByLabel("开始位置 / %", { exact: true }).fill("25");
  await timing.getByRole("combobox", { name: "持续方式" }).click();
  await page.getByRole("option", { name: "固定时长", exact: true }).click();
  // 场景：切换固定时长时采用当前视频的剩余 15 秒，用户随后可以输入其他时长。
  assert.equal(await timing.getByLabel("持续时间 / 秒", { exact: true }).inputValue(), "15");
  await range("顶部标题：5～20 秒");
  await timing.getByLabel("持续时间 / 秒", { exact: true }).fill("3");
  await timing.getByLabel("持续时间 / 秒", { exact: true }).press("Enter");
  await range("顶部标题：5～8 秒");
  await ready();

  // 场景：空白或零时长触发原生校验，修正后预览继续使用当前规则。
  const lengthInput = timing.getByLabel("持续时间 / 秒", { exact: true });
  for (const value of ["", "0"]) {
    await lengthInput.fill(value);
    assert.equal(await lengthInput.evaluate((input) => input.checkValidity()), false);
    await page.getByRole("button", { name: "保存模板", exact: true }).click();
    assert.equal(writes.length, 0);
  }
  await lengthInput.fill("3");
  assert.equal(await lengthInput.evaluate((input) => input.checkValidity()), true);
  await range("顶部标题：5～8 秒");

  // 场景：替换为短视频重新计算百分比，模板中的开始百分比和持续时间保持原值。
  await loadVideo(4);
  await range("顶部标题：1～4 秒");
  assert.equal(await timing.getByLabel("开始位置 / %", { exact: true }).inputValue(), "25");
  assert.equal(await timing.getByLabel("持续时间 / 秒", { exact: true }).inputValue(), "3");
  await timing.getByLabel("持续时间 / 秒", { exact: true }).fill("6");
  await preview.getByLabel("时间调整说明").getByText(/持续时间已缩短/).waitFor();
  assert.equal(await timing.getByLabel("持续时间 / 秒", { exact: true }).inputValue(), "6");

  // 场景：更换回长视频恢复完整持续时间；播放器实际推进且未出现页面异常。
  await loadVideo(20);
  await range("顶部标题：5～11 秒");
  await ready();

  // 场景：真实拖动保留百分比开始方式与六秒持续时间，其他全长对象规则保持不变。
  const action = preview.locator('.timeline-editor-action:has([aria-label^="顶部标题："])');
  const box = await action.boundingBox();
  assert(box);
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + box.width / 6, box.y + box.height / 2, { steps: 8 });
  await page.mouse.up();
  await page.waitForFunction(() => {
    const input = document.querySelector('[aria-label="轨道时间设置"] input');
    return input && Math.abs(Number(input.value) - 30) < 0.5;
  });
  assert.equal(await timing.getByRole("combobox", { name: "开始方式" }).innerText(), "视频时长百分比");
  assert.equal(await timing.getByLabel("持续时间 / 秒", { exact: true }).inputValue(), "6");
  await ready();
  await preview.getByRole("button", { name: "播放", exact: true }).click();
  await page.waitForFunction(() => Number(document.querySelector('[aria-label="预览播放位置"]')?.getAttribute("aria-valuenow")) > 0.5);
  await preview.getByRole("button", { name: "暂停", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 900 });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  assert.deepEqual(errors, []);
  assert.deepEqual(writes, [], "时间编辑只更新内存草稿");
  console.log("PASS：时间规则自动更新、编辑期间无保存请求、真实视频替换、区间重算、缩短提示、参数保留、时间轴拖动、实际播放和窄屏布局。");
} finally {
  await browser.close();
  rmSync(directory, { recursive: true, force: true });
}
