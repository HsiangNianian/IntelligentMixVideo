/** 真实 SDK 圆形蒙版、羽化与人物抠像回归；bun tests/avatar-preview.browser.mjs，需要 Chrome、FFmpeg、服务端虚拟环境及 SDK 网络。 */
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { chromium } from "playwright-core";
import { createServer } from "vite";

const cache = resolve("node_modules/.cache/avatar-preview-tests");
mkdirSync(cache, { recursive: true });
for (const [name, filter] of [["circle", "color=c=green:size=360x640:rate=30,drawbox=x=0:y=0:w=180:h=640:color=red:t=fill"], ["material", "color=c=blue:size=360x640:rate=30"]]) {
  const result = spawnSync("ffmpeg", ["-y", "-v", "error", "-f", "lavfi", "-i", filter, "-t", "10", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", resolve(cache, `${name}.mp4`)], { encoding: "utf8" });
  assert.equal(result.status, 0, result.error?.message || result.stderr);
}
// 只注册实际模板资源路由，不启动应用 lifespan，不连接数据库或合成服务。
const backend = spawn(process.env.IMV_TEST_PYTHON || resolve("../server/.venv/bin/python"), ["-u", "-c", `
import socket, uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from server.template.router import router
app=FastAPI()
app.include_router(router)
app.add_middleware(CORSMiddleware,allow_origins=['*'])
sock=socket.socket();sock.bind(('127.0.0.1',0))
print(sock.getsockname()[1],flush=True)
uvicorn.Server(uvicorn.Config(app,log_level='error')).run(sockets=[sock])
`], { env: { ...process.env, PYTHONPATH: resolve("../server/src") }, stdio: ["ignore", "pipe", "inherit"] });
let server, browser, page;
const evidence = { requests: [], errors: [], pixels: {} };
try {
  const port = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("几何资源测试服务未启动")), 15000);
    backend.once("error", reject);
    backend.once("exit", (code) => { clearTimeout(timer); reject(new Error(`几何资源服务退出：${code}`)); });
    backend.stdout.once("data", (data) => { clearTimeout(timer); resolve(Number(String(data).trim())); });
  });
  process.env.VITE_API_URL = `http://localhost:${port}`;
  process.env.VITE_PREVIEW_VIDEO_1_URL = "/node_modules/.cache/avatar-preview-tests/material.mp4";
  process.env.VITE_PREVIEW_VIDEO_2_URL = "/preview/sample-2.mp4";
  process.env.VITE_PREVIEW_VIDEO_3_URL = "/preview/sample-1.mp4";
  const workspaceHtml = `<!doctype html><html lang="zh-CN"><body><div id="root"></div><script type="module">
import React from 'react';
import {createRoot} from 'react-dom/client';
import {TemplateWorkspace} from '/src/features/templates/TemplateWorkspace.tsx';
import '/src/styles/globals.css';
createRoot(document.getElementById('root')).render(React.createElement(TemplateWorkspace,{
selection:{templateId:null,name:'数字人预览验收',description:'圆形开场、圆形头像与 SDK 人物抠像'},onHome:()=>{}
}));
</script></body></html>`;
  const html = `<!doctype html><html><body><div id="root"></div><script type="module">
import React from 'react';
import {createRoot} from 'react-dom/client';
import {TemplatePreview} from '/src/features/templates/TemplatePreview.tsx';
import {newDraft} from '/src/features/templates/model.ts';
import {defaultAvatarEffects} from '/src/features/templates/avatarEffects.ts';
import {defaultSplitScreen} from '/src/features/templates/splitScreen.ts';
import {previewContext,resolveTemplate} from '/src/features/templates/tracks.ts';
import '/src/styles/globals.css';
import '/src/features/templates/template-workspace.css';
const root=createRoot(document.getElementById('root'));
const draft={...newDraft(),tracks:[],avatar_effects:{...defaultAvatarEffects(),openingEnabled:true,openingDuration:2,feather:0}};
const media=[{url:location.origin+'/node_modules/.cache/avatar-preview-tests/circle.mp4',width:360,height:640,duration:10},
{url:location.origin+'/node_modules/.cache/avatar-preview-tests/material.mp4',width:360,height:640,duration:10},
{url:location.origin+'/preview/sample-1.mp4',width:1080,height:1920,duration:10}];
const resolved=resolveTemplate(draft,previewContext(media));
const preview={avatarSource:0,previewCenter:{x:50,y:50}};
window.preview=(patch)=>{Object.assign(preview,patch);window.mount()};
window.mount=(patch={})=>{Object.assign(draft,patch);root.render(React.createElement('div',{className:'template-workspace'},React.createElement(TemplatePreview,{
draft:{...draft},...preview,media,resolved,videoInputKey:'avatar',onMediaChange:()=>{},onCatalog:()=>{}
})));};
window.effects=draft.avatar_effects;
window.layout={...defaultSplitScreen(),enabled:true,layout:'circle'};
window.pixels=(points)=>{
 const frame=document.querySelector('iframe[title="模板预览播放器"]');
 const canvas=document.createElement('canvas');canvas.width=1080;canvas.height=1920;
 const ctx=canvas.getContext('2d');
 for(const layer of frame.contentDocument.querySelectorAll('canvas'))ctx.drawImage(layer,0,0,1080,1920);
 return points.map(([x,y])=>[...ctx.getImageData(x,y,1,1).data]);
};
window.mount();
</script></body></html>`;
  server = await createServer({ server: { host: "localhost", port: 0, open: false }, plugins: [{
    name: "avatar-preview-test",
    configureServer(vite) {
      vite.middlewares.use("/__avatar_workspace", async (_req, res, next) => {
        try { res.setHeader("Content-Type", "text/html; charset=utf-8"); res.end(await vite.transformIndexHtml("/__avatar_workspace", workspaceHtml)); }
        catch (error) { next(error); }
      });
      vite.middlewares.use("/__avatar_preview", async (_req, res, next) => {
        try { res.setHeader("Content-Type", "text/html"); res.end(await vite.transformIndexHtml("/__avatar_preview", html)); }
        catch (error) { next(error); }
      });
    },
  }] });
  await server.listen();
  browser = await chromium.launch({ ...(process.env.IMV_TEST_CHROME ? { executablePath: process.env.IMV_TEST_CHROME } : { channel: "chrome" }), headless: true, args: ["--enable-unsafe-swiftshader"] });
  page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  page.setDefaultTimeout(60000);
  page.on("request", (request) => { if (/preview-circle-mask|tfmodel|tensorflowLoader/.test(request.url())) evidence.requests.push({ url: request.url(), method: request.method() }); });
  page.on("pageerror", (error) => { evidence.errors.push(error.message); console.error(error.message); });
  page.on("console", (message) => { if (message.type() === "error") console.error(message.text().slice(0, 400)); });
  await page.goto(`http://localhost:${server.httpServer.address().port}/__avatar_preview`);
  await page.getByText("预览已就绪，点击播放查看效果", { exact: true }).waitFor();
  await page.waitForFunction(() => window.pixels([[420,960]])[0][0] > 200);
  evidence.pixels.opening = await page.evaluate(() => window.pixels([[10,960],[420,960],[600,960],[900,960]]));
  assert(evidence.pixels.opening[0][0] < 10 && evidence.pixels.opening[3][1] < 10, "圆形开场外应为黑色");
  assert(evidence.pixels.opening[1][0] > 200 && evidence.pixels.opening[2][1] > 80, "圆内保留原视频");
  await page.screenshot({ path: resolve(cache, "opening.png") });
  await page.evaluate(() => window.preview({ previewCenter: { x: 25, y: 50 } }));
  await page.waitForFunction(() => window.pixels([[270,960],[540,960]])[0][0] > 200 && window.pixels([[540,960]])[0][1] < 10);
  await page.evaluate(() => window.mount({ avatar_effects: { ...window.effects, feather: 0.2 } }));
  await page.waitForFunction(() => { const value=window.pixels([[465,960]])[0][0];return value > 15 && value < 140; });
  evidence.pixels.feather = await page.evaluate(() => window.pixels([[270,960],[420,960],[465,960],[490,960]]));
  await page.screenshot({ path: resolve(cache, "feather.png") });
  // 精确开场边界恢复原素材，不留下额外一帧黑底。
  await page.getByLabel("预览进度").evaluate((element) => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(element, "2");
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await page.waitForFunction(() => window.pixels([[900,960]])[0][1] > 80);
  evidence.pixels.openingEnd = await page.evaluate(() => window.pixels([[100,960],[900,960]]));
  await page.evaluate(() => window.mount({ avatar_effects: { ...window.effects, openingEnabled: false }, split_screen: window.layout }));
  await page.waitForFunction(() => { const p=window.pixels([[540,1500],[370,1500]]);return p[0][0]>200 && p[1][2]>200; });
  await page.evaluate(() => window.preview({ previewCenter: { x: 75, y: 50 } }));
  await page.waitForFunction(() => { const p=window.pixels([[540,1500]])[0];return p[1]>80 && p[0]<20; });
  await page.screenshot({ path: resolve(cache, "avatar-circle.png") });
  // 内置人物示例完全在本地分割；以绿色草地变成红绿下层、脸部仍可见来验证模型真实生效。
  await page.evaluate(() => window.preview({ avatarSource: 2, previewCenter: { x: 50, y: 50 } }));
  await page.evaluate(() => window.mount({ avatar_effects: { ...window.effects, openingEnabled: false, removeBackground: true }, split_screen: { ...window.layout, layout: 'overlay', avatar: { ...window.layout.avatar, x:25,y:10,width:50,height:80 } } }));
  await page.getByText("预览已就绪，点击播放查看效果", { exact: true }).waitFor();
  await page.waitForFunction(() => { const p=window.pixels([[300,650],[540,900]]);return p[0][0]>200 && p[1][0]>80; });
  await page.getByRole("button", { name: "播放", exact: true }).click();
  await page.waitForFunction(() => Number(document.querySelector('input[aria-label="预览进度"]').value) > 0.5);
  await page.getByRole("button", { name: "暂停", exact: true }).click();
  await page.getByLabel("预览进度").evaluate((element) => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(element, "0.5");
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await page.waitForFunction(() => { const face=window.pixels([[540,900]])[0];return face[0]>80 && face[1]>40 && face[1]<190; });
  evidence.pixels.matting = await page.evaluate(() => window.pixels([[300,650],[540,900],[780,650]]));
  await page.screenshot({ path: resolve(cache, "matting.png") });
  console.log("抠像采样", evidence.pixels.matting);
  assert(evidence.requests.some((request) => request.url.includes("tfmodel")), "SDK 应加载真实分割模型");
  assert(evidence.pixels.matting[0][0] > 200 && evidence.pixels.matting[0][1] < 30, "人物左侧草地应被移除，透出下层素材");
  assert(evidence.pixels.matting[1][0] > 80, "人脸应保留");
  assert(!evidence.errors.length, evidence.errors.join("\n"));
  for (const request of evidence.requests.filter((item) => item.url.includes("preview-circle-mask"))) {
    assert.equal(request.method, "GET");
    assert.deepEqual([...new URL(request.url).searchParams.keys()].sort(), ["feather", "height", "radius", "seconds", "width", "x", "y"], "几何资源请求不得携带视频地址或帧");
  }
  // 真实模型下载失败也不能留下黑窗并声称抠像成功；重新创建 SDK 以清掉已加载的模型。
  await page.route("**/wasm/starry/model/**", (route) => route.fulfill({ status: 503, body: "model unavailable" }));
  await page.getByRole("button", { name: "刷新预览", exact: true }).click();
  await page.getByText("SDK 抠像预览不可用，当前保留原视频背景。", { exact: true }).waitFor();
  await page.getByLabel("数字人同屏预览").waitFor();
  await page.screenshot({ path: resolve(cache, "matting-unavailable.png") });
  // 同一个基础播放器继续预览圆形，实际截图验证 CSS 蒙版，不能只断言 DOM 样式字符串。
  await page.evaluate(() => window.preview({ avatarSource: 0, previewCenter: { x: 50, y: 50 } }));
  await page.evaluate(() => window.mount({ avatar_effects: { ...window.effects, feather: 0.2 }, split_screen: window.layout }));
  await page.evaluate(() => window.preview({ previewCenter: { x: 25, y: 50 } }));
  await page.waitForFunction(() => document.querySelector('video[aria-label="圆形开场预览"]').readyState >= 2);
  await page.getByTestId("circle-opening-preview").screenshot({ path: resolve(cache, "basic-circle.png") });
  const basic = spawnSync(process.env.IMV_TEST_PYTHON || resolve("../server/.venv/bin/python"), ["-c", "import sys,json;from PIL import Image;im=Image.open(sys.argv[1]);w,h=im.size;print(json.dumps([im.getpixel((int(w*x),h//2))[:3] for x in [.25,.431,.9]]))", resolve(cache, "basic-circle.png")], { encoding: "utf8" });
  assert.equal(basic.status, 0, basic.stderr);
  evidence.pixels.basic = JSON.parse(basic.stdout);
  assert(evidence.pixels.basic[0][0] > 200 && evidence.pixels.basic[1][0] > 15 && evidence.pixels.basic[1][0] < 140 && evidence.pixels.basic[2][0] < 10, "基础播放器应显示相同的圆内原视频、软边与黑底");
  evidence.expectedFailureErrors = evidence.errors.splice(0);
  // 完整工作区使用实际 UI 操作；六组截图同时覆盖入口、参数归属及失败后真正恢复抠像。
  await page.unroute("**/wasm/starry/model/**");
  await page.setViewportSize({ width: 1680, height: 1100 });
  const writes = [];
  await page.route(`http://localhost:${port}/**`, (route) => {
    if (/\/template\/preview-(circle-)?mask\?/.test(route.request().url())) return route.continue();
    if (route.request().method() !== "GET") writes.push(route.request().url());
    return route.fulfill({ json: { sprites: [], fonts: [] } });
  });
  console.log("工作区：开始六组截图");
  await page.goto(`http://localhost:${server.httpServer.address().port}/__avatar_workspace`);
  await page.getByText("预览已就绪，点击播放查看效果", { exact: true }).waitFor();
  await page.evaluate(() => {
    window.pixels = (points) => {
      const frame = document.querySelector('iframe[title="模板预览播放器"]');
      const canvas = document.createElement("canvas"); canvas.width = 1080; canvas.height = 1920;
      const ctx = canvas.getContext("2d");
      for (const layer of frame.contentDocument.querySelectorAll("canvas")) ctx.drawImage(layer, 0, 0, 1080, 1920);
      return points.map(([x,y]) => [...ctx.getImageData(x,y,1,1).data]);
    };
  });
  const inspector = page.locator(".template-workspace-inspector");
  const card = page.getByRole("button", { name: "添加圆形蒙版" });
  await page.getByRole("tab", { name: "蒙版", exact: true }).click();
  assert.equal(await page.getByLabel("圆形蒙版设置").count(), 0);
  assert((await card.innerText()).includes("点击添加，开场显示"));
  assert(!(await card.innerText()).includes("打开设置"));
  await page.screenshot({ path: resolve(cache, "ui-02-mask-entry.png") });
  await card.click();
  assert.equal(await inspector.getByRole("button", { name: "编辑圆形蒙版" }).count(), 1);
  await page.getByLabel("时间轴轨道标签").getByRole("button", { name: "编辑圆形蒙版" }).waitFor();
  assert.equal(await page.getByLabel("启用圆形开场", { exact: true }).count(), 0);
  assert.equal(await page.locator(".template-workspace-canvas").getByLabel("数字人预览来源").count(), 0);
  await inspector.getByLabel("数字人预览来源").selectOption("2");
  await inspector.getByLabel("头部圆形留量（倍）").fill("2.4");
  await inspector.getByLabel("圆边羽化", { exact: true }).fill("0");
  await inspector.getByLabel("预览取景 Y（%）").fill("42");
  await page.waitForFunction(() => { const p = window.pixels([[540,806],[80,900]]); return p[0][0]>70 && p[1][0]<10; });
  await page.getByText("预览已就绪，点击播放查看效果", { exact: true }).waitFor();
  await inspector.evaluate((el) => { el.scrollTop = 0; });
  await page.screenshot({ path: resolve(cache, "ui-01-three-columns.png") });
  await page.screenshot({ path: resolve(cache, "ui-03-opening.png") });
  console.log("工作区：开场就绪");
  const hardEdge = await page.evaluate(() => window.pixels([[828,806]])[0]);
  await inspector.getByLabel("圆边羽化", { exact: true }).fill("0.2");
  await page.waitForFunction((edge) => { const p=window.pixels([[828,806],[540,806]]);return p[0][0]>10 && p[0][0]<edge[0]*.7 && p[1][0]>70; }, hardEdge);
  await page.getByText("预览已就绪，点击播放查看效果", { exact: true }).waitFor();
  evidence.pixels.workspaceFeather = { hard: hardEdge, soft: await page.evaluate(() => window.pixels([[828,806]])[0]) };
  await page.screenshot({ path: resolve(cache, "ui-03-feather.png") });
  // 从时间轴重选仍保留参数，移除对象同时移除轨道与设置。
  await page.getByRole("button", { name: "关闭圆形蒙版设置" }).click();
  assert.equal(await page.getByLabel("预览取景 X（%）").count(), 0);
  await page.getByLabel("时间轴轨道标签").getByRole("button", { name: "编辑圆形蒙版" }).click();
  assert.equal(await inspector.getByLabel("圆边羽化", { exact: true }).inputValue(), "0.2");
  await inspector.getByRole("button", { name: "移除当前画面对象" }).click();
  assert.equal(await page.getByRole("button", { name: "编辑圆形蒙版" }).count(), 0);
  assert.equal(await page.getByLabel("圆形蒙版设置").count(), 0);
  await inspector.getByText("素材与数字人同屏", { exact: true }).click();
  await inspector.getByLabel("启用同屏", { exact: true }).check();
  await inspector.getByLabel("同屏布局", { exact: true }).selectOption("circle");
  await inspector.getByLabel("数字人X %", { exact: true }).fill("60");
  await inspector.getByLabel("数字人Y %", { exact: true }).fill("60");
  await inspector.getByLabel("数字人宽度 %", { exact: true }).fill("36");
  await inspector.getByLabel("数字人高度 %", { exact: true }).fill("30");
  await inspector.getByLabel("圆边羽化", { exact: true }).fill("0.04");
  await page.waitForFunction(() => { const p=window.pixels([[842,1440],[100,900]]);return p[0][0]>70 && p[1][2]>200; });
  await page.getByText("预览已就绪，点击播放查看效果", { exact: true }).waitFor();
  assert.equal(await inspector.getByRole("button", { name: "编辑圆形蒙版" }).count(), 0);
  await inspector.evaluate((el) => { el.scrollTop = 150; });
  await page.screenshot({ path: resolve(cache, "ui-04-avatar-placement.png") });
  console.log("工作区：头像就绪");
  // 加回开场后再移除，同屏头像依旧显示；对象、轨道和设置一起移除。
  await card.click();
  await page.waitForFunction(() => window.pixels([[100,900]])[0][2]<10 && window.pixels([[540,806]])[0][0]>70);
  await inspector.getByRole("button", { name: "移除当前画面对象" }).click();
  assert.equal(await page.getByRole("button", { name: "编辑圆形蒙版" }).count(), 0);
  assert.equal(await inspector.getByLabel("同屏布局", { exact: true }).inputValue(), "circle");
  assert.equal(await inspector.getByLabel("预览取景 Y（%）").inputValue(), "42");
  await inspector.evaluate((el) => { el.scrollTop = 0; });
  await page.waitForFunction(() => { const p=window.pixels([[842,1440],[100,900]]);return p[0][0]>70 && p[1][2]>200; });
  await page.getByText("预览已就绪，点击播放查看效果", { exact: true }).waitFor();
  await page.screenshot({ path: resolve(cache, "ui-04-placement.png") });
  await inspector.getByLabel("同屏布局", { exact: true }).selectOption("overlay");
  await inspector.getByLabel("数字人X %", { exact: true }).fill("25");
  await inspector.getByLabel("数字人Y %", { exact: true }).fill("10");
  await inspector.getByLabel("数字人宽度 %", { exact: true }).fill("50");
  await inspector.getByLabel("数字人高度 %", { exact: true }).fill("80");
  await page.waitForFunction(() => { const p=window.pixels([[300,650],[540,900]]);return p[0][2]>200 && p[0][0]<20 && p[1][0]>80; });
  await page.getByText("预览已就绪，点击播放查看效果", { exact: true }).waitFor();
  await inspector.evaluate((el) => { el.scrollTop = 0; });
  await page.screenshot({ path: resolve(cache, "ui-05-matting.png") });
  console.log("工作区：抠像就绪");
  await page.route("**/wasm/starry/model/**", (route) => route.fulfill({ status: 503, body: "model unavailable" }));
  await page.getByRole("button", { name: "刷新预览", exact: true }).click();
  await page.getByText("SDK 抠像预览不可用，当前保留原视频背景。", { exact: true }).waitFor();
  await page.waitForFunction(() => document.querySelector('video[aria-label="数字人同屏预览"]')?.readyState >= 2);
  await page.screenshot({ path: resolve(cache, "ui-06-failure.png") });
  console.log("工作区：失败提示就绪");
  await page.unroute("**/wasm/starry/model/**");
  await page.getByRole("button", { name: "重试 IMS 预览", exact: true }).click();
  await page.waitForFunction(() => { const p=window.pixels([[300,650],[540,900]]);return p[0][2]>200 && p[0][0]<20 && p[1][0]>80; });
  await page.getByText("预览已就绪，点击播放查看效果", { exact: true }).waitFor();
  assert.equal(await page.getByText("SDK 抠像预览不可用，当前保留原视频背景。", { exact: true }).count(), 0);
  await page.screenshot({ path: resolve(cache, "ui-06-retry-success.png") });
  assert.deepEqual(writes, [], "预览操作不得发送模板保存或合成请求");
  evidence.workspace = { screenshotGroups: 6, writes, retryRestored: true, openingTrackSelectable: true, removalPreservesAvatar: true };
  evidence.expectedFailureErrors.push(...evidence.errors.splice(0));
  evidence.completed = true;
  console.log("通过：真实 SDK 开场、手动取景、羽化、圆形头像裁剪、人物抠像与模型下载失败降级。");
} catch (error) {
  if (page) { console.error((await page.locator("body").innerText()).slice(0, 1500)); await page.screenshot({ path: resolve(cache, "failed.png") }); }
  throw error;
} finally {
  writeFileSync(resolve(cache, "evidence.json"), JSON.stringify(evidence, null, 2));
  await browser?.close();
  await server?.close();
  backend.kill();
}
