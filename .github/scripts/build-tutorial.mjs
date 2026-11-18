/** Capture the current client, combine its screenshots with release-maintained prose, and validate the offline HTML and A4 PDF before publishing artifacts. */
import { readFile, writeFile, mkdir, realpath, mkdtemp, rm } from "node:fs/promises";
import { resolve, relative, isAbsolute, join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";

const repository = fileURLToPath(new URL("../../", import.meta.url));

/** Inline only files belonging to the tutorial, so the exported HTML has no network or checkout dependencies. */
export async function assembleTutorial(source, commit) {
  const root = await realpath(source);
  /** Resolve symlinks before reading; exported assets must stay inside this source directory. */
  async function asset(path) {
    const absolute = await realpath(resolve(root, path));
    const local = relative(root, absolute);
    if (local.startsWith("..") || isAbsolute(local)) throw new Error(`Asset outside tutorial: ${path}`);
    return readFile(absolute);
  }
  let html = await readFile(resolve(root, "index.html"), "utf8");
  html = html.replace("{{BUILD_COMMIT}}", commit.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;"));
  for (const match of [...html.matchAll(/<link rel="stylesheet" href="([^"]+)">/g)]) {
    html = html.replace(match[0], `<style>${await asset(match[1])}</style>`);
  }
  for (const match of [...html.matchAll(/<img\b[^>]*\bsrc="([^"]+)"[^>]*>/g)]) {
    if (!match[1].endsWith(".png")) throw new Error(`Expected a local PNG: ${match[1]}`);
    const data = (await asset(match[1])).toString("base64");
    html = html.replace(match[0], match[0].replace(match[1], `data:image/png;base64,${data}`));
  }
  if (html.includes("{{")) throw new Error("Unresolved tutorial placeholder");
  return html;
}

/** Catch broken figures, duplicate IDs, missing internal destinations and pages that would be clipped when printed. */
export async function validateTutorial(page) {
  await page.evaluate(() => document.fonts.ready);
  const problems = await page.evaluate(async () => {
    const errors = [];
    const ids = new Set();
    for (const element of document.querySelectorAll("[id]")) {
      if (ids.has(element.id)) errors.push(`Duplicate ID: ${element.id}`);
      ids.add(element.id);
    }
    for (const anchor of document.querySelectorAll('a[href^="#"]')) {
      if (!ids.has(anchor.getAttribute("href").slice(1))) errors.push(`Missing destination: ${anchor.getAttribute("href")}`);
    }
    for (const picture of document.images) {
      try { await picture.decode(); } catch { errors.push(`Broken image: ${picture.alt}`); }
      if (!picture.alt || !picture.naturalWidth) errors.push("Image needs descriptive alt text and pixels");
    }
    const pages = [...document.querySelectorAll(".page")];
    if (!pages.length) errors.push("Tutorial has no pages");
    for (const [index, sheet] of pages.entries()) {
      if (sheet.scrollHeight > sheet.clientHeight + 2 || sheet.scrollWidth > sheet.clientWidth + 2)
        errors.push(`Page ${index + 1} overflows: ${sheet.id}`);
    }
    return errors;
  });
  if (problems.length) throw new Error(problems.join("\n"));
}

/** Use the project's locked Playwright dependency and bundled Chromium; IMV_CHROME_PATH permits an installed local browser. */
export async function buildTutorial(output = resolve(repository, "dist/tutorial")) {
  const require = createRequire(resolve(repository, "client/package.json"));
  const { chromium } = require("playwright-core");
  const commit = execFileSync("git", ["rev-parse", "--short=7", "HEAD"], { cwd: repository, encoding: "utf8" }).trim();
  const dirty = execFileSync("git", ["status", "--porcelain"], { cwd: repository, encoding: "utf8" }).trim();
  const buildCommit = `${commit}${dirty ? "-dirty" : ""}`;
  const source = await mkdtemp(join(tmpdir(), "imv-tutorial-"));
  let browser;
  try {
    const { captureTutorial } = await import("../../docs/tutorial/capture.ts");
    await captureTutorial(source);
    const html = await assembleTutorial(source, buildCommit);
    browser = await chromium.launch({ headless: true, ...(process.env.IMV_CHROME_PATH ? { executablePath: process.env.IMV_CHROME_PATH } : {}) });
    const page = await browser.newPage({ viewport: { width: 794, height: 1123 }, locale: "zh-CN" });
    page.setDefaultTimeout(30_000);
    const network = [];
    await page.route("**/*", route => { network.push(route.request().url()); return route.abort(); });
    await page.emulateMedia({ media: "print" });
    await page.setContent(html, { waitUntil: "load" });
    await validateTutorial(page);
    if (network.length) throw new Error(`Tutorial must be offline; requested: ${network.join(", ")}`);
    const pdf = await page.pdf({
      format: "A4", preferCSSPageSize: true, printBackground: true, tagged: true, outline: true,
      displayHeaderFooter: true, headerTemplate: "<span></span>",
      footerTemplate: '<div style="font-size:8px;width:100%;text-align:center;color:#64748b;font-family:sans-serif">IntelligentMixVideo · <span class="pageNumber"></span> / <span class="totalPages"></span></div>',
    });
    await mkdir(output, { recursive: true });
    await writeFile(resolve(output, "IntelligentMixVideo-Tutorial.html"), html);
    await writeFile(resolve(output, "IntelligentMixVideo-Tutorial.pdf"), pdf);
    const screenshots = JSON.parse(await readFile(join(source, "screenshots.json"), "utf8"));
    await writeFile(resolve(output, "screenshots.json"), JSON.stringify({ buildCommit, ...screenshots }, null, 2) + "\n");
    const pages = await page.locator(".page").count();
    console.log(`Built ${pages} tutorial pages in ${output} (${pdf.length} PDF bytes; ${commit}${dirty ? "-dirty" : ""})`);
  } finally {
    await browser?.close();
    await rm(source, { recursive: true, force: true });
  }
}

if (import.meta.main) await buildTutorial(process.argv[2] ? resolve(process.argv[2]) : undefined);
