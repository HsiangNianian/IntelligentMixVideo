/**
 * Networkless browser behavior validator for PR76 components.  It performs
 * source-policy/type checks and drives a fixed Remotion Player through a small
 * page bridge; it never encodes media or invokes a visual reviewer. On request,
 * once every check has passed, it captures a few still frames for the Agent to
 * look at (`captureFrames`), or measures full-width subtitle capacity before segmentation.
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
      // `imv` is the host content runtime of free-form effects; the bundler inlines it like a local module.
      if (!["react", "react/jsx-runtime", "react/jsx-dev-runtime", "remotion", "imv"].includes(module)) throw new PolicyError(`Import not permitted: ${module}`, node, source);
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
  const attributes = [...Object.entries(request.default_parameters ?? {}).map(jsxAttribute), ROLE_HOST_PROPS[request.role] ?? ""].join(" ");
  await fs.writeFile(`${root}/contract.tsx`, `/** Host-generated default props call site. */\nimport React from 'react';\nimport Template from './Template';\nimport Export from './Export';\nconst element = <Template ${attributes} />;\nconst exported = <Export />;\n`, "utf8");
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

/**
 * Capture `request.capture.frames` of the component as it is configured (with `request.capture.parameters` over it,
 * such as sample keywords), composited onto neutral #808080 gray (the gray the model is told is not content) and cut
 * to `request.capture.box` at `request.capture.scale`, as `frames/frame-<n>.png` for the Agent to compare with what was
 * asked. The checks before may leave other values or another render length mounted, so these are put back first.
 */
/**
 * What each line and keyword hit of a text effect is doing on the current frame, measured from the running code rather than
 * seen in a picture: the centre and size of its text (transforms included), the opacity it is painted with (its own and its
 * ancestors') and the share of its characters painted at all. Copies of a text count as one subject.
 */
function measureSubjects() {
  const layer = document.querySelector("[data-imv-host-layer]")?.firstElementChild;
  if (!layer) return [];
  const opacityOf = (element) => {
    let opacity = 1;
    for (let item = element; item && layer.contains(item); item = item.parentElement) {
      const style = getComputedStyle(item);
      if (style.visibility === "hidden" || style.display === "none") return 0;
      opacity *= Number(style.opacity);
    }
    return opacity;
  };
  const measure = (node, name) => {
    const range = document.createRange();
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    let [left, top, right, bottom, opacity, painted, total, text] = [Infinity, Infinity, -Infinity, -Infinity, 0, 0, 0, ""];
    for (let item = walker.nextNode(); item; item = walker.nextNode()) {
      const value = item.nodeValue.replace(/\s+/g, "");
      if (!value) continue;
      total += value.length;
      text += value;
      range.selectNodeContents(item);
      const rect = range.getBoundingClientRect();
      const own = opacityOf(item.parentElement);
      if (!rect.width || !rect.height || own <= 0.01) continue;
      painted += value.length;
      opacity = Math.max(opacity, own);
      [left, top, right, bottom] = [Math.min(left, rect.left), Math.min(top, rect.top), Math.max(right, rect.right), Math.max(bottom, rect.bottom)];
    }
    if (!total) return null;
    const shown = painted > 0;
    return { name, text: text.slice(0, 24), x: shown ? (left + right) / 2 : null, y: shown ? (top + bottom) / 2 : null, width: shown ? right - left : 0, opacity, revealed: painted / total };
  };
  return [
    ...[...layer.querySelectorAll("[data-imv-tier]")].map((node) => measure(node, `line ${Number(node.getAttribute("data-imv-tier")) + 1}`)),
    ...[...layer.querySelectorAll("[data-imv-keyword]")].map((node, index) => measure(node, `keyword ${index + 1}`)),
  ].filter(Boolean);
}

async function captureFrames(page) {
  const { frames, box, scale } = request.capture;
  await fs.mkdir(`${root}/frames`, { recursive: true });
  await page.evaluate((frames) => window.__imvTool.setDuration(frames), request.composition.duration_frames);
  await page.evaluate((values) => window.__imvTool.setParameters(values), { ...(request.parameters ?? {}), ...(request.capture.parameters ?? {}) });
  const client = page._client();
  await client.send("Emulation.setDefaultBackgroundColorOverride", { color: { r: 128, g: 128, b: 128, a: 1 } });
  try {
    const measured = request.capture.trace ?? [];
    const motion = {};
    for (const frame of [...new Set([...frames, ...measured])].sort((a, b) => a - b)) {
      await page.evaluate((target) => window.__imvTool.setFrame(target), frame);
      if (measured.includes(frame)) motion[frame] = await page.evaluate(measureSubjects);
      if (!frames.includes(frame)) continue;
      const shot = await client.send("Page.captureScreenshot", { format: "png", fromSurface: true, captureBeyondViewport: true, clip: { ...box, scale } });
      // The renderer's protocol client wraps the CDP result as `{ value: { data } }`.
      await fs.writeFile(`${root}/frames/frame-${frame}.png`, Buffer.from((shot.value ?? shot).data, "base64"));
    }
    if (measured.length) await fs.writeFile(`${root}/frames/motion.json`, JSON.stringify(motion), "utf8");
  } finally {
    await client.send("Emulation.setDefaultBackgroundColorOverride", {});
  }
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

/**
 * Sample values of the props the host passes a text-block Preset (`ROLE_PROPS` in contracts.py), for the type check's
 * call site: a text Preset draws one tier from the host's line (`children`) or from its text, keyword segments and
 * `Keyword` itself, in its fonts; a keyword Preset draws one hit from the host's glyphs (`children`) or its `text`; an
 * effect Preset wraps children in the host's Box at a progress, as unit `index` of `count`. Props a Preset may leave undeclared are spread: TypeScript checks the type of
 * a spread prop the component declares (and its absence when required) but not an extra one, as at runtime, where the
 * host passes them all.
 */
const ROLE_HOST_PROPS = {
  text: 'fontFamily={"Noto Sans CJK SC"} fontWeight={700} fontSize={96} {...{text: "示例文字", segments: [{text: "示例", keyword: true}, {text: "文字", keyword: false}], Keyword: (keyword: {text: string}) => <span>{keyword.text}</span>, children: <span>示例文字</span>}}',
  keyword: '{...{text: "示例", children: <span>示例</span>}}',
  effect: 'progress={1} Box={(box: {style?: React.CSSProperties; children?: React.ReactNode}) => <div style={box.style}>{box.children}</div>} children={<span>示例文字</span>} {...{index: 0, count: 1, completedCount: 1}}',
};

/**
 * One default parameter as a JSX attribute of the type check's call site. Attributes are typed against each prop, so a
 * literal default such as "center" satisfies a prop typed "left" | "center" | "right"; an object spread would widen it
 * to string and refuse a correctly typed component. A key that is no identifier is spread on its own.
 */
function jsxAttribute([key, value]) {
  return /^[A-Za-z_$][\w$]*$/.test(key) ? `${key}={${JSON.stringify(value)}}` : `{...{${JSON.stringify(key)}: ${JSON.stringify(value)}}}`;
}

/** Filler glyph for the longest-text check: CJK glyphs are the widest, so they expose layout problems first. */
const TEXT_FILLER = "测";
/** A rendered text line shorter than this is unreadable on the 1080x1920 canvas (the smallest supported fontSize is 24). */
const MIN_LINE_HEIGHT = 20;
/**
 * The smallest font size the host shrinks a text to, whatever minimum the effect declares: below it lines fall under
 * `MIN_LINE_HEIGHT` and no longer count as readable, so sizes under it never fit (and the "smaller fits better"
 * assumption of the search in `fitTexts` would not hold). The generation check measures at this size too.
 */
const READABLE_FONT_SIZE = 24;

/**
 * The smallest fontSize at which every line of `values` stays readable: a two-tier text renders tier i at
 * fontSize * line{i}SizeRatio, so a tier smaller than fontSize raises the floor to `READABLE_FONT_SIZE` / its ratio.
 * Other texts (no ratios) keep `READABLE_FONT_SIZE`.
 */
function readableFontSize(values) {
  const ratios = Object.entries(values ?? {})
    .filter(([key, value]) => /^line\d+SizeRatio$/.test(key) && typeof value === "number" && value > 0)
    .map(([, value]) => value);
  return Math.ceil(READABLE_FONT_SIZE / Math.min(1, ...ratios));
}
/** Sub-pixel tolerance when comparing a text rectangle with a box. */
const FIT_TOLERANCE = 2;
/** Frames whose text area is at least this share of the largest one show the text at its full size. */
const FULL_SIZE_SHARE = 0.98;

/**
 * The host transform of one placement as `host-layer.tsx` applies it: the content box centre (cx, cy) is moved to
 * (x, y), then the Sprite is rotated by `rotation` degrees and scaled by `scale` around it. Missing fields keep the
 * Sprite where it is, so an empty transform is the identity. `visible` is the part of the Sprite's canvas the video
 * shows (see `visibleWindow`). With `nudge: true` (see `fitTexts`) the text leaving that window is not a problem: the
 * canvas `extent` of the text is measured so the caller can move it back inside.
 */
function hostPlacement(box, transform = {}, visible = null, nudge = false) {
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  const finite = (value, fallback) => (typeof value === "number" && Number.isFinite(value) ? value : fallback);
  return { cx, cy, x: finite(transform.x, cx), y: finite(transform.y, cy), scale: finite(transform.scale, 1), rotation: finite(transform.rotation, 0), visible, nudge };
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
 * and the `area` of the shown lines, which tells the frames that show the text at its full size. `extent` is the canvas
 * rectangle of all shown text after the transform and `view` the window of the canvas the video shows. With
 * `collectLines` it also names the top and bottom visual line (their canvas centre and their text), which `fitTexts`
 * uses to find where that line would sit as a single line. Render preflight (`mode: fit`) also counts laid-out
 * transparent text toward completeness: an operator may set one tier's opacity to zero. Only painted text contributes
 * geometry; generation still requires visible text.
 */
function textFitProblems(page, needle, placement, collectLines = false) {
  return page.evaluate((needle, minLine, tolerance, placement, countTransparentText, collectLines) => {
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
    /** Whether `element` is painted rather than faded out by its own or an ancestor's opacity. */
    const painted = (element) => {
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
    /**
     * The text of each visual line of `node`, paired with the line's left edge, when `collectLines` is on. Every
     * character is mapped to the line box that contains it, so keyword spans on the same line keep their own text and
     * the top and bottom lines can be measured on one line to find where a single line would sit.
     */
    const lineSegments = (node, rects) => {
      const value = node.nodeValue;
      const spans = rects.map(() => null);
      if (!value) return spans;
      const range = document.createRange();
      let offset = 0;
      for (const char of value) {
        const end = offset + char.length;
        range.setStart(node, offset);
        range.setEnd(node, end);
        offset = end;
        const box = Array.from(range.getClientRects()).find((rect) => rect.width > 0 && rect.height > 0);
        if (!box) continue;
        const middle = (box.top + box.bottom) / 2;
        let index = rects.findIndex((rect) => middle >= rect.top && middle <= rect.bottom);
        if (index < 0) {
          let overlap = -Infinity;
          rects.forEach((rect, candidate) => {
            const shared = Math.min(rect.bottom, box.bottom) - Math.max(rect.top, box.top);
            if (shared > overlap) { overlap = shared; index = candidate; }
          });
        }
        if (index < 0) continue;
        const span = spans[index] ?? { start: offset - char.length, end: offset };
        span.end = offset;
        spans[index] = span;
      }
      return spans.map((span, index) => span && { left: rects[index].left, text: value.slice(span.start, span.end) });
    };
    // Components may split the text into words or glyphs and drop the spaces between them.
    const compact = (value) => value.replace(/\s+/g, "");
    const wanted = compact(needle);
    /**
     * Whether the text nodes, in document order, still show all of `wanted`: each node continues the run where the
     * last one stopped, or holds the whole remaining text itself. A keyword's glow, outline or echo copy repeats a
     * fragment inside the line ("prefix keyword keyword suffix"), so joining every node would hide the complete line;
     * this rule still fails text that is cut, reordered or missing characters.
     */
    const covers = (parts) => {
      let at = 0;
      for (const part of parts) {
        if (at === wanted.length) return true;
        if (wanted.startsWith(part, at)) at += part.length;
        else if (part.includes(wanted.slice(at))) return true;
      }
      return at === wanted.length;
    };
    const walker = document.createTreeWalker(layer, NodeFilter.SHOW_TEXT);
    const problems = new Set();
    const parts = []; // each text node's text, in document order, for the completeness check below
    let area = 0;
    let readable = true;
    let extent = null; // canvas rectangle of all shown text after the transform
    // The visual text lines: rectangles whose vertical ranges overlap belong to the same line, so the pieces of a line
    // split into keyword spans still count once. A text block laid out for one line that wraps is then two lines.
    // `segments` holds the line's text pieces, ordered by their left edge when the top and bottom lines are needed.
    const rows = [];
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const shown = compact(node.nodeValue);
      if (!shown || !(wanted.includes(shown) || shown.includes(wanted)) || !node.parentElement || getComputedStyle(node.parentElement).visibility !== "visible") continue;
      const isPainted = painted(node.parentElement);
      if (!isPainted && !countTransparentText) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      const lines = lineBoxes(range, node.parentElement);
      // Text without a box (display: none, an empty or collapsed element) is not shown.
      if (!lines.length) continue;
      // Opacity can deliberately hide a tier without removing any text from its layout. Count it for render
      // preflight, but do not let its invisible boxes affect the full-size frames, clipping, or placement shift.
      // Completeness reads these nodes in order (`covers`); every painted copy keeps its own boxes for clipping,
      // size and placement below, so a decorative copy that overflows still fails.
      parts.push(shown);
      if (!isPainted) continue;
      const bounds = {
        left: Math.min(...lines.map((rect) => rect.left)), right: Math.max(...lines.map((rect) => rect.right)),
        top: Math.min(...lines.map((rect) => rect.top)), bottom: Math.max(...lines.map((rect) => rect.bottom)),
      };
      area += lines.reduce((sum, rect) => sum + (rect.right - rect.left) * rect.height, 0);
      const segments = collectLines ? lineSegments(node, lines) : null;
      for (const [index, rect] of lines.entries()) {
        // Spans of one line overlap almost completely; line boxes of two stacked lines only touch, so half the smaller
        // height tells them apart even when a taller keyword span shifts slightly.
        const same = rows.find((row) => Math.min(row.bottom, rect.bottom) - Math.max(row.top, rect.top) > Math.min(row.bottom - row.top, rect.height) / 2);
        const segment = segments?.[index];
        if (same) {
          same.left = Math.min(same.left, rect.left);
          same.right = Math.max(same.right, rect.right);
          same.top = Math.min(same.top, rect.top);
          same.bottom = Math.max(same.bottom, rect.bottom);
          if (segment) same.segments.push(segment);
        } else rows.push({
          left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom,
          segments: segment ? [segment] : [],
        });
      }
      const smallest = Math.min(...lines.map((rect) => rect.height));
      if (smallest < minLine) {
        readable = false;
        problems.add(`its lines are only ${Math.round(smallest)}px tall, too small to read`);
      }
      const corners = lines.flatMap((rect) => [place(rect.left, rect.top), place(rect.right, rect.top), place(rect.left, rect.bottom), place(rect.right, rect.bottom)]);
      const [left, right] = [Math.min(...corners.map(([x]) => x)), Math.max(...corners.map(([x]) => x))];
      const [top, bottom] = [Math.min(...corners.map(([, y]) => y)), Math.max(...corners.map(([, y]) => y))];
      extent = {
        left: Math.min(extent?.left ?? left, left), right: Math.max(extent?.right ?? right, right),
        top: Math.min(extent?.top ?? top, top), bottom: Math.max(extent?.bottom ?? bottom, bottom),
      };
      if (!placement.nudge && (left < view.x - tolerance || right > view.x + view.width + tolerance || top < view.y - tolerance || bottom > view.y + view.height + tolerance))
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
    if (!extent) return null;
    if (!covers(parts)) problems.add(`only part of it is rendered, not all ${wanted.length} characters`);
    const ordered = [...rows].sort((first, second) => first.top - second.top);
    /** The canvas centre and rendered text of one visual line, for measuring its single-line position. */
    const describe = (row) => row && {
      center: place((row.left + row.right) / 2, (row.top + row.bottom) / 2),
      text: row.segments.sort((first, second) => first.left - second.left).map((segment) => segment.text).join(""),
    };
    return {
      problems: [...problems], readable, area, extent, view, lines: rows.length,
      ...(collectLines ? { first: describe(ordered[0]), last: describe(ordered.at(-1)) } : {}),
    };
  }, needle, MIN_LINE_HEIGHT, FIT_TOLERANCE, placement, ["fit", "capacity"].includes(request.mode), collectLines);
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
 * `readable` is true only when no full-size frame has lines too small to read, so fitting can tell a floor that fails
 * for being unreadable from one that fails for overflow and need not assume the floor fits.
 * With `placement.nudge` the text leaving the window is no problem and `shift` is the smallest move of the placement
 * (`{x, y}` in canvas pixels, zero when none is needed) that brings the text of the middle one of the problem-free
 * full-size frames back inside it; when that text is larger than the window no move helps and it does not fit.
 * `lines` is the largest number of visual lines those full-size frames show, which `fitTexts` uses to keep an English
 * title or subtitle on one line, and `extent` is the canvas rectangle of the text in the middle problem-free one of
 * them (the middle full-size one when none is problem-free). With `collectLines`, `first` and `last` describe the top
 * and bottom visual line of that frame, and `frame` is its frame number so a one-line sample of an anchor line can be
 * measured at the same point of the animation.
 */
async function measureTextFit(page, needle, frames = sampleFrames(request.composition.duration_frames), placement = UNPLACED, collectLines = false) {
  const verdicts = [];
  for (const frame of frames) {
    await page.evaluate((target) => window.__imvTool.setFrame(target), frame);
    const error = await page.evaluate(() => window.__imvRuntimeError ?? null);
    if (error) return { error, frame, shown: verdicts.length, full: 0, fits: false, readable: false, lines: 0, extent: null, first: null, last: null, problems: [] };
    const verdict = await textFitProblems(page, needle, placement, collectLines);
    if (verdict) verdicts.push({ ...verdict, frame });
  }
  const largest = Math.max(0, ...verdicts.map((verdict) => verdict.area));
  const full = verdicts.filter((verdict) => verdict.area >= largest * FULL_SIZE_SHARE);
  const clean = full.filter((verdict) => !verdict.problems.length);
  let shift = null;
  if (placement.nudge && clean.length) {
    const { extent, view } = clean[Math.floor((clean.length - 1) / 2)];
    /** Move along one axis: the start edge into the window first, then the end edge back, or none when it is wider. */
    const along = (low, high, from, size) => (high - low > size + FIT_TOLERANCE ? null : low < from ? from - low : high > from + size ? from + size - high : 0);
    const [x, y] = [along(extent.left, extent.right, view.x, view.width), along(extent.top, extent.bottom, view.y, view.height)];
    if (x !== null && y !== null) shift = { x, y };
  }
  const fits = placement.nudge ? shift !== null : clean.length > 0;
  // The anchor line and its text come from a frame without problems, so an entrance or exit offset cannot move it.
  const middle = (clean.length ? clean : full)[Math.floor(((clean.length ? clean : full).length - 1) / 2)] ?? null;
  return {
    // Every full-size frame must be readable for the floor to fail only for its size; one tiny frame still needs a bigger size.
    // No full-size frame at all says nothing about size, so it is not reported as readable.
    error: null, shown: verdicts.length, full: full.length, fits,
    readable: full.length > 0 && full.every((verdict) => verdict.readable),
    lines: Math.max(0, ...full.map((verdict) => verdict.lines)), extent: middle?.extent ?? null,
    first: middle?.first ?? null, last: middle?.last ?? null, frame: middle?.frame ?? null, shift,
    problems: fits ? [] : [...new Set(full.flatMap((verdict) => verdict.problems))],
  };
}

/**
 * Mechanical check that a text effect shows its text in full: first the text parameter filled to its declared
 * maxLength at the smallest size the host shrinks text to (`readableFontSize` or the declared minimum, whichever is
 * larger: long text is made smaller at render time, see `fitTexts`), then the default text at the default size, which
 * is what a user sees first. The sampled frames are measured. A sample fails when every frame that shows the text at
 * its full size has a problem (a text cut to a slice of itself is one), and when the text is never visibly rendered.
 * A multi-line text (a `lineStartFrames` parameter of N items) is filled with N lines that share maxLength, and both
 * samples show every line from the first frame; its default lines must also pass `lineSyncProblem` and `lineJumpProblem`.
 * The host's composed still text instead patches `text_fit_parameters` (its cue wrappers off, so a tier can leave as
 * another arrives), and then must pass `cueHoldProblem`: a cue at its start state may never hide the tier it waits with.
 * An effect with a `keywords` parameter is also mounted with a hit in its default text: the hit, and any keyword effect
 * drawn on it, must not throw or keep the default text from showing in full, and in a text block the hit must keep
 * its tier's look (`keywordLookProblem`). The default text must also keep the fill it is painted with
 * (`coveredFillProblem`).
 * Apart from those paint comparisons this is a geometric measurement of the text boxes, not a visual review of how the result looks.
 */
async function textFitCheck(page) {
  const composition = request.composition;
  const length = request.parameter_schema?.properties?.text?.maxLength;
  if (!Number.isInteger(length) || length < 1)
    return { name: "text_fit", status: "failed", message: "A text effect must declare a text parameter with an integer maxLength." };
  const declared = request.parameter_schema?.properties?.fontSize?.minimum;
  const smallest = typeof declared === "number" ? Math.max(declared, readableFontSize(request.parameters)) : null;
  /** Mount the sample's parameters, measure its text, and put the configured parameters back for later checks. */
  const measureSample = async (patch, needle) => {
    await page.evaluate((values) => window.__imvTool.setParameters(values), patch);
    try {
      return await measureTextFit(page, needle);
    } finally {
      await page.evaluate((values) => window.__imvTool.setParameters(values), request.parameters ?? {});
      await page.evaluate(() => window.__imvTool.setFrame(0));
    }
  };
  const physicalLines = request.parameter_schema?.properties?.lineStartFrames?.maxItems;
  const bilingual = request.parameter_schema?.properties?.textEn?.type === "string";
  // 原文的长度在内容组之间分配；英文占自己的物理行，不能把四行资产误填成四组中文。
  const lines = bilingual ? Math.max(1, Math.floor(physicalLines / 2)) : physicalLines;
  const multi = Number.isInteger(lines) && lines > 1;
  // Every line is shown from frame 0, so the measurement sees the complete text.
  // Only the host supplies this patch for composed still text. A cue may intentionally remove a tier;
  // enter/exit and loop effects stay on so an inverted or permanently hidden transition still fails.
  const layout = request.text_fit_parameters ?? {};
  const allLines = { ...layout, ...(physicalLines > 1 ? { lineStartFrames: Array(physicalLines).fill(0) } : {}) };
  // The N lines share maxLength with their N - 1 line breaks: the first lines take one more character each when it
  // does not divide evenly, so the joined sample is exactly maxLength long.
  const characters = multi ? Math.max(lines, length - (lines - 1)) : length;
  const longest = multi
    ? Array.from({ length: lines }, (_, index) => TEXT_FILLER.repeat(Math.floor(characters / lines) + (index < characters % lines ? 1 : 0))).join("\n")
    : TEXT_FILLER.repeat(length);
  const where = `its maxLength (${length} characters${multi ? ` over ${lines} lines` : ""})${smallest !== null ? ` at the smallest fontSize ${smallest}` : ""}`;
  const measured = await measureSample({ text: longest, ...allLines, ...(smallest !== null ? { fontSize: smallest } : {}) }, longest);
  if (measured.error)
    return { name: "text_fit", status: "failed", message: `The component throws at frame ${measured.frame} with the text filled to ${where}: ${measured.error}`.slice(0, 6000) };
  if (!measured.shown)
    return { name: "text_fit", status: "failed", message: `With text filled to ${where} the component never renders any of that text visibly. The text parameter must be displayed as given.` };
  if (!measured.fits)
    return {
      name: "text_fit",
      status: "failed",
      message: `With text filled to ${where} the text is not shown in full: ${measured.problems.join("; ")}. `
        + `Long text must fit on the ${composition.width}x${composition.height} canvas: lay it out from its content, wrapping onto several lines `
        + "(a bounded width of about 90% of the canvas width with whiteSpace normal and wordBreak break-all for CJK, no fixed-height box with overflow hidden). "
        + "Render the text at the fontSize parameter as given and do not derive a size from the character count: the host lowers fontSize (and keywordSize in the same proportion) "
        + "for long text at render time. Keep text maxLength exactly 100; if 100 characters still do not fit at the smallest fontSize, adjust the layout and wrapping instead of lowering maxLength or clipping.",
    };
  const shortText = request.parameters?.text;
  // 双语的译文同样必须完整可见；只检查中文会让漏掉第二组英文的组件通过。
  if (bilingual && typeof request.parameters?.textEn === "string" && request.parameters.textEn.trim()) {
    const english = await measureSample(allLines, request.parameters.textEn);
    if (english.error || !english.shown || !english.fits)
      return { name: "text_fit", status: "failed", message: `The default English text is not shown in full: ${english.error || english.problems.join("; ") || "missing translation"}. Keep every bilingual group visible.` };
  }
  let shortNote = "";
  if (typeof shortText === "string" && shortText.trim()) {
    const preview = await measureSample(allLines, shortText);
    if (!preview.error && preview.shown && !preview.fits)
      return {
        name: "text_fit",
        status: "failed",
        message: `The default text "${shortText}" at the default fontSize is not shown in full: ${preview.problems.join("; ")}. `
          + "A user sees the default text first, so it must fit the canvas at the default fontSize without relying on the host shrinking it: adjust the default fontSize or the layout.",
      };
    shortNote = " The default text is shown in full at the default fontSize too.";
    const covered = await coveredFillProblem(page, allLines);
    if (covered) return { name: "text_fit", status: "failed", message: covered };
    const defaultLines = shortText.split("\n");
    // The host's composed tiers can have independent effects. Legacy components must still prove shared timing;
    // every component must keep the earlier tier in place when a later tier appears.
    const sync = !bilingual && multi && defaultLines.length === lines
      ? (request.text_fit_parameters === undefined ? await lineSyncProblem(page, defaultLines) : null)
        ?? (await lineJumpProblem(page, defaultLines, layout)) : null;
    if (sync) return { name: "text_fit", status: "failed", message: sync };
    // The patch switches the cue wrappers off for the layout measurement, so the hold they run over is measured with
    // every effect on: a cue that is already transformed at progress 0 would hide its tier for the whole hold. Only the
    // first line waits the whole hold, so a text that opens with an empty line has no tier to measure here.
    const hold = !bilingual && multi && defaultLines.length === lines && request.text_fit_parameters !== undefined && defaultLines[0].trim()
      ? await cueHoldProblem(page, defaultLines[0]) : null;
    if (hold) return { name: "text_fit", status: "failed", message: hold };
    // Templates pass keywords at render time, so the keyword hit (and any keyword effect drawn on it) must keep the text
    // whole: up to two characters opening the first non-empty default line are a hit every text effect can show
    // (the workspace preview shows the same sample keyword).
    const word = defaultLines.find((line) => line.trim())?.match(/\S{1,2}/u)?.[0] ?? "";
    if (request.parameter_schema?.properties?.keywords?.type === "array" && word) {
      const hit = await measureSample({ ...allLines, keywords: [word] }, shortText);
      if (hit.error)
        return { name: "text_fit", status: "failed", message: `The component throws at frame ${hit.frame} with keywords ["${word}"]: ${hit.error}`.slice(0, 6000) };
      if (!hit.shown || !hit.fits)
        return {
          name: "text_fit",
          status: "failed",
          message: `With keywords ["${word}"] the default text "${shortText}" is not shown in full${hit.shown ? `: ${hit.problems.join("; ")}` : ""}. `
            + "A keyword hit and any keyword effect drawn on it must stay inline and within the text's room, so the whole text still shows.",
        };
      const look = await keywordLookProblem(page, { ...allLines, keywords: [word] }, word, defaultLines.find((line) => line.trim()));
      if (look) return { name: "text_fit", status: "failed", message: look };
    }
  }
  return { name: "text_fit", status: "passed", message: `The ${length}-character text${multi ? ` over ${lines} lines` : ""} is shown in full${smallest !== null ? ` at the smallest fontSize ${smallest}` : ""} (measured on the ${measured.full} of ${measured.shown} frames that show it at its full size).${shortNote}` };
}

/**
 * Text whose fill is a background clipped to it (`background-clip: text` on its element or an ancestor, with a
 * transparent text fill) loses that fill under any text-shadow of its own: text shadows are painted above the
 * element's background, so the text shows only the shadow colour (audit: a gold gradient tier outlined with eight
 * text shadows rendered as a dark blob, work eb692e7e). Mounts `values` on the middle frame and returns the failure
 * message for the first such text, or null.
 */
async function coveredFillProblem(page, values) {
  await page.evaluate((patch) => window.__imvTool.setParameters(patch), values);
  try {
    await page.evaluate((frame) => window.__imvTool.setFrame(frame), Math.floor((request.composition.duration_frames - 1) / 2));
    return await page.evaluate(() => {
      const layer = document.querySelector("[data-imv-host-layer]")?.firstElementChild;
      if (!layer) return null;
      const clear = (color) => color === "transparent" || /^rgba\([^)]*,\s*0\)$/.test(color);
      const walker = document.createTreeWalker(layer, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const element = node.parentElement;
        if (!element || !node.nodeValue.trim()) continue;
        const style = getComputedStyle(element);
        if (style.textShadow === "none" || !clear(style.webkitTextFillColor)) continue;
        for (let item = element; item && layer.contains(item); item = item.parentElement) {
          const own = getComputedStyle(item);
          if (own.backgroundClip !== "text" && own.webkitBackgroundClip !== "text") continue;
          return `The text "${node.nodeValue.trim().slice(0, 20)}" takes its fill from a background-clip: text background (on a <${item.tagName.toLowerCase()}>) `
            + `with a transparent text fill, and also has text-shadow ${style.textShadow.slice(0, 160)}: text shadows are painted over that background, `
            + "so the text shows only its shadow colour. Keep text-shadow off text drawn with background-clip: text: outline it with -webkit-text-stroke "
            + "(paint-order: stroke fill), or draw the outline and shadow on a separate copy of the text behind it.";
        }
      }
      return null;
    });
  } finally {
    await page.evaluate((patch) => window.__imvTool.setParameters(patch), request.parameters ?? {});
    await page.evaluate(() => window.__imvTool.setFrame(0));
  }
}

/** Inherited text paint a keyword hit takes from its tier; with the keyword options off it equals that of the tier's other text. */
const KEYWORD_LOOK = [
  "color", "-webkit-text-fill-color", "-webkit-text-stroke-width", "-webkit-text-stroke-color", "paint-order", "text-shadow",
  "font-family", "font-size", "font-weight", "font-style", "letter-spacing",
];

/** The keyword options that leave a hit as its tier draws it, and the switch that leaves out a keyword Preset. */
const PLAIN_KEYWORD = {
  keywordBold: false, keywordItalic: false, keywordUnderline: false, keywordStrikeout: false, keywordColor: "", keywordSize: 0, keywordEffect: false,
};

/**
 * A text block hands every keyword hit to the host's `Keyword` (`data-imv-keyword`), which only adds the six keyword
 * options and the keyword Preset, so with those off (the parameters of `PLAIN_KEYWORD` the schema declares) a hit must
 * look like the rest of its line. A text Preset that puts its look on the spans of the other segments only, not on its
 * own block, draws the hit without it: a white stroke on the plain spans alone left the keyword of a template bare.
 * `values` (the default text with `word` as a keyword) are mounted on the middle frame, where every effect of the
 * block has fully entered and none has started to leave, and the hit's text is compared with the nearest other text
 * of `line` (the first non-empty default line): its inherited text paint (`KEYWORD_LOOK`, the stroke colour and paint
 * order only when either text is stroked), and the filter, decoration and text-clipped background the other text gets
 * from its own elements below the element that holds both. Returns the failure message, or null when the look is kept
 * or there is nothing to compare (no host keyword, or a line that is all keyword).
 */
async function keywordLookProblem(page, values, word, line) {
  const declared = request.parameter_schema?.properties ?? {};
  const plain = Object.fromEntries(Object.entries(PLAIN_KEYWORD).filter(([key]) => key in declared));
  await page.evaluate((patch) => window.__imvTool.setParameters(patch), { ...values, ...plain });
  try {
    await page.evaluate((target) => window.__imvTool.setFrame(target), Math.floor((request.composition.duration_frames - 1) / 2));
    if (await page.evaluate(() => window.__imvRuntimeError ?? null)) return null; // the fit measurement reports it
    const differences = await page.evaluate(({ line, inherited }) => {
      const layer = document.querySelector("[data-imv-host-layer]")?.firstElementChild;
      const hit = layer?.querySelector("[data-imv-keyword]");
      if (!hit) return null;
      const compact = (value) => value.replace(/\s+/g, "");
      const wanted = compact(line);
      /** The first non-blank text node under `root` that `keep` accepts. */
      const firstText = (root, keep) => {
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        for (let node = walker.nextNode(); node; node = walker.nextNode()) if (compact(node.nodeValue) && keep(node)) return node;
        return null;
      };
      const own = firstText(hit, () => true);
      if (!own) return null;
      // The lowest ancestor of the hit that also holds other text of its line, within the composed instance.
      let common = hit.parentElement;
      let other = null;
      for (; common && layer.contains(common); common = common.parentElement) {
        other = firstText(common, (node) => !node.parentElement?.closest("[data-imv-keyword]") && wanted.includes(compact(node.nodeValue)));
        if (other || common.hasAttribute("data-imv-instance")) break;
      }
      if (!other) return null;
      const mine = getComputedStyle(own.parentElement);
      const theirs = getComputedStyle(other.parentElement);
      const stroked = [mine, theirs].some((style) => style.getPropertyValue("-webkit-text-stroke-width") !== "0px");
      const found = inherited
        .filter((name) => stroked || !["-webkit-text-stroke-color", "paint-order"].includes(name))
        .filter((name) => mine.getPropertyValue(name) !== theirs.getPropertyValue(name))
        .map((name) => `${name} is ${mine.getPropertyValue(name)} instead of ${theirs.getPropertyValue(name)}`);
      /** Paint that is not inherited, added by the elements between `node` and the element holding both texts. */
      const added = (node) => {
        const paint = [];
        for (let element = node.parentElement; element && element !== common; element = element.parentElement) {
          const style = getComputedStyle(element);
          if (style.filter !== "none") paint.push(`filter ${style.filter}`);
          if (style.textDecorationLine !== "none") paint.push(`text-decoration ${style.textDecorationLine}`);
          if ([style.backgroundClip, style.webkitBackgroundClip].includes("text") && style.backgroundImage !== "none")
            paint.push(`background ${style.backgroundImage} clipped to the text`);
        }
        return paint;
      };
      const kept = new Set(added(own));
      for (const item of added(other)) if (!kept.has(item)) found.push(`it has no ${item}`);
      return found;
    }, { line, inherited: KEYWORD_LOOK });
    if (!differences?.length) return null;
    return `With keywords ["${word}"] and the keyword options off, the hit "${word}" is drawn without its tier's look: ${differences.join("; ")} (compared with the rest of its line). `
      + "A text Preset sets its look (colour, stroke, shadow, gradient fill, underline, font) on its own block, so every <Keyword> segment inside inherits it; "
      + "never put that look only on the spans of the other segments. The host styles a hit only with the six keyword options.";
  } finally {
    await page.evaluate((patch) => window.__imvTool.setParameters(patch), request.parameters ?? {});
    await page.evaluate(() => window.__imvTool.setFrame(0));
  }
}

