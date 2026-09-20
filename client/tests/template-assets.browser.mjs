/** 资产编辑浏览器验证：使用真实页面、SDK 和视频，只修改未保存草稿；执行 bun tests/template-assets.browser.mjs。 */
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { resolve } from "node:path";
import { chromium } from "playwright-core";
import { defaultEditor, textRoles } from "../src/features/templates/model.ts";

// 浏览器缓存全部写入已经忽略的依赖目录，使用独立会话。
const cache = resolve("node_modules/.cache/template-assets-browser");
mkdirSync(cache, { recursive: true });
const temporaryDirectory = mkdtempSync(`${cache}/run-`);
process.env.TMPDIR = temporaryDirectory;
const browser = await chromium.launch({ channel: "chrome", headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(20_000);
  const errors = [];
  const writes = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/template") && request.method() !== "GET") writes.push(request.method());
  });
  await page.goto(process.env.IMV_BROWSER_URL || "http://localhost:1420");
  await page.getByRole("region", { name: "云端模板", exact: true }).getByRole("button", { name: "新建模板", exact: true }).click();
  await page.getByRole("dialog").getByLabel("模板名称", { exact: true }).fill("资产浏览器验证草稿");
  await page.getByRole("dialog").getByRole("button", { name: "进入编辑", exact: true }).click();
  const assets = page.getByRole("region", { name: "特效资产", exact: true });
  const inspector = page.getByRole("region", { name: "特效设置", exact: true });
  const preview = page.getByRole("region", { name: "实时预览", exact: true });
  const frame = page.getByLabel("模板视频预览", { exact: true });
  const applied = page.getByRole("region", { name: "已添加特效", exact: true });

  /** 使用真实 Radix 选择器切换应用目标或取消效果。 */
  async function choose(label, option) {
    await page.getByRole("combobox", { name: label, exact: true }).click();
    await page.getByRole("option", { name: option, exact: true }).click();
  }

  /** 等待真实播放器完成最新时间线，失败时附带界面诊断。 */
  async function ready() {
    await page.waitForFunction(() => {
      const region = document.querySelector('[aria-label="实时预览"]');
      return region?.querySelector('[role="alert"]') || [...(region?.querySelectorAll("button") ?? [])].some((button) => button.textContent === "播放 / 重播" && !button.disabled);
    }, undefined, { timeout: 60_000 });
    assert.equal(await preview.getByRole("alert").count(), 0, await preview.innerText());
    assert.equal(await preview.getByRole("button", { name: "播放 / 重播" }).isEnabled(), true);
  }

  // 场景：SDK 真实加载，桌面三栏从左到右排列，播放器可播放并推进时间。
  await ready();
  const panels = await Promise.all([assets.boundingBox(), preview.boundingBox(), inspector.boundingBox()]);
  assert(panels[0].x + panels[0].width <= panels[1].x + 1);
  assert(panels[1].x + panels[1].width <= panels[2].x + 1);
  await preview.getByRole("button", { name: "播放 / 重播" }).click();
  await page.waitForFunction(() => {
    const text = document.querySelector('[aria-label="实时预览"]')?.textContent ?? "";
    return /16:9 · [1-9]/.test(text);
  });
  await preview.getByRole("button", { name: "暂停", exact: true }).click();

  // 场景：搜索空结果可恢复，真实花字应用到字幕，右侧编辑与标题互相独立。
  const search = assets.getByLabel("搜索特效");
  await search.fill("__missing_asset_726184__");
  await assets.getByText("没有匹配的特效").waitFor();
  await page.evaluate(() => {
    document.addEventListener("keydown", (event) => {
      if (event.target instanceof HTMLInputElement && event.target.type === "search")
        event.target.dataset.enterPrevented = String(event.defaultPrevented);
    }, { once: true });
  });
  await search.press("Enter");
  assert.equal(await search.getAttribute("data-enter-prevented"), "true");
  await search.fill("");
  const initialAssets = await assets.getByRole("button", { name: /^应用花字：/ }).count();
  await assets.getByRole("button", { name: "显示更多", exact: true }).click();
  assert((await assets.getByRole("button", { name: /^应用花字：/ }).count()) > initialAssets);
  await choose("应用到", "底部字幕");
  const flower = assets.getByRole("button", { name: /^应用花字：/ }).first();
  await flower.click();
  assert.equal(await flower.getAttribute("aria-pressed"), "true");
  await inspector.getByLabel("示例文字").fill("资产浏览器验证字幕");
  await inspector.getByLabel("字号", { exact: true }).fill("36");
  await inspector.getByLabel("垂直位置 %").fill("72");
  await applied.getByRole("button", { name: "编辑顶部标题", exact: true }).click();
  assert.notEqual(await inspector.getByLabel("示例文字").inputValue(), "资产浏览器验证字幕");
  await applied.getByRole("button", { name: "编辑底部字幕", exact: true }).click();
  assert.equal(await inspector.getByLabel("示例文字").inputValue(), "资产浏览器验证字幕");
  assert.equal(await inspector.getByLabel("字号", { exact: true }).inputValue(), "36");
  await ready();

  // 场景：动画互斥在资产区与参数区一致；取消入场后可以选择循环。
  await assets.getByRole("button", { name: "文字入场", exact: true }).click();
  await assets.getByRole("button", { name: /^应用文字入场：/ }).first().click();
  await assets.getByRole("button", { name: "文字循环", exact: true }).click();
  assert.equal(await assets.getByRole("button", { name: /^应用文字循环：/ }).first().isDisabled(), true);
  await choose("入场动画", "无效果");
  assert.equal(await assets.getByRole("button", { name: /^应用文字循环：/ }).first().isEnabled(), true);

  // 场景：滤镜选择打开对应设置，移除不会清除字幕；重选标题恢复独立参数。
  await assets.getByRole("button", { name: "滤镜", exact: true }).click();
  await assets.getByRole("button", { name: /^应用滤镜：/ }).first().click();
  await inspector.getByRole("combobox", { name: "视频滤镜", exact: true }).waitFor();
  await inspector.getByRole("button", { name: "移除当前画面对象" }).click();
  await inspector.waitFor({ state: "detached" });
  assert.equal(await applied.getByRole("button", { name: "编辑视频滤镜", exact: true }).count(), 0);
  await applied.getByRole("button", { name: "编辑底部字幕", exact: true }).click();
  assert.equal(await inspector.getByLabel("示例文字").inputValue(), "资产浏览器验证字幕");

  // 场景：未保存切换取消后保留草稿，页签隐藏再进入仍保留当前对象和独立参数。
  await page.getByRole("tab", { name: "主页", exact: true }).click();
  await page.getByRole("region", { name: "云端模板", exact: true }).getByRole("button", { name: "新建模板", exact: true }).click();
  await page.getByRole("dialog").getByLabel("模板名称", { exact: true }).fill("另一份验证草稿");
  await page.getByRole("dialog").getByRole("button", { name: "进入编辑", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("tab", { name: "主页", exact: true }).click();
  await page.getByRole("tab", { name: "模板库", exact: true }).click();
  assert.equal(await inspector.getByLabel("示例文字").inputValue(), "资产浏览器验证字幕");

  // 场景：三个文字对象的重置按钮恢复全部默认参数和动画选择，重复重置保持结果。
  for (const role of ["title", "bubble", "subtitle"]) {
    await assets.getByRole("button", { name: role === "bubble" ? "气泡" : "花字", exact: true }).click();
    if (role !== "bubble") await choose("应用到", textRoles[role]);
    await assets.getByRole("button", { name: role === "bubble" ? /^应用气泡：/ : /^应用花字：/ }).first().click();
    await inspector.getByLabel("示例文字").fill("重置之前的自定义文字");
    await inspector.getByLabel("字号", { exact: true }).fill("59");
    await inspector.getByLabel("水平位置 %").fill("17");
    await inspector.getByLabel("垂直位置 %").fill("61");
    await assets.getByRole("button", { name: "文字入场", exact: true }).click();
    await assets.getByRole("button", { name: /^应用文字入场：/ }).first().click();
    await assets.getByRole("button", { name: "文字出场", exact: true }).click();
    await assets.getByRole("button", { name: /^应用文字出场：/ }).first().click();
    await inspector.getByLabel("时长 / 秒", { exact: true }).nth(0).fill("1.7");
    await inspector.getByLabel("时长 / 秒", { exact: true }).nth(1).fill("2.3");
    for (let attempt = 0; attempt < 2; attempt++) {
      await inspector.getByRole("button", { name: "重置特效设置", exact: true }).click();
      assert.equal(await inspector.getByLabel("示例文字").inputValue(), defaultEditor[role === "bubble" ? "bubbleText" : role]);
      assert.equal(await inspector.getByLabel("字号", { exact: true }).inputValue(), String(defaultEditor[`${role}Size`]));
      assert.equal(await inspector.getByLabel("水平位置 %").inputValue(), String(defaultEditor[`${role}X`]));
      assert.equal(await inspector.getByLabel("垂直位置 %").inputValue(), String(defaultEditor[`${role}Y`]));
      for (const duration of await inspector.getByLabel("时长 / 秒", { exact: true }).all()) {
        assert.equal(await duration.inputValue(), "0.5");
        assert.equal(await duration.isDisabled(), true);
      }
      for (const label of [role === "bubble" ? "气泡样式" : "花字样式", "入场动画", "出场动画", "循环动画"])
        assert.equal(await inspector.getByRole("combobox", { name: label, exact: true }).textContent(), "无效果");
    }
    if (role !== "subtitle") {
      await applied.getByRole("button", { name: "编辑底部字幕", exact: true }).click();
      assert.equal(await inspector.getByLabel("示例文字").inputValue(), "资产浏览器验证字幕");
      assert.equal(await inspector.getByLabel("字号", { exact: true }).inputValue(), "36");
    }
  }

  // 场景：手机、平板和桌面下没有横向溢出，关闭及重新打开设置时视频尺寸保持不变。
  for (const width of [390, 768, 1280, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${width}px 页面横向超出`);
    for (const panel of [assets, preview, inspector]) {
      const box = await panel.boundingBox();
      assert(box.x >= 0 && box.x + box.width <= width + 1);
    }
    const frameSize = await frame.boundingBox();
    const text = await inspector.getByLabel("示例文字").inputValue();
    const close = inspector.getByRole("button", { name: "关闭特效设置", exact: true });
    await close.focus();
    await close.press("Enter");
    await inspector.waitFor({ state: "detached" });
    assert.equal(await applied.getByRole("button", { pressed: true }).count(), 0);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    const closedSize = await frame.boundingBox();
    assert.equal(closedSize.width, frameSize.width);
    assert.equal(closedSize.height, frameSize.height);
    await page.getByRole("tab", { name: "主页", exact: true }).click();
    await page.getByRole("tab", { name: "模板库", exact: true }).click();
    assert.equal(await inspector.count(), 0);
    await applied.getByRole("button", { name: "编辑底部字幕", exact: true }).click();
    assert.equal(await inspector.getByLabel("示例文字").inputValue(), text);
    const reopenedSize = await frame.boundingBox();
    assert.equal(reopenedSize.width, frameSize.width);
    assert.equal(reopenedSize.height, frameSize.height);
  }

  // 场景：移除各种画面对象都会关闭面板，重新添加真实资产后恢复对应设置。
  for (const [label, category] of [["顶部标题", "花字"], ["底部字幕", "花字"], ["气泡字", "气泡"], ["视频滤镜", "滤镜"], ["画面特效", "画面特效"], ["镜头转场", "转场"]]) {
    await assets.getByRole("button", { name: category, exact: true }).click();
    if (category === "花字") await choose("应用到", label);
    const asset = assets.getByRole("button", { name: new RegExp(`^应用${category}：`) }).first();
    await asset.click();
    const frameSize = await frame.boundingBox();
    await inspector.getByRole("button", { name: "移除当前画面对象", exact: true }).click();
    await inspector.waitFor({ state: "detached" });
    const removedSize = await frame.boundingBox();
    assert.equal(removedSize.width, frameSize.width);
    assert.equal(removedSize.height, frameSize.height);
    assert.equal(await applied.getByRole("button", { name: `编辑${label}`, exact: true }).evaluateAll((buttons) => buttons.filter((button) => button.hasAttribute("aria-pressed")).length), 0);
    assert.equal(await asset.getAttribute("aria-pressed"), "false");
    await asset.click();
    await inspector.getByText(label, { exact: true }).first().waitFor();
    assert.equal(await applied.getByRole("button", { name: `编辑${label}`, pressed: true }).count(), 1);
  }
  await ready();
  assert.deepEqual(writes, []);
  assert.deepEqual(errors, []);
  console.log("PASS：真实 SDK 播放、资产搜索与应用、独立参数、动画互斥、完整重置、移除自动关闭、关闭后恢复、未保存保护、页签保留和四种宽度；未写入模板数据。");
} finally {
  await browser.close();
  rmSync(temporaryDirectory, { recursive: true, force: true });
}
