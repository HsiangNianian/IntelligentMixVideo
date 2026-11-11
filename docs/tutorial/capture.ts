/**
 * Deterministic tutorial screenshots: start the real Vite client, intercept business requests with
 * local fixtures, perform documented interactions, and add numbered DOM callouts. No AI, cloud
 * rendering or real account data is used. Run with Bun; the PDF builder calls captureTutorial.
 */
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { cp, mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { Locator, Page } from "../../client/node_modules/playwright-core";
import { savedTemplate, jsonListResponse, jsonTemplateResponse } from "../../client/tests/fixtures";
import { ListSpritesResponseSchema } from "../../client/src/generated/imv/sprite/v1/sprite_pb";

const repository = fileURLToPath(new URL("../../", import.meta.url));
const source = join(repository, "docs/tutorial");
const client = join(repository, "client");
const require = createRequire(join(client, "package.json"));
const { create, toBinary } = require("@bufbuild/protobuf");
const { chromium } = require("playwright-core");
const apiOrigin = "http://tutorial-api.invalid";
const viewport = { width: 1440, height: 1000 };

/** Valid template responses come from the same shared wire fixtures as the client's core tests. */
function demoTemplates() {
  const sample = savedTemplate();
  sample.name = "海外口播 · 双语模板";
  sample.description = "教程示例：竖屏标题、中英字幕与上下蒙版";
  sample.tracks[0].editor.title = "让每一个想法被看见";
  sample.tracks[1].editor.subtitle = "从第一个模板开始";
  sample.created_at = sample.updated_at = "2026-11-09T00:00:00.000Z";
  const second = savedTemplate();
  second.template_id = "00000000-0000-4000-8000-000000000002";
  second.name = "简洁口播 · 浅色模板";
  second.description = "教程示例：比较不同的字幕样式";
  second.created_at = second.updated_at = sample.updated_at;
  return [sample, second];
}

/** Six catalog categories exercise the real protobuf decoder; no generated effects are executed. */
function demoSprites() {
  return toBinary(ListSpritesResponseSchema, create(ListSpritesResponseSchema, {
    sprites: [
      { name: "电影感标题", kind: 1, textCategory: 1 },
      { name: "清晰口播字幕", kind: 1, textCategory: 2 },
      { name: "中英双语字幕", kind: 1, parameters: [{ key: "textEn", label: "英文", access: 1, defaultValue: { value: { case: "stringValue", value: "Hello" } } }] },
      { name: "柔光滤镜", kind: 2 }, { name: "闪白转场", kind: 4 }, { name: "粒子光点", kind: 3 },
    ].map((sprite, index) => ({ ...sprite, spriteId: `demo-${index}`, canvas: { width: 1080, height: 1920, fps: 30, previewFrames: 150 } })),
  }));
}

/** A bundled, licensed font supplies a real local font file for the demo library and detail view. */
async function demoFont() {
  const bytes = await readFile(join(client, "node_modules/@fontsource-variable/geist/files/geist-latin-wght-normal.woff2"));
  const face = { id: "geist-regular", slot: "regular", weights: [100, 900], size: bytes.length, sha256: "", file_url: "/api/templates/fonts/demo.woff2", preview_url: null };
  return { bytes, card: {
    id: "geist", family: "Geist", label: "Geist", style: "无衬线可变字体 · 教程演示目录", weights: [100, 900],
    source: "builtin", license: "OFL-1.1", size: bytes.length, file_url: face.file_url, preview_url: null, faces: [face],
  } };
}

/** Wait for the startup overlay to leave and verify that the homepage can receive input without clicking it. */
export async function waitForTutorialHome(page: Page) {
  const create = page.getByRole("button", { name: "新建云端模板", exact: true });
  await create.waitFor({ state: "visible" });
  await page.getByRole("button", { name: "IntelligentMixVideo 开场动画，点击跳过", exact: true, includeHidden: true }).waitFor({ state: "hidden" });
  await create.click({ trial: true });
}

/** Outline visible controls in DOM coordinates; labels are drawn by the browser and removed after capture. */
async function capture(page: Page, directory: string, name: string, controls: Locator[]) {
  await page.evaluate(() => document.fonts.ready);
  // Timeline interactions may scroll the middle pane; show its preview from the top in each figure.
  await page.locator(".template-workspace-canvas").evaluateAll(elements => elements.forEach(element => { element.scrollTop = 0; }));
  for (const control of controls) {
    await control.waitFor({ state: "visible" });
    await control.scrollIntoViewIfNeeded();
  }
  const boxes = [];
  for (const [index, control] of controls.entries()) {
    await control.waitFor({ state: "visible" });
    const box = await control.boundingBox();
    assert(box && box.width > 0 && box.height > 0, `${name}: step ${index + 1} is hidden`);
    assert(box.x >= -1 && box.y >= -1 && box.x + box.width <= viewport.width + 1 && box.y + box.height <= viewport.height + 1,
      `${name}: step ${index + 1} is outside the viewport`);
    boxes.push(box);
  }
  await page.evaluate((rectangles) => {
    const overlay = document.createElement("div");
    overlay.id = "tutorial-callouts";
    overlay.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:2147483647";
    rectangles.forEach((box, index) => {
      const outline = document.createElement("div");
      outline.style.cssText = `position:absolute;left:${box.x}px;top:${box.y}px;width:${box.width}px;height:${box.height}px;border:2px solid #e54949;border-radius:6px;box-sizing:border-box`;
      const label = document.createElement("span");
      label.textContent = String(index + 1);
      label.style.cssText = `position:absolute;left:${Math.max(3, box.x - 11)}px;top:${Math.max(3, box.y - 17)}px;width:27px;height:27px;border:2px solid white;border-radius:50%;background:#e54949;color:white;text-align:center;font:bold 17px/23px sans-serif;box-shadow:0 1px 4px #0003`;
      overlay.append(outline, label);
    });
    document.body.append(overlay);
  }, boxes);
  try {
    await page.screenshot({ path: join(directory, name), animations: "disabled" });
  } finally {
    await page.locator("#tutorial-callouts").evaluate(element => element.remove());
  }
  console.log(`Captured ${name}`);
}

/** Capture all referenced figures in a fresh source snapshot; browser and Vite always shut down, including on failure. */
export async function captureTutorial(destination: string) {
  await mkdir(join(destination, "images"), { recursive: true });
  await cp(join(source, "index.html"), join(destination, "index.html"));
  await cp(join(source, "print.css"), join(destination, "print.css"));
  const { createServer } = require("vite");
  const vite = await createServer({
    root: client, configFile: join(client, "vite.config.ts"), envDir: false,
    define: { "import.meta.env.VITE_API_URL": JSON.stringify(apiOrigin), "import.meta.env.IMV_DEBUG": JSON.stringify("false") },
    server: { host: "127.0.0.1", port: 0, strictPort: true, hmr: false, open: false },
    logLevel: "error",
  });
  let browser;
  try {
    await vite.listen();
    const address = vite.httpServer!.address();
    assert(address && typeof address !== "string");
    const appOrigin = `http://127.0.0.1:${address.port}`;
    browser = await chromium.launch({ headless: true, ...(process.env.IMV_CHROME_PATH ? { executablePath: process.env.IMV_CHROME_PATH } : {}) });
    const page: Page = await browser.newPage({ viewport, deviceScaleFactor: 1, locale: "zh-CN", timezoneId: "Asia/Shanghai", reducedMotion: "reduce", serviceWorkers: "block" });
    page.setDefaultTimeout(30_000);
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.addInitScript(() => { if (window === window.top) localStorage.setItem("imv.theme", "light"); });
    await page.clock.setFixedTime(new Date("2026-11-09T08:00:00+08:00"));
    const templates = demoTemplates(), sprites = demoSprites(), font = await demoFont();
    // The preview package explicitly reports no rendering capabilities: the UI shows an honest empty cover.
    const emptyPreview = '<!doctype html><script>parent.postMessage({type:"imv-preview-ready",channel:location.hash.slice(1)},"*");</script>';
    await page.route("**/*", async route => {
      const url = new URL(route.request().url());
      if (url.origin === appOrigin) return route.continue();
      if (url.origin !== apiOrigin) return route.abort(); // IMS SDK and remote thumbnails stay offline.
      if (route.request().method() !== "GET") {
        errors.push(`Tutorial must not submit business writes: ${url.pathname}`);
        return route.fulfill({ status: 405, json: { detail: "Tutorial fixtures are read-only" } });
      }
      const path = url.pathname;
      if (path === "/template") return route.fulfill({ json: await jsonListResponse(templates).json() });
      const record = templates.find(item => path === `/template/${item.template_id}`);
      if (record) return route.fulfill({ json: await jsonTemplateResponse(record, "get").json() });
      if (path === "/api/sprites") return route.fulfill({ body: Buffer.from(sprites), contentType: "application/x-protobuf" });
      if (path === "/api/sprites/stream") return route.fulfill({ body: ": connected\n\n", contentType: "text/event-stream" });
      if (/^\/api\/sprites\/demo-\d+\/preview$/.test(path)) return route.fulfill({ body: emptyPreview, contentType: "text/html" });
      if (path === "/api/templates/capabilities") return route.fulfill({ json: { models_configured: true } });
      if (path === "/api/templates/fonts") return route.fulfill({ json: [font.card] });
      if (path === "/api/templates/fonts/demo.woff2") return route.fulfill({ body: font.bytes, contentType: "font/woff2" });
      errors.push(`Unhandled demonstration API: ${path}`);
      return route.fulfill({ status: 404, json: { detail: "Not part of the tutorial fixture" } });
    });
    const images = join(destination, "images");
    /** Every picture uses the real control names; renamed or missing controls fail the documentation build. */
    const captured = new Set<string>();
    const shot = async (name: string, ...controls: Locator[]) => {
      assert(!captured.has(name), `Duplicate screenshot: ${name}`);
      await capture(page, images, name, controls);
      captured.add(name);
    };
    const button = (name: string) => page.getByRole("button", { name, exact: true });
    const tab = (name: string) => page.getByRole("tab", { name, exact: true });
    const object = (name: string) => page.getByRole("region", { name: "已添加特效", exact: true }).getByRole("button", { name: `编辑${name}`, exact: true });
    await page.goto(appOrigin);
    await button(`选择模板：${templates[0].name}`).waitFor();
    await waitForTutorialHome(page);
    // Match the four numbered descriptions: navigation, both creation cards, templates, and preferences.
    await shot("home.png", page.getByRole("navigation", { name: "主导航", exact: true }).getByRole("list"), button("新建云端模板").locator(".."), page.getByRole("region", { name: "模板列表", exact: true }), button("设置").locator(".."));
    await button("设置").click();
    await shot("settings.png", page.getByLabel("后端服务地址", { exact: true }), button("保存"));
    await page.keyboard.press("Escape");
    await button("新建云端模板").click();
    await page.getByRole("textbox", { name: "模板名称", exact: true }).fill("带货口播 · 黄色花字");
    await page.getByRole("textbox", { name: "模板描述", exact: true }).fill("教程示例：清晰标题与底部字幕");
    await shot("new-template.png", page.getByRole("textbox", { name: "模板名称", exact: true }), page.getByRole("textbox", { name: "模板描述", exact: true }), button("进入编辑"));
    await button("取消").click();
    await button(`选择模板：${templates[0].name}`).hover();
    await shot("manage.png", button("打开编辑").first(), button("复制模板 ID").first(), button(`删除模板：${templates[0].name}`));
    await button(`选择模板：${templates[0].name}`).click();
    await page.getByText("基础视频已就绪，点击播放查看效果", { exact: true }).waitFor();
    await shot("editor.png", button("保存模板"), tab("特效素材"), button("刷新预览"), page.getByText("时间轴", { exact: true }), page.getByRole("region", { name: "已添加特效", exact: true }));
    await button("编辑模板名称").click();
    await shot("rename.png", page.getByRole("textbox", { name: "模板名称", exact: true }), button("编辑模板描述"));
    await page.keyboard.press("Escape");
    const flower = page.getByRole("button", { name: /^应用花字：/ }).first();
    await shot("effects.png", page.getByRole("radio", { name: /^花字/ }), page.getByLabel("应用到", { exact: true }), flower);
    await tab("外观与效果").click();
    await shot("text-style.png", tab("外观与效果"), page.getByLabel("示例文字", { exact: true }), page.getByRole("combobox", { name: "字体", exact: true }));
    await page.getByRole("checkbox", { name: "中英双语", exact: true }).check();
    await shot("bilingual-title.png", page.getByRole("checkbox", { name: "中英双语", exact: true }), object("英文标题"), object("中文标题"));
    await object("底部字幕").click();
    await page.getByRole("checkbox", { name: "中英双语", exact: true }).check();
    await shot("bilingual-subtitles.png", page.getByRole("checkbox", { name: "中英双语", exact: true }), object("英文字幕"), object("中文字幕"));
    await tab("关键词设置").click();
    await shot("keywords.png", tab("关键词设置"), page.getByRole("group", { name: "关键词样式", exact: true }), page.getByLabel("设置关键词颜色", { exact: true }));
    await object("中文标题").click();
    await tab("时间设置").click();
    await shot("timing.png", tab("时间设置"), page.getByLabel("开始方式", { exact: true }), page.getByLabel("持续方式", { exact: true }));
    await button("镜头转场").click();
    await shot("transitions.png", page.getByRole("switch", { name: "使用转场", exact: true }), page.getByRole("combobox", { name: "添加转场", exact: true }), page.getByLabel("IMS 转场持续时间 / 秒", { exact: true }), page.getByRole("radiogroup", { name: "转场选择方式", exact: true }));
    await button("加载预览视频").click();
    await shot("preview-videos.png", page.getByLabel("视频 1 URL", { exact: true }), button("为视频 1 选择本地文件"), button("加载"));
    await page.keyboard.press("Escape");
    await tab("AI 资产").click();
    await page.getByRole("radio", { name: /^双语字效/ }).waitFor();
    await shot("assets.png", tab("AI 资产"), page.getByRole("radiogroup", { name: "AI 资产分类", exact: true }));
    await tab("蒙版").click();
    await button("选择上下蒙版").click();
    await page.getByRole("radio", { name: "上栏渐变", exact: true }).click();
    await shot("masks.png", tab("蒙版"), page.getByRole("switch", { name: "启用上栏", exact: true }), page.getByRole("radio", { name: "上栏渐变", exact: true }), button("保存模板"));
    await button("主页").click();
    await button(`选择模板：${templates[1].name}`).click();
    await shot("unsaved.png", button("保存并切换"), button("放弃修改"), button("取消"));
    await button("取消").click();
    await button("AI 字效").click();
    await page.getByLabel("字效描述", { exact: true }).fill("做一个中英双语字幕，中文白色粗体，英文暖黄色，每组同步淡入。");
    await page.getByRole("switch", { name: "双语字幕", exact: true }).click();
    await shot("ai-bilingual.png", button("新建会话"), page.getByLabel("字效描述", { exact: true }), button("图片"), page.getByLabel("预览时长", { exact: true }), page.getByRole("switch", { name: "双语字幕", exact: true }), button("发送"));
    await button("操练场").click();
    await page.getByText(templates[0].name, { exact: true }).last().waitFor();
    await shot("playground.png", button("操练场"), page.getByText(templates[0].name, { exact: true }).last(), button("开始合成"));
    await button("字体库").click();
    await button("查看字体 Geist").waitFor();
    await shot("fonts.png", button("字体库"), page.getByLabel("搜索字体", { exact: true }), button("上传字体"), button("查看字体 Geist"));
    await button("查看字体 Geist").click();
    await shot("font-details.png", page.getByRole("dialog"));
    assert.deepEqual(errors, [], "Unexpected runtime or fixture errors");
    const html = await readFile(join(destination, "index.html"), "utf8");
    const expected = [...html.matchAll(/<img\b[^>]*src="(images\/[^\"]+)"/g)].map(match => match[1]);
    assert.deepEqual([...captured].map(name => `images/${name}`).sort(), [...new Set(expected)].sort(), "Every referenced figure must be freshly captured");
    for (const filename of expected) assert((await readFile(join(destination, filename))).length > 0, `Missing screenshot: ${filename}`);
    await writeFile(join(destination, "screenshots.json"), JSON.stringify({ viewport, browser: browser.version(), screenshots: expected, aiCalls: 0 }, null, 2) + "\n");
    console.log(`Captured all ${expected.length} tutorial figures without AI or cloud services.`);
  } catch (error) {
    if (browser) {
      const page = browser.contexts()[0]?.pages()[0];
      if (page) await writeFile(join(destination, "failure.txt"), await page.locator("body").innerText()).catch(() => {});
    }
    throw error;
  } finally {
    await browser?.close();
    await vite.close();
  }
}

if (import.meta.main) await captureTutorial(resolve(process.argv[2] || join(repository, "dist/tutorial/source")));
