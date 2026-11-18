/** 主页按需挂载一个独立预览，复用编辑器的一秒画面和原视频来源，取图后释放播放器。 */
import { useEffect, useRef } from "react";
import { createRoot } from "react-dom/client";
import { Tooltip } from "radix-ui";
import { listSprites } from "@/features/sprites/api";
import type { SpriteAsset } from "@/features/sprites/model";
import { getTemplate } from "./api";
import { defaultAvatarPreview } from "./avatarPreview";
import { readCoverMedia } from "./media";
import { toDraft, type Draft, type PreviewMedia, type TemplateSummary } from "./model";
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

/** 列表版本与素材必须和保存时一致；取消或切换服务后销毁本次取帧，旧图不被替换。 */
export async function captureHomeCover(summary: TemplateSummary, signal: AbortSignal, stream?: MediaStream): Promise<Blob> {
  const template = await getTemplate(summary.template_id, signal);
  if (template.cover?.revision !== summary.cover?.revision) throw new Error("模板已更新，请再次刷新");
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
  const root = createRoot(host);
  let cancel: () => void;
  try {
    return await new Promise<Blob>((resolve, reject) => {
      cancel = () => reject(signal.reason);
      signal.addEventListener("abort", cancel, { once: true });
      root.render(<Tooltip.Provider><CoverPreview draft={toDraft(template)} media={media} assets={assets} stream={stream} resolve={resolve} reject={reject} /></Tooltip.Provider>);
    });
  } finally {
    signal.removeEventListener("abort", cancel!);
    root.unmount();
    host.remove();
  }
}
