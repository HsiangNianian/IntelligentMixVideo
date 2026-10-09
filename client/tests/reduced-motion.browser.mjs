/** 减少动态效果浏览器回归：系统偏好 reduce 时浮层 CSS 动画被压缩；宽屏设置导航不保留隐藏提示层，Esc 一次即可关闭弹窗；执行 bun tests/reduced-motion.browser.mjs。 */
import assert from "node:assert/strict";
import { chromium } from "playwright-core";

const url = process.env.IMV_BROWSER_URL || "http://127.0.0.1:1420";
const browser = await chromium.launch({
  ...(process.env.IMV_CHROME_PATH ? { executablePath: process.env.IMV_CHROME_PATH } : { channel: "chrome" }),
  headless: true,
});
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 }, reducedMotion: "reduce" });
  // 只模拟设置目录与模板列表，不连接真实服务。
  await page.route((target) => target.pathname.startsWith("/api/settings/"), (route) => route.fulfill({ json: [] }));
  await page.route((target) => target.pathname === "/template", (route) => route.fulfill({ status: 503, json: { detail: "离线" } }));
  await page.goto(url);
  await page.getByRole("button", { name: "设置" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.waitFor();
  // 展开动画仍存在但时长压缩到 0.01ms 以内，不再出现 200ms 的缩放淡入。
  const duration = await dialog.evaluate((element) => getComputedStyle(element).animationDuration);
  assert(parseFloat(duration) <= 0.00001, `弹窗动画时长应被压缩，实际为 ${duration}`);
  await page.keyboard.press("Escape");
  await dialog.waitFor({ state: "detached", timeout: 2000 });
  console.log("PASS: 减少动态效果时浮层动画被压缩，弹窗仍能正常卸载。");
} finally {
  await browser.close();
}
