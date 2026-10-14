/**
 * Networkless browser behavior validator for PR76 components.  It performs
 * source-policy/type checks and drives a fixed Remotion Player through a small
 * page bridge; it never captures frames, encodes media, or invokes a visual
 * reviewer.
 */
import fs from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import ts from "typescript";
import { build } from "esbuild";
import { openBrowser } from "@remotion/renderer";
import prettier from "prettier";
import { exportTemplate } from "./presentation.mjs";
import { languageDiagnostics } from "./typescript.mjs";

const root = process.env.IMV_WORK_ROOT ?? "/work";
const rendererRoot = process.env.IMV_RENDERER_ROOT ?? "/renderer";
os.tmpdir = () => `${root}/.tmp`;
const request = JSON.parse(await fs.readFile(`${root}/request.json`, "utf8"));
const checks = [];
const result = { checks, diagnostics: [], tests: [], custom_tests_executed: 0 };

/**
 * A policy violation that remembers where it is in the submitted code, so the diagnostic can point at it
 * instead of leaving the author to search the whole file.
 */
class PolicyError extends Error {
  constructor(message, node, source) {
    const start = node.getStart(source);
    const { line, character } = source.getLineAndCharacterOfPosition(start);
    super(`${message} (line ${line + 1}, column ${character + 1})`);
    // A node can span lines, so its end is resolved from its own offset rather than added to the start column.
    this.position = { line, character, end: source.getLineAndCharacterOfPosition(Math.max(node.getEnd(), start + 1)) };
  }
}

/** Reject generated code which can reach the host, files or network. */
function sourcePolicy(code) {
  const source = ts.createSourceFile("Component.tsx", code, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  // The renderer sandbox supplies the complete pinned React/Remotion packages.
  // Capability restrictions here only prevent escaping the sandbox; visual
  // elements, media props and hooks are valid PR76 component behavior.
  const forbidden = new Set(["eval", "Function", "require", "process", "global", "globalThis", "fetch", "XMLHttpRequest", "WebSocket", "Worker", "child_process", "exec", "spawn"]);
  // `exec` and `spawn` only name host capabilities when used as a bare identifier. As a member name they
  // are ordinary methods, such as RegExp.prototype.exec, which colour and text parsing legitimately use.
  const memberAllowed = new Set(["exec", "spawn"]);
  const isMemberName = (node) => (ts.isPropertyAccessExpression(node.parent) && node.parent.name === node)
    || (ts.isQualifiedName(node.parent) && node.parent.right === node);
  function visit(node) {
    if (ts.isImportDeclaration(node)) {
      const module = node.moduleSpecifier.text;
      if (!["react", "react/jsx-runtime", "react/jsx-dev-runtime", "remotion"].includes(module)) throw new PolicyError(`Import not permitted: ${module}`, node, source);
      const clause = node.importClause;
      if (!clause || (clause.name && module !== "react") || (clause.namedBindings && !ts.isNamedImports(clause.namedBindings) && !ts.isNamespaceImport(clause.namedBindings))) throw new PolicyError("Only React/Remotion imports are permitted.", node, source);
    }
    if (ts.isIdentifier(node) && forbidden.has(node.text) && !(memberAllowed.has(node.text) && isMemberName(node)))
      throw new PolicyError(`Unsupported capability: ${node.text}`, node, source);
    if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword) throw new PolicyError("Dynamic imports are not permitted.", node, source);
    if (ts.isExportDeclaration(node) && node.moduleSpecifier) throw new PolicyError("Re-exports are not permitted.", node, source);
    ts.forEachChild(node, visit);
  }
  visit(source);
}

/** The trimmed source line a diagnostic points at, or an empty object when there is none. */
function excerpt(lines, line) {
  const text = Number.isInteger(line) ? lines?.[line]?.trim() : "";
  return text ? { excerpt: text.slice(0, 200) } : {};
}

/** Build the public diagnostic for a thrown error; a policy violation also reports its zero-based range and line. */
function diagnosticFromError(error, code = null) {
  const position = error?.position;
  return {
    source: "contract",
    severity: "error",
    message: String(error?.message ?? error),
    ...(code ? { code: String(code) } : {}),
    ...(position ? { range: { start: { line: position.line, character: position.character }, end: { line: position.end.line, character: position.end.character } } } : {}),
    ...(position ? excerpt(String(request.code).split("\n"), position.line) : {}),
  };
}

/**
 * Convert the existing language service shape into the public LSP-like shape.
 * Positions refer to the checked (formatted or host-generated) files, so each diagnostic also carries the
 * line it points at; the author can find that text in its own submission even when line numbers differ.
 */
async function lspDiagnostics(items) {
  const files = new Map();
  for (const item of items) {
    if (item.file && !files.has(item.file)) files.set(item.file, (await fs.readFile(`${root}/${path.basename(item.file)}`, "utf8").catch(() => "")).split("\n"));
  }
  return items.map((item) => {
    const line = Number.isInteger(item.line) ? Math.max(0, item.line - 1) : null;
    const character = Number.isInteger(item.column) ? Math.max(0, item.column - 1) : null;
    return {
      source: "lsp",
      severity: ["error", "warning", "information", "hint"].includes(item.severity) ? item.severity : "error",
      message: String(item.message),
      ...(item.file ? { file: path.basename(item.file) } : {}),
      ...(item.code !== null && item.code !== undefined ? { code: String(item.code) } : {}),
      ...(line !== null && character !== null ? { range: { start: { line, character }, end: { line, character: character + 1 } } } : {}),
      ...(item.file ? excerpt(files.get(item.file), line) : {}),
    };
  });
}

