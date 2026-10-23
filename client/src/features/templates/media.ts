/**
 * 预览视频地址与元数据读取；输入表单和播放器共用，支持 HTTP(S)、public 资源、本地文件（桌面端为预览媒体服务地址，
 * 浏览器为 blob URL），取消或结束时释放媒体资源。内置示例随客户端打包：桌面端经预览媒体服务以 HTTP 播放，
 * 浏览器首次读取时整段载入内存并改用 blob URL 播放，不再每次从网络下载。
 */
import { isTauri } from "@tauri-apps/api/core";
import type { MasterVideo, PreviewMedia } from "./model";
import { localPreviewVideos } from "./localVideos";
import { isLocalMediaUrl, mediaServerBase, sampleMediaUrl } from "./mediaServer";
import { debugLog, describeMedia } from "@/lib/diagnostics";

/** 内置示例：public/preview/ 下正好 10 秒的 1080×1920、30fps 无音轨片段。 */
const bundledVideos = ["/preview/sample-1.mp4", "/preview/sample-2.mp4", "/preview/sample-3.mp4"] as const;

/** 内置示例的资源地址到准备中或已备好的播放地址；失败后删除以便重试。 */
const bundledLoads = new Map<string, Promise<string>>();
/** 已备好的内置示例：播放地址（媒体服务地址或 blob URL）到资源地址；应用存活期间复用、不释放。 */
const bundledSources = new Map<string, string>();

/**
 * 环境配置留空时使用内置示例，支持 HTTP(S) 直链和 public 资源路径。
 * 相对路径按页面地址解析，桌面端 macOS 与 Linux 的页面地址（tauri://localhost）同样适用。
 */
export function previewVideoUrl(index: 0 | 1 | 2 = 0): string {
  const configured = [
    import.meta.env.VITE_PREVIEW_VIDEO_1_URL,
    import.meta.env.VITE_PREVIEW_VIDEO_2_URL,
    import.meta.env.VITE_PREVIEW_VIDEO_3_URL,
  ];
  return new URL(configured[index]?.trim() || bundledVideos[index], window.location.href).href;
}

/** 内置示例的完整地址；与 previewVideoUrl 使用相同的解析方式，供识别和反查。 */
function bundledUrls(): string[] {
  return bundledVideos.map((path) => new URL(path, window.location.href).href);
}

/**
 * 内置示例的播放地址。桌面端页面协议不是 HTTP(S)（Windows 的页面服务也不支持范围请求），blob URL 又让系统 WebKitGTK 里的
 * IMS SDK 一直等不到 canplaythrough，所以改用预览媒体服务的 HTTP 地址；浏览器把示例整段读成 blob URL。
 * 并发读取共用同一请求，其他地址原样返回。
 */
async function playableUrl(input: string): Promise<string> {
  let url: string;
  try { url = new URL(input, window.location.href).href; } catch { return input; }
  const index = bundledUrls().indexOf(url);
  if (index < 0) return input;
  let pending = bundledLoads.get(url);
  if (!pending) {
    const playable = isTauri()
      ? mediaServerBase().then((server) => sampleMediaUrl(server, index))
      : fetch(url).then(async (response) => {
        if (!response.ok) throw new Error(`内置示例视频读取失败（HTTP ${response.status}）`);
        return URL.createObjectURL(await response.blob());
      });
    pending = playable.then((value) => {
      bundledSources.set(value, url);
      return value;
    });
    bundledLoads.set(url, pending);
    pending.catch(() => bundledLoads.delete(url));
  }
  return pending;
}

/** 弹窗中展示的来源地址：内置示例显示其资源地址，而不是播放用的地址。 */
export function sourceUrl(url: string): string {
  return bundledSources.get(url) ?? url;
}

/** 用户选择的本地文件（桌面端的媒体服务地址或浏览器的 blob URL，不含内置示例）。 */
function isLocalVideo(url: string): boolean {
  return isLocalMediaUrl(url) || (url.startsWith("blob:") && !bundledSources.has(url));
}

/** URL 来源与内置示例各使用前十秒（不足十秒用完整区间）；用户选择的本地文件使用完整时长。 */
export function previewVideos(sources: PreviewMedia): PreviewMedia {
  const clip = (video: MasterVideo) => isLocalVideo(video.url) ? video : { ...video, duration: Math.min(10, video.duration) };
  return [clip(sources[0]), clip(sources[1]), clip(sources[2])];
}

/** 工作区默认来源：每个槽位优先使用本机保存的本地视频，否则使用配置或内置地址；元数据须经 readPreviewVideos 读取。 */
export async function defaultPreviewUrls(): Promise<string[]> {
  const local = await localPreviewVideos();
  return ([0, 1, 2] as const).map((index) => local[index]?.url ?? previewVideoUrl(index));
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
    try { return await readMasterVideo(await playableUrl(urls[index].trim()), controller.signal); }
    catch (reason) { throw new Error(`视频 ${index + 1}：${reason instanceof Error ? reason.message : "读取失败"}`); }
  };
  try { return await Promise.all([read(0), read(1), read(2)]); }
  finally { controller.abort(); signal.removeEventListener("abort", abort); }
}

/** 读取真实时长和尺寸；超时、取消或无法加载时明确失败并清理监听器。 */
export function readMasterVideo(url: string, signal: AbortSignal): Promise<MasterVideo> {
  const parsed = new URL(url, window.location.origin);
  const local = parsed.protocol === "blob:" || isLocalMediaUrl(parsed.href);
  if (!local && !["http:", "https:"].includes(parsed.protocol)) return Promise.reject(new Error("母版须为 HTTP(S) 视频地址、public 资源路径或本地文件"));
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
      // 媒体错误码在清除地址后就读不到，先交给调试日志。
      if (error && error.name !== "AbortError") debugLog("预览视频", `${error.message}；${describeMedia(video)}`);
      video.pause(); video.removeAttribute("src"); video.load();
      if (error) reject(error); else resolve(media);
    };
    const loaded = () => finish(Number.isFinite(video.duration) && video.duration > 0 && video.videoWidth > 0 && video.videoHeight > 0 ? undefined : new Error("母版视频缺少有效时长或画面尺寸"));
    const failed = () => finish(new Error(local ? "本地视频无法播放，请确认格式受支持" : "母版加载失败，请检查地址与跨域访问配置"));
    const aborted = () => finish(new DOMException("母版读取已取消", "AbortError"));
    const timer = setTimeout(() => finish(new Error("母版读取超时")), 20_000);
    video.addEventListener("loadedmetadata", loaded);
    video.addEventListener("error", failed);
    signal.addEventListener("abort", aborted, { once: true });
    if (signal.aborted) aborted(); else video.src = parsed.href;
  });
}
