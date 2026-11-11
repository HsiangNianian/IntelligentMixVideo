/** Tutorial regression tests: offline assets, print validation, and homepage readiness before screenshot capture. Run bun test ./.github/scripts. */
import { test, expect } from "bun:test";
import { mkdtemp, writeFile, rm, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRequire } from "node:module";
import { assembleTutorial, validateTutorial } from "./build-tutorial.mjs";

// A shareable HTML artifact must retain its images and stylesheet after leaving the checkout.
test("tutorial HTML embeds local assets and escapes the build label", async () => {
  const root = await mkdtemp(join(tmpdir(), "imv-tutorial-"));
  try {
    await writeFile(join(root, "index.html"), '<link rel="stylesheet" href="print.css"><p>{{BUILD_COMMIT}}</p><img src="image.png" alt="示例">');
    await writeFile(join(root, "print.css"), "body { color: black; }");
    await writeFile(join(root, "image.png"), Buffer.from([137, 80, 78, 71]));
    const result = await assembleTutorial(root, 'abc<&"');
    expect(result).toContain("<style>body { color: black; }</style>");
    expect(result).toContain('src="data:image/png;base64,iVBORw=="');
    expect(result).toContain("abc&lt;&amp;&quot;");
  } finally { await rm(root, { recursive: true, force: true }); }
});

// Real Chromium checks catch broken content and pagination before users receive a PDF.
test.skipIf(process.env.IMV_TEST_TUTORIAL_BROWSER !== "1")("tutorial print validation detects missing links, broken images and overflow", async () => {
  const require = createRequire(new URL("../../client/package.json", import.meta.url));
  const { chromium } = require("playwright-core");
  const browser = await chromium.launch({ headless: true, ...(process.env.IMV_CHROME_PATH ? { executablePath: process.env.IMV_CHROME_PATH } : {}) });
  try {
    const page = await browser.newPage();
    await page.emulateMedia({ media: "print" });
    await page.setContent('<section class="page" id="intro"><a href="#intro">目录</a></section>');
    await validateTutorial(page);
    await page.setContent('<section class="page"><a href="#missing">目录</a></section>');
    await expect(validateTutorial(page)).rejects.toThrow("Missing destination");
    await page.setContent('<section class="page"><img src="data:image/png;base64,broken" alt="坏图"></section>');
    await expect(validateTutorial(page)).rejects.toThrow("Broken image");
    await page.setContent('<section class="page" style="height:20px"><div style="height:100px">超长段落</div></section>');
    await expect(validateTutorial(page)).rejects.toThrow("overflows");
  } finally { await browser.close(); }
}, 30_000);

// 首页控件位于开场遮罩后仍会被判定为 visible；必须等待遮罩退出，并验证可交互而不实际点击。
test.skipIf(process.env.IMV_TEST_TUTORIAL_BROWSER !== "1")("tutorial homepage waits for overlays without clicking controls", async () => {
  const require = createRequire(new URL("../../client/package.json", import.meta.url));
  const { chromium } = require("playwright-core");
  const { waitForTutorialHome } = await import("../../docs/tutorial/capture.ts");
  const browser = await chromium.launch({ headless: true, ...(process.env.IMV_CHROME_PATH ? { executablePath: process.env.IMV_CHROME_PATH } : {}) });
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(500);
    await page.setContent('<button onclick="document.body.dataset.clicked = \'yes\'">新建云端模板</button><div id="splash" role="button" aria-label="IntelligentMixVideo 开场动画，点击跳过" style="position:fixed;inset:0;z-index:80;background:white">开场画面</div>');
    expect(await page.getByRole("button", { name: "新建云端模板", exact: true }).isVisible()).toBe(true);
    await expect(waitForTutorialHome(page)).rejects.toThrow("Timeout");
    // Even a non-interactive splash can cover the screenshot, so input hit-testing alone is insufficient.
    await page.locator("#splash").evaluate(element => { element.style.pointerEvents = "none"; });
    await expect(waitForTutorialHome(page)).rejects.toThrow("Timeout");
    await page.locator("#splash").evaluate(element => element.remove());
    await waitForTutorialHome(page);
    expect(await page.locator("body").getAttribute("data-clicked")).toBeNull();
    // An unrelated overlay must also prevent capturing a blocked homepage after the splash has gone.
    await page.evaluate(() => {
      const blocker = document.createElement("div");
      blocker.style.cssText = "position:fixed;inset:0;z-index:80;background:white";
      document.body.append(blocker);
    });
    await expect(waitForTutorialHome(page)).rejects.toThrow("Timeout");
  } finally { await browser.close(); }
}, 30_000);

// Broken or misplaced files must fail the build instead of silently producing an incomplete PDF.
test("tutorial rejects missing images and files outside its source directory", async () => {
  const parent = await mkdtemp(join(tmpdir(), "imv-tutorial-"));
  const root = join(parent, "source");
  try {
    await mkdir(root);
    await writeFile(join(root, "index.html"), '<img src="missing.png" alt="Missing">');
    await expect(assembleTutorial(root, "abc")).rejects.toThrow();
    await writeFile(join(parent, "outside.png"), "outside");
    await writeFile(join(root, "index.html"), '<img src="../outside.png" alt="Outside">');
    await expect(assembleTutorial(root, "abc")).rejects.toThrow("Asset outside tutorial");
  } finally { await rm(parent, { recursive: true, force: true }); }
});