/** Create the isolated source files and perform syntax/source-policy/LSP checks. */
async function prepareSource() {
  let formatted;
  try {
    sourcePolicy(request.code);
    formatted = await prettier.format(request.code, { parser: "typescript" });
    checks.push({ name: "source_policy", status: "pass", message: "Source policy passed." });
  } catch (error) {
    checks.push({ name: "source_policy", status: "failed", message: String(error?.message ?? error).slice(0, 6000) });
    result.diagnostics.push(diagnosticFromError(error));
    return false;
  }
  await fs.writeFile(`${root}/Template.tsx`, formatted, "utf8");
  try {
    // Read-only diagnostics use the exact sealed bytes displayed by the client.
    const exported = request.export_code ?? await exportTemplate(formatted, request.default_parameters, request.composition);
    await fs.writeFile(`${root}/Export.tsx`, exported, "utf8");
    checks.push({ name: "export_source", status: "pass", message: "Default component export accepted." });
  } catch (error) {
    checks.push({ name: "export_source", status: "failed", message: String(error?.message ?? error).slice(0, 6000) });
    result.diagnostics.push(diagnosticFromError(error?.message ?? error));
    return false;
  }
  await fs.writeFile(`${root}/contract.tsx`, `/** Host-generated default props call site. */\nimport React from 'react';\nimport Template from './Template';\nimport Export from './Export';\nconst props = ${JSON.stringify(request.default_parameters)};\nconst element = <Template {...props} />;\nconst exported = <Export />;\n`, "utf8");
  await fs.symlink(`${rendererRoot}/node_modules`, `${root}/node_modules`).catch(() => undefined);
  const checked = languageDiagnostics(root);
  if (checked.diagnostics.length) result.diagnostics.push(...await lspDiagnostics(checked.diagnostics));
  checks.push({ name: "typescript", status: checked.diagnostics.some((item) => item.severity === "error") ? "failed" : "pass", message: checked.detail ? checked.detail.slice(0, 6000) : "TypeScript checks passed." });
  result.diagnostics = result.diagnostics.filter((item, index, all) => index === all.findIndex((other) => JSON.stringify(other) === JSON.stringify(item)));
  return checked.diagnostics.every((item) => item.severity !== "error");
}

/** The host context of the submitted component: its parameters, canvas and, for boundary samples, content box. */
function hostConfig() {
  return { defaults: request.default_parameters, configured: request.parameters ?? request.default_parameters, parameter_schema: request.parameter_schema, composition: request.composition, ...(request.box ? { box: request.box } : {}) };
}

/** Build a page host with only the component in `Template.tsx` and the immutable context `config`. */
async function bundleHost(config) {
  const output = await build({
    entryPoints: [`${rendererRoot}/tool-validation-host.tsx`], bundle: true, write: false,
    platform: "browser", format: "iife", target: "es2022", jsx: "automatic", minify: false,
    define: { "process.env.NODE_ENV": '"production"' },
    plugins: [{
      name: "validation-input",
      setup(builder) {
        builder.onResolve({ filter: /^imv:template$/ }, () => ({ path: `${root}/Template.tsx` }));
        builder.onResolve({ filter: /^imv:config$/ }, () => ({ path: "config", namespace: "imv" }));
        builder.onLoad({ filter: /.*/, namespace: "imv" }, () => ({ contents: JSON.stringify(config), loader: "json" }));
      },
    }],
  });
  if (output.outputFiles.length !== 1) throw new Error("Validation host bundle is empty");
  await fs.writeFile(`${root}/interactive.js`, output.outputFiles[0].text, "utf8");
}

