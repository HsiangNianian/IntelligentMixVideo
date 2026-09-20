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
    const failed = () => finish(new Error("缩略图加载失败，请检查视频地址及跨域访问配置。"));
    const aborted = () => finish(new DOMException("缩略图加载已取消", "AbortError"));
    const timer = setTimeout(() => finish(new Error("缩略图加载超时")), 15_000);
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
  const keys = times.map((time) => JSON.stringify([clip.url, time]));
  signal.throwIfAborted();
  if (keys.every((key) => cache.has(key))) return keys.map((key) => cache.get(key)!);
  const video = document.createElement("video");
  video.crossOrigin = "anonymous";
  video.preload = "auto";
  video.muted = true;
  const canvas = document.createElement("canvas");
  canvas.width = 160;
  canvas.height = 90;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("当前环境无法生成视频缩略图");
  try {
    const loaded = waitForMedia(video, "loadeddata", signal);
    video.src = clip.url;
    video.load();
    await loaded;
    if (!Number.isFinite(video.duration) || clip.sourceOut > video.duration + 0.05)
      throw new Error("视频时长不足，无法提取当前片段的缩略图");
    const images: string[] = [];
    for (let index = 0; index < times.length; index++) {
      signal.throwIfAborted();
      const key = keys[index];
      let image = cache.get(key);
      if (!image) {
        const sought = waitForMedia(video, "seeked", signal);
        video.currentTime = times[index];
        await sought;
        signal.throwIfAborted();
        context.drawImage(video, 0, 0, canvas.width, canvas.height);
        image = canvas.toDataURL("image/jpeg", 0.7);
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
