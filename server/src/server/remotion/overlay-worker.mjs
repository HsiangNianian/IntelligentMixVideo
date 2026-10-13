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
// Instances of the same Sprite (repeated placements, subtitle cues) share one module, so its source is bundled once.
const codes = [...new Set(instances.map((item) => item.code))];

/**
 * Generate the trusted entry: managed fonts, then one Sequence per placement in bottom-to-top order, each Sprite
 * inside the shared host layer that applies its placement transform around its content box.
 * A Sprite reads useVideoConfig().durationInFrames from its own Sequence, exactly as when it was accepted
 * standalone, so its source runs unmodified. An instance with `hold` (a short subtitle) is frozen on `hold.frame`
 * and shown for its first `hold.frames` frames only. Remotion clamps a Sequence to its composition, so the
 * composition spans the latest clip end (`duration_in_frames`) and only `frames` of it are rendered.
 */
function entrypoint() {
  const imports = codes.map((_, index) => `import Sprite${index} from './Sprite${index}';`).join("\n");
  const placed = instances.map((item, index) => {
    const layer = `<HostLayer box={${JSON.stringify(item.host.box)}} transform={${JSON.stringify(item.host.transform)}}><Sprite${codes.indexOf(item.code)} {...${JSON.stringify(item.config)}} /></HostLayer>`;
    const content = item.hold ? `<Hold frame={${item.hold.frame}} frames={${item.hold.frames}}>${layer}</Hold>` : layer;
    return `<Sequence key={${index}} from={${item.start_frame}} durationInFrames={${item.duration_frames}}>${content}</Sequence>`;
  }).join("");
  return `/** Trusted overlay host waits for both managed font weights. */
import React from 'react';
import {AbsoluteFill, Composition, Freeze, Sequence, registerRoot, delayRender, continueRender, cancelRender, staticFile, useCurrentFrame} from 'remotion';
import {HostLayer} from './host-layer';
${imports}
const handle = delayRender('managed fonts');
Promise.all([400,700].map(async weight => {
  const face = new FontFace('Noto Sans CJK SC', 'url(' + staticFile('font-' + weight + '.ttc') + ')', {weight:String(weight)});
  document.fonts.add(await face.load());
})).then(() => continueRender(handle)).catch(cancelRender);
/** A still instance: frozen on one frame of its own timeline and shown only for its first \`frames\` frames. */
const Hold = ({frame, frames, children}) => useCurrentFrame() < frames ? <Freeze frame={frame}>{children}</Freeze> : null;
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
  for (const [index, code] of codes.entries()) {
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
    frameRange: [0, composition.frames - 1],
    codec: "vp9",
    imageFormat: "png",
    pixelFormat: "yuva420p",
    // Full-canvas filters and glows are heavy; one tab keeps Chrome within the sandbox's memory.
    concurrency: 1,
    outputLocation: `${root}/overlay.webm`,
  });
  await fs.writeFile(`${root}/renderer.json`, JSON.stringify({ frames: composition.frames }));
} catch (error) {
  // A non-zero exit lets the host surface worker.log; no partial report is ever written.
  console.error(String(error?.stack ?? error));
  process.exitCode = 1;
} finally {
  if (browser) await browser.close({ silent: true });
}