/**
 * The lines of a multi-line text must form one effect: shown from frame 0 (every `lineStartFrames` at 0), the default
 * `lines` must be rendered by one composed instance and be shown and hidden together on every sampled frame, so
 * they share the layout (and its `lineGap`), the entrance and the exit. Returns the failure message, or null.
 */
async function lineSyncProblem(page, lines) {
  await page.evaluate((values) => window.__imvTool.setParameters(values), { lineStartFrames: lines.map(() => 0) });
  try {
    const owners = new Set();
    for (const frame of sampleFrames(request.composition.duration_frames)) {
      await page.evaluate((target) => window.__imvTool.setFrame(target), frame);
      if (await page.evaluate(() => window.__imvRuntimeError ?? null)) return null; // the fit measurement reports it
      const shown = [];
      for (const line of lines) shown.push((await textFitProblems(page, line, UNPLACED)) !== null);
      // The composed instance (`data-imv-instance`) each text node of a line belongs to.
      for (const owner of await page.evaluate((lines) => {
        const layer = document.querySelector("[data-imv-host-layer]")?.firstElementChild;
        if (!layer) return [];
        const compact = (value) => value.replace(/\s+/g, "");
        const wanted = lines.map(compact);
        const found = new Set();
        const walker = document.createTreeWalker(layer, NodeFilter.SHOW_TEXT);
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
          const text = compact(node.nodeValue);
          if (text && wanted.some((line) => line.includes(text) || text.includes(line)))
            found.add(node.parentElement?.closest("[data-imv-instance]")?.getAttribute("data-imv-instance") ?? "");
        }
        return [...found];
      }, lines)) owners.add(owner);
      if (owners.size > 1)
        return `The ${lines.length} lines of the multi-line text are rendered by different instances (${[...owners].join(", ")}). `
          + 'Render every line in one Preset instance: split text on "\\n" and stack the lines in one column spaced by the lineGap parameter, so they share one layout and one entrance and exit.';
      if (shown.some(Boolean) && !shown.every(Boolean)) {
        const numbers = (wanted) => shown.flatMap((value, index) => (value === wanted ? [index + 1] : [])).join(", ");
        return `With every lineStartFrames at 0, frame ${frame} shows line ${numbers(true)} but not line ${numbers(false)} of the default text. `
          + "Give every line the same animationIn/animationOut: line i enters from lineStartFrames[i] and all lines exit together, timed from useVideoConfig().durationInFrames instead of a constant frame count.";
      }
    }
    return null;
  } finally {
    await page.evaluate((values) => window.__imvTool.setParameters(values), request.parameters ?? {});
    await page.evaluate(() => window.__imvTool.setFrame(0));
  }
}