function serveBundle() {
  return new Promise((resolve, reject) => {
    const server = http.createServer(async (req, res) => {
      try {
        if (req.url === "/interactive.js") {
          res.writeHead(200, { "content-type": "text/javascript; charset=utf-8", "cache-control": "no-store" });
          res.end(await fs.readFile(`${root}/interactive.js`));
        } else {
          res.writeHead(200, { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" });
          // The host installs its own listeners only after the component module has evaluated, so a component that throws
          // while loading would leave no trace; the shell records that error first.
          const early = `for (const type of ["error", "unhandledrejection"]) window.addEventListener(type, (event) => { window.__imvRuntimeError = String(event.error?.message ?? event.reason?.message ?? event.reason ?? event.message ?? "Runtime error"); });`;
          res.end(`<!doctype html><html><body style="margin:0"><div id="root"></div><script>${early}</script><script src="/interactive.js"></script></body></html>`);
        }
      } catch (error) { res.writeHead(500); res.end(String(error)); }
    });
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve({ server, url: `http://127.0.0.1:${server.address().port}/` }));
  });
}

/** Open the browser and its page at the canvas size, ready to load the host from `url`. */
async function openPage(url) {
  // Chromium creates its profile under the attempt-local temporary directory.
  await fs.mkdir(os.tmpdir(), { recursive: true });
  const browser = await openBrowser("chrome", { browserExecutable: request.browser, logLevel: "error", chromiumOptions: { gl: "swangle", enableMultiProcessOnLinux: true } });
  const pages = await browser.pages();
  const page = pages[0] ?? await browser.newPage({ context: () => null, logLevel: "error", indent: false, pageIndex: 0, onBrowserLog: null, onLog: () => undefined });
  page.__imvUrl = url;
  // The real overlay render opens its page at the composition size, so vw/vh, window.inner* and media queries must see that size here too.
  await page.setViewport({ width: request.composition.width, height: request.composition.height, deviceScaleFactor: 1 });
  return { browser, page };
}

/** Wait for the host bridge. Returns the error a component threw while loading, which keeps the host from ever mounting, else null. */
async function waitReady(page) {
  await page.goto({ url: page.__imvUrl, timeout: 30000, options: { waitUntil: "load" } });
  return page.evaluate(async () => {
    const end = Date.now() + 15000;
    while (!(window).__imvTool?.ready && !(window).__imvRuntimeError && Date.now() < end) await new Promise((resolve) => setTimeout(resolve, 20));
    if ((window).__imvTool?.ready) return null;
    if ((window).__imvRuntimeError) return (window).__imvRuntimeError;
    throw new Error("Validation host did not initialize");
  });
}

/** Fail a base check when the mounted component threw during React rendering. */
async function ensureHealthy(page) {
  const message = await page.evaluate(() => window.__imvRuntimeError ?? null);
  if (message) throw new Error(`Component runtime error: ${message}`);
}

function querySnapshot(page, selector, all) {
  return page.evaluate(({ selector, all }) => {
    const root = document.querySelector("[data-imv-validation-canvas]");
    if (!root) throw new Error("Validation canvas is unavailable");
    const nodes = all ? Array.from(root.querySelectorAll(selector)) : [root.querySelector(selector)].filter(Boolean);
    const canvasBox = root.getBoundingClientRect();
    return nodes.map((node) => {
      const box = node.getBoundingClientRect();
      const styles = getComputedStyle(node);
      const styleValues = {};
      for (const name of ["opacity", "color", "background-color", "font-size", "font-family", "font-weight", "transform", "display", "visibility"]) styleValues[name] = styles.getPropertyValue(name);
      return { text: node.textContent ?? "", box: { x: box.x - canvasBox.x, y: box.y - canvasBox.y, width: box.width, height: box.height }, styles: styleValues };
    });
  }, { selector, all });
}

/** Alpha at or above this value (about 95%) hides whatever lies underneath. */
const OPAQUE_ALPHA = 242;
/** A frame hides the video when at least this share of its pixels is opaque. */
const COVERED_SHARE = 0.98;
/**
 * A Sprite hides the video when this share of the sampled frames is covered. One clear frame is not enough: a cover that fades in
 * or starts late is clear on the first frame and hides the video afterwards. A cover over only the middle of the timeline, as a
 * transition has, stays well below this share.
 */
const COVERED_FRAMES = 0.8;

/** First, last and three frames in between, so a Sprite that is clear for part of its time is not rejected. */
function sampleFrames(total) {
  return [...new Set([0, 0.25, 0.5, 0.75, 1].map((ratio) => Math.min(total - 1, Math.floor((total - 1) * ratio))))];
}

/** Share of canvas pixels that are nearly opaque at the current frame, read from a screenshot taken without a page background. */
async function opaqueShare(page, composition) {
  const client = page._client();
  await client.send("Emulation.setDefaultBackgroundColorOverride", { color: { r: 0, g: 0, b: 0, a: 0 } });
  try {
    const shot = await client.send("Page.captureScreenshot", {
      format: "png", fromSurface: true, captureBeyondViewport: true,
      clip: { x: 0, y: 0, width: composition.width, height: composition.height, scale: 1 },
    });
    // The renderer's protocol client wraps the CDP result as `{ value: { data } }`.
    const encoded = (shot.value ?? shot).data;
    if (typeof encoded !== "string" || !encoded) throw new Error("Transparency screenshot was empty");
    return await page.evaluate(async ({ encoded, threshold }) => {
      const image = new Image();
      image.src = `data:image/png;base64,${encoded}`;
      await image.decode();
      const canvas = document.createElement("canvas");
      canvas.width = image.naturalWidth;
      canvas.height = image.naturalHeight;
      const context = canvas.getContext("2d", { willReadFrequently: true });
      context.drawImage(image, 0, 0);
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
      let opaque = 0;
      for (let index = 3; index < pixels.length; index += 4) if (pixels[index] >= threshold) opaque += 1;
      return { width: canvas.width, height: canvas.height, share: opaque / (canvas.width * canvas.height) };
    }, { encoded, threshold: OPAQUE_ALPHA });
  } finally {
    await client.send("Emulation.setDefaultBackgroundColorOverride", {});
  }
}

/**
 * Mechanical check that a Sprite leaves the video visible: it fails when most sampled frames are opaque almost
 * everywhere. This is a pixel measurement, not a visual review of how the result looks.
 */
async function transparencyCheck(page) {
  const composition = request.composition;
  const frames = sampleFrames(composition.duration_frames);
  const needed = Math.ceil(frames.length * COVERED_FRAMES);
  const samples = [];
  let covered = 0;
  for (const frame of frames) {
    await page.evaluate((target) => window.__imvTool.setFrame(target), frame);
    // The base checks only mount frame 0; a component that throws later is the author's to fix, not an unavailable validator.
    const failure = await page.evaluate(() => window.__imvRuntimeError ?? null);
    if (failure)
      return {
        name: "transparency",
        status: "failed",
        message: `The component throws at frame ${frame}: ${failure}. Every frame of its duration must render without an error, so the canvas could not be measured.`.slice(0, 6000),
      };
    // Capturing is the costly part; once the verdict is settled the remaining frames are only mounted, to catch errors.
    if (covered >= needed || samples.length - covered > frames.length - needed) continue;
    const measured = await opaqueShare(page, composition);
    if (measured.width !== composition.width || measured.height !== composition.height)
      throw new Error(`Transparency screenshot is ${measured.width}x${measured.height}, expected ${composition.width}x${composition.height}`);
    samples.push({ frame, share: measured.share });
    if (measured.share >= COVERED_SHARE) covered += 1;
  }
  await page.evaluate(() => window.__imvTool.setFrame(0));
  const summary = samples.map(({ frame, share }) => `frame ${frame}: ${(share * 100).toFixed(1)}% opaque`).join(", ");
  if (covered >= needed)
    return {
      name: "transparency",
      status: "failed",
      message: `The Sprite hides the video: at least ${COVERED_SHARE * 100}% of the ${composition.width}x${composition.height} canvas is opaque in ${covered} of the ${frames.length} sampled frames (${summary}). `
        + "Sprites are composited over the user's own video, so keep the canvas transparent: do not paint an opaque full-frame background "
        + "(backgroundColor or background on the root or on a full-size layer, or a full-size layer at opacity 1), also when it fades in or starts late. "
        + "For a filter or colour tint build translucent layers (alpha well below 1), gradients, a vignette or grain.",
    };
  return { name: "transparency", status: "passed", message: `The video stays visible: the canvas is covered in ${covered} of the ${samples.length} measured frames (${summary}).` };
}

/** First, middle and last frame of one render length. */
function edgeFrames(length) {
  return [...new Set([0, Math.floor((length - 1) / 2), length - 1])];
}

/**
 * The bus renders a Sprite at the length it passes (`request.lengths` holds the shortest and longest it may ask
 * for), not at the previewed one: mount the first, middle and last frame at each of them. The preview length is
 * restored afterwards, also after a failure, so the custom tests that follow run at the length they were written for.
 */
async function lengthsCheck(page) {
  const { fps, duration_frames: preview } = request.composition;
  try {
    for (const length of request.lengths) {
      await page.evaluate((frames) => window.__imvTool.setDuration(frames), length);
      for (const frame of edgeFrames(length)) {
        await page.evaluate((target) => window.__imvTool.setFrame(target), frame);
        const failure = await page.evaluate(() => window.__imvRuntimeError ?? null);
        if (failure)
          return {
            name: "render_lengths",
            status: "failed",
            message: `Rendered ${length} frames long (${length / fps} s) instead of the ${preview}-frame preview, the component throws at frame ${frame}: ${failure}. `
              + "The final video sets the length, anywhere from the shortest to the longest of these, so the code must not assume the preview length: "
              + "for example keep interpolate input ranges increasing when durationInFrames is short.",
          };
      }
    }
    return { name: "render_lengths", status: "passed", message: `Rendered without an error at ${request.lengths.join(" and ")} frames.` };
  } finally {
    await page.evaluate((frames) => window.__imvTool.setDuration(frames), preview);
    await page.evaluate(() => window.__imvTool.setFrame(0));
  }
}

/** Filler glyph for the longest-text check: CJK glyphs are the widest, so they expose layout problems first. */
const TEXT_FILLER = "测";
/** A rendered text line shorter than this is unreadable on the 1080x1920 canvas (the smallest supported fontSize is 24). */
const MIN_LINE_HEIGHT = 20;
/** Sub-pixel tolerance when comparing a text rectangle with a box. */
const FIT_TOLERANCE = 2;
/** Frames whose text area is at least this share of the largest one show the text at its full size. */
const FULL_SIZE_SHARE = 0.98;

/**
 * The host transform of one placement as `host-layer.tsx` applies it: the content box centre (cx, cy) is moved to
 * (x, y), then the Sprite is rotated by `rotation` degrees and scaled by `scale` around it. Missing fields keep the
 * Sprite where it is, so an empty transform is the identity. `visible` is the part of the Sprite's canvas the video
 * shows (see `visibleWindow`).
 */
function hostPlacement(box, transform = {}, visible = null) {
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  const finite = (value, fallback) => (typeof value === "number" && Number.isFinite(value) ? value : fallback);
  return { cx, cy, x: finite(transform.x, cx), y: finite(transform.y, cy), scale: finite(transform.scale, 1), rotation: finite(transform.rotation, 0), visible };
}

/**
 * The part of a Sprite's `canvas` that a video of `output` size shows, in canvas pixels. `overlay-worker.mjs` scales
 * a canvas of another size to cover the output and centres it, so the overflow on the longer side is cut off; a
 * canvas of the output's size is shown whole.
 */
function visibleWindow(canvas, output) {
  const scale = Math.max(output.width / canvas.width, output.height / canvas.height);
  const [width, height] = [output.width / scale, output.height / scale];
  return { x: (canvas.width - width) / 2, y: (canvas.height - height) / 2, width, height };
}

/**
 * Describe, for the frame currently mounted, how the text `needle` does not fit: text clipped by an ancestor whose
 * overflow is not visible (hidden, clip, auto or scroll) or that contains its paint, text outside the canvas once
 * `placement` (see `hostPlacement`) moves, rotates and scales it, text lines too small to read, or only part of the
 * text rendered (sliced, or partly hidden). The component is mounted without the host transform: clipping and line height are measured in its own layout, and only the corners
 * of each line are mapped through the transform for the canvas. Only painted text counts: a node without a box
 * (`display: none`), with `visibility: hidden` or faded out to opacity 0 is not shown. A line is measured as its line
 * box (at least its em box), not as the font's whole ascent and descent: with a lineHeight below about 1.45 the
 * latter sticks out of a box that holds the line exactly, although no glyph is cut. Returns `null` when no part of
 * the text is shown at this frame (not yet shown), otherwise the `problems` (empty when all of it is shown and fits)
 * and the `area` of the shown lines, which tells the frames that show the text at its full size.
 */
function textFitProblems(page, needle, placement) {
  return page.evaluate((needle, minLine, tolerance, placement) => {
    const canvasNode = document.querySelector("[data-imv-validation-canvas]");
    const layer = document.querySelector("[data-imv-host-layer]")?.firstElementChild;
    if (!canvasNode || !layer) return null;
    const canvas = canvasNode.getBoundingClientRect();
    const view = placement.visible ?? { x: 0, y: 0, width: canvas.width, height: canvas.height };
    const radians = (placement.rotation * Math.PI) / 180;
    const [cos, sin] = [Math.cos(radians) * placement.scale, Math.sin(radians) * placement.scale];
    /** Canvas position of a point of the untransformed layout, as the host layer places it. */
    const place = (left, top) => {
      const [dx, dy] = [left - canvas.left - placement.cx, top - canvas.top - placement.cy];
      return [placement.x + dx * cos - dy * sin, placement.y + dx * sin + dy * cos];
    };
    /** Whether `element` is painted: not `visibility: hidden` and not faded out by its own or an ancestor's opacity. */
    const painted = (element) => {
      if (getComputedStyle(element).visibility !== "visible") return false;
      let opacity = 1;
      for (let item = element; item && layer.contains(item); item = item.parentElement) opacity *= Number(getComputedStyle(item).opacity);
      return opacity > 0.01;
    };
    const ruler = document.createElement("canvas").getContext("2d");
    /**
     * The client rectangles of a text node's lines, each trimmed to its line box. A rectangle spans the font's ascent
     * and descent (1.448em for Noto Sans CJK), and CSS centres the line box on it; the share above the line box is
     * read in CSS pixels from the font and scaled to the rectangle, so the component's own transforms are respected.
     */
    const lineBoxes = (range, element) => {
      const style = getComputedStyle(element);
      ruler.font = style.font;
      const metrics = ruler.measureText(needle.slice(0, 1) || "测");
      const content = metrics.fontBoundingBoxAscent + metrics.fontBoundingBoxDescent;
      const size = parseFloat(style.fontSize);
      // `normal` line height already holds the whole ascent and descent.
      const line = Math.max(parseFloat(style.lineHeight) || content, size);
      const share = content > line ? (content - line) / 2 / content : 0;
      return Array.from(range.getClientRects()).filter((rect) => rect.width > 0 && rect.height > 0).map((rect) => {
        const inset = rect.height * share;
        return { left: rect.left, right: rect.right, top: rect.top + inset, bottom: rect.bottom - inset, height: rect.height - 2 * inset };
      });
    };
    // Components may split the text into words or glyphs and drop the spaces between them.
    const compact = (value) => value.replace(/\s+/g, "");
    const wanted = compact(needle);
    const walker = document.createTreeWalker(layer, NodeFilter.SHOW_TEXT);
    const problems = new Set();
    let joined = "";
    let area = 0;
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const shown = compact(node.nodeValue);
      if (!shown || !(wanted.includes(shown) || shown.includes(wanted)) || !node.parentElement || !painted(node.parentElement)) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      const lines = lineBoxes(range, node.parentElement);
      // Text without a box (display: none, an empty or collapsed element) is not shown.
      if (!lines.length) continue;
      const bounds = {
        left: Math.min(...lines.map((rect) => rect.left)), right: Math.max(...lines.map((rect) => rect.right)),
        top: Math.min(...lines.map((rect) => rect.top)), bottom: Math.max(...lines.map((rect) => rect.bottom)),
      };
      joined += shown;
      area += lines.reduce((sum, rect) => sum + (rect.right - rect.left) * rect.height, 0);
      const smallest = Math.min(...lines.map((rect) => rect.height));
      if (smallest < minLine) problems.add(`its lines are only ${Math.round(smallest)}px tall, too small to read`);
      const corners = lines.flatMap((rect) => [place(rect.left, rect.top), place(rect.right, rect.top), place(rect.left, rect.bottom), place(rect.right, rect.bottom)]);
      const [left, right] = [Math.min(...corners.map(([x]) => x)), Math.max(...corners.map(([x]) => x))];
      const [top, bottom] = [Math.min(...corners.map(([, y]) => y)), Math.max(...corners.map(([, y]) => y))];
      if (left < view.x - tolerance || right > view.x + view.width + tolerance || top < view.y - tolerance || bottom > view.y + view.height + tolerance)
        problems.add(`it extends beyond the ${Math.round(view.width)}x${Math.round(view.height)} ${view.width < canvas.width - tolerance || view.height < canvas.height - tolerance ? "part of the canvas the video shows" : "canvas"} (text box ${Math.round(left)},${Math.round(top)} to ${Math.round(right)},${Math.round(bottom)})`);
      for (let element = node.parentElement; element && layer.contains(element); element = element.parentElement) {
        const style = getComputedStyle(element);
        // Every overflow other than visible cuts the painted content (auto and scroll only add a scroll position the
        // video never moves), and paint containment clips like overflow clip.
        const contained = /\b(?:paint|strict|content)\b/.test(style.contain);
        const clipsX = contained || style.overflowX !== "visible";
        const clipsY = contained || style.overflowY !== "visible";
        if (!clipsX && !clipsY) continue;
        const box = element.getBoundingClientRect();
        if ((clipsX && (bounds.left < box.left - tolerance || bounds.right > box.right + tolerance)) || (clipsY && (bounds.top < box.top - tolerance || bounds.bottom > box.bottom + tolerance)))
          problems.add(`it is cut off by a ${Math.round(box.width)}x${Math.round(box.height)} box with ${contained ? `contain: ${style.contain}` : `overflow ${clipsX ? style.overflowX : style.overflowY}`}`);
      }
    }
    if (!joined) return null;
    if (!joined.includes(wanted)) problems.add(`only part of it is rendered, not all ${wanted.length} characters`);
    return { problems: [...problems], area };
  }, needle, MIN_LINE_HEIGHT, FIT_TOLERANCE, placement);
}

