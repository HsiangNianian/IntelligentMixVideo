/** 预览视频地址与元数据读取；输入表单和播放器共用，取消或结束时释放媒体资源。 */
import type { MasterVideo, PreviewMedia } from "./model";

/** 环境配置留空时使用内置视频，支持 HTTP(S) 直链和 public 资源路径。 */
export function previewVideoUrl(index: 0 | 1 | 2 = 0): string {
  const configured = [
    import.meta.env.VITE_PREVIEW_VIDEO_1_URL,
    import.meta.env.VITE_PREVIEW_VIDEO_2_URL,
    import.meta.env.VITE_PREVIEW_VIDEO_3_URL,
  ];
  const defaults = [
    "https://oss.joyfile.net/source/2026/09/14/RC202609141904000001/01c1b9a1e9f747408d6522f7ec4cee61.mp4",
    "https://oss.oemi.jdword.com/api/clip/2026-10-13/99083fe0c1d04947b523b0439401a0ee.mp4",
    "https://oss.oemi.jdword.com/api/clip/2026-10-13/80ea1f518457424986fb425bf6da5b36.mp4",
  ];
  return new URL(
    configured[index]?.trim() || defaults[index],
    window.location.origin,
  ).href;
}

/** 三个来源各使用前十秒，不足十秒时使用完整区间。 */
export function previewVideos(sources: PreviewMedia): PreviewMedia {
  const clip = (video: MasterVideo) => ({ ...video, duration: Math.min(10, video.duration) });
  return [clip(sources[0]), clip(sources[1]), clip(sources[2])];
}

/** 加载入口共用默认地址；元数据必须通过 readPreviewVideos 读取。 */
export function previewUrls(media?: PreviewMedia): string[] {
  return media ? media.map((video) => video.url) : [previewVideoUrl(0), previewVideoUrl(1), previewVideoUrl(2)];
}

/** 同时读取三个真实视频；任何来源失败时取消剩余读取，全部成功才返回。 */
export async function readPreviewVideos(urls: readonly string[], signal: AbortSignal): Promise<PreviewMedia> {
  if (urls.length !== 3) throw new Error("需要提供三个预览视频地址");
  urls.forEach((url, index) => { if (!url.trim()) throw new Error(`请输入视频 ${index + 1} 的 URL`); });
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener("abort", abort, { once: true });
  if (signal.aborted) abort();
  const read = async (index: number) => {
    try { return await readMasterVideo(urls[index].trim(), controller.signal); }
    catch (reason) { throw new Error(`视频 ${index + 1}：${reason instanceof Error ? reason.message : "读取失败"}`); }
  };
  try { return await Promise.all([read(0), read(1), read(2)]); }
  finally { controller.abort(); signal.removeEventListener("abort", abort); }
}

/** 视频只允许重新排列，三个来源必须各出现一次。 */
export function reorderVideos(order: number[], source: number, destination: number): number[] {
  if (order.length !== 3 || new Set(order).size !== 3 || order.some((item) => ![0, 1, 2].includes(item)) || !order.includes(source) || !Number.isInteger(destination) || destination < 0 || destination > 2)
    throw new Error("视频排列无效");
  const next = order.filter((item) => item !== source);
  next.splice(destination, 0, source);
  return next;
}

/** 读取真实时长和尺寸；超时、取消或无法加载时明确失败并清理监听器。 */
export function readMasterVideo(url: string, signal: AbortSignal): Promise<MasterVideo> {
  const parsed = new URL(url, window.location.origin);
  if (!["http:", "https:"].includes(parsed.protocol)) return Promise.reject(new Error("母版须为 HTTP(S) 视频地址或 public 资源路径"));
  return new Promise((resolve, reject) => {
    const video = document.createElement("video");
    video.preload = "metadata";
    video.crossOrigin = "anonymous";
    const finish = (error?: Error) => {
      clearTimeout(timer);
      video.removeEventListener("loadedmetadata", loaded);
      video.removeEventListener("error", failed);
      signal.removeEventListener("abort", aborted);
      const media = { url: parsed.href, duration: video.duration, width: video.videoWidth, height: video.videoHeight };
      video.pause(); video.removeAttribute("src"); video.load();
      if (error) reject(error); else resolve(media);
    };
    const loaded = () => finish(Number.isFinite(video.duration) && video.duration > 0 && video.videoWidth > 0 && video.videoHeight > 0 ? undefined : new Error("母版视频缺少有效时长或画面尺寸"));
    const failed = () => finish(new Error("母版加载失败，请检查地址与跨域访问配置"));
    const aborted = () => finish(new DOMException("母版读取已取消", "AbortError"));
    const timer = setTimeout(() => finish(new Error("母版读取超时")), 20_000);
    video.addEventListener("loadedmetadata", loaded);
    video.addEventListener("error", failed);
    signal.addEventListener("abort", aborted, { once: true });
    if (signal.aborted) aborted(); else video.src = parsed.href;
  });
}
