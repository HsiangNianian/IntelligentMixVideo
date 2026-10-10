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
    const { line, character } = source.getLineAndCharacterOfPosition(node.getStart(source));
    super(`${message} (line ${line + 1}, column ${character + 1})`);
    this.position = { line, character, length: Math.max(1, node.getEnd() - node.getStart(source)) };
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

/** Build the public diagnostic for a thrown error; a policy violation also reports its zero-based range. */
function diagnosticFromError(error, code = null) {
  const position = error?.position;
  return {
    source: "contract",
    severity: "error",
    message: String(error?.message ?? error),
    ...(code ? { code: String(code) } : {}),
    ...(position ? { range: { start: { line: position.line, character: position.character }, end: { line: position.line, character: position.character + position.length } } } : {}),
  };
}

/** Convert the existing language service shape into the public LSP-like shape. */
function lspDiagnostics(items) {
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
  if (checked.diagnostics.length) result.diagnostics.push(...lspDiagnostics(checked.diagnostics));
  checks.push({ name: "typescript", status: checked.diagnostics.some((item) => item.severity === "error") ? "failed" : "pass", message: checked.detail ? checked.detail.slice(0, 6000) : "TypeScript checks passed." });
  result.diagnostics = result.diagnostics.filter((item, index, all) => index === all.findIndex((other) => JSON.stringify(other) === JSON.stringify(item)));
  return checked.diagnostics.every((item) => item.severity !== "error");
}

/** Build a page host with only the submitted component and immutable context. */
async function bundleHost() {
  const output = await build({
    entryPoints: [`${rendererRoot}/tool-validation-host.tsx`], bundle: true, write: false,
    platform: "browser", format: "iife", target: "es2022", jsx: "automatic", minify: false,
    define: { "process.env.NODE_ENV": '"production"' },
    plugins: [{
      name: "validation-input",
      setup(builder) {
        builder.onResolve({ filter: /^imv:template$/ }, () => ({ path: `${root}/Template.tsx` }));
        builder.onResolve({ filter: /^imv:config$/ }, () => ({ path: "config", namespace: "imv" }));
        builder.onLoad({ filter: /.*/, namespace: "imv" }, () => ({ contents: JSON.stringify({ defaults: request.default_parameters, configured: request.parameters ?? request.default_parameters, parameter_schema: request.parameter_schema, composition: request.composition }), loader: "json" }));
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
          res.end(`<!doctype html><html><body style="margin:0"><div id="root"></div><script src="/interactive.js"></script></body></html>`);
        }
      } catch (error) { res.writeHead(500); res.end(String(error)); }
    });
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve({ server, url: `http://127.0.0.1:${server.address().port}/` }));
  });
}

async function waitReady(page) {
  await page.goto({ url: page.__imvUrl, timeout: 30000, options: { waitUntil: "load" } });
  await page.evaluate(async () => {
    const end = Date.now() + 15000;
    while (!(window).__imvTool?.ready && Date.now() < end) await new Promise((resolve) => setTimeout(resolve, 20));
    if (!(window).__imvTool?.ready) throw new Error("Validation host did not initialize");
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

async function main() {
  const valid = await prepareSource();
  if (!valid) { result.passed = false; return; }
  if (request.mode === "code") { result.passed = checks.every((item) => item.status === "passed" || item.status === "pass"); return; }
  await bundleHost();
  const { server, url } = await serveBundle();
  let browser;
  try {
    // Chromium creates its profile under the attempt-local temporary directory.
    await fs.mkdir(os.tmpdir(), { recursive: true });
    browser = await openBrowser("chrome", { browserExecutable: request.browser, logLevel: "error", chromiumOptions: { gl: "swangle", enableMultiProcessOnLinux: false } });
    const pages = await browser.pages();
    const page = pages[0] ?? await browser.newPage({ context: () => null, logLevel: "error", indent: false, pageIndex: 0, onBrowserLog: null, onLog: () => undefined });
    page.__imvUrl = url;
    await waitReady(page);
    let baseReady = true;
    try {
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