/** No host transform: generation checks mount the Sprite over the whole canvas where it was laid out. */
const UNPLACED = Object.freeze({ cx: 0, cy: 0, x: 0, y: 0, scale: 1, rotation: 0, visible: null });

/**
 * Measure the text `needle` on `frames` (default: the sampled frames of the composition) of the currently mounted
 * values, placed on the canvas by `placement`. `shown` counts the frames that display it; `error` is a runtime error
 * thrown at `frame`. The text is judged on the frames that show it at its full size (`full`, by the area of its
 * lines): entrance and exit animations show it smaller, scaled down or typed in, and would hide a layout that clips
 * it while it holds. It `fits` when one of those frames has no problem, so a reveal mask or slide-in that moves the
 * full-size text out of view for a few frames is fine; `problems` are those of the full-size frames otherwise.
 */
async function measureTextFit(page, needle, frames = sampleFrames(request.composition.duration_frames), placement = UNPLACED) {
  const verdicts = [];
  for (const frame of frames) {
    await page.evaluate((target) => window.__imvTool.setFrame(target), frame);
    const error = await page.evaluate(() => window.__imvRuntimeError ?? null);
    if (error) return { error, frame, shown: verdicts.length, full: 0, fits: false, problems: [] };
    const verdict = await textFitProblems(page, needle, placement);
    if (verdict) verdicts.push(verdict);
  }
  const largest = Math.max(0, ...verdicts.map((verdict) => verdict.area));
  const full = verdicts.filter((verdict) => verdict.area >= largest * FULL_SIZE_SHARE);
  const fits = full.some((verdict) => !verdict.problems.length);
  return { error: null, shown: verdicts.length, full: full.length, fits, problems: fits ? [] : [...new Set(full.flatMap((verdict) => verdict.problems))] };
}

