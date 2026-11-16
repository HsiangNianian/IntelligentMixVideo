/**
 * Isolated overlay renderer: bundle the sealed Sprite modules of one template, place each in its own
 * Sequence on a shared transparent canvas, and pipe each frame range to its WebM or color/mask encoder.
 * Sources were policy-checked at acceptance and are SHA-256 sealed by the host before reaching here.
 */
import fs from "node:fs/promises";
import os from "node:os";
import { bundle } from "@remotion/bundler";
import { openBrowser, selectComposition, renderFrames, RenderInternals } from "@remotion/renderer";
import { recordFontLoad, verifyManagedFonts } from "./managed-fonts.mjs";
import { encodeRange, GpuFailure } from "./overlay-encoder.mjs";

const root = process.env.IMV_WORK_ROOT ?? "/work";
const rendererRoot = process.env.IMV_RENDERER_ROOT ?? "/renderer";
// Remotion creates browser profiles through os.tmpdir(); keep that state inside this attempt.
os.tmpdir = () => `${root}/.tmp`;
const request = JSON.parse(await fs.readFile(`${root}/request.json`, "utf8"));
const { composition, instances } = request;
const route = request.route ?? "cpu-webm";
const gpuRoute = route !== "cpu-webm";
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
    const visible = item.visible_start_frame === undefined ? "" : `frame >= ${item.visible_start_frame} && frame < ${item.visible_end_frame} && `;
    return `{${visible}<Sequence key={${index}} from={${item.start_frame}} durationInFrames={${item.duration_frames}} width={${width}} height={${height}} style={{width:${width},height:${height},left:${(composition.width-width*scale)/2},top:${(composition.height-height*scale)/2},transform:'scale(${scale})',transformOrigin:'top left'}}>${content}</Sequence>}`;
  }).join("");
  return `/** Trusted overlay host waits for both managed font weights. */
import React from 'react';
import {AbsoluteFill, Composition, Freeze, Sequence, registerRoot, delayRender, continueRender, staticFile, useCurrentFrame} from 'remotion';
import {HostLayer} from './host-layer';
import {loadManagedFonts} from './font-loader.mjs';
${imports}
const handle = delayRender('managed fonts');
loadManagedFonts({400:staticFile('font-400.otf'),700:staticFile('font-700.otf')}, staticFile('font-fallback.otf'))
  .then(() => continueRender(handle));
/** A still instance: frozen on one frame of a \`length\`-frame timeline and shown only for its first \`frames\` frames. */
const Hold = ({frame, frames, length, children}) => useCurrentFrame() < frames ? <Freeze frame={frame}><Sequence durationInFrames={length}>{children}</Sequence></Freeze> : null;
/** Transparent canvas: nothing paints a background, so unplaced frames stay fully transparent. */
const Overlay = () => { const frame = useCurrentFrame(); return <AbsoluteFill>${placed}</AbsoluteFill>; };
const Root = () => <Composition id="Overlay" component={Overlay} width={${composition.width}} height={${composition.height}} fps={${composition.fps}} durationInFrames={${composition.duration_in_frames}} />;
registerRoot(Root);
`;
}

/** Read the browser-level hardware renderer and crash counter, which generated page code cannot report or spoof. */
async function checkGraphics(browser, initialCrashes) {
  // The CDP client has no per-command timeout; sending after the browser socket closed can wait indefinitely.
  const report = browser.connection.send("SystemInfo.getInfo");
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error("Chromium GPU diagnostics timed out")), 5000);
  });
  const {value: {gpu}} = await Promise.race([report, timeout])
    .finally(() => clearTimeout(timer));
  const gl = gpu?.auxAttributes?.glRenderer;
  const crashes = gpu?.auxAttributes?.processCrashCount;
  const vendor = gpu?.devices?.[0]?.vendorId;
  // ANGLE can report enabled compositing even when running on SwiftShader or Mesa llvmpipe.
  if (!vendor || vendor === 0xffff || !gl || /swiftshader|llvmpipe|softpipe|lavapipe|software rasterizer/i.test(gl) ||
      !gpu.featureStatus?.gpu_compositing?.startsWith("enabled")) {
    throw new GpuFailure("graphics", `Hardware GPU compositing unavailable: ${gl ?? "unknown renderer"}`);
  }
  if (Number.isInteger(initialCrashes) && crashes > initialCrashes) {
    throw new GpuFailure("graphics", "Chromium GPU process crashed during frame capture");
  }
  return {gl, crashes};
}