/** How far (canvas pixels, at least) an earlier line may move in the frame a later line appears beyond its own motion just before. */
const LINE_JUMP_TOLERANCE = 6;

/**
 * A cue effect runs from its own start state at progress 0 to its final state at 1, which may remove the text, so an
 * inverted cue (transformed at 0, intact at 1) hides its tier for the whole hold: the same mistake an exit cannot make.
 * The host's layout measurement switches cue wrappers off (`text_fit_parameters`), so this mounts the default lines
 * with the lower tier starting on the last frame, which keeps every cue at its start, and requires the first line to
 * still show in full around the middle of the preview, where entrances have finished and the exit has not started
 * (a looping effect may blink on that exact frame, so its two neighbours are accepted as well). Returns the failure
 * message, or null.
 */
async function cueHoldProblem(page, line) {
  const last = request.composition.duration_frames - 1;
  const middle = Math.floor(last / 2);
  await page.evaluate((values) => window.__imvTool.setParameters(values), { lineStartFrames: [0, last] });
  try {
    for (const frame of [middle, middle - 2, middle + 2]) {
      if (frame < 0 || frame > last) continue;
      await page.evaluate((target) => window.__imvTool.setFrame(target), frame);
      if (await page.evaluate(() => window.__imvRuntimeError ?? null)) return null; // the fit measurement reports it
      const verdict = await textFitProblems(page, line, UNPLACED);
      if (verdict && !verdict.problems.length) return null;
    }
    return `With the lower tier starting on the last frame, so a cue effect stays at its start for the whole hold, the first line `
      + `"${line}" of the default text is not shown in full around frame ${middle}. `
      + "A cue runs from its start state at progress 0 to its final state at 1 and may remove text only as it rises: it must not hide "
      + "its tier while that tier waits. Draw the removal in the cue's final state; an effect that is already transformed at progress 0 "
      + "belongs to an exit.";
  } finally {
    await page.evaluate((values) => window.__imvTool.setParameters(values), request.parameters ?? {});
    await page.evaluate(() => window.__imvTool.setFrame(0));
  }
}

