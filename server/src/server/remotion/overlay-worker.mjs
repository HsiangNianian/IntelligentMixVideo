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
// Verbose capture/encoder diagnostics are only requested by the private local benchmark.
const logLevel = request.log_level ?? "error";
// Instances of the same Sprite (repeated placements, subtitle cues) share one module, so its source is bundled once.
const codes = [...new Set(instances.map((item) => item.code))];
// Durations are monotonic and contain no generated source, text, or asset URLs.
const timings = { chunks: [] };
const started = performance.now();

/**
 * Generate the trusted entry: managed fonts, then one Sequence per placement in bottom-to-top order, each Sprite
 * inside the shared host layer that applies its placement transform around its content box.
 * A Sprite reads useVideoConfig().durationInFrames from its own Sequence, exactly as when it was accepted
 * standalone, so its source runs unmodified. An instance with `hold` (a short subtitle) is frozen on `hold.frame`
 * of a `hold.length`-frame timeline, so the Sprite reads the length it was accepted at, and is shown for its first
 * `hold.frames` frames only. Remotion clamps a Sequence to its composition, so the
 * composition spans the latest clip end (`duration_in_frames`) and only `frames` of it are rendered.
 */
function entrypoint() {
  const imports = codes.map((_, index) => `import Sprite${index} from './Sprite${index}';`).join("\n");
  const placed = instances.map((item, index) => {
    const width = item.width ?? composition.width, height = item.height ?? composition.height;
    const scale = Math.max(composition.width / width, composition.height / height);
    const host = item.host ?? {box: {x: 0, y: 0, width, height}, transform: {}};
    const layer = `<HostLayer box={${JSON.stringify(host.box)}} transform={${JSON.stringify(host.transform)}}><Sprite${codes.indexOf(item.code)} {...${JSON.stringify(item.config)}} /></HostLayer>`;
    const content = item.hold ? `<Hold frame={${item.hold.frame}} frames={${item.hold.frames}} length={${item.hold.length}}>${layer}</Hold>` : layer;
    return `<Sequence key={${index}} from={${item.start_frame}} durationInFrames={${item.duration_frames}} width={${width}} height={${height}} style={{width:${width},height:${height},left:${(composition.width-width*scale)/2},top:${(composition.height-height*scale)/2},transform:'scale(${scale})',transformOrigin:'top left'}}>${content}</Sequence>`;
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
/** A still instance: frozen on one frame of a \`length\`-frame timeline and shown only for its first \`frames\` frames. */
const Hold = ({frame, frames, length, children}) => useCurrentFrame() < frames ? <Freeze frame={frame}><Sequence durationInFrames={length}>{children}</Sequence></Freeze> : null;
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
  timings.source_prepare_ms = performance.now() - started;
  let stageStarted = performance.now();
  const serveUrl = await bundle({
    entryPoint: `${root}/entry.tsx`,
    outDir: `${root}/bundle`,
    publicDir: `${root}/public`,
    webpackOverride: (config) => ({ ...config, cache: false }),
  });
  timings.bundle_ms = performance.now() - stageStarted;
  console.info(`[overlay] ${new Date().toISOString()} opening browser`);
  stageStarted = performance.now();
  browser = await openBrowser("chrome", {
    browserExecutable: request.browser,
    logLevel,
    chromiumOptions: { gl: "swangle" },
  });
  timings.browser_ms = performance.now() - stageStarted;
  console.info(`[overlay] ${new Date().toISOString()} browser connected`);
  stageStarted = performance.now();
  const selected = await selectComposition({ serveUrl, id: "Overlay", inputProps: {}, puppeteerInstance: browser, logLevel });
  timings.select_ms = performance.now() - stageStarted;
  // A long single Sprite render asks for consecutive `ranges`, each written to its own file from the same bundle and
  // browser; the host joins them. Otherwise the whole output is one file.
  const ranges = request.ranges ?? [[0, composition.frames - 1]];
  const chunkConcurrency = Math.min(request.chunk_concurrency ?? 1, ranges.length);
  timings.chunk_concurrency = chunkConcurrency;
  stageStarted = performance.now();
  /** Render an absolute range without changing any instance clock; one failure ends the isolated worker. */
  async function renderChunk(index) {
    const frameRange = ranges[index];
    // Per-chunk progress separates a live render from browser startup without logging business content.
    let reportedWork = 0;
    const frameCount = frameRange[1] - frameRange[0] + 1;
    const chunkStarted = performance.now();
    const chunk = { frame_range: frameRange, rendered_ms: null, encoded_ms: null };
    timings.chunks[index] = chunk;
    await renderMedia({
      serveUrl,
      composition: selected,
      inputProps: {},
      puppeteerInstance: browser,
      logLevel,
      timeoutInMilliseconds: 30000,
      frameRange,
      codec: "vp9",
      imageFormat: "png",
      pixelFormat: "yuva420p",
      // The host owns the page budget; Remotion validates it against the available CPU count.
      concurrency: request.concurrency ?? 1,
      disableSharedMemoryCapture: request.disable_shared_memory_capture ?? false,
      disallowParallelEncoding: request.disallow_parallel_encoding ?? false,
      // Only modify the actual VP9 encoder, never the final copy-only mux or shared-memory input threads.
      ffmpegOverride: ({ args }) => {
        if (args[args.indexOf("-c:v") + 1] !== "libvpx-vp9") return args;
        chunk.shared_memory_capture = args.some((arg, i) => arg === "-f" && args[i + 1] === "remotionshm");
        const vp9 = request.vp9 ?? {};
        const flags = [];
        for (const [name, flag] of [["cpu_used", "-cpu-used"], ["threads", "-threads"], ["row_mt", "-row-mt"]]) {
          if (vp9[name] != null) flags.push(flag, String(typeof vp9[name] === "boolean" ? Number(vp9[name]) : vp9[name]));
        }
        return [...args.slice(0, -1), ...flags, args.at(-1)];
      },
      onStart: ({ parallelEncoding, resolvedConcurrency }) => {
        chunk.parallel_encoding = parallelEncoding;
        chunk.resolved_concurrency = resolvedConcurrency;
      },
      onProgress: ({ renderedFrames, encodedFrames, renderedDoneIn, encodedDoneIn }) => {
        // Keep the raw progress API intervals: with parallel encoding, renderedDoneIn waits for
        // frames AND the pre-encoder, while encodedDoneIn measures the final copy-only mux.
        // elapsed_ms is the comparable wall time; encoded_ms alone is not total encoding cost.
        if (renderedDoneIn != null) chunk.rendered_ms = renderedDoneIn;
        if (encodedDoneIn != null) chunk.encoded_ms = encodedDoneIn;
        chunk.rendered_frames = renderedFrames;
        chunk.encoded_frames = encodedFrames;
        const completed = renderedFrames + encodedFrames;
        if (completed > reportedWork && (reportedWork === 0 || completed - reportedWork >= 100 || encodedFrames === frameCount)) {
          reportedWork = completed;
          console.info(`[overlay] ${new Date().toISOString()} chunk ${index + 1}/${ranges.length}, rendered ${renderedFrames}/${frameCount}, encoded ${encodedFrames}`);
        }
      },
      outputLocation: request.ranges ? `${root}/chunk-${index}.webm` : `${root}/overlay.webm`,
    });
    chunk.elapsed_ms = performance.now() - chunkStarted;
  }
  // Bound chunk jobs separately from pages per chunk. Their product is the per-video page budget.
  let nextChunk = 0;
  await Promise.all(Array.from({ length: chunkConcurrency }, async () => {
    while (nextChunk < ranges.length) await renderChunk(nextChunk++);
  }));
  timings.render_ms = performance.now() - stageStarted;
  timings.total_ms = performance.now() - started;
  console.info(`[overlay-performance] ${JSON.stringify(timings)}`);
  await fs.writeFile(`${root}/renderer.json`, JSON.stringify({ frames: composition.frames, performance: timings }));
} catch (error) {
  // openBrowser can reject before returning a handle while Chrome still keeps Node alive.
  // End this isolated worker immediately; bubblewrap tears down its remaining child processes.
  console.error(String(error?.stack ?? error));
  process.exit(1);
} finally {
  if (browser) await browser.close({ silent: true });
}
