/** 模板流程集成验证：连接独立 MySQL 测试服务，使用真实浏览器完成创建、更新、失败恢复和切换。 */
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { resolve } from "node:path";
import { chromium } from "playwright-core";

const api = process.env.IMV_TEST_API_URL || "http://127.0.0.1:20171";
const url = process.env.IMV_BROWSER_URL || "http://localhost:1427";
assert(["localhost", "127.0.0.1"].includes(new URL(api).hostname));
const identity = await fetch(`${api}/template`);
assert.match(identity.headers.get("x-imv-test-database") || "", /^imv_browser_test_[a-f0-9]{32}$/);
assert.equal(identity.status, 200);
assert.deepEqual(await identity.json(), [], "使用新启动的独立测试数据库");
const cache = resolve("node_modules/.cache/template-save-browser");
mkdirSync(cache, { recursive: true });
const temporaryDirectory = mkdtempSync(`${cache}/run-`);
process.env.TMPDIR = temporaryDirectory;
const browser = await chromium.launch({ channel: "chrome", headless: true });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  page.setDefaultTimeout(20_000);
  const errors = [];
  const posts = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === "/template") {
      assert.equal(new URL(request.url()).origin, new URL(api).origin);
      posts.push(request.postDataJSON());
    }
  });
  const cloud = page.getByRole("region", { name: "云端模板", exact: true });
  const info = page.getByRole("region", { name: "模板信息", exact: true });
  const editor = page.getByRole("region", { name: "特效设置", exact: true });
  const assets = page.getByRole("region", { name: "特效资产", exact: true });
  const protection = page.getByRole("dialog", { name: "保存当前修改？", exact: true });

  /** 从主页填写真实创建表单，提交只产生页面草稿。 */
  async function create(name, description = "集成验证描述") {
    await page.getByRole("tab", { name: "主页", exact: true }).click();
    await cloud.getByRole("button", { name: "新建模板", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "新建云端模板", exact: true });
    await dialog.getByLabel("模板名称", { exact: true }).fill(name);
    await dialog.getByLabel("模板描述", { exact: true }).fill(description);
    await dialog.getByRole("button", { name: "进入编辑", exact: true }).click();
  }

  /** 读取完整真实存储结果，所有断言均针对独立数据库。 */
  async function records() {
    const response = await fetch(`${api}/template`);
    assert.equal(response.status, 200);
    return response.json();
  }

  /** 等待真实保存响应与界面状态，返回持久化模板。 */
  async function save(label = "保存模板") {
    const response = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === "/template");
    await page.getByRole("button", { name: label, exact: true }).click();
    const result = await response;
    assert(result.ok(), await result.text());
    await page.getByText("已保存", { exact: true }).waitFor();
    return result.json();
  }

  /** 在主页打开已有模板，触发最新详情读取或未保存保护。 */
  async function select(name) {
    await page.getByRole("tab", { name: "主页", exact: true }).click();
    await cloud.getByRole("button", { name: `选择模板：${name}`, exact: true }).click();
  }

  // 场景：直接访问模板库不会自动建立模板，主页创建表单执行原生必填验证。
  const initialList = page.waitForResponse((response) => response.request().method() === "GET" && new URL(response.url()).pathname === "/template");
  await page.goto(url);
  const actualList = await initialList;
  assert.equal(new URL(actualList.url()).origin, new URL(api).origin);
  assert.equal(actualList.headers()["x-imv-test-database"], identity.headers.get("x-imv-test-database"));
  await cloud.getByText("暂无云端模板，点击「新建模板」开始创作。").waitFor();
  await page.getByRole("tab", { name: "模板库", exact: true }).click();
  await page.getByRole("button", { name: "前往主页" }).click();
  await cloud.getByRole("button", { name: "新建模板" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "进入编辑" }).click();
  assert.equal(await dialog.getByLabel("模板名称").evaluate((input) => input.validity.valueMissing), true);
  await dialog.getByLabel("模板名称").fill("   ");
  await dialog.getByRole("button", { name: "进入编辑" }).click();
  await dialog.getByText("请输入模板名称").waitFor();
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await create("  浏览器模板 A  ", "  已填写描述  ");
  await info.waitFor();
  assert.equal(await info.getByLabel("模板名称").textContent(), "浏览器模板 A");
  assert.equal(await info.getByLabel("模板描述").textContent(), "已填写描述");
  assert.equal(await info.getByLabel("当前环境").textContent(), "云端");
  assert.equal(await info.locator("input, textarea, select, [role=combobox]").count(), 0);
  assert.deepEqual(await info.getByRole("button").allTextContents(), ["保存模板"]);
  assert.deepEqual(await records(), []);
  await info.getByRole("button", { name: "保存模板" }).click();
  await page.getByText("请至少选择一个效果").waitFor();
  assert.equal(posts.length, 0);

  // 场景：连续点击保存只创建一次，后续保存携带原 ID 并更新效果参数。
  await assets.getByRole("button", { name: /^应用花字：/ }).first().click();
  await editor.getByLabel("示例文字").fill("首次保存标题");
  const firstResponse = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === "/template");
  await info.getByRole("button", { name: "保存模板" }).evaluate((button) => { button.click(); button.click(); });
  const first = await firstResponse;
  assert.equal(first.status(), 201);
  const a = await first.json();
  await page.getByText("已保存", { exact: true }).waitFor();
  assert.equal(posts.length, 1);
  assert.equal((await records()).length, 1);
  await editor.getByLabel("示例文字").fill("更新后的标题");
  const updated = await save();
  assert.equal(updated.template_id, a.template_id);
  assert.equal(posts.at(-1).template_id, a.template_id);
  assert.equal(updated.name, "浏览器模板 A");
  assert.equal(updated.description, "已填写描述");
  assert.equal((await records()).length, 1);

  // 场景：已有同名模板在主页创建时被拒绝，取消后可以继续使用当前模板。
  await create("浏览器模板 A");
  await page.getByRole("dialog").getByText("模板名称已存在，请使用其他名称").waitFor();
  await page.getByRole("dialog").getByRole("button", { name: "取消", exact: true }).click();
  await create("浏览器模板 B", "");
  await info.getByText("浏览器模板 B", { exact: true }).waitFor();
  await assets.getByRole("button", { name: /^应用花字：/ }).first().click();
  const b = await save();
  await select(a.name);
  await info.getByText(a.name, { exact: true }).waitFor();
  assert.equal(await editor.getByLabel("示例文字").inputValue(), "更新后的标题");

  // 场景：取消、保存并切换、放弃修改均处理真实已有模板和最新详情。
  await editor.getByLabel("示例文字").fill("切换保存标题");
  await select(a.name);
  await info.waitFor();
  assert.equal(await protection.count(), 0);
  assert.equal(await editor.getByLabel("示例文字").inputValue(), "切换保存标题");
  await select(b.name);
  await protection.getByRole("button", { name: "取消", exact: true }).click();
  assert.equal(await editor.getByLabel("示例文字").inputValue(), "切换保存标题");
  await select(b.name);
  await protection.getByRole("button", { name: "保存并切换" }).click();
  await info.getByText(b.name, { exact: true }).waitFor();
  await select(a.name);
  await info.getByText(a.name, { exact: true }).waitFor();
  assert.equal(await editor.getByLabel("示例文字").inputValue(), "切换保存标题");
  await editor.getByLabel("示例文字").fill("放弃这份修改");
  await select(b.name);
  await protection.getByRole("button", { name: "放弃修改" }).click();
  await info.getByText(b.name, { exact: true }).waitFor();
  await select(a.name);
  await info.getByText(a.name, { exact: true }).waitFor();
  assert.equal(await editor.getByLabel("示例文字").inputValue(), "切换保存标题");

  // 场景：真实离线保存失败保留草稿，恢复网络后由用户明确重试成功。
  await editor.getByLabel("示例文字").fill("离线后重试标题");
  await context.setOffline(true);
  await info.getByRole("button", { name: "保存模板" }).click();
  await page.getByRole("alert").filter({ hasText: "无法连接服务端" }).waitFor();
  assert.equal(await editor.getByLabel("示例文字").inputValue(), "离线后重试标题");
  await context.setOffline(false);
  assert.equal((await save()).template_id, a.template_id);

  // 场景：保存并切换发生真实网络失败，弹窗与待切换目标保留，显式重试后完成切换。
  await editor.getByLabel("示例文字").fill("切换失败后重试标题");
  await select(b.name);
  await protection.waitFor();
  await context.setOffline(true);
  await protection.getByRole("button", { name: "保存并切换" }).click();
  await protection.getByRole("alert").filter({ hasText: "无法连接服务端" }).waitFor();
  assert.equal(await page.getByLabel("模板名称", { exact: true }).textContent(), a.name);
  await context.setOffline(false);
  await protection.getByRole("button", { name: "保存并切换" }).click();
  await info.getByText(b.name, { exact: true }).waitFor();
  await select(a.name);
  await info.getByText(a.name, { exact: true }).waitFor();
  assert.equal(await editor.getByLabel("示例文字").inputValue(), "切换失败后重试标题");

  // 场景：主页创建的新模板在保存并切换时创建独立记录，已有模板仍使用自己的 ID。
  await create("浏览器模板 C");
  await info.getByText("浏览器模板 C", { exact: true }).waitFor();
  await assets.getByRole("button", { name: /^应用花字：/ }).first().click();
  await editor.getByLabel("示例文字").fill("新建并切换标题");
  await select(a.name);
  await protection.getByRole("button", { name: "保存并切换" }).click();
  await info.getByText(a.name, { exact: true }).waitFor();
  const created = (await records()).find((template) => template.name === "浏览器模板 C");
  assert(created);
  assert.notEqual(created.template_id, a.template_id);
  assert.equal(created.tracks.find((track) => track.target === "title").editor.title, "新建并切换标题");
  assert(!Object.hasOwn(created, "editor"));
  assert.equal((await fetch(`${api}/template/${created.template_id}`, { method: "DELETE" })).status, 204);

  // 场景：主页列表展示后，目标被删除，读取失败及重试均保留当前模板与草稿。
  await page.getByRole("tab", { name: "主页", exact: true }).click();
  await cloud.getByRole("button", { name: `选择模板：${b.name}`, exact: true }).waitFor();
  const deletion = await fetch(`${api}/template/${b.template_id}`, { method: "DELETE" });
  assert.equal(deletion.status, 204);
  await cloud.getByRole("button", { name: `选择模板：${b.name}`, exact: true }).click();
  await page.getByRole("button", { name: "重试打开模板" }).waitFor();
  assert.equal(await info.getByLabel("模板名称").textContent(), a.name);
  await page.getByRole("button", { name: "重试打开模板" }).click();
  await page.getByRole("alert").filter({ hasText: "模板不存在" }).waitFor();
  await select(a.name);
  await info.waitFor();
  assert.equal(await page.getByRole("button", { name: "重试打开模板" }).count(), 0);

  // 场景：主页读取失败仍允许创建，恢复网络后重试展示实际列表。
  await context.setOffline(true);
  await page.getByRole("tab", { name: "主页", exact: true }).click();
  await cloud.getByRole("alert").waitFor();
  assert.equal(await cloud.getByRole("button", { name: "新建模板" }).isEnabled(), true);
  await context.setOffline(false);
  await cloud.getByRole("button", { name: "重试", exact: true }).click();
  await cloud.getByRole("button", { name: `选择模板：${a.name}`, exact: true }).waitFor();

  // 场景：新建输入最大长度在只读信息区域换行，各尺寸下页面没有横向溢出。
  await create("标题".repeat(50), "描述".repeat(500));
  await info.getByText("标题".repeat(50), { exact: true }).waitFor();
  for (const width of [390, 768, 1280, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${width}px 页面横向超出`);
  }
  assert.equal((await records()).length, 1);
  assert.deepEqual(errors, []);
  console.log("PASS：主页创建、只读信息、真实创建与更新、防重复保存、三种切换选择、离线恢复、详情失败重试、最大长度与响应式布局。");
} finally {
  await browser.close();
  rmSync(temporaryDirectory, { recursive: true, force: true });
}
