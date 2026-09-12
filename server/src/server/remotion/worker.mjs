/** Isolated renderer: format and check one TSX candidate, then render evidence into /work. */
import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import ts from "typescript";
import prettier from "prettier";
import { bundle } from "@remotion/bundler";
import { exportTemplate, buildPresentation } from "./presentation.mjs";
import {
  openBrowser,
  selectComposition,
  renderStill,
  renderMedia,
} from "@remotion/renderer";

const root = "/work";
const request = JSON.parse(await fs.readFile(`${root}/request.json`, "utf8"));
const checks = [];
const result = {
  checks,
  frames: request.frames,
  runtime: { node: process.version, remotion: "4.0.523" },
};

/** Record only checks which actually executed; missing checks prevent host acceptance. */
async function check(name, operation) {
  try {
    const value = await operation();
    checks.push({
      name,
      status: "pass",
      detail: "Verified by isolated renderer.",
    });
    return value;
  } catch (error) {
    checks.push({
      name,
      status: "fail",
      detail: String(error.message).slice(0, 6000),
    });
    throw error;
  }
}

/** Limit generated modules to deterministic, local React/Remotion rendering capabilities. */
function sourcePolicy(code) {
  const source = ts.createSourceFile(
    "Template.tsx",
    code,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  );
  const forbidden = new Set([
    "eval",
    "Function",
    "require",
    "process",
    "global",
    "globalThis",
    "window",
    "document",
    "navigator",
    "fetch",
    "XMLHttpRequest",
    "WebSocket",
    "Worker",
    "Date",
    "setTimeout",
    "setInterval",
    "requestAnimationFrame",
    "useEffect",
    "useLayoutEffect",
    "useState",
    "useReducer",
    "useRef",
    "useImperativeHandle",
    "random",
    "dangerouslySetInnerHTML",
    "src",
    "href",
    "url",
    "registerRoot",
    "staticFile",
    "Img",
    "Video",
    "Audio",
    "OffthreadVideo",
    "IFrame",
    "delayRender",
    "continueRender",
    "cancelRender",
  ]);
  const react = new Set([
    "React",
    "CSSProperties",
    "FC",
    "Fragment",
    "useMemo",
    "useCallback",
    "memo",
  ]);
  const remotion = new Set([
    "AbsoluteFill",
    "Sequence",
    "Series",
    "useCurrentFrame",
    "useVideoConfig",
    "interpolate",
    "interpolateColors",
    "spring",
    "Easing",
  ]);
  /** Inspect every syntax node; the OS sandbox remains the execution boundary. */
  function visit(node) {
    if (ts.isImportDeclaration(node)) {
      const module = node.moduleSpecifier.text;
      if (!["react", "remotion"].includes(module))
        throw new Error(`Import not permitted: ${module}`);
      const clause = node.importClause;
      if (
        !clause ||
        (clause.name && module !== "react") ||
        (clause.namedBindings && !ts.isNamedImports(clause.namedBindings))
      )
        throw new Error(
          "Use named imports; only React may be a default import.",
        );
      for (const element of clause.namedBindings?.elements ?? []) {
        if (
          !(module === "react" ? react : remotion).has(
            (element.propertyName ?? element.name).text,
          )
        )
          throw new Error("Unsupported imported capability.");
      }
    }
    if (ts.isIdentifier(node) && forbidden.has(node.text))
      throw new Error(`Unsupported capability: ${node.text}`);
    if (
      ts.isCallExpression(node) &&
      node.expression.kind === ts.SyntaxKind.ImportKeyword
    )
      throw new Error("Dynamic imports are not permitted.");
    if (ts.isExportDeclaration(node) && node.moduleSpecifier)
      throw new Error("Re-exports are not permitted.");
    if (
      (ts.isStringLiteralLike(node) ||
        ts.isNoSubstitutionTemplateLiteral(node)) &&
      /(?:https?:|data:|file:|url\s*\()/i.test(node.text)
    )
      throw new Error("External and embedded assets are not permitted.");
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      if (
        [
          "img",
          "video",
          "audio",
          "iframe",
          "script",
          "object",
          "embed",
          "canvas",
          "style",
          "link",
          "image",
          "foreignObject",
        ].includes(node.tagName.getText(source))
      )
        throw new Error("Render text and its decorations only.");
    }
    ts.forEachChild(node, visit);
  }
  visit(source);
}

/** Typecheck candidate and the actual default-props call site against installed declarations. */
function typecheck() {
  const program = ts.createProgram(
    [`${root}/Template.tsx`, `${root}/Export.tsx`, `${root}/contract.tsx`],
    {
      strict: true,
      noEmit: true,
      skipLibCheck: true,
      jsx: ts.JsxEmit.ReactJSX,
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
      moduleResolution: ts.ModuleResolutionKind.Bundler,
      esModuleInterop: true,
      types: ["react", "react-dom"],
    },
  );
  const errors = ts.getPreEmitDiagnostics(program);
  if (errors.length)
    throw new Error(
      ts.formatDiagnostics(errors, {
        getCurrentDirectory: () => root,
        getCanonicalFileName: (name) => name,
        getNewLine: () => "\n",
      }),
    );
}