/**
 * The lines of a multi-line text appear one by one, and an appearing line must not push the lines already shown:
 * a layout that only adds a line when it starts (not rendered before, in a centred column) makes every earlier line
 * jump in that frame. The default `lines` are started evenly over the preview (line i at i/(N+1) of it, after the
 * entrance of the line before has settled), and at the frame each later line starts, every earlier line may move no
 * more than it moved in the frame before plus `LINE_JUMP_TOLERANCE` (or a tenth of its height). Returns the failure
 * message, or null.
 */
async function lineJumpProblem(page, lines, layout = {}) {
  const step = Math.floor(request.composition.duration_frames / (lines.length + 1));
  if (step < 3) return null;
  const starts = lines.map((_, index) => index * step);
  await page.evaluate((values) => window.__imvTool.setParameters(values), { ...layout, lineStartFrames: starts });
  try {
    for (let index = 1; index < lines.length; index++) {
      const boxes = [];
      for (const frame of [starts[index] - 2, starts[index] - 1, starts[index]]) {
        await page.evaluate((target) => window.__imvTool.setFrame(target), frame);
        if (await page.evaluate(() => window.__imvRuntimeError ?? null)) return null; // the fit measurement reports it
        const shown = [];
        for (const line of lines.slice(0, index)) shown.push((await textFitProblems(page, line, UNPLACED))?.extent ?? null);
        boxes.push(shown);
      }
      for (let earlier = 0; earlier < index; earlier++) {
        const [first, before, after] = boxes.map((shown) => shown[earlier]);
        if (!first || !before || !after) continue;
        const moved = (from, to) => Math.max(...["left", "right", "top", "bottom"].map((edge) => Math.abs(to[edge] - from[edge])));
        const jump = moved(before, after);
        if (jump > moved(first, before) + Math.max(LINE_JUMP_TOLERANCE, (before.bottom - before.top) / 10))
          return `When line ${index + 1} of the default text appears (lineStartFrames ${JSON.stringify(starts)}), line ${earlier + 1} jumps ${Math.round(jump)}px `
            + `between frames ${starts[index] - 1} and ${starts[index]}. Keep the space of every line from the first frame: render a line that has not appeared yet `
            + "with opacity 0 instead of not rendering it, so the lines already shown stay where they are and each line only plays its own entrance.";
      }
    }
    return null;
  } finally {
    await page.evaluate((values) => window.__imvTool.setParameters(values), request.parameters ?? {});
    await page.evaluate(() => window.__imvTool.setFrame(0));
  }
}

