/**
 * 模板预览播放器调试日志的真实浏览器回归：真实 SDK 与解码，原生日志由页面里的假 Tauri IPC 接收；
 * 元数据可读但播放器加载不了的视频要在预览里提示并带地址与错误码写进日志，正常播放记录播放状态与流畅度汇总，
 * 坏视频在 canplay 超时后也不能显示为就绪，渲染授权请求失败时照常用 IMS 播放并记警告，SDK 加载失败时用基础播放器播放视频，
 * 显示就绪后立即播放不会在 0 秒结束；
 * 执行 bun tests/preview-diagnostics.browser.mjs（需要 Chrome、FFmpeg 和联网访问 SDK、字体）。
 */
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { chromium } from "playwright-core";
import { createServer } from "vite";

const cache = resolve("node_modules/.cache/preview-diagnostics-tests");
mkdirSync(cache, { recursive: true });
// 可解码的短视频，以及只有零字节内容、播放器无法解析的“视频”。
const result = spawnSync("ffmpeg", ["-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=10", "-t", "3", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", resolve(cache, "ok.mp4")], { encoding: "utf8" });
assert.equal(result.status, 0, result.error?.message || result.stderr);
writeFileSync(resolve(cache, "broken.mp4"), Buffer.alloc(4096));

// 页面直接挂载生产组件；跳过工作区的元数据读取，模拟“元数据正常、播放器加载失败”（编码或跨域）。
const html = `<!doctype html><html><body><div id="root"></div><script type="module">
import React from 'react';
import {createRoot} from 'react-dom/client';
import {TemplatePreview} from '/src/features/templates/TemplatePreview.tsx';
import {newDraft} from '/src/features/templates/model.ts';
import {previewContext,resolveTemplate} from '/src/features/templates/tracks.ts';
import {openDiagnostics} from '/src/lib/diagnostics.ts';
import '/src/styles/globals.css';
await openDiagnostics();
const root=createRoot(document.getElementById('root'));
const draft=newDraft();
window.mountPreview=(name,key=name)=>{
 const media={url:new URL('/node_modules/.cache/preview-diagnostics-tests/'+name,location.href).href,width:1080,height:1920,duration:3};
 const sources=[media,media,media];
 window.diagnostics.length=0;
 root.render(React.createElement(TemplatePreview,{key,draft,media:sources,resolved:resolveTemplate(draft,previewContext(sources)),videoInputKey:name,onMediaChange:()=>{},onCatalog:()=>{}}));
};
</script></body></html>`;

// 只在顶层页面伪装桌面端：diagnostics_log 的记录存入 window.diagnostics。
const desktop = () => {
  if (window !== window.top) return;
  window.isTauri = true;
  window.diagnostics = [];
  window.__TAURI_INTERNALS__ = {
    invoke: async (command, args) => {
      if (command === "diagnostics_log") window.diagnostics.push(args);
      return command === "diagnostics_open" ? { path: "/tmp/diagnostics.log", viewerError: null } : null;
    },
    transformCallback: () => 0,
  };
};

const server = await createServer({ server: { host: "localhost", port: 0, open: false }, plugins: [{
  name: "preview-diagnostics-test-page",
  configureServer(vite) {
    vite.middlewares.use("/__diagnostics_test", async (_req, res, next) => {
      try {
        res.setHeader("Content-Type", "text/html");
        res.end(await vite.transformIndexHtml("/__diagnostics_test", html));
      } catch (error) { next(error); }
    });
  },
}] });
let browser;
try {
  await server.listen();
  browser = await chromium.launch({ channel: "chrome", headless: true });
  // 授权测试只控制网络结果：默认返回和服务端一致的“无证书”JSON，授权失败场景再改成 502；SDK 不等这个结果，立即拿到同样的无证书结果。
  let licenseFailure = false;
  /** 打开挂载生产组件的测试页；每次都是新的浏览器上下文，不共用其他页面已加载的 SDK 脚本。 */
  const openPage = async () => {
    const opened = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    // 首次运行需要联网下载 SDK 与字体，慢网络下 90 秒会误报；等待页面与断言使用更宽松的时限。
    opened.setDefaultTimeout(180000);
    opened.setDefaultNavigationTimeout(180000);
    await opened.addInitScript(desktop);
    await opened.route(/vpp-license-proxy\.aliyuncs\.com/, async (route) => {
      if (route.request().method() === "OPTIONS") {
        await route.fulfill({ status: 204, headers: { "access-control-allow-origin": "*", "access-control-allow-methods": "POST, OPTIONS", "access-control-allow-headers": "content-type" } });
      } else if (licenseFailure) {
        await route.fulfill({ status: 502, contentType: "text/html", headers: { "access-control-allow-origin": "*" }, body: "bad gateway" });
      } else {
        await route.fulfill({ status: 200, contentType: "application/json", headers: { "access-control-allow-origin": "*" }, body: '{"message":"Signature verification error on the server."}' });
      }
    });
    await opened.goto(`http://localhost:${server.httpServer.address().port}/__diagnostics_test`);
    await opened.waitForFunction(() => typeof window.mountPreview === "function");
    return opened;
  };
  const page = await openPage();
  /** 取出已转发的日志文本，按“级别 来源：内容”拼接。 */
  const logs = () => page.evaluate(() => window.diagnostics.map((entry) => `${entry.level} ${entry.source}：${entry.message}`));

  // SDK 的 <video> 不挂到文档上：加载失败不能只显示“已就绪”，预览按视频序号提示，日志带地址与 MediaError 码。
  await page.evaluate(() => window.mountPreview("broken.mp4"));
  await page.getByText("视频 1 无法在播放器中播放（格式或地址不受支持），请检查视频编码与跨域访问配置后重试预览。").waitFor();
  assert(await page.getByRole("button", { name: "重试预览" }).isVisible());
  await page.waitForFunction(() => window.diagnostics.some((entry) => entry.message.startsWith("video 加载失败；地址 ") && entry.message.includes("broken.mp4#t=0.1；媒体错误 4（格式或地址不受支持）")));
  // 回归：canplay 超时只说明无法确认可播放，不能把素材加载失败的预览重新标成“预览已就绪”并启用播放。
  await page.waitForTimeout(3500);
  assert.equal(await page.getByRole("button", { name: "播放", exact: true }).isDisabled(), true);
  assert.equal(await page.getByText("预览已就绪，点击播放查看效果", { exact: true }).count(), 0);
  assert.match(await page.locator('p[role="alert"]').textContent(), /预览素材无法播放/);

  // 回归：Windows 测试机上授权请求失败后 SDK 把渲染引擎标为初始化失败，视频黑屏、点播放时间不走。授权改在后台请求后，
  // 请求失败（这里是 502）时 IMS 预览照常就绪并播放两次，只记一条警告，不降级成基础视频。
  licenseFailure = true;
  await page.evaluate(() => window.mountPreview("ok.mp4", "license-failed"));
  await page.getByText("预览已就绪，点击播放查看效果", { exact: true }).waitFor();
  for (let round = 0; round < 2; round++) {
    const before = Number(await page.getByLabel("预览进度").inputValue());
    await page.getByRole("button", { name: "播放", exact: true }).click();
    await page.waitForFunction((from) => Number(document.querySelector('input[aria-label="预览进度"]').value) >= from + 0.5, before, { timeout: 5000 });
    await page.getByRole("button", { name: "暂停", exact: true }).click();
  }
  await page.waitForFunction(() => window.diagnostics.some((entry) => entry.message === "渲染授权请求失败（Error: HTTP 502）；预览不等授权，照常播放"));
  assert.equal(await page.getByLabel("基础视频预览").count(), 0);
  licenseFailure = false;

  // 回归：SDK 本身加载不了（这里拦掉 player.js）时普通视频必须继续播放：切换基础视频，只停用 IMS 文字与特效。
  // 用新的浏览器上下文：页面加载过 player.js 后，新 iframe 直接用内存缓存里的脚本、不再发请求，拦截不到。
  const isolated = await openPage();
  await isolated.route(/video-editing-websdk\/5\.2\.2\/player\.js/, (route) => route.abort());
  await isolated.evaluate(() => window.mountPreview("ok.mp4"));
  const basic = isolated.getByLabel("基础视频预览");
  await basic.waitFor();
  await isolated.getByText("IMS 渲染暂不可用，已切换基础视频预览；文字与特效暂不可用。", { exact: true }).waitFor();
  assert(await isolated.getByRole("button", { name: "重试 IMS 预览" }).isVisible());
  await basic.evaluate((video) => video.play());
  await isolated.waitForFunction(() => document.querySelector('video[aria-label="基础视频预览"]').currentTime > 0.5, null, { timeout: 5000 });
  await isolated.close();

  // 正常视频：没有失败提示与加载失败记录，播放后日志记录播放状态变化；播放一秒多后暂停，写出流畅度汇总
  // （帧率、丢帧与主线程长帧统计；Chrome 支持 long-animation-frame）。
  await page.evaluate(() => window.mountPreview("ok.mp4"));
  await page.getByText("预览已就绪，点击播放查看效果", { exact: true }).waitFor();
  await page.getByRole("button", { name: "播放", exact: true }).click();
  await page.waitForFunction(() => window.diagnostics.some((entry) => /^播放状态 playing（/.test(entry.message)));
  await page.waitForTimeout(1500);
  // 回归：setTimeline 返回时 SDK 还没建好播放节点，就绪后立刻点播放会在 0 秒“播放结束”；就绪要等 SDK 发出 canplay。
  const started = await logs();
  assert(!started.some((entry) => entry.endsWith("播放状态 ended（0.00 秒）")), started.join("\n"));
  await page.getByRole("button", { name: "暂停", exact: true }).click();
  await page.waitForFunction(() => window.diagnostics.some((entry) => entry.message.startsWith("播放 ")));
  const entries = await logs();
  const summary = entries.find((entry) => entry.includes("模板预览：播放 "));
  assert.match(summary, /画面 [\d.]+ fps，最长间隔 \d+ ms.*；视频丢帧 \d+\/\d+；等待数据 \d+ 次；(主线程长帧 \d+ 次|无主线程长帧)/, summary);
  console.log(summary);
  assert(!entries.some((entry) => entry.includes("加载失败")), entries.join("\n"));
  assert.equal(await page.getByText("无法在播放器中播放", { exact: false }).count(), 0);
  // SDK 初始化时打印的空警告不再进入日志。
  assert(!entries.some((entry) => entry.endsWith("模板预览 SDK：")), entries.join("\n"));
} finally {
  await browser?.close();
  await server.close();
}
