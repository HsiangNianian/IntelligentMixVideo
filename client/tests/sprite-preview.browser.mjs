/**
 * Sprite 覆盖的浏览器回归：挂载真实封面/叠加组件，模拟预览端点与消息协议，验证同 ID 新版本会导航并重新发送摆放。
 * 执行 bun tests/sprite-preview.browser.mjs；需要 Chrome，可用 IMV_CHROME_PATH 指定。独立 Vite 与浏览器结束时关闭，
 * API 全部拦截，不访问业务后端；不验证 Remotion 渲染或视频解码。
 */
import assert from "node:assert/strict";
import { chromium } from "playwright-core";
import { createServer } from "vite";

const html = `<!doctype html><html><body><div id="root"></div><script type="module">
import React from 'react';
import {createRoot} from 'react-dom/client';
import {SpriteCover} from '/src/features/templates/SpriteCover.tsx';
import {SpriteOverlay} from '/src/features/templates/SpriteOverlay.tsx';
import '/src/styles/globals.css';
const root=createRoot(document.getElementById('root'));
const overlay=React.createRef();
window.show=(sourceVersionId,name='测试资产')=>{
  const asset={id:'review-sprite',sourceVersionId,name,kind:1,seconds:3,aspect:9/16,width:1080,height:1920,fps:30,box:{x:0,y:0,width:1080,height:1920},parameters:[]};
  const clip={id:'placed',spriteId:asset.id,sourceVersionId,name,aspect:asset.aspect,start:1,end:4,length:5,values:{fontSize:200},transform:{x:300}};
  root.render(React.createElement('main',{style:{display:'flex',gap:30}},
    React.createElement('div',{style:{width:260}},React.createElement(SpriteCover,{asset,active:false})),
    React.createElement('div',{style:{position:'relative',width:360,height:640}},React.createElement(SpriteOverlay,{ref:overlay,clips:[clip],stageAspect:9/16,initialTime:2}))));
};
window.seek=time=>overlay.current.setTime(time);
window.show('v1');
</script></body></html>`;

const server = await createServer({ server: { host: "127.0.0.1", port: 0, open: false }, plugins: [{
  name: "sprite-preview-test-page",
  configureServer(vite) {
    vite.middlewares.use("/__sprite_preview_test", async (_req, res, next) => {
      try {
        res.setHeader("Content-Type", "text/html; charset=utf-8");
        res.end(await vite.transformIndexHtml("/__sprite_preview_test", html));
      } catch (error) { next(error); }
    });
  },
}] });
let browser;
try {
  await server.listen();
  browser = await chromium.launch({ ...(process.env.IMV_CHROME_PATH ? { executablePath: process.env.IMV_CHROME_PATH } : { channel: "chrome" }), headless: true });
  const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
  page.setDefaultTimeout(15_000);
  const errors = [];
  const requests = [];
  let version = "V1";
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/**", (route) => route.fulfill({ status: 404 }));
  await page.route("**/api/sprites/review-sprite/preview**", (route) => {
    requests.push(route.request().url());
    return route.fulfill({ contentType: "text/html; charset=utf-8", body: `<p id="version">${version}</p><p id="values"></p><p id="time"></p><script>
      const channel=location.hash.slice(1);
      addEventListener('message',({data,source})=>{
        if(source!==parent||data.channel!==channel)return;
        if(data.type==='imv-preview-update')document.getElementById('values').textContent=JSON.stringify([data.values,data.host.transform]);
        if(data.type==='imv-preview-sync')document.getElementById('time').textContent=String(data.time);
      });
      setTimeout(()=>parent.postMessage({type:'imv-preview-ready',channel,sync:true,host:true},'*'),50);
    </script>` });
  });
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/__sprite_preview_test`);
  /** 两个实际 iframe 都已执行该版本脚本，叠加已收到原摆放参数与当前时间。 */
  async function expectVersion(name, expected) {
    for (const kind of ["封面", "预览"]) {
      const frame = page.frameLocator(`iframe[title="AI 资产${kind}：${name}"]`);
      await frame.locator("#version").filter({ hasText: expected }).waitFor();
    }
    const overlay = page.frameLocator(`iframe[title="AI 资产预览：${name}"]`);
    await overlay.locator("#values").filter({ hasText: '[{"fontSize":200},{"x":300}]' }).waitFor();
    await overlay.locator("#time").filter({ hasText: /^1$/ }).waitFor();
  }
  await expectVersion("测试资产", "V1");
  assert.equal(requests.length, 2);
  const firstSources = await page.locator("iframe").evaluateAll((frames) => frames.map((frame) => frame.src));
  await page.evaluate(() => window.show("v1", "改名资产"));
  await expectVersion("改名资产", "V1");
  assert.deepEqual(await page.locator("iframe").evaluateAll((frames) => frames.map((frame) => frame.src)), firstSources);
  assert.equal(requests.length, 2, "改名不重新请求预览");
  version = "V2";
  await page.evaluate(() => window.show("v2", "改名资产"));
  await expectVersion("改名资产", "V2");
  assert.equal(requests.length, 4, "覆盖后封面和叠加各重新请求一次");
  assert.ok(requests.slice(2).every((url) => new URL(url).searchParams.get("version") === "v2"));
  await page.evaluate(() => window.seek(2.5));
  await page.frameLocator('iframe[title="AI 资产预览：改名资产"]').locator("#time").filter({ hasText: /^1\.5$/ }).waitFor();
  assert.deepEqual(errors, []);
  console.log("PASS: 同 ID 覆盖后封面与叠加自动加载 V2，重发摆放和时间；改名不重载，无页面异常。");
} finally {
  await browser?.close();
  await server.close();
}
