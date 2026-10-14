/** 独立视频元素提取源素材缩略图；支持取消、超时和有界缓存，不操作 SDK 播放器。 */
import { thumbnailTimes, type PreviewClip } from "./timeline";

/** 等待媒体加载或定位；成功、失败、取消与超时均移除监听器。 */
function waitForMedia(video: HTMLVideoElement, event: "loadeddata" | "seeked", signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const finish = (error?: Error) => {
      clearTimeout(timer);
      video.removeEventListener(event, ready);
      video.removeEventListener("error", failed);
      signal.removeEventListener("abort", aborted);
      if (error) reject(error);
      else resolve();
    };
    const ready = () => finish();
    const failed = () => finish(new Error("视频画面加载失败，请检查视频地址及跨域访问配置。"));
    const aborted = () => finish(new DOMException("视频画面加载已取消", "AbortError"));
    const timer = setTimeout(() => finish(new Error("视频画面加载超时")), 15_000);
    video.addEventListener(event, ready, { once: true });
    video.addEventListener("error", failed, { once: true });
    signal.addEventListener("abort", aborted, { once: true });
    if (signal.aborted) aborted();
  });
}

/** 缓存按素材地址和源时间复用，最多保留 48 张；媒体错误交给调用方展示。 */
export async function loadThumbnails(
  clip: Pick<PreviewClip, "url" | "sourceIn" | "sourceOut">,
  signal: AbortSignal,
  cache: Map<string, string>,
): Promise<string[]> {
  const times = thumbnailTimes(clip);
  return loadVideoFrames(clip.url, times, signal, cache, 160);
}

/** 按源时间提取画面；省略尺寸限制时保留原始分辨率，供转场补帧使用。 */
export async function loadVideoFrames(url: string, times: number[], signal: AbortSignal, cache: Map<string, string>, maxDimension?: number): Promise<string[]> {
  if (times.some((time) => !Number.isFinite(time) || time < 0)) throw new Error("视频采样时间无效");
  const keys = times.map((time) => JSON.stringify([url, time, maxDimension]));
  signal.throwIfAborted();
  if (keys.every((key) => cache.has(key))) return keys.map((key) => cache.get(key)!);
  const video = document.createElement("video");
  video.crossOrigin = "anonymous";
  video.preload = "auto";
  video.muted = true;
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d");
  if (!context) throw new Error("当前环境无法提取视频画面");
  try {
    const loaded = waitForMedia(video, "loadeddata", signal);
    video.src = url;
    video.load();
    await loaded;
    if (video.videoWidth <= 0 || video.videoHeight <= 0)
      throw new Error("视频尺寸无效，无法提取画面");
    const scale = maxDimension ? maxDimension / Math.max(video.videoWidth, video.videoHeight) : 1;
    canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
    canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
    if (!Number.isFinite(video.duration) || times.some((time) => time >= video.duration))
      throw new Error("视频时长不足，无法提取当前片段的画面");
    const images: string[] = [];
    for (let index = 0; index < times.length; index++) {
      signal.throwIfAborted();
      const key = keys[index];
      let image = cache.get(key);
      if (!image) {
        if (video.currentTime !== times[index]) {
          const sought = waitForMedia(video, "seeked", signal);
          video.currentTime = times[index];
          await sought;
        }
        signal.throwIfAborted();
        context.drawImage(video, 0, 0, canvas.width, canvas.height);
        image = canvas.toDataURL("image/jpeg", maxDimension ? 0.7 : 0.95);
        cache.set(key, image);
        if (cache.size > 48) cache.delete(cache.keys().next().value!);
      }
      images.push(image);
    }
    return images;
  } finally {
    video.pause();
    video.removeAttribute("src");
    video.load();
  }
}
