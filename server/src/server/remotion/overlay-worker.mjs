/**
 * Isolated overlay renderer: bundle the sealed Sprite modules of one template, place each in its own
 * Sequence on a shared transparent canvas, and render a single VP9 alpha WebM into /work.
 * Sources were policy-checked at acceptance and are SHA-256 sealed by the host before reaching here.
 */
import fs from "node:fs/promises";
import os from "node:os";
import { bundle } from "@remotion/bundler";
import { openBrowser, selectComposition, renderMedia } from "@remotion/renderer";

const root = process.env.IMV_WORK_ROOT ?? "/work";
const rendererRoot = process.env.IMV_RENDERER_ROOT ?? "/renderer";
// Remotion creates browser profiles through os.tmpdir(); keep that state inside this attempt.
os.tmpdir = () => `${root}/.tmp`;
const request = JSON.parse(await fs.readFile(`${root}/request.json`, "utf8"));
const { composition, instances } = request;

/**
 * Sprites were accepted as standalone compositions and size their animations from
 * useVideoConfig().durationInFrames. Inside the shared composition that value would be the whole
 * overlay length, so each Sprite's `remotion` import is routed through this shim, which reports the
 * Sprite's own length instead.
 */
const shim = `import {createContext, useContext} from 'react';
import {useVideoConfig as useCompositionConfig} from 'remotion';
export * from 'remotion';
export const OwnDuration = createContext<number | null>(null);
export const useVideoConfig = () => {
  const config = useCompositionConfig();
  const own = useContext(OwnDuration);
  return own === null ? config : {...config, durationInFrames: own};
};
`;

/** Generate the trusted entry: managed fonts, then one Sequence per placement in bottom-to-top order. */
function entrypoint() {
  const imports = instances.map((_, index) => `import Sprite${index} from './Sprite${index}';`).join("\n");
  const placed = instances.map((item, index) =>
    `<Sequence key={${index}} from={${item.start_frame}} durationInFrames={${item.duration_frames}}><OwnDuration.Provider value={${item.own_frames}}><Sprite${index} {...${JSON.stringify(item.config)}} /></OwnDuration.Provider></Sequence>`,
  ).join("");
  return `/** Trusted overlay host waits for both managed font weights. */
import React from 'react';
import {AbsoluteFill, Composition, Sequence, registerRoot, delayRender, continueRender, cancelRender, staticFile} from 'remotion';
import {OwnDuration} from './remotion-shim';
${imports}
const handle = delayRender('managed fonts');
Promise.all([400,700].map(async weight => {
  const face = new FontFace('Noto Sans CJK SC', 'url(' + staticFile('font-' + weight + '.ttc') + ')', {weight:String(weight)});
  document.fonts.add(await face.load());
})).then(() => continueRender(handle)).catch(cancelRender);
/** Transparent canvas: nothing paints a background, so unplaced frames stay fully transparent. */
const Overlay = () => <AbsoluteFill>${placed}</AbsoluteFill>;
const Root = () => <Composition id="Overlay" component={Overlay} width={${composition.width}} height={${composition.height}} fps={${composition.fps}} durationInFrames={${composition.duration_in_frames}} />;
registerRoot(Root);
`;
}

let browser;
try {
  await fs.symlink(`${rendererRoot}/node_modules`, `${root}/node_modules`);
  await fs.writeFile(`${root}/remotion-shim.tsx`, shim);
  for (const [index, item] of instances.entries()) {
    const code = item.code.replace(/from\s+(['"])remotion\1/g, `from "./remotion-shim"`);
    await fs.writeFile(`${root}/Sprite${index}.tsx`, code);
  }
  await fs.writeFile(`${root}/entry.tsx`, entrypoint());
  const serveUrl = await bundle({
    entryPoint: `${root}/entry.tsx`,
    outDir: `${root}/bundle`,
    publicDir: `${root}/public`,
    webpackOverride: (config) => ({ ...config, cache: false }),
  });
  browser = await openBrowser("chrome", {
    browserExecutable: request.browser,
    logLevel: "error",
    chromiumOptions: { gl: "swangle" },
  });
  const selected = await selectComposition({ serveUrl, id: "Overlay", inputProps: {}, puppeteerInstance: browser });
  await renderMedia({
    serveUrl,
    composition: selected,
    inputProps: {},
    puppeteerInstance: browser,
    logLevel: "error",
    timeoutInMilliseconds: 30000,
    codec: "vp9",
    imageFormat: "png",
    pixelFormat: "yuva420p",
    // Full-canvas filters and glows are heavy; one tab keeps Chrome within the sandbox's memory.
    concurrency: 1,
    outputLocation: `${root}/overlay.webm`,
  });
  await fs.writeFile(`${root}/renderer.json`, JSON.stringify({ frames: composition.duration_in_frames }));
} catch (error) {
  // A non-zero exit lets the host surface worker.log; no partial report is ever written.
  console.error(String(error?.stack ?? error));
  process.exitCode = 1;
} finally {
  if (browser) await browser.close({ silent: true });
}