/** Frames the wiring probe compares, in the order it tries them: the middle first, then towards both ends. */
function wiringFrames(total) {
  const last = total - 1;
  return [...new Set([0.5, 0.1, 0.9, 0.03, 0.97, 0.25, 0.75].map((share) => Math.round(last * share)))];
}

/**
 * Contract probes of a free-form effect (`Effect.tsx`, `request.effect_contract`), on the behaviour the bus, templates
 * and composition rely on. For a text effect: text injected instead of the sample must be what it shows; a keyword hit
 * must take the keyword options (colour, bold) with the keyword effect off; every default line must be shown in full on
 * the middle frame, where the bus freezes subtitles shorter than 1.5 s; and lowering `fontSize` must shrink the text,
 * which the host's fit relies on. For every kind each of its own style parameters (`wiring`, two extreme values) must
 * change at least one sampled frame, so the parameter panel has no dead controls. Returns one check listing every
 * problem with its fix.
 */
async function effectContractCheck(page) {
  const spec = request.effect_contract;
  const total = request.composition.duration_frames;
  const middle = Math.floor((total - 1) / 2);
  const problems = [];
  const mount = async (patch, frame) => {
    await page.evaluate((values) => window.__imvTool.setParameters(values), patch);
    await page.evaluate((target) => window.__imvTool.setFrame(target), frame);
    return page.evaluate(() => window.__imvRuntimeError ?? null);
  };
  const compact = (value) => String(value).replace(/\s+/g, "");
  try {
    if (spec.probe) {
      const failure = await mount({ ...spec.probe, keywords: [], ...(spec.starts ? { lineStartFrames: spec.starts } : {}) }, middle);
      if (failure) problems.push(`The effect throws with other text: ${failure}`);
      else {
        const shown = compact(await page.evaluate(() => document.querySelector("[data-imv-host-layer]")?.firstElementChild?.textContent ?? ""));
        const missing = spec.probe_lines.filter((line) => line && !shown.includes(compact(line)));
        const fixed = spec.lines.filter((line) => line && !spec.probe_lines.some((probe) => compact(probe).includes(compact(line))) && shown.includes(compact(line)));
        if (missing.length || fixed.length)
          problems.push(`With the text set to ${JSON.stringify(spec.probe_lines)} the effect ${missing.length ? `does not show ${JSON.stringify(missing)}` : ""}${missing.length && fixed.length ? " and " : ""}${fixed.length ? `still shows the sample ${JSON.stringify(fixed)}` : ""}. `
            + "Templates pass new text and keywords on every render: draw the lines useTextContent(props) returns (inside TextRoot), never the sample written into the code.");
      }
    }
    if (spec.keyword) {
      const failure = await mount({ ...spec.sample, keywords: [spec.keyword], keywordColor: "#13579B", keywordBold: true, ...("keywordEffect" in (request.parameter_schema?.properties ?? {}) ? { keywordEffect: false } : {}), ...(spec.starts ? { lineStartFrames: spec.starts } : {}) }, middle);
      const found = failure ? null : await page.evaluate((word) => {
        const layer = document.querySelector("[data-imv-host-layer]")?.firstElementChild;
        const compact = (value) => String(value).replace(/\s+/g, "");
        for (const hit of layer?.querySelectorAll("[data-imv-keyword]") ?? []) {
          if (!compact(hit.textContent).includes(compact(word))) continue;
          const walker = document.createTreeWalker(hit, NodeFilter.SHOW_TEXT);
          for (let node = walker.nextNode(); node; node = walker.nextNode()) {
            if (!compact(node.nodeValue)) continue;
            const style = getComputedStyle(node.parentElement);
            return { color: style.color, weight: Number(style.fontWeight) };
          }
        }
        return { color: null, weight: 0 };
      }, spec.keyword);
      if (failure) problems.push(`The effect throws with keywords ["${spec.keyword}"]: ${failure}`);
      else if (found.color === null)
        problems.push(`With keywords ["${spec.keyword}"] no keyword hit is marked: draw each run whose keyword is true inside <KeywordRun> from "imv" (LineText does it), so templates can style and measure the hit.`);
      else if (found.color !== "rgb(19, 87, 155)" || found.weight < 700)
        problems.push(`With keywords ["${spec.keyword}"], keywordColor #13579B and keywordBold on (keywordEffect off), the hit is drawn in ${found.color} at weight ${found.weight}. `
          + "Templates style keyword hits with the six keyword options: keep them on the text inside <KeywordRun> and draw the effect's own keyword look only while content.keywordEffect is on.");
    }
    if (spec.lines?.length) {
      const failure = await mount({ ...spec.sample, keywords: [], ...(spec.starts ? { lineStartFrames: spec.starts } : {}) }, middle);
      if (failure) problems.push(`The effect throws on its middle frame ${middle}: ${failure}`);
      else for (const [index, line] of spec.lines.entries()) {
        if (!line) continue;
        const verdict = await textFitProblems(page, line, UNPLACED);
        if (!verdict || verdict.problems.length) {
          problems.push(`On the middle frame ${middle} of the ${total}-frame preview line ${index + 1} "${line}" is not shown in full${verdict ? `: ${verdict.problems.join("; ")}` : ""}. `
            + "The bus freezes subtitles shorter than 1.5 s on this frame, so every line must be fully visible there: finish entrances before the middle and start exits after it.");
          break;
        }
      }
      if (!failure && spec.font_size && spec.font_size.min < spec.font_size.default) {
        const area = async (size) => {
          await mount({ ...spec.sample, keywords: [], fontSize: size, ...(spec.starts ? { lineStartFrames: spec.starts } : {}) }, middle);
          const verdict = await textFitProblems(page, spec.lines.find(Boolean), UNPLACED);
          return verdict?.extent ? (verdict.extent.right - verdict.extent.left) * (verdict.extent.bottom - verdict.extent.top) : null;
        };
        const [large, small] = [await area(spec.font_size.default), await area(spec.font_size.min)];
        if (large !== null && small !== null && small > large * 0.9)
          problems.push(`Lowering fontSize from ${spec.font_size.default} to ${spec.font_size.min} does not make the text smaller. The host lowers fontSize to fit long text: `
            + "draw every line at the font size TextRoot gives it (line.fontSize), without fixed font sizes, scale factors derived from the text length or fixed-size boxes.");
      }
    }
    const unwired = [];
    if (spec.wiring?.length) {
      const client = page._client();
      const shot = async () => {
        const image = await client.send("Page.captureScreenshot", { format: "png", fromSurface: true, captureBeyondViewport: true, clip: { x: 0, y: 0, width: request.composition.width, height: request.composition.height, scale: 0.25 } });
        return (image.value ?? image).data;
      };
      for (const item of spec.wiring) {
        let changes = false;
        for (const frame of wiringFrames(total)) {
          const shots = [];
          for (const value of item.values) {
            await mount({ ...spec.base, [item.name]: value }, frame);
            shots.push(await shot());
          }
          if (shots[0] !== shots[1]) { changes = true; break; }
        }
        if (!changes) unwired.push(`${item.name} (${item.values.map((value) => JSON.stringify(value)).join(" and ")})`);
        await mount(spec.base, 0);
      }
    }
    if (unwired.length)
      problems.push(`These parameters do not change any sampled frame between their two extremes: ${unwired.join(", ")}. `
        + "Every parameter in effect.json is a control in the parameter panel: read it in Effect.tsx where it draws what its title says, or remove it from effect.json.");
  } finally {
    await page.evaluate((values) => window.__imvTool.setParameters(values), request.parameters ?? {});
    await page.evaluate(() => window.__imvTool.setFrame(0));
  }
  return problems.length
    ? { name: "effect_contract", status: "failed", message: problems.join("\n").slice(0, 6000) }
    : { name: "effect_contract", status: "passed", message: "The effect shows the injected text and keywords, keeps its lines whole on the middle frame, shrinks with fontSize and uses every parameter." };
}