/**
 * Mechanical check that a text effect shows its longest allowed text in full: the text parameter is filled to its
 * declared maxLength and the sampled frames are measured. The check fails when every frame that shows the text at
 * its full size has a problem (a text cut to a slice of itself is one), and when the text is never visibly rendered.
 * This is a geometric measurement of the text boxes, not a visual review of how the result looks.
 */
async function textFitCheck(page) {
  const composition = request.composition;
  const length = request.parameter_schema?.properties?.text?.maxLength;
  if (!Number.isInteger(length) || length < 1)
    return { name: "text_fit", status: "failed", message: "A text effect must declare a text parameter with an integer maxLength." };
  const text = TEXT_FILLER.repeat(length);
  await page.evaluate((patch) => window.__imvTool.setParameters(patch), { text });
  let measured;
  try {
    measured = await measureTextFit(page, text);
  } finally {
    // Later tests and the cached report must see the configured parameters again.
    await page.evaluate((values) => window.__imvTool.setParameters(values), request.parameters ?? {});
    await page.evaluate(() => window.__imvTool.setFrame(0));
  }
  if (measured.error)
    return { name: "text_fit", status: "failed", message: `The component throws at frame ${measured.frame} with the text filled to maxLength ${length}: ${measured.error}`.slice(0, 6000) };
  if (!measured.shown)
    return { name: "text_fit", status: "failed", message: `With text filled to its maxLength (${length} characters) the component never renders any of that text visibly. The text parameter must be displayed as given.` };
  if (measured.fits)
    return { name: "text_fit", status: "passed", message: `The ${length}-character text is shown in full (measured on the ${measured.full} of ${measured.shown} frames that show it at its full size).` };
  return {
    name: "text_fit",
    status: "failed",
    message: `With text filled to its maxLength (${length} characters) the text is not shown in full: ${measured.problems.join("; ")}. `
      + `Long text must fit on the ${composition.width}x${composition.height} canvas at any length up to maxLength: lay it out from its content, wrapping onto several lines `
      + "(a bounded width with whiteSpace normal and wordBreak break-all for CJK, no fixed-height box with overflow hidden), and reduce the font size as the text gets longer "
      + "(derive it from the character count, never below 24px) while short text keeps the fontSize parameter. Fit as much text as possible; "
      + "Keep text maxLength exactly 100; if 100 characters still do not fit at 24px, adjust the layout and wrapping instead of lowering maxLength or clipping.",
  };
}

