/** 真实 Chromium 验证可信点击、原生表单校验和 API 因果链；隔离 IPC/业务 HTTP，不安装更新；执行 bun tests/action-diagnostics.browser.mjs。 */
import assert from "node:assert/strict";
import { chromium } from "playwright-core";
import { createServer } from "vite";

const html = `<!doctype html><html><body><div id="root"></div><script type="module">
import React from 'react';
import {createRoot} from 'react-dom/client';
import {PluginSettings} from '/src/features/settings/PluginSettings.tsx';
import {initializeUpdates} from '/src/features/updates/store.ts';
import {listTemplates} from '/src/features/templates/api.ts';
import {setApiBase} from '/src/lib/api-base.ts';
import {openDiagnostics} from '/src/lib/diagnostics.ts';
import '/src/styles/globals.css';
setApiBase(location.origin);
await openDiagnostics();
await initializeUpdates();
createRoot(document.getElementById('root')).render(React.createElement(React.Fragment,null,
 React.createElement('button',{onClick:()=>listTemplates().catch(()=>{})},'刷新模板测试'),
 React.createElement(PluginSettings)
));
</script></body></html>`;

/** 模拟桌面持久化与更新结果，原参数可查；诊断消息不写入使用者的真实日志。 */
function desktop() {
  window.isTauri = true;
  window.diagnostics = [];
  window.failCheck = false;
  window.failSave = false;
  window.__TAURI_INTERNALS__ = {
    transformCallback: () => 0,
    invoke: async (command, args) => {
      if (command === "diagnostics_log") window.diagnostics.push(args);
      if (command === "diagnostics_open") return { path: "/test/diagnostics.log", viewerError: null };
      if (command === "local_settings") {
        if (args?.id && window.failSave) throw new Error("设置文件只读");
        return {};
      }
      if (command === "client_update") {
        if (args.operation === "status") return { enabled: true, notice: "" };
        if (args.operation === "check" && window.failCheck) throw new Error("镜像签名校验失败");
      }
      return null;
    },
  };
}

const requests = [];
const server = await createServer({ server: { host: "localhost", port: 0, open: false }, plugins: [{
  name: "action-diagnostics-test",
  configureServer(vite) {
    vite.middlewares.use(async (req, res, next) => {
      if (req.url === "/__actions") {
        res.setHeader("Content-Type", "text/html");
        res.end(await vite.transformIndexHtml("/__actions", html));
      } else if (req.url === "/template") {
        requests.push(req.headers["x-imv-request-id"]);
        res.setHeader("Content-Type", "application/json");
        res.setHeader("X-IMV-Diagnostics", "1");
        res.setHeader("X-IMV-Elapsed-Ms", "4.2");
        res.end('{"templates":[]}');
      } else if (req.url?.startsWith("/api/settings/plugins")) {
        res.setHeader("Content-Type", "application/json");
        res.end("[]");
      } else next();
    });
  },
}] });
let browser;
try {
  await server.listen();
  browser = await chromium.launch({ channel: "chrome", headless: true });
  const page = await browser.newPage();
  await page.addInitScript(desktop);
  await page.goto(`http://localhost:${server.httpServer.address().port}/__actions`);
  await page.getByRole("button", { name: "检查更新" }).waitFor();
  await page.waitForFunction(() => document.body.textContent.includes("当前已是可用的最新版本"));
  await page.evaluate(() => { window.diagnostics.length = 0; window.failCheck = true; });
  await page.getByRole("button", { name: "检查更新" }).click();
  await page.waitForFunction(() => window.diagnostics.some(entry => entry.message.includes("检查失败：Error: 镜像签名校验失败")));
  const update = await page.evaluate(() => window.diagnostics);
  const click = update.find(entry => entry.message === "点击 button「检查更新」");
  const id = /#([0-9a-f]{16})/.exec(click.source)[1];
  assert(update.some(entry => entry.message.includes("开始 检查更新") && entry.message.includes(`来自 #${id}`)));

  const address = page.getByLabel("后端服务地址");
  await address.fill("not-a-url");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await page.waitForFunction(() => window.diagnostics.some(entry => entry.source === "前端校验" && entry.message.includes("未提交")));
  await page.evaluate(() => { window.failSave = true; });
  await address.fill("http://localhost:20071");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await page.waitForFunction(() => window.diagnostics.some(entry => entry.message.includes("保存失败：Error: 设置文件只读")));

  await page.getByRole("button", { name: "刷新模板测试" }).click();
  await page.waitForFunction(() => window.diagnostics.some(entry => entry.message.includes("已收到成功响应")));
  await page.getByRole("button", { name: "刷新模板测试" }).click();
  await page.waitForFunction(() => window.diagnostics.filter(entry => entry.message.includes("已收到成功响应")).length >= 2);
  assert.equal(requests.length, 2);
  assert.equal(requests[0], undefined);
  assert.match(requests[1], /^[0-9a-f]{16}$/);
  const logs = await page.evaluate(() => window.diagnostics);
  const refreshClick = logs.find(entry => entry.message === "点击 button「刷新模板测试」");
  const refreshId = /#([0-9a-f]{16})/.exec(refreshClick.source)[1];
  assert(logs.some(entry => entry.source === "前端模板" && entry.message.includes(`来自 #${refreshId}`)));
  console.log("PASS: 真实点击关联更新与模板请求；表单拦截、IPC 失败原因、后端能力协商均已记录。");
} finally {
  await browser?.close();
  await server.close();
}
