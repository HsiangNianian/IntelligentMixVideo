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
 * of a `hold.length`-frame timeline, so the Sprite reads the length it was accepted at, and is shown for its first
 * `hold.frames` frames only. Remotion clamps a Sequence to its composition, so the
 * composition spans the latest clip end (`duration_in_frames`) and only `frames` of it are rendered.
 *
 * In-place keywords: a body instance with `anchor` is wrapped in `[data-imv-anchor]`, which hides the keyword hits
 * its Sprite marks with `data-imv-keyword` (their space stays); an instance with `anchored: {anchor, keyword}` (listed
 * after its body) is a keyword effect laid over that hit each frame by `KeywordAnchor`, ignoring its own placement.
 */
function entrypoint() {
  const imports = codes.map((_, index) => `import Sprite${index} from './Sprite${index}';`).join("\n");
  const placed = instances.map((item, index) => {
    const width = item.width ?? composition.width, height = item.height ?? composition.height;
    const scale = Math.max(composition.width / width, composition.height / height);
    const host = item.host ?? {box: {x: 0, y: 0, width, height}, transform: {}};
    const sprite = `<Sprite${codes.indexOf(item.code)} {...${JSON.stringify(item.config)}} />`;
    const layer = item.anchored
      ? `<KeywordAnchor anchor={${JSON.stringify(item.anchored.anchor)}} keyword={${JSON.stringify(item.anchored.keyword)}} box={${JSON.stringify(host.box)}}>${sprite}</KeywordAnchor>`
      : `<HostLayer box={${JSON.stringify(host.box)}} transform={${JSON.stringify(host.transform)}}>${sprite}</HostLayer>`;
    const held = item.hold ? `<Hold frame={${item.hold.frame}} frames={${item.hold.frames}} length={${item.hold.length}}>${layer}</Hold>` : layer;
    const content = item.anchor ? `<AbsoluteFill data-imv-anchor={${JSON.stringify(item.anchor)}}>${held}</AbsoluteFill>` : held;
    return `<Sequence key={${index}} from={${item.start_frame}} durationInFrames={${item.duration_frames}} width={${width}} height={${height}} style={{width:${width},height:${height},left:${(composition.width-width*scale)/2},top:${(composition.height-height*scale)/2},transform:'scale(${scale})',transformOrigin:'top left'}}>${content}</Sequence>`;
  }).join("");
  return `/** Trusted overlay host waits for both managed font weights. */
import React from 'react';
import {AbsoluteFill, Composition, Freeze, Sequence, registerRoot, delayRender, continueRender, cancelRender, staticFile, useCurrentFrame} from 'remotion';
import {HostLayer} from './host-layer';
import './keyword-anchor.js';
${imports}
const handle = delayRender('managed fonts');
Promise.all([400,700].map(async weight => {
  const face = new FontFace('Noto Sans CJK SC', 'url(' + staticFile('font-' + weight + '.ttc') + ')', {weight:String(weight)});
  document.fonts.add(await face.load());
})).then(() => continueRender(handle)).catch(cancelRender);
/** A still instance: frozen on one frame of a \`length\`-frame timeline and shown only for its first \`frames\` frames. */
const Hold = ({frame, frames, length, children}) => useCurrentFrame() < frames ? <Freeze frame={frame}><Sequence durationInFrames={length}>{children}</Sequence></Freeze> : null;
/**
 * A keyword effect over the hit its body marks under \`[data-imv-anchor=anchor]\` (the one whose text is \`keyword\`, or
 * the first): every frame, after the body has rendered, the host transform maps the effect's text onto the hit's
 * glyphs at their height and opacity (see keyword-anchor.js). Hidden until measured, and when the body shows no hit.
 * The measurement repeats once fonts finish loading, since they change the layout.
 */
const KeywordAnchor = ({anchor, keyword, box, children}) => {
  const canvas = React.useRef(null);
  const [transform, setTransform] = React.useState({opacity: 0});
  const frame = useCurrentFrame();
  React.useLayoutEffect(() => {
    const geometry = globalThis.imvKeywordAnchor;
    const measure = () => {
      if (!canvas.current) return;
      const root = document.querySelector('[data-imv-anchor="' + CSS.escape(anchor) + '"]');
      const spots = root ? geometry.spots(root, canvas.current) : [];
      const layer = canvas.current.querySelector('[data-imv-host-layer]')?.firstElementChild;
      const next = geometry.placement(spots.find((spot) => spot.text === keyword) ?? spots[0], layer ? geometry.restText(layer) : null, box) ?? {opacity: 0};
      setTransform((current) => JSON.stringify(current) === JSON.stringify(next) ? current : next);
    };
    measure();
    if (document.fonts.status !== 'loaded') document.fonts.ready.then(measure);
  }, [frame]);
  return <AbsoluteFill ref={canvas}><HostLayer box={box} transform={transform}>{children}</HostLayer></AbsoluteFill>;
};
/** Transparent canvas: nothing paints a background, so unplaced frames stay fully transparent. */
const Overlay = () => <AbsoluteFill>${placed}<style>{'[data-imv-anchor] [data-imv-keyword]{visibility:hidden !important}'}</style></AbsoluteFill>;
const Root = () => <Composition id="Overlay" component={Overlay} width={${composition.width}} height={${composition.height}} fps={${composition.fps}} durationInFrames={${composition.duration_in_frames}} />;
registerRoot(Root);
`;
}