/** The smallest `keywordSize` a placement may set (0 follows the text size, which is not scaled). */
const KEYWORD_SIZE_MIN = 12;

/**
 * The parameter values of a fit item with its text at `size`. `keywordSize` is an absolute size (0 follows the text
 * size), so it shrinks in the same proportion as the text, or a keyword set larger than the text would stay at its
 * full size while the rest is made small and crowd it. It is never made larger and not below the smallest size
 * (`KEYWORD_SIZE_MIN`) a placement may set.
 */
function valuesAtSize(item, size) {
  const values = { ...item.values, [item.size_key]: size };
  const requested = item.values[item.size_key];
  const keyword = item.values.keywordSize;
  if (typeof keyword === "number" && keyword > 0 && size < requested)
    values.keywordSize = Math.min(keyword, Math.max(KEYWORD_SIZE_MIN, Math.round((keyword * size) / requested)));
  return values;
}

/**
 * Host-side fit of the text a render really passes, for one Sprite (`sprite.fit` items, `sprite.box` content box):
 * for each item, the largest whole font size from its floor (`min`, at least `readableFontSize`) up to
 * the requested one at which the text is shown in full on the item's own `frames`. Each item is mounted at the
 * `length` its Sprite plays in the render (which it reads as `durationInFrames`) and measured where its host
 * `transform` puts it on the canvas. The requested size is tried first, so text that already fits is untouched;
 * a component that throws, or never renders the text, is left alone (the render reports its own failure). Sizes are
 * searched above the smallest actually readable size, so a small keyword cannot make the floor look too large.
 * When even that floor leaves the text outside the window the video
 * shows (a long title placed at the top edge), the placement is moved the shortest way back inside at `min`:
 * the answer's `shift` is that move in canvas pixels. `fits: false` means the text is too long even for that. When the
 * size is lowered the answer also carries the matching `keyword_size` (see `valuesAtSize`).
 *
 * An item with `one_line` (an English title or subtitle, which is laid out for one line and would otherwise wrap into
 * the other language's text) is fitted to the largest size that also shows it on one line, shrinking it as needed.
 * This is best effort: a text that cannot be one line at the readable floor keeps the largest size that fits it
 * wrapped, so a long translation is still shown in full. An item with `grow` (a subtitle's `up` or `down`) that still
 * wraps answers `grow_shift`: the line nearest the other language (the first line for `down`, the last for `up`) is
 * measured again on one line, and the placement moves vertically so that line sits where the single line would. This is
 * exact for any vertical alignment of the effect (top, centre or bottom), not only for centred text; the move is
 * clamped to the part of the canvas the video shows.
 */