/**
 * Host-side fit of the text a render really passes, for one Sprite (`sprite.fit` items, `sprite.box` content box):
 * for each item, the largest whole font size from `min` up to
 * the requested one at which the text is shown in full on the item's own `frames`. Each item is mounted at the
 * `length` its Sprite plays in the render (which it reads as `durationInFrames`) and measured where its host
 * `transform` puts it on the canvas. The requested size is tried first, so text that already fits is untouched;
 * `fits: false` means even `min` does not fit. A component that throws, or never renders the text, is left alone
 * (the render reports its own failure). Sizes are assumed to fit monotonically, so a binary search.
 */
async function fitTexts(page, loadError, sprite) {
  if (loadError !== null) throw new Error(`Component runtime error: ${loadError}`);
  const canvas = sprite.canvas ?? request.composition;
  const box = sprite.box ?? { x: 0, y: 0, width: canvas.width, height: canvas.height };
  const visible = visibleWindow(canvas, request.composition);
  const fits = async (item, size) => {
    await page.evaluate(() => window.__imvTool.reset());
    await page.evaluate((frames) => window.__imvTool.setDuration(frames), item.length);
    await page.evaluate((values) => window.__imvTool.setSample(values, {}), { ...item.values, [item.size_key]: size });
    const measured = await measureTextFit(page, item.text, item.frames, hostPlacement(box, item.transform, visible));
    return measured.error !== null || measured.shown === 0 || measured.fits;
  };
  const answers = [];
  for (const item of sprite.fit) {
    const requested = item.values[item.size_key];
    let size = requested;
    let ok = await fits(item, requested);
    if (!ok) {
      ok = requested > item.min && await fits(item, item.min);
      if (ok) {
        // `low` fits: the answer lies in [low, requested - 1].
        let low = item.min;
        let high = requested - 1;
        while (low < high) {
          const middle = Math.ceil((low + high) / 2);
          if (await fits(item, middle)) low = middle; else high = middle - 1;
        }
        size = low;
      } else size = item.min;
    }
    answers.push({ index: item.index, fits: ok, size });
  }
  return answers;
}

