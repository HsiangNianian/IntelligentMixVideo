/** bun tests/cover-frame.browser.mjs 使用真实 Remotion Player 延迟定位，核对 30 fps 封面 PNG 的像素。需要两端的 Bun 依赖与 Chrome。 */
import assert from "node:assert/strict";
import { mkdtemp, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { createServer } from "vite";
import { buildPresentation } from "../../server/src/server/remotion/presentation.mjs";

const renderer = fileURLToPath(new URL("../../server/src/server/remotion/", import.meta.url));
const work = await mkdtemp(join(tmpdir(), "imv-cover-frame-"));
const server = await createServer({ define: { "import.meta.env.VITE_API_URL": "window.location.origin" }, server: { host: "127.0.0.1", port: 0 } });
let browser;
try {
  // 第 0 帧是黑色，第 n 帧红色通道恰好为 n；不依赖字幕识别或动画的主观观感。
  await writeFile(join(work, "Template.tsx"), `import { AbsoluteFill, useCurrentFrame } from "remotion";
    export default function Probe() { const frame = useCurrentFrame(); return <AbsoluteFill data-frame={frame} style={{backgroundColor:"rgb("+frame+",0,0)"}}/>; }`);
  await symlink(join(renderer, "node_modules"), join(work, "node_modules"), "junction");
  process.env.IMV_RENDERER_ROOT = renderer;
  await buildPresentation(work, {}, { width: 180, height: 320, fps: 30, duration_in_frames: 90 });
  await server.listen();
  const base = `http://127.0.0.1:${server.httpServer.address().port}`;
  browser = await chromium.launch(process.env.IMV_CHROME_PATH ? { executablePath: process.env.IMV_CHROME_PATH } : { channel: "chrome" });
  const page = await browser.newPage();
  page.setDefaultTimeout(15000);
  await page.route(base + "/probe.js", route => route.fulfill({ path: join(work, "interactive.js"), contentType: "text/javascript" }));
  await page.route(base + "/api/sprites/fonts/*", route => route.fulfill({ path: "public/fonts/imv-hand.woff2", contentType: "font/woff2" }));
  await page.route(base + "/api/sprites/probe/preview**", route => route.fulfill({ contentType: "text/html", body: `
    <style>html,body,#root{height:100%;margin:0}</style><div id="root"></div><script>
    // 延迟所有非零定位，参数更新与首帧绘制照常送达。
    addEventListener("message", event => {
      if (!event.isTrusted || event.data?.type !== "imv-preview-sync" || !event.data.time) return;
      event.stopImmediatePropagation();
      setTimeout(() => dispatchEvent(new MessageEvent("message", {data:event.data, source:parent})), 900);
    }, true);
    </script><script src="/probe.js"></script>` }));
  // 从 HTML 入口导入真实组件，让 Vite 处理 React 依赖；只有 IMS 主时钟用可控的测试时间代替。
  const html = `<div id="stage" data-cover-ready="true" data-cover-time="0" data-cover-layers="1" data-cover-text-layers="0">
    <div id="frame" style="position:relative;width:180px;height:320px"></div></div><script type="module">
    import React from "react"; import {createRoot} from "react-dom/client";
    import {SpriteOverlay} from "/src/features/templates/SpriteOverlay.tsx";
    import {prepareCoverFrame} from "/src/features/templates/coverCapture.ts";
    import "/src/styles/globals.css";
    const root=createRoot(frame), ref=React.createRef();
    const start=Number(new URLSearchParams(location.search).get("start")), clip={id:"clip",spriteId:"probe",name:"帧号",aspect:9/16,start,end:3,length:3-start};
    stage.dataset.coverTime=String(start);
    root.render(React.createElement(SpriteOverlay,{ref,clips:[clip],stageAspect:9/16,initialTime:start}));
    window.capture=()=>prepareCoverFrame(frame, t=>{ref.current.setTime(t);stage.dataset.coverTime=String(t)});
    </script>`;
  await page.route(base + "/cover-probe.html?start=*", async route => route.fulfill({ contentType: "text/html", body: await server.transformIndexHtml("/cover-probe.html", html) }));
  for (const start of [0, 0.5]) {
    await page.goto(base + "/cover-probe.html?start=" + start);
    const film = page.frameLocator("#frame iframe").locator("[data-frame]");
    await film.waitFor({ state: "attached" });
    await page.waitForFunction(() => document.querySelector("#frame iframe")?.dataset.coverPainted === "true");
    assert.equal(await film.getAttribute("data-frame"), "0");
    await page.evaluate(() => window.capture());
    const expected = (1 - start) * 30;
    assert.equal(await film.getAttribute("data-frame"), String(expected));
    const png = await page.locator("#frame").screenshot();
    const pixel = await page.evaluate(async bytes => {
      const bitmap = await createImageBitmap(new Blob([new Uint8Array(bytes)], { type: "image/png" }));
      const ctx = new OffscreenCanvas(1, 1).getContext("2d");
      ctx.drawImage(bitmap, 90, 160, 1, 1, 0, 0, 1, 1);
      return [...ctx.getImageData(0, 0, 1, 1).data];
    }, [...png]);
    assert.deepEqual(pixel, [expected, 0, 0, 255]);
    console.log(`PASS: 延迟 900 ms 后，全局 1 秒对应片段帧 ${expected}，PNG 像素 ${pixel}`);
  }
} finally {
  await browser?.close();
  await server.close();
  await rm(work, { recursive: true, force: true });
}
