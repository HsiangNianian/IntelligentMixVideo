/** Isolated renderer: format and check one TSX candidate, then render evidence into /work. */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import ts from "typescript";
import prettier from "prettier";
import { languageDiagnostics } from "./typescript.mjs";
import { bundle } from "@remotion/bundler";
import { exportTemplate, buildPresentation } from "./presentation.mjs";
import {
  openBrowser,
  selectComposition,
  renderStill,
  renderMedia,
} from "@remotion/renderer";

const root = process.env.IMV_WORK_ROOT ?? "/work";
const rendererRoot = process.env.IMV_RENDERER_ROOT ?? "/renderer";
// Remotion creates browser profiles through os.tmpdir(); keep that state inside this attempt.
os.tmpdir = () => `${root}/.tmp`;
const request = JSON.parse(await fs.readFile(`${root}/request.json`, "utf8"));
const previewConfig = request.preview_config ?? request.config;
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

/** Check the actual candidate/default-props call sites and retain structured diagnostic locations. */
function typecheck(sprite = false) {
  const checked = languageDiagnostics(root, sprite);
  result.diagnostics = checked.diagnostics;
  if (checked.diagnostics.length) throw new Error(checked.detail);
}

/** Render an accepted, bus-bound Sprite with VP9 alpha inside the same source-policy sandbox. */
async function renderSprite() {
  let browser;
  try {
    const code = await check("source_policy", async () => {
      sourcePolicy(request.code);
      return prettier.format(request.code, { parser: "typescript" });
    });
    await fs.writeFile(`${root}/Template.tsx`, code);
    await fs.symlink(`${rendererRoot}/node_modules`, `${root}/node_modules`);
    await fs.writeFile(
      `${root}/contract.tsx`,
      `/** Bind validated runtime props to the accepted component type. */\nimport React from 'react';\nimport Template from './Template';\nconst props = ${JSON.stringify(request.config)} as React.ComponentProps<typeof Template>;\nconst element = <Template {...props} />;\n`,
    );
    await fs.writeFile(
      `${root}/entry.tsx`,
      `/** Trusted Sprite host supplies fonts, canvas and business time. */
import React from 'react';
import {Composition, Freeze, registerRoot, delayRender, continueRender, cancelRender, staticFile} from 'remotion';
import Template from './Template';
const handle = delayRender('managed fonts');
Promise.all([400,700].map(async weight => {
  const face = new FontFace('Noto Sans CJK SC', 'url(' + staticFile('font-' + weight + '.ttc') + ')', {weight:String(weight)});
  document.fonts.add(await face.load());
})).then(() => continueRender(handle)).catch(cancelRender);
const Bound = (props) => ${Number.isInteger(request.static_frame) ? `<Freeze frame={${request.static_frame}}><Template {...props}/></Freeze>` : `<Template {...props}/>`};
registerRoot(() => <Composition id="Sprite" component={Bound} defaultProps={${JSON.stringify(request.config)}} width={${request.composition.width}} height={${request.composition.height}} fps={${request.composition.fps}} durationInFrames={${request.composition.duration_in_frames}} />);
`,
    );
    await check("typescript", async () => typecheck(true));
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
      id: "Sprite",
      inputProps: request.config,
      puppeteerInstance: browser,
    });
    await check("render", () =>
      renderMedia({
        serveUrl,
        composition,
        inputProps: request.config,
        puppeteerInstance: browser,
        logLevel: "error",
        timeoutInMilliseconds: 30000,
        codec: "vp9",
        pixelFormat: "yuva420p",
        imageFormat: "png",
        concurrency: 2,
        frameRange: request.frame_range,
        outputLocation: `${root}/sprite.webm`,
      }),
    );
  } catch (error) {
    if (!checks.some((item) => item.status === "fail"))
      checks.push({ name: "render", status: "fail", detail: String(error.message).slice(0, 6000) });
  } finally {
    if (browser) await browser.close({ silent: true });
    await fs.writeFile(`${root}/renderer.json`, JSON.stringify(result));
  }
}

/** Load managed fonts before Remotion captures any frame; user code owns no loader side effects. */
function entrypoint() {
  const config = JSON.stringify(previewConfig);
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
        request.subtitle ? previewConfig.highlightRanges : null,
      );
      await fs.writeFile(`${root}/Export.tsx`, exported);
    });
    await fs.symlink(`${rendererRoot}/node_modules`, `${root}/node_modules`);
    await fs.writeFile(
      `${root}/contract.tsx`,
      `/** Verify source props and default export call sites. */\nimport React from 'react';\nimport Template from './Template';\nimport Export from './Export';\nconst props = ${JSON.stringify(request.config)};\nconst element = <Template {...props} ${request.subtitle ? `highlightRanges={[[0,1]]}` : ""} />;\nconst exported = <Export />;\n`,
    );
    await fs.writeFile(`${root}/entry.tsx`, entrypoint());
    await check("typescript", async () => typecheck());
    if (request.mode === "code") return;
    await check("interactive_bundle", () =>
      buildPresentation(root, request.config, request.composition, request.keywords),
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
      inputProps: previewConfig,
      puppeteerInstance: browser,
    });
    const options = {
      serveUrl,
      composition,
      inputProps: previewConfig,
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
    // Capture evidence only; the host compares visible pixels with bounded raster tolerance.
    await check("repeat_render", async () => {
      const frame = request.frames[Math.floor(request.frames.length / 2)];
      await renderStill({
        ...options,
        frame,
        imageFormat: "png",
        output: `${root}/repeat.png`,
      });
    });
    await check("export_default_render", async () => {
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

if (request.mode === "sprite_render") await renderSprite();
else await main();
