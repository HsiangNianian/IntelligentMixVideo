/**
 * 桌面端预览媒体服务的地址：Rust 在 127.0.0.1 的随机端口提供内置示例与本机保存的预览视频（支持范围请求），
 * 地址带每次启动随机生成的令牌。桌面端把这些视频以 HTTP 地址交给预览：blob URL 在系统 WebKitGTK 里让 IMS SDK 的
 * <video> 一直等不到 canplaythrough（SDK 报告在播放、画面却定格），Chromium 里也明显更晚就绪。浏览器没有该服务，不调用这里。
 */
import { updateInvoke as invoke } from "@/lib/update-guard";

let base: Promise<string> | undefined;
// 已取得的地址前缀，供同步判断某个地址是否为本机视频。
let known: string | undefined;

/** 媒体服务地址前缀（`http://127.0.0.1:端口/令牌`）；只在桌面端调用，取得后复用，失败时下次重试。 */
export function mediaServerBase(): Promise<string> {
  base ??= invoke<string>("preview_media_base").then((value) => {
    if (!value) throw new Error("预览媒体服务不可用");
    return (known = value);
  }).catch((reason: unknown) => {
    base = undefined;
    throw reason;
  });
  return base;
}

/** 内置示例 1～3 的媒体服务地址。 */
export function sampleMediaUrl(server: string, index: number): string {
  return `${server}/sample/${index + 1}`;
}

/** 本机保存的预览视频地址；数据文件名每次保存都不同，替换后旧地址自然失效。 */
export function localMediaUrl(server: string, file: string): string {
  return `${server}/local/${encodeURIComponent(file)}`;
}

/** 是否为媒体服务提供的本机视频（本机视频使用完整时长，读取失败按本地文件提示）。 */
export function isLocalMediaUrl(url: string): boolean {
  return known !== undefined && url.startsWith(`${known}/local/`);
}
