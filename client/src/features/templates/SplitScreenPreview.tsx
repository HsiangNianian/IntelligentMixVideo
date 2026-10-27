/** 基础播放器的同屏画面；与 SDK 时间线共享几何和边框，跟随同一个预览时钟。 */
import { useImperativeHandle, useRef, type Ref } from "react";
import type { MasterVideo, PreviewMedia } from "./model";
import { previewVideos } from "./media";
import { borderSvg, previewMaterial, previewRect, type SplitScreen } from "./splitScreen";

export interface SplitPreviewHandle { sync(time: number, materialTime: number, playing: boolean): void }

/** 布局示例可循环短源，时长仍由原预览片段决定；所有窗口静音。 */
export function SplitScreenPreview({ ref, layout, media, source, avatar, onError }: { ref: Ref<SplitPreviewHandle>; layout: SplitScreen; media: PreviewMedia; source: number; avatar: number; onError: () => void }) {
  const videos = useRef<(HTMLVideoElement | null)[]>([]);
  const sources = useRef<MasterVideo[]>([]);
  const previews = previewVideos(media);
  sources.current = [previews[previewMaterial(source, avatar)], previews[avatar], previews[previewMaterial(source, avatar)]];
  useImperativeHandle(ref, () => ({ sync(time, materialTime, playing) {
    videos.current.forEach((video, index) => {
      if (!video) return;
      const at = (index === 1 ? time : materialTime) % sources.current[index].duration;
      if (Math.abs(video.currentTime - at) > 0.1) video.currentTime = at;
      if (playing && video.paused) void video.play().catch((error: DOMException) => { if (error.name !== "AbortError") onError(); });
      else if (!playing) video.pause();
    });
  } }), [onError]);
  return <div className="absolute inset-0" style={{ background: layout.backgroundColor }} data-testid="split-screen-preview">
    {layout.layout !== "split" && <video ref={(node) => { videos.current[2] = node; }} src={sources.current[0].url} muted playsInline loop preload="auto" aria-hidden="true" className="absolute inset-0 size-full scale-110 object-cover blur-xl" onError={onError} />}
    {(["material", "avatar"] as const).map((role, index) => {
      const source = sources.current[index], rect = previewRect(layout, role, source);
      return <div key={role} data-role={role} className="absolute overflow-hidden" style={{ left: `${rect.x / 10.8}%`, top: `${rect.y / 19.2}%`, width: `${rect.width / 10.8}%`, height: `${rect.height / 19.2}%`, borderRadius: role === "avatar" && layout.layout === "circle" ? "50%" : `${rect.radius / rect.width * 100}% / ${rect.radius / rect.height * 100}%` }}>
        <video ref={(node) => { videos.current[index] = node; }} src={source.url} muted playsInline loop preload="auto" className={`size-full ${role === "material" && layout.layout !== "split" ? "object-contain" : "object-cover"}`} onError={onError} aria-label={`${role === "avatar" ? "数字人" : "素材"}同屏预览`} />
        {rect.border > 0 && <img alt="" className="pointer-events-none absolute inset-0 size-full" src={borderSvg(rect, layout[role].borderColor)} />}
      </div>;
    })}
  </div>;
}
