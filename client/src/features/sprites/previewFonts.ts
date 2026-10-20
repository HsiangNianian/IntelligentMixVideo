/**
 * AI 资产预览的受管字体：宿主页面向服务端读取一次（`GET /api/sprites/fonts/{weight}`），在应用存活期间复用，
 * 应播放器的 `imv-preview-fonts-request` 转交给它（预览页参数 `fonts=host`）。沙箱预览页是不透明来源，浏览器不为它的
 * 请求使用 HTTP 缓存，由播放器各自读取时，每个叠加层和封面每次挂载都要重新下载两份字体。
 */
import { updateFetch as fetch } from "@/lib/update-guard";
import { apiBase } from "@/lib/api-base";

/** 预览页的字体模式参数：带上它的播放器向宿主页面请求字体，而不是自己下载。 */
export const HOST_FONTS = "fonts=host";

/** 两个字重的字体文件；读取失败后清空，下次请求重新读取。 */
let loading: Promise<Record<400 | 700, Blob>> | undefined;

/** 读取两个字重的字体文件，并发的请求共用同一次读取。 */
export function previewFonts(): Promise<Record<400 | 700, Blob>> {
  if (!loading) {
    loading = Promise.all(([400, 700] as const).map(async (weight) => {
      const response = await fetch(`${apiBase()}/api/sprites/fonts/${weight}`);
      if (!response.ok) throw new Error(`预览字体读取失败（HTTP ${response.status}）`);
      return response.blob();
    })).then(([regular, bold]) => ({ 400: regular, 700: bold }));
    loading.catch(() => { loading = undefined; });
  }
  return loading;
}

/** 回应一个播放器的字体请求；读取失败时回复 null，播放器改为自行下载，预览不受影响。 */
export function sendPreviewFonts(target: Window, channel: string): void {
  void previewFonts().catch(() => null).then((fonts) => target.postMessage({ type: "imv-preview-fonts", channel, fonts }, "*"));
}
