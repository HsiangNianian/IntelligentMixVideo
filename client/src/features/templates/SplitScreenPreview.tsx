/** 基础播放器的同屏及圆形开场；与 SDK 共用取景几何，跟随同一个时钟，不执行人物抠像。 */
import { useImperativeHandle, useRef, type Ref } from "react";
import type { MasterVideo, PreviewMedia } from "./model";
import { previewVideos } from "./media";
import { borderSvg, previewMaterial, previewRect, type ScreenPoint, type SplitScreen } from "./splitScreen";
import { defaultAvatarEffects, type AvatarEffects } from "./avatarEffects";
import { previewCircleGradient, previewHeadCircle } from "./avatarPreview";

export interface SplitPreviewHandle { sync(time: number, materialTime: number, playing: boolean): void }

/** 布局示例可循环短源，时长仍由原预览片段决定；所有窗口静音。 */
export function SplitScreenPreview({ ref, layout, media, source, avatar, avatarEffects, previewCenter, openingDuration = 0, onError }: { ref: Ref<SplitPreviewHandle>; layout?: SplitScreen; media: PreviewMedia; source: number; avatar: number; avatarEffects?: AvatarEffects; previewCenter?: ScreenPoint; openingDuration?: number; onError: () => void }) {
  const videos = useRef<(HTMLVideoElement | null)[]>([]);
  const opening = useRef<HTMLDivElement>(null);
  const sources = useRef<MasterVideo[]>([]);
  const previews = previewVideos(media);
  const effects = avatarEffects ?? defaultAvatarEffects();
  const avatarVideo = previews[avatar];
  const circle = previewHeadCircle(avatarVideo, effects.circleScale, previewCenter);
  const maskImage = previewCircleGradient(avatarVideo, circle, effects.feather);
  sources.current = [previews[previewMaterial(source, avatar)], avatarVideo, previews[previewMaterial(source, avatar)], avatarVideo];
  useImperativeHandle(ref, () => ({ sync(time, materialTime, playing) {
    if (opening.current) opening.current.style.display = time < openingDuration ? "" : "none";
    videos.current.forEach((video, index) => {
      if (!video) return;
      const at = (index === 1 || index === 3 ? time : materialTime) % sources.current[index].duration;
      if (Math.abs(video.currentTime - at) > 0.1) video.currentTime = at;
      const visible = index === 3 ? time < openingDuration : time >= openingDuration;
      if (playing && visible && video.paused) void video.play().catch((error: DOMException) => { if (error.name !== "AbortError") onError(); });
      else if (!playing || !visible) video.pause();
    });
  } }), [onError, openingDuration]);
  return <>{layout?.enabled && <div className="absolute inset-0" style={{ background: layout.backgroundColor }} data-testid="split-screen-preview">
    {layout.layout !== "split" && <video ref={(node) => { videos.current[2] = node; }} src={sources.current[0].url} muted playsInline loop preload="auto" aria-hidden="true" className="absolute inset-0 size-full scale-110 object-cover blur-xl" onError={onError} />}
    {(["material", "avatar"] as const).map((role, index) => {
      const source = sources.current[index], rect = previewRect(layout, role, source);
      const circular = role === "avatar" && layout.layout === "circle";
      if (role === "avatar" && !circular && effects.removeBackground) { rect.radius = 0; rect.border = 0; }
      return <div key={role} data-role={role} className="absolute overflow-hidden" style={{ left: `${rect.x / 10.8}%`, top: `${rect.y / 19.2}%`, width: `${rect.width / 10.8}%`, height: `${rect.height / 19.2}%`, borderRadius: role === "avatar" && layout.layout === "circle" ? "50%" : `${rect.radius / rect.width * 100}% / ${rect.radius / rect.height * 100}%` }}>
        <video ref={(node) => { videos.current[index] = node; }} src={source.url} muted playsInline loop preload="auto" className={`size-full ${role === "material" && layout.layout !== "split" ? "object-contain" : "object-cover"}`} style={circular ? { position: "absolute", width: `${source.width / (2 * circle.radius) * 100}%`, height: `${source.height / (2 * circle.radius) * 100}%`, maxWidth: "none", left: `${-(circle.x - circle.radius) / (2 * circle.radius) * 100}%`, top: `${-(circle.y - circle.radius) / (2 * circle.radius) * 100}%`, maskImage } : undefined} onError={onError} aria-label={`${role === "avatar" ? "数字人" : "素材"}同屏预览`} />
        {rect.border > 0 && <img alt="" className="pointer-events-none absolute inset-0 size-full" src={borderSvg(rect, layout[role].borderColor)} />}
      </div>;
    })}
  </div>}{openingDuration > 0 && <div ref={opening} className="absolute inset-0 flex items-center justify-center bg-black" data-testid="circle-opening-preview">
    <video ref={(node) => { videos.current[3] = node; }} src={avatarVideo.url} muted playsInline loop preload="auto" aria-label="圆形开场预览" onError={onError}
      style={{ width: avatarVideo.width / avatarVideo.height >= 1080 / 1920 ? "100%" : "auto", height: avatarVideo.width / avatarVideo.height >= 1080 / 1920 ? "auto" : "100%", maxWidth: "100%", maxHeight: "100%", maskImage }} />
  </div>}</>;
}