/**
 * Fit mode: the texts of every text Sprite of one render, measured in one browser under one deadline. Each Sprite's
 * code as the render runs it (the sealed source, type-checked when it was published, or a video composition's
 * timing wrapper around it) is policy-checked again, bundled into its own host with its own `canvas` size and loaded
 * on the same page in turn. `result.fits` holds one list of answers per Sprite, in request order.
 */
async function fitAll() {
  await fs.symlink(`${rendererRoot}/node_modules`, `${root}/node_modules`).catch(() => undefined);
  const { server, url } = await serveBundle();
  let browser;
  try {
    const opened = await openPage(url);
    browser = opened.browser;
    result.fits = [];
    for (const sprite of request.sprites) {
      sourcePolicy(sprite.code);
      await fs.writeFile(`${root}/Template.tsx`, sprite.code, "utf8");
      // A Sprite lays out on its own canvas, which the video may scale; the page keeps the video's size, as when rendering.
      const { width, height } = sprite.canvas ?? request.composition;
      await bundleHost({
        defaults: sprite.default_parameters, configured: sprite.default_parameters, parameter_schema: sprite.parameter_schema,
        composition: { ...request.composition, width, height }, ...(sprite.box ? { box: sprite.box } : {}),
      });
      result.fits.push(await fitTexts(opened.page, await waitReady(opened.page), sprite));
    }
  } finally {
    if (browser) await browser.close({ silent: true });
    await new Promise((resolve) => server.close(resolve));
  }
}

function scriptPolicy(code) {
  if (/\b(?:import|export\s+(?!default))\b|\b(?:require|process|globalThis|self|window|document|navigator|location|fetch|XMLHttpRequest|WebSocket|Worker|importScripts|postMessage|Function|eval)\b/.test(code)) throw new Error("Test scripts may only use TestContext.");
}

function compileTest(code) {
  scriptPolicy(code);
  const compiled = ts.transpileModule(code, { reportDiagnostics: true, compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX } });
  if ((compiled.diagnostics ?? []).some((item) => item.category === ts.DiagnosticCategory.Error)) throw new Error(ts.flattenDiagnosticMessageText(compiled.diagnostics[0].messageText, "\n"));
  const transformed = compiled.outputText;
  const replaced = transformed.replace(/export\s+default\s+/, "const __imvTest = ");
  if (replaced === transformed || !/\b__imvTest\b/.test(replaced)) throw new Error("Test script must have a default export.");
  return `${replaced}\n;return __imvTest;`;
}

