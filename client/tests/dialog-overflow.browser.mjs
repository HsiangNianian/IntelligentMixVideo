/** 弹窗溢出浏览器回归：切换保护弹窗显示不含空格的超长错误路径时，文字与按钮不得超出弹窗；执行 bun tests/dialog-overflow.browser.mjs。 */
import assert from "node:assert/strict";
import { chromium } from "playwright-core";
import { protobufListResponse, savedTemplate } from "./fixtures.ts";

const url = process.env.IMV_BROWSER_URL || "http://127.0.0.1:1420";
const longPath = `本地模板字段已变化，原有模板无法读取。请删除旧模板文件后重试（将清除全部本地模板）：/home/muyuzhong/.local/share/com.intelligentmixvideo.client/data/template/templates.json`;
const browser = await chromium.launch({
  ...(process.env.IMV_CHROME_PATH ? { executablePath: process.env.IMV_CHROME_PATH } : { channel: "chrome" }),
  headless: true,
});
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const template = savedTemplate();
  // 列表返回一个模板，读取详情返回超长错误；只模拟模板接口，不连接真实服务。
  await page.route((target) => target.pathname.startsWith("/template"), async (route) => {
    if (new URL(route.request().url()).pathname === "/template") {
      const response = protobufListResponse([template]);
      return route.fulfill({ contentType: "application/x-protobuf", body: Buffer.from(await response.arrayBuffer()) });
    }
    return route.fulfill({ status: 404, json: { detail: longPath } });
  });
  await page.goto(url);
  // 新建草稿后回到主页选择已有模板，放弃修改会读取详情并在保护弹窗内显示错误。
  await page.getByRole("button", { name: "新建云端模板" }).click();
  await page.getByLabel("模板名称").fill("溢出测试");
  await page.getByRole("button", { name: "进入编辑" }).click();
  await page.getByRole("region", { name: "模板信息" }).waitFor();
  await page.getByRole("tab", { name: "主页" }).click();
  await page.getByRole("button", { name: `选择模板：${template.name}` }).click();
  const dialog = page.getByRole("dialog", { name: "保存当前修改？" });
  await dialog.getByRole("button", { name: "放弃修改" }).click();
  const alert = dialog.getByRole("alert");
  await alert.waitFor();
  const box = await dialog.boundingBox();
  // 弹窗本身不能被撑宽，错误文字与每个按钮都必须完整落在弹窗内。
  assert.equal(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth), true);
  for (const element of [alert, ...await dialog.getByRole("button").all()]) {
    const inner = await element.boundingBox();
    assert(inner.x >= box.x - 1 && inner.x + inner.width <= box.x + box.width + 1, "内容超出弹窗右侧");
  }
  console.log("PASS: 超长错误路径在切换保护弹窗内换行，按钮保持在弹窗内。");
} finally {
  await browser.close();
}
