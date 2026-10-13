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
const modules = [...new Set(instances.map(item => item.code))];

/**
 * Generate the trusted entry: managed fonts, then one Sequence per placement in bottom-to-top order, each Sprite
 * inside the shared host layer that applies its placement transform around its content box.
 * A Sprite reads useVideoConfig().durationInFrames from its own Sequence, exactly as when it was accepted
 * standalone, so its source runs unmodified. Remotion clamps a Sequence to its composition, so the
 * composition spans the latest clip end (`duration_in_frames`) and only `frames` of it are rendered.
 */
function entrypoint() {
  const imports = modules.map((_, index) => `import Sprite${index} from './Sprite${index}';`).join("\n");
  const placed = instances.map((item, index) => {
    const width = item.width ?? composition.width, height = item.height ?? composition.height;
    const scale = Math.max(composition.width / width, composition.height / height);
    const host = item.host ?? {box: {x: 0, y: 0, width, height}, transform: {}};
    return `<Sequence key={${index}} from={${item.start_frame}} durationInFrames={${item.duration_frames}} width={${width}} height={${height}} style={{width:${width},height:${height},left:${(composition.width-width*scale)/2},top:${(composition.height-height*scale)/2},transform:'scale(${scale})',transformOrigin:'top left'}}><HostLayer box={${JSON.stringify(host.box)}} transform={${JSON.stringify(host.transform)}}><Sprite${modules.indexOf(item.code)} {...${JSON.stringify(item.config)}} /></HostLayer></Sequence>`;
  }).join("");
  return `/** Trusted overlay host waits for both managed font weights. */
import React from 'react';
import {AbsoluteFill, Composition, Sequence, registerRoot, delayRender, continueRender, cancelRender, staticFile} from 'remotion';
import {HostLayer} from './host-layer';
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
  await fs.copyFile(`${rendererRoot}/host-layer.tsx`, `${root}/host-layer.tsx`);
  for (const [index, code] of modules.entries()) {
    await fs.writeFile(`${root}/Sprite${index}.tsx`, code);
  }
  await fs.writeFile(`${root}/entry.tsx`, entrypoint());
  const serveUrl = await bundle({
    entryPoint: `${root}/entry.tsx`,
    outDir: `${root}/bundle`,
    publicDir: `${root}/public`,
    webpackOverride: (config) => ({ ...config, cache: false }),
  });
  console.info(`[overlay] ${new Date().toISOString()} opening browser`);
  browser = await openBrowser("chrome", {
    browserExecutable: request.browser,
    logLevel: "error",
    chromiumOptions: { gl: "swangle" },
  });
  console.info(`[overlay] ${new Date().toISOString()} browser connected`);
  const selected = await selectComposition({ serveUrl, id: "Overlay", inputProps: {}, puppeteerInstance: browser });
  // Sparse frame counts distinguish a running render from browser startup without logging business content.
  let reportedWork = 0;
  await renderMedia({
    serveUrl,
    composition: selected,
    inputProps: {},
    puppeteerInstance: browser,
    logLevel: "error",
    timeoutInMilliseconds: 30000,
    frameRange: [0, composition.frames - 1],
    codec: "vp9",
    imageFormat: "png",
    pixelFormat: "yuva420p",
    // Full-canvas filters and glows are heavy; one tab keeps Chrome within the sandbox's memory.
    concurrency: 1,
    onProgress: ({ renderedFrames, encodedFrames }) => {
      const completed = renderedFrames + encodedFrames;
      if (completed > reportedWork && (reportedWork === 0 || completed - reportedWork >= 100 || encodedFrames === composition.frames)) {
        reportedWork = completed;
        console.info(`[overlay] ${new Date().toISOString()} rendered ${renderedFrames}/${composition.frames}, encoded ${encodedFrames}`);
      }
    },
    outputLocation: `${root}/overlay.webm`,
  });
  await fs.writeFile(`${root}/renderer.json`, JSON.stringify({ frames: composition.frames }));
} catch (error) {
  // openBrowser can reject before returning a handle while Chrome still keeps Node alive.
  // End this isolated worker immediately; bubblewrap tears down its remaining child processes.
  console.error(String(error?.stack ?? error));
  process.exit(1);
} finally {
  if (browser) await browser.close({ silent: true });
}
