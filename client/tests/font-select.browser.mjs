/** ego-browser 回归：真实组件的十一种字形、搜索、键盘选择、独立状态和 Remotion 目录；运行方式见 client/README.md。 */
import assert from "node:assert/strict";

/** 使用已启动的 Vite 和独立后端，不创建业务模板或上传字体。 */
export async function testFontSelect(page, origin = "http://localhost:18766") {
  /** 等待浮层关闭动画完成，后续查询只包含当前选择器的选项。 */
  const closed = () => page.waitForFunction(() => document.querySelectorAll('[data-slot="popover-content"]').length === 0);
  await page.goto(`${origin}/tests/font-select.html`);
  console.log(await page.snapshot());
  await page.click("#title-font");
  const labels = await page.evaluate(() => [...document.querySelectorAll('[role="option"] [role="img"]')].map((node) => node.getAttribute("aria-label")));
  assert.equal(labels.length, 11);
  assert.equal(new Set(labels).size, 11);
  // 场景：每种字体的预览条都能解码，包含文字像素和透明背景，实际选项使用对应的图片。
  const previews = await page.evaluate(async () => {
    const { templateFonts } = await import("/src/features/templates/fonts.ts");
    const results = [];
    for (const font of templateFonts) {
      const url = new URL(`/src/features/templates/font-previews/${font.value}.png`, location.href).href;
      const image = new Image();
      image.src = url;
      await image.decode();
      const canvas = document.createElement("canvas");
      canvas.width = image.naturalWidth;
      canvas.height = image.naturalHeight;
      const context = canvas.getContext("2d");
      context.drawImage(image, 0, 0);
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
      let ink = 0;
      for (let index = 3; index < pixels.length; index += 4) if (pixels[index] > 0) ink += 1;
      const option = [...document.querySelectorAll('[role="option"] [role="img"]')].find((node) => node.getAttribute("aria-label") === font.label);
      results.push({ label: font.label, width: canvas.width, height: canvas.height, ink, mask: option.style.maskImage === `url("${url}")` });
    }
    return results;
  });
  for (const preview of previews) {
    assert(preview.width > 16 && preview.width <= 640, preview.label);
    assert.equal(preview.height, 64, preview.label);
    assert(preview.ink > 100 && preview.ink < 640 * 64, preview.label);
    assert(preview.mask, preview.label);
  }
  // 场景：搜索可同时匹配名称与 SDK 标识，选中后关闭，标题和字幕独立保留 IMS 保存值。
  await page.fill('input[placeholder="搜索字体"]', "Source Han Sans CN Normal");
  assert.equal(await page.evaluate(() => document.querySelectorAll('[role="option"]').length), 1);
  await page.press('input[placeholder="搜索字体"]', "Enter");
  await closed();
  assert.equal(await page.evaluate(() => document.querySelector('[aria-label="标题字体值"]').textContent), "Source Han Sans CN");
  assert.equal(await page.evaluate(() => document.querySelector("#title-font").getAttribute("aria-expanded")), "false");
  await page.click("#subtitle-font");
  assert.equal(await page.evaluate(() => document.querySelectorAll('[role="option"]').length), 11);
  await page.fill('input[placeholder="搜索字体"]', "思源宋体");
  await page.press('input[placeholder="搜索字体"]', "Enter");
  await closed();
  assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll("output")].slice(0, 2).map((node) => node.textContent)), ["Source Han Sans CN", "Source Han Serif CN"]);
  assert.equal(await page.evaluate(() => document.querySelector('#subtitle-font [role="img"]').getAttribute("aria-label")), "思源宋体");
  // 场景：字形预览随主题文字颜色变化；无匹配结果时给出提示。
  const light = await page.evaluate(() => getComputedStyle(document.querySelector('#subtitle-font [role="img"]')).backgroundColor);
  await page.evaluate(() => document.documentElement.classList.add("dark"));
  const dark = await page.evaluate(() => getComputedStyle(document.querySelector('#subtitle-font [role="img"]')).backgroundColor);
  assert.notEqual(light, dark);
  await page.click("#subtitle-font");
  await page.fill('input[placeholder="搜索字体"]', "没有这个字体");
  await page.waitForFunction(() => document.body.textContent.includes("没有匹配的字体"));
  await page.press('input[placeholder="搜索字体"]', "Escape");
  await closed();
  // 场景：Remotion 复用相同选项界面，继续读取实时目录并显示上传入口。
  await page.click("#remotion-font");
  await page.waitForFunction(() => document.querySelectorAll('[role="option"]').length > 0);
  await page.fill('input[placeholder="搜索字体"]', "Noto Sans CJK SC");
  await page.press('input[placeholder="搜索字体"]', "Enter");
  await closed();
  assert.equal(await page.evaluate(() => document.querySelector('[aria-label="Remotion 字体值"]').textContent), "Noto Sans CJK SC");
  await page.click("#remotion-font");
  assert.equal(await page.evaluate(() => document.querySelector('input[aria-label="选择字体文件"]').accept), ".ttf,.otf,font/ttf,font/otf");
  await page.press('input[placeholder="搜索字体"]', "Escape");
  await closed();
  console.log("PASS: 11 张真实字形预览、搜索、键盘选择、独立状态、主题颜色与 Remotion 目录。");
}
