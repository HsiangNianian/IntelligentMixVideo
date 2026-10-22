/**
 * 模板预览播放器调试日志的真实浏览器回归：真实 SDK 与解码，原生日志由页面里的假 Tauri IPC 接收；
 * 元数据可读但播放器加载不了的视频要在预览里提示并带地址与错误码写进日志，正常播放记录播放状态；
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
window.mountPreview=(name)=>{
 const media={url:new URL('/node_modules/.cache/preview-diagnostics-tests/'+name,location.href).href,width:1080,height:1920,duration:3};
 const sources=[media,media,media];
 window.diagnostics.length=0;
 root.render(React.createElement(TemplatePreview,{key:name,draft,media:sources,resolved:resolveTemplate(draft,previewContext(sources)),videoInputKey:name,onMediaChange:()=>{},onCatalog:()=>{}}));
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
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(90000);
  await page.addInitScript(desktop);
  await page.goto(`http://localhost:${server.httpServer.address().port}/__diagnostics_test`);
  await page.waitForFunction(() => typeof window.mountPreview === "function");
  /** 取出已转发的日志文本，按“级别 来源：内容”拼接。 */
  const logs = () => page.evaluate(() => window.diagnostics.map((entry) => `${entry.level} ${entry.source}：${entry.message}`));

  // SDK 的 <video> 不挂到文档上：加载失败不能只显示“已就绪”，预览按视频序号提示，日志带地址与 MediaError 码。
  await page.evaluate(() => window.mountPreview("broken.mp4"));
  await page.getByText("视频 1 无法在播放器中播放（格式或地址不受支持），请检查视频编码与跨域访问配置后重试预览。").waitFor();
  assert(await page.getByRole("button", { name: "重试预览" }).isVisible());
  await page.waitForFunction(() => window.diagnostics.some((entry) => entry.message.startsWith("video 加载失败；地址 ") && entry.message.includes("broken.mp4#t=0.1；媒体错误 4（格式或地址不受支持）")));

  // 正常视频：没有失败提示与加载失败记录，播放后日志记录播放状态变化。
  await page.evaluate(() => window.mountPreview("ok.mp4"));
  await page.getByText("预览已就绪，点击播放查看效果", { exact: true }).waitFor();
  await page.getByRole("button", { name: "播放", exact: true }).click();
  await page.waitForFunction(() => window.diagnostics.some((entry) => /^播放状态 playing（/.test(entry.message)));
  await page.getByRole("button", { name: "暂停", exact: true }).click();
  const entries = await logs();
  assert(!entries.some((entry) => entry.includes("加载失败")), entries.join("\n"));
  assert.equal(await page.getByText("无法在播放器中播放", { exact: false }).count(), 0);
  // SDK 初始化时打印的空警告不再进入日志。
  assert(!entries.some((entry) => entry.endsWith("模板预览 SDK：")), entries.join("\n"));
} finally {
  await browser?.close();
  await server.close();
}
