/** 固定画布浏览器回归：真实 SDK、视频解码、横竖屏视频切换时画布固定 1080×1920、CSS 尺寸、全屏时 AI 资产叠加层的尺寸和卸载；执行 bun tests/preview-canvas.browser.mjs。 */
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

// 测试页面直接挂载生产组件；React、SDK 与媒体读取均使用实际实现，外层与模板编辑相同地套用工作区样式（含全屏规则）。
const html = `<!doctype html><html><body><div id="root"></div><script type="module">
import React from 'react';
import {createRoot} from 'react-dom/client';
import {TemplatePreview} from '/src/features/templates/TemplatePreview.tsx';
import {newDraft} from '/src/features/templates/model.ts';
import {readMasterVideo} from '/src/features/templates/media.ts';
import {buildTimeline} from '/src/features/templates/timeline.ts';
import {readCatalog} from '/src/features/templates/sdk.ts';
import {previewContext,resolveTemplate} from '/src/features/templates/tracks.ts';
import {setApiBase} from '/src/lib/api-base.ts';
import '/src/styles/globals.css';
import '/src/features/templates/template-workspace.css';
// AI 资产播放器指向本测试服务的空白页，不访问业务后端；全屏用例只核对它的尺寸。
setApiBase(location.origin+'/__sprite_preview');
const root=createRoot(document.getElementById('root'));
const draft=newDraft();
window.mountPreview=async(name,sprites)=>{
 const media=await readMasterVideo('/node_modules/.cache/preview-canvas-tests/'+name+'.mp4',new AbortController().signal);
 const sources=[media,media,media];
 root.render(React.createElement('div',{className:'template-workspace'},React.createElement(TemplatePreview,{draft,media:sources,resolved:resolveTemplate(draft,previewContext(sources)),videoInputKey:name,sprites,onMediaChange:()=>{},onCatalog:()=>{}})));
 return media;
};
window.unmountPreview=()=>root.unmount();
// 播放器实际时间与时间轴游标指示的时间（按 PreviewTimeline 的刻度换算：起点 20px，整段 5 格）。
window.cursorSeconds=()=>{
 const box=document.querySelector('[aria-label="预览播放位置"]');
 const perSecond=Math.max(40,(box.clientWidth-42)/5)*5/Number(box.getAttribute('aria-valuemax'));
 return [window.sdkPlayer.currentTime,(parseFloat(document.querySelector('.timeline-editor-cursor').style.left)-20)/perSecond];
};
window.checkTextContent=async()=>{
 const editor={...(await import('/src/features/templates/model.ts')).defaultEditor,title:'画布文字宽度验证'.repeat(20),subtitle:'字幕宽度验证'.repeat(20),titleSize:100,subtitleSize:100};
 const textDraft={...draft,tracks:['title','subtitle'].map(target=>({id:target,target,start_mode:'seconds',start:0,duration:null,editor}))};
 return [1920,1080].map(width=>{const media={url:location.origin+'/node_modules/.cache/preview-canvas-tests/landscape.mp4',width,height:1080,duration:2};const sources=[media,media,media];return buildTimeline(textDraft,readCatalog(),sources,resolveTemplate(textDraft,previewContext(sources))).SubtitleTracks.map(track=>track.SubtitleTrackClips[0].Content);});
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
    vite.middlewares.use("/__sprite_preview", (_req, res) => {
      res.setHeader("Content-Type", "text/html");
      res.end("<!doctype html><title>sprite</title>");
    });
  },
}] });
let browser;
try {
  await server.listen();
  const address = server.httpServer.address();
  browser = await chromium.launch({ channel: "chrome", headless: true });
  // 按 2 倍屏打开：SDK 内置的 Konva 默认按屏幕倍数放大画布，下面核对画布仍与视频原尺寸一致。
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2 });
  page.setDefaultTimeout(60000);
  await page.goto(`http://localhost:${address.port}/__canvas_test`);
  await page.waitForFunction(() => typeof window.mountPreview === "function");

  // 回归：画布原先跟着视频 1 的尺寸变。现在横屏、竖屏视频初始化后，实际 Canvas 像素都固定为成片的 1080×1920
  // （横屏视频按比例放入、模糊填充留白）；切换后旧 iframe 被移除。
  let previous;
  const cases = [["landscape", 1920, 1080], ["portrait", 1080, 1920], ["landscape", 1920, 1080]];
  for (const [index, [name, width, height]] of cases.entries()) {
    const media = await page.evaluate(name => window.mountPreview(name), name);
    assert.deepEqual([media.width, media.height], [width, height]);
    await page.getByText("1080 × 1920", { exact: true }).waitFor();
    await page.getByText("预览已就绪，点击播放查看效果", { exact: true }).waitFor();
    if (previous) {
      assert.equal(await previous.evaluate(frame => frame.isConnected), false);
      await previous.dispose();
    }
    const frame = page.locator('iframe[title="模板预览播放器"]');
    assert.equal(await frame.count(), 1);
    previous = await frame.elementHandle();
    await page.waitForFunction(() => {
      const canvases = [...document.querySelector('iframe').contentDocument.querySelectorAll('canvas')];
      return canvases.length > 0 && canvases.every(canvas => canvas.width === 1080 && canvas.height === 1920);
    });
    const box = await frame.boundingBox();
    assert(Math.abs(box.width / box.height - 1080 / 1920) < 0.01);
    assert(box.height <= 601);

    // 实际播放推进后暂停，验证更换尺寸后的播放器仍可工作。
    await page.evaluate(() => {
      const label = document.querySelector('.template-preview-controls .tabular-nums');
      window.displayTimes = [];
      window.timeObserver = new MutationObserver(() => {
        window.displayTimes.push(Number(document.querySelector('input[aria-label="预览进度"]').value));
      });
      window.timeObserver.observe(label, { characterData: true, childList: true, subtree: true });
      // 时间轴组件每次移动游标都整体重渲染，记录播放期间游标位置的实际变化次数。
      window.cursorMoves = 0;
      window.cursorObserver = new MutationObserver((records) => { window.cursorMoves += records.length; });
      window.cursorObserver.observe(document.querySelector('.timeline-editor-cursor'), { attributes: true, attributeFilter: ['style'] });
    });
    // 记下 SDK 播放器实例，供下面核对游标与播放器的实际时间。
    await page.evaluate(() => {
      const proto = document.querySelector('iframe').contentWindow.AliyunTimelinePlayer.prototype;
      const time = Object.getOwnPropertyDescriptor(proto, 'currentTime');
      Object.defineProperty(proto, 'currentTime', { ...time, get() { window.sdkPlayer = this; return time.get.call(this); } });
    });
    await page.getByRole("button", { name: "播放", exact: true }).click();
    await page.waitForFunction(() => Number(document.querySelector('input[aria-label="预览进度"]').value) >= 1);
    // 暂停前统计游标的移动次数，暂停时的校准另行核对。
    const cursorMoves = await page.evaluate(() => { window.cursorObserver.disconnect(); return window.cursorMoves; });
    await page.getByRole("button", { name: "暂停", exact: true }).click();
    // 数字时间和时间轴游标仅在十分之一秒发生变化时更新，不随 SDK 的每个渲染帧反复更新（#210）。
    const times = await page.evaluate(() => {
      window.timeObserver.disconnect();
      return window.displayTimes;
    });
    assert(times.length > 0);
    assert(times.every((time, index) => Number.isFinite(time) && (index === 0 || time > times[index - 1])));
    assert(times.length <= Math.round(times.at(-1) * 10));
    assert(cursorMoves > 0 && cursorMoves <= Math.round(times.at(-1) * 10), `播放约 ${times.at(-1)} 秒游标移动了 ${cursorMoves} 次`);
    // 播放中游标按 0.1 秒更新，暂停后必须落在播放器实际停下的时间上，而不是停在最近一次更新处（可落后 0.1 秒）；
    // 在不同的帧相位暂停几次，避免恰好停在更新帧上。
    for (const wait of [0, 17, 33]) {
      const before = await page.evaluate(() => Number(document.querySelector('input[aria-label="预览进度"]').value));
      await page.getByRole("button", { name: "播放", exact: true }).click();
      await page.waitForFunction((from) => Number(document.querySelector('input[aria-label="预览进度"]').value) >= from + 0.3, before);
      await page.waitForTimeout(wait);
      await page.getByRole("button", { name: "暂停", exact: true }).click();
      const [stopped, shown] = await page.evaluate(() => window.cursorSeconds());
      assert(Math.abs(stopped - shown) < 0.005, `暂停在 ${stopped} 秒，游标显示 ${shown} 秒`);
    }
    // 播放到结尾自行停止时，游标同样要到达播放器停下的时间。
    if (index === cases.length - 1) {
      await page.getByRole("button", { name: "播放", exact: true }).click();
      await page.getByText("预览结束，点击播放可重播", { exact: true }).waitFor();
      const [stopped, shown] = await page.evaluate(() => window.cursorSeconds());
      assert(Math.abs(stopped - shown) < 0.005, `播放结束在 ${stopped} 秒，游标显示 ${shown} 秒`);
    }
    if (name === "portrait") {
      await page.setViewportSize({ width: 390, height: 844 });
      const narrow = await frame.boundingBox();
      assert(Math.abs(narrow.width / narrow.height - 1080 / 1920) < 0.01);
      assert(narrow.height <= 844 * 0.6 + 1);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.setViewportSize({ width: 1440, height: 1000 });
    }
  }
  // 全屏时画布撑满屏幕：AI 资产叠加层与 SDK 播放器共用按视频比例居中的画面框，竖屏视频在横屏屏幕上全屏时字效不按整个屏幕放大。
  const sprite = { id: "fullscreen-sprite", spriteId: "fullscreen-sprite", aspect: 1080 / 1920, name: "全屏字效", start: 0, end: 2, length: 2 };
  await page.evaluate(sprite => window.mountPreview("portrait", [sprite]), sprite);
  await page.getByText("1080 × 1920", { exact: true }).waitFor();
  await page.getByText("预览已就绪，点击播放查看效果", { exact: true }).waitFor();
  const boxes = () => page.evaluate(() => ["模板预览播放器", "AI 资产预览：全屏字效"].map(title => {
    const { x, y, width, height } = document.querySelector(`iframe[title="${title}"]`).getBoundingClientRect();
    return [x, y, width, height];
  }));
  const sameBox = ([player, overlay]) => player.every((value, index) => Math.abs(value - overlay[index]) <= 1);
  assert(sameBox(await boxes()));
  await page.getByRole("button", { name: "全屏预览" }).click();
  await page.waitForFunction(() => document.fullscreenElement?.getAttribute("aria-label") === "模板视频预览");
  const [player, overlay] = await boxes();
  assert(sameBox([player, overlay]), `全屏时播放器 ${player}，AI 资产 ${overlay}`);
  assert(Math.abs(player[3] - 1000) <= 1 && Math.abs(player[2] / player[3] - 1080 / 1920) < 0.01, `全屏播放器 ${player}`);
  await page.evaluate(() => document.exitFullscreen());
  await page.waitForFunction(() => !document.fullscreenElement);
  assert(sameBox(await boxes()));
  // 两种画布宽度都保留标题和字幕原文，不插入换行。
  const [wide, narrow] = await page.evaluate(() => window.checkTextContent());
  assert.deepEqual(wide, ["画布文字宽度验证".repeat(20), "字幕宽度验证".repeat(20)]);
  assert.deepEqual(narrow, wide);
  await page.evaluate(() => window.unmountPreview());
  assert.equal(await page.locator("iframe").count(), 0);
  assert.equal(await previous.evaluate(frame => frame.isConnected), false);
  console.log("PASS：原始画布、横竖屏切换、实际播放、时间与游标更新频率、窄屏比例、全屏 AI 资产尺寸、文字内容和卸载清理。");
} finally {
  await browser?.close();
  await server.close();
}
