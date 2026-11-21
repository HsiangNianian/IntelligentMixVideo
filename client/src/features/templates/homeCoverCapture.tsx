/** 主页按需挂载一个独立预览，复用编辑器的一秒画面和原视频来源，取图后释放播放器。 */
import { useEffect, useRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import { Tooltip } from "radix-ui";
import { listSprites } from "@/features/sprites/api";
import type { SpriteAsset } from "@/features/sprites/model";
import { defaultAvatarPreview } from "./avatarPreview";
import { readCoverMedia } from "./media";
import { toDraft, type Draft, type PreviewMedia, type Template } from "./model";
import { TemplatePreview, type CaptureCover } from "./TemplatePreview";
import { useTemplatePreviewModel } from "./useTemplatePreviewModel";

interface FrameProps {
  draft: Draft;
  media: PreviewMedia;
  assets: SpriteAsset[];
  stream?: MediaStream;
  resolve: (image: Blob) => void;
  reject: (error: unknown) => void;
}

/** 只挂载预览画布，不读写当前编辑器的草稿、选择或播放进度。 */
function CoverPreview({ draft, media, assets, stream, resolve, reject }: FrameProps) {
  const capture = useRef<CaptureCover>(null);
  const { previewDraft, preview, spriteClips, coverAssetsReady } = useTemplatePreviewModel(draft, media, assets);
  const avatar = draft.avatar_preview ?? defaultAvatarPreview;
  useEffect(() => {
    if (!coverAssetsReady || !preview.timeline || !capture.current) {
      reject(new Error(preview.error || "模板预览所需的字效或时间线不可用"));
      return;
    }
    void capture.current(stream).then(resolve, reject);
  }, [coverAssetsReady, preview, stream, resolve, reject]);
  return previewDraft && <TemplatePreview captureRef={capture} draft={previewDraft} media={media} resolved={preview.timeline}
    avatarSource={avatar.source} previewCenter={avatar} sprites={spriteClips} />;
}

/** 以主页已核对的模板详情和原素材取帧；卸载或取消时释放播放器。 */
export async function captureHomeCover(template: Template, signal: AbortSignal, stream?: MediaStream): Promise<Blob> {
  const source = template.cover?.source ?? "builtin";
  const media = await readCoverMedia(source, signal);
  const assets = template.clips.length ? await listSprites(signal) : [];
  signal.throwIfAborted();
  const host = document.createElement("div");
  host.className = "home-cover-capture";
  // WebKitGTK 和 WebView2 可读取视口外的文档。标签页共享与 WKWebView 取可视区域。
  if (!stream && /Linux|Windows/.test(navigator.userAgent)) host.dataset.offscreen = "";
  host.setAttribute("aria-hidden", "true");
  document.body.append(host);
  let root: Root | undefined;
  const listeners = new AbortController();
  try {
    return await new Promise<Blob>((resolve, reject) => {
      const options = { signal: listeners.signal };
      // 最小化的 WebView 仍可能报告 visible，却不再绘制。恢复窗口时用同一份模板重新挂载播放器。
      const sync = () => {
        if (!stream && (document.hidden || !document.hasFocus())) {
          root?.unmount();
          root = undefined;
        } else if (!root) {
          const current = root = createRoot(host);
          current.render(<Tooltip.Provider><CoverPreview draft={toDraft(template)} media={media} assets={assets} stream={stream}
            resolve={image => { if (root === current) resolve(image); }}
            reject={error => { if (root === current) reject(error); }} /></Tooltip.Provider>);
        }
      };
      if (!stream) {
        window.addEventListener("focus", sync, options);
        window.addEventListener("blur", sync, options);
        document.addEventListener("visibilitychange", sync, options);
      }
      signal.addEventListener("abort", () => reject(signal.reason), options);
      sync();
    });
  } finally {
    listeners.abort();
    root?.unmount();
    host.remove();
  }
}
