/** 设置浏览器回归：隔离 HTTP，验证原生数字校验、取消草稿与窄屏操作栏；执行方式见设置模块 README。 */
import { chromium } from "playwright-core";
import assert from "node:assert/strict";

const browser = await chromium.launch({
  ...(process.env.IMV_CHROME_PATH ? { executablePath: process.env.IMV_CHROME_PATH } : { channel: "chrome" }),
  headless: true,
});
try {
  const page = await browser.newPage();
  // 仅有一个可选数字的新模块；不访问真实后端、凭据或模型。
  await page.route("**/api/templates/**", route => route.fulfill({ status: 503, json: {} }));
  await page.route("**/api/settings/plugins*", route => route.fulfill({ json: [{
    id: "native-number", name: "数字验证", schema: { type: "object", properties: {
      count: { type: "integer", title: "可选数量", default: 3 },
    } },
  }] }));
  await page.goto(process.env.IMV_BROWSER_URL || "http://localhost:1420");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "设置", exact: true });
  await dialog.getByRole("tab", { name: "数字验证" }).click();
  const input = dialog.getByLabel("可选数量");
  const save = dialog.getByRole("button", { name: "保存", exact: true });
  // 场景：逐键输入未完成的指数，浏览器暴露 value="" 和 badInput，必须阻止保存。
  await input.fill("");
  await input.pressSequentially("1e");
  assert.equal(await input.inputValue(), "");
  assert(await input.evaluate(element => element.validity.badInput));
  await save.click();
  await dialog.getByRole("alert").filter({ hasText: "请输入有效数字" }).waitFor();
  assert.equal(await dialog.getByRole("status").count(), 0);
  // 场景：修正成有效整数可保存；之后主动清空允许省略，重新打开恢复 Schema 默认值。
  await input.fill("5");
  await save.click();
  await dialog.getByRole("status").filter({ hasText: "已保存到当前页面" }).waitFor();
  await input.fill("");
  await save.click();
  await dialog.getByRole("status").filter({ hasText: "已保存到当前页面" }).waitFor();
  await input.fill("99");
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await dialog.getByRole("tab", { name: "数字验证" }).click();
  assert.equal(await input.inputValue(), "3");
  // 场景：窄屏操作栏保持可见，通用地址草稿取消后丢弃。
  await page.setViewportSize({ width: 390, height: 640 });
  await dialog.getByRole("tab", { name: "通用", exact: true }).click();
  const address = dialog.getByLabel("后端服务地址");
  const original = await address.inputValue();
  await address.fill("https://unsaved.test");
  const bounds = await save.boundingBox();
  assert(bounds && bounds.x >= 0 && bounds.y + bounds.height <= 640);
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("button", { name: "设置", exact: true }).click();
  assert.equal(await address.inputValue(), original);
  console.log("PASS: 原生数字 badInput 被拒绝，修正可保存，主动清空恢复默认值，取消丢弃草稿，窄屏操作栏可见。");
} finally {
  await browser.close();
}
