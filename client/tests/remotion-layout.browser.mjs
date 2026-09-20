/** 浏览器布局回归：模拟 API 和 iframe 协议，验证真实 CSS、指针/键盘分栏、持久化与窄屏交互；执行 bun run test:remotion-browser。 */
import { chromium } from "playwright-core";
import { remotionVersion } from "./remotion-fixtures.ts";
import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const screenshots = mkdtempSync(join(tmpdir(), "imv-remotion-browser-"));
const url = process.env.IMV_BROWSER_URL || "http://127.0.0.1:1421";
const versions = [
  remotionVersion("version-1"),
  remotionVersion("version-2", {
    text: "让灵感发光",
    size: 96,
    color: "#F2CF78",
  }),
];
versions[1].spec.name = "让灵感发光";
// 此夹具仅测试父页面与 iframe 协议；不宣称模拟内容验证了 Remotion 播放或媒体解码。
const preview = `<html><style>html,body{margin:0;height:100%;background:#182238;color:#f2cf78;font-family:sans-serif}body{display:grid;place-items:center}p{font-size:20px}</style><p id="text"></p><script>
const channel=location.hash.slice(1);addEventListener('message',event=>{const data=event.data;if(event.source!==parent||data.channel!==channel||data.type!=='imv-preview-update')return;document.getElementById('text').textContent=data.values.text;parent.postMessage({type:'imv-preview-rendered',channel,requestId:data.requestId},'*')});parent.postMessage({type:'imv-preview-ready',channel},'*');
</script></html>`;
const browser = await chromium.launch({
  ...(process.env.IMV_CHROME_PATH
    ? { executablePath: process.env.IMV_CHROME_PATH }
    : { channel: "chrome" }),
  headless: true,
});
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  const errors = [];
  let deleted = false;
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("**/api/templates/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace("/api/templates", "");
    /** 返回确定的公开数据，仅允许本测试明确执行的会话删除，不连接真实服务。 */
    const json = (body) => route.fulfill({ json: body });
    if (req.method() === "DELETE" && path === "/works/work-1") {
      deleted = true;
      return route.fulfill({ status: 204 });
    }
    assert.equal(req.method(), "GET");
    const jobs = versions.map((v, i) => ({
      id: `job-${i + 1}`,
      project_id: "work-1",
      status: "succeeded",
      result_version_id: v.id,
      questions: [],
      message: null,
      parameters: null,
      created_at: "2026-09-17T06:00:00Z",
      updated_at: "2026-09-17T06:01:00Z",
    }));
    if (path === "/capabilities") return json({ models_configured: true });
    if (path === "/works")
      return json({
        items: deleted
          ? []
          : [
              {
                id: "work-1",
                title: "温暖的电影标题",
                updated_at: "2026-09-17T06:01:00Z",
                current_version_id: "version-2",
                job: jobs[1],
              },
            ],
        next_cursor: null,
      });
    if (path.endsWith("/session"))
      return json({
        work: { id: "work-1", current_version_id: "version-2" },
        job: jobs[1],
        jobs,
        messages: [
          {
            id: "m1",
            sequence: 1,
            job_id: "job-1",
            role: "user",
            text: "做一个简洁的居中标题，淡入出现。",
            created_at: jobs[0].created_at,
          },
          {
            id: "m2",
            sequence: 2,
            job_id: "job-1",
            role: "assistant",
            text: "标题已完成。可以预览效果，或者继续调整。",
            created_at: jobs[0].updated_at,
          },
          {
            id: "m3",
            sequence: 3,
            job_id: "job-2",
            role: "user",
            text: "改成「让灵感发光」，暖金色，加一条下划线。",
            created_at: jobs[1].created_at,
          },
          {
            id: "m4",
            sequence: 4,
            job_id: "job-2",
            role: "assistant",
            text: "已更新暖金色标题和下划线，保留淡入动画。",
            created_at: jobs[1].updated_at,
          },
        ],
        cursor: 0,
        next_before: null,
      });
    if (path.endsWith("/stream"))
      return route.fulfill({
        contentType: "text/event-stream",
        body: ": connected\n\n",
      });
    if (path.endsWith("/versions")) return json(versions);
    if (/\/versions\/[^/]+$/.test(path))
      return json(versions.find((v) => path.endsWith(v.id)));
    if (path.endsWith("Export.tsx"))
      return route.fulfill({
        contentType: "text/plain",
        body: `export default function Template() { return '${path.includes("version-1") ? "今日灵感" : "让灵感发光"}'; }`,
      });
    if (path.endsWith("/preview"))
      return route.fulfill({ contentType: "text/html", body: preview });
    return route.fulfill({ status: 404 });
  });
  await page.goto(url);
  await page.getByRole("button", { name: /温暖的电影标题/ }).click();
  await page.getByRole("button", { name: "预览 V2", exact: true }).waitFor();
  await page
    .getByText("正在渲染预览…", { exact: true })
    .waitFor({ state: "hidden" });
  await page.getByLabel("字号", { exact: true }).waitFor();
  // 历史消息、任务栏与版本卡片异步恢复后，底部代码操作仍须可见。
  await page.waitForFunction(() => {
    const log = document.querySelector('[role="log"]');
    return log.scrollHeight - log.scrollTop - log.clientHeight <= 24;
  });
  assert(
    await page.evaluate(
      () => document.documentElement.scrollHeight <= innerHeight + 4,
    ),
  );
  await page.screenshot({
    path: join(screenshots, "imv-remotion-desktop.png"),
    fullPage: true,
  });
  // 桌面删除先确认，取消不影响当前播放器或历史选择。
  await page.getByRole("button", { name: "删除聊天", exact: true }).click();
  await page.getByRole("dialog").waitFor();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  assert.equal(deleted, false);
  const frame = page.locator('iframe[title="Remotion 字效播放器"]');
  const rect = await frame.boundingBox();
  // Player 自身保持画布比例；iframe 占满预览区域，避免竖屏画布挤压播放控件。
  const frameParent = await frame.locator("..").boundingBox();
  assert(Math.abs(rect.width - frameParent.width) <= 2);
  assert(Math.abs(rect.height - frameParent.height) <= 2);
  const handle = page.getByRole("separator", { name: "调整历史与聊天宽度" });

  const before = await page
    .locator('[data-slot="resizable-panel"]')
    .first()
    .boundingBox();
  const box = await handle.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + 60, box.y + box.height / 2, { steps: 8 });
  await page.mouse.up();
  const after = await page
    .locator('[data-slot="resizable-panel"]')
    .first()
    .boundingBox();
  assert(after.width > before.width + 30);
  await handle.focus();
  await page.keyboard.press("ArrowLeft");
  // 键盘调整后的尺寸必须真正恢复，而不只断言 localStorage 未丢失。
  const resizedWidth = (
    await page.locator('[data-slot="resizable-panel"]').first().boundingBox()
  ).width;
  assert(resizedWidth < after.width - 1);
  const pref = await page.evaluate(() =>
    localStorage.getItem("imv.remotion.layout"),
  );
  assert(pref);
  await page.reload();
  await page.getByRole("button", { name: "预览 V2", exact: true }).waitFor();
  assert.equal(
    await page.evaluate(() => localStorage.getItem("imv.remotion.layout")),
    pref,
  );
  assert(
    Math.abs(
      (
        await page
          .locator('[data-slot="resizable-panel"]')
          .first()
          .boundingBox()
      ).width - resizedWidth,
    ) < 2,
  );
  await page.getByRole("button", { name: "恢复布局" }).click();
  assert(
    Math.abs(
      (
        await page
          .locator('[data-slot="resizable-panel"]')
          .first()
          .boundingBox()
      ).width - before.width,
    ) < 2,
  );
  // 第二条分隔线调整预览宽度；极端拖动也必须保留三个面板的最小可用宽度。
  const second = await page
    .getByRole("separator", { name: "调整聊天与预览宽度" })
    .boundingBox();
  const previewBefore = await page
    .locator('[data-slot="resizable-panel"]')
    .last()
    .boundingBox();
  await page.mouse.move(second.x + second.width / 2, second.y + 100);
  await page.mouse.down();
  await page.mouse.move(second.x - 60, second.y + 100, { steps: 8 });
  await page.mouse.up();
  assert(
    (await page.locator('[data-slot="resizable-panel"]').last().boundingBox())
      .width >
      previewBefore.width + 30,
  );
  await page.mouse.move(second.x - 60, second.y + 100);
  await page.mouse.down();
  await page.mouse.move(1400, second.y + 100, { steps: 8 });
  await page.mouse.up();
  assert(
    (await page.locator('[data-slot="resizable-panel"]').last().boundingBox())
      .width >= 359,
  );
  await page.getByRole("button", { name: "恢复布局" }).click();
  // 点击成功卡片只替换预览；返回最新后恢复参数编辑。
  await page.getByRole("button", { name: "预览 V1", exact: true }).click();
  await page
    .getByText("正在渲染预览…", { exact: true })
    .waitFor({ state: "hidden" });
  assert(await page.getByLabel("字号", { exact: true }).isDisabled());
  await page.getByRole("button", { name: "返回最新版本" }).click();
  await page
    .getByText("正在渲染预览…", { exact: true })
    .waitFor({ state: "hidden" });
  assert(await page.getByLabel("字号", { exact: true }).isEnabled());
  await page.getByLabel("字号", { exact: true }).fill("104");
  await page.getByRole("button", { name: "预览 V1", exact: true }).click();
  await page.getByRole("dialog").waitFor();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  assert.equal(
    await page.getByLabel("字号", { exact: true }).inputValue(),
    "104",
  );
  await page.getByRole("button", { name: "撤销修改" }).click();
  await page.getByRole("button", { name: "展开 V2 代码" }).click();
  await page.screenshot({
    path: join(screenshots, "imv-remotion-code.png"),
    fullPage: true,
  });
  // 常见桌面尺寸与手机尺寸均不能撑出横向滚动；窄屏切换保留同一播放器。
  await page.setViewportSize({ width: 1280, height: 800 });
  assert(
    await page.evaluate(
      () => document.documentElement.scrollHeight <= innerHeight + 4,
    ),
  );
  await page.screenshot({
    path: join(screenshots, "imv-remotion-1280.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: join(screenshots, "imv-remotion-mobile-chat.png"),
    fullPage: true,
  });
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  );
  await page.getByRole("tab", { name: "预览与参数", exact: true }).click();
  await page.screenshot({
    path: join(screenshots, "imv-remotion-mobile-preview.png"),
    fullPage: true,
  });
  assert(await frame.isVisible());
  // 窄屏删除按钮关闭抽屉并打开确认框，删除当前会话后播放器和选择同步释放。
  await page.getByRole("button", { name: "聊天历史", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "删除聊天", exact: true })
    .click();
  await page.getByRole("button", { name: "确认删除", exact: true }).click();
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  assert.equal(deleted, true);
  assert.equal(await frame.count(), 0);
  assert.equal(
    await page.evaluate(
      () =>
        Object.keys(localStorage).filter((key) =>
          key.startsWith("imv.remotion.selected:"),
        ).length,
    ),
    0,
  );
  await page.getByRole("button", { name: "聊天历史", exact: true }).click();
  await page.getByRole("dialog").getByText("还没有聊天会话").waitFor();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "新增聊天" })
    .click();
  assert.equal(await page.getByRole("dialog").count(), 0);
  await page.getByRole("tab", { name: "聊天", exact: true }).click();
  await page.getByText("把想法变成字效").waitFor();
  // 损坏的本地偏好不能阻止打开工作区，也不能生成 NaN 或挤掉面板。
  await page.evaluate(() =>
    localStorage.setItem("imv.remotion.layout", '{"history":"bad"}'),
  );
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.reload();
  await page.getByRole("button", { name: "新增聊天" }).waitFor();
  assert(
    Math.abs(
      (
        await page
          .locator('[data-slot="resizable-panel"]')
          .first()
          .boundingBox()
      ).width - before.width,
    ) < 2,
  );
  assert.equal(errors.length, 0, errors.join("\n"));
  console.log(
    "PASS: 预览尺寸、拖拽/键盘调宽、栏宽恢复、历史预览、未保存保护、新增聊天、桌面删除确认与窄屏删除清理及 390px 窄屏布局。截图：" +
      screenshots,
  );
} finally {
  await browser.close();
}