async function fitTexts(page, loadError, sprite) {
  if (loadError !== null) throw new Error(`Component runtime error: ${loadError}`);
  const canvas = sprite.canvas ?? request.composition;
  const box = sprite.box ?? { x: 0, y: 0, width: canvas.width, height: canvas.height };
  const visible = visibleWindow(canvas, request.composition);
  /**
   * The item's values with the visible text replaced. A composition fills the same text into every text parameter path,
   * so every exact match is replaced; keywords stay and are applied where the sample still contains them.
   */
  const valuesWithText = (item, size, text) => {
    const replace = (value) => {
      if (typeof value === "string") return value === item.text ? text : value;
      if (Array.isArray(value)) return value.map(replace);
      if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([key, child]) => [key, replace(child)]));
      return value;
    };
    return replace(valuesAtSize(item, size));
  };
  const measure = async (item, size, nudge, collectLines = false) => {
    await page.evaluate(() => window.__imvTool.reset());
    await page.evaluate((frames) => window.__imvTool.setDuration(frames), item.length);
    await page.evaluate((values) => window.__imvTool.setSample(values, {}), valuesAtSize(item, size));
    return measureTextFit(page, item.text, item.frames, hostPlacement(box, item.transform, visible, nudge), collectLines);
  };
  /** Mount a one-line sample of the same values at `size` and measure where that line sits on the canvas. */
  const measureSample = async (item, size, text, frames) => {
    await page.evaluate(() => window.__imvTool.reset());
    await page.evaluate((value) => window.__imvTool.setDuration(value), item.length);
    await page.evaluate((values) => window.__imvTool.setSample(values, {}), valuesWithText(item, size, text));
    return measureTextFit(page, text, frames, hostPlacement(box, item.transform, visible, false));
  };
  /** A component that throws or never renders its text is left alone; the render reports that itself. */
  const usable = (measured) => measured.error !== null || measured.shown === 0 || measured.fits;
  const answers = [];
  for (const item of sprite.fit) {
    /** One measurement per size: the size search and the subtitle shift read the same result. */
    const measuredSizes = new Map();
    const measureSize = async (size) => {
      if (!measuredSizes.has(size)) measuredSizes.set(size, await measure(item, size, false));
      return measuredSizes.get(size);
    };
    const requested = item.values[item.size_key];
    // Never below what is readable, and never above what was asked for.
    let floor = Math.min(requested, Math.max(item.min, readableFontSize(item.values)));
    // Turning this off when the text cannot be one line makes `accepts` check the wrapped fit instead.
    let oneLine = item.one_line === true;
    const accepts = (measured) => usable(measured) && (!oneLine || measured.lines <= 1);
    let size = requested;
    const atRequested = await measureSize(requested);
    let ok = accepts(atRequested);
    if (!ok) {
      let atFloor = floor < requested ? await measureSize(floor) : atRequested;
      // The declared floor may make a smaller keyword or a Preset's own scaled text unreadable. Fit is then not
      // monotonic: the floor fails for being too small while the request fails for being too large. First find
      // the lowest actually readable size; only above it does the usual "smaller fits better" search apply.
      if (!usable(atFloor) && !atFloor.readable && atRequested.readable) {
        let low = floor;
        let high = Math.floor(requested);
        while (low < high) {
          const middle = Math.floor((low + high) / 2);
          if ((await measureSize(middle)).readable) high = middle; else low = middle + 1;
        }
        floor = low;
        atFloor = await measureSize(floor);
      }
      const floorOK = accepts(atFloor);
      if (oneLine && !floorOK && atRequested.fits) {
        // It cannot be one line at any readable size: keep the text whole, wrapped, at the size asked for.
        ok = true;
      } else {
        // One line where possible; otherwise the normal wrapped fit below.
        if (!floorOK) oneLine = false;
        ok = accepts(atFloor);
        if (ok) {
          // `floor` fits: the answer lies in [floor, requested - 1].
          let low = floor;
          let high = requested - 1;
          while (low < high) {
            const middle = Math.ceil((low + high) / 2);
            if (accepts(await measureSize(middle))) low = middle; else high = middle - 1;
          }
          size = low;
        } else size = floor;
      }
    }
    let shift = null;
    if (!ok) {
      const nudged = await measure(item, size, true);
      ok = nudged.fits;
      shift = nudged.shift;
    }
    // An English subtitle (`down`) keeps its first line and grows below; a Chinese one (`up`) keeps its last line and
    // grows above. Measure that line and the same text on one line, then move the placement so the line sits exactly
    // where the single line would, whatever vertical alignment the effect uses. Never move it past the shown canvas.
    let growShift = null;
    // A rotated subtitle adds its lines along the rotated direction; moving only host y would not keep the nearest line
    // away from the other language. Leave that placement on its original layout rather than moving it the wrong way.
    const upright = Math.abs(item.transform.rotation ?? 0) < 0.5;
    if (item.grow && ok && upright) {
      const measured = await measureSize(size);
      // A text that throws or shows nothing here is reported by the render; a sample measurement is only an improvement.
      if (!measured.error && measured.shown > 0 && measured.lines > 1 && measured.extent) {
        let anchored = false;
        const detailed = await measure(item, size, false, true);
        if (!detailed.error && detailed.shown > 0 && detailed.lines > 1 && detailed.extent) {
          const anchor = item.grow === "down" ? detailed.first : detailed.last;
          // Only the anchor line's own text keeps its style. If the row text is not the start (for `down`) or end (for
          // `up`) of the passed text (a duplicated layer or reordered spans), use one character on that line instead.
          const edge = Array.from(item.text.trim())[item.grow === "down" ? 0 : -1];
          const exact = anchor?.text && (item.grow === "down" ? item.text.startsWith(anchor.text) : item.text.endsWith(anchor.text));
          const candidates = [...new Set([exact ? anchor.text : edge, edge].filter(Boolean))];
          let sample = null;
          for (const text of candidates) {
            let single;
            try {
              single = await measureSample(item, size, text, detailed.frame == null ? item.frames : [detailed.frame]);
            } catch {
              continue;
            }
            if (!single.error && single.shown > 0 && single.lines <= 1 && single.extent) { sample = single; break; }
          }
          // The nudge (when the floor size still left the canvas) moves the text too, so clamp against where it will sit.
          const nudged = { top: detailed.extent.top + (shift?.y ?? 0), bottom: detailed.extent.bottom + (shift?.y ?? 0) };
          if (anchor?.center && sample?.extent) {
            const middle = (sample.extent.top + sample.extent.bottom) / 2;
            let move = middle - anchor.center[1];
            // Keep the whole multi-line text inside the window the video shows, without ever reversing the direction.
            if (item.grow === "down") move = Math.max(0, Math.min(move, visible.y + visible.height - nudged.bottom));
            else move = Math.min(0, Math.max(move, visible.y - nudged.top));
            growShift = Math.round(move);
            anchored = true;
            if (growShift === 0) growShift = null;
          }
        }
        if (!anchored) {
          // A component that cannot render its anchor line as one line keeps the old centred-growth estimate.
          const nudged = { top: measured.extent.top + (shift?.y ?? 0), bottom: measured.extent.bottom + (shift?.y ?? 0) };
          const height = measured.extent.bottom - measured.extent.top;
          const grow = (height * (measured.lines - 1)) / (2 * measured.lines);
          if (item.grow === "down") growShift = Math.min(grow, Math.max(0, visible.y + visible.height - nudged.bottom));
          else if (item.grow === "up") growShift = -Math.min(grow, Math.max(0, nudged.top - visible.y));
          if (growShift !== null) growShift = Math.round(growShift);
          if (growShift === 0) growShift = null;
        }
      }
    }
    const keywordSize = valuesAtSize(item, size).keywordSize;
    const final = await measureSize(size);
    answers.push({
      index: item.index, fits: ok, size, lines: final.lines, ...(keywordSize !== item.values.keywordSize ? { keyword_size: keywordSize } : {}),
      ...(shift && (shift.x || shift.y) ? { shift } : {}), ...(growShift ? { grow_shift: growShift } : {}),
      ...(!ok ? { problems: final.problems } : {}),
    });
  }
  return answers;
}

