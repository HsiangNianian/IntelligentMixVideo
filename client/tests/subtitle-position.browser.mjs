/** 同屏字幕真实预览回归：生产参数面板、时间线与 SDK 5.2.2，检查实际文字像素；执行 bun tests/subtitle-position.browser.mjs，需要 Chrome、FFmpeg 和 SDK/字体网络。 */
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { chromium } from "playwright-core";
import { createServer } from "vite";

const cache = resolve("node_modules/.cache/subtitle-position-tests");
mkdirSync(cache, { recursive: true });
// 黑色视频让文字像素可直接测量，不调用业务 API、合成或抠像服务。
const video = spawnSync("ffmpeg", ["-y", "-v", "error", "-f", "lavfi", "-i", "color=black:size=1080x1920:rate=30", "-t", "4", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", resolve(cache, "black.mp4")], { encoding: "utf8" });
assert.equal(video.status, 0, video.error?.message || video.stderr);

// 只替换工作区的媒体和草稿来源；输入控件、React 更新、SDK 加载和最终绘制都走生产实现。
const html = `<!doctype html><html><body><div id="root"></div><script type="module">
import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {TemplatePreview} from '/src/features/templates/TemplatePreview.tsx';
import {SplitScreenEditor} from '/src/features/templates/SplitScreenEditor.tsx';
import {defaultEditor,newDraft} from '/src/features/templates/model.ts';
import {defaultSplitScreen} from '/src/features/templates/splitScreen.ts';
import {previewContext,resolveTemplate} from '/src/features/templates/tracks.ts';
import '/src/styles/globals.css';
import '/src/features/templates/template-workspace.css';
const media=Array.from({length:3},()=>({url:location.origin+'/node_modules/.cache/subtitle-position-tests/black.mp4',width:1080,height:1920,duration:4}));
const layout=defaultSplitScreen();
layout.enabled=true; layout.backgroundColor='#000000'; layout.avatar.radius=0; layout.avatar.borderWidth=0;
layout.subtitle={x:50,y:10}; layout.subtitleEn={x:50,y:10};
const initial={...newDraft(),split_screen:layout,tracks:[{id:'subtitle',target:'subtitle',start_mode:'seconds',start:0,duration:null,editor:{...defaultEditor,subtitle:'坐标测试',subtitleSize:80}}]};
// 与真实工作区一样持有稳定的资产列表，避免测试壳在播放器每次更新时创建新的默认数组。
const sprites=[];
// 测试壳只维护草稿；面板更新和预览渲染沿用工作区的生产组件。
function Harness(){
 const [draft,setDraft]=useState(initial);
 // 英文必须有对应中文对象；英文用例将中文放在底部，单独测量顶部的英文像素。
 window.setSubtitle=(editor,language='zh',x=50)=>setDraft(current=>({
  ...current,split_screen:{...current.split_screen,subtitle:{x,y:language==='en'?80:10}},
  tracks:[...(language==='en'?[initial.tracks[0]]:[]),{...initial.tracks[0],id:language==='en'?'subtitle-en':'subtitle',language,editor:{...initial.tracks[0].editor,...editor}}]
 }));
 return React.createElement('div',{className:'template-workspace',style:{display:'grid',gridTemplateColumns:'400px 1fr'}},
  React.createElement(SplitScreenEditor,{value:draft.split_screen,onChange:split_screen=>setDraft(current=>({...current,split_screen}))}),
  React.createElement(TemplatePreview,{draft,media,sprites,resolved:resolveTemplate(draft,previewContext(media)),videoInputKey:'subtitle-position',onMediaChange:()=>{},onCatalog:catalog=>{window.catalog=catalog;}}));
}
createRoot(document.getElementById('root')).render(React.createElement(Harness));
</script></body></html>`;
const server = await createServer({ server: { host: "localhost", port: 0, open: false }, plugins: [{
  name: "subtitle-position-test",
  /** 提供测试入口；模块与视频仍由实际 Vite 服务加载。 */
  configureServer(vite) {
    vite.middlewares.use("/__subtitle_test", async (_req, res, next) => {
      try {
        res.setHeader("Content-Type", "text/html");
        res.end(await vite.transformIndexHtml("/__subtitle_test", html));
      } catch (error) { next(error); }
    });
  },
}] });
let browser;
try {
  await server.listen();
  browser = await chromium.launch({ ...(process.env.IMV_TEST_CHROME ? { executablePath: process.env.IMV_TEST_CHROME } : { channel: "chrome" }), headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 4 });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => { if (message.text().includes("Maximum update depth exceeded")) errors.push(message.text()); });
  page.setDefaultTimeout(60000);
  await page.goto(`http://localhost:${server.httpServer.address().port}/__subtitle_test`);
  const ready = page.getByText("预览已就绪，点击播放查看效果", { exact: true });
  await ready.waitFor();
  await page.getByText("素材与数字人同屏 · 已开启", { exact: true }).click();
  // 主用例只观察 SDK 入参与时钟；最后的原生对照才用同坐标、无换行的短字幕，避免把 SDK 自身的动画特性误判为回归。
  await page.evaluate(() => {
    const proto = document.querySelector('iframe[title="模板预览播放器"]').contentWindow.AliyunTimelinePlayer.prototype;
    const setTimeline = proto.setTimeline;
    const time = Object.getOwnPropertyDescriptor(proto, 'currentTime');
    Object.defineProperty(proto, 'currentTime', { ...time, get() { window.sdkPlayer = this; return time.get.call(this); } });
    proto.setTimeline = function (timeline) {
      window.sdkPlayer = this;
      if (timeline.VideoTracks?.some(row => row.Type === 'Subtitle')) {
        if (window.nativeSubtitlePosition) for (const row of timeline.VideoTracks.filter(row => row.Type === 'Subtitle')) for (const clip of row.VideoTrackClips) {
          delete clip.AdaptMode;
          clip.Content = '坐标测试';
        }
        window.applications = (window.applications || 0) + 1;
      }
      return setTimeline.call(this, timeline);
    };
  });

  /** 等待这次参数确实进入 SDK 并完成 canplay，避免读到防抖之前的旧画面。 */
  async function update(change) {
    const count = await page.evaluate(() => window.applications || 0);
    await change();
    await page.waitForFunction(count => window.applications > count, count);
    await ready.waitFor();
    assert.equal(await page.getByLabel("预览进度", { exact: true }).inputValue(), "0", "调参后仍应回到开头");
    await page.getByRole("button", { name: "播放", exact: true }).waitFor();
  }

  /** 走生产进度条定位到实际帧；文字在 SDK 的初始零帧可能还未绘制。 */
  async function seek(seconds) {
    const progress = page.getByLabel("预览进度", { exact: true });
    if (await progress.inputValue() === String(seconds)) return;
    await progress.evaluate((input, seconds) => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, String(seconds));
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }, seconds);
    await page.waitForFunction(seconds => Math.abs(window.sdkPlayer.currentTime - seconds) < 0.04, seconds);
    await page.getByText("预览已暂停", { exact: true }).waitFor();
  }

  /** 通过实际同屏输入修改 X；同值不触发 React 更新。 */
  async function moveX(value, language = "中文") {
    const input = page.getByLabel(`${language}字幕中心 X %`, { exact: true });
    if (await input.inputValue() !== String(value)) await update(() => input.fill(String(value)));
  }

  /** 静态截图与播放帧共用像素测量；透明动画帧合成到黑底，测量可见颜色及字形。 */
  async function readPixels(dataUrl) {
    return page.evaluate(async dataUrl => {
      const layer = new Image(); layer.src = dataUrl; await layer.decode();
      const canvas = document.createElement('canvas'); canvas.width = 1080; canvas.height = 700;
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = '#000'; ctx.fillRect(0, 0, 1080, 700);
      ctx.drawImage(layer, 0, 0, 1080, 1920);
      const data = ctx.getImageData(0, 0, 1080, 700).data;
      const points = []; const rows = [];
      let minX = 1080, maxX = -1, minY = 700, maxY = -1;
      for (let y = 100; y < 700; y++) {
        let count = 0;
        for (let x = 0; x < 1080; x++) {
          const offset = (y * 1080 + x) * 4;
          if (Math.max(data[offset], data[offset + 1], data[offset + 2]) < 80) continue;
          points.push([x, y, data[offset], data[offset + 1], data[offset + 2]]);
          count++; minX = Math.min(x, minX); maxX = Math.max(x, maxX); minY = Math.min(y, minY); maxY = Math.max(y, maxY);
        }
        if (count >= 3) rows.push(y);
      }
      return { minX, maxX, minY, maxY, rows, points, image: canvas.toDataURL() };
    }, dataUrl);
  }

  /** 截取浏览器实际合成帧；只在截图时隐藏其他 Canvas，隔离随机水印，不覆盖字幕样式或坐标。 */
  async function pixels(name) {
    // playerSeeked 后字幕 worker 的帧可能稍晚提交；有界等待实际字形，不用固定延时假定它已绘制。
    let result;
    for (let attempt = 0; attempt < 20; attempt++) {
      const screenshot = await page.locator('iframe[title="模板预览播放器"]').screenshot({ style: 'canvas:not(#subtitle-canvas){visibility:hidden!important} html,body{background:#000!important}' });
      result = await readPixels('data:image/png;base64,' + screenshot.toString('base64'));
      if (result.points.length > 100) break;
      await page.waitForTimeout(100);
    }
    writeFileSync(resolve(cache, `${name}.png`), Buffer.from(result.image.split(',')[1], 'base64'));
    delete result.image;
    assert(result.points.length > 100, `${name} 应真正绘制字幕`);
    console.log(name, JSON.stringify({ x: [result.minX, result.maxX], y: [result.minY, result.maxY], pixels: result.points.length }));
    return result;
  }

  await page.getByRole("button", { name: "播放", exact: true }).click();
  await page.waitForFunction(() => window.sdkPlayer.currentTime >= 1);
  await page.getByRole("button", { name: "暂停", exact: true }).click();
  await seek(0.5);
  const centered = await pixels("center");
  await moveX(25);
  await seek(0.5);
  const left = await pixels("left");
  await page.screenshot({ path: resolve(cache, "page.png"), fullPage: true });
  assert(Math.abs((left.minX + left.maxX - centered.minX - centered.maxX) / 2 + 270) < 2, "X 50% → 25% 应向左移动 270 像素");
  assert.deepEqual(left.rows, centered.rows, "X 调整不应改变 Y 或字形高度");

  /** 比较真实字形平移后的 RGB，长字幕只比较画布内可见部分，不把正常出界裁剪当作换行变化。 */
  function translated(before, after, dx) {
    const actual = new Map(after.points.map(([x, y, ...rgb]) => [y * 1080 + x, rgb]));
    let visible = 0, matched = 0;
    for (const [x, y, ...rgb] of before.points) {
      if (x + dx < 0 || x + dx >= 1080) continue;
      visible++;
      const pixel = actual.get(y * 1080 + x + dx);
      if (pixel && rgb.every((channel, i) => Math.abs(channel - pixel[i]) <= 8)) matched++;
    }
    assert(visible > 100 && matched / visible > 0.97 && matched / actual.size > 0.97,
      `文字横移 ${dx}px 后应保留字形、行距、颜色和样式，匹配 ${matched}/${visible}，实际 ${actual.size}`);
  }

  translated(centered, left, -270);
  await moveX(75); await seek(0.5);
  translated(centered, await pixels("right"), 270);
  await update(() => page.getByLabel("中文字幕顶部 Y %", { exact: true }).fill("20"));
  await seek(0.5);
  const down = await pixels("right-down");
  assert(Math.abs(down.minY - centered.minY - 192) <= 1 && Math.abs(down.maxY - centered.maxY - 192) <= 1, "Y 10% → 20% 应下移 192 像素（允许字体栅格化的 1px 差异）");
  assert.equal(down.minX, centered.minX + 270, "修改 Y 不改变 X");

  // 默认居中不加兼容标记，是原 SDK 渲染的对照；逐项检查非居中时只有位置改变。
  const flower = await page.evaluate(() => window.catalog.find(item => item.id === 'flower/CS0003-000001').id);
  const keyword = { subtitleKeywordColor: "#FFFF00", subtitleKeywordSize: 120, subtitleKeywordBold: true, subtitleKeywordItalic: true, subtitleKeywordUnderline: true, subtitleKeywordStrikeout: true };
  const cases = [
    ["long", { subtitle: "长字幕自动换行保持每一行的内容与原有排版，修改同屏字幕的中心位置以后，字号和行距都应该保持一致。" }],
    ["flower", { subtitleFlower: flower }],
    ["keyword", keyword],
    ["flower-keyword", { subtitleFlower: flower, ...keyword }],
  ];
  let keywordOnly;
  for (const [name, editor] of cases) {
    await update(() => page.evaluate(editor => window.setSubtitle(editor), editor));
    await seek(1.5);
    const before = await pixels(`${name}-center`);
    if (name === "long") {
      const lines = before.rows.filter((y, i) => i === 0 || y - before.rows[i - 1] > 5).length;
      assert(lines >= 3, `长字幕应实际自动换行，当前 ${lines} 行`);
    }
    // 组合样式必须同时有关键词颜色和额外花字效果，避免两处同时丢失效果仍通过平移比较。
    if (name === "keyword") keywordOnly = before;
    if (name === "keyword" || name === "flower-keyword") assert(before.points.filter(([, , r, g, b]) => r > 160 && g > 160 && b < 80).length > 100, "关键词应有实际黄色像素");
    if (name === "flower") assert.notDeepEqual(before.points, centered.points, "花字应实际改变渲染样式");
    if (name === "flower-keyword") assert.notDeepEqual(before.points, keywordOnly.points, "组合样式应保留关键词之外的花字效果");
    await moveX(25); await seek(1.5);
    translated(before, await pixels(`${name}-left`), -270);
  }

  // 英文走 AutoScale（SDK 当前不支持缩放），仍应响应独立同屏 X，现有英文外观保持不变。
  await update(() => page.evaluate(() => window.setSubtitle({ subtitle: 'Subtitle position test' }, 'en')));
  await seek(1.5);
  const english = await pixels("english-center");
  await moveX(75, "英文"); await seek(1.5);
  translated(english, await pixels("english-right"), 270);

  /** SDK 暂停时显示静态封面；在实际播放期间定时读取字幕画布，不改变渲染、播放速度或时钟，播放结束后停止采样。 */
  async function animationFrames() {
    await seek(0);
    await page.evaluate(() => {
      const frames = new Map();
      const canvas = document.querySelector('iframe[title="模板预览播放器"]').contentDocument.querySelector('#subtitle-canvas');
      const timer = setInterval(() => {
        const time = window.sdkPlayer.currentTime;
        const frame = Math.round(time * 30);
        if (time > 0 && time < 12 && !frames.has(frame)) frames.set(frame, { time, image: canvas.toDataURL() });
      }, 100);
      window.stopCapture = () => { clearInterval(timer); return [...frames.values()]; };
    });
    await page.getByRole("button", { name: "播放", exact: true }).click();
    await page.getByText("预览结束，点击播放可重播", { exact: true }).waitFor();
    return page.evaluate(() => window.stopCapture());
  }

  /** 比较循环动画的完整运动范围，同时确保它确实产生不同画面；原生 SDK 的摇摆轨迹本身与 X 有关。 */
  async function loopRange(frames) {
    const samples = [];
    for (const frame of frames.filter(frame => frame.time > 1)) {
      const sample = await readPixels(frame.image);
      assert(sample.points.length > 100, "循环动画不应丢失文字");
      samples.push([sample.minX, sample.maxX, sample.minY, sample.maxY, sample.points.length]);
    }
    assert(new Set(samples.map(sample => JSON.stringify(sample))).size >= 3, "循环动画应产生多个不同的实际画面");
    return [Math.min(...samples.map(sample => sample[0])), Math.max(...samples.map(sample => sample[1])), Math.min(...samples.map(sample => sample[2])), Math.max(...samples.map(sample => sample[3]))];
  }

  // 三秒入出场给低帧率软件渲染留出采样窗口；检查实际变化与稳定阶段字形，不要求两次播放恰好产生相同帧号。
  for (const [name, editor] of [
    ["fade", { subtitleIn: "in/fade_in", subtitleOut: "out/fade_out", subtitleInDuration: 3, subtitleOutDuration: 3 }],
    ["slide", { subtitleIn: "in/slide_right_in", subtitleOut: "out/slide_left_out", subtitleInDuration: 3, subtitleOutDuration: 3 }],
    ["loop", { subtitleLoop: "loop/swing_display" }],
  ]) {
    console.log(`验证 ${name} 实际播放帧`);
    await update(() => page.evaluate(editor => window.setSubtitle(editor), editor));
    const before = await animationFrames();
    await moveX(25);
    const after = await animationFrames();
    const stable = [];
    const loopBounds = [];
    for (const [index, frames] of [before, after].entries()) {
      const middle = frames.filter(frame => frame.time >= 4 && frame.time <= 8);
      assert(middle.length > 0, `${name} 应有中间阶段的播放帧`);
      const reference = await readPixels(middle[Math.floor(middle.length / 2)].image);
      assert(reference.points.length > 100, `${name} 实际播放时应绘制文字`);
      stable.push(reference);
      if (name === "loop") {
        loopBounds.push(await loopRange(frames));
        writeFileSync(resolve(cache, `loop-${index}.png`), Buffer.from(reference.image.split(',')[1], 'base64'));
      } else {
        for (const [phase, candidates] of [["入场", frames.filter(frame => frame.time < 3)], ["出场", frames.filter(frame => frame.time > 9)]]) {
          let animated = false;
          for (const frame of candidates) {
            const sample = await readPixels(frame.image);
            if (sample.points.length <= 100) continue;
            const changing = name === "fade"
              ? sample.points.reduce((sum, pixel) => sum + pixel[2], 0) < reference.points.reduce((sum, pixel) => sum + pixel[2], 0) * 0.9
              : (sample.minX + sample.maxX) / 2 < (reference.minX + reference.maxX) / 2 - 10;
            if (!changing) continue;
            animated = true;
            writeFileSync(resolve(cache, `${name}-${index}-${phase}.png`), Buffer.from(sample.image.split(',')[1], 'base64'));
            break;
          }
          assert(animated, `${name} 在 X=${index ? 25 : 50}% 时应实际显示${phase}动画`);
        }
      }
    }
    if (name !== "loop") translated(stable[0], stable[1], -270);
    else {
      // 原生短字幕不需要换行；由 SDK 自己按 X=25% 定位，作为相同坐标下的动画基准，不要求摇摆轨迹刚性平移。
      await page.evaluate(() => { window.nativeSubtitlePosition = true; });
      await update(() => page.evaluate(editor => window.setSubtitle(editor, 'zh', 25), editor));
      const native = await loopRange(await animationFrames());
      await page.evaluate(() => { window.nativeSubtitlePosition = false; });
      assert(loopBounds[1].every((value, i) => Math.abs(value - native[i]) <= 12),
        `同坐标的循环动画应与原生 SDK 一致（允许采样相位差）：${JSON.stringify({ actual: loopBounds[1], native })}`);
    }
    console.log(`${name} 实际动画与定位通过`);
  }
  // 直接在实际播放中改参，除 UI 回零暂停外，SDK 时间也必须持续停在零帧。
  await seek(0);
  await page.getByRole("button", { name: "播放", exact: true }).click();
  await page.waitForFunction(() => window.sdkPlayer.currentTime >= 1);
  await page.getByRole("button", { name: "暂停", exact: true }).waitFor();
  await moveX(75);
  for (let sample = 0; sample < 10; sample++) {
    await page.waitForTimeout(100);
    assert(Math.abs(await page.evaluate(() => window.sdkPlayer.currentTime)) < 1 / 30, "播放中调参后 SDK 应持续停在零帧");
  }
  console.log("播放中调参回零并持续暂停通过");
  assert.deepEqual(errors, [], "真实预览不应产生页面异常");
  console.log("同屏字幕真实预览回归通过");
} finally {
  await browser?.close();
  await server.close();
}
