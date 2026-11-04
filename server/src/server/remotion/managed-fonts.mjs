/** Prefer SC glyphs before layout or encoding; a bad primary font switches once to the bundled Chinese fallback. */
import fs from "node:fs/promises";
import http from "node:http";

/** Load one face, then inspect the font Chrome actually uses for two region-sensitive Chinese glyphs. */
async function inspectFace(page, url, weight, expected) {
  try {
    await page.evaluate(async ({ url, weight }) => {
      document.documentElement.lang = "zh-CN";
      document.documentElement.style.fontFamily = '"Noto Sans CJK SC", "Noto Sans SC", sans-serif';
      for (const face of document.fonts) {
        if (face.family === "Noto Sans CJK SC" && face.weight === String(weight)) document.fonts.delete(face);
      }
      const face = new FontFace("Noto Sans CJK SC", `url("${url}")`, { weight: String(weight) });
      let timer;
      try {
        document.fonts.add(await Promise.race([
          face.load(),
          new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("Managed font load timed out")), 10000); }),
        ]));
      } finally {
        clearTimeout(timer);
      }
      const probe = document.createElement("span");
      probe.id = "imv-managed-font-probe";
      probe.lang = "zh-CN";
      probe.textContent = "关复";
      probe.style.cssText = `position:fixed;top:0;left:0;font:normal ${weight} 48px "Noto Sans CJK SC";`;
      document.body.append(probe);
    }, { url, weight });
    const client = page._client();
    await client.send("DOM.enable");
    await client.send("CSS.enable");
    const { value: { root: document } } = await client.send("DOM.getDocument");
    const { value: { nodeId } } = await client.send("DOM.querySelector", { nodeId: document.nodeId, selector: "#imv-managed-font-probe" });
    const { value: { fonts } } = await client.send("CSS.getPlatformFontsForNode", { nodeId });
    if (fonts.length !== 1 || !fonts[0].isCustomFont || fonts[0].glyphCount !== 2 || fonts[0].postScriptName !== expected) {
      throw new Error(`Managed SC glyph font mismatch at weight ${weight}: ${JSON.stringify(fonts)}`);
    }
  } finally {
    await page.evaluate(() => document.getElementById("imv-managed-font-probe")?.remove());
  }
}

/** Switch failed primary files to the bundled SC face before bundling; diagnostic checks never veto a composition. */
export async function checkManagedFonts(page, root) {
  const identities = JSON.parse(await fs.readFile(`${root}/public/fonts.json`, "utf8"));
  const fallbackWeights = [];
  // Keep large fonts off the CDP JSON channel; only these three attempt-local files are served on loopback.
  const server = http.createServer(async (request, response) => {
    if (!/^\/font-(400|700|fallback)\.otf$/.test(request.url ?? "")) {
      response.writeHead(200, { "Content-Type": "text/html" });
      response.end('<!doctype html><html lang="zh-CN"><body></body></html>');
      return;
    }
    try {
      const data = await fs.readFile(`${root}/public${request.url}`);
      response.writeHead(200, { "Content-Type": "font/otf", "Access-Control-Allow-Origin": "*", "Cache-Control": "max-age=60" });
      response.end(data);
    } catch {
      response.writeHead(404);
      response.end();
    }
  });
  try {
    await new Promise((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
    const base = `http://127.0.0.1:${server.address().port}`;
    if (page.url() === "about:blank") await page.goto({ url: base, timeout: 10000 });
    for (const weight of [400, 700]) {
      const file = `${root}/public/font-${weight}.otf`;
      try {
        await inspectFace(page, `${base}/font-${weight}.otf`, weight, identities[String(weight)]);
      } catch (error) {
        console.warn(`[fonts] Weight ${weight}: using bundled SC fallback (${String(error.message).slice(0, 500)})`);
        await fs.copyFile(`${root}/public/font-fallback.otf`, file);
        identities[String(weight)] = identities.fallback;
        fallbackWeights.push(weight);
        try {
          await inspectFace(page, `${base}/font-fallback.otf`, weight, identities.fallback);
        } catch (fallbackError) {
          // Unsupported diagnostics are advisory; the renderer still has the SC file and the local font fallback.
          console.warn(`[fonts] SC fallback check unavailable: ${String(fallbackError.message).slice(0, 500)}`);
        }
      }
    }
    await fs.writeFile(`${root}/public/fonts.json`, JSON.stringify(identities));
    return { fallbackWeights };
  } finally {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  }
}

/** Check the exact files used by Remotion's entrypoint on a temporary page, and always close it before rendering. */
export async function verifyManagedFonts(browser, root) {
  const page = await browser.newPage({ context: () => null, logLevel: "error", indent: false, pageIndex: 0, onBrowserLog: null, onLog: () => undefined });
  try {
    return await checkManagedFonts(page, root);
  } finally {
    await page.close();
  }
}
