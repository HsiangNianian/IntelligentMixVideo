/** 预览视频地址与元数据读取；输入表单和播放器共用，取消或结束时释放媒体资源。 */
import type { MasterVideo } from "./model";

/** 环境配置留空时使用内置视频，支持 HTTP(S) 直链和 public 资源路径。 */
export function previewVideoUrl(): string {
  return new URL(
    import.meta.env.VITE_PREVIEW_VIDEO_URL?.trim() || "https://ice-pub-media.myalicdn.com/vod-demo/最美中国纪录片-智能字幕.mp4",
    window.location.origin,
  ).href;
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
