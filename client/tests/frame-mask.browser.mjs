/** 真实 Canvas / IMS SDK 回归；执行 bun tests/frame-mask.browser.mjs，需要 Chrome、FFmpeg 和 SDK 网络。 */
import assert from "node:assert/strict";
import { mkdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { chromium } from "playwright-core";
import { createServer } from "vite";

const cache = resolve("node_modules/.cache/frame-mask-tests");
mkdirSync(cache, { recursive: true });
const video = spawnSync("ffmpeg", ["-y", "-v", "error", "-f", "lavfi", "-i", "color=c=green:size=180x320:rate=30",
  "-t", "10", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", resolve(cache, "video.mp4")], { encoding: "utf8" });
assert.equal(video.status, 0, video.error?.message || video.stderr);
const { geometry: cases, gradient: gradientCases } = JSON.parse(readFileSync("../server/tests/frame_mask_cases.json", "utf8"));
const html = `<!doctype html><html><body><div id="root"></div><script type="module">
import React from 'react';
import {createRoot} from 'react-dom/client';
import {TemplatePreview} from '/src/features/templates/TemplatePreview.tsx';
import {FrameMaskPanel} from '/src/features/templates/FrameMaskPanel.tsx';
import {newDraft} from '/src/features/templates/model.ts';
import {newFrameMask,frameMaskImage} from '/src/features/templates/frameMask.ts';
import {previewContext,resolveTemplate} from '/src/features/templates/tracks.ts';
import '/src/styles/globals.css';
import '/src/features/templates/template-workspace.css';
const root=createRoot(document.getElementById('root')), draft=newDraft();
draft.tracks[0].editor.title='蒙版上的标题';
draft.tracks[0].editor.titleY=10;
const media=Array.from({length:3},()=>({url:location.origin+'/node_modules/.cache/frame-mask-tests/video.mp4',duration:10,width:180,height:320}));
const resolved=resolveTemplate(draft,previewContext(media));
window.mount=(mask)=>root.render(React.createElement('div',{className:'template-workspace flex'},React.createElement('div',{className:'flex-1'},React.createElement(TemplatePreview,{
 draft:{...draft,frame_mask:mask},media,resolved,videoInputKey:'mask',onMediaChange:()=>{},onCatalog:()=>{}
})),mask&&React.createElement('aside',{className:'w-80'},React.createElement(FrameMaskPanel,{mask,onChange:window.mount,onRemove:()=>window.mount(undefined),onClose:()=>{}}))));
window.mask=newFrameMask();
// 服务端已完成 9:16 裁剪；真实 Canvas 验证整张图拉伸与半透明像素，不叠旧颜色。
window.checkImagePixels=async()=>{
 const source=document.createElement('canvas');source.width=90;source.height=160;
 const paint=source.getContext('2d');paint.fillStyle='rgba(255,0,0,0.5)';paint.fillRect(0,0,90,80);
 paint.fillStyle='#0000ff';paint.fillRect(0,80,90,80);
 const data=source.toDataURL('image/png'),image=new Image();image.src=data;await image.decode();
 const bar={enabled:true,height:20,color:'#00ff00',imageId:'a'.repeat(64)};
 const result=new Image();result.src=frameMaskImage({top:bar,bottom:{...bar,height:30}},90,160,new Map([[bar.imageId,image]]));await result.decode();
 const output=document.createElement('canvas');output.width=90;output.height=160;const ctx=output.getContext('2d');ctx.drawImage(result,0,0);
 for(const [x,y,expected] of [[0,2,[255,0,0,128]],[89,10,[255,0,0,128]],[45,31,[0,0,255,255]],[45,32,[0,0,0,0]],[45,111,[0,0,0,0]],[45,120,[255,0,0,128]],[45,159,[0,0,255,255]]]){
  if(String(ctx.getImageData(x,y,1,1).data)!==String(expected))throw Error('图片像素不一致：'+x+','+y);
 }
 return data;
};
window.checkPixels=async(cases)=>{
 for(const item of cases){
  const mask={top:{enabled:true,height:item.top,color:'#112233'},bottom:{enabled:true,height:item.bottom,color:'#445566'}};
  const url=frameMaskImage(mask,80,item.height);
  if(!item.expected.length){if(url!==undefined)throw Error('零高度应省略');continue;}
  const image=new Image();image.src=url;await image.decode();
  const canvas=document.createElement('canvas');canvas.width=80;canvas.height=item.height;
  const ctx=canvas.getContext('2d');ctx.drawImage(image,0,0);
  for(let y=0;y<item.height;y++){
   let expected=[0,0,0,0];
   for(const [start,size] of item.expected)if(y>=start&&y<start+size)expected=start===0?[17,34,51,255]:[68,85,102,255];
   for(const x of [0,79])if(String(ctx.getImageData(x,y,1,1).data)!==String(expected))throw Error('像素不一致：'+item.height+':'+x+','+y);
  }
 }
};
window.checkGradientPixels=async(cases)=>{
 for(const item of cases){
  const bar={enabled:true,height:item.barHeight,color:item.color,gradient:item.gradient};
  const url=frameMaskImage({top:bar,bottom:bar},80,item.height);
  if(!item.expected.length){if(url!==undefined)throw Error('零高度应省略');continue;}
  const image=new Image();image.src=url;await image.decode();
  const canvas=document.createElement('canvas');canvas.width=80;canvas.height=item.height;
  const ctx=canvas.getContext('2d');ctx.drawImage(image,0,0);
  for(let y=0;y<item.height;y++){
   const size=item.expected.length,row=y<size?y:y-(item.height-size);
   const expected=item.expected[row]??[0,0,0,0];
   for(const x of [0,79])if(String(ctx.getImageData(x,y,1,1).data)!==String(expected))throw Error('渐变像素不一致：'+item.name+':'+x+','+y);
  }
 }
};
window.pixels=(rows=[10,352,353,1000,1600,1601,1910])=>{
 const frame=document.querySelector('iframe[title="模板预览播放器"]');
 const canvases=[...frame.contentDocument.querySelectorAll('canvas')];
 const canvas=document.createElement('canvas');canvas.width=1080;canvas.height=1920;
 const ctx=canvas.getContext('2d');
 for(const layer of canvases)ctx.drawImage(layer,0,0,1080,1920);
 // 检查精确栏高边界及原视频内部；原视频已有的边缘缩放不属于蒙版，四角满铺另由 edgePixels 检查。
 return rows.map(y=>[...ctx.getImageData(10,y,1,1).data]);
};
window.edgePixels=()=>{
 const doc=document.querySelector('iframe[title="模板预览播放器"]').contentDocument;
 const canvas=document.createElement('canvas');canvas.width=1080;canvas.height=1920;
 const ctx=canvas.getContext('2d');
 for(const layer of doc.querySelectorAll('canvas'))ctx.drawImage(layer,0,0,1080,1920);
 return [[0,0],[1079,0],[0,1919],[1079,1919]].map(([x,y])=>[...ctx.getImageData(x,y,1,1).data]);
};
</script></body></html>`;
const server = await createServer({ server: { host: "localhost", port: 0, open: false }, plugins: [{
  name: "frame-mask-test",
  configureServer(vite) {
    vite.middlewares.use("/__frame_mask", async (_req, res, next) => {
      try { res.setHeader("Content-Type", "text/html"); res.end(await vite.transformIndexHtml("/__frame_mask", html)); }
      catch (error) { next(error); }
    });
  },
}] });
let browser;
let page;
try {
  await server.listen();
  browser = await chromium.launch({
    ...(process.env.IMV_TEST_CHROME ? { executablePath: process.env.IMV_TEST_CHROME } : { channel: "chrome" }),
    headless: true, args: ["--enable-unsafe-swiftshader"],
  });
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.on("pageerror", (error) => console.error(error.message));
  page.on("console", (message) => { if (message.type() === "error") console.error(message.text().slice(0, 400)); });
  page.setDefaultTimeout(60000);
  await page.goto(`http://localhost:${server.httpServer.address().port}/__frame_mask`);
  await page.waitForFunction(() => window.mount);
  await page.evaluate((cases) => window.checkPixels(cases), cases);
  await page.evaluate((cases) => window.checkGradientPixels(cases), gradientCases);
  const imageData = await page.evaluate(() => window.checkImagePixels());
  const imageBytes = Buffer.from(imageData.split(",")[1], "base64");
  // 只替换图片存储 HTTP；上传面板、读取解码、Canvas 和 IMS SDK 都执行真实逻辑。
  await page.route("**/template/frame-mask-images", route => route.fulfill({ status: 201, contentType: "application/json",
    headers: { "Access-Control-Allow-Origin": "*" }, body: JSON.stringify({ imageId: "a".repeat(64) }) }));
  await page.route("**/template/frame-mask-images/*", route => route.fulfill(route.request().url().endsWith("a".repeat(64))
    ? { status: 200, contentType: "image/png", headers: { "Access-Control-Allow-Origin": "*" }, body: imageBytes }
    : { status: 404, contentType: "application/json", headers: { "Access-Control-Allow-Origin": "*" }, body: JSON.stringify({ detail: "蒙版图片不存在" }) }));
  await page.evaluate(() => window.mount(window.mask));
  await page.getByText("预览已就绪，点击播放查看效果", { exact: true }).waitFor();
  await page.getByRole("button", { name: "播放", exact: true }).click();
  await page.waitForFunction(() => Number(document.querySelector('input[aria-label="预览进度"]').value) >= 0.5);
  await page.waitForFunction(() => String(window.pixels()[0]) === "14,44,66,255" && window.pixels()[3][1] > 50, undefined, { polling: 250 });
  await page.getByRole("button", { name: "暂停", exact: true }).click();
  const pixels = await page.evaluate(() => window.pixels());
  for (const index of [0,1,5,6]) assert.deepEqual(pixels[index], [14,44,66,255]);
  const edges = await page.evaluate(() => window.edgePixels());
  console.log("四角像素", edges);
  for (const pixel of edges) assert.deepEqual(pixel, [14,44,66,255], "蒙版应覆盖四角，不能留下透明或黑边");
  for (const index of [2,3,4]) assert(pixels[index][1] > pixels[index][0] + 50, "中部应透出绿色视频");
  await page.screenshot({ path: resolve(cache, "enabled.png") });
  // 保持素材和时间对象不变，只改蒙版；无需刷新整个播放器。
  await page.evaluate(() => window.mount({ ...window.mask, top: { ...window.mask.top, color: "#aa2244" } }));
  await page.waitForFunction(() => String(window.pixels()[0]) === "170,34,68,255");
  // 从真实参数面板开启反向渐变，颜色、方向与范围改动均暂停回零后自动刷新。
  await page.getByRole("radio", { name: "上栏渐变", exact: true }).click();
  await page.getByRole("radio", { name: "下栏渐变", exact: true }).click();
  await page.waitForFunction(() => String(window.pixels([0,352,1601,1919])) === "7,16,31,255,170,34,68,255,14,44,66,255,7,16,31,255");
  await page.getByRole("button", { name: "播放", exact: true }).click();
  await page.waitForFunction(() => Number(document.querySelector('input[aria-label="预览进度"]').value) >= 1);
  await page.getByLabel("上栏渐变范围", { exact: true }).fill("50");
  await page.waitForFunction(() => Number(document.querySelector('input[aria-label="预览进度"]').value) === 0);
  assert.equal(await page.getByRole("button", { name: "暂停", exact: true }).count(), 0);
  await page.waitForFunction(() => String(window.pixels([0,176,264,352])) === "7,16,31,255,7,16,31,255,89,25,50,255,170,34,68,255");
  await page.getByRole("radio", { name: "上栏上到下", exact: true }).click();
  await page.waitForFunction(() => String(window.pixels([0,88,176,352])) === "170,34,68,255,89,25,50,255,7,16,31,255,7,16,31,255");
  await page.screenshot({ path: resolve(cache, "gradient.png") });
  await page.getByRole("radio", { name: "上栏纯色", exact: true }).click();
  assert.equal(await page.getByLabel("上栏结束色", { exact: true }).count(), 0);
  await page.waitForFunction(() => window.pixels([0,176,352]).every(pixel => String(pixel) === "170,34,68,255"));
  await page.getByLabel("上传上栏图片", { exact: true }).setInputFiles({ name: "mask.png", mimeType: "image/png", buffer: imageBytes });
  await page.getByRole("img", { name: "上栏图片缩略图" }).waitFor();
  await page.waitForFunction(() => String(window.pixels([300])[0]) === "0,0,255,255");
  await page.getByLabel("上栏高度", { exact: true }).fill("30");
  await page.waitForFunction(() => String(window.pixels([500])[0]) === "0,0,255,255");
  assert.equal(await page.getByLabel("上栏颜色", { exact: true }).count(), 0);
  await page.screenshot({ path: resolve(cache, "image.png") });
  await page.evaluate(() => window.mount({
    top: { ...window.mask.top, enabled: false }, bottom: { ...window.mask.bottom, enabled: false },
  }));
  await page.waitForFunction(() => window.pixels().every(pixel => pixel[1] > pixel[0] + 50));
  await page.screenshot({ path: resolve(cache, "disabled.png") });
  // 这里只验证预览撤除与时间轴移除；工作区点击删除、保存重读由 frame-mask.test.tsx 覆盖。
  // 重新启用再移除整个配置；视频恢复且时间轴不再保留蒙版行。
  await page.evaluate(() => window.mount(window.mask));
  await page.waitForFunction(() => String(window.pixels()[0]) === "14,44,66,255");
  assert.equal(await page.getByRole("button", { name: "编辑蒙版轨道", exact: true }).count(), 1);
  await page.evaluate(() => window.mount(undefined));
  await page.waitForFunction(() => window.pixels().every(pixel => pixel[1] > pixel[0] + 50));
  assert.equal(await page.getByRole("button", { name: "编辑蒙版轨道", exact: true }).count(), 0);
  await page.screenshot({ path: resolve(cache, "removed.png") });
  // SDK 不可用时，基础视频上的蒙版仍使用同一生成器和完整分辨率。
  await page.route("**/video-editing-websdk/**", route => route.abort());
  await page.goto(`http://localhost:${server.httpServer.address().port}/__frame_mask`);
  await page.waitForFunction(() => window.mount);
  await page.evaluate(() => window.mount({
    top: { ...window.mask.top, gradient: { enabled: true, endColor: "#07101F", direction: "up", range: 100 } },
    bottom: { ...window.mask.bottom, enabled: false },
  }));
  const fallback = page.getByAltText("上下蒙版预览", { exact: true });
  await fallback.waitFor();
  const fallbackPixels = await fallback.evaluate(async image => {
    await image.decode();
    const canvas = document.createElement("canvas"); canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
    const ctx = canvas.getContext("2d"); ctx.drawImage(image, 0, 0);
    return [0,176,352,353,1919].map(y => [...ctx.getImageData(0,y,1,1).data]);
  });
  assert.deepEqual(fallbackPixels, [[7,16,31,255],[11,30,49,255],[14,44,66,255],[0,0,0,0],[0,0,0,0]]);
  await page.screenshot({ path: resolve(cache, "fallback.png") });
  await page.evaluate(() => window.mount({ top: { ...window.mask.top, height: 20, imageId: 'a'.repeat(64) }, bottom: { ...window.mask.bottom, enabled: false } }));
  await page.waitForFunction(async () => {
    const image=document.querySelector('img[alt="上下蒙版预览"]');if(!image)return false;await image.decode();
    const canvas=document.createElement('canvas');canvas.width=1080;canvas.height=1920;const ctx=canvas.getContext('2d');ctx.drawImage(image,0,0);
    return String(ctx.getImageData(10,10,1,1).data)==='255,0,0,128' && String(ctx.getImageData(10,380,1,1).data)==='0,0,255,255';
  });
  await page.screenshot({ path: resolve(cache, "image-fallback.png") });
  await page.evaluate(() => window.mount({ top: { ...window.mask.top, imageId: 'b'.repeat(64) }, bottom: { ...window.mask.bottom, enabled: false } }));
  await page.getByRole("alert").filter({ hasText: "蒙版图片读取失败" }).waitFor();
  console.log("通过：纯色/渐变/图片 PNG 像素、上传与调高、真实 SDK、暂停回零、降级与缺图报错、透明中部、停用与移除。");
} catch (error) {
  if (page) {
    console.error((await page.locator("body").innerText()).slice(0, 1200));
    console.error("采样", await page.evaluate(() => window.pixels?.()));
    await page.screenshot({ path: resolve(cache, "failed.png") });
  }
  throw error;
} finally {
  await browser?.close();
  await server.close();
}
