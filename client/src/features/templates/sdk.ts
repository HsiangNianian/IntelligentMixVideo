/**
 * 加载固定版本阿里云预览 SDK、固定其绘制倍数并读取目录；播放器实例的创建与销毁由预览组件负责。
 * 另把 SDK 内部的媒体元素与公开事件流接到调试日志：素材加载失败、卡顿、播放状态与渲染上下文丢失。
 */
import motions from "./motions.json";
import bundledCatalog from "../../../../server/src/server/template/sdk_catalog.json";
import type { Category, EffectAsset } from "./model";
import { debugLog, describeMedia, type DiagnosticLevel } from "@/lib/diagnostics";

/** SDK 返回的原始目录项；不同目录分别使用 key 或 subType。 */
interface CatalogItem {
  key?: string;
  subType?: string;
  title?: string;
  name?: string;
  cover?: string;
}
/** 只声明本功能实际使用的播放器表面。 */
export interface Player {
  play(): void;
  pause(): void;
  destroy(): void;
  currentTime: number;
  timeline: unknown;
  aspectRatio?: string;
  setTimeline(timeline: unknown): Promise<unknown>;
  event$: {
    subscribe(callback: (event: PlayerEvent) => void): {
      unsubscribe(): void;
    };
  };
}
/** 公开事件流里的一条事件；只声明用到的字段：播放状态、加载耗时（毫秒）与当前时间。 */
export interface PlayerEvent {
  type: string;
  data?: { currentTime?: number; time?: number; state?: string; useTime?: number };
}
/** 示例媒体使用配置的 URL；回调将其直接交给 SDK。 */
interface PlayerOptions {
  container: HTMLElement;
  mode: string;
  controls: boolean;
  locale: string;
  licenseConfig: { rootDomain: string; licenseKey: string };
  aspectRatio: string;
  maxCanvasConfig: { width?: number; height?: number };
  getMediaInfo: (
    id: string,
    type: string,
    origin: string,
    url?: string,
  ) => Promise<string>;
  getTimelineMaterials: (
    materials: Record<string, unknown>[],
  ) => Promise<Record<string, unknown>[]>;
}
/** 固定 5.2.2 的构造器与目录方法。 */
export interface PreviewSDK {
  new (options: PlayerOptions): Player;
  getSubtitleEffectColorStyles(): CatalogItem[];
  getSubtitleBubbles(): CatalogItem[];
  getVideoFilters(): CatalogItem[];
  getVideoEffects(): CatalogItem[];
  getVideoTransitions(): CatalogItem[];
}

declare global {
  interface Window {
    AliyunTimelinePlayer?: PreviewSDK;
    /** SDK 内置并挂到全局的 Konva；只使用它的全局绘制倍数。 */
    Konva?: { pixelRatio: number };
  }
}
/**
 * 在播放器独立文档中加载 SDK 和样式；失败或取消时清理资源，尺寸配置随文档重新初始化。
 * 成功后把 SDK 内置 Konva 的绘制倍数固定为 1：场景已按视频原尺寸（如 1080×1920）设置，
 * Konva 默认再乘屏幕缩放倍数，2 倍屏上每层画布变成 2160×3840，暂停时也逐帧重绘。
 * 倍数只影响画布的实际像素，场景尺寸、坐标与字号不变；须在创建播放器之前设置。
 */
