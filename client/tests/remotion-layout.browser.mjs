/**
 * 浏览器布局回归：模拟 API（创建、SSE 事件与送达确认）和 iframe 协议，验证真实 CSS、指针/键盘分栏、持久化与窄屏交互；
 * 会话历史只在页面内存的本地库里，由 SSE 事件写入。执行 bun run test:remotion-browser。
 */
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
  // 主页读取也使用离线夹具，不连接用户真实后端。
  await page.route("**/template", (route) => route.fulfill({ status: 503 }));
  const errors = [];
  let deleted = false;
  let creates = 0;
  let diagnosticRequests = 0;
  page.on("pageerror", (e) => errors.push(e.message));
  const loopRounds = [
    { layer: "outer", turn: 1, calls: [], error_code: "MODEL_CONTRACT_FAILED" },
    { layer: "executor", turn: 2, calls: [{ tool: "sprite.compose", status: "fail", error_code: "COMPOSITION_FAILED", message: "组合生成失败。", detail: "Sprite bundler unavailable", arguments: '{"draft_id":"d1"}' }] },
  ];
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
  /** 一条用户或助手消息事件的载荷。 */
  const message = (n, job, role, text) => ({ id: `m${n}`, sequence: n, job_id: job.id, role, text, created_at: job.updated_at });
  // 会话历史只在客户端本地：浏览器版本地库在页面内存里，由创建作品后 SSE 送达的事件写入（两轮成功任务、四条消息与循环记录）。
  const history = [
    ["message.created", message(1, jobs[0], "user", "做一个简洁的居中标题，淡入出现。")],
    ["job.updated", jobs[0]],
    ...loopRounds.map((round) => ["job.round", { job_id: "job-1", round }]),
    ["version.ready", { version_id: "version-1", job_id: "job-1" }],
    ["message.created", message(2, jobs[0], "assistant", "标题已完成。可以预览效果，或者继续调整。")],
    ["message.created", message(3, jobs[1], "user", "改成「让灵感发光」，暖金色，加一条下划线。")],
    ["job.updated", jobs[1]],
    ...loopRounds.map((round) => ["job.round", { job_id: "job-2", round }]),
    ["version.ready", { version_id: "version-2", job_id: "job-2" }],
    ["message.created", message(4, jobs[1], "assistant", "已更新暖金色标题和下划线，保留淡入动画。")],
  ].map(([type, data], index) => ({ id: index + 1, work_id: "work-1", created_at: "2026-09-17T06:01:00Z", type, data }));
  await page.route("**/api/templates/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace("/api/templates", "");
    /** 返回确定的公开数据，仅允许本测试明确执行的创建、送达确认与会话删除，不连接真实服务。 */
    const json = (body) => route.fulfill({ json: body });
    if (req.method() === "DELETE" && path === "/works/work-1") {
      deleted = true;
      return route.fulfill({ status: 204 });
    }
    if (req.method() === "POST" && path === "/works") {
      creates++;
      deleted = false;
      return json({
        work: { id: "work-1", current_version_id: null, created_at: "2026-09-17T06:00:00Z", request: { description: "温暖的电影标题" } },
        job: { ...jobs[0], status: "running", result_version_id: null },
      });
    }
    if (req.method() === "POST" && path === "/works/work-1/ack")
      return route.fulfill({ status: 204 });
    assert.equal(req.method(), "GET", `${req.method()} ${path}`);
    if (path === "/capabilities") return json({ models_configured: true });
    if (path.endsWith("/events")) return json([]);
    if (path.endsWith("/stream")) {
      const after = Number(url.searchParams.get("after"));
      return route.fulfill({
        contentType: "text/event-stream",
        body: ": connected\n\n" + history
          .filter((event) => event.id > after)
          .map((event) => `id: ${event.id}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`)
          .join(""),
      });
    }
    if (path.endsWith("/versions")) return json(versions);
    if (/\/versions\/[^/]+$/.test(path))
      return json(versions.find((v) => path.endsWith(v.id)));
    if (path.endsWith("/diagnostics")) {
      diagnosticRequests++;
      return json({ passed: false, diagnostics: [
        { source: "lsp", severity: "error", file: "Export.tsx", code: "2322", message: "导出类型不匹配", range: { start: { line: 0, character: 0 }, end: { line: 0, character: 1 } } },
        { source: "lsp", severity: "error", file: "contract.tsx", code: "2322", message: "调用点类型不匹配", range: { start: { line: 0, character: 0 }, end: { line: 0, character: 1 } } },
      ] });
    }
    if (path.endsWith("Export.tsx"))
      return route.fulfill({
        contentType: "text/plain",
        body: `export default function Template() { return '${path.includes("version-1") ? "今日灵感" : "让灵感发光"}'; }`,
      });
    if (path.endsWith("/preview"))
      return route.fulfill({ contentType: "text/html", body: preview });
    return route.fulfill({ status: 404 });
  });
  /** 浏览器版会话只在页面内存：从空白聊天发送首条消息创建作品，等 SSE 把历史写入本地并恢复到最新版本。 */
  async function createWork() {
    await page.getByLabel("字效描述").fill("做一个简洁的居中标题，淡入出现。");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await page.getByRole("button", { name: /温暖的电影标题/ }).first().waitFor();
    await page.getByRole("button", { name: "预览 V2", exact: true }).waitFor();
  }
  await page.goto(url);
  await page.getByRole("button", { name: "AI 字效", exact: true }).click();
  await createWork();
  await page
    .getByText("正在渲染预览…", { exact: true })
    .waitFor({ state: "hidden" });
  await page.getByLabel("字号", { exact: true }).waitFor();
  // 在真实浏览器验证诊断定位和折叠缓存；不执行生成的 TSX。
  const codeToggle = page.getByRole("button", { name: "展开 V2 代码", exact: true });
  await codeToggle.click();
  await page.getByLabel("第 1 行：error", { exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: /contract.tsx.*调用点类型不匹配/ }).count(), 0);
  await page.getByRole("button", { name: /Export.tsx.*导出类型不匹配/ }).click();
  assert.equal(await page.locator('[data-line="1"]:focus').count(), 1);
  await codeToggle.click();
  await codeToggle.click();
  assert.equal(diagnosticRequests, 1);
  await page.screenshot({ path: join(screenshots, "imv-audit-diagnostics.png"), fullPage: true });
  await codeToggle.click();
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
  // 在真实浏览器验证失败轮次文案，不执行生成的 TSX。
  const rounds = page.getByRole("region", { name: "循环记录", exact: true }).last();
  await rounds.getByRole("button", { name: /循环记录 · 2 轮/ }).click();
  await rounds.getByRole("button", { name: /任务循环 第 1 轮/ }).click();
  await rounds.getByText("模型响应格式无效，本轮未调用工具。").waitFor();
  await rounds.getByRole("button", { name: /执行循环 第 2 轮/ }).click();
  // 调用行显示工具名、错误码与参数摘要；点开后原始 tool call 带错误码与工具的真实错误。
  const compose = rounds.getByRole("button", { name: /sprite\.compose ✗ COMPOSITION_FAILED/ });
  await compose.waitFor();
  assert((await compose.textContent()).includes("draft_id=d1"));
  await compose.click();
  await rounds.getByText(/COMPOSITION_FAILED：组合生成失败。：Sprite bundler unavailable/).waitFor();
  await page.screenshot({ path: join(screenshots, "imv-audit-rounds.png"), fullPage: true });
  await rounds.getByRole("button", { name: /循环记录 · 2 轮/ }).click();
  // 桌面删除先确认，取消不影响当前播放器或历史选择。
  await page.getByRole("button", { name: "删除聊天", exact: true }).click();
  await page.getByRole("dialog").waitFor();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  assert.equal(deleted, false);
  const frame = page.locator('iframe[title="Remotion 字效播放器"]');
  const rect = await frame.boundingBox();
  // iframe 与外框保持成功版本的真实画布比例；外框完整落在预览区内。
  assert(Math.abs(rect.width / rect.height - versions[1].spec.composition.width / versions[1].spec.composition.height) < 0.01);
  const frameParent = await frame.locator("..").boundingBox();
  assert(Math.abs(rect.width - frameParent.width) <= 2);
  assert(Math.abs(rect.height - frameParent.height) <= 2);
  const handle = page.getByRole("separator", { name: "调整聊天与预览宽度" });

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
    localStorage.getItem("imv.remotion.studio-layout"),
  );
  assert(pref);
  await page.reload();
  await page.getByRole("button", { name: "AI 字效", exact: true }).click();
  // 刷新清空了浏览器内存会话库：记住的会话静默清除，不提示删除；重新创建后继续验证。
  await page.getByText("还没有聊天会话").first().waitFor();
  assert.equal(await page.getByText(/会话已删除或正在清理/).count(), 0);
  await createWork();
  assert.equal(creates, 2);
  assert.equal(
    await page.evaluate(() => localStorage.getItem("imv.remotion.studio-layout")),
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
  // 「正在渲染预览…」在重新加载开始前可能尚未出现，等它隐藏不代表渲染完成；以 V2 的参数真正下发到新 iframe 为准。
  // 审计复现：iframe 就绪早于状态清空时参数不再下发，这里会一直等不到 V2 文字，而不是靠 20 秒超时解锁蒙混过去。
  await page
    .frameLocator('iframe[title="Remotion 字效播放器"]')
    .locator("#text", { hasText: "让灵感发光" })
    .waitFor({ timeout: 10_000 });
  await page
    .getByLabel("字号", { exact: true })
    .and(page.locator(":enabled"))
    .waitFor({ timeout: 10_000 });
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
  await page.getByRole("button", { name: "历史会话", exact: true }).click();
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
  await page.getByRole("button", { name: "历史会话", exact: true }).click();
  await page.getByRole("dialog").getByText("还没有聊天会话").waitFor();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "新建会话" })
    .click();
  // 抽屉带关闭动画，等待卸载完成后再确认没有残留弹窗。
  await page.getByRole("dialog").waitFor({ state: "detached" });
  assert.equal(await page.getByRole("dialog").count(), 0);
  await page.getByRole("tab", { name: "聊天", exact: true }).click();
  await page.getByRole("heading", { name: "把想法，变成画面" }).waitFor();
  // 新会话的时长选择与自定义输入加宽了工具栏：窄屏与三栏桌面下发送按钮都须留在输入框内，不被裁掉。
  await page.getByRole("combobox", { name: "预览时长" }).click();
  await page.getByRole("option", { name: "自定义", exact: true }).click();
  // Chrome 把「-」这类无法解析的数字输入报告为空值；必须显示错误，而不是只把发送按钮变灰。
  await page.getByLabel("自定义预览时长（秒）").press("-");
  assert.equal(await page.getByLabel("自定义预览时长（秒）").inputValue(), "");
  await page.getByRole("alert").filter({ hasText: "请输入不超过 30 秒、至少 1 帧的时长" }).waitFor();
  await page.getByLabel("自定义预览时长（秒）").fill("99");
  for (const width of [390, 375, 1024]) {
    await page.setViewportSize({ width, height: 844 });
    const box = await page.evaluate(() => {
      const send = [...document.querySelectorAll("button")].find((b) => b.textContent.includes("发送"));
      const form = send.closest("form").getBoundingClientRect();
      const button = send.getBoundingClientRect();
      return { inside: button.left >= form.left && button.right <= form.right };
    });
    assert(box.inside, `发送按钮在 ${width}px 下溢出输入框`);
  }
  await page.screenshot({
    path: join(screenshots, "imv-remotion-duration-1024.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  // 损坏的本地偏好不能阻止打开工作区，也不能生成 NaN 或挤掉面板。
  await page.evaluate(() =>
    localStorage.setItem("imv.remotion.studio-layout", '{"history":"bad"}'),
  );
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.reload();
  await page.getByRole("button", { name: "AI 字效", exact: true }).click();
  await page.getByRole("button", { name: "保存资产", exact: true }).waitFor();
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
    "PASS: 预览尺寸、拖拽/键盘调宽、栏宽恢复、历史预览、未保存保护、新增聊天、桌面删除确认与窄屏删除清理、新会话时长工具栏不溢出及 390px 窄屏布局。截图：" +
      screenshots,
  );
} finally {
  await browser.close();
}
