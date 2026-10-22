/**
 * IMS 渲染不可用时的基础视频预览：按解析后的片段顺序依次播放三个视频，保留原生播放控制；
 * 不渲染 IMS 文字、转场、滤镜和 VFX，并把全局时间回报给时间轴与 AI 资产叠加层。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { previewVideos } from "./media";
import type { PreviewMedia } from "./model";
import type { ResolvedTimeline } from "./tracks";

/** 基础播放器只依赖已读取的媒体与解析后的片段区间，不创建 SDK 或请求授权。 */
export function BasicVideoPreview({ media, resolved, order, onTime, onReady, onError, onEnd }: {
  media: PreviewMedia;
  resolved: ResolvedTimeline;
  order: number[];
  onTime: (time: number) => void;
  onReady: () => void;
  onError: () => void;
  onEnd: () => void;
}) {
  const sources = useMemo(() => previewVideos(media), [media]);
  const video = useRef<HTMLVideoElement>(null);
  const callbacks = useRef({ onTime, onReady, onError, onEnd });
  const wantsPlay = useRef(false);
  const continueAfterLoad = useRef(false);
  const loaded = useRef(false);
  const [clipIndex, setClipIndex] = useState(0);
  const safeIndex = Math.min(clipIndex, resolved.clips.length - 1);
  const clip = resolved.clips[safeIndex];
  const source = sources[order[safeIndex]];
  callbacks.current = { onTime, onReady, onError, onEnd };

  // 更换媒体、排列或时间规则时从首段重新开始，避免沿用旧视频的播放位置。
  useEffect(() => {
    wantsPlay.current = false;
    continueAfterLoad.current = false;
    loaded.current = false;
    setClipIndex(0);
  }, [media, order, resolved]);

  // 每段使用独立媒体地址；加载后把全局时间标到该片段起点，自动衔接时从下一段开头继续播放。
  useEffect(() => {
    const element = video.current;
    if (!element || !clip || !source) return;
    let active = true;
    loaded.current = false;
    const settle = () => {
      if (!active) return;
      loaded.current = true;
      element.currentTime = 0;
      callbacks.current.onTime(clip.start);
      callbacks.current.onReady();
      const shouldPlay = wantsPlay.current || continueAfterLoad.current;
      continueAfterLoad.current = false;
      if (shouldPlay) void element.play().catch(() => { if (active) callbacks.current.onError(); });
    };
    const failed = () => { if (active) callbacks.current.onError(); };
    const cleanup = () => {
      active = false;
      element.pause();
      element.removeEventListener("loadedmetadata", settle);
      element.removeEventListener("error", failed);
      element.removeAttribute("src");
      element.load();
    };
    element.addEventListener("loadedmetadata", settle, { once: true });
    element.addEventListener("error", failed);
    element.src = source.url;
    element.load();
    return cleanup;
  }, [clip, source]);

  return <video ref={video} aria-label="基础视频预览" controls playsInline preload="auto"
    className="absolute inset-0 size-full bg-black object-contain"
    onPlay={() => { wantsPlay.current = true; }}
    onPause={() => { wantsPlay.current = false; }}
    onTimeUpdate={(event) => { if (loaded.current) callbacks.current.onTime(clip.start + event.currentTarget.currentTime); }}
    onEnded={() => {
      if (safeIndex + 1 < resolved.clips.length) {
        continueAfterLoad.current = true;
        wantsPlay.current = true;
        setClipIndex(safeIndex + 1);
      } else {
        wantsPlay.current = false;
        callbacks.current.onTime(resolved.duration);
        callbacks.current.onEnd();
      }
    }}
  />;
}