async function executeTest(page, test, timeoutMs) {
  const script = compileTest(test.code);
  await page.evaluate(() => window.__imvTool.reset());
  return page.evaluate(({ script, timeoutMs }) => window.__imvTool.runTest(script, timeoutMs), { script, timeoutMs });
}

/**
 * Publish-time boundary samples: mount each complete sample with its host transform and require the first,
 * middle and last frame to render without an error, at the preview length and at every length in
 * `request.lengths`. One check per sample, named `sample:<name>`.
 */
async function sampleChecks(page, loadError) {
  const lengths = [...new Set([request.composition.duration_frames, ...(request.lengths ?? [])])];
  for (const sample of request.samples) {
    const name = `sample:${sample.name}`;
    try {
      if (loadError !== null) throw new Error(`Component runtime error: ${loadError}`);
      await page.evaluate(() => window.__imvTool.reset());
      await page.evaluate((item) => window.__imvTool.setSample(item.values, item.transform), sample);
      for (const length of lengths) {
        await page.evaluate((frames) => window.__imvTool.setDuration(frames), length);
        for (const frame of edgeFrames(length)) {
          await page.evaluate((target) => window.__imvTool.setFrame(target), frame);
          const failure = await page.evaluate(() => window.__imvRuntimeError ?? null);
          if (failure) throw new Error(`frame ${frame} of a ${length}-frame render: ${failure}`);
        }
      }
      checks.push({ name, status: "passed", message: `First, middle and last frames rendered at ${lengths.join(", ")} frames.` });
    } catch (error) {
      checks.push({ name, status: "failed", message: String(error?.message ?? error).slice(0, 6000) });
    }
  }
  result.passed = checks.every((item) => item.status === "passed" || item.status === "pass");
}

async function main() {
  if (request.mode === "fit") return fitAll();
  const valid = await prepareSource();
  if (!valid) { result.passed = false; return; }
  if (request.mode === "code") { result.passed = checks.every((item) => item.status === "passed" || item.status === "pass"); return; }
  await bundleHost(hostConfig());
  const { server, url } = await serveBundle();
  let browser;
  try {
    const opened = await openPage(url);
    browser = opened.browser;
    const { page } = opened;
    const loadError = await waitReady(page);
    if (request.mode === "samples") {
      await sampleChecks(page, loadError);
      return;
    }
    let baseReady = true;
    try {
      if (loadError !== null) throw new Error(`Component runtime error: ${loadError}`);
      await page.evaluate(() => window.__imvTool.setFrame(0));
      await ensureHealthy(page);
      checks.push({ name: "default_render", status: "passed", message: "Default parameters mounted at frame 0." });
    } catch (error) {
      checks.push({ name: "default_render", status: "failed", message: String(error?.message ?? error).slice(0, 6000) });
      baseReady = false;
    }
    if (baseReady) {
      try {
        if (request.parameters) await page.evaluate((values) => window.__imvTool.setParameters(values), request.parameters);
        await page.evaluate(() => window.__imvTool.setFrame(0));
        await ensureHealthy(page);
        checks.push({ name: "configured_render", status: "passed", message: "Configured parameters mounted at frame 0." });
      } catch (error) {
        checks.push({ name: "configured_render", status: "failed", message: String(error?.message ?? error).slice(0, 6000) });
        baseReady = false;
      }
    } else {
      checks.push({ name: "configured_render", status: "not_run", message: "Default render failed." });
    }
    if (baseReady && request.transparency === true) checks.push(await transparencyCheck(page));
    if (baseReady && Array.isArray(request.lengths)) checks.push(await lengthsCheck(page));
    if (baseReady && request.text_fit === true) checks.push(await textFitCheck(page));
    if (!baseReady) {
      result.tests = (request.tests ?? []).map((test) => ({ name: test.name, status: "not_run", message: "Base render failed.", assertions: [] }));
      result.custom_tests_executed = 0;
      result.passed = false;
      return;
    }
    for (const test of request.tests ?? []) {
      const item = { name: test.name, status: "passed", assertions: [] };
      result.custom_tests_executed += 1;
      try {
        const output = await executeTest(page, test, Math.min(30000, Number(request.test_timeout_ms ?? 10000)));
        item.assertions = output.assertions;
        if (output.runtimeError) { item.status = "error"; item.message = `Component runtime error: ${output.runtimeError}`; }
        else if (!output.assertions.length) { item.status = "error"; item.message = "Test did not record an assertion."; }
        else if (output.assertions.some((assertion) => !assertion.passed)) { item.status = "failed"; item.message = output.error ?? "One or more assertions failed."; }
        else if (output.error) { item.status = "error"; item.message = output.error; }
      } catch (error) { item.status = "error"; item.message = String(error?.message ?? error).slice(0, 6000); }
      result.tests.push(item);
    }
  } finally {
    if (browser) await browser.close({ silent: true });
    await new Promise((resolve) => server.close(resolve));
  }
  result.checks = checks;
  result.passed = checks.every((item) => item.status === "passed" || item.status === "pass") && result.tests.every((item) => item.status === "passed");
}

try { await main(); }
catch (error) { checks.push({ name: "runtime", status: "error", message: String(error?.message ?? error).slice(0, 6000) }); result.passed = false; }
finally { await fs.writeFile(`${root}/renderer.json`, JSON.stringify(result), "utf8"); }