let browser;
try {
  await fs.symlink(`${rendererRoot}/node_modules`, `${root}/node_modules`);
  await fs.copyFile(`${rendererRoot}/host-layer.tsx`, `${root}/host-layer.tsx`);
  await fs.copyFile(`${rendererRoot}/keyword-anchor.js`, `${root}/keyword-anchor.js`);
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
  console.info(`[overlay] ${new Date().toISOString()} opening browser`);
  browser = await openBrowser("chrome", {
    browserExecutable: request.browser,
    logLevel: "error",
    chromiumOptions: { gl: "swangle" },
  });
  console.info(`[overlay] ${new Date().toISOString()} browser connected`);
  const selected = await selectComposition({ serveUrl, id: "Overlay", inputProps: {}, puppeteerInstance: browser });
  // A long single Sprite render asks for consecutive `ranges`, each written to its own file from the same bundle and
  // browser; the host joins them. Otherwise the whole output is one file.
  const ranges = request.ranges ?? [[0, composition.frames - 1]];
  for (const [index, frameRange] of ranges.entries()) {
    // Per-chunk progress separates a live render from browser startup without logging business content.
    let reportedWork = 0;
    const frameCount = frameRange[1] - frameRange[0] + 1;
    await renderMedia({
      serveUrl,
      composition: selected,
      inputProps: {},
      puppeteerInstance: browser,
      logLevel: "error",
      timeoutInMilliseconds: 30000,
      frameRange,
      codec: "vp9",
      imageFormat: "png",
      pixelFormat: "yuva420p",
      // Full-canvas filters and glows are heavy; one tab keeps Chrome within the sandbox's memory.
      concurrency: 1,
      onProgress: ({ renderedFrames, encodedFrames }) => {
        const completed = renderedFrames + encodedFrames;
        if (completed > reportedWork && (reportedWork === 0 || completed - reportedWork >= 100 || encodedFrames === frameCount)) {
          reportedWork = completed;
          console.info(`[overlay] ${new Date().toISOString()} chunk ${index + 1}/${ranges.length}, rendered ${renderedFrames}/${frameCount}, encoded ${encodedFrames}`);
        }
      },
      outputLocation: request.ranges ? `${root}/chunk-${index}.webm` : `${root}/overlay.webm`,
    });
  }
  await fs.writeFile(`${root}/renderer.json`, JSON.stringify({ frames: composition.frames }));
} catch (error) {
  // openBrowser can reject before returning a handle while Chrome still keeps Node alive.
  // End this isolated worker immediately; bubblewrap tears down its remaining child processes.
  console.error(String(error?.stack ?? error));
  process.exit(1);
} finally {
  if (browser) await browser.close({ silent: true });
}