export function loadSDK(target: Document, signal: AbortSignal): Promise<PreviewSDK> {
  return new Promise<PreviewSDK>((resolve, reject) => {
    const script = target.createElement("script");
    const stylesheet = target.createElement("link");
    stylesheet.rel = "stylesheet";
    stylesheet.href = "https://g.alicdn.com/thor-server/video-editing-websdk/5.2.2/player.css";
    script.src =
      "https://g.alicdn.com/thor-server/video-editing-websdk/5.2.2/player.js";
    let scriptReady = false;
    let styleReady = false;
    // 等待脚本和样式均加载，成功和失败都清理定时器与监听器。
    const finish = (error?: Error) => {
      if (!error && (!scriptReady || !styleReady)) return;
      window.clearTimeout(timeout);
      script.onload = script.onerror = null;
      stylesheet.onload = stylesheet.onerror = null;
      signal.removeEventListener("abort", aborted);
      if (error) {
        script.remove();
        stylesheet.remove();
        reject(error);
      } else {
        const view = target.defaultView!;
        if (view.Konva) view.Konva.pixelRatio = 1;
        resolve(view.AliyunTimelinePlayer!);
      }
    };
    const aborted = () => finish(new DOMException("SDK 加载已取消", "AbortError"));
    const timeout = window.setTimeout(
      () => finish(new Error("SDK 加载超时，请检查网络后重试")),
      30_000,
    );
    script.onload = () => {
      scriptReady = true;
      finish(target.defaultView?.AliyunTimelinePlayer ? undefined : new Error("SDK 未正确初始化"));
    };
    stylesheet.onload = () => { styleReady = true; finish(); };
    stylesheet.onerror = () => finish(new Error("SDK 样式下载失败，请检查网络后重试"));
    script.onerror = () => finish(new Error("SDK 下载失败，请检查网络后重试"));
    signal.addEventListener("abort", aborted, { once: true });
    if (signal.aborted) aborted(); else target.head.append(stylesheet, script);
  });
}

/** 调试日志中 SDK 相关记录的来源名。 */
const SOURCE = "模板预览 SDK";
/** 媒体元素的停顿事件：数据停止到达（stalled）与播放等待数据（waiting），都是卡顿的直接信号。 */
const MEDIA_STALLS: Record<string, string> = { stalled: "数据停止到达（stalled）", waiting: "等待数据（waiting）" };

/**
 * SDK 用不挂到文档上的 <video>/<audio> 播放素材，它们的事件到不了窗口监听，加载失败时 SDK 自己也只打印组件编号。
 * 在 SDK 加载前接管播放器文档的 createElement，给每个媒体元素挂上 error、stalled、waiting 监听，
 * 把地址、MediaError 码与加载状态写进调试日志；设置了地址的加载失败另交给 onFailure 在预览里提示
 * （SDK 清空地址引起的 error 不是素材问题，忽略）。监听随元素与播放器文档一起释放。
 */
export function watchSdkMedia(target: Document, onFailure: (media: HTMLMediaElement) => void) {
  const create = target.createElement.bind(target);
  target.createElement = ((tagName: string, options?: ElementCreationOptions) => {
    const element = create(tagName, options);
    if (!/^(video|audio)$/i.test(tagName)) return element;
    const media = element as HTMLMediaElement;
    const tag = tagName.toLowerCase();
    media.addEventListener("error", () => {
      if (!media.getAttribute("src")) return;
      debugLog(SOURCE, `${tag} 加载失败；${describeMedia(media)}`);
      onFailure(media);
    });
    for (const [type, label] of Object.entries(MEDIA_STALLS))
      media.addEventListener(type, () => debugLog(SOURCE, `${tag} ${label}；${describeMedia(media)}`, "警告"));
    return element;
  }) as Document["createElement"];
}

/** 播放状态的日志级别：卡住（stalled）记警告、损坏（broken）记错误，其余（playing、paused、ended）记信息。 */
const STATE_LEVELS: Record<string, DiagnosticLevel> = { stalled: "警告", broken: "错误" };
/** 素材加载超过该时长仍未完成时写一条警告。 */
const LOADING_WARNING_MS = 10_000;

/** 把秒数格式化为两位小数，缺失时标为未知。 */
function seconds(value: number | undefined) {
  return typeof value === "number" ? `${value.toFixed(2)} 秒` : "未知时间";
}

/**
 * 把 SDK 公开事件流里与排查有关的事件写进调试日志：播放状态变化（含卡住与损坏）、WebGL 渲染上下文丢失与恢复，
 * 以及开始加载素材后 10 秒仍没有加载完成（之后完成时补记用时）。SDK 的素材与播放失败事件不进公开事件流，
 * 由 watchSdkMedia 从媒体元素记录；逐帧的 render 等事件不记录。返回的函数取消订阅并清除计时器。
 */