let browser;
try {
  if (!["cpu-webm", "gpu-webm", "h264-pair"].includes(route)) throw new Error("Unknown overlay route");
  await fs.symlink(`${rendererRoot}/node_modules`, `${root}/node_modules`);
  await fs.copyFile(`${rendererRoot}/host-layer.tsx`, `${root}/host-layer.tsx`);
  await fs.copyFile(`${rendererRoot}/font-loader.mjs`, `${root}/font-loader.mjs`);
  for (const [index, code] of codes.entries()) {
    await fs.writeFile(`${root}/Sprite${index}.tsx`, code);
  }
  await fs.writeFile(`${root}/entry.tsx`, entrypoint());
  timings.source_prepare_ms = performance.now() - started;
  console.info(`[overlay] ${new Date().toISOString()} opening browser`);
  let stageStarted = performance.now();
  try {
    browser = await openBrowser("chrome", {
      browserExecutable: request.browser, logLevel, chromiumOptions: { gl: gpuRoute ? "angle-egl" : "swangle" },
    });
  } catch (error) {
    if (gpuRoute && /GPU process isn't usable|GPU process launch failed|eglInitialize|EGL_NOT_INITIALIZED/i.test(error.message)) {
      throw new GpuFailure("graphics", error.message);
    }
    throw error;
  }
  const graphics = gpuRoute ? await checkGraphics(browser) : null;
  if (graphics) console.info(`[overlay] graphics renderer: ${graphics.gl}`);
  timings.browser_ms = performance.now() - stageStarted;
  console.info(`[overlay] ${new Date().toISOString()} browser connected`);
  const fonts = await verifyManagedFonts(browser, root);
  const onBrowserLog = (log) => recordFontLoad(fonts.runtime, log);
  stageStarted = performance.now();
  const serveUrl = await bundle({
    entryPoint: `${root}/entry.tsx`,
    outDir: `${root}/bundle`,
    publicDir: `${root}/public`,
    webpackOverride: (config) => ({ ...config, cache: false }),
  });
  timings.bundle_ms = performance.now() - stageStarted;
  stageStarted = performance.now();
  const selected = await selectComposition({ serveUrl, id: "Overlay", inputProps: {}, puppeteerInstance: browser, logLevel, onBrowserLog });
  timings.select_ms = performance.now() - stageStarted;
  // A long single Sprite render asks for consecutive `ranges`, each written to its own file from the same bundle and
  // browser; the host joins them. Otherwise the whole output is one file.
  const ranges = request.ranges ?? [[0, composition.frames - 1]];
  const requestedChunks = Math.min(request.chunk_concurrency ?? 1, ranges.length);
  const chunkConcurrency = gpuRoute ? Math.min(2, requestedChunks) : requestedChunks;
  const ffmpeg = request.bundled_ffmpeg && route !== "h264-pair"
    ? RenderInternals.getExecutablePath({type: "ffmpeg", binariesDirectory: null, logLevel, indent: false})
    : "ffmpeg";
  timings.chunk_concurrency = chunkConcurrency;
  stageStarted = performance.now();
  /** Render an absolute range without changing any instance clock; one failure ends the isolated worker. */
  async function renderChunk(index) {
    const frameRange = ranges[index];
    // Per-chunk progress separates a live render from browser startup without logging business content.
    let reportedWork = 0;
    const frameCount = frameRange[1] - frameRange[0] + 1;
    const chunkStarted = performance.now();
    const chunk = { frame_range: frameRange, rendered_ms: null, encoded_ms: null, rendered_frames: 0, encoded_frames: 0,
      parallel_encoding: true, shared_memory_capture: false };
    timings.chunks[index] = chunk;
    /** Recheck the active device after each capture; a lost CDP connection alone cannot identify the cause of a Sprite error. */
    async function capture(onFrameBuffer) {
      const rendering = renderFrames({
        serveUrl, composition: selected, inputProps: {}, puppeteerInstance: browser, onBrowserLog, logLevel,
        timeoutInMilliseconds: 30000, frameRange, imageFormat: "png", outputDir: null, muted: true,
        concurrency: request.concurrency ?? 1, onFrameBuffer, onFrameUpdate: () => {},
        onStart: ({resolvedConcurrency}) => { chunk.resolved_concurrency = resolvedConcurrency; },
      });
      if (!graphics) return rendering;
      try {
        await rendering;
      } catch (renderError) {
        await checkGraphics(browser, graphics.crashes).catch((probeError) => {
          if (probeError instanceof GpuFailure) throw probeError;
        });
        throw renderError;
      }
      await checkGraphics(browser, graphics.crashes);
    }
    await encodeRange({
      root, route, ffmpeg, composition, frameRange, chunk, index: request.ranges ? index : null, vp9: request.vp9 ?? {}, capture,
      progress: () => {
        const completed = chunk.rendered_frames + chunk.encoded_frames;
        if (completed > reportedWork && (!reportedWork || completed - reportedWork >= 100 || chunk.encoded_frames === frameCount)) {
          reportedWork = completed;
          console.info(`[overlay] ${new Date().toISOString()} chunk ${index + 1}/${ranges.length}, rendered ${chunk.rendered_frames}/${frameCount}, encoded ${chunk.encoded_frames}`);
        }
      },
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
  await fs.writeFile(`${root}/renderer.json`, JSON.stringify({ frames: composition.frames, performance: timings, runtime: fonts.runtime }));
} catch (error) {
  // openBrowser can reject before returning a handle while Chrome still keeps Node alive.
  // End this isolated worker immediately; bubblewrap tears down its remaining child processes.
  console.error(String(error?.stack ?? error));
  try {
    if (error instanceof GpuFailure) {
      await fs.writeFile(`${root}/gpu-failure.json`, JSON.stringify({kind: error.kind, reason: error.reason}), {mode: 0o600});
    }
  } finally {
    process.exit(1);
  }
} finally {
  if (browser) await browser.close({ silent: true });
}
