/** bun tests/backfill-template.browser.mjs：从主页首批补图到 PNG 上传；模拟桌面快照和慢绘制 SDK，不连接业务服务。 */
import assert from "node:assert/strict";
import { chromium } from "playwright-core";
import { createServer } from "vite";
import { jsonListResponse, jsonTemplateResponse, savedTemplate } from "./fixtures.ts";

// 颜色标记视频帧号、标题和字幕，只有发出定位回执后一段时间才能截到三者。
const sdk = `window.AliyunTimelinePlayer = class {
  static getSubtitleEffectColorStyles() {} static getSubtitleBubbles() {} static getVideoFilters() {}
  static getVideoEffects() {} static getVideoTransitions() {}
  listeners = new Set();
  event$ = { subscribe: fn => (this.listeners.add(fn), { unsubscribe: () => this.listeners.delete(fn) }) };
  emit(type) { for (const fn of this.listeners) fn({type, data:{currentTime:this.time}}); }
  constructor({container}) {
    this.canvas = document.createElement("canvas"); this.canvas.id = "subtitle-canvas";
    this.canvas.width = 60; this.canvas.height = 90; this.canvas.style = "width:100%;height:100%";
    container.append(this.canvas);
  }
  async setTimeline() { setTimeout(() => this.emit("canplay"), 30); }
  pause() {} destroy() {}
  get currentTime() { return this.time || 0; }
  set currentTime(t) {
    this.time = t; this.emit("playerSeeked");
    this.canvas.style.background = "rgb(" + Math.round(t * 30) + ",0,0)";
    if (t !== 1 || parent.holdText) return;
    const {width, height} = parent.document.querySelector(".template-preview-frame").getBoundingClientRect();
    parent.seekSize = [width, height];
    setTimeout(() => {
      const ctx = this.canvas.getContext("2d"), mark = document.createElement("canvas");
      mark.width = 60; mark.height = 30;
      const brush = mark.getContext("2d");
      ctx.clearRect(0,0,60,90);
      brush.fillStyle = "lime"; brush.fillRect(0,0,60,30); ctx.drawImage(mark,0,0);
      brush.fillStyle = "blue"; brush.fillRect(0,0,60,30); ctx.drawImage(mark,0,60);
    },350);
  }
}`;
const server = await createServer({ define: { "import.meta.env.VITE_API_URL": "window.location.origin" }, server: { host: "127.0.0.1", port: 0 } });
let browser;
try {
  await server.listen();
  const base = `http://127.0.0.1:${server.httpServer.address().port}`;
  browser = await chromium.launch(process.env.IMV_CHROME_PATH ? { executablePath: process.env.IMV_CHROME_PATH } : { channel: "chrome" });
  const page = await browser.newPage();
  page.on("pageerror", error => console.error(error.message));
  const client = await page.context().newCDPSession(page);
  const template = savedTemplate();
  let cover, captures = 0, reads = 0, platform;
  // 桌面 IPC 边界返回实际整页 PNG；取帧、画布裁剪及上传使用产品代码。
  await page.exposeFunction("snapshot", async documentSize => {
    captures++;
    assert.equal(Boolean(documentSize), platform !== "Macintosh");
    assert.deepEqual(await page.evaluate(() => {
      const {width, height} = document.querySelector(".template-preview-frame").getBoundingClientRect();
      return [width, height];
    }), await page.evaluate(() => window.seekSize));
    if (!documentSize) return (await client.send("Page.captureScreenshot", {})).data;
    const [width, height] = documentSize;
    return (await client.send("Page.captureScreenshot", { clip: { x: 0, y: 0, width, height, scale: 1 }, captureBeyondViewport: true })).data;
  });
  await page.addInitScript(() => {
    window.isTauri = true;
    const os = new URLSearchParams(location.search).get("os");
    Object.defineProperty(navigator, "userAgent", { value: os || navigator.userAgent });
    window.testFocus = value => {
      Object.defineProperty(document, "hasFocus", { configurable: true, value: () => value });
      window.dispatchEvent(new Event(value ? "focus" : "blur"));
    };
    if (os === "Linux") window.testFocus(false);
    window.__TAURI_INTERNALS__ = { invoke: async (cmd, args) => {
      if (cmd === "preview_media_base") return location.origin;
      if (cmd === "preview_snapshot") return Uint8Array.from(atob(await window.snapshot(args.documentSize ?? null)), ch => ch.charCodeAt(0)).buffer;
    } };
  });
  await page.route("**/player.js", route => route.fulfill({ contentType: "application/javascript", body: sdk }));
  await page.route("**/player.css", route => route.fulfill({ contentType: "text/css", body: "" }));
  await page.route(base + "/sample/*", route => route.fulfill({ path: "public/preview/sample-" + route.request().url().at(-1) + ".mp4", contentType: "video/mp4" }));
  await page.route(base + "/template", async route => route.fulfill({ json: await jsonListResponse([template]).json() }));
  const item = base + "/template/" + template.template_id;
  await page.route(item, async route => { reads++; await route.fulfill({ json: await jsonTemplateResponse(template, "get").json() }); });
  await page.route(item + "/cover", route => route.fulfill({ body: cover, contentType: "image/png" }));
  await page.route(item + "/cover/" + template.template_id, async route => {
    const request = route.request();
    const data = await new Response(request.postDataBuffer(), { headers: { "Content-Type": request.headers()["content-type"] } }).formData();
    assert.equal(data.get("refresh"), "true");
    cover = Buffer.from(await data.get("file").arrayBuffer());
    template.cover = { source: "builtin", revision: "published", publishedRevision: "published", url: item + "/cover" };
    await route.fulfill({ json: { url: template.cover.url, source: "builtin", revision: "published", published_revision: "published" } });
  });
  // 挂载产品主页，避免外壳的更新检查等无关桌面命令。
  const html = `<!doctype html><div id="root"></div><script type="module">
    import React from "react";
    import { createRoot } from "react-dom/client";
    import { Tooltip } from "radix-ui";
    import { Toaster } from "sonner";
    import { TemplateHome } from "/src/features/templates/TemplateHome.tsx";
    import "/src/styles/globals.css";
    import "/src/features/templates/template-workspace.css";
    createRoot(document.getElementById("root")).render(React.createElement(
      Tooltip.Provider, null, React.createElement(Toaster), React.createElement(TemplateHome)
    ));
  </script>`;
  await page.route(base + "/backfill.html*", async route => route.fulfill({contentType: "text/html", body: await server.transformIndexHtml("/backfill.html", html)}));
  for (platform of ["Linux", "Windows", "Macintosh"]) {
    delete template.cover;
    captures = reads = 0;
    const address = base + "/backfill.html?os=" + platform;
    await page.goto(address);
    if (platform === "Linux") {
      // Wayland 最小化可仍报告 visible。测试先恢复窗口，再在 IMS 位图返回前失焦。
      const host = page.locator(".home-cover-capture");
      await host.waitFor({ state:"attached" });
      assert.equal(await host.locator(".template-preview-frame").count(), 0);
      await page.evaluate(() => { window.holdText = true; window.testFocus(true); });
      await page.waitForFunction(() => document.querySelector("[data-cover-time='1']"));
      await page.evaluate(() => window.testFocus(false));
      await host.locator(".template-preview-frame").waitFor({state:"detached"});
      assert.equal(captures, 0);
      await page.evaluate(() => { window.holdText = false; window.testFocus(true); });
    }
    for (let visit = 0; visit < 2; visit++) {
      if (visit) await page.goto(address);
      await page.locator("article img[src^='blob:']").first().waitFor({timeout:20000});
      assert.equal(captures, 1);
      assert.equal(reads, 1);
    }
    const colors = await page.evaluate(async bytes => {
      const frame = await createImageBitmap(new Blob([new Uint8Array(bytes)], {type:"image/png"}));
      const ctx = new OffscreenCanvas(frame.width, frame.height).getContext("2d");
      ctx.drawImage(frame, 0, 0);
      return [1 / 6, 1 / 2, 5 / 6].map(y => [...ctx.getImageData(frame.width / 2, frame.height * y, 1, 1).data]);
    }, [...cover]);
    assert.deepEqual(colors, [[0, 255, 0, 255], [30, 0, 0, 255], [0, 0, 255, 255]]);
    console.log("PASS:", platform, "快照参数与裁剪，PNG 含第 30 帧、标题及字幕；再次打开复用");
  }
} finally {
  await browser?.close();
  await server.close();
}
