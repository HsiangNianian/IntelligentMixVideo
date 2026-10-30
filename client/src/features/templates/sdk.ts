/**
 * 加载固定版本阿里云预览 SDK、固定其绘制倍数并读取目录；播放器实例的创建与销毁由预览组件负责。
 * 另把 SDK 内部的媒体元素与公开事件流接到调试日志：素材加载失败、卡顿、播放状态与渲染上下文丢失；
 * 渲染授权改为后台请求；抠像使用内置 WebGL 后端并监听模型加载失败，交由预览组件明确降级。
 */
import motions from "./motions.json";
import bundledCatalog from "../../../../server/src/server/template/sdk_catalog.json";
import type { Category, EffectAsset } from "./model";
import { briefly, debugLog, describeMedia, type DiagnosticLevel } from "@/lib/diagnostics";

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
  data?: { currentTime?: number; time?: number; state?: string; useTime?: number; error?: string };
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
  ) => Promise<string | { url: string; maskUrl: string }>;
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
  // 5.2.2 的 TensorflowLoader 调用 setBackend("webgpu") 后未等待即加载模型，真实浏览器会报后端未初始化。
  // 仅在 SDK 独立 iframe 内选择其已有的同步 WebGL 后端，不影响应用窗口或替换官方模型。
  if (target.defaultView) Object.defineProperty(target.defaultView.navigator, "gpu", { configurable: true, value: undefined });
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
 * （SDK 清空地址或中止加载引起的 error 不是素材问题，分别忽略或只记信息）。onMediaCreated 把播放期间新建的元素
 * 通知给播放统计。监听随元素与播放器文档一起释放。
 * 返回仍存活的媒体元素（供调试模式的播放汇总统计丢帧）；只保留弱引用，SDK 释放的元素照常回收。
 */
export function watchSdkMedia(
  target: Document,
  onFailure: (media: HTMLMediaElement) => void,
  onMediaCreated?: (media: HTMLMediaElement) => void,
): () => HTMLMediaElement[] {
  const trackable = typeof WeakRef === "function";
  const created = new Set<WeakRef<HTMLMediaElement>>();
  /** 取出仍存活的元素，顺带清掉已回收的弱引用；没有 WeakRef 的 WebView 不统计。 */
  const alive = () => {
    if (!trackable) return [];
    const elements: HTMLMediaElement[] = [];
    for (const reference of created) {
      const element = reference.deref();
      if (element) elements.push(element);
      else created.delete(reference);
    }
    return elements;
  };
  const create = target.createElement.bind(target);
  target.createElement = ((tagName: string, options?: ElementCreationOptions) => {
    const element = create(tagName, options);
    if (!/^(video|audio)$/i.test(tagName)) return element;
    const media = element as HTMLMediaElement;
    const tag = tagName.toLowerCase();
    if (trackable) {
      // 每次修改都会重建 SDK 的视频，登记过多时先清掉已回收的弱引用。
      if (created.size > 50) alive();
      created.add(new WeakRef(media));
    }
    onMediaCreated?.(media);
    media.addEventListener("error", () => {
      if (!media.getAttribute("src")) return;
      // 重新加载或切换素材会中止上一次加载；这不是素材损坏，不提示预览失败。
      if (media.error?.code === 1) {
        debugLog(SOURCE, `${tag} 加载被中止；${describeMedia(media)}`, "信息");
        return;
      }
      debugLog(SOURCE, `${tag} 加载失败；${describeMedia(media)}`);
      onFailure(media);
    });
    for (const [type, label] of Object.entries(MEDIA_STALLS))
      media.addEventListener(type, () => debugLog(SOURCE, `${tag} ${label}；${describeMedia(media)}`, "警告"));
    return element;
  }) as Document["createElement"];
  return alive;
}

/**
 * 应用时间线后等待 canplay 的上限：平时一秒内就到，网络或设备慢时 SDK 加载素材与字体可能要十几秒，
 * 等满它仍没有 canplay 才判定 IMS 预览不可用；超时只表示无法确认可播放，不能据此把预览标为就绪。
 */
export const CAN_PLAY_TIMEOUT_MS = 60_000;

/**
 * IMS 播放中时间不前进的上限：网络慢时 SDK 缓冲一般几秒内恢复，与素材加载的慢加载警告一样等 10 秒，
 * 仍不前进才判定 SDK 卡住、切换基础视频。
 */
export const STALL_TIMEOUT_MS = 10_000;

/** 等待 SDK 可播放事件的结果；只有 canplay 能证明播放节点已建好。 */
export type CanPlayResult = "canplay" | "timeout" | "cancelled";

/**
 * 等 SDK 应用时间线后发出 canplay（播放节点建好、首帧画出）。setTimeline 返回时 SDK 还没建好播放节点，内部时长仍是 0，
 * 这时开始播放会在 0 秒立即“播放结束”。订阅在调用 setTimeline 前建立，超时从它完成后再开始，避免应用时间线耗时吞掉等待窗口；
 * 取消或超时不会冒充 canplay。signal 用于卸载新修订，timeline 失败时按取消结束。
 */
export function waitForCanPlay(
  player: Pick<Player, "event$">,
  timeline: Promise<unknown>,
  signal?: AbortSignal,
): Promise<CanPlayResult> {
  return new Promise((resolve) => {
    let settled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let subscription: { unsubscribe(): void } | undefined;
    /** 结束等待并取消订阅与计时器。 */
    function finish(result: CanPlayResult) {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      subscription?.unsubscribe();
      signal?.removeEventListener("abort", cancelled);
      resolve(result);
    }
    const cancelled = () => finish("cancelled");
    subscription = player.event$.subscribe(({ type }) => { if (type === "canplay") finish("canplay"); });
    if (settled) subscription.unsubscribe();
    signal?.addEventListener("abort", cancelled, { once: true });
    if (signal?.aborted) cancelled();
    else void timeline.then(
      () => { if (!settled) timer = setTimeout(() => finish("timeout"), CAN_PLAY_TIMEOUT_MS); },
      cancelled,
    );
  });
}

