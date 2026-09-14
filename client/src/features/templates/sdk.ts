/** 加载固定版本阿里云预览 SDK 并读取目录；播放器实例的创建与销毁由预览组件负责。 */
import motions from "./motions.json";
import bundledCatalog from "../../../../server/src/server/template/sdk_catalog.json";
import type { Category, EffectAsset } from "./model";

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
  setTimeline(timeline: unknown): Promise<unknown>;
  event$: {
    subscribe(callback: (event: { type: string }) => void): {
      unsubscribe(): void;
    };
  };
}
/** 示例媒体使用配置的 URL；回调将其直接交给 SDK。 */
interface PlayerOptions {
  container: HTMLElement;
  mode: string;
  controls: boolean;
  locale: string;
  licenseConfig: { rootDomain: string; licenseKey: string };
  aspectRatio: string;
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
  }
}
let loading: Promise<PreviewSDK> | undefined;

/** 并发加载复用同一 Promise；下载失败清理脚本并允许重试。 */
export function loadSDK(): Promise<PreviewSDK> {
  if (window.AliyunTimelinePlayer)
    return Promise.resolve(window.AliyunTimelinePlayer);
  if (loading) return loading;
  loading = new Promise<PreviewSDK>((resolve, reject) => {
    const script = document.createElement("script");
    script.src =
      "https://g.alicdn.com/thor-server/video-editing-websdk/5.2.2/player.js";
    // 成功和失败都清理定时器、监听器，失败时移除节点以支持重试。
    const finish = (error?: Error) => {
      window.clearTimeout(timeout);
      script.onload = script.onerror = null;
      if (error) {
        script.remove();
        reject(error);
      } else resolve(window.AliyunTimelinePlayer!);
    };
    const timeout = window.setTimeout(
      () => finish(new Error("SDK 加载超时，请检查网络后重试")),
      30_000,
    );
    script.onload = () =>
      finish(
        window.AliyunTimelinePlayer ? undefined : new Error("SDK 未正确初始化"),
      );
    script.onerror = () => finish(new Error("SDK 下载失败，请检查网络后重试"));
    document.head.append(script);
  }).catch((error) => {
    loading = undefined;
    throw error;
  });
  return loading;
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