export function watchPlayer(player: Pick<Player, "event$">): () => void {
  let loading: { timer: ReturnType<typeof setTimeout>; warned: boolean } | undefined;
  const subscription = player.event$.subscribe(({ type, data = {} }) => {
    if (type === "playerStateChange" && data.state)
      debugLog(SOURCE, `播放状态 ${data.state}（${seconds(data.currentTime)}）`, STATE_LEVELS[data.state] ?? "信息");
    else if (type === "renderContextLost") debugLog(SOURCE, "WebGL 渲染上下文丢失，画面会黑屏直到恢复");
    else if (type === "renderContextRestart") debugLog(SOURCE, "WebGL 渲染上下文已恢复", "警告");
    else if (type === "loading" && !loading) {
      const state = { warned: false, timer: setTimeout(() => {
        state.warned = true;
        debugLog(SOURCE, `素材加载 ${LOADING_WARNING_MS / 1000} 秒仍未完成（${seconds(data.currentTime)}）`, "警告");
      }, LOADING_WARNING_MS) };
      loading = state;
    } else if (type === "loadEnd" && loading) {
      clearTimeout(loading.timer);
      if (loading.warned) debugLog(SOURCE, `素材加载完成，用时 ${((data.useTime ?? 0) / 1000).toFixed(1)} 秒`, "信息");
      loading = undefined;
    }
  });
  return () => {
    subscription.unsubscribe();
    clearTimeout(loading?.timer);
  };
}

/** 合并效果与动画目录；SDK 未加载时使用同版本随包白名单，让离线编辑不依赖网络。 */
export function readCatalog(sdk?: PreviewSDK): EffectAsset[] {
  const result: EffectAsset[] = motions.map((item) => ({
    ...item,
    category: item.category as Category,
    parameters: Object.fromEntries(
      Object.entries(item.parameters).filter(
        (entry): entry is [string, string] => typeof entry[1] === "string",
      ),
    ),
  }));
  const lists: [Category, CatalogItem[], string][] = [
    ["flower", sdk?.getSubtitleEffectColorStyles() || bundledCatalog.categories.flower.map((key) => ({ key })), "EffectColorStyle"],
    ["bubble", sdk?.getSubtitleBubbles() || bundledCatalog.categories.bubble.map((key) => ({ key })), "BubbleStyleId"],
    ["filter", sdk?.getVideoFilters() || bundledCatalog.categories.filter.map((key) => ({ key })), "SubType"],
    ["vfx/normal", sdk?.getVideoEffects() || bundledCatalog.categories["vfx/normal"].map((key) => ({ key })), "SubType"],
    ["transition/normal", sdk?.getVideoTransitions() || bundledCatalog.categories["transition/normal"].map((key) => ({ key })), "SubType"],
  ];
  for (const [category, items, parameter] of lists)
    for (const item of items) {
      const code = item.key || item.subType;
      if (!code) continue;
      result.push({
        id: `${category}/${code}`,
        category,
        name: item.title || item.name || code,
        effect_id: code,
        parameters: { [parameter]: code },
        preview_url: item.cover || "",
      });
    }
  return result;
}

let fontLoading: Promise<void> | undefined;

/** 加载与旧预览一致的测量字体；超时或失败允许重试，成功后供页面反复使用。 */
export function loadPreviewFont(): Promise<void> {
  if (!fontLoading) {
    fontLoading = (async () => {
      const response = await fetch(
        "https://ice-pub-media.myalicdn.com/websdk/fonts/Alibaba-PuHuiTi-Bold.ttf",
        { signal: AbortSignal.timeout(20_000) },
      );
      if (!response.ok) throw new Error("预览字体加载失败，请重试");
      const font = new FontFace("ims-preview", await response.arrayBuffer());
      document.fonts.add(await font.load());
    })().catch((error) => {
      fontLoading = undefined;
      throw error;
    });
  }
  return fontLoading;
}