/** Load managed fonts before Remotion captures any frame; user code owns no loader side effects. */
function entrypoint() {
  const config = JSON.stringify(request.config);
  const composition = request.composition;
  return `/** Trusted preview host waits for both managed font weights. */
import React from 'react';
import {Composition, registerRoot, delayRender, continueRender, cancelRender, staticFile} from 'remotion';
import Template from './Template';
import Export from './Export';
const handle = delayRender('managed fonts');
Promise.all([400,700].map(async weight => {
  const face = new FontFace('Noto Sans CJK SC', 'url(' + staticFile('font-' + weight + '.ttc') + ')', {weight:String(weight)});
  document.fonts.add(await face.load());
})).then(() => continueRender(handle)).catch(cancelRender);
/** Compose the transparent candidate at its declared dimensions and frame rate. */
const Root = () => <><Composition id="Template" component={Template} defaultProps={${config}} width={${composition.width}} height={${composition.height}} fps={${composition.fps}} durationInFrames={${composition.duration_in_frames}} /><Composition id="Export" component={Export} defaultProps={{}} width={${composition.width}} height={${composition.height}} fps={${composition.fps}} durationInFrames={${composition.duration_in_frames}} /></>;
registerRoot(Root);
`;
}

/** Execute stages sequentially and always leave bounded diagnostic evidence after failure. */
async function main() {
  let browser;
  try {
    const code = await check("source_policy", async () => {
      sourcePolicy(request.code);
      return prettier.format(request.code, { parser: "typescript" });
    });
    await fs.writeFile(`${root}/Template.tsx`, code);
    await check("export_source", async () => {
      const exported = await exportTemplate(
        code,
        request.config,
        request.composition,
      );
      await fs.writeFile(`${root}/Export.tsx`, exported);
    });
    await fs.symlink("/renderer/node_modules", `${root}/node_modules`);
    await fs.writeFile(
      `${root}/contract.tsx`,
      `/** Verify source props and default export call sites. */\nimport React from 'react';\nimport Template from './Template';\nimport Export from './Export';\nconst props = ${JSON.stringify(request.config)};\nconst element = <Template {...props} />;\nconst exported = <Export />;\n`,
    );
    await fs.writeFile(`${root}/entry.tsx`, entrypoint());
    await check("typescript", async () => typecheck());
    await check("interactive_bundle", () =>
      buildPresentation(root, request.config, request.composition),
    );
    const serveUrl = await check("bundle", () =>
      bundle({
        entryPoint: `${root}/entry.tsx`,
        outDir: `${root}/bundle`,
        publicDir: `${root}/public`,
        webpackOverride: (config) => ({ ...config, cache: false }),
      }),
    );
    browser = await openBrowser("chrome", {
      browserExecutable: request.browser,
      logLevel: "error",
      chromiumOptions: { gl: "swangle" },
    });
    const composition = await selectComposition({
      serveUrl,
      id: "Template",
      inputProps: request.config,
      puppeteerInstance: browser,
    });
    const options = {
      serveUrl,
      composition,
      inputProps: request.config,
      puppeteerInstance: browser,
      logLevel: "error",
      timeoutInMilliseconds: 30000,
    };
    await check("render", async () => {
      for (const frame of request.frames)
        await renderStill({
          ...options,
          frame,
          imageFormat: "png",
          output: `${root}/frame-${frame}.png`,
        });
      await renderMedia({
        ...options,
        codec: "h264",
        pixelFormat: "yuv420p",
        concurrency: 2,
        outputLocation: `${root}/preview.mp4`,
      });
    });
    await check("parameter_render", async () => {
      // Each experiment changes one host-selected prop while preserving source and all other props.
      for (const [index, probe] of request.probes.entries()) {
        await renderStill({
          ...options,
          composition: { ...composition, props: probe.config },
          inputProps: probe.config,
          frame: probe.frame,
          imageFormat: "png",
          output: `${root}/probe-${index}.png`,
        });
      }
    });
    await check("determinism", async () => {
      const frame = request.frames[Math.floor(request.frames.length / 2)];
      await renderStill({
        ...options,
        frame,
        imageFormat: "png",
        output: `${root}/repeat.png`,
      });
      const first = await fs.readFile(`${root}/frame-${frame}.png`);
      const second = await fs.readFile(`${root}/repeat.png`);
      if (
        crypto.createHash("sha256").update(first).digest("hex") !==
        crypto.createHash("sha256").update(second).digest("hex")
      )
        throw new Error("Repeated frame changed after out-of-order rendering.");
    });
    await check("export_defaults", async () => {
      const exportedComposition = await selectComposition({
        serveUrl,
        id: "Export",
        inputProps: {},
        puppeteerInstance: browser,
      });
      await renderStill({
        ...options,
        composition: exportedComposition,
        inputProps: {},
        frame: request.frames[0],
        imageFormat: "png",
        output: `${root}/export-default.png`,
      });
      const original = await fs.readFile(
        `${root}/frame-${request.frames[0]}.png`,
      );
      const exported = await fs.readFile(`${root}/export-default.png`);
      if (!original.equals(exported))
        throw new Error(
          "Export defaults differ from accepted parameter rendering.",
        );
    });
  } catch (error) {
    if (!checks.some((item) => item.status === "fail"))
      checks.push({
        name: "render",
        status: "fail",
        detail: String(error.message).slice(0, 6000),
      });
  } finally {
    if (browser) await browser.close({ silent: true });
    await fs.writeFile(
      path.join(root, "renderer.json"),
      JSON.stringify(result),
    );
  }
}

await main();
