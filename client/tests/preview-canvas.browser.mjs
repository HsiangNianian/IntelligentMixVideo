/** 原始画布浏览器回归：真实 SDK、视频解码、横竖屏切换、CSS 尺寸和卸载；执行 bun tests/preview-canvas.browser.mjs。 */
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { chromium } from "playwright-core";
import { createServer } from "vite";

const cache = resolve("node_modules/.cache/preview-canvas-tests");
mkdirSync(cache, { recursive: true });
process.env.TMPDIR = cache;
// FFmpeg 生成可被浏览器实际解码的短视频，全部中间文件保存在忽略目录。
for (const [name, size] of [["landscape", "1920x1080"], ["portrait", "1080x1920"]]) {
  const result = spawnSync("ffmpeg", ["-y", "-v", "error", "-f", "lavfi", "-i", `testsrc2=size=${size}:rate=10`, "-t", "2", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", resolve(cache, `${name}.mp4`)], { encoding: "utf8" });
  assert.equal(result.status, 0, result.error?.message || result.stderr);
}

// 测试页面直接挂载生产组件；React、SDK 与媒体读取均使用实际实现。
const html = `<!doctype html><html><body><div id="root"></div><script type="module">
import React from 'react';
import {createRoot} from 'react-dom/client';
import {TemplatePreview} from '/src/features/templates/TemplatePreview.tsx';
import {newDraft} from '/src/features/templates/model.ts';
import {readMasterVideo} from '/src/features/templates/media.ts';
import {buildTimeline} from '/src/features/templates/timeline.ts';
import {readCatalog,loadPreviewFont} from '/src/features/templates/sdk.ts';
import '/src/styles/globals.css';
const root=createRoot(document.getElementById('root'));
const draft=newDraft();
window.mountPreview=async(name)=>{
 const media=await readMasterVideo('/node_modules/.cache/preview-canvas-tests/'+name+'.mp4',new AbortController().signal);
 root.render(React.createElement(TemplatePreview,{draft,media,onCatalog:()=>{}}));
 return media;
};
window.unmountPreview=()=>root.unmount();
window.checkWrapping=async()=>{
 await loadPreviewFont();
 const editor={...(await import('/src/features/templates/model.ts')).defaultEditor,title:'画布文字宽度验证'.repeat(20),titleSize:100};
 const textDraft={...draft,tracks:[{id:'title',target:'title',start_mode:'seconds',start:0,duration:null,editor}]};
 return [1920,1080].map(width=>buildTimeline(textDraft,readCatalog(),{url:location.origin+'/node_modules/.cache/preview-canvas-tests/landscape.mp4',width,height:1080,duration:2}).SubtitleTracks[0].SubtitleTrackClips[0].Content);
};
</script></body></html>`;
const server = await createServer({ server: { host: "localhost", port: 0, open: false }, plugins: [{
  name: "preview-canvas-test-page",
  configureServer(vite) {
    vite.middlewares.use("/__canvas_test", async (_req, res, next) => {
      try {
        res.setHeader("Content-Type", "text/html");
        res.end(await vite.transformIndexHtml("/__canvas_test", html));
      } catch (error) { next(error); }
    });
  },
}] });
let browser;
try {
  await server.listen();
  const address = server.httpServer.address();
  browser = await chromium.launch({ channel: "chrome", headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(60000);
  await page.goto(`http://localhost:${address.port}/__canvas_test`);
  await page.waitForFunction(() => typeof window.mountPreview === "function");

  // 每次完成初始化后，实际 Canvas 像素与媒体元数据相同；切换后旧 iframe 被移除。
  let previous;
  for (const [name, width, height] of [["landscape", 1920, 1080], ["portrait", 1080, 1920], ["landscape", 1920, 1080]]) {
    const media = await page.evaluate(name => window.mountPreview(name), name);
    assert.deepEqual([media.width, media.height], [width, height]);
    await page.getByText(`${width} × ${height} · 0.0 / 2 秒`, { exact: true }).waitFor();
    await page.getByText("预览已就绪，点击播放查看效果", { exact: true }).waitFor();
    if (previous) {
      assert.equal(await previous.evaluate(frame => frame.isConnected), false);
      await previous.dispose();
    }
    const frame = page.locator('iframe[title="模板预览播放器"]');
    assert.equal(await frame.count(), 1);
    previous = await frame.elementHandle();
    await page.waitForFunction(({ width, height }) => {
      const canvases = [...document.querySelector('iframe').contentDocument.querySelectorAll('canvas')];
      return canvases.length > 0 && canvases.every(canvas => canvas.width === width && canvas.height === height);
    }, { width, height });
    const box = await frame.boundingBox();
    assert(Math.abs(box.width / box.height - width / height) < 0.01);
    assert(box.height <= 601);

    // 实际播放推进后暂停，验证更换尺寸后的播放器仍可工作。
    await page.evaluate(() => {
      const label = document.querySelector('section[aria-label="实时预览"] span');
      window.displayTimes = [];
      window.timeObserver = new MutationObserver(() => {
        window.displayTimes.push(Number(label.textContent.split(" · ")[1].split(" / ")[0]));
      });
      window.timeObserver.observe(label, { characterData: true, childList: true, subtree: true });
    });
    await page.getByRole("button", { name: "播放", exact: true }).click();
    await page.getByText(new RegExp(`${width} × ${height} · [1-2]\\.[0-9] / 2 秒`)).waitFor();
    await page.getByRole("button", { name: "暂停", exact: true }).click();
    // 数字时间仅在十分之一秒发生变化时更新，不随重复视频帧反复更新。
    const times = await page.evaluate(() => {
      window.timeObserver.disconnect();
      return window.displayTimes;
    });
    assert(times.length > 0);
    assert(times.every((time, index) => Number.isFinite(time) && (index === 0 || time > times[index - 1])));
    assert(times.length <= Math.round(times.at(-1) * 10));
    if (name === "portrait") {
      await page.setViewportSize({ width: 390, height: 844 });
      const narrow = await frame.boundingBox();
      assert(Math.abs(narrow.width / narrow.height - width / height) < 0.01);
      assert(narrow.height <= 844 * 0.6 + 1);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.setViewportSize({ width: 1440, height: 1000 });
    }
  }
  // 真实 Canvas 字体测量应随画布宽度改变折行，正文内容保持完整。
  const [wide, narrow] = await page.evaluate(() => window.checkWrapping());
  assert(narrow.split("\n").length > wide.split("\n").length);
  assert.equal(wide.replaceAll("\n", ""), "画布文字宽度验证".repeat(20));
  assert.equal(narrow.replaceAll("\n", ""), wide.replaceAll("\n", ""));
  await page.evaluate(() => window.unmountPreview());
  assert.equal(await page.locator("iframe").count(), 0);
  assert.equal(await previous.evaluate(frame => frame.isConnected), false);
  console.log("PASS：原始画布、横竖屏切换、实际播放、窄屏比例、文字折行和卸载清理。");
} finally {
  await browser?.close();
  await server.close();
}
