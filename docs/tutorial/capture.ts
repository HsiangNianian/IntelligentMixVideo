/**
 * Deterministic tutorial screenshots: start the real Vite client, intercept business requests with
 * local fixtures, perform documented interactions, and add numbered SVG callouts. No AI, cloud
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
import { renderCallouts } from "./callouts.mjs";

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

/** Placement is relative to the measured control; paddingY can tighten dense rows without crowding their text. */
type CalloutOptions = { side?: "left" | "right" | "top" | "bottom"; align?: number; shift?: number; gap?: number; size?: number; padding?: number; paddingY?: number };
type Callout = CalloutOptions & { targets: Locator[] };

/** Group related controls into one contour and place its numbered arrow in nearby whitespace. */
const mark = (target: Locator | Locator[], options: CalloutOptions = {}): Callout => ({ targets: Array.isArray(target) ? target : [target], ...options });

/** Measure the live UI, draw authored SVG callouts, and remove them after each screenshot. */
async function capture(page: Page, directory: string, name: string, controls: (Locator | Callout)[]) {
  const annotations = controls.map(control => "targets" in control ? control : mark(control));
  await page.evaluate(() => document.fonts.ready);
  // Timeline interactions may scroll the middle pane; show its preview from the top in each figure.
  await page.locator(".template-workspace-canvas").evaluateAll(elements => elements.forEach(element => { element.scrollTop = 0; }));
  for (const control of annotations.flatMap(annotation => annotation.targets)) {
    await control.waitFor({ state: "visible" });
    await control.scrollIntoViewIfNeeded();
  }
  const boxes = [];
  for (const [index, { targets, ...options }] of annotations.entries()) {
    const members = [];
    for (const control of targets) {
      await control.waitFor({ state: "visible" });
      const box = await control.boundingBox();
      assert(box && box.width > 0 && box.height > 0, `${name}: step ${index + 1} is hidden`);
      assert(box.x >= -1 && box.y >= -1 && box.x + box.width <= viewport.width + 1 && box.y + box.height <= viewport.height + 1,
        `${name}: step ${index + 1} is outside the viewport`);
      members.push(box);
    }
    const x = Math.min(...members.map(box => box.x)), y = Math.min(...members.map(box => box.y));
    boxes.push({ ...options, box: { x, y, width: Math.max(...members.map(box => box.x + box.width)) - x, height: Math.max(...members.map(box => box.y + box.height)) - y } });
  }
  let svg;
  try { svg = renderCallouts(boxes, viewport); } catch (error) { throw new Error(`${name}: ${error}`); }
  await page.evaluate((drawing) => {
    const overlay = document.createElement("div");
    overlay.id = "tutorial-callouts";
    overlay.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:2147483647";
    overlay.innerHTML = drawing;
    document.body.append(overlay);
  }, svg);
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
    const shot = async (name: string, ...controls: (Locator | Callout)[]) => {
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
    await shot("home.png",
      mark(page.getByRole("navigation", { name: "主导航", exact: true }).getByRole("list"), { side: "top", align: .85, shift: 12, gap: 40, size: 52 }),
      mark(button("新建云端模板").locator(".."), { side: "top", align: .6, shift: 70, gap: 54, size: 56 }),
      mark([page.getByRole("heading", { name: "我的模板", exact: true }), ...templates.map(item => button(`选择模板：${item.name}`))], { side: "right", align: .16, shift: -24, size: 56 }),
      mark(button("设置").locator(".."), { side: "right", align: .2, shift: -32, size: 52 }));
    await button("设置").click();
    await shot("settings.png", mark(page.getByLabel("后端服务地址", { exact: true }), { side: "left" }), mark(button("保存"), { side: "right" }));
    await page.keyboard.press("Escape");
    await button("新建云端模板").click();
    await page.getByRole("textbox", { name: "模板名称", exact: true }).fill("带货口播 · 黄色花字");
    await page.getByRole("textbox", { name: "模板描述", exact: true }).fill("教程示例：清晰标题与底部字幕");
    await shot("new-template.png",
      mark(page.getByRole("textbox", { name: "模板名称", exact: true }), { side: "left" }),
      mark(page.getByRole("textbox", { name: "模板描述", exact: true }), { side: "left" }),
      mark(button("进入编辑"), { side: "right", shift: -30 }));
    await button("取消").click();
    // The last card leaves open space to its right for three distinct operation labels.
    await button(`选择模板：${templates[1].name}`).hover();
    await shot("manage.png",
      mark(button("打开编辑").last(), { side: "right", gap: 84, shift: -34, size: 44, padding: 3 }),
      mark(button("复制模板 ID").last(), { side: "right", gap: 84, shift: 0, size: 44, padding: 3 }),
      mark(button(`删除模板：${templates[1].name}`), { side: "right", gap: 84, shift: 34, size: 44, padding: 3 }));
    await button(`选择模板：${templates[0].name}`).click();
    await page.getByText("基础视频已就绪，点击播放查看效果", { exact: true }).waitFor();
    await shot("editor.png",
      mark(button("保存模板").locator(".."), { side: "left", shift: 0, size: 44 }),
      mark(page.locator(".template-workspace-assets"), { side: "right", align: .08, shift: 24, gap: 52, size: 44, padding: 2 }),
      mark(page.getByLabel("模板视频预览", { exact: true }), { side: "top", align: .85, shift: 40, gap: 52, size: 44 }),
      mark(page.getByText("时间轴", { exact: true }).locator("../.."), { side: "top", align: .92, shift: 26, gap: 48, size: 44 }),
      mark(page.getByRole("region", { name: "已添加特效", exact: true }), { side: "left", align: .5, shift: 28, size: 44 }));
    await button("编辑模板名称").click();
    await shot("rename.png",
      mark(page.getByRole("textbox", { name: "模板名称", exact: true }), { side: "bottom", align: .85, shift: -66, gap: 76 }),
      mark(button("编辑模板描述"), { side: "bottom", shift: -56, gap: 48 }));
    await page.keyboard.press("Escape");
    const flower = page.getByRole("button", { name: /^应用花字：/ }).nth(1);
    await shot("effects.png",
      mark(page.getByRole("radio", { name: /^花字/ }), { side: "top", align: .85, gap: 24, shift: 70 }),
      mark(page.getByLabel("应用到", { exact: true }), { side: "right", gap: 70 }),
      mark(flower, { side: "right", gap: 75, shift: 26 }));
    await tab("外观与效果").click();
    await shot("text-style.png",
      mark(tab("外观与效果"), { side: "left" }),
      mark(page.getByLabel("示例文字", { exact: true }), { side: "left" }),
      mark(page.getByRole("combobox", { name: "字体", exact: true }), { side: "left", shift: 0 }));
    await page.getByRole("checkbox", { name: "中英双语", exact: true }).check();
    await shot("bilingual-title.png",
      mark(page.getByRole("checkbox", { name: "中英双语", exact: true }).locator(".."), { side: "left" }),
      mark(object("英文标题"), { side: "left", shift: 12, padding: 2 }),
      mark(object("中文标题"), { side: "left", shift: -6, padding: 2 }));
    await object("底部字幕").click();
    await page.getByRole("checkbox", { name: "中英双语", exact: true }).check();
    await shot("bilingual-subtitles.png",
      mark(page.getByRole("checkbox", { name: "中英双语", exact: true }).locator(".."), { side: "left" }),
      mark(object("英文字幕"), { side: "left", shift: 12, padding: 2 }),
      mark(object("中文字幕"), { side: "left", shift: -6, padding: 2 }));
    await tab("关键词设置").click();
    await shot("keywords.png",
      mark(tab("关键词设置"), { side: "top", shift: 0, gap: 56 }),
      mark(page.getByRole("group", { name: "关键词样式", exact: true }).getByRole("toolbar"), { side: "left", padding: 1 }),
      mark(page.getByLabel("设置关键词颜色", { exact: true }).locator(".."), { side: "left", shift: 14, paddingY: 1 }));
    await object("中文标题").click();
    await tab("时间设置").click();
    await shot("timing.png",
      mark(tab("时间设置"), { side: "top", shift: 32, gap: 56 }),
      mark(page.getByLabel("开始方式", { exact: true }), { side: "left", shift: -58, gap: 100 }),
      page.getByLabel("持续方式", { exact: true }));
    await button("镜头转场").click();
    await shot("transitions.png",
      mark(page.getByRole("switch", { name: "使用转场", exact: true }).locator(".."), { side: "left" }),
      mark(page.getByRole("combobox", { name: "添加转场", exact: true }), { side: "left", shift: 20, padding: 1 }),
      mark(page.getByLabel("IMS 转场持续时间 / 秒", { exact: true }).locator(".."), { side: "left", shift: 0, padding: 1 }),
      mark(page.getByRole("radiogroup", { name: "转场选择方式", exact: true }), { side: "left", shift: 0 }));
    await button("加载预览视频").click();
    await shot("preview-videos.png",
      mark(page.getByLabel("视频 1 URL", { exact: true }), { side: "left", padding: 1 }),
      mark(button("为视频 1 选择本地文件"), { side: "right", padding: 1 }),
      mark(button("加载"), { side: "right" }));
    await page.keyboard.press("Escape");
    await tab("AI 资产").click();
    await page.getByRole("radio", { name: /^双语字效/ }).waitFor();
    await shot("assets.png",
      mark(tab("AI 资产"), { side: "right", align: .95, gap: 140, shift: 42 }),
      mark(page.getByRole("radiogroup", { name: "AI 资产分类", exact: true }), { side: "right", gap: 64 }));
    await tab("蒙版").click();
    await button("选择上下蒙版").click();
    await page.getByRole("radio", { name: "上栏渐变", exact: true }).click();
    await shot("masks.png",
      tab("蒙版"),
      mark(page.getByRole("switch", { name: "启用上栏", exact: true }).locator(".."), { side: "left" }),
      mark(page.getByRole("radiogroup", { name: "上栏填充方式", exact: true }), { side: "left", shift: 20 }),
      mark(button("保存模板").locator(".."), { side: "left", shift: 0, size: 44 }));
    await button("主页").click();
    await button(`选择模板：${templates[1].name}`).click();
    await shot("unsaved.png",
      mark(button("保存并切换"), { side: "right", padding: 1 }),
      mark(button("放弃修改"), { side: "bottom", shift: 0, padding: 1 }),
      mark(button("取消"), { side: "left", shift: 0, padding: 1 }));
    await button("取消").click();
    await button("AI 字效").click();
    await page.getByLabel("字效描述", { exact: true }).fill("做一个中英双语字幕，中文白色粗体，英文暖黄色，每组同步淡入。");
    await page.getByRole("switch", { name: "双语字幕", exact: true }).click();
    await shot("ai-bilingual.png",
      mark(button("新建会话"), { side: "bottom", shift: 60, gap: 60 }),
      mark(page.getByLabel("字效描述", { exact: true }), { side: "top", align: .75, shift: 24, gap: 60, paddingY: 1 }),
      mark(button("图片"), { side: "left", gap: 64, shift: 0, size: 44, padding: 1 }),
      mark(page.getByLabel("预览时长", { exact: true }), { side: "right", shift: 116, gap: 110, size: 44, padding: 2 }),
      mark(page.getByRole("switch", { name: "双语字幕", exact: true }).locator(".."), { side: "bottom", shift: 38, gap: 150, size: 44, padding: 1 }),
      mark(button("发送"), { side: "bottom", shift: 38, gap: 64, size: 44 }));
    await button("操练场").click();
    await page.getByText(templates[0].name, { exact: true }).last().waitFor();
    await shot("playground.png", mark(button("操练场"), { side: "bottom", shift: 70, gap: 86 }), page.getByText(templates[0].name, { exact: true }).last(), button("开始合成"));
    await button("字体库").click();
    await button("查看字体 Geist").waitFor();
    await shot("fonts.png",
      mark(button("字体库"), { side: "right", align: .95, shift: 140, gap: 76 }),
      mark(page.getByLabel("搜索字体", { exact: true }), { side: "right", align: .2, shift: -50, gap: 110 }),
      mark(button("上传字体"), { side: "left" }),
      mark(button("查看字体 Geist"), { side: "right", align: .3 }));
    await button("查看字体 Geist").click();
    await shot("font-details.png",
      mark(page.getByRole("dialog").locator("dl"), { side: "left" }),
      mark(page.getByRole("button", { name: "查看常规字样", exact: true }), { side: "left", shift: 20, padding: 1 }),
      mark(page.getByRole("listitem", { name: "粗体", exact: true }), { side: "right", shift: 0, padding: 1 }));
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