/**
 * Measure full-width characters per intended line at the saved size and position. The caller supplies schema-valid
 * plain/keyword samples; measure both so a larger keyword cannot silently reduce capacity. Binary search assumes
 * ordinary text layout grows with length, as render preflight does with font size. It samples full-size preview
 * frames, includes actual font metrics, letter spacing and transforms, and never shrinks or nudges the placement.
 * These probes precede real text/timing; render preflight remains authoritative for the final subtitles.
 */
async function textCapacities(page, loadError, sprite) {
  if (loadError !== null) throw new Error(`Component runtime error: ${loadError}`);
  const canvas = sprite.canvas ?? request.composition;
  const box = sprite.box ?? { x: 0, y: 0, width: canvas.width, height: canvas.height };
  const visible = visibleWindow(canvas, request.composition);
  const answers = [];
  for (const item of sprite.fit) {
    const placement = hostPlacement(box, item.transform, visible);
    /** A sample fits only when every style variant is complete, readable and has no extra wrapped lines. */
    const fits = async (sample) => {
      for (const values of sample.values) {
        await page.evaluate(() => window.__imvTool.reset());
        await page.evaluate((length) => window.__imvTool.setDuration(length), item.length);
        await page.evaluate((value) => window.__imvTool.setSample(value, {}), values);
        const measured = await measureTextFit(page, sample.text, item.frames, placement);
        if (measured.error || measured.shown === 0) throw new Error("Subtitle capacity probe could not measure rendered text");
        if (!measured.fits || measured.lines > item.lines) return false;
      }
      return true;
    };
    let low = 0;
    let high = item.samples.length;
    // [0, low) fits and [high, length) does not; zero means even the shortest valid sample cannot fit.
    while (low < high) {
      const middle = Math.floor((low + high) / 2);
      if (await fits(item.samples[middle])) low = middle + 1; else high = middle;
    }
    answers.push({ index: item.index, capacity: low ? item.samples[low - 1].chars : 0 });
  }
  return answers;
}

/**
 * Fit/capacity mode: the texts of every text Sprite, measured in one browser under one deadline. Each Sprite's
 * code as the render runs it (the sealed source, type-checked when it was published, or a video composition's
 * timing wrapper around it) is policy-checked again, bundled into its own host with its own `canvas` size and loaded
 * on the same page in turn. The result holds one list of answers per Sprite, in request order.
 */
async function fitAll() {
  await fs.symlink(`${rendererRoot}/node_modules`, `${root}/node_modules`).catch(() => undefined);
  const { server, url } = await serveBundle();
  let browser;
  try {
    const opened = await openPage(url);
    browser = opened.browser;
    const answers = [];
    result[request.mode === "capacity" ? "capacities" : "fits"] = answers;
    for (const sprite of request.sprites) {
      sourcePolicy(sprite.code);
      await fs.writeFile(`${root}/Template.tsx`, sprite.code, "utf8");
      // A Sprite lays out on its own canvas, which the video may scale; the page keeps the video's size, as when rendering.
      const { width, height } = sprite.canvas ?? request.composition;
      await bundleHost({
        defaults: sprite.default_parameters, configured: sprite.default_parameters, parameter_schema: sprite.parameter_schema,
        composition: { ...request.composition, width, height, ...(sprite.canvas?.fps ? { fps: sprite.canvas.fps } : {}) }, ...(sprite.box ? { box: sprite.box } : {}),
      });
      const measure = request.mode === "capacity" ? textCapacities : fitTexts;
      answers.push(await measure(opened.page, await waitReady(opened.page), sprite));
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
  if (["fit", "capacity"].includes(request.mode)) return fitAll();
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
    if (baseReady && request.effect_contract) checks.push(await effectContractCheck(page));
    if (baseReady && request.capture && checks.every((item) => item.status === "passed" || item.status === "pass")) await captureFrames(page);
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