/** SDK 5.2.2 渲染引擎初始化时固定请求的授权接口。 */
const LICENSE_API = "https://vpp-license-proxy.aliyuncs.com/getWebCert";
/** 后台授权请求的记录上限：平时一两秒就有回应，超过它只记一条调试日志，不影响预览。 */
const LICENSE_TIMEOUT_MS = 10_000;

/**
 * 渲染引擎加载 wasm 后请求一次授权，等到回应才初始化、开始画视频：网络慢时第一次播放要等它，请求失败或回应不是 JSON
 * （代理的错误页等）时 SDK 还会把渲染引擎标为初始化失败且不再重试，视频层一直黑屏。预览以空 License 运行在 localhost，
 * 服务端对空域名的请求只回一段不含证书的 JSON，引擎按 localhost 条件无证书初始化（保留水印），授权结果并不决定能否播放。
 * 所以在 SDK 加载前接管播放器窗口的 fetch，把授权变成后台操作：SDK 立即拿到同样的无证书结果（空 JSON），不等网络就初始化；
 * 真实请求照常在后台发出，出错、非 2xx、回应不是 JSON 或 10 秒没有回应时只写一条调试日志。其他请求原样转发。
 * 返回的函数恢复原 fetch，之后结束的后台请求不再记录。
 */
export function deferLicenseRequest(view: Window): () => void {
  const scope = view as Window & typeof globalThis;
  const original = scope.fetch;
  const request = original.bind(scope);
  let stopped = false;
  const wrapper = ((input: RequestInfo | URL, init?: RequestInit) => {
    if (String(typeof input === "object" && "url" in input ? input.url : input) !== LICENSE_API) return request(input, init);
    void checkLicense((signal) => request(input, { ...init, signal }), () => stopped || scope.closed);
    return Promise.resolve(new Response("{}", { headers: { "Content-Type": "application/json" } }));
  }) as typeof fetch;
  scope.fetch = wrapper;
  return () => {
    stopped = true;
    if (scope.fetch === wrapper) scope.fetch = original;
  };
}

/** SDK 的跨源 TensorFlow 拒绝不总触发窗口事件；在实际模型请求和脚本入口捕获失败，卸载时恢复 fetch。 */
export function watchMattingFailures(view: Window, onFailure: (error: unknown) => void): () => void {
  const scope = view as Window & typeof globalThis;
  const original = scope.fetch, request = original.bind(scope);
  const wrapper = ((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(typeof input === "object" && "url" in input ? input.url : input);
    if (!url.includes("/wasm/starry/model/")) return request(input, init);
    return request(input, init).then((response) => {
      if (!response.ok) onFailure(new Error(`抠像模型下载失败：HTTP ${response.status}`));
      return response;
    }, (error) => { onFailure(error); throw error; });
  }) as typeof fetch;
  // 字体等无关拒绝只记录诊断；模型网络错误由上面的请求 URL 归因，不依赖模糊的 Failed to fetch。
  const rejected = (event: PromiseRejectionEvent) => {
    if (/\/starry\/(tensorflow|model)\//.test(String(event.reason?.stack ?? event.reason))) onFailure(event.reason);
  };
  const scriptFailed = (event: Event) => {
    const element = event.target as HTMLScriptElement | null;
    if (element?.tagName === "SCRIPT" && element.src.includes("/starry/tensorflow/")) onFailure(new Error("抠像脚本加载失败"));
  };
  scope.fetch = wrapper;
  view.addEventListener("unhandledrejection", rejected);
  view.addEventListener("error", scriptFailed, true);
  return () => {
    if (scope.fetch === wrapper) scope.fetch = original;
    view.removeEventListener("unhandledrejection", rejected);
    view.removeEventListener("error", scriptFailed, true);
  };
}

/** 在后台完成授权请求，结果不交给 SDK；失败只写调试日志，播放器已停用或卸载时（请求随页面中止）不记录。 */
async function checkLicense(send: (signal: AbortSignal) => Promise<Response>, gone: () => boolean) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new DOMException(`${LICENSE_TIMEOUT_MS / 1000} 秒没有回应`, "TimeoutError")), LICENSE_TIMEOUT_MS);
  try {
    const response = await send(controller.signal);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const text = await response.text();
    try { JSON.parse(text); } catch { throw new Error(`HTTP ${response.status} 的回应不是 JSON`); }
  } catch (error) {
    if (!gone()) debugLog(SOURCE, `渲染授权请求失败（${briefly(error)}）；预览不等授权，照常播放`, "警告");
  } finally {
    clearTimeout(timer);
  }
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
export function watchPlayer(player: Pick<Player, "event$">, onRenderError?: (message: string) => void): () => void {
  let loading: { timer: ReturnType<typeof setTimeout>; warned: boolean } | undefined;
  const subscription = player.event$.subscribe(({ type, data = {} }) => {
    if (type === "playerStateChange" && data.state)
      debugLog(SOURCE, `播放状态 ${data.state}（${seconds(data.currentTime)}）`, STATE_LEVELS[data.state] ?? "信息");
    else if (type === "renderError") {
      const message = data.error || "渲染引擎初始化失败";
      debugLog(SOURCE, `渲染失败：${message}`, "错误");
      onRenderError?.(message);
    }
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
