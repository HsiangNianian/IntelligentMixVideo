/**
 * Feed one absolute Remotion frame range to FFmpeg in presentation order. The capture pages wait
 * for their turns and the encoder's pipe, keeping queued PNG memory bounded by page concurrency.
 */
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createInterface } from "node:readline";
import { performance } from "node:perf_hooks";

/** Only failures identified by the host's graphics checks or FFmpeg can select another render route. */
export class GpuFailure extends Error {
  /** Keep the private error message separate from the failure class and per-task fallback reason. */
  constructor(kind, message, reason) {
    super(message);
    this.kind = kind;
    this.reason = reason;
  }
}

/** Set up a single VP9 alpha output or two frame-synchronous NVENC outputs from one RGBA input. */
function argumentsFor(route, fps, root, index, vp9) {
  const args = ["-hide_banner", "-loglevel", "warning", "-nostdin", "-n", "-nostats", "-progress", "pipe:1",
    "-threads", "3", "-f", "image2pipe", "-framerate", String(fps), "-vcodec", "png", "-i", "pipe:0"];
  const prefix = index === null ? "" : `chunk-${index}-`;
  if (route !== "h264-pair") {
    const output = index === null ? "overlay.webm" : `chunk-${index}.webm`;
    return [...args, "-an", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-deadline", "realtime",
      "-cpu-used", String(vp9.cpu_used ?? 7), "-threads", String(vp9.threads ?? 3), "-row-mt", String(Number(vp9.row_mt ?? true)),
      "-lag-in-frames", "0", "-crf", "34", "-b:v", "0", `${root}/${output}`];
  }
  args.push("-filter_complex_threads", "3", "-filter_complex",
    "[0:v]format=rgba,split=2[c][a];[c]scale=in_range=pc:out_range=tv:out_color_matrix=bt709,format=yuv420p[color];" +
    "[a]alphaextract,scale=in_range=pc:out_range=pc,format=yuv420p,setparams=range=full[mask]");
  for (const [name, qp, range] of [["color", "24", "tv"], ["mask", "15", "pc"]]) {
    args.push("-map", `[${name}]`, "-an", "-c:v", "h264_nvenc", "-gpu", "0", "-preset", "p4", "-profile:v", "high",
      "-rc", "constqp", "-bf", "0", "-g", "60", "-qp", qp, "-color_range", range,
      "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-movflags", "+faststart",
      `${root}/${prefix}${name}.mp4`);
  }
  return args;
}

// Only diagnostic text from FFmpeg is inspected. Filesystem, PNG and generic FFmpeg failures stay ordinary failures.
const nvencDimensions = [
  /InitializeEncoder failed: invalid param \(\d+\): Frame Dimension less than the minimum supported value\b/i,
  /(?:^|\bh264_nvenc\b[^\]\r\n]*\] *)(?:Width|Height) \d+ exceeds \d+\s*$/im,
];
const nvencFailures = [
  /unknown encoder ['"]?h264_nvenc|cannot (?:load|init) (?:libcuda|libnvidia-encode|cuda)|no (?:nvenc )?capable devices/i,
  /OpenEncodeSessionEx failed(?!:\s*invalid param)|the minimum required Nvidia driver|driver does not support the required nvenc/i,
  /(?:EncodePicture|InitializeEncoder|LockBitstream) failed: (?:out of memory|device|encoder busy)/i,
  /CUDA_ERROR_(?:OUT_OF_MEMORY|NO_DEVICE|DEVICE_UNAVAILABLE|INVALID_DEVICE)/i,
  /NV_ENC_ERR_(?:NO_ENCODE_DEVICE|UNSUPPORTED_DEVICE|DEVICE_NOT_EXIST|ENCODER_BUSY|OUT_OF_MEMORY)/i,
];

/** Capture and encode a range, reporting pipe/render time and post-capture encoder drain separately. */
export async function encodeRange({route, root, index, ffmpeg, vp9, composition, frameRange, chunk, capture, progress}) {
  const [first, last] = frameRange;
  const args = argumentsFor(route, composition.fps, root, index, vp9);
  const child = spawn(ffmpeg, args, {stdio: ["pipe", "pipe", "pipe"]});
  const started = performance.now();
  let stderr = "";
  let next = first;
  let inputEnded = false;
  const waiters = new Map();
  child.stderr.setEncoding("utf8").on("data", (data) => { stderr = (stderr + data).slice(-8192); });
  // A failed encoder can close stdin before its exit event. Its exit code and stderr give the actionable cause.
  child.stdin.on("error", () => {});
  createInterface({input: child.stdout}).on("line", (line) => {
    if (line.startsWith("frame=")) {
      chunk.encoded_frames = Number(line.slice(6));
      progress();
    }
  });
  const finished = new Promise((resolve, reject) => {
    child.once("error", (error) => reject(route === "h264-pair" && error.code === "ENOENT"
      ? new GpuFailure("encoder", error.message) : error));
    child.once("close", (code, signal) => {
      if (code === 0 && inputEnded) return resolve();
      const detail = `FFmpeg exited (${code ?? signal}): ${stderr.trim()}`;
      // A canvas outside this encoder's bounds can use WebM without putting a healthy card in cooldown.
      if (route === "h264-pair" && nvencDimensions.some((pattern) => pattern.test(stderr))) {
        return reject(new GpuFailure("encoder", detail, "nvenc_dimensions_unsupported"));
      }
      const hardware = route === "h264-pair" && nvencFailures.some((pattern) => pattern.test(stderr));
      reject(hardware ? new GpuFailure("encoder", detail) : new Error(detail));
    });
  });

  /** Remotion awaits the callback on each page, so a page with a later frame holds only its own PNG. */
  async function writeFrame(png, frame) {
    if (frame < next || frame > last || waiters.has(frame)) throw new Error("Unexpected Remotion frame index");
    if (frame !== next) await new Promise((resolve) => waiters.set(frame, resolve));
    if (png.readUInt32BE(0) !== 0x89504e47 || png.readUInt32BE(16) !== composition.width || png.readUInt32BE(20) !== composition.height) {
      throw new Error("Unexpected Remotion PNG dimensions");
    }
    try {
      if (!child.stdin.write(png)) await once(child.stdin, "drain");
    } catch (error) {
      // Prefer the encoder's diagnosis when its closed pipe reports EPIPE before its close event.
      if (error.code === "EPIPE" || error.code === "ERR_STREAM_DESTROYED") await finished;
      throw error;
    }
    next++;
    chunk.rendered_frames = next - first;
    progress();
    waiters.get(next)?.();
    waiters.delete(next);
  }

  try {
    await Promise.race([finished, capture(writeFrame)]);
    if (next !== last + 1) throw new Error("Incomplete Remotion PNG frame range");
    chunk.rendered_ms = performance.now() - started;
    inputEnded = true;
    child.stdin.end();
    await finished;
    chunk.encoded_ms = performance.now() - started - chunk.rendered_ms;
  } finally {
    if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
    await finished.catch(() => {});
  }
}
